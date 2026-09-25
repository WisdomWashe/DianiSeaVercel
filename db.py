"""
Database layer for Diani Sea Adventures using Supabase/PostgreSQL.

The Flask app talks to Supabase through the server-side Python client.
No SQLite database file is required or used.

Required environment variables:
    SUPABASE_URL
    SUPABASE_SECRET_KEY

For older Supabase projects, SUPABASE_SERVICE_ROLE_KEY is accepted as a
fallback. Prefer SUPABASE_SECRET_KEY for new deployments.
"""

import re
from datetime import datetime, timezone

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


def _supabase():
    """Return the server-side Supabase client for the current Flask app."""
    if "supabase" not in g:
        url = current_app.config.get("SUPABASE_URL")
        key = current_app.config.get("SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "Supabase is not configured. Set SUPABASE_URL and "
                "SUPABASE_SECRET_KEY in the hosting environment."
            )
        g.supabase = create_client(url, key)
    return g.supabase


def get_db():
    """Backward-compatible alias used by the CLI reset command."""
    return _supabase()


def close_db(exc=None):
    g.pop("supabase", None)


def init_app(app):
    app.teardown_appcontext(close_db)


def init_db():
    """
    Validate that the configured Supabase database is reachable and that the
    required schema exists. Schema creation belongs in supabase_schema.sql,
    which is run once in the Supabase SQL Editor.
    """
    _supabase().table("safaris").select("id").limit(1).execute()


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
    result = (
        _supabase()
        .table("safaris")
        .select("*")
        .order("display_order")
        .execute()
    )
    return [_safari_row_to_dict(r, locale) for r in (result.data or [])]


def get_safari_by_slug(slug, locale=DEFAULT_LOCALE):
    result = (
        _supabase()
        .table("safaris")
        .select("*")
        .eq("slug", slug)
        .limit(1)
        .execute()
    )
    row = (result.data or [None])[0]
    return _safari_row_to_dict(row, locale) if row else None


def get_other_safaris(slug, locale=DEFAULT_LOCALE, limit=3):
    result = (
        _supabase()
        .table("safaris")
        .select("*")
        .neq("slug", slug)
        .order("display_order")
        .limit(limit)
        .execute()
    )
    return [_safari_row_to_dict(r, locale) for r in (result.data or [])]


def count_safaris():
    result = _supabase().table("safaris").select("id").execute()
    return len(result.data or [])


def get_all_slugs():
    result = _supabase().table("safaris").select("slug").execute()
    return [r["slug"] for r in (result.data or [])]


def _safari_row_to_raw_dict(row):
    d = dict(row)
    for field in ALL_TRANSLATABLE_FIELDS:
        d[field] = d.get(field) or {}
    d["images"] = get_gallery_images(d["id"])
    return d


def get_safari_raw(slug):
    result = (
        _supabase()
        .table("safaris")
        .select("*")
        .eq("slug", slug)
        .limit(1)
        .execute()
    )
    row = (result.data or [None])[0]
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
    result = (
        _supabase()
        .table("safaris")
        .select("display_order")
        .order("display_order", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return (rows[0]["display_order"] if rows else -1) + 1


def create_safari(data):
    payload = dict(data)
    for field in ALL_TRANSLATABLE_FIELDS:
        payload[field] = payload.get(field, {})
    payload["display_order"] = next_display_order()

    result = _supabase().table("safaris").insert(payload).execute()
    if not result.data:
        raise RuntimeError("Supabase did not return the newly created safari.")
    return result.data[0]["slug"]


def update_safari(safari_id, data):
    payload = dict(data)
    for field in ALL_TRANSLATABLE_FIELDS:
        payload[field] = payload.get(field, {})
    _supabase().table("safaris").update(payload).eq("id", safari_id).execute()


def delete_safari(safari_id):
    _supabase().table("safaris").delete().eq("id", safari_id).execute()


def seed_safaris(seed_list):
    if not seed_list:
        return
    payloads = []
    for order, entry in enumerate(seed_list):
        payload = dict(entry)
        for field in ALL_TRANSLATABLE_FIELDS:
            payload[field] = payload.get(field, {})
        payload["display_order"] = order
        payloads.append(payload)
    _supabase().table("safaris").insert(payloads).execute()


def get_gallery_images(safari_id):
    result = (
        _supabase()
        .table("gallery_images")
        .select("*")
        .eq("safari_id", safari_id)
        .order("uploaded_at", desc=True)
        .execute()
    )
    return [_image_row_to_dict(r) for r in (result.data or [])]


def add_gallery_image(safari_id, filename):
    _supabase().table("gallery_images").insert({
        "safari_id": safari_id,
        "filename": filename,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }).execute()


def get_gallery_image(image_id):
    result = (
        _supabase()
        .table("gallery_images")
        .select("*")
        .eq("id", image_id)
        .limit(1)
        .execute()
    )
    row = (result.data or [None])[0]
    if not row:
        return None

    safari_result = (
        _supabase()
        .table("safaris")
        .select("slug")
        .eq("id", row["safari_id"])
        .limit(1)
        .execute()
    )
    safari = (safari_result.data or [None])[0]
    row["safari_slug"] = safari["slug"] if safari else None
    return row


def delete_gallery_image(image_id):
    _supabase().table("gallery_images").delete().eq("id", image_id).execute()


def add_inquiry(name, email, phone, message, safari_id):
    _supabase().table("inquiries").insert({
        "name": name,
        "email": email,
        "phone": phone,
        "message": message,
        "safari_id": safari_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }).execute()


def get_all_inquiries(locale=DEFAULT_LOCALE):
    result = (
        _supabase()
        .table("inquiries")
        .select("*")
        .order("received_at", desc=True)
        .execute()
    )
    safaris_by_id = {s["id"]: s for s in get_all_safaris(locale)}
    return [_inquiry_row_to_dict(r, safaris_by_id) for r in (result.data or [])]


def reset_database(seed_list):
    """Delete application rows from Supabase and reseed the safari catalog."""
    client = _supabase()
    client.table("gallery_images").delete().gte("id", 0).execute()
    client.table("inquiries").delete().gte("id", 0).execute()
    client.table("safaris").delete().gte("id", 0).execute()
    seed_safaris(seed_list)
