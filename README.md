# Diani Sea Adventures — Vercel + Supabase

This version is designed to deploy the Flask application on **Vercel** with **Supabase PostgreSQL + Supabase Storage**.

## Architecture

- **Vercel**: Flask web application / serverless runtime
- **Supabase PostgreSQL**: safaris, inquiries, gallery metadata
- **Supabase Storage**: uploaded gallery photos and admin cover photos
- **Static assets**: bundled CSS, JS, and the original fallback cover photos

No SQLite database is required in production, and the application does not rely on Vercel's ephemeral filesystem for uploaded media.

## 1. Create the Supabase project

Create a project in Supabase, then open **SQL Editor** and run `supabase_schema.sql`.

The SQL creates the application tables and a public `media` Storage bucket. The Flask backend uses the Supabase server-side secret key for database and Storage writes.

## 2. Environment variables in Vercel

Add these under **Project → Settings → Environment Variables**:

```text
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_SECRET_KEY=YOUR_SUPABASE_SECRET_KEY
SECRET_KEY=YOUR_LONG_RANDOM_FLASK_SECRET
ADMIN_PASSWORD=YOUR_ADMIN_PASSWORD
SUPABASE_STORAGE_BUCKET=media
```

For older Supabase projects, `SUPABASE_SERVICE_ROLE_KEY` is also accepted as a fallback, but `SUPABASE_SECRET_KEY` is preferred for new deployments.

Never expose the Supabase secret/service-role key to browser JavaScript or commit it to Git.

## 3. Deploy to Vercel

Import this repository/folder into Vercel. Vercel detects `vercel.json` and uses `api/index.py` as the Flask entrypoint.

No Gunicorn command is required on Vercel.

## 4. Existing SQLite data

If you have an older `diani.db`, run the included migration utility from an environment where the old database is available and the Supabase environment variables are configured:

```bash
python migrate_sqlite_to_supabase.py
```

The production deployment itself does not need the SQLite file.

## 5. Media uploads

The admin dashboard uploads images directly through the Flask backend into the Supabase `media` bucket:

```text
media/covers/<slug>.jpg
media/gallery/<slug>/<timestamp>_<filename>
```

The database stores gallery object paths. Public URLs are generated from the Supabase Storage endpoint.

Existing images in `static/covers/` are retained as bundled fallback images if a corresponding Storage cover has not been uploaded.

## Local development

Install dependencies:

```bash
pip install -r requirements.txt
```

Set the same environment variables and run:

```bash
flask --app app run
```

## Vercel notes

Vercel's runtime filesystem should not be treated as persistent storage. Do not modify the application to save uploaded files under `static/uploads` or `static/covers`; use Supabase Storage instead.


### Vercel startup note
The Flask module does not run database bootstrap or schema-creation code during import. Run `supabase_schema.sql` once in Supabase before deploying; Vercel can then import `api/index.py` normally.


## Direct PostgreSQL connection

This version uses a direct PostgreSQL connection for application data. Set
`DATABASE_URL` in Vercel to the Supabase PostgreSQL connection string. Keep the
password server-side and never commit it to Git.

The Supabase client remains only for Storage uploads. Run `supabase_schema.sql`
once in Supabase before deploying.
