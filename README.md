# dav_integration (ERPNext / Frappe v16)

Starter skeleton for a generic DAV integration app:
- WebDAV (files) — **implemented** (PROPFIND list + GET/PUT)
- CalDAV (calendar) — stubs
- CardDAV (contacts) — stubs

## Quick start (bench)

1) Unzip into your bench `apps/` folder:
   `apps/dav_integration/`

2) Install python deps:
   `./env/bin/pip install -r apps/dav_integration/requirements.txt`

3) Install the app:
   `bench --site <yoursite> install-app dav_integration`

4) Run migrations:
   `bench --site <yoursite> migrate`

## Configure

- **DAV Settings** (singleton)
- **DAV Account** (create one record)

For Nextcloud you can usually set:
- Base URL: `https://cloud.example.com`
- Username: `<your-user>`
- App Password: `<nextcloud app password>`
- (optional) WebDAV Root Path: `/remote.php/dav/files/<your-user>/`

## Run a one-off sync (test)

`bench --site <yoursite> execute dav_integration.jobs.sync.sync_account --kwargs "{'account':'<DAV Account name>'}"`

This will do a WebDAV PROPFIND listing on the configured root and write a **DAV Log** entry.

## What’s inside

- `dav/session.py` : requests wrapper
- `dav/webdav.py`  : PROPFIND + GET + PUT
- `jobs/sync.py`   : scheduler entry + account sync job
- DocTypes: DAV Settings / DAV Account / DAV Sync State / DAV Log
