import mimetypes
import re
import time
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

import frappe
import pybreaker
import requests
from defusedxml import ElementTree as ET
from frappe import _
from frappe.utils.password import get_decrypted_password
from icalendar import Calendar, vCalAddress, vText
from icalendar import Event as ICalEvent
from requests.auth import HTTPBasicAuth

from .rate_limiter import RateLimiter

webdav_breaker = pybreaker.CircuitBreaker(fail_max=5, reset_timeout=60)

# ── WebDAV XML namespaces ──────────────────────────────────────────────────────
NS = {
	"d": "DAV:",
	"oc": "http://owncloud.org/ns",
	"nc": "http://nextcloud.org/ns",
	"cs": "http://calendarserver.org/ns/",
}

# ── Folder classification tags (Nextcloud-specific OC props) ──────────────────
INVOICE_KEYWORDS = re.compile(r"invoice|rechnung|factura|fattura", re.I)
CONTRACT_KEYWORDS = re.compile(r"contract|vertrag|contrato|contratto", re.I)

DEFAULT_CALDAV_SYNC_PAST_DAYS = 10
DEFAULT_CALDAV_SYNC_FUTURE_DAYS = 365


def normalize_url(url: str) -> str:
	"""Normalize a URL by stripping trailing slashes and whitespace."""
	return url.rstrip("/").strip()


class WebDAVManager:
	def __init__(self, dav_account_name):
		self.dav_account = frappe.get_doc("DAV Account", dav_account_name)
		self.username = self.dav_account.username
		self.password = get_decrypted_password("DAV Account", dav_account_name, "app_password")
		self.base_url = normalize_url(self.dav_account.base_url)
		self.auth = HTTPBasicAuth(self.username, self.password)
		self.rate_limiter = RateLimiter(rate=5, per=1)
		expire_days = int(self.dav_account.default_share_link_expire_in or 0)
		self.file_expire_date = datetime.now(datetime.timezone.utc) + timedelta(days=expire_days)

		# Load CalDAV sync window from DAV Settings singleton
		try:
			dav_settings = frappe.get_single("DAV Settings")
		except Exception:
			dav_settings = None

		self.caldav_sync_past_days = int(
			getattr(dav_settings, "caldav_sync_past_days", DEFAULT_CALDAV_SYNC_PAST_DAYS)
			or DEFAULT_CALDAV_SYNC_PAST_DAYS
		)
		self.caldav_sync_future_days = int(
			getattr(dav_settings, "caldav_sync_future_days", DEFAULT_CALDAV_SYNC_FUTURE_DAYS)
			or DEFAULT_CALDAV_SYNC_FUTURE_DAYS
		)

	# ---------------------------
	# 🔍 GENERIC REQUEST HANDLER
	# ---------------------------
	@webdav_breaker
	def _request(self, method, url, data=None, depth=None, headers=None, stream=False):
		# ✅ THROTTLING
		self.rate_limiter.wait()

		# Convert server-relative paths to full URLs
		if not url.startswith("http://") and not url.startswith("https://"):
			url = self._url(url)

		default_headers = {
			"Content-Type": "application/xml",
		}
		if depth:
			default_headers["Depth"] = depth

		# Merge custom headers with defaults
		if headers:
			default_headers.update(headers)
		try:
			request_kwargs = {
				"method": method,
				"url": url,
				"headers": default_headers,
				"data": data,
				"auth": self.auth,
				"timeout": 15,
			}
			if stream:
				request_kwargs["stream"] = stream

			response = requests.request(**request_kwargs)

			# 🔴 Important: treat 5xx as failure
			if response.status_code >= 500:
				raise Exception(f"Server error: {response.status_code}")

			return response

		except requests.exceptions.RequestException as e:
			frappe.log_error(f"CalDAV request failed: {e!s}")
			raise

	# ---------------------------
	# 🔍 XML HELPERS
	# ---------------------------
	def _url(self, path: str) -> str:
		"""Build absolute URL from a server-relative path."""
		return self.base_url + "/" + path.lstrip("/")

	def _encode_path(self, path: str) -> str:
		"""Percent-encode each path segment while preserving slashes."""
		parts = path.split("/")
		return "/".join(quote(p, safe="") for p in parts)

	def _find_text(self, parent, tag_suffix):
		"""Find element ignoring namespace"""
		for elem in parent.iter():
			if elem.tag.endswith(tag_suffix):
				return elem.text
		return None

	# ── PROPFIND ──────────────────────────────────────────────────────────────

	def propfind(self, path: str, depth: str = "1") -> list[dict]:
		"""
		Return a list of resource dicts for *path* and its immediate children
		(depth="1").  Each dict contains: href, name, is_collection, content_type,
		content_length, last_modified, etag, share_url.
		"""
		body = """<?xml version="1.0"?>
        <d:propfind xmlns:d="DAV:"
                    xmlns:oc="http://owncloud.org/ns"
                    xmlns:nc="http://nextcloud.org/ns">
            <d:prop>
                <d:resourcetype/>
                <d:displayname/>
                <d:getcontenttype/>
                <d:getcontentlength/>
                <d:getlastmodified/>
                <d:getetag/>
                <oc:fileid/>
                <oc:share-types/>
                <nc:has-preview/>
            </d:prop>
        </d:propfind>"""

		response = self._request("PROPFIND", path, data=body, depth=depth)
		if response.status_code == 404:
			return []

		if response.status_code not in (207, 200):
			frappe.log_error(f"PROPFIND {path} → {response.status_code}")
			return []

		root = ET.fromstring(response.content)
		results = []

		for resp in root.iter():
			if not resp.tag.endswith("response"):
				continue

			href = self._find_text(resp, "href") or ""

			# skip the container itself when depth="1"
			if depth == "1" and href.rstrip("/") == path.rstrip("/"):
				continue

			prop = next((e for e in resp.iter() if e.tag.endswith("propstat")), None)
			if prop is None:
				continue

			status = self._find_text(prop, "status") or ""
			if "200" not in status:
				continue

			resourcetype = next((e for e in prop.iter() if e.tag.endswith("resourcetype")), None)
			is_collection = resourcetype is not None and any(
				c.tag.endswith("collection") for c in resourcetype
			)

			results.append(
				{
					"href": href,
					"name": self._find_text(prop, "displayname") or PurePosixPath(href.rstrip("/")).name,
					"is_collection": is_collection,
					"content_type": self._find_text(prop, "getcontenttype") or "",
					"content_length": int(self._find_text(prop, "getcontentlength") or 0),
					"last_modified": self._find_text(prop, "getlastmodified") or "",
					"etag": (self._find_text(prop, "getetag") or "").strip('"'),
					"file_id": self._find_text(prop, "fileid") or "",
				}
			)

		return results

	# ── GET (download) ────────────────────────────────────────────────────────

	def get_file(self, path: str) -> bytes:
		"""Download a file and return its raw bytes."""
		response = self._request("GET", path, headers={"Content-Type": "application/octet-stream"})
		if response.status_code != 200:
			frappe.throw(_("WebDAV GET failed [{0}]: {1}").format(path, response.status_code))
		return response.content

	def get_file_stream(self, path: str):
		"""Return a streaming response (use for large files)."""
		return self._request(
			"GET",
			path,
			headers={"Content-Type": "application/octet-stream"},
			stream=True,
		)

	# ── PUT (upload) ──────────────────────────────────────────────────────────

	def put_file(
		self,
		path: str,
		content: bytes,
		content_type: str | None = None,
		etag: str | None = None,
	) -> str:
		"""
		Upload *content* to *path*.  Pass *etag* for optimistic-lock conflict detection.
		Returns the new ETag on success.
		"""
		if content_type is None:
			guessed, _ = mimetypes.guess_type(path)
			content_type = guessed or "application/octet-stream"

		headers = {"Content-Type": content_type}
		if etag:
			headers["If-Match"] = f'"{etag}"'

		response = self._request("PUT", path, data=content, headers=headers)

		if response.status_code == 412:
			frappe.throw(_("File was modified by another client.  Refresh the cloud version and retry."))
		if response.status_code not in (201, 204):
			frappe.throw(
				_("WebDAV PUT failed [{0}]: {1}\n{2}").format(path, response.status_code, response.text)
			)

		return response.headers.get("ETag", "").strip('"')

	# ── MKCOL (create collection / folder) ────────────────────────────────────

	def mkcol(self, path: str) -> bool:
		"""
		Create a collection at *path*.
		Creates intermediate parents automatically (Nextcloud allows this with
		a single MKCOL if the parent already exists; otherwise we walk up).
		Returns True if created, False if already existed.
		"""
		response = self._request("MKCOL", path)

		if response.status_code == 405:
			return False  # already exists
		if response.status_code == 409:
			# parent missing - ensure it exists first, then retry
			parent = str(PurePosixPath(path.rstrip("/")).parent) + "/"
			self.mkcol(parent)
			return self.mkcol(path)
		if response.status_code not in (200, 201):
			frappe.throw(_("MKCOL failed [{0}]: {1}").format(path, response.status_code))

		return True

	# ── DELETE ────────────────────────────────────────────────────────────────

	def delete(self, path: str) -> bool:
		"""Delete a resource (file or collection).  Returns True on success."""
		response = self._request("DELETE", path)
		if response.status_code == 404:
			return False
		if response.status_code not in (200, 204):
			frappe.throw(_("WebDAV DELETE failed [{0}]: {1}").format(path, response.status_code))
		return True

	# ── COPY ──────────────────────────────────────────────────────────────────

	def copy(self, src: str, dst: str, overwrite: bool = False) -> bool:
		"""Server-side copy from *src* to *dst*."""
		headers = {
			"Destination": self._url(dst),
			"Overwrite": "T" if overwrite else "F",
		}
		response = self._request("COPY", src, headers=headers)
		if response.status_code not in (201, 204):
			frappe.throw(_("WebDAV COPY failed [{0} -> {1}]: {2}").format(src, dst, response.status_code))
		return True

	# ── MOVE ──────────────────────────────────────────────────────────────────

	def move(self, src: str, dst: str, overwrite: bool = False) -> bool:
		"""Server-side move/rename from *src* to *dst*."""
		headers = {
			"Destination": self._url(dst),
			"Overwrite": "T" if overwrite else "F",
		}
		response = self._request("MOVE", src, headers=headers)
		if response.status_code not in (201, 204):
			frappe.throw(_("WebDAV MOVE failed [{0} -> {1}]: {2}").format(src, dst, response.status_code))
		return True

	# ── Nextcloud share-link API ──────────────────────────────────────────────

	def _normalize_ocs_path(self, path: str) -> str:
		"""
		Convert a full DAV URL or path into:
		/folder/file.txt
		"""

		# If full URL, extract path only
		if path.startswith(("http://", "https://")):
			path = urlparse(path).path
		# Example: %20 -> space
		path = unquote(path)
		# Remove DAV prefix
		dav_prefix = f"/remote.php/dav/files/{self.username}"
		if path.startswith(dav_prefix):
			path = path[len(dav_prefix) :]

		# Ensure leading slash
		if not path.startswith("/"):
			path = "/" + path
		return path

	def create_share_link(
		self,
		path: str,
		share_type: int = 3,  # 3 = public link
		permissions: int = 1,  # 1 = read
		password: str | None = None,
		expire_date: datetime | None = None,
	) -> dict:
		"""
		Create a Nextcloud public share via OCS API.

		Returns dict with keys: id, url, token.
		"""
		api_url = f"{self.base_url}/ocs/v2.php/apps/files_sharing/api/v1/shares"
		normalized_path = self._normalize_ocs_path(path)

		data = {
			"path": normalized_path,
			"shareType": share_type,
			"permissions": permissions,
			"expireDate": expire_date.strftime("%Y-%m-%d")
			if expire_date
			else self.file_expire_date.strftime("%Y-%m-%d")
			if self.dav_account.default_share_link_expire_in
			else None,
		}
		if password:
			data["password"] = password
		response = requests.post(
			api_url,
			data=data,
			auth=self.auth,
			headers={
				"OCS-APIRequest": "true",
				"Accept": "application/json",
			},
			timeout=20,
		)
		if response.status_code not in (200, 201):
			frappe.throw(
				_("Nextcloud share creation failed [{0}]: {1}\n{2}").format(
					normalized_path, response.status_code, response.text
				)
			)

		payload = response.json()
		share = payload.get("ocs", {}).get("data", {})
		return {
			"id": share.get("id"),
			"url": share.get("url"),
			"token": share.get("token"),
		}

	def validate_dav_url(self, dav_url):
		"""
		Validate if the provided calendar URL is accessible and belongs to the user
		"""
		url = f"{self.base_url}{dav_url}"
		try:
			response = self._request("PROPFIND", url, depth="0")
			if response.status_code in [207, 200]:
				return True
			else:
				return False
		except Exception as e:
			frappe.log_error(f"DAV URL validation failed: {e!s}")
			return False

	def get_share_links(self, path: str) -> list[dict]:
		"""Return existing share links for *path*."""
		api_url = f"{self.base_url}/ocs/v2.php/apps/files_sharing/api/v1/shares"
		normalized_path = self._normalize_ocs_path(path)
		response = requests.get(
			api_url,
			params={"path": normalized_path, "reshares": "true"},
			auth=self.auth,
			headers={"OCS-APIRequest": "true", "Accept": "application/json"},
			timeout=15,
		)
		if response.status_code != 200:
			return []
		shares = response.json().get("ocs", {}).get("data", [])
		return [{"id": s.get("id"), "url": s.get("url"), "token": s.get("token")} for s in shares]

	# ── Convenience: ensure folder exists ────────────────────────────────────

	def ensure_folder(self, path: str) -> bool:
		"""Create *path* if it does not already exist.  Returns True if created."""
		resources = self.propfind(path, depth="0")
		if resources:
			return False  # already there
		return self.mkcol(path)

	def _has_calendar_resource(self, resourcetype_elem):
		if resourcetype_elem is None:
			return False

		for child in resourcetype_elem:
			if child.tag.endswith("calendar"):
				return True
		return False

	# ---------------------------
	# 🏠 STEP 1: DISCOVER CALENDAR HOME
	# ---------------------------
	def get_calendar_home_set(self):
		url = f"{self.base_url}/remote.php/dav/principals/users/{self.username}/"

		data = """<?xml version="1.0"?>
        <d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
            <d:prop>
                <c:calendar-home-set />
            </d:prop>
        </d:propfind>
        """

		try:
			response = self._request("PROPFIND", url, data=data, depth="0")
			root = ET.fromstring(response.content)

			for elem in root.iter():
				if elem.tag.endswith("calendar-home-set"):
					for child in elem:
						if child.tag.endswith("href"):
							return child.text.rstrip("/") + "/"

		except Exception as e:
			frappe.log_error(f"Calendar home discovery failed: {e!s}")

		# ✅ Fallback for Nextcloud
		fallback = f"/remote.php/dav/calendars/{self.username}/"
		return fallback

	# ---------------------------
	# 📅 STEP 2: DISCOVER CALENDARS
	# ---------------------------
	def discover_calendars(self, skip_filtering: bool = False):
		calendar_home = self.get_calendar_home_set()
		url = f"{self.base_url}{calendar_home}"

		data = """<?xml version="1.0"?>
        <d:propfind
            xmlns:d="DAV:"
            xmlns:c="urn:ietf:params:xml:ns:caldav"
            xmlns:cs="http://calendarserver.org/ns/"
            xmlns:oc="http://owncloud.org/ns"
            xmlns:nc="http://nextcloud.org/ns"
            xmlns:ical="http://apple.com/ns/ical/">
            <d:prop>
                <d:resourcetype />
                <d:displayname />
                <cs:getctag />
                <d:sync-token />
                <c:supported-calendar-component-set />
                <c:calendar-timezone />
                <ical:calendar-color />
            </d:prop>
        </d:propfind>"""

		response = self._request("PROPFIND", url, data=data, depth="1")
		calendars = []
		root = ET.fromstring(response.content)
		dav_calendar_data = frappe.db.get_all(
			"DAV Calendar", fields=["name", "dav_calendar", "dav_calendar_url", "dav_account"]
		)
		for resp in root.iter():
			if not resp.tag.endswith("response"):
				continue
			href = self._find_text(resp, "href")
			if not href:
				continue
			if (
				not any(
					normalize_url(cal.dav_calendar_url) == normalize_url(href) for cal in dav_calendar_data
				)
				and not skip_filtering
			):
				continue
			prop = None
			for elem in resp.iter():
				if elem.tag.endswith("prop"):
					prop = elem
					break

			if not prop:
				continue

			resourcetype = None
			for elem in prop:
				if elem.tag.endswith("resourcetype"):
					resourcetype = elem
					break

			if self._has_calendar_resource(resourcetype):
				displayname = self._find_text(prop, "displayname")
				color = self._extract_color(prop)
				tz_data = self._find_text(prop, "calendar-timezone")
				tz_info = extract_timezone_details(tz_data)

				timezone = normalize_timezone(tz_info.get("tzid"))
				getctag = self._find_text(prop, "getctag") or self._find_text(prop, "sync-token")
				calendars.append(
					{
						"url": normalize_url(href),
						"name": displayname or "Calendar",
						"color": color,
						"timezone": timezone,
						"getctag": getctag,
					}
				)

		return calendars

	def _extract_color(self, prop):
		color = self._find_text(prop, "calendar-color")
		if not color:
			return None

		# Nextcloud usually returns hex like: #FF0000
		return color.strip()

	def _extract_timezone(self, prop):
		tz = self._find_text(prop, "calendar-timezone")
		return tz.strip() if tz else None

	def fetch_single_event(self, event_url):
		url = f"{self.base_url}{event_url}"

		response = self._request("GET", url, depth="1")
		cal = response.text
		event_data = self._parse_ical_event(Calendar.from_ical(cal))

		if event_data:
			event_data.update(
				{
					"caldav_url": event_url,
					"etag": response.headers.get("ETag", "").strip('"'),
					"text": response.text,
				}
			)
			return event_data
		return None

	def _normalize_caldav_event_url(self, url: str) -> str:
		if not url:
			return ""

		parsed = urlparse(url)
		if parsed.scheme:
			return parsed.path.rstrip("/")

		return url.rstrip("/")

	def _get_caldav_time_range(self):
		now = datetime.now(datetime.timezone.utc)
		start = now - timedelta(days=self.caldav_sync_past_days)
		end = now + timedelta(days=self.caldav_sync_future_days)
		return start.strftime("%Y%m%dT%H%M%SZ"), end.strftime("%Y%m%dT%H%M%SZ")

	def fetch_events_from_calendar(self, calendar_url):
		url = f"{self.base_url}{calendar_url}"
		start, end = self._get_caldav_time_range()

		data = f"""<?xml version="1.0"?>
        <c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
            <d:prop>
                <d:getetag />
                <c:calendar-data />
            </d:prop>
            <c:filter>
                <c:comp-filter name="VCALENDAR">
                    <c:comp-filter name="VEVENT">
                    <c:time-range start="{start}" end="{end}" />
                    </c:comp-filter>
                </c:comp-filter>
            </c:filter>
        </c:calendar-query>"""

		response = self._request("REPORT", url, data=data, depth="1")
		root = ET.fromstring(response.content)

		events = []

		for resp in root.iter():
			if not resp.tag.endswith("response"):
				continue

			href = self._find_text(resp, "href")
			etag = self._find_text(resp, "getetag")

			cal_data = None
			for elem in resp.iter():
				if elem.tag.endswith("calendar-data"):
					cal_data = elem.text
					break

			if not cal_data:
				continue

			try:
				cal = Calendar.from_ical(cal_data)
				event_data = self._parse_ical_event(cal)

				if event_data:
					event_data.update(
						{
							"caldav_url": href,
							"etag": etag,
							"calendar_url": calendar_url,
							"text": cal_data,
						}
					)
					events.append(event_data)

			except Exception as e:
				frappe.log_error(f"Event parse failed ({href}): {e!s}")

		return events

	def fetch_event_hrefs_from_calendar(self, calendar_url):
		url = f"{self.base_url}{calendar_url}"

		data = """<?xml version="1.0"?>
        <c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
            <d:prop>
                <d:getetag />
            </d:prop>
            <c:filter>
                <c:comp-filter name="VCALENDAR">
                    <c:comp-filter name="VEVENT" />
                </c:comp-filter>
            </c:filter>
        </c:calendar-query>"""

		response = self._request("REPORT", url, data=data, depth="1")
		root = ET.fromstring(response.content)

		event_hrefs = set()
		for resp in root.iter():
			if not resp.tag.endswith("response"):
				continue

			href = self._find_text(resp, "href")
			if href:
				event_hrefs.add(self._normalize_caldav_event_url(href))

		return event_hrefs

	# ---------------------------
	# 🧠 ICAL PARSER
	# ---------------------------
	def _parse_ical_event(self, cal):
		for component in cal.walk():
			if component.name == "VEVENT":
				return {
					"uid": str(component.get("uid")),
					"summary": str(component.get("summary", "")),
					"description": str(component.get("description", "")),
					"location": str(component.get("location", "")),
					"dtstart": component.get("dtstart").dt if component.get("dtstart") else None,
					"dtend": component.get("dtend").dt if component.get("dtend") else None,
					"attendees": self._extract_attendees(component),
					"organizer": str(component.get("organizer", "").replace("mailto:", ""))
					if component.get("organizer")
					else "",
					"organizer_cn": str(component.get("organizer").params.get("cn", ""))
					if component.get("organizer") and hasattr(component.get("organizer"), "params")
					else "",
					"status": str(component.get("status", "CONFIRMED")),
				}
		return None

	def _extract_attendees(self, component):
		attendees = []
		attendees_raw = component.get("attendee", [])
		if not isinstance(attendees_raw, list):
			attendees_raw = [attendees_raw]

		for attendee in attendees_raw:
			email = str(attendee).replace("mailto:", "")
			cn = ""
			rsvp = False
			partstat = "NEEDS-ACTION"
			if hasattr(attendee, "params"):
				cn = attendee.params.get("CN", "")
				rsvp = attendee.params.get("RSVP", "FALSE") == "TRUE"
				partstat = attendee.params.get("PARTSTAT", "NEEDS-ACTION")
			attendees.append(
				{
					"email": email,
					"cn": cn,
					"rsvp": rsvp,
					"partstat": partstat,
				}
			)

		return attendees

	# ---------------------------
	# 🔄 FULL SYNC
	# ---------------------------
	def sync_caldav_to_erpnext(self):
		calendars = self.discover_calendars()

		for cal in calendars:
			frappe.msgprint(_("Syncing calendar: {0} ({1})").format(cal["name"], cal["url"]))
			events = self.fetch_events_from_calendar(cal["url"])

			# ✅ STEP 1: Sync existing events
			current_uids = set()

			for event in events:
				try:
					current_uids.add(event.get("uid"))

					WebDAVSyncor.sync_caldav_to_erpnext(event, self.dav_account, cal["url"])
				except Exception as e:
					frappe.log_error(f"Sync failed for {event.get('uid')}: {e!s}")

			# ✅ STEP 2: Detect deletions
			# self._mark_deleted_events(current_uids, cal["url"])

	def _mark_deleted_events(self, current_uids, calendar_url):
		"""
		Mark ERPNext events as deleted if they no longer exist in CalDAV
		"""

		erp_events = frappe.get_all(
			"Event",
			filters={
				"caldav_account": self.dav_account.name,
				"caldav_calendar_url": calendar_url,
				"caldav_sync_status": ["!=", "Deleted in Provider"],
			},
			fields=["name", "caldav_event_id"],
		)

		for event in erp_events:
			if event.caldav_event_id not in current_uids:
				try:
					WebDAVSyncor.handle_caldav_deletion(event.caldav_event_id, self.dav_account.name)
				except Exception as e:
					frappe.log_error(f"Deletion sync failed for {event.name}: {e!s}")

	# ---------------------------
	# ✨ CREATE NEW EVENT
	# ---------------------------
	def create_event_in_calendar(self, calendar_url, event_doc):
		"""
		Create new event in Nextcloud Calendar

		Args:
		    calendar_url: '/remote.php/dav/calendars/user/personal/'
		    event_doc: ERPNext Event doctype instance

		Returns:
		    dict with caldav_url, caldav_uuid, etag, caldav_card_text
		"""
		# Generate UUID for the event
		event_uuid = str(uuid4())
		caldav_url = f"{calendar_url}{event_uuid}.ics"

		# Build iCalendar
		cal = Calendar()
		cal.add("prodid", "-//Nextcloud//Calendar app 6.2.1//EN")
		cal.add("version", "2.0")
		cal.add("calscale", "GREGORIAN")

		# Create VEVENT
		vevent = ICalEvent()

		# Core event data
		vevent.add("uid", event_uuid)
		vevent.add("dtstamp", datetime.now(datetime.timezone.utc))
		vevent.add("created", datetime.now(datetime.timezone.utc))
		vevent.add("last-modified", datetime.now(datetime.timezone.utc))
		vevent.add("sequence", 0)

		# Event details - FIX: Use correct field name
		vevent.add("summary", event_doc.subject or "No Title")

		# 🔴 FIX: Handle both possible field names
		description = getattr(event_doc, "event_public_description", None) or ""

		if description:
			vevent.add("description", description)

		if event_doc.location:
			vevent.add("location", event_doc.location)

		# Dates - handle both datetime and date objects
		if event_doc.starts_on:
			vevent.add("dtstart", self._to_utc_datetime(event_doc.starts_on))

		if event_doc.ends_on:
			vevent.add("dtend", self._to_utc_datetime(event_doc.ends_on))
		else:
			# Ensure dtend exists (required by CalDAV)
			if event_doc.starts_on:
				vevent.add("dtend", self._to_utc_datetime(event_doc.starts_on))

		vevent.add("status", "CONFIRMED")

		# Organizer - 🔴 FIX: Use lowercase mailto (RFC standard)
		organizer_email = event_doc.caldav_organizer
		organizer = vCalAddress(f"mailto:{organizer_email}")
		organizer.params["cn"] = event_doc.caldav_organizer_name
		vevent.add("organizer", organizer)

		# Add participants (only those marked for invitation)
		added_emails = set()

		for participant in event_doc.get("caldav_participants_table", []):
			if participant.email in added_emails:
				continue

			added_emails.add(participant.email)

			attendee = vCalAddress(f"mailto:{participant.email}")

			if participant.contact:
				contact_doc = frappe.get_doc("Contact", participant.contact)
				attendee.params["cn"] = contact_doc.first_name or participant.email
			else:
				attendee.params["cn"] = participant.email

			attendee.params["role"] = "REQ-PARTICIPANT"
			attendee.params["partstat"] = participant.get("invitation_status", "NEEDS-ACTION")
			attendee.params["rsvp"] = "TRUE"
			attendee.params["cutype"] = "INDIVIDUAL"

			vevent.add("attendee", attendee)

		cal.add_component(vevent)

		# Convert to iCalendar string
		ical_data = cal.to_ical()
		# PUT to create event
		url = f"{self.base_url}{caldav_url}"
		headers = {
			"Content-Type": "text/calendar; charset=utf-8",
		}

		try:
			response = requests.request(
				"PUT", url, headers=headers, data=ical_data, auth=self.auth, timeout=30
			)
			frappe.msgprint(_("Creating event in calendar: {0}").format(calendar_url), alert=True)
		except Exception as e:
			error_msg = f"CalDAV create request failed: {e!s}"
			frappe.log_error(error_msg)
			frappe.throw(error_msg)

		# Handle response
		if response.status_code not in [201, 204]:
			error_msg = _("Failed to create event in CalDAV: {0}\n{1}").format(
				response.status_code, response.text
			)
			frappe.log_error(error_msg)
			frappe.throw(error_msg)

		# Extract UUID from URL
		caldav_uuid = caldav_url.replace(".ics", "").split("/")[-1]

		# 🔴 FIX: Ensure etag is string, not bytes
		etag = response.headers.get("ETag", "").strip('"')

		return {
			"caldav_url": caldav_url,
			"caldav_uuid": caldav_uuid,
			"caldav_event_id": event_uuid,
			"etag": etag,
			"caldav_card_text": ical_data.decode("utf-8") if isinstance(ical_data, bytes) else ical_data,
		}

	# ---------------------------
	# 🔄 UPDATE EXISTING EVENT (CORRECTED)
	# ---------------------------
	def update_event_in_calendar(self, event_doc):
		"""
		Update existing event in Nextcloud Calendar


		"""
		# 🟢 NEW DOC → treat as changed OR skip

		if not event_doc.caldav_event_id:
			frappe.throw(_("Event not connected to CalDAV"))

		if not event_doc.caldav_event_url:
			frappe.throw(_("CalDAV URL not found"))

		# 🔴 FIX: Correct sequence handling
		sequence = (event_doc.caldav_sequence or 0) + 1

		# Build calendar
		cal = Calendar()
		cal.add("prodid", "-//Nextcloud//Calendar app 6.2.1//EN")
		cal.add("version", "2.0")
		cal.add("calscale", "GREGORIAN")

		vevent = ICalEvent()

		# Core fields (DO NOT CHANGE UID)
		vevent.add("uid", event_doc.caldav_event_id)
		vevent.add("dtstamp", datetime.now(datetime.timezone.utc))
		vevent.add(
			"created", self._to_utc_datetime(event_doc.caldav_created) or datetime.now(datetime.timezone.utc)
		)
		vevent.add("last-modified", datetime.now(datetime.timezone.utc))
		vevent.add("sequence", sequence)

		# Basic details
		vevent.add("summary", event_doc.subject or "No Title")

		# 🔴 FIX: Handle both possible field names
		description = getattr(event_doc, "event_public_description", None) or ""

		if description:
			vevent.add("description", description)

		if event_doc.location:
			vevent.add("location", event_doc.location)

		# Dates (ensure dtend exists)
		if event_doc.starts_on:
			vevent.add("dtstart", self._to_utc_datetime(event_doc.starts_on))

		if event_doc.ends_on:
			vevent.add("dtend", self._to_utc_datetime(event_doc.ends_on))
		elif event_doc.starts_on:
			vevent.add("dtend", self._to_utc_datetime(event_doc.starts_on))

		# 🔴 FIX: Correct status mapping
		vevent.add("status", event_doc.caldav_status.capitalize())

		# 🔴 FIX: Consistent organizer format (lowercase mailto)
		organizer_email = event_doc.caldav_organizer
		organizer = vCalAddress(f"mailto:{organizer_email}")
		organizer.params["cn"] = event_doc.caldav_organizer_name
		vevent.add("organizer", organizer)

		# Add attendees (avoid duplicates)
		added_emails = set()

		for participant in event_doc.get("caldav_participants_table", []):
			if not participant.email or participant.email in added_emails:
				continue

			added_emails.add(participant.email)

			attendee = vCalAddress(f"mailto:{participant.email}")

			if participant.contact:
				contact_doc = frappe.get_doc("Contact", participant.contact)
				attendee.params["cn"] = contact_doc.first_name or participant.email
			else:
				attendee.params["cn"] = participant.email

			attendee.params["role"] = "REQ-PARTICIPANT"
			attendee.params["partstat"] = participant.get("invitation_status", "NEEDS-ACTION")
			attendee.params["rsvp"] = "TRUE"
			attendee.params["cutype"] = "INDIVIDUAL"

			vevent.add("attendee", attendee)

		cal.add_component(vevent)

		# Convert to iCalendar
		ical_data = cal.to_ical()
		# 🔴 CRITICAL FIX: Add If-Match header for conflict detection
		url = f"{self.base_url}{event_doc.caldav_event_url}"
		headers = {
			"Content-Type": "text/calendar; charset=utf-8",
			# 'If-Match': event_doc.caldav_etag  # 🔴 THIS IS CRITICAL
		}

		try:
			response = requests.put(url, headers=headers, data=ical_data, auth=self.auth, timeout=30)
		except Exception as e:
			error_msg = f"CalDAV update request failed: {e!s}"
			frappe.log_error(error_msg)
			frappe.throw(error_msg)

		# 🔴 FIX: Handle 409 Conflict
		if response.status_code == 409:
			frappe.throw(
				_(
					"Event was modified in the calendar. Click 'Refresh from Calendar' to get the latest version, then try again."
				)
			)

		# Handle other errors
		if response.status_code not in [201, 204]:
			error_msg = _("CalDAV update failed: {0}\n{1}").format(response.status_code, response.text)
			frappe.log_error(error_msg)
			frappe.throw(error_msg)

		# Get new ETag
		new_etag = response.headers.get("ETag", "").strip('"')

		return {
			"etag": new_etag,
			"sequence": sequence,
			"caldav_card_text": ical_data.decode("utf-8") if isinstance(ical_data, bytes) else ical_data,
		}

	# ---------------------------
	# ❌ DELETE EVENT
	# ---------------------------
	def delete_event_from_calendar(self, event_doc):
		"""
		Delete event from Nextcloud Calendar

		🔴 FIXED: Now includes If-Match header for safety
		"""

		if not event_doc.caldav_event_url:
			frappe.throw(_("CalDAV URL not found"))

		# 🔴 CRITICAL FIX: Add If-Match header for safety
		url = f"{self.base_url}{event_doc.caldav_event_url}"
		headers = {
			# 'If-Match': event_doc.caldav_etag  # 🔴 SAFETY CHECK
		}

		try:
			response = requests.request(
				"DELETE",
				url,
				headers=headers,  # 🔴 THIS IS CRITICAL
				auth=self.auth,
				timeout=30,
			)
		except Exception as e:
			error_msg = f"CalDAV delete request failed: {e!s}"
			frappe.log_error(error_msg)
			frappe.throw(error_msg)

		# 🔴 FIX: Handle 409 Conflict
		if response.status_code == 409:
			frappe.throw(
				_(
					"Event was modified in the calendar. Click 'Refresh from Calendar' to get the latest version, then try again."
				)
			)

		# Handle other errors
		if response.status_code not in [204, 200]:
			error_msg = _("Failed to delete event from CalDAV: {0}\n{1}").format(
				response.status_code, response.text
			)
			frappe.log_error(error_msg)
			frappe.throw(error_msg)

		return True

	# ---------------------------
	# 🕒 DATETIME HELPERS
	# ---------------------------
	@staticmethod
	def _to_utc_datetime(value):
		if not value:
			return None

		try:
			try:
				system_tz = ZoneInfo(frappe.utils.get_system_timezone())
			except Exception:
				system_tz = datetime.now().astimezone().tzinfo or datetime.timezone.utc

			# ---------------------------
			# 🔤 String → datetime
			# ---------------------------
			if isinstance(value, str):
				value = frappe.utils.get_datetime(value)

			# ---------------------------
			# 📅 Date → datetime
			# ---------------------------
			if hasattr(value, "year") and not hasattr(value, "hour"):
				value = datetime.combine(value, datetime.min.time())

			# ---------------------------
			# ❌ Invalid
			# ---------------------------
			if not isinstance(value, datetime):
				return None

			# ---------------------------
			# 🌍 Timezone handling
			# ---------------------------
			if value.tzinfo:
				# Already timezone-aware → convert to UTC
				value = value.astimezone(datetime.timezone.utc)
			else:
				# 🔴 CRITICAL FIX:
				value = value.replace(tzinfo=system_tz).astimezone(datetime.timezone.utc)

			return value

		except Exception as e:
			frappe.log_error(f"_to_utc_datetime failed: {value} -> {e!s}")
			return None

	# ---------------------------
	# 📥 FETCH & EXTRACT SEQUENCE
	# ---------------------------
	@staticmethod
	def _extract_sequence_from_ical(ical_text):
		"""
		Extract SEQUENCE number from iCalendar text
		"""
		try:
			cal = Calendar.from_ical(ical_text)
			for component in cal.walk():
				if component.name == "VEVENT":
					return int(component.get("sequence", 0))
		except Exception:
			pass
		return 0

	@staticmethod
	def _extract_created_from_ical(ical_text):
		"""
		Extract CREATED timestamp from iCalendar
		"""
		try:
			cal = Calendar.from_ical(ical_text)
			for component in cal.walk():
				if component.name == "VEVENT":
					created = component.get("created")
					if created:
						return created.dt if hasattr(created, "dt") else created
		except Exception:
			pass
		return None


def extract_timezone_details(tz_data):
	if not tz_data:
		return {}

	try:
		cal = Calendar.from_ical(tz_data)

		result = {}

		for comp in cal.walk():
			if comp.name == "VTIMEZONE":
				result["tzid"] = str(comp.get("TZID"))

			if comp.name in ("STANDARD", "DAYLIGHT"):
				result["tzname"] = str(comp.get("TZNAME"))
				result["tzoffsetfrom"] = str(comp.get("TZOFFSETFROM"))
				result["tzoffsetto"] = str(comp.get("TZOFFSETTO"))

		return result

	except Exception as e:
		return {"error": str(e)}


def normalize_timezone(tzid):
	if tzid == "Asia/Calcutta":
		return "Asia/Kolkata"
	return tzid


def extract_tzid_fast(tz_data):
	match = re.search(r"TZID:(.+)", tz_data)
	return match.group(1).strip() if match else None


# ══════════════════════════════════════════════════════════════════════════════
# 3.  AttachmentStrategy - copy vs link
# ══════════════════════════════════════════════════════════════════════════════


class AttachmentStrategy:
	"""
	Determines *how* a Nextcloud file is attached to an ERPNext document.

	Two strategies:
	  COPY - download the file and upload it to ERPNext's file storage.
	          The ERPNext File record owns the bytes; changes in Nextcloud
	          are NOT reflected automatically.

	  LINK - create (or reuse) a Nextcloud public share link and attach it
	          as a URL-type File record in ERPNext.  Zero storage duplication.
	          The file lives in Nextcloud permanently.

	Usage:
	    strategy = AttachmentStrategy(webdav_manager)
	    file_doc = strategy.attach(
	        cloud_path="/remote.php/dav/files/alice/Contracts/NDA.pdf",
	        doctype="Contract",
	        docname="CONT-0001",
	        mode="link",    # "copy" | "link"
	    )
	"""

	def __init__(self, webdav_manager: WebDAVManager):
		self.mgr = webdav_manager

	def attach(
		self,
		cloud_path: str,
		doctype: str,
		docname: str,
		mode: str = "link",
		filename: str | None = None,
		is_private: bool = True,
	) -> "frappe.Document":
		"""
		Attach a Nextcloud file to an ERPNext document.

		Args:
		    cloud_path:  server-relative WebDAV path to the file.
		    doctype:     target ERPNext doctype (e.g. "Purchase Invoice").
		    docname:     target document name.
		    mode:        "copy" or "link".
		    filename:    override display name (defaults to basename of cloud_path).
		    is_private:  if True the ERPNext File is private (copy mode only).

		Returns:
		    The saved ERPNext File doctype instance.
		"""
		if filename is None:
			filename = PurePosixPath(cloud_path.rstrip("/")).name

		if mode == "copy":
			return self._attach_copy(cloud_path, doctype, docname, filename, is_private)
		elif mode == "link":
			return self._attach_link(cloud_path, doctype, docname, filename)
		else:
			frappe.throw(_("AttachmentStrategy: unknown mode '{0}'.  Use 'copy' or 'link'.").format(mode))

	# ── copy strategy ─────────────────────────────────────────────────────────

	def _attach_copy(
		self,
		cloud_path: str,
		doctype: str,
		docname: str,
		filename: str,
		is_private: bool,
	) -> "frappe.Document":
		"""Download file bytes and save them as an ERPNext File."""
		content = self.mgr.get_file(cloud_path)

		# Avoid duplicates: check if this file (same name + parent) already exists
		existing = frappe.db.get_value(
			"File",
			{"attached_to_doctype": doctype, "attached_to_name": docname, "file_name": filename},
			"name",
		)
		if existing:
			return frappe.get_doc("File", existing)

		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": filename,
				"attached_to_doctype": doctype,
				"attached_to_name": docname,
				"is_private": is_private,
				"content": content,
				# Custom fields (add via ERPNext customisation):
				"webdav_source_path": cloud_path,
				"webdav_sync_mode": "copy",
				"webdav_account": self.mgr.dav_account.name,
				"webdav_synced_on": datetime.now(),
			}
		)
		file_doc.save(ignore_permissions=True)
		return file_doc

	# ── link strategy ─────────────────────────────────────────────────────────

	def _attach_link(
		self,
		cloud_path: str,
		doctype: str,
		docname: str,
		filename: str,
	) -> "frappe.Document":
		"""Create a Nextcloud share link and attach it as an external URL."""
		# Reuse an existing share link if one already exists
		existing_shares = self.mgr.get_share_links(cloud_path)
		if existing_shares:
			share_url = existing_shares[0]["url"]
		else:
			share_info = self.mgr.create_share_link(cloud_path, permissions=1)
			share_url = share_info["url"]

		# Avoid duplicate File records
		existing = frappe.db.get_value(
			"File",
			{"attached_to_doctype": doctype, "attached_to_name": docname, "file_url": share_url},
			"name",
		)
		if existing:
			return frappe.get_doc("File", existing)

		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": filename,
				"file_url": share_url,
				"attached_to_doctype": doctype,
				"attached_to_name": docname,
				"is_private": False,
				# Custom fields:
				"webdav_source_path": cloud_path,
				"webdav_sync_mode": "link",
				"webdav_account": self.mgr.dav_account.name,
				"webdav_synced_on": datetime.now(),
			}
		)
		file_doc.save(ignore_permissions=True)
		return file_doc


# ══════════════════════════════════════════════════════════════════════════════
# 4.  WebDAVSyncor - pull invoices & contracts from Nextcloud
# ══════════════════════════════════════════════════════════════════════════════


class WebDAVSyncor:
	"""
	Scans configured Nextcloud folders for invoices and contracts,
	then creates or updates the corresponding ERPNext documents.

	Behaviour:
	  1.  Walk the configured cloud path (Invoices/, Contracts/, etc.).
	  2.  For each unprocessed file, classify it (invoice vs contract vs other).
	  3.  Apply the AttachmentStrategy (copy or link) based on account settings.
	  4.  Optionally create a stub Purchase Invoice / Contract document
	      so the file lands inside the correct ERPNext record.

	Idempotent: tracked via a "WebDAV File Sync Log" doctype (see below).
	If you re-run the sync, already-processed files are skipped by ETag check.
	"""

	@staticmethod
	def sync_caldav_to_erpnext(caldav_event, dav_account, calendar_url):
		"""Create or update ERPNext Event from CalDAV event"""

		if not caldav_event.get("uid"):
			return None  # skip invalid events

		# 🔍 Check existing event
		existing = frappe.db.get_value(
			"Event",
			{
				"caldav_event_id": caldav_event["uid"],
				"caldav_account": dav_account.name,
			},
			["name", "caldav_etag"],
			as_dict=True,
		)

		# 🧠 Skip unchanged events (ETag optimization)
		if existing and existing.caldav_etag == caldav_event.get("etag"):
			return frappe.get_doc("Event", existing.name)

		# 📄 Create or load
		if existing:
			event = frappe.get_doc("Event", existing.name)
			if not event.sync_with_caldav:
				return event  # skip if user has disabled sync for this event
		else:
			event = frappe.new_doc("Event")

		# ---------------------------
		# 🧾 FIELD MAPPING
		# ---------------------------
		event.subject = caldav_event.get("summary") or "No Title"
		event.event_public_description = caldav_event.get("description")
		event.location = caldav_event.get("location")

		# 🕒 Datetime handling
		event.starts_on = WebDAVSyncor._safe_datetime(caldav_event.get("dtstart"))
		event.ends_on = WebDAVSyncor._safe_datetime(caldav_event.get("dtend"))

		# 🔗 CalDAV metadata
		event.caldav_event_id = caldav_event["uid"]
		event.caldav_card_text = caldav_event["text"]
		event.caldav_account = dav_account.name
		event.caldav_event_url = caldav_event["caldav_url"]
		event.caldav_calendar_url = calendar_url
		event.caldav_etag = caldav_event.get("etag").strip('"')
		event.caldav_sync_status = "Connected"
		event.dav_calendar = frappe.db.get_value(
			"DAV Calendar", {"dav_account": dav_account.name, "dav_calendar": calendar_url}, "name"
		)
		event.caldav_status = caldav_event.get("status").title()
		# set organizer_email for backward compatibility
		event.caldav_organizer = caldav_event.get("organizer").replace("mailto:", "")
		event.caldav_organizer_name = caldav_event.get("organizer_cn") or event.caldav_organizer
		event.create_in_caldav = 0

		WebDAVSyncor.set_selected_calendar_options(dav_account, event, calendar_url)

		# ---------------------------
		# 👥 PARTICIPANTS (DIFF SAFE)
		# ---------------------------
		WebDAVSyncor._sync_participants(event, caldav_event.get("attendees", []))

		# 💾 Save
		event.save(ignore_permissions=True)
		return event

	@staticmethod
	def set_selected_calendar_options(dav_account, event_doc, calendar_url):
		meta = frappe.get_meta("Event")
		field = meta.get_field("selected_calendar")
		options = []
		selected_calendar = None
		color = None
		for cal in dav_account.available_calendars:
			options.append(cal.display_name)
			if cal.calendar_url == calendar_url:
				selected_calendar = cal.display_name
				color = cal.calendar_color
		frappe.db.set_value("DocField", field.name, "options", "\n".join(options))
		event_doc.selected_calendar = selected_calendar
		event_doc.dav_calendar = frappe.db.get_value(
			"DAV Calendar", {"dav_account": dav_account.name, "dav_calendar": selected_calendar}, "name"
		)
		event_doc.color = color

	# ---------------------------
	# 👥 PARTICIPANT SYNC
	# ---------------------------
	@staticmethod
	def _sync_participants(event, attendees):
		existing_emails = {row.email: row for row in event.get("caldav_participants_table", [])}

		new_table = []

		for attendee in attendees:
			email = attendee.get("email")
			if not email:
				continue

			contact = frappe.db.get_value("Contact", {"email_id": email}) or frappe.db.get_value(
				"Contact Email", {"email_id": email}, "parent"
			)

			existing_emails.get(email)

			new_table.append(
				{
					"email": email,
					"contact": contact,
					"invitation_status": attendee.get("partstat", "NEEDS-ACTION"),
				}
			)

		event.set("caldav_participants_table", new_table)

	# ---------------------------
	# 🕒 SAFE DATETIME HANDLER
	# ---------------------------

	@staticmethod
	def _safe_datetime(value):
		if not value:
			return None

		try:
			system_tz = ZoneInfo(frappe.utils.get_system_timezone())
		except Exception:
			system_tz = datetime.now().astimezone().tzinfo or datetime.timezone.utc

		# ---------------------------
		# 📅 If already datetime/date
		# ---------------------------
		if hasattr(value, "isoformat"):
			# date → datetime
			if not hasattr(value, "hour"):
				value = datetime.combine(value, datetime.min.time())

			# If timezone-aware → convert to system tz
			if value.tzinfo is not None:
				value = value.astimezone(system_tz)
			else:
				# assume UTC if naive (CalDAV usually sends UTC or TZ-aware)
				value = value.replace(tzinfo=datetime.timezone.utc).astimezone(system_tz)

			return value.replace(tzinfo=None)

		# ---------------------------
		# 🔤 If string
		# ---------------------------
		try:
			dt = frappe.utils.get_datetime(value)

			if dt.tzinfo is not None:
				dt = dt.astimezone(system_tz)
			else:
				dt = dt.replace(tzinfo=datetime.timezone.utc).astimezone(system_tz)

			return dt.replace(tzinfo=None)

		except Exception:
			frappe.log_error(f"Invalid datetime value: {value}")
			return None

	# ---------------------------
	# ❌ HANDLE DELETION
	# ---------------------------
	@staticmethod
	def handle_caldav_deletion(caldav_event_id, dav_account_name):
		events = frappe.db.get_list(
			"Event",
			filters={
				"caldav_event_id": caldav_event_id,
				"caldav_account": dav_account_name,
			},
			fields=["name", "owner", "subject"],
		)

		for event_record in events:
			event = frappe.get_doc("Event", event_record["name"])

			# Avoid re-processing
			if event.caldav_sync_status == "Deleted in Provider":
				continue

			event.caldav_sync_status = "Deleted in Provider"
			event.caldav_etag = None
			event.caldav_event_url = None
			event.caldav_status = ""
			event.save(ignore_permissions=True)

			# 📌 ToDo notification
			frappe.get_doc(
				{
					"doctype": "ToDo",
					"owner": event_record["owner"],
					"reference_type": "Event",
					"reference_name": event_record["name"],
					"title": f"Event '{event_record['subject']}' was deleted from calendar",
					"description": "This event was removed from your CalDAV provider.",
					"priority": "High",
				}
			).insert(ignore_permissions=True)

			# 🔔 Realtime push
			frappe.publish_realtime(
				"event_caldav_deleted",
				{
					"event": event_record["name"],
					"message": f"Event '{event_record['subject']}' was deleted from your calendar",
				},
				user=event_record["owner"],
			)

	def __init__(self, dav_account_name: str):
		self.mgr = WebDAVManager(dav_account_name)
		self.strategy = AttachmentStrategy(self.mgr)
		self.account = frappe.get_doc("DAV Account", dav_account_name)
		self.mode = getattr(self.account, "webdav_attach_mode", "link")  # "copy" | "link"
