# Diani Sea Adventures

A Flask website for a sea excursion company on Diani Beach, Kenya, with six
excursion categories, each on its own detail page with a photo gallery you
can manage as an administrator. Automatically shown in English, German or
French depending on the visitor's browser language.

## Features

- Home page with all six excursions, "why choose us", testimonials, and FAQ
- A dedicated page per excursion: full details, quick facts, what's
  included, what to bring, a step-by-step itinerary timeline with icons,
  and a photo gallery
- Cover photo per excursion (`static/covers/<slug>.jpg`) shown on the
  homepage, detail page hero, and related-excursions cards. If a cover
  photo hasn't been added yet, the site falls back to a colored block with
  an emoji instead of a broken image - so it's safe to add a new excursion
  before you have a professional photo of it.
- **Automatic language detection** - the site reads the visitor's browser
  language (the standard `Accept-Language` header every browser sends) and
  shows English, German, or French accordingly, with a manual switcher in
  the nav that overrides the guess and is remembered for the session. See
  "Languages" below for how this works and how to add more.
- Contact page with an enquiry form (saved to the database) and a map
- Password-protected admin dashboard to add, edit, and delete safaris
  (including their itinerary), upload/delete gallery and cover photos, and
  review enquiries - deliberately English-only, since it's for site staff,
  not visitors. See "Managing safaris from the admin panel" below.
- **A real SQLite database** (`data/diani.db`) by default for excursions,
  gallery photo metadata, and enquiries - built on Python's built-in
  `sqlite3` module, nothing extra to install. Can switch to a webhosted
  MySQL database instead with a few environment variables - see "Using a
  webhosted database" below.
- **Deployable to hosts with no persistent disk (e.g. Vercel)** by
  switching photo uploads over to cloud storage (Cloudinary) - see "Using
  cloud storage for uploads" and "Deploying to Vercel" below. Also
  includes ready-to-use deployment setups for PythonAnywhere, Fly.io
  (`Dockerfile`/`fly.toml`), and any host that runs a WSGI server.
- Mobile-first, responsive layout with no heavy front-end frameworks

## Getting started

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000 in your browser.

## Languages

The site ships with English, German and French. Language detection works
like this, in order:

1. If the URL has `?lang=de` (or `en`/`fr`), that language is used and
   remembered for the visitor's session - this is what the nav switcher
   does.
2. Otherwise, if a language was already chosen earlier in the session,
   that's used again.
3. Otherwise, the visitor's browser `Accept-Language` header is matched
   against the supported languages, falling back to English if there's no
   match.

This deliberately does **not** use IP-based geolocation: a tourist's phone
is usually still set to their home language regardless of which country
they're physically in, so the browser's own language setting is a more
reliable signal than "what country does this IP belong to" - and it needs
no external service, API key, or GeoIP database.

**Adding a fourth language:** add a new key (e.g. `"es"`) to every dict in
`i18n.py`'s `UI_STRINGS`, add `"es"` to `SUPPORTED_LOCALES` and give it a
display name in `LOCALE_NAMES`, then add an `"es"` key alongside the
existing `"en"`/`"de"`/`"fr"` entries for every translatable field in each
safari in `data.py` (and in `TESTIMONIALS`/`FAQS`). Existing safaris in an
already-seeded database won't pick up the new language automatically - use
`flask --app app reset-db` to reseed, or add the missing translations to
the database rows directly.

**Admin dashboard is English-only on purpose** - only your own staff use
it, so translating internal tooling wasn't worth the extra maintenance.

## Admin access

Go to the "Admin" link in the footer (or visit `/admin/login`).

- Default password: `diani2026`

**Change this before putting the site online.** Set an environment variable
instead of using the default:

```bash
export ADMIN_PASSWORD="a-strong-password-of-your-choice"
export SECRET_KEY="a-long-random-string"
python app.py
```

On Windows (PowerShell):

```powershell
$env:ADMIN_PASSWORD="a-strong-password-of-your-choice"
$env:SECRET_KEY="a-long-random-string"
python app.py
```

From the admin dashboard you can:
- Upload one or more photos (JPG, PNG, WEBP, GIF) to any safari's gallery
- Delete photos from a gallery
- View enquiries submitted through the contact form
- Go to **Manage safaris** to add, edit, or delete safaris (see below)

## Managing safaris from the admin panel

Go to **Manage safaris** from the admin dashboard nav. From there:

- **Add a new safari** - fills in a form for every field (name, pricing,
  what's included, itinerary, etc.) in English, German, and French. You
  only *have* to fill in the English name; anything left blank in German
  or French falls back to showing the English text on the site until you
  come back and translate it.
- **Edit an existing safari** - same form, pre-filled with the current
  content.
- **Delete a safari** - removes it, its gallery photos, and its cover
  photo permanently. There's a confirmation prompt since this can't be
  undone.
- **Upload a cover photo** - right on the edit form now, as a `.jpg`
  upload. No cover photo yet? The site shows the emoji + accent color
  fallback automatically, same as it always has.

**The URL slug is fixed once a safari is created** - it can't be edited
later. This is deliberate: gallery photo folders and cover photos are
matched to a safari by its slug, and renaming it would silently orphan
them. If you really need to change a safari's URL, delete it and create a
new one (re-uploading its photos), or edit the `slug` column directly in
the database.

**Editing the itinerary:** each language has its own textarea, one stop
per line, formatted as `icon | title | short description`, for example:

```
🏨 | Hotel pick-up | We collect you from your accommodation.
⛵ | Board the boat | Head out to search for dolphins.
```

The **icon only needs to be typed in the English textarea** - the German
and French versions of the same stop (matched by line number) reuse it
automatically, so you don't have to retype an emoji three times. Keep the
same number of lines, in the same order, across all three languages so
they line up correctly; if a translation is well short of the others for
now, that's fine, it just won't have a translated itinerary until you
catch it up.

## The database

Safari details, gallery photo metadata, and contact-form enquiries live in
a SQLite database at `data/diani.db`. It's created automatically the first
time you run the app, and seeded with the six default excursions from
`data.SEED_SAFARIS`.

> **Upgrading from an older version of this project?** Safari text fields
> used to be stored as plain strings; they're now stored as
> `{"en": ..., "de": ..., "fr": ...}` JSON so each field can hold all three
> languages. An existing `data/diani.db` from before this change is **not**
> compatible - run `flask --app app reset-db` to migrate (see the warning
> below: back up first if you've collected real photos or enquiries).

Three tables:

- `safaris` - one row per excursion category
- `gallery_images` - one row per uploaded photo, linked to a safari
  (the actual image file lives on disk under `static/uploads/<slug>/`)
- `inquiries` - contact-form submissions, optionally linked to a safari

**Editing safari content:** the admin panel's **Manage safaris** page (see
above) is the normal way to add, edit, or delete safaris now - no need to
touch `data.py` or the database directly for day-to-day changes.

`data.py`'s `SEED_SAFARIS` only matters before the database exists yet
(the very first run) or if you want to bulk-edit content by hand and
reseed from scratch. Once the database is seeded, editing `data.py` has
no effect on existing safaris until you either edit the database rows
directly (e.g. with the `sqlite3` CLI or
[DB Browser for SQLite](https://sqlbrowser.org/)) or wipe and reseed
everything with:

```bash
flask --app app reset-db
```

**Warning:** `reset-db` permanently deletes every safari, gallery photo
record, and enquiry currently in the database - including anything added
or edited through the admin panel - and replaces them with a fresh copy
of `data.py`'s `SEED_SAFARIS`. It won't delete photo *files* on disk
(you'd clear `static/uploads/` separately for a full reset). Back up
`data/diani.db` first if there's anything in it you'd miss.

`TESTIMONIALS`, `FAQS`, and `CONTACT_INFO` aren't stored in the database
at all, so those three are still edited directly in `data.py`, any time,
and take effect on the next page load with no reseeding needed.

**Note on the Jet Ski Safari:** unlike the other five excursions, this one
wasn't sourced from a real excursions page - it was written from general
knowledge of how jet ski rentals typically run on Diani Beach (short,
tide-dependent, guided sessions through a marked reef channel). The price
and duration are a reasonable placeholder, not confirmed real rates -
double-check and update them (through the admin panel, easiest) before
this goes live. It also doesn't have a cover photo yet - add one through
the edit form whenever you have it; until then the site shows a colored
block with a 🚤 instead.

**Using a real webhosted database instead of the local SQLite file?** See
"Using a webhosted database" below - the app already supports this, no
code changes needed, just environment variables.

## Using a webhosted database

By default this app stores everything in the local SQLite file described
above. If you'd rather the database itself lived on a database *server*,
set these environment variables and the app switches over automatically
- no code changes needed:

```bash
export DB_HOST="your-database-host"
export DB_USER="your-database-user"
export DB_PASSWORD="your-database-password"
export DB_NAME="your-database-name"
```

Setting `DB_HOST` at all is what tells the app to use MySQL instead of
SQLite - if it's unset, everything works exactly as before with the local
file, so this is entirely opt-in. Which host you point these at depends
on where the *app itself* is deployed:

- **On PythonAnywhere** → use the MySQL database included with your
  account (free, zero extra signup). See "PythonAnywhere's built-in
  MySQL" below.
- **On Vercel, or anywhere else with no persistent disk** → PythonAnywhere's
  MySQL won't work here (see why below) - you need a MySQL-compatible
  database reachable over the public internet instead. See "A publicly
  reachable MySQL database" below.
- **On Fly.io, a VPS, or anywhere with persistent disk** → the default
  local SQLite file is genuinely fine; only bother with either option
  above if you specifically want the data on a separate server.

### PythonAnywhere's built-in MySQL

1. Go to the **Databases** tab in your PythonAnywhere dashboard.
2. If you haven't already, set a MySQL password there (this is separate
   from your PythonAnywhere login password).
3. Under "Create a database", create one - call it `dianidb` (any name
   works, PythonAnywhere will prefix it with `yourusername$` for you
   automatically).
4. Set the four environment variables above using the hostname shown on
   that page (`yourusername.mysql.pythonanywhere-services.com`), user
   `yourusername`, and name `yourusername$dianidb` (dollar sign
   included) - in your WSGI config file (see "Deploying to
   PythonAnywhere" below for exactly where, since PythonAnywhere doesn't
   run your app through a shell that would remember `export`).
5. Reload your web app. The very first request creates the three tables
   and seeds the six default safaris automatically - no SQL to run by
   hand.

**Important:** this database is **only reachable from within
PythonAnywhere itself** on free/lower plans. That's fine if your app
also runs on PythonAnywhere, but it means this specific option **will
not work for a Vercel deployment** - Vercel's servers can't reach it.
For Vercel, use a publicly-reachable database instead (next section).

### A publicly reachable MySQL database

Needed for Vercel (or any host without its own database service reachable
from where your app runs). A few providers with usable free tiers as of
this writing: **Aiven** and **TiDB Cloud** both offer a free MySQL-compatible
database reachable over the public internet - search their current sign-up
pages, since free-tier terms shift over time. Whichever you pick, you'll
get a hostname, port, username, password, and database name - set those as
`DB_HOST`, `DB_PORT` (only if it's not the standard 3306), `DB_USER`,
`DB_PASSWORD`, `DB_NAME`.

**These almost always require an encrypted connection.** Set:

```bash
export DB_SSL=1
```

This validates the server against the standard public CA bundle (via the
`certifi` package, already in `requirements.txt`). If your provider gives
you its own CA certificate file instead of using a publicly-trusted one,
point `DB_SSL_CA` at that file's path instead of setting `DB_SSL`.

> **Testing note:** the MySQL code path was written carefully against
> documented PyMySQL and MySQL behavior, and the SQLite path (which
> shares all the same query logic) is fully tested - but I wasn't able
> to run this against a live MySQL server before handing it to you,
> since that requires a real database connection. After you set this
> up, load the homepage and try adding a safari through the admin panel
> as a quick end-to-end check; if anything errors, your host's function
> logs will show exactly what failed and I can fix it from there.

### Common to both

**This does not migrate existing data.** Switching `DB_HOST` on doesn't
copy anything from your local SQLite file - MySQL starts out empty and
gets seeded fresh from `data.py`. If you've already added real safaris,
photos, or enquiries locally that you want to keep, either recreate them
through the admin panel after switching, or migrate the rows by hand
(export from SQLite, adjust for the schema differences noted in `db.py`,
import into MySQL) before you rely on the new database.

**Using Postgres instead of MySQL** would need one small code change:
`db.py`'s webhosted-database connection and schema are written for
PyMySQL specifically, so a Postgres driver (e.g. `psycopg`) and its
slightly different SQL dialect would need swapping in - ask if you'd
like this added.

## Using cloud storage for uploads

Gallery photos and safari cover photos are saved to local disk
(`static/uploads/`, `static/covers/`) by default - works fine on any host
with a persistent, writable filesystem (PythonAnywhere, Fly.io, a VPS).

**Vercel's filesystem is read-only**, so local uploads cannot work there
at all - photos would appear to upload successfully and then silently
vanish. For Vercel (or any similar host), set:

```bash
export CLOUDINARY_URL="cloudinary://<api_key>:<api_secret>@<cloud_name>"
```

[Cloudinary](https://cloudinary.com) has a generous free tier built
specifically for exactly this (image hosting with a CDN in front of it).
After creating a free account, this single connection string is on your
dashboard's home page - Cloudinary's own SDK reads it automatically, no
further configuration needed.

Setting `CLOUDINARY_URL` at all is what switches uploads over - unset, the
app behaves exactly as before with local files. When it's set:

- New gallery photo uploads go to Cloudinary; the safari page displays
  them directly from Cloudinary's CDN.
- A cover photo uploaded through the admin panel's edit form also goes to
  Cloudinary, and its URL is saved in the database (a `cover_url` column
  that's otherwise unused) - this takes priority over any local
  `static/covers/<slug>.jpg` file for that safari.
- Any accepted image format works for uploads (not just `.jpg`), since
  Cloudinary handles format conversion.

**Deleting a photo through the admin panel removes it from the site
immediately** (the database row is deleted), but this app does not also
delete the underlying file from your Cloudinary account - it's left as a
harmless orphan you can clean up from Cloudinary's own dashboard if you
ever care about storage limits. This was a deliberate simplicity
trade-off; ask if you'd rather have true delete-everywhere behavior.

> **Testing note:** same caveat as the database above - I tested this
> logic thoroughly against a stand-in that mimics Cloudinary's SDK
> response shape, but couldn't upload to a real Cloudinary account from
> here. Try uploading one gallery photo after setup as a quick check.

## Deploying to Vercel

Vercel is a serverless platform: there's no persistent disk at all (only
`/tmp`, which doesn't survive between requests), so **both of the above
are required, not optional, on Vercel** - the app will refuse to start
with a clear error if `DB_HOST` or `CLOUDINARY_URL` is missing when it
detects it's running on Vercel.

1. Set up a publicly-reachable MySQL database (see "A publicly reachable
   MySQL database" above) and a Cloudinary account (see "Using cloud
   storage for uploads" above).
2. In your Vercel project's settings, add these environment variables:
   `SECRET_KEY`, `ADMIN_PASSWORD`, `DB_HOST`, `DB_USER`, `DB_PASSWORD`,
   `DB_NAME`, `DB_SSL` (or `DB_SSL_CA`), `CLOUDINARY_URL`.
3. No `vercel.json` is required - Vercel auto-detects `app.py` at the
   project root as a Flask app (a variable named `app`, which this
   project already has) and handles routing for you.
4. Deploy. Vercel runs `pip install -r requirements.txt` automatically
   from that file.
5. The `.vercelignore` file included in this project excludes
   `static/uploads/`, `data/`, and the Docker/Fly.io-specific files from
   the deployment - none of those are used on Vercel anyway.

**Function execution time limits:** Vercel's Python runtime has a request
time limit that varies by plan (seconds on the free Hobby tier, longer on
paid plans) - fine for this app's normal page loads and photo uploads,
just worth knowing if a very large gallery upload batch ever times out.

**Cold starts:** as a serverless platform, Vercel may spin down an idle
deployment and take a moment to restart it on the next visit - the first
request after a quiet period may be slower than subsequent ones. This is
normal Vercel behavior, not specific to this app.

## Deploying to PythonAnywhere

If you're seeing `ModuleNotFoundError: No module named 'flask_app'` (or
similar) in your error log, it's because PythonAnywhere's auto-generated
WSGI file assumes a different project layout than this one. Fix:

1. On the **Web** tab, click the link to your WSGI configuration file
   (something like `/var/www/yourusername_pythonanywhere_com_wsgi.py`).
2. Replace its contents with:

```python
import sys
import os

# Change this to wherever you uploaded/cloned the project
project_home = "/home/yourusername/diani-sea-adventures"
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Environment variables the app needs - PythonAnywhere's web server
# doesn't run your app through a shell, so `export` in a bash console
# has no effect here. Set the real values directly:
os.environ["SECRET_KEY"] = "a-long-random-string"
os.environ["ADMIN_PASSWORD"] = "a-strong-password-of-your-choice"

# Uncomment these four lines only if you're using a webhosted MySQL
# database - see "Using a webhosted database" above. Leave them
# commented out to keep using the local SQLite file.
# os.environ["DB_HOST"] = "yourusername.mysql.pythonanywhere-services.com"
# os.environ["DB_USER"] = "yourusername"
# os.environ["DB_PASSWORD"] = "your-mysql-password"
# os.environ["DB_NAME"] = "yourusername$dianidb"

from app import app as application  # noqa
```

3. Make sure `project_home` matches wherever you actually uploaded the
   project's files on PythonAnywhere (check the **Files** tab if unsure).
4. On the **Web** tab, confirm the virtualenv path points at a virtualenv
   where you've run `pip install -r requirements.txt`.
5. Click the green **Reload** button.

The key line is the last one: `from app import app as application` -
this project's entry point is `app.py` (not `flask_app.py`, which is
what PythonAnywhere's quick-start template assumes), and WSGI servers
specifically look for a variable named `application`, hence the `as`.

**This project also includes a `Dockerfile` and `fly.toml`** for
deploying to Fly.io instead, if you'd rather not use PythonAnywhere -
those don't need any of the WSGI setup above, since Fly.io runs the
`gunicorn` command in the `Dockerfile` directly.

## Deploying (general)

This app is ready to run behind a production WSGI server such as gunicorn:

```bash
pip install gunicorn
gunicorn app:app
```

Make sure the `static/uploads/` folder is writable by whichever user runs the
app, and that `SECRET_KEY` / `ADMIN_PASSWORD` are set as environment
variables rather than left at their defaults.

## Project structure

```
diani-sea-adventures/
├── app.py                  # Flask routes, locale detection, admin auth, uploads
├── db.py                   # DB schema/queries - SQLite by default, MySQL if DB_HOST is set
├── data.py                 # Seed content, testimonials, FAQ, contact info (all per-language)
├── i18n.py                 # Supported languages, static UI string translations
├── requirements.txt
├── Dockerfile              # For Fly.io (or any other Docker-based host)
├── fly.toml                # Fly.io app configuration
├── .vercelignore           # Excludes local-storage-only paths from Vercel deploys
├── data/
│   └── diani.db             # Created automatically on first run (SQLite backend only)
├── static/
│   ├── css/style.css
│   ├── js/main.js
│   ├── covers/<slug>.jpg   # Cover photo per safari (add your own; optional)
│   └── uploads/<slug>/     # Gallery photo files (local storage backend only)
└── templates/
    ├── base.html
    ├── index.html
    ├── safari_detail.html
    ├── contact.html
    ├── admin_login.html
    ├── admin_dashboard.html
    ├── admin_safaris_list.html
    ├── admin_safari_form.html
    └── 404.html
```
