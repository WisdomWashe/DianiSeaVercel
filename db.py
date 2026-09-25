"""
PostgreSQL database layer for Diani Sea Adventures.

The application data is stored directly in Supabase PostgreSQL using DATABASE_URL.
Supabase Storage is still used for uploaded images, through the Supabase client.

Set:
    DATABASE_URL=postgresql://...
    SUPABASE_URL=https://...
    SUPABASE_SECRET_KEY=...
"""

import json
import re
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row
from flask import current_app, g
from supabase import create_client

from i18n import DEFAULT_LOCALE, resolve_locale

TRANSLATABLE_TEXT_FIELDS = (
    "name", "tagline", "teaser", "duration", "group_size",
    "price", "difficulty", "departs", "best_time",
)
TRANSLATABLE_LIST_FIELDS = (
    "overview", "highlights", "included", "excluded", "bring", "itinerary",
)
ALL_TRANSLATABLE_FIELDS = TRANSLATABLE_TEXT_FIELDS + TRANSLATABLE_LIST_FIELDS


def _database_url():
    url = current_app.config.get("DATABASE_URL") or ""
    if not url:
        raise RuntimeError("DATABASE_URL is not configured.")
    return url


def get_db():
    """Return one PostgreSQL connection for the current Flask request/context."""
    if "db" not in g:
        g.db = psycopg.connect(_database_url(), row_factory=dict_row)
    return g.db


def _supabase_storage():
    """Supabase client used only for Storage operations."""
    if "supabase_storage" not in g:
        url = current_app.config.get("SUPABASE_URL")
        key = current_app.config.get("SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "Supabase Storage is not configured. Set SUPABASE_URL and "
                "SUPABASE_SECRET_KEY."
            )
        g.supabase_storage = create_client(url, key)
    return g.supabase_storage


def close_db(exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()
    g.pop("supabase_storage", None)


def init_app(app):
    app.teardown_appcontext(close_db)


def init_db():
    """Check that PostgreSQL is reachable and the application schema exists."""
    with get_db().cursor() as cur:
        cur.execute("select 1 from public.safaris limit 1")
        cur.fetchone()


def _json_value(value, default):
    if value is None:
        return default
    return value


def _safari_row_to_dict(row, locale=DEFAULT_LOCALE, include_images=True):
    d = dict(row)
    for field in ALL_TRANSLATABLE_FIELDS:
        raw = d.get(field) or {}
        default = [] if field in TRANSLATABLE_LIST_FIELDS else ""
        d[field] = resolve_locale(raw, locale) if raw else default
    d["images"] = get_gallery_images(d["id"]) if include_images else []
    return d


def _image_row_to_dict(row):
    d = dict(row)
    path = d.get("filename", "")
    if path:
        d["url"] = _storage_public_url(path)
    return d


def _storage_public_url(path):
    base = current_app.config.get("SUPABASE_URL", "").rstrip("/")
    bucket = current_app.config.get("SUPABASE_STORAGE_BUCKET", "media")
    return f"{base}/storage/v1/object/public/{bucket}/{path.lstrip('/')}" if base else ""


def _inquiry_row_to_dict(row, safaris_by_id):
    d = dict(row)
    d["safari"] = safaris_by_id.get(d["safari_id"])
    return d


def get_all_safaris(locale=DEFAULT_LOCALE):
    with get_db().cursor() as cur:
        cur.execute("select * from public.safaris order by display_order")
        rows = cur.fetchall()
    return [_safari_row_to_dict(r, locale) for r in rows]


def get_safari_by_slug(slug, locale=DEFAULT_LOCALE):
    with get_db().cursor() as cur:
        cur.execute("select * from public.safaris where slug = %s limit 1", (slug,))
        row = cur.fetchone()
    return _safari_row_to_dict(row, locale) if row else None


def get_other_safaris(slug, locale=DEFAULT_LOCALE, limit=3):
    with get_db().cursor() as cur:
        cur.execute(
            "select * from public.safaris where slug <> %s order by display_order limit %s",
            (slug, limit),
        )
        rows = cur.fetchall()
    return [_safari_row_to_dict(r, locale) for r in rows]


def count_safaris():
    with get_db().cursor() as cur:
        cur.execute("select count(*) as count from public.safaris")
        return cur.fetchone()["count"]


def get_all_slugs():
    with get_db().cursor() as cur:
        cur.execute("select slug from public.safaris")
        return [r["slug"] for r in cur.fetchall()]


def _safari_row_to_raw_dict(row):
    d = dict(row)
    for field in ALL_TRANSLATABLE_FIELDS:
        d[field] = d.get(field) or {}
    d["images"] = get_gallery_images(d["id"])
    return d


def get_safari_raw(slug):
    with get_db().cursor() as cur:
        cur.execute("select * from public.safaris where slug = %s limit 1", (slug,))
        row = cur.fetchone()
    return _safari_row_to_raw_dict(row) if row else None


def slugify(text):
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "safari"


def unique_slug(base_slug, exclude_slug=None):
    existing = set(get_all_slugs())
    existing.discard(exclude_slug)
    if base_slug not in existing:
        return base_slug
    n = 2
    while f"{base_slug}-{n}" in existing:
        n += 1
    return f"{base_slug}-{n}"


def next_display_order():
    with get_db().cursor() as cur:
        cur.execute(
            "select display_order from public.safaris "
            "order by display_order desc limit 1"
        )
        row = cur.fetchone()
    return (row["display_order"] if row else -1) + 1


def _prepare_payload(data):
    payload = dict(data)
    for field in ALL_TRANSLATABLE_FIELDS:
        payload[field] = payload.get(field, {})
    return payload


def _columns_and_values(payload):
    cols = list(payload.keys())
    vals = [json.dumps(payload[c]) if c in ALL_TRANSLATABLE_FIELDS else payload[c] for c in cols]
    return cols, vals


def create_safari(data):
    payload = _prepare_payload(data)
    payload["display_order"] = next_display_order()
    cols, vals = _columns_and_values(payload)
    col_sql = ", ".join(cols)
    placeholders = ", ".join(["%s"] * len(cols))
    with get_db().cursor() as cur:
        cur.execute(
            f"insert into public.safaris ({col_sql}) values ({placeholders}) returning slug",
            vals,
        )
        row = cur.fetchone()
    get_db().commit()
    return row["slug"]


def update_safari(safari_id, data):
    payload = _prepare_payload(data)
    cols, vals = _columns_and_values(payload)
    assignments = ", ".join(f"{c} = %s" for c in cols)
    with get_db().cursor() as cur:
        cur.execute(
            f"update public.safaris set {assignments} where id = %s",
            vals + [safari_id],
        )
    get_db().commit()


def delete_safari(safari_id):
    with get_db().cursor() as cur:
        cur.execute("delete from public.safaris where id = %s", (safari_id,))
    get_db().commit()


def seed_safaris(seed_list):
    if not seed_list:
        return
    for order, entry in enumerate(seed_list):
        payload = _prepare_payload(entry)
        payload["display_order"] = order
        cols, vals = _columns_and_values(payload)
        col_sql = ", ".join(cols)
        placeholders = ", ".join(["%s"] * len(cols))
        with get_db().cursor() as cur:
            cur.execute(
                f"insert into public.safaris ({col_sql}) values ({placeholders})",
                vals,
            )
    get_db().commit()


def get_gallery_images(safari_id):
    with get_db().cursor() as cur:
        cur.execute(
            "select * from public.gallery_images "
            "where safari_id = %s order by uploaded_at desc",
            (safari_id,),
        )
        rows = cur.fetchall()
    return [_image_row_to_dict(r) for r in rows]


def add_gallery_image(safari_id, filename):
    with get_db().cursor() as cur:
        cur.execute(
            "insert into public.gallery_images "
            "(safari_id, filename, uploaded_at) values (%s, %s, %s)",
            (safari_id, filename, datetime.now(timezone.utc)),
        )
    get_db().commit()


def get_gallery_image(image_id):
    with get_db().cursor() as cur:
        cur.execute(
            "select gi.*, s.slug as safari_slug "
            "from public.gallery_images gi "
            "left join public.safaris s on s.id = gi.safari_id "
            "where gi.id = %s limit 1",
            (image_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def delete_gallery_image(image_id):
    with get_db().cursor() as cur:
        cur.execute("delete from public.gallery_images where id = %s", (image_id,))
    get_db().commit()


def add_inquiry(name, email, phone, message, safari_id):
    with get_db().cursor() as cur:
        cur.execute(
            "insert into public.inquiries "
            "(name, email, phone, message, safari_id, received_at) "
            "values (%s, %s, %s, %s, %s, %s)",
            (name, email, phone, message, safari_id, datetime.now(timezone.utc)),
        )
    get_db().commit()


def get_all_inquiries(locale=DEFAULT_LOCALE):
    with get_db().cursor() as cur:
        cur.execute(
            "select * from public.inquiries order by received_at desc"
        )
        rows = cur.fetchall()
    safaris_by_id = {s["id"]: s for s in get_all_safaris(locale)}
    return [_inquiry_row_to_dict(r, safaris_by_id) for r in rows]


def reset_database(seed_list):
    """Delete application rows from PostgreSQL and reseed the safari catalog."""
    with get_db().cursor() as cur:
        cur.execute("delete from public.gallery_images")
        cur.execute("delete from public.inquiries")
        cur.execute("delete from public.safaris")
    get_db().commit()
    seed_safaris(seed_list)


def storage():
    return _supabase_storage().storage.from_(
        current_app.config.get("SUPABASE_STORAGE_BUCKET", "media")
    )
