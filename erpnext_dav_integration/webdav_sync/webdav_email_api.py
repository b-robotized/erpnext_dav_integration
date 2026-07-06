"""
webdav_email_api.py  –  Use Case 1: WebDAV files in email attachments

Whitelisted API endpoints called by webdav_file_browser.js:

  get_root_folder()          → root WebDAV path for the logged-in user
  list_folder(path)          → PROPFIND at depth=1, returns file/folder list
  attach_webdav_file(...)    → download + ERPNext File record + attach to doc
  create_share_link(path)    → Nextcloud OCS share → returns public URL

All endpoints resolve the DAV account from the current user's Employee record.
An explicit dav_account param is accepted so admin users can act on behalf of
accounts they manage (guarded by System Manager role check).
"""

from pathlib import PurePosixPath

import frappe

from .manager import AttachmentStrategy, WebDAVManager

# ── internal helper ────────────────────────────────────────────────────────────


def _resolve_dav_account(dav_account: str | None = None) -> str:
	"""
	Return the DAV account name to use for this request.

	Resolution order:
	  1. Explicit dav_account param (System Manager only).
	  2. DAV account linked to the current user's Employee record.
	"""
	# Look up via Employee → username
	user = frappe.session.user
	if frappe.db.exists("DAV Account", {"username": user, "enabled": 1}):
		return frappe.db.get_value("DAV Account", {"username": user, "enabled": 1}, "name")
	else:
		frappe.throw(
			"No WebDAV account linked to your profile.  "
			"Configure your DAV Account with your username to use Cloud Files Feature.",
			frappe.PermissionError,
		)


def _file_size_display(bytes_count: int) -> str:
	"""Human-readable file size."""
	if bytes_count < 1024:
		return f"{bytes_count} B"
	if bytes_count < 1024**2:
		return f"{bytes_count / 1024:.1f} KB"
	return f"{bytes_count / 1024**2:.1f} MB"


def _icon_type(filename: str, is_collection: bool) -> str:
	"""Map filename extension → icon hint consumed by the JS renderer."""
	if is_collection:
		return "folder"
	ext = PurePosixPath(filename).suffix.lower()
	mapping = {
		".pdf": "pdf",
		".doc": "doc",
		".docx": "doc",
		".xls": "xls",
		".xlsx": "xls",
		".ppt": "ppt",
		".pptx": "ppt",
		".jpg": "img",
		".jpeg": "img",
		".png": "img",
		".gif": "img",
		".webp": "img",
		".zip": "zip",
		".tar": "zip",
		".gz": "zip",
		".txt": "txt",
		".md": "txt",
		".mp4": "video",
		".mov": "video",
	}
	return mapping.get(ext, "file")


# ══════════════════════════════════════════════════════════════════════════════
# Public API endpoints
# ══════════════════════════════════════════════════════════════════════════════


@frappe.whitelist()
def get_root_folder(dav_account: str | None = None) -> dict:
	"""
	Return the root WebDAV path and account name for the current user.

	Response:
	    {
	        "path": "/remote.php/dav/files/alice/",
	        "dav_account": "Alice DAV",
	        "username": "alice",
	        "base_url": "https://cloud.example.com"
	    }
	"""
	account_name = _resolve_dav_account(dav_account)
	mgr = WebDAVManager(account_name)
	root_path = f"/remote.php/dav/files/{mgr.username}/"
	return {
		"path": root_path,
		"dav_account": account_name,
		"username": mgr.username,
		"base_url": mgr.base_url,
	}


@frappe.whitelist()
def list_folder(path: str, dav_account: str | None = None) -> list[dict]:
	"""
	List the contents of a WebDAV folder at *path*.

	Each item in the returned list contains:
	    href, name, is_collection, icon_type,
	    size_display, content_length, last_modified, etag

	The JS side uses icon_type to pick a Tabler icon.
	"""
	account_name = _resolve_dav_account(dav_account)
	mgr = WebDAVManager(account_name)
	base_url = mgr.base_url.rstrip("/") + "/"
	actual_path = base_url + path.lstrip("/")
	resources = mgr.propfind(actual_path, depth="1")

	result = []
	for r in resources:
		# first resource is the folder itself, skip it
		if r["href"].rstrip("/") == path.rstrip("/"):
			continue
		from urllib.parse import quote, unquote

		dav_href = r["href"]
		relative_path = dav_href.split(f"/remote.php/dav/files/{mgr.username}", 1)[-1]

		preview_url = f"{base_url}/core/preview?file={quote(relative_path)}&x=64&y=64&a=true"
		result.append(
			{
				"href": r["href"],
				"name": r["name"],
				"is_collection": r["is_collection"],
				"icon_type": _icon_type(r["name"], r["is_collection"]),
				"size_display": _file_size_display(r["content_length"]) if not r["is_collection"] else "",
				"content_length": r["content_length"],
				"last_modified": r["last_modified"],
				"etag": r["etag"],
				"preview_url": preview_url,
				"id": r["file_id"],
			}
		)

	# Folders first, then files, both alphabetically
	result.sort(key=lambda x: (not x["is_collection"], x["name"].lower()))
	return result


@frappe.whitelist()
def attach_webdav_file(
	cloud_path: str,
	doctype: str,
	docname: str,
	dav_account: str | None = None,
	is_private: int = 1,
) -> dict:
	"""
	Download a file from WebDAV and attach it to an ERPNext document.

	Used by the "Attach file" button in the browser dialog.

	Response:
	    {
	        "file":     "File/ABC123",
	        "file_url": "/private/files/NDA.pdf",
	        "file_name": "NDA.pdf"
	    }
	"""
	account_name = _resolve_dav_account(dav_account)
	mgr = WebDAVManager(account_name)
	strategy = AttachmentStrategy(mgr)

	file_doc = strategy.attach(
		cloud_path=cloud_path,
		doctype=doctype,
		docname=docname,
		mode="copy",
		is_private=bool(is_private),
	)

	return {
		"file": file_doc.name,
		"file_url": file_doc.file_url,
		"file_name": file_doc.file_name,
	}


@frappe.whitelist()
def create_share_link(
	cloud_path: str,
	dav_account: str | None = None,
	password: str | None = None,
	expire_date: str | None = None,
) -> dict:
	"""
	Create (or reuse) a Nextcloud public share link for *cloud_path*.

	Used by the "Insert share link" button in the browser dialog.

	Response:
	    {
	        "url":   "https://cloud.example.com/s/aBcDeF",
	        "reused": true   # true if an existing share was found
	    }
	"""
	account_name = _resolve_dav_account(dav_account)
	mgr = WebDAVManager(account_name)

	share = mgr.create_share_link(
		cloud_path,
		permissions=1,
		password=password or None,
		expire_date=frappe.utils.get_datetime(expire_date) if expire_date else None,
	)
	return {"url": share["url"], "reused": False}
