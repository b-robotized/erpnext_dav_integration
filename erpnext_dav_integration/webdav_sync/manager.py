import mimetypes
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlparse
from uuid import uuid4

import frappe
import pybreaker
import pytz
import requests
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


class WebDAVManager:
	def __init__(self, dav_account_name):
		self.dav_account = frappe.get_doc("DAV Account", dav_account_name)
		self.username = self.dav_account.username
		self.password = get_decrypted_password("DAV Account", dav_account_name, "app_password")
		self.base_url = self.dav_account.base_url.rstrip("/")
		self.auth = HTTPBasicAuth(self.username, self.password)
		self.rate_limiter = RateLimiter(rate=5, per=1)
		expire_days = int(self.dav_account.default_share_link_expire_in or 0)
		self.file_expire_date = datetime.now(timezone.utc) + timedelta(days=expire_days)

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
		frappe.log_error(f"PROPFIND {path} → {response}", "WebDAV PROPFIND")
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
			frappe.throw(f"WebDAV GET failed [{path}]: {response.status_code}")
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
			frappe.throw("File was modified by another client.  Refresh the cloud version and retry.")
		if response.status_code not in (201, 204):
			frappe.throw(f"WebDAV PUT failed [{path}]: {response.status_code}\n{response.text}")

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
			# parent missing – ensure it exists first, then retry
			parent = str(PurePosixPath(path.rstrip("/")).parent) + "/"
			self.mkcol(parent)
			return self.mkcol(path)
		if response.status_code not in (200, 201):
			frappe.throw(f"MKCOL failed [{path}]: {response.status_code}")

		return True

	# ── DELETE ────────────────────────────────────────────────────────────────

	def delete(self, path: str) -> bool:
		"""Delete a resource (file or collection).  Returns True on success."""
		response = self._request("DELETE", path)
		if response.status_code == 404:
			return False
		if response.status_code not in (200, 204):
			frappe.throw(f"WebDAV DELETE failed [{path}]: {response.status_code}")
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
			frappe.throw(f"WebDAV COPY failed [{src} → {dst}]: {response.status_code}")
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
			frappe.throw(f"WebDAV MOVE failed [{src} → {dst}]: {response.status_code}")
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
				f"Nextcloud share creation failed [{normalized_path}]: "
				f"{response.status_code}\n{response.text}"
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
	def discover_calendars(self, skip_filtering=False):
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
			if not any(cal.dav_calendar_url == href for cal in dav_calendar_data) and not skip_filtering:
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
						"url": href,
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

	# ---------------------------
	# 📥 STEP 3: FETCH EVENTS
	# ---------------------------
	def fetch_events_from_calendar(self, calendar_url):
		url = f"{self.base_url}{calendar_url}"

		data = """<?xml version="1.0"?>
        <c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
            <d:prop>
                <d:getetag />
                <c:calendar-data />
            </d:prop>
            <c:filter>
                <c:comp-filter name="VCALENDAR">
                    <c:comp-filter name="VEVENT" />
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
						{"caldav_url": href, "etag": etag, "calendar_url": calendar_url, "text": cal_data}
					)
					events.append(event_data)

			except Exception as e:
				frappe.log_error(f"Event parse failed ({href}): {e!s}")

		return events

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
		vevent.add("dtstamp", datetime.now(pytz.UTC))
		vevent.add("created", datetime.now(pytz.UTC))
		vevent.add("last-modified", datetime.now(pytz.UTC))
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
			frappe.msgprint(f"Creating event in calendar: {calendar_url}", alert=True)
		except Exception as e:
			error_msg = f"CalDAV create request failed: {e!s}"
			frappe.log_error(error_msg)
			frappe.throw(error_msg)

		# Handle response
		if response.status_code not in [201, 204]:
			error_msg = f"Failed to create event in CalDAV: {response.status_code}\n{response.text}"
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
			frappe.throw("Event not connected to CalDAV")

		if not event_doc.caldav_event_url:
			frappe.throw("CalDAV URL not found")

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
		vevent.add("dtstamp", datetime.now(pytz.UTC))
		vevent.add("created", self._to_utc_datetime(event_doc.caldav_created) or datetime.now(pytz.UTC))
		vevent.add("last-modified", datetime.now(pytz.UTC))
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
				"Event was modified in the calendar. "
				"Click 'Refresh from Calendar' to get the latest version, then try again."
			)

		# Handle other errors
		if response.status_code not in [201, 204]:
			error_msg = f"CalDAV update failed: {response.status_code}\n{response.text}"
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
			frappe.throw("CalDAV URL not found")

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
				"Event was modified in the calendar. "
				"Click 'Refresh from Calendar' to get the latest version, then try again."
			)

		# Handle other errors
		if response.status_code not in [204, 200]:
			error_msg = f"Failed to delete event from CalDAV: {response.status_code}\n{response.text}"
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
			system_tz = pytz.timezone(frappe.utils.get_system_timezone())

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
				value = value.astimezone(pytz.UTC)
			else:
				# 🔴 CRITICAL FIX:
				value = system_tz.localize(value).astimezone(pytz.UTC)

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
		except:
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
		except:
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
# 2.  FolderProvisioner  –  employee & project folder management
# ══════════════════════════════════════════════════════════════════════════════


class FolderProvisioner:
	"""
	Manages folder structures on Nextcloud for:
	  - Employees : a shared employer folder  +  a private employee folder
	  - Projects  : a shared team folder

	Folder layout on Nextcloud (configurable via DAV Account):
	    /remote.php/dav/files/{admin}/
	        ERPNext/
	            Employees/
	                {employee_id}/
	                    Shared/          ← employer can read/write
	                    Private/         ← employee-only
	            Projects/
	                {project_id}/        ← shared team folder
	                    Invoices/
	                    Contracts/
	                    Assets/
	"""

	DEFAULT_ROOT = "ERPNext"

	def __init__(self, dav_account_name: str):
		self.mgr = WebDAVManager(dav_account_name)
		self.account = frappe.get_doc("DAV Account", dav_account_name)
		self.root = (getattr(self.account, "webdav_root_folder", None) or self.DEFAULT_ROOT).strip("/")
		self._files_base = f"/remote.php/dav/files/{self.mgr.username}/{self.root}"

	# ── path builders ─────────────────────────────────────────────────────────

	def _employee_base(self, employee_id: str) -> str:
		return f"{self._files_base}/Employees/{employee_id}"

	def _project_base(self, project_id: str) -> str:
		return f"{self._files_base}/Projects/{project_id}"

	# ── employee folders ──────────────────────────────────────────────────────

	def provision_employee_folders(self, employee_doc) -> dict:
		"""
		Create shared and private folders for *employee_doc*.
		Saves paths back to the Employee doctype fields:
		    webdav_shared_folder_url
		    webdav_private_folder_url

		Returns: {"shared": <path>, "private": <path>}
		"""
		emp_id = employee_doc.name
		shared = f"{self._employee_base(emp_id)}/Shared/"
		private = f"{self._employee_base(emp_id)}/Private/"

		self.mgr.ensure_folder(shared)
		self.mgr.ensure_folder(private)

		# Persist paths on the Employee doc
		employee_doc.webdav_shared_folder_url = shared
		employee_doc.webdav_private_folder_url = private
		employee_doc.webdav_account = self.account.name
		employee_doc.save(ignore_permissions=True)

		frappe.logger().info(f"FolderProvisioner: Employee {emp_id} → shared={shared}  private={private}")
		return {"shared": shared, "private": private}

	def get_employee_folder_contents(self, employee_doc, folder: str = "shared") -> list[dict]:
		"""
		List files in the employee's shared or private folder.
		*folder* must be "shared" or "private".
		"""
		if folder == "shared":
			path = employee_doc.webdav_shared_folder_url
		elif folder == "private":
			path = employee_doc.webdav_private_folder_url
		else:
			frappe.throw(f"Unknown folder type: {folder}")

		if not path:
			frappe.throw(
				f"Employee {employee_doc.name} has no {folder} folder provisioned.  "
				"Run provision_employee_folders() first."
			)

		return self.mgr.propfind(path, depth="1")

	# ── project folders ───────────────────────────────────────────────────────

	def provision_project_folders(self, project_doc) -> dict:
		"""
		Create the standard sub-folder structure for a project.
		Saves the base folder path to project_doc.technical_folder_url.

		Returns: {"base": ..., "invoices": ..., "contracts": ..., "assets": ...}
		"""
		proj_id = project_doc.name
		base = f"{self._project_base(proj_id)}/"
		invoices = f"{base}Invoices/"
		contracts = f"{base}Contracts/"
		assets = f"{base}Assets/"

		for folder in (base, invoices, contracts, assets):
			self.mgr.ensure_folder(folder)

		project_doc.technical_folder_url = base
		project_doc.webdav_account = self.account.name
		project_doc.save(ignore_permissions=True)

		frappe.logger().info(f"FolderProvisioner: Project {proj_id} → {base}")
		return {
			"base": base,
			"invoices": invoices,
			"contracts": contracts,
			"assets": assets,
		}

	def get_project_folder_contents(self, project_doc, subfolder: str = "") -> list[dict]:
		"""
		List files in the project folder (or a named sub-folder).
		*subfolder* can be "Invoices", "Contracts", "Assets", or "".
		"""
		base = project_doc.technical_folder_url
		if not base:
			frappe.throw(
				f"Project {project_doc.name} has no WebDAV folder provisioned.  "
				"Run provision_project_folders() first."
			)
		path = f"{base.rstrip('/')}/{subfolder}/" if subfolder else base
		return self.mgr.propfind(path, depth="1")


# ══════════════════════════════════════════════════════════════════════════════
# 3.  AttachmentStrategy  –  copy vs link
# ══════════════════════════════════════════════════════════════════════════════


class AttachmentStrategy:
	"""
	Determines *how* a Nextcloud file is attached to an ERPNext document.

	Two strategies:
	  COPY  – download the file and upload it to ERPNext's file storage.
	          The ERPNext File record owns the bytes; changes in Nextcloud
	          are NOT reflected automatically.

	  LINK  – create (or reuse) a Nextcloud public share link and attach it
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
			frappe.throw(f"AttachmentStrategy: unknown mode '{mode}'.  Use 'copy' or 'link'.")

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
# 4.  WebDAVSyncor  –  pull invoices & contracts from Nextcloud
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

		system_tz = pytz.timezone(frappe.utils.get_system_timezone())

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
				value = pytz.UTC.localize(value).astimezone(system_tz)

			return value.replace(tzinfo=None)

		# ---------------------------
		# 🔤 If string
		# ---------------------------
		try:
			dt = frappe.utils.get_datetime(value)

			if dt.tzinfo is not None:
				dt = dt.astimezone(system_tz)
			else:
				dt = pytz.UTC.localize(dt).astimezone(system_tz)

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

	# ── main entry point ──────────────────────────────────────────────────────

	def sync_folder(self, cloud_path: str, doctype: str, docname: str) -> list[dict]:
		"""
		Sync all files in *cloud_path* as attachments on *doctype*/*docname*.

		Returns a list of result dicts:
		    {"file": <filename>, "status": "synced"|"skipped"|"error", "file_doc": <name>}
		"""
		resources = self.mgr.propfind(cloud_path, depth="1")
		results = []

		for resource in resources:
			if resource["is_collection"]:
				continue  # skip sub-folders at this level

			try:
				result = self._sync_single_file(resource, doctype, docname)
				results.append(result)
			except Exception as exc:
				frappe.log_error(f"WebDAVSyncor: error syncing {resource['href']}: {exc}")
				results.append(
					{
						"file": resource["name"],
						"status": "error",
						"error": str(exc),
						"file_doc": None,
					}
				)
			time.sleep(0.1)  # rate limiting courtesy sleep

		return results

	def _sync_single_file(self, resource: dict, doctype: str, docname: str) -> dict:
		"""Sync one file resource.  Skip if ETag unchanged."""
		href = resource["href"]
		etag = resource["etag"]
		name = resource["name"]

		# ── ETag-based skip (same as CalDAV's etag optimisation) ──────────────
		log_name = frappe.db.get_value(
			"WebDAV File Sync Log",
			{"cloud_path": href, "dav_account": self.account.name},
			"name",
		)
		if log_name:
			cached_etag = frappe.db.get_value("WebDAV File Sync Log", log_name, "etag")
			if cached_etag == etag:
				return {"file": name, "status": "skipped", "file_doc": None}

		# ── attach ─────────────────────────────────────────────────────────────
		file_doc = self.strategy.attach(
			cloud_path=href,
			doctype=doctype,
			docname=docname,
			mode=self.mode,
			filename=name,
		)

		# ── upsert sync log ────────────────────────────────────────────────────
		self._upsert_sync_log(href, etag, doctype, docname, file_doc.name)

		return {"file": name, "status": "synced", "file_doc": file_doc.name}

	@staticmethod
	def _upsert_sync_log(cloud_path, etag, doctype, docname, file_doc_name):
		log_name = frappe.db.get_value(
			"WebDAV File Sync Log",
			{"cloud_path": cloud_path},
			"name",
		)
		if log_name:
			frappe.db.set_value(
				"WebDAV File Sync Log",
				log_name,
				{
					"etag": etag,
					"last_synced_on": datetime.now(),
					"erp_file": file_doc_name,
				},
			)
		else:
			frappe.get_doc(
				{
					"doctype": "WebDAV File Sync Log",
					"cloud_path": cloud_path,
					"dav_account": frappe.db.get_value("DAV Account", {"name": ["!=", ""]}, "name"),
					"etag": etag,
					"erp_doctype": doctype,
					"erp_docname": docname,
					"erp_file": file_doc_name,
					"last_synced_on": datetime.now(),
				}
			).insert(ignore_permissions=True)

	# ── pull invoices (folder-scan → Purchase Invoice stub) ───────────────────

	def pull_invoices_from_folder(self, cloud_folder: str) -> list[dict]:
		"""
		Scan *cloud_folder* for invoice files.
		For each found file:
		  1.  Create a stub Purchase Invoice (status=Draft) if none matched yet.
		  2.  Attach the cloud file using the configured strategy.

		Returns list of result dicts.
		"""
		resources = self.mgr.propfind(cloud_folder, depth="1")
		results = []

		for resource in resources:
			if resource["is_collection"]:
				continue
			if not INVOICE_KEYWORDS.search(resource["name"]):
				# Still process even without the keyword — caller picked this folder
				pass

			try:
				result = self._pull_single_invoice(resource)
				results.append(result)
			except Exception as exc:
				frappe.log_error(f"pull_invoices_from_folder: {resource['href']}: {exc}")
				results.append({"file": resource["name"], "status": "error", "error": str(exc)})
			time.sleep(0.1)

		return results

	def _pull_single_invoice(self, resource: dict) -> dict:
		href = resource["href"]
		name = resource["name"]
		etag = resource["etag"]

		# ── skip unchanged ──────────────────────────────────────────────────
		log_name = frappe.db.get_value("WebDAV File Sync Log", {"cloud_path": href}, "name")
		if log_name and frappe.db.get_value("WebDAV File Sync Log", log_name, "etag") == etag:
			return {"file": name, "status": "skipped"}

		# ── resolve or create a Purchase Invoice stub ───────────────────────
		erp_inv_name = self._resolve_or_create_invoice_stub(name)

		# ── attach file ─────────────────────────────────────────────────────
		file_doc = self.strategy.attach(
			cloud_path=href,
			doctype="Purchase Invoice",
			docname=erp_inv_name,
			mode=self.mode,
			filename=name,
		)

		self._upsert_sync_log(href, etag, "Purchase Invoice", erp_inv_name, file_doc.name)
		return {"file": name, "status": "synced", "invoice": erp_inv_name, "file_doc": file_doc.name}

	@staticmethod
	def _resolve_or_create_invoice_stub(filename: str) -> str:
		"""
		Try to match an existing Purchase Invoice by title/name.
		If none found, create a minimal draft stub named after the file.
		"""
		stem = PurePosixPath(filename).stem  # "INV-2024-001.pdf" → "INV-2024-001"

		# Try exact match on the name or title field
		match = frappe.db.get_value(
			"Purchase Invoice",
			{"title": ["like", f"%{stem}%"]},
			"name",
		)
		if match:
			return match

		# Create a stub
		inv = frappe.get_doc(
			{
				"doctype": "Purchase Invoice",
				"title": stem,
				"is_return": 0,
				"docstatus": 0,  # Draft
				# Required fields will raise validation — callers should enrich the doc
				# before submission.  For now we insert without validation:
			}
		)
		try:
			inv.flags.ignore_mandatory = True
			inv.insert(ignore_permissions=True)
			return inv.name
		except Exception:
			frappe.log_error(f"Could not create invoice stub for {filename}")
			raise

	# ── pull contracts ────────────────────────────────────────────────────────

	def pull_contracts_from_folder(self, cloud_folder: str) -> list[dict]:
		"""
		Scan *cloud_folder* for contract files and attach them to ERPNext Contract docs.
		Creates stub Contract documents where none exist.
		"""
		resources = self.mgr.propfind(cloud_folder, depth="1")
		results = []

		for resource in resources:
			if resource["is_collection"]:
				continue

			try:
				result = self._pull_single_contract(resource)
				results.append(result)
			except Exception as exc:
				frappe.log_error(f"pull_contracts_from_folder: {resource['href']}: {exc}")
				results.append({"file": resource["name"], "status": "error", "error": str(exc)})
			time.sleep(0.1)

		return results

	def _pull_single_contract(self, resource: dict) -> dict:
		href = resource["href"]
		name = resource["name"]
		etag = resource["etag"]

		log_name = frappe.db.get_value("WebDAV File Sync Log", {"cloud_path": href}, "name")
		if log_name and frappe.db.get_value("WebDAV File Sync Log", log_name, "etag") == etag:
			return {"file": name, "status": "skipped"}

		erp_contract_name = self._resolve_or_create_contract_stub(name)

		file_doc = self.strategy.attach(
			cloud_path=href,
			doctype="Contract",
			docname=erp_contract_name,
			mode=self.mode,
			filename=name,
		)

		self._upsert_sync_log(href, etag, "Contract", erp_contract_name, file_doc.name)
		return {
			"file": name,
			"status": "synced",
			"contract": erp_contract_name,
			"file_doc": file_doc.name,
		}

	@staticmethod
	def _resolve_or_create_contract_stub(filename: str) -> str:
		stem = PurePosixPath(filename).stem

		match = frappe.db.get_value(
			"Contract", {"contract_template": ["like", f"%{stem}%"]}, "name"
		) or frappe.db.get_value("Contract", {"name": ["like", f"%{stem}%"]}, "name")
		if match:
			return match

		contract = frappe.get_doc(
			{
				"doctype": "Contract",
				"party_name": stem,
				"status": "Draft",
			}
		)
		contract.flags.ignore_mandatory = True
		contract.insert(ignore_permissions=True)
		return contract.name

	# ── full account sync ─────────────────────────────────────────────────────

	def sync_all_project_folders(self):
		"""
		Iterate every Project that has a technical_folder_url set and sync
		Invoices/ and Contracts/ sub-folders.
		"""
		projects = frappe.get_all(
			"Project",
			filters={"technical_folder_url": ["!=", ""], "webdav_account": self.account.name},
			fields=["name", "technical_folder_url"],
		)

		for proj in projects:
			base = proj["technical_folder_url"].rstrip("/")
			try:
				self.pull_invoices_from_folder(f"{base}/Invoices/")
			except Exception as exc:
				frappe.log_error(f"sync_all_project_folders – invoices for {proj['name']}: {exc}")
			try:
				self.pull_contracts_from_folder(f"{base}/Contracts/")
			except Exception as exc:
				frappe.log_error(f"sync_all_project_folders – contracts for {proj['name']}: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# 6.  Scheduler entry points  –  add to hooks.py
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# 7.  Whitelisted API endpoints  –  call from JS / client scripts
# ══════════════════════════════════════════════════════════════════════════════


@frappe.whitelist()
def provision_employee_folders_api(employee_name: str, dav_account: str):
	"""
	API: POST /api/method/your_app.webdav_manager.provision_employee_folders_api
	Body: { employee_name, dav_account }
	"""
	emp_doc = frappe.get_doc("Employee", employee_name)
	provisioner = FolderProvisioner(dav_account)
	paths = provisioner.provision_employee_folders(emp_doc)
	return {"success": True, "paths": paths}


@frappe.whitelist()
def provision_project_folders_api(project_name: str, dav_account: str):
	proj_doc = frappe.get_doc("Project", project_name)
	provisioner = FolderProvisioner(dav_account)
	paths = provisioner.provision_project_folders(proj_doc)
	return {"success": True, "paths": paths}


@frappe.whitelist()
def list_employee_files_api(employee_name: str, folder: str = "shared"):
	emp_doc = frappe.get_doc("Employee", employee_name)
	dav = emp_doc.webdav_account
	if not dav:
		frappe.throw("Employee has no DAV account linked.")
	provisioner = FolderProvisioner(dav)
	files = provisioner.get_employee_folder_contents(emp_doc, folder)
	return files


@frappe.whitelist()
def list_project_files_api(project_name: str, subfolder: str = ""):
	proj_doc = frappe.get_doc("Project", project_name)
	dav = proj_doc.webdav_account
	if not dav:
		frappe.throw("Project has no DAV account linked.")
	provisioner = FolderProvisioner(dav)
	files = provisioner.get_project_folder_contents(proj_doc, subfolder)
	return files


@frappe.whitelist()
def attach_cloud_file_api(
	cloud_path: str,
	doctype: str,
	docname: str,
	mode: str = "link",
	dav_account: str | None = None,
):
	"""
	Attach a single cloud file to any ERPNext document.
	mode = "copy" | "link"
	"""
	if not dav_account:
		frappe.throw("DAV account must be configured by User to use Cloud Files.")

	mgr = WebDAVManager(dav_account)
	strategy = AttachmentStrategy(mgr)
	file_doc = strategy.attach(cloud_path, doctype, docname, mode=mode)
	return {"success": True, "file": file_doc.name, "file_url": file_doc.file_url}


@frappe.whitelist()
def sync_project_files_api(project_name: str):
	proj_doc = frappe.get_doc("Project", project_name)
	dav = proj_doc.webdav_account
	syncor = WebDAVSyncor(dav)
	base = proj_doc.technical_folder_url.rstrip("/")
	results = {
		"invoices": syncor.pull_invoices_from_folder(f"{base}/Invoices/"),
		"contracts": syncor.pull_contracts_from_folder(f"{base}/Contracts/"),
	}
	return results
