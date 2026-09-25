"""
One-time migration from the old SQLite database to Supabase.

Usage:
    python migrate_sqlite_to_supabase.py

Optional:
    SQLITE_DB_PATH=/path/to/diani.db python migrate_sqlite_to_supabase.py
    python migrate_sqlite_to_supabase.py --replace

By default the script refuses to overwrite existing Supabase data.
"""
import argparse
import json
import os
import sqlite3

from supabase import create_client

TEXT_FIELDS = (
    "name", "tagline", "teaser", "duration", "group_size",
    "price", "difficulty", "departs", "best_time",
)
LIST_FIELDS = ("overview", "highlights", "included", "excluded", "bring", "itinerary")
JSON_FIELDS = TEXT_FIELDS + LIST_FIELDS


def env(name):
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def decode(value):
    if value in (None, ""):
        return {}
    return json.loads(value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing Supabase application data before importing.",
    )
    args = parser.parse_args()

    default_candidates = (
        os.path.join(os.path.dirname(__file__), "data", "diani.db"),
        os.path.join(os.path.dirname(__file__), "static", "data", "diani.db"),
    )
    db_path = os.environ.get("SQLITE_DB_PATH")
    if not db_path:
        db_path = next((p for p in default_candidates if os.path.exists(p)), None)
    if not os.path.exists(db_path):
        raise SystemExit(f"SQLite database not found: {db_path}")

    client = create_client(env("SUPABASE_URL"), os.environ.get(
        "SUPABASE_SECRET_KEY",
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
    ) or env("SUPABASE_SECRET_KEY"))

    existing = client.table("safaris").select("id").execute().data or []
    if existing and not args.replace:
        raise SystemExit(
            "Supabase already contains safari rows. Use --replace only if "
            "you intentionally want to replace its application data."
        )

    if args.replace:
        client.table("gallery_images").delete().gte("id", 0).execute()
        client.table("inquiries").delete().gte("id", 0).execute()
        client.table("safaris").delete().gte("id", 0).execute()

    sqlite = sqlite3.connect(db_path)
    sqlite.row_factory = sqlite3.Row
    safari_id_map = {}

    safaris = sqlite.execute(
        "select * from safaris order by display_order"
    ).fetchall()

    for row in safaris:
        payload = dict(row)
        old_id = payload.pop("id")
        for field in JSON_FIELDS:
            payload[field] = decode(payload.get(field))
        inserted = client.table("safaris").insert(payload).execute().data
        if not inserted:
            raise RuntimeError(f"Failed to import safari {row['slug']}")
        safari_id_map[old_id] = inserted[0]["id"]

    images = sqlite.execute(
        "select * from gallery_images order by id"
    ).fetchall()
    for row in images:
        new_safari_id = safari_id_map.get(row["safari_id"])
        if new_safari_id is None:
            continue
        client.table("gallery_images").insert({
            "safari_id": new_safari_id,
            "filename": row["filename"],
            "uploaded_at": row["uploaded_at"],
        }).execute()

    inquiries = sqlite.execute(
        "select * from inquiries order by id"
    ).fetchall()
    for row in inquiries:
        client.table("inquiries").insert({
            "name": row["name"],
            "email": row["email"],
            "phone": row["phone"] or "",
            "message": row["message"],
            "safari_id": safari_id_map.get(row["safari_id"]),
            "received_at": row["received_at"],
        }).execute()

    sqlite.close()
    print(
        f"Migration complete: {len(safaris)} safaris, "
        f"{len(images)} gallery records, {len(inquiries)} inquiries."
    )


if __name__ == "__main__":
    main()
