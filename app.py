import os
import shutil
import time
from datetime import datetime
from functools import wraps
from urllib.parse import urlencode

from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

import db
from data import CONTACT_INFO, FAQS, SEED_SAFARIS, TESTIMONIALS
from i18n import (
    DEFAULT_LOCALE,
    LOCALE_NAMES,
    SUPPORTED_LOCALES,
    format_date,
    resolve_locale,
    translate,
)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
COVERS_DIR = os.path.join(BASE_DIR, "static", "covers")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-key-change-this-in-production")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB per request

# SameSite=Lax means browsers won't send the login cookie on a POST that
# originates from another website - which blocks the classic "malicious
# page auto-submits a Delete button while you're logged in" attack
# against the admin panel's POST forms (delete safari/photo/review, etc.).
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Every host this app supports (PythonAnywhere, Fly.io, Vercel, nginx)
# terminates HTTPS at a reverse proxy and forwards plain HTTP internally,
# setting standard X-Forwarded-* headers to say so. Without this, Flask
# sees every request as scheme=http, which would make canonical URLs,
# hreflang tags, and the sitemap all advertise the wrong (insecure)
# scheme - SEO-relevant, not just cosmetic.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Database: SQLite by default (a local file, zero setup), or a webhosted
# MySQL database if DB_HOST is set - e.g. the MySQL database included
# with every PythonAnywhere account. See the README's "Using a webhosted
# database" section for exactly what to set and where.
DB_HOST = os.environ.get("DB_HOST")
if DB_HOST:
    app.config["DB_BACKEND"] = "mysql"
    app.config["DB_HOST"] = DB_HOST
    app.config["DB_PORT"] = int(os.environ.get("DB_PORT", "3306"))
    app.config["DB_USER"] = os.environ.get("DB_USER", "")
    app.config["DB_PASSWORD"] = os.environ.get("DB_PASSWORD", "")
    app.config["DB_NAME"] = os.environ.get("DB_NAME", "")

    missing = [k for k in ("DB_USER", "DB_PASSWORD", "DB_NAME") if not app.config[k]]
    if missing:
        raise RuntimeError(
            f"DB_HOST is set, so the app is trying to use a MySQL database, "
            f"but {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} "
            f"not set. Set all of DB_HOST, DB_USER, DB_PASSWORD and DB_NAME "
            f"as environment variables - see the README's "
            f"'Using a webhosted database' section."
        )
    try:
        import pymysql  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "DB_HOST is set, so the app is trying to use a MySQL database, "
            "but the 'pymysql' package isn't installed. Run: "
            "pip install -r requirements.txt"
        )

    # Optional encrypted (TLS) connection - needed by most databases
    # reached over the public internet (Aiven, TiDB Cloud, ...).
    #
    #   DB_SSL=1            verify the server against the standard public CA
    #                       bundle (works for providers with publicly-trusted
    #                       certificates, e.g. TiDB Cloud)
    #   DB_SSL_CA=<path>    verify against this CA certificate file instead -
    #                       needed for providers that use their own private CA
    #                       (Aiven gives you a `ca.pem`). Setting this ALONE
    #                       is enough to turn TLS on; DB_SSL=1 isn't also
    #                       required. A relative path (like `ca.pem`) is
    #                       resolved against the project folder, not the
    #                       process's working directory, so it works the same
    #                       on Vercel, Fly.io and locally.
    #   DB_SSL_VERIFY_IDENTITY=0/1
    #                       also check the server's hostname against its
    #                       certificate. Defaults to on for the public CA
    #                       bundle, and off when you supply your own provider
    #                       CA (that CA only ever signs your provider's own
    #                       servers, and provider certificates don't always
    #                       list the connection hostname).
    ssl_ca_setting = os.environ.get("DB_SSL_CA", "").strip()
    ssl_flag = os.environ.get("DB_SSL", "").lower() in ("1", "true", "yes")
    app.config["DB_SSL"] = ssl_flag or bool(ssl_ca_setting)
    app.config["DB_SSL_CA"] = ""
    app.config["DB_SSL_VERIFY_IDENTITY"] = False

    if app.config["DB_SSL"]:
        if ssl_ca_setting:
            ca_path = (
                ssl_ca_setting
                if os.path.isabs(ssl_ca_setting)
                else os.path.join(BASE_DIR, ssl_ca_setting)
            )
            if not os.path.isfile(ca_path):
                raise RuntimeError(
                    f"DB_SSL_CA is set to '{ssl_ca_setting}', but no file was "
                    f"found at {ca_path}. Put your provider's CA certificate "
                    f"(e.g. Aiven's ca.pem) in the project folder, or give "
                    f"DB_SSL_CA as a full path."
                )
            app.config["DB_SSL_CA"] = ca_path
            default_verify_identity = False
        else:
            try:
                import certifi
            except ImportError:
                raise RuntimeError(
                    "DB_SSL is set, but the 'certifi' package isn't installed. "
                    "Run: pip install -r requirements.txt"
                )
            app.config["DB_SSL_CA"] = certifi.where()
            default_verify_identity = True

        verify_env = os.environ.get("DB_SSL_VERIFY_IDENTITY", "").strip().lower()
        if verify_env:
            app.config["DB_SSL_VERIFY_IDENTITY"] = verify_env in ("1", "true", "yes")
        else:
            app.config["DB_SSL_VERIFY_IDENTITY"] = default_verify_identity
else:
    if os.environ.get("VERCEL"):
        # Checked here, before os.makedirs below, so a missing DB_HOST on
        # Vercel produces this message instead of a bare "read-only
        # file system" OSError.
        raise RuntimeError(
            "Running on Vercel, but DB_HOST isn't set. Vercel's filesystem is "
            "read-only, so SQLite (a local file) can't work there - set "
            "DB_HOST, DB_USER, DB_PASSWORD and DB_NAME to use a webhosted "
            "MySQL database instead. See the README's 'Using a webhosted "
            "database' section."
        )
    app.config["DB_BACKEND"] = "sqlite"
    os.makedirs(DATA_DIR, exist_ok=True)
    app.config["DATABASE_PATH"] = os.environ.get(
        "DATABASE_PATH", os.path.join(DATA_DIR, "diani.db")
    )

# Photo storage: local disk by default (static/uploads/, static/covers/ -
# works fine on any host with a persistent, writable filesystem), or
# Cloudinary if CLOUDINARY_URL is set - required on hosts with a
# read-only filesystem, like Vercel, where local file uploads simply
# can't work. See the README's "Using cloud storage for uploads" section.
CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL")
USE_CLOUD_STORAGE = bool(CLOUDINARY_URL)
if USE_CLOUD_STORAGE:
    try:
        import cloudinary
        import cloudinary.uploader
    except ImportError:
        raise RuntimeError(
            "CLOUDINARY_URL is set, so the app is trying to upload photos "
            "to Cloudinary, but the 'cloudinary' package isn't installed. "
            "Run: pip install -r requirements.txt"
        )
    # cloudinary's own SDK reads CLOUDINARY_URL from the environment
    # automatically - no cloudinary.config(...) call needed here.

# Vercel specifically has a read-only filesystem (only /tmp is writable,
# and it doesn't persist between requests), so both of the local-storage
# defaults above are hard requirements to replace there, not just
# recommendations - fail loudly at startup rather than let uploads or
# the database silently misbehave later.
IS_VERCEL = bool(os.environ.get("VERCEL"))
if IS_VERCEL and not USE_CLOUD_STORAGE:
    raise RuntimeError(
        "Running on Vercel, but CLOUDINARY_URL isn't set. Vercel's "
        "filesystem is read-only, so photo uploads (gallery photos and "
        "safari cover photos) need cloud storage to work at all - set "
        "CLOUDINARY_URL. See the README's 'Using cloud storage for "
        "uploads' section."
    )

db.init_app(app)  # registers the connection-close hook

# Change this before deploying! Set ADMIN_PASSWORD as an environment
# variable in production rather than relying on the default below.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "diani2026")


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def ensure_upload_dirs(slugs):
    if USE_CLOUD_STORAGE:
        return
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    for slug in slugs:
        os.makedirs(os.path.join(UPLOAD_DIR, slug), exist_ok=True)


def bootstrap_database():
    """Create tables if missing, and seed the safaris on first run."""
    with app.app_context():
        db.init_db()
        if db.count_safaris() == 0:
            db.seed_safaris(SEED_SAFARIS)
        ensure_upload_dirs(db.get_all_slugs())


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# The only accent colors that actually have matching CSS classes
# (static/css/style.css). Adding a 7th means adding a new .accent-xxx
# rule there first.
ACCENT_CHOICES = ["lagoon", "ocean", "coral", "gold", "indigo", "sky"]


def blank_safari_raw():
    """An empty safari shape, for the 'add new safari' form - same
    structure get_safari_raw() returns for an existing one, just empty."""
    d = {"id": None, "slug": "", "accent": "lagoon", "emoji": "", "display_order": 0}
    for field in db.TRANSLATABLE_TEXT_FIELDS:
        d[field] = {loc: "" for loc in SUPPORTED_LOCALES}
    for field in db.TRANSLATABLE_LIST_FIELDS:
        d[field] = {loc: [] for loc in SUPPORTED_LOCALES}
    return d


def safari_to_form_values(raw):
    """Flatten a raw (unresolved, per-locale) safari dict into the same
    flat {field}_{locale} shape the edit form's inputs use, so the
    template can read e.g. form.name_en / form.itinerary_de directly."""
    values = {
        "id": raw["id"],
        "slug": raw["slug"],
        "accent": raw["accent"],
        "emoji": raw["emoji"],
        "display_order": raw["display_order"],
    }
    for field in db.TRANSLATABLE_TEXT_FIELDS:
        for loc in SUPPORTED_LOCALES:
            values[f"{field}_{loc}"] = raw[field].get(loc, "")
    for field in ("overview", "highlights", "included", "excluded", "bring"):
        for loc in SUPPORTED_LOCALES:
            values[f"{field}_{loc}"] = "\n".join(raw[field].get(loc, []))
    for loc in SUPPORTED_LOCALES:
        lines = [
            f"{stop.get('icon', '')} | {stop.get('title', '')} | {stop.get('description', '')}"
            for stop in raw["itinerary"].get(loc, [])
        ]
        values[f"itinerary_{loc}"] = "\n".join(lines)
    return values


def parse_itinerary_textarea(text):
    """Parse lines shaped 'icon | title | description' into stop dicts."""
    stops = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|", 2)]
        parts += [""] * (3 - len(parts))  # pad so short lines don't crash
        stops.append({"icon": parts[0], "title": parts[1], "description": parts[2]})
    return stops


def build_itinerary_field(form):
    """
    Combine the three itinerary_<locale> textareas into one
    {"en": [...], "de": [...], "fr": [...]} field. Stops are matched
    across languages by line position (line 1 of each textarea is the
    same stop), and the icon is always taken from the English line at
    that position when one exists - so admins only need to keep the
    icon consistent in the English textarea, not retype it three times.
    """
    per_locale = {loc: parse_itinerary_textarea(form.get(f"itinerary_{loc}", "")) for loc in SUPPORTED_LOCALES}
    en_stops = per_locale.get(DEFAULT_LOCALE, [])
    result = {}
    for loc, stops in per_locale.items():
        merged = []
        for i, stop in enumerate(stops):
            icon = en_stops[i]["icon"] if i < len(en_stops) and en_stops[i]["icon"] else stop["icon"]
            merged.append({"icon": icon, "title": stop["title"], "description": stop["description"]})
        result[loc] = merged
    return result


def parse_safari_form(form):
    """Build the data dict create_safari()/update_safari() expect from
    a submitted admin form. Returns (data, errors)."""
    errors = []
    data = {
        "accent": form.get("accent", "lagoon"),
        "emoji": form.get("emoji", "").strip(),
    }
    if data["accent"] not in ACCENT_CHOICES:
        data["accent"] = "lagoon"

    try:
        data["display_order"] = int(form.get("display_order", 0))
    except ValueError:
        data["display_order"] = 0

    for field in db.TRANSLATABLE_TEXT_FIELDS:
        data[field] = {loc: form.get(f"{field}_{loc}", "").strip() for loc in SUPPORTED_LOCALES}

    for field in ("overview", "highlights", "included", "excluded", "bring"):
        data[field] = {
            loc: [line.strip() for line in form.get(f"{field}_{loc}", "").splitlines() if line.strip()]
            for loc in SUPPORTED_LOCALES
        }

    data["itinerary"] = build_itinerary_field(form)

    if not data["name"].get(DEFAULT_LOCALE):
        errors.append("Please fill in at least the English name.")

    return data, errors


def save_cover_photo(file, slug, safari_id):
    """Save an uploaded cover photo - to Cloudinary (storing the
    resulting URL in the safaris.cover_url column) if cloud storage is
    active, otherwise as a local static/covers/<slug>.jpg file, same as
    always. Returns (success, message) - failure never blocks saving
    the rest of the safari, it just leaves the existing/no cover photo
    in place."""
    if not file or not file.filename:
        return True, None

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""

    if USE_CLOUD_STORAGE:
        if ext not in ALLOWED_EXTENSIONS:
            return False, "Cover photo skipped - please upload an image file."
        result = cloudinary.uploader.upload(
            file,
            folder="diani-sea-adventures/covers",
            public_id=slug,
            overwrite=True,
        )
        db.set_safari_cover_url(safari_id, result["secure_url"])
        return True, None

    if ext not in ("jpg", "jpeg"):
        return False, "Cover photo skipped - please upload a .jpg file."
    os.makedirs(COVERS_DIR, exist_ok=True)
    file.save(os.path.join(COVERS_DIR, f"{slug}.jpg"))
    return True, None


def upload_gallery_image(file, slug):
    """Save one uploaded gallery photo - to Cloudinary if cloud storage
    is active, otherwise to static/uploads/<slug>/, same as always.
    Returns the value to store in gallery_images.filename: a full URL
    for Cloudinary, or a local filename otherwise (see
    gallery_image_url(), which turns either shape back into a usable
    <img src> at display time)."""
    if USE_CLOUD_STORAGE:
        result = cloudinary.uploader.upload(file, folder=f"diani-sea-adventures/{slug}")
        return result["secure_url"]

    filename = secure_filename(file.filename)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    filename = f"{timestamp}_{filename}"
    folder = os.path.join(UPLOAD_DIR, slug)
    os.makedirs(folder, exist_ok=True)
    file.save(os.path.join(folder, filename))
    return filename


def gallery_image_url(slug, stored_value):
    """Turn a gallery_images.filename value into a full <img src> URL -
    it's either already a full URL (Cloudinary) or a local filename that
    needs the static/uploads/<slug>/ path built for it."""
    if stored_value.startswith(("http://", "https://")):
        return stored_value
    return url_for("static", filename=f"uploads/{slug}/{stored_value}", _external=True)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Please log in to access the admin area.", "error")
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


# ---------------------------------------------------------------------------
# Language detection
#
# Automatic, based on the visitor's browser/device language (the standard
# Accept-Language header every browser sends), NOT their IP address. This
# matters for a tourism site in particular: a German visitor's phone is
# still set to German once they land in Kenya, but their IP would just say
# "Kenya", which tells us nothing useful about what language to show. A
# manual switcher (?lang=xx, remembered for the session) always overrides
# the automatic guess.
#
# The admin dashboard is intentionally excluded from all of this and
# always renders in English - see inject_globals() below.
# ---------------------------------------------------------------------------

def get_locale():
    query_lang = request.args.get("lang")
    if query_lang in SUPPORTED_LOCALES:
        session["locale"] = query_lang
        return query_lang
    if session.get("locale") in SUPPORTED_LOCALES:
        return session["locale"]
    best = request.accept_languages.best_match(SUPPORTED_LOCALES)
    locale = best or DEFAULT_LOCALE
    session["locale"] = locale
    return locale


@app.before_request
def load_locale():
    g.locale = get_locale()


def safari_cover_url(safari):
    """Return a safari's cover photo URL, or None if it doesn't have one
    yet. Checks the database's cover_url column first (set when a cover
    photo was uploaded to cloud storage - see save_cover_photo), then
    falls back to the original local-file convention
    (static/covers/<slug>.jpg) so existing deployments that don't use
    cloud storage keep working exactly as before."""
    if safari.get("cover_url"):
        return safari["cover_url"]
    path = os.path.join(COVERS_DIR, f"{safari['slug']}.jpg")
    if os.path.exists(path):
        # _external=True: og:image and the TouristTrip structured data's
        # image field both require a fully-qualified URL, not a relative
        # path - harmless for the normal <img src> use of this same
        # value, which works identically either way.
        return url_for("static", filename=f"covers/{safari['slug']}.jpg", _external=True)
    return None


def localized_contact_info(locale):
    info = dict(CONTACT_INFO)
    info["hours"] = [
        (resolve_locale(h["day"], locale), h["time"]) for h in CONTACT_INFO["hours"]
    ]
    return info


def build_switch_urls():
    """
    Current page's URL with ?lang=xx swapped for each supported language,
    preserving any other query params (e.g. ?safari=... on the contact
    page). These double as both the nav language switcher's links AND
    the canonical/hreflang URLs in <head> (see base.html) - search
    engines require those to be absolute, so this always returns full
    https://... URLs rather than relative paths.
    """
    base = request.url_root.rstrip("/")
    urls = {}
    for code in SUPPORTED_LOCALES:
        args = request.args.to_dict(flat=True)
        args["lang"] = code
        urls[code] = base + request.path + "?" + urlencode(args)
    return urls


@app.context_processor
def inject_globals():
    is_admin_area = request.path.startswith("/admin")
    locale = DEFAULT_LOCALE if is_admin_area else g.locale

    safaris = db.get_all_safaris(locale)
    for s in safaris:
        s["cover_url"] = safari_cover_url(s)
        if is_admin_area:
            # The admin dashboard lists every safari's gallery thumbnails
            for img in s["images"]:
                img["url"] = gallery_image_url(s["slug"], img["filename"])

    switch_urls = build_switch_urls()

    pending_reviews_count = 0
    if is_admin_area and session.get("is_admin"):
        pending_reviews_count = db.count_pending_reviews()

    return {
        "pending_reviews_count": pending_reviews_count,
        "safaris": safaris,
        "contact": localized_contact_info(locale),
        "current_year": datetime.now().year,
        "tr": lambda key, **kw: translate(key, locale, **kw),
        "locale": locale,
        "SUPPORTED_LOCALES": SUPPORTED_LOCALES,
        "LOCALE_NAMES": LOCALE_NAMES,
        "switch_urls": switch_urls,
        # Each language variant is self-canonical (it's genuinely
        # different content per language, not a duplicate), with the
        # language's own ?lang=xx made explicit - see base.html's
        # hreflang tags, which use switch_urls for the same reason.
        "canonical_url": switch_urls[locale],
        # The site's homepage, for the sitewide TravelAgency structured
        # data's "url" field - deliberately NOT switch_urls (which is
        # always specific to the *current* page).
        "home_url": f"{request.url_root.rstrip('/')}{url_for('index')}",
        "use_cloud_storage": USE_CLOUD_STORAGE,
    }


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    locale = g.locale
    safaris_by_slug = {s["slug"]: s for s in db.get_all_safaris(locale)}

    testimonials = []
    for item in TESTIMONIALS:
        safari = safaris_by_slug.get(item["trip_slug"])
        testimonials.append(
            {
                "quote": resolve_locale(item["quote"], locale),
                "name": item["name"],
                "trip_name": safari["name"] if safari else "",
            }
        )

    faqs = [
        {"q": resolve_locale(item["q"], locale), "a": resolve_locale(item["a"], locale)}
        for item in FAQS
    ]

    faq_jsonld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": item["q"],
                "acceptedAnswer": {"@type": "Answer", "text": item["a"]},
            }
            for item in faqs
        ],
    }

    return render_template(
        "index.html", testimonials=testimonials, faqs=faqs, faq_jsonld=faq_jsonld
    )


REVIEW_COOLDOWN_SECONDS = 60
REVIEW_MIN_LENGTH = 10
REVIEW_MAX_LENGTH = 1500
REVIEW_NAME_MAX_LENGTH = 80


@app.route("/safari/<slug>", methods=["GET", "POST"])
def safari_detail(slug):
    locale = g.locale
    safari = db.get_safari_by_slug(slug, locale)
    if safari is None:
        abort(404)
    safari["cover_url"] = safari_cover_url(safari)

    # --- Guest review submission (POST to the same URL as the page) ------
    # Nothing submitted here is shown on the site until an admin approves
    # it. Spam protection is deliberately simple and dependency-free: a
    # hidden "website" field that real people never fill in (bots do), and
    # a one-review-per-minute cooldown per browser session.
    review_form = {"name": "", "rating": "", "body": ""}
    status_code = 200
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        body = request.form.get("body", "").strip()
        rating_raw = request.form.get("rating", "")
        review_form = {"name": name, "rating": rating_raw, "body": body}
        try:
            rating = int(rating_raw)
        except ValueError:
            rating = 0

        done_url = url_for("safari_detail", slug=slug, lang=locale, _anchor="reviews")
        if request.form.get("website"):
            # Honeypot tripped: look like a success, store nothing.
            flash(translate("review_flash_success", locale), "review_success")
            return redirect(done_url)

        now = time.time()
        if now - session.get("last_review_at", 0) < REVIEW_COOLDOWN_SECONDS:
            flash(translate("review_flash_wait", locale), "review_error")
            status_code = 400
        elif (
            not name
            or len(name) > REVIEW_NAME_MAX_LENGTH
            or rating not in (1, 2, 3, 4, 5)
            or not (REVIEW_MIN_LENGTH <= len(body) <= REVIEW_MAX_LENGTH)
        ):
            flash(translate("review_flash_error", locale), "review_error")
            status_code = 400
        else:
            db.add_review(safari["id"], name, rating, body)
            session["last_review_at"] = now
            flash(translate("review_flash_success", locale), "review_success")
            return redirect(done_url)

    reviews = db.get_public_reviews(safari["id"])
    for r in reviews:
        r["date_display"] = format_date(r["created_at"], locale)
    review_stats = db.get_review_stats(safari["id"])

    for img in safari["images"]:
        img["url"] = gallery_image_url(safari["slug"], img["filename"])

    others = db.get_other_safaris(slug, locale, limit=3)
    for o in others:
        o["cover_url"] = safari_cover_url(o)

    base = request.url_root.rstrip("/")
    page_url = f"{base}{url_for('safari_detail', slug=slug)}?lang={locale}"
    home_url = f"{base}{url_for('index')}"

    touristtrip_jsonld = {
        "@context": "https://schema.org",
        "@type": "TouristTrip",
        "name": safari["name"],
        "description": safari["teaser"],
        "url": page_url,
        "provider": {
            "@type": "TravelAgency",
            "name": "Diani Sea Adventures",
            "url": home_url,
        },
    }
    if safari.get("cover_url"):
        touristtrip_jsonld["image"] = safari["cover_url"]

    # Real, visible, approved reviews only. Typed as both TouristTrip and
    # Product because Google's review-star results are defined for
    # Product-like items; whether stars actually show is Google's call.
    if review_stats["count"]:
        touristtrip_jsonld["@type"] = ["TouristTrip", "Product"]
        touristtrip_jsonld["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": round(review_stats["average"], 1),
            "reviewCount": review_stats["count"],
            "bestRating": 5,
            "worstRating": 1,
        }
        touristtrip_jsonld["review"] = [
            {
                "@type": "Review",
                "author": {"@type": "Person", "name": r["name"]},
                "datePublished": r["created_at"][:10],
                "reviewRating": {
                    "@type": "Rating",
                    "ratingValue": r["rating"],
                    "bestRating": 5,
                    "worstRating": 1,
                },
                "reviewBody": r["body"],
            }
            for r in reviews[:10]
        ]

    breadcrumb_jsonld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem", "position": 1,
                "name": translate("detail_breadcrumb_home", locale),
                "item": home_url,
            },
            {
                "@type": "ListItem", "position": 2,
                "name": translate("detail_breadcrumb_safaris", locale),
                "item": f"{home_url}#safaris",
            },
            {
                "@type": "ListItem", "position": 3,
                "name": safari["name"],
                "item": page_url,
            },
        ],
    }

    return (
        render_template(
            "safari_detail.html",
            safari=safari,
            gallery=safari["images"],
            others=others,
            touristtrip_jsonld=touristtrip_jsonld,
            breadcrumb_jsonld=breadcrumb_jsonld,
            reviews=reviews,
            review_stats=review_stats,
            review_form=review_form,
        ),
        status_code,
    )


@app.route("/contact", methods=["GET", "POST"])
def contact():
    locale = g.locale
    preselect = request.args.get("safari", "")
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        safari_slug = request.form.get("safari", "").strip()
        message = request.form.get("message", "").strip()

        if not name or not email or not message:
            flash(translate("contact_flash_missing", locale), "error")
            return render_template("contact.html", preselect=safari_slug)

        safari = db.get_safari_by_slug(safari_slug, locale) if safari_slug else None
        db.add_inquiry(
            name=name,
            email=email,
            phone=phone,
            message=message,
            safari_id=safari["id"] if safari else None,
        )

        flash(translate("contact_flash_success", locale), "success")
        return redirect(url_for("contact"))

    return render_template("contact.html", preselect=preselect)


@app.route("/sitemap.xml")
def sitemap():
    """
    Lists every public page in every language, with hreflang alternate
    links so search engines understand the English/German/French
    versions of a page are intentional translations of each other
    (not duplicate or unrelated content) - see base.html's matching
    <link rel="alternate" hreflang="..."> tags for the same data used
    on the pages themselves.
    """
    base = request.url_root.rstrip("/")

    # (endpoint, url_for kwargs) for every page that doesn't depend on
    # database content; safari pages are added separately below.
    static_pages = [("index", {}), ("contact", {})]
    safari_pages = [("safari_detail", {"slug": slug}) for slug in db.get_all_slugs()]

    entries = []
    for endpoint, kwargs in static_pages + safari_pages:
        alternates = {
            loc: f"{base}{url_for(endpoint, **kwargs)}?lang={loc}"
            for loc in SUPPORTED_LOCALES
        }
        for loc in SUPPORTED_LOCALES:
            entries.append({"loc": alternates[loc], "alternates": alternates})

    xml = render_template("sitemap.xml", entries=entries)
    return Response(xml, mimetype="application/xml")


@app.route("/robots.txt")
def robots_txt():
    base = request.url_root.rstrip("/")
    lines = [
        "User-agent: *",
        "Disallow: /admin/",
        "Allow: /",
        f"Sitemap: {base}/sitemap.xml",
    ]
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


# ---------------------------------------------------------------------------
# Admin routes
#
# Deliberately not translated - only the site's own staff use this area,
# so it always renders in English regardless of visitor locale detection
# (see inject_globals() above, which forces locale="en" for /admin/*).
# ---------------------------------------------------------------------------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == ADMIN_PASSWORD:
            session["is_admin"] = True
            flash("Welcome back.", "success")
            next_url = request.form.get("next") or url_for("admin_dashboard")
            return redirect(next_url)
        flash("Incorrect password.", "error")
    return render_template("admin_login.html", next=request.args.get("next", ""))


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    flash("You've been logged out.", "success")
    return redirect(url_for("index"))


@app.route("/admin/dashboard")
@login_required
def admin_dashboard():
    inquiries = db.get_all_inquiries(DEFAULT_LOCALE)
    return render_template("admin_dashboard.html", inquiries=inquiries)


@app.route("/admin/upload/<slug>", methods=["POST"])
@login_required
def admin_upload(slug):
    safari = db.get_safari_by_slug(slug, DEFAULT_LOCALE)
    if safari is None:
        abort(404)

    files = request.files.getlist("photos")
    if not files or all(f.filename == "" for f in files):
        flash("No files were selected.", "error")
        return redirect(url_for("admin_dashboard"))

    saved, skipped = 0, 0

    for file in files:
        if file and file.filename and allowed_file(file.filename):
            stored_value = upload_gallery_image(file, slug)
            db.add_gallery_image(safari["id"], stored_value)
            saved += 1
        elif file and file.filename:
            skipped += 1

    if saved:
        flash(f"Uploaded {saved} photo(s) to {safari['name']}.", "success")
    if skipped:
        flash(f"Skipped {skipped} file(s) with an unsupported format.", "error")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/delete-image/<int:image_id>", methods=["POST"])
@login_required
def admin_delete_image(image_id):
    image = db.get_gallery_image(image_id)
    if image is None:
        abort(404)
    # A cloud-hosted photo is stored as a full URL, not a local filename
    # - there's nothing on this server's disk to remove for those (see
    # upload_gallery_image()). This checks the stored value itself
    # rather than the current USE_CLOUD_STORAGE setting, so it keeps
    # working correctly even for older photos uploaded before a switch
    # from local storage to cloud storage, or vice versa.
    if not image["filename"].startswith(("http://", "https://")):
        filepath = os.path.join(UPLOAD_DIR, image["safari_slug"], image["filename"])
        if os.path.exists(filepath):
            os.remove(filepath)
    db.delete_gallery_image(image_id)
    flash("Photo deleted.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/safaris")
@login_required
def admin_safaris():
    safaris = db.get_all_safaris(DEFAULT_LOCALE)
    for s in safaris:
        s["cover_url"] = safari_cover_url(s)
    return render_template("admin_safaris_list.html", safaris=safaris)


@app.route("/admin/safaris/new", methods=["GET", "POST"])
@login_required
def admin_safari_new():
    if request.method == "POST":
        data, errors = parse_safari_form(request.form)

        base_slug = db.slugify(request.form.get("slug") or data["name"][DEFAULT_LOCALE])
        data["slug"] = db.unique_slug(base_slug)

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template(
                "admin_safari_form.html", form=request.form, is_new=True,
                accent_choices=ACCENT_CHOICES,
            )

        db.create_safari(data)
        ensure_upload_dirs([data["slug"]])

        new_safari = db.get_safari_raw(data["slug"])
        ok, msg = save_cover_photo(request.files.get("cover_photo"), data["slug"], new_safari["id"])
        if not ok:
            flash(msg, "error")

        flash(f"Added {data['name'][DEFAULT_LOCALE]}.", "success")
        return redirect(url_for("admin_safaris"))

    form_values = safari_to_form_values(blank_safari_raw())
    return render_template(
        "admin_safari_form.html", form=form_values, is_new=True,
        accent_choices=ACCENT_CHOICES,
    )


@app.route("/admin/safaris/<slug>/edit", methods=["GET", "POST"])
@login_required
def admin_safari_edit(slug):
    raw = db.get_safari_raw(slug)
    if raw is None:
        abort(404)

    if request.method == "POST":
        data, errors = parse_safari_form(request.form)
        data["slug"] = raw["slug"]  # slug is fixed once created - see the form's note

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template(
                "admin_safari_form.html", form=request.form, is_new=False,
                slug=raw["slug"], accent_choices=ACCENT_CHOICES,
                cover_url=safari_cover_url(raw),
            )

        db.update_safari(raw["id"], data)

        ok, msg = save_cover_photo(request.files.get("cover_photo"), raw["slug"], raw["id"])
        if not ok:
            flash(msg, "error")

        flash(f"Saved changes to {data['name'][DEFAULT_LOCALE]}.", "success")
        return redirect(url_for("admin_safaris"))

    form_values = safari_to_form_values(raw)
    return render_template(
        "admin_safari_form.html", form=form_values, is_new=False,
        slug=raw["slug"], accent_choices=ACCENT_CHOICES,
        cover_url=safari_cover_url(raw),
    )


@app.route("/admin/safaris/<slug>/delete", methods=["POST"])
@login_required
def admin_safari_delete(slug):
    raw = db.get_safari_raw(slug)
    if raw is None:
        abort(404)

    db.delete_safari(raw["id"])

    upload_folder = os.path.join(UPLOAD_DIR, slug)
    if os.path.isdir(upload_folder):
        shutil.rmtree(upload_folder)

    cover_path = os.path.join(COVERS_DIR, f"{slug}.jpg")
    if os.path.exists(cover_path):
        os.remove(cover_path)

    flash(f"Deleted {raw['name'].get(DEFAULT_LOCALE, slug)}.", "success")
    return redirect(url_for("admin_safaris"))


@app.route("/admin/reviews")
@login_required
def admin_reviews():
    counts = db.count_reviews_by_filter()
    current = request.args.get("filter")
    if current not in db.REVIEW_FILTERS:
        # Land on the work queue if there is one, otherwise show everything
        current = "pending" if counts["pending"] else "all"
    reviews = db.get_reviews_for_admin(current, DEFAULT_LOCALE)
    for r in reviews:
        r["date_display"] = format_date(r["created_at"], DEFAULT_LOCALE)
    return render_template(
        "admin_reviews.html", reviews=reviews, counts=counts, current=current
    )


@app.route("/admin/reviews/<int:review_id>/<action>", methods=["POST"])
@login_required
def admin_review_action(review_id, action):
    review = db.get_review(review_id)
    if review is None:
        abort(404)

    if action == "approve":
        db.set_review_status(review_id, "approved")
        flash("Review approved - it's now visible on the site (unless hidden).", "success")
    elif action == "disapprove":
        db.set_review_status(review_id, "disapproved")
        flash("Review disapproved - it will not appear on the site.", "success")
    elif action == "hide":
        db.set_review_hidden(review_id, True)
        flash("Review hidden from the site.", "success")
    elif action == "unhide":
        db.set_review_hidden(review_id, False)
        flash("Review unhidden.", "success")
    elif action == "delete":
        db.delete_review(review_id)
        flash("Review deleted permanently.", "success")
    else:
        abort(404)

    back = request.form.get("filter")
    if back not in db.REVIEW_FILTERS:
        back = "all"
    return redirect(url_for("admin_reviews", filter=back))


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


# ---------------------------------------------------------------------------
# CLI helper: `flask --app app reset-db` wipes and reseeds the database -
# handy in development, but it permanently deletes all galleries and
# enquiries, so don't run it in production without a backup.
# ---------------------------------------------------------------------------

@app.cli.command("reset-db")
def reset_db_command():
    with app.app_context():
        db.drop_all_tables()
        db.init_db()
        db.seed_safaris(SEED_SAFARIS)
    print("Database reset and reseeded.")


bootstrap_database()

if __name__ == "__main__":
    app.run(debug=True)
