"""
Database layer for Diani Sea Adventures.

Supports two backends, chosen automatically based on config:

- SQLite (default): a single file, `data/diani.db`, using Python's
  built-in `sqlite3` module. Zero setup, used for local development and
  for hosts with persistent disk but no separate database service.
- MySQL ("webhosted"): a real database server, e.g. the MySQL database
  included with every PythonAnywhere account. Used when DB_HOST is set
  (see app.py / the README's "Using a webhosted database" section).
  Requires the `pymysql` package (see requirements.txt).

Both backends share every table definition, query, and row-processing
function below - only connection setup and the handful of places where
SQL syntax genuinely differs between SQLite and MySQL (schema DDL,
placeholder style, column-existence checks) branch on backend. This
keeps the two paths from drifting apart and means the SQLite path -
fully testable without any external service - already exercises nearly
all of this file's logic.

Three tables:
- safaris:        the six safari categories and all their content
- gallery_images: one row per uploaded photo, linked to a safari
- inquiries:      contact-form submissions, optionally linked to a safari

Photo files themselves still live on disk under static/uploads/<slug>/
regardless of which database backend is active - only the filename and
metadata are stored in the database, which is the normal pattern for
file uploads (databases are for structured data and lookups, not for
storing binary blobs).

Translatable safari fields are stored as JSON-encoded {"en": ..., "de":
..., "fr": ...} dicts (see i18n.py). Every function that returns safari
data takes a `locale` argument and resolves those dicts down to a single
language before handing back a plain dict, so templates and the rest of
the app never have to think about translation - they just see
`safari["name"]` already in the right language.

Rows are returned as plain dicts (not sqlite3.Row objects) so templates
can use the same `safari.name` / `image.filename` dot-access they would
with an ORM.
"""

import json
import re
import sqlite3
from datetime import datetime

from flask import current_app, g

from i18n import DEFAULT_LOCALE, resolve_locale

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS safaris (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    accent TEXT DEFAULT 'lagoon',
    emoji TEXT DEFAULT '',
    name TEXT NOT NULL,
    tagline TEXT DEFAULT '{}',
    teaser TEXT DEFAULT '{}',
    duration TEXT DEFAULT '{}',
    group_size TEXT DEFAULT '{}',
    price TEXT DEFAULT '{}',
    difficulty TEXT DEFAULT '{}',
    departs TEXT DEFAULT '{}',
    best_time TEXT DEFAULT '{}',
    overview TEXT DEFAULT '{}',
    highlights TEXT DEFAULT '{}',
    included TEXT DEFAULT '{}',
    excluded TEXT DEFAULT '{}',
    bring TEXT DEFAULT '{}',
    itinerary TEXT DEFAULT '{}',
    cover_url TEXT,
    display_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS gallery_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    safari_id INTEGER NOT NULL REFERENCES safaris(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    uploaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inquiries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT DEFAULT '',
    message TEXT NOT NULL,
    safari_id INTEGER REFERENCES safaris(id) ON DELETE SET NULL,
    received_at TEXT NOT NULL
);
"""

# MySQL differences from the SQLite schema above, all deliberate:
#  - INTEGER PRIMARY KEY AUTOINCREMENT -> INT AUTO_INCREMENT PRIMARY KEY
#  - VARCHAR(191) instead of TEXT for slug: MySQL can't put a UNIQUE
#    index on a full TEXT column, and 191 is the standard safe length for
#    an indexed utf8mb4 column on older MySQL/InnoDB (191 * 4 bytes stays
#    under the 767-byte index key limit some hosts still enforce).
#  - No inline DEFAULT on TEXT columns: MySQL versions before 8.0.13
#    reject a literal default on TEXT/BLOB columns. Harmless here since
#    the application always supplies every field on insert (see
#    create_safari/seed_safaris) - the SQLite defaults above are just a
#    convenience for hand-written test data, not load-bearing.
#  - Explicit ENGINE=InnoDB (so foreign keys are enforced) and
#    utf8mb4 (so emoji - stored in `emoji` and in itinerary icons -
#    survive; MySQL's older "utf8" charset alias is only 3 bytes/char
#    and silently mangles 4-byte emoji).
#  - Each CREATE TABLE is a separate statement here because PyMySQL's
#    cursor.execute() runs one statement at a time (see _run_script).
#
# cover_url (both schemas) is NULL for most deployments: the normal way
# to set a safari's cover photo is still just dropping a file at
# static/covers/<slug>.jpg (see safari_cover_url() in app.py), which
# needs no database entry at all. This column only gets used when photo
# uploads go to cloud storage instead of local disk (e.g. deploying to
# Vercel, where the filesystem is read-only) - see app.py's
# save_cover_photo() and the README's "Using cloud storage for uploads"
# section.
SCHEMA_MYSQL = [
    """
    CREATE TABLE IF NOT EXISTS safaris (
        id INT AUTO_INCREMENT PRIMARY KEY,
        slug VARCHAR(191) UNIQUE NOT NULL,
        accent VARCHAR(30) DEFAULT 'lagoon',
        emoji VARCHAR(20) DEFAULT '',
        name TEXT NOT NULL,
        tagline TEXT,
        teaser TEXT,
        duration TEXT,
        group_size TEXT,
        price TEXT,
        difficulty TEXT,
        departs TEXT,
        best_time TEXT,
        overview TEXT,
        highlights TEXT,
        included TEXT,
        excluded TEXT,
        bring TEXT,
        itinerary TEXT,
        cover_url VARCHAR(500),
        display_order INT DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS gallery_images (
        id INT AUTO_INCREMENT PRIMARY KEY,
        safari_id INT NOT NULL,
        filename VARCHAR(255) NOT NULL,
        uploaded_at VARCHAR(40) NOT NULL,
        FOREIGN KEY (safari_id) REFERENCES safaris(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS inquiries (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(191) NOT NULL,
        email VARCHAR(191) NOT NULL,
        phone VARCHAR(60) DEFAULT '',
        message TEXT NOT NULL,
        safari_id INT,
        received_at VARCHAR(40) NOT NULL,
        FOREIGN KEY (safari_id) REFERENCES safaris(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]

# Every one of these is stored as JSON: a dict of {locale: string} for the
# first group, {locale: [strings, ...]} for the second. Only slug, accent,
# emoji and display_order are plain, non-translatable columns.
TRANSLATABLE_TEXT_FIELDS = (
    "name", "tagline", "teaser", "duration", "group_size",
    "price", "difficulty", "departs", "best_time",
)
TRANSLATABLE_LIST_FIELDS = (
    "overview", "highlights", "included", "excluded", "bring", "itinerary",
)
ALL_TRANSLATABLE_FIELDS = TRANSLATABLE_TEXT_FIELDS + TRANSLATABLE_LIST_FIELDS


# ---------------------------------------------------------------------------
# Connection handling
# ---------------------------------------------------------------------------

def _backend():
    return current_app.config.get("DB_BACKEND", "sqlite")


def get_db():
    """Return a per-request database connection, opening one if needed."""
    if "db" not in g:
        if _backend() == "mysql":
            import pymysql
            import pymysql.cursors

            kwargs = dict(
                host=current_app.config["DB_HOST"],
                port=current_app.config.get("DB_PORT", 3306),
                user=current_app.config["DB_USER"],
                password=current_app.config["DB_PASSWORD"],
                database=current_app.config["DB_NAME"],
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=False,
                connect_timeout=10,
            )
            if current_app.config.get("DB_SSL"):
                # Hosted databases reached over the public internet
                # (Aiven, TiDB Cloud, etc. - the usual choice when the app
                # runs on Vercel) require an encrypted connection.
                kwargs["ssl_ca"] = current_app.config["DB_SSL_CA"]
                kwargs["ssl_verify_cert"] = True
                kwargs["ssl_verify_identity"] = True
            g.db = pymysql.connect(**kwargs)
        else:
            g.db = sqlite3.connect(current_app.config["DATABASE_PATH"])
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_app(app):
    app.teardown_appcontext(close_db)


def _run(conn, query, params=()):
    """
    Execute one query and return a cursor, whichever backend is active.

    sqlite3.Connection.execute() is a convenience shortcut that creates
    a cursor, runs it, and returns that cursor - PyMySQL has no
    equivalent, so this wraps the two-step cursor()+execute() form and
    hands back the same kind of object either way. Every query in this
    file is written with SQLite-style "?" placeholders; MySQL uses "%s",
    so that's swapped here rather than in every call site (safe, since
    none of these hand-written queries ever contain a literal "?").
    """
    if isinstance(conn, sqlite3.Connection):
        return conn.execute(query, params)
    cursor = conn.cursor()
    cursor.execute(query.replace("?", "%s"), params)
    return cursor


def init_db():
    conn = get_db()
    if _backend() == "mysql":
        for statement in SCHEMA_MYSQL:
            _run(conn, statement)
    else:
        conn.executescript(SCHEMA_SQLITE)
    _migrate_add_missing_columns(conn)
    conn.commit()


def _migrate_add_missing_columns(conn):
    """
    Lightweight, additive migration: add newly-introduced columns to an
    already-existing safaris table without touching existing data. Safe
    to run on every startup - it's a no-op once a column exists.

    (This only helps if the table already exists in the newer JSON-per-
    locale shape. Upgrading from a much older version of this project,
    from before safari fields were stored as JSON, still needs
    `flask --app app reset-db` - see the README.)
    """
    # (column name, SQLite ADD COLUMN clause, MySQL ADD COLUMN clause)
    new_columns = [
        ("itinerary", "itinerary TEXT DEFAULT '{}'", "itinerary TEXT"),
        ("cover_url", "cover_url TEXT", "cover_url VARCHAR(500)"),
    ]

    if _backend() == "mysql":
        rows = _run(conn, "SHOW COLUMNS FROM safaris").fetchall()
        existing = {row["Field"] for row in rows}
        for name, _sqlite_clause, mysql_clause in new_columns:
            if name not in existing:
                _run(conn, f"ALTER TABLE safaris ADD COLUMN {mysql_clause}")
    else:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(safaris)")}
        for name, sqlite_clause, _mysql_clause in new_columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE safaris ADD COLUMN {sqlite_clause}")


# ---------------------------------------------------------------------------
# Row -> dict helpers
# ---------------------------------------------------------------------------

def _safari_row_to_dict(row, locale=DEFAULT_LOCALE):
    d = dict(row)
    for field in ALL_TRANSLATABLE_FIELDS:
        raw = json.loads(d[field]) if d[field] else {}
        default = [] if field in TRANSLATABLE_LIST_FIELDS else ""
        d[field] = resolve_locale(raw, locale) if raw else default
    d["images"] = get_gallery_images(d["id"])
    return d


def _image_row_to_dict(row):
    return dict(row)


def _inquiry_row_to_dict(row, safaris_by_id):
    d = dict(row)
    d["safari"] = safaris_by_id.get(d["safari_id"])
    return d


# ---------------------------------------------------------------------------
# Safaris
# ---------------------------------------------------------------------------

def get_all_safaris(locale=DEFAULT_LOCALE):
    rows = _run(get_db(), "SELECT * FROM safaris ORDER BY display_order").fetchall()
    return [_safari_row_to_dict(r, locale) for r in rows]


def get_safari_by_slug(slug, locale=DEFAULT_LOCALE):
    row = _run(get_db(), "SELECT * FROM safaris WHERE slug = ?", (slug,)).fetchone()
    return _safari_row_to_dict(row, locale) if row else None


def get_other_safaris(slug, locale=DEFAULT_LOCALE, limit=3):
    rows = _run(
        get_db(),
        "SELECT * FROM safaris WHERE slug != ? ORDER BY display_order LIMIT ?",
        (slug, limit),
    ).fetchall()
    return [_safari_row_to_dict(r, locale) for r in rows]


def count_safaris():
    row = _run(get_db(), "SELECT COUNT(*) AS n FROM safaris").fetchone()
    return row["n"]


def get_all_slugs():
    rows = _run(get_db(), "SELECT slug FROM safaris").fetchall()
    return [r["slug"] for r in rows]


def _safari_row_to_raw_dict(row):
    """
    Like _safari_row_to_dict, but WITHOUT resolving translatable fields
    down to one locale - each stays as its full {"en": ..., "de": ...,
    "fr": ...} dict. Used to populate the admin edit form, which needs to
    show and edit all three languages at once.
    """
    d = dict(row)
    for field in ALL_TRANSLATABLE_FIELDS:
        d[field] = json.loads(d[field]) if d[field] else {}
    return d


def get_safari_raw(slug):
    row = _run(get_db(), "SELECT * FROM safaris WHERE slug = ?", (slug,)).fetchone()
    return _safari_row_to_raw_dict(row) if row else None


def slugify(text):
    """Turn a safari name into a URL-friendly slug, e.g. 'Jet Ski Safari' -> 'jet-ski-safari'."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "safari"


def unique_slug(base_slug, exclude_slug=None):
    """Add -2, -3, etc. if base_slug is already taken by a different safari."""
    existing = set(get_all_slugs())
    existing.discard(exclude_slug)
    if base_slug not in existing:
        return base_slug
    n = 2
    while f"{base_slug}-{n}" in existing:
        n += 1
    return f"{base_slug}-{n}"


def next_display_order():
    row = _run(get_db(), "SELECT MAX(display_order) AS n FROM safaris").fetchone()
    return (row["n"] or 0) + 1


def create_safari(data):
    """
    Create a new safari. `data` is a dict with slug, accent, emoji, and
    every field in ALL_TRANSLATABLE_FIELDS as a {"en": ..., "de": ...,
    "fr": ...} dict (or {"en": [...], ...} for list fields). Returns the
    new safari's slug.
    """
    conn = get_db()
    payload = dict(data)
    for field in ALL_TRANSLATABLE_FIELDS:
        payload[field] = json.dumps(payload.get(field, {}))
    payload["display_order"] = next_display_order()
    columns = ", ".join(payload.keys())
    placeholders = ", ".join("?" for _ in payload)
    _run(
        conn,
        f"INSERT INTO safaris ({columns}) VALUES ({placeholders})",
        tuple(payload.values()),
    )
    conn.commit()
    return payload["slug"]


def update_safari(safari_id, data):
    """
    Update an existing safari (by id, since slug is immutable after
    creation - see the admin form). `data` has the same shape as
    create_safari's.
    """
    conn = get_db()
    payload = dict(data)
    for field in ALL_TRANSLATABLE_FIELDS:
        payload[field] = json.dumps(payload.get(field, {}))
    set_clause = ", ".join(f"{col} = ?" for col in payload.keys())
    _run(
        conn,
        f"UPDATE safaris SET {set_clause} WHERE id = ?",
        tuple(payload.values()) + (safari_id,),
    )
    conn.commit()


def set_safari_cover_url(safari_id, url):
    """Store a cloud-hosted cover photo URL for a safari - see the
    cover_url column note near SCHEMA_SQLITE/SCHEMA_MYSQL above. Not
    used at all for the default local-file cover photo convention."""
    conn = get_db()
    _run(conn, "UPDATE safaris SET cover_url = ? WHERE id = ?", (url, safari_id))
    conn.commit()


def delete_safari(safari_id):
    """Delete a safari along with its gallery image rows, and detach any
    enquiries that referenced it. Done explicitly rather than relying on
    the ON DELETE CASCADE / SET NULL foreign keys, because some
    MySQL-compatible hosted databases don't enforce those consistently.
    Does NOT touch files on disk - the caller (app.py) handles removing
    the uploads folder and cover photo, since that's a filesystem
    concern, not a database one."""
    conn = get_db()
    _run(conn, "DELETE FROM gallery_images WHERE safari_id = ?", (safari_id,))
    _run(conn, "UPDATE inquiries SET safari_id = NULL WHERE safari_id = ?", (safari_id,))
    _run(conn, "DELETE FROM safaris WHERE id = ?", (safari_id,))
    conn.commit()


def drop_all_tables():
    """Used by the `flask reset-db` CLI command. Drops all three tables
    so init_db() + seed_safaris() can recreate everything from scratch.
    Works on both backends - sqlite3.Connection.executescript() (multi-
    statement) isn't available on the MySQL/PyMySQL path, so this runs
    each DROP TABLE through _run() individually instead."""
    conn = get_db()
    for table in ("gallery_images", "inquiries", "safaris"):
        _run(conn, f"DROP TABLE IF EXISTS {table}")
    conn.commit()


def seed_safaris(seed_list):
    conn = get_db()
    for order, entry in enumerate(seed_list):
        payload = dict(entry)
        for field in ALL_TRANSLATABLE_FIELDS:
            payload[field] = json.dumps(payload.get(field, {}))
        payload["display_order"] = order
        columns = ", ".join(payload.keys())
        placeholders = ", ".join("?" for _ in payload)
        _run(
            conn,
            f"INSERT INTO safaris ({columns}) VALUES ({placeholders})",
            tuple(payload.values()),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Gallery images
# ---------------------------------------------------------------------------

def get_gallery_images(safari_id):
    rows = _run(
        get_db(),
        "SELECT * FROM gallery_images WHERE safari_id = ? ORDER BY uploaded_at DESC",
        (safari_id,),
    ).fetchall()
    return [_image_row_to_dict(r) for r in rows]


def add_gallery_image(safari_id, filename):
    conn = get_db()
    _run(
        conn,
        "INSERT INTO gallery_images (safari_id, filename, uploaded_at) VALUES (?, ?, ?)",
        (safari_id, filename, datetime.utcnow().isoformat()),
    )
    conn.commit()


def get_gallery_image(image_id):
    row = _run(
        get_db(),
        "SELECT gi.*, s.slug AS safari_slug FROM gallery_images gi "
        "JOIN safaris s ON s.id = gi.safari_id WHERE gi.id = ?",
        (image_id,),
    ).fetchone()
    return dict(row) if row else None


def delete_gallery_image(image_id):
    conn = get_db()
    _run(conn, "DELETE FROM gallery_images WHERE id = ?", (image_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Inquiries
# ---------------------------------------------------------------------------

def add_inquiry(name, email, phone, message, safari_id):
    conn = get_db()
    _run(
        conn,
        "INSERT INTO inquiries (name, email, phone, message, safari_id, received_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name, email, phone, message, safari_id, datetime.utcnow().isoformat()),
    )
    conn.commit()


def get_all_inquiries(locale=DEFAULT_LOCALE):
    rows = _run(get_db(), "SELECT * FROM inquiries ORDER BY received_at DESC").fetchall()
    safaris_by_id = {s["id"]: s for s in get_all_safaris(locale)}
    return [_inquiry_row_to_dict(r, safaris_by_id) for r in rows]
