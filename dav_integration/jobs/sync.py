# -*- coding: utf-8 -*-
from __future__ import annotations

import frappe
from frappe.utils import now

from dav_integration.dav.auth import build_basic_auth_headers
from dav_integration.dav.session import DavSession
from dav_integration.dav.webdav import WebDavClient

def enqueue_all():
    """Cron entry: enqueue sync jobs for all enabled accounts."""
    try:
        settings = frappe.get_single("DAV Settings")
        if not int(getattr(settings, "sync_enabled", 0)):
            return
    except Exception:
        # App not fully installed yet
        return

    accounts = frappe.get_all("DAV Account", filters={"enabled": 1}, pluck="name")
    for acc in accounts:
        frappe.enqueue(
            "dav_integration.jobs.sync.sync_account",
            queue="long",
            account=acc,
            job_name=f"dav_sync::{acc}",
            is_async=True,
        )

def sync_account(account: str):
    """Sync an account (currently: WebDAV list root; CalDAV/CardDAV stubs)."""
    doc = frappe.get_doc("DAV Account", account)

    headers = build_basic_auth_headers(doc.username, doc.get_password("app_password"))

    session = DavSession(
        base_url=doc.base_url,
        headers=headers,
        timeout_s=int(doc.request_timeout_s or 30),
        verify_tls=bool(doc.verify_tls) if doc.verify_tls is not None else True,
    )

    # WebDAV root: if not provided, try Nextcloud default.
    webdav_root = doc.webdav_root_url or f"/remote.php/dav/files/{doc.username}/"

    client = WebDavClient(session)

    try:
        items = client.propfind(webdav_root, depth=1)
        _log(
            account=account,
            resource_type="webdav",
            action="propfind",
            status="ok",
            message=f"Listed {len(items)} items at {webdav_root}",
        )
    except Exception as e:
        _log(
            account=account,
            resource_type="webdav",
            action="propfind",
            status="error",
            message=str(e),
        )
        raise

def _log(account: str, resource_type: str, action: str, status: str, message: str):
    """Best-effort logging into DAV Log DocType."""
    try:
        frappe.get_doc(
            {
                "doctype": "DAV Log",
                "timestamp": now(),
                "account": account,
                "resource_type": resource_type,
                "action": action,
                "status": status,
                "message": (message or "")[:1400],
            }
        ).insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        pass
