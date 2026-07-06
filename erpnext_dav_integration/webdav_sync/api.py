# caldav_sync/api.py

import json
import time

import frappe
from frappe import _

from .manager import WebDAVManager, WebDAVSyncor


def check_rate_limit(key, limit=20, seconds=60):
	cache = frappe.cache()
	current = cache.get_value(key) or 0

	if current >= limit:
		frappe.throw("Rate limit exceeded. Try again later.")

	cache.set_value(key, current + 1, expires_in_sec=seconds)


@frappe.whitelist()
def get_calendars(dav_account):
	"""Get available calendars for the account"""
	check_rate_limit(f"user:{frappe.session.user}:get_calendars", 10, 60)

	dav_doc = frappe.get_doc("DAV Account", dav_account)
	return dav_doc.available_calendars


@frappe.whitelist()
def get_calendar_url(dav_account, calendar_name):
	"""Get calendar URL for a given calendar name"""
	check_rate_limit(f"user:{frappe.session.user}:get_calendar_url", 10, 60)
	# Check permission
	if not frappe.has_permission("DAV Account", "read", dav_account):
		frappe.throw("No permission", frappe.PermissionError)

	calendar_values = frappe.db.get_value(
		"DAV Account Calendar",
		{"parent": dav_account, "display_name": calendar_name},
		["calendar_url", "calendar_color"],
	)

	if not calendar_values:
		frappe.throw("Calendar not found")

	calendar_url, calendar_color = calendar_values
	if not calendar_url:
		frappe.throw("Calendar not found")

	return calendar_url, calendar_color


@frappe.whitelist()
def refresh_calendar_discovery(dav_account):
	"""Force rediscovery of calendars"""
	if not frappe.has_permission("DAV Account", "write", dav_account):
		frappe.throw("No permission", frappe.PermissionError)

	manager = WebDAVManager(dav_account)

	# Discover calendar-home-set
	calendar_home = manager.get_calendar_home_set()

	# Discover calendars
	calendars = manager.discover_calendars()

	# Update DAV Account
	dav_doc = frappe.get_doc("DAV Account", dav_account)
	dav_doc.default_calendar_url = calendar_home
	dav_doc.available_calendars = []

	for cal in calendars:
		dav_doc.append(
			"available_calendars",
			{"calendar_url": cal["url"], "display_name": cal["name"], "sync_enabled": 1},
		)

	dav_doc.save(ignore_permissions=True)
	return "Calendars discovered successfully"


def fetch_events_from_dav_calendar():
	frappe.flags.in_caldav_sync = True  # Set a flag to indicate sync is in progress
	account = frappe.get_doc("DAV Account", {"enabled": 1, "default": 1})

	if not account:
		# Fallback to any enabled DAV account if there is no default configured
		account = frappe.get_doc("DAV Account", {"enabled": 1, "default": 0})

	if not account:
		frappe.throw(_("No active DAV Account with default calendar found"))
	else:
		manager = WebDAVManager(account.name)

		try:
			manager.sync_caldav_to_erpnext()
		except Exception as e:
			frappe.log_error(str(e))

		time.sleep(2)  # 🔴 throttle between accounts


@frappe.whitelist()
def enqueue_fetch_events_from_dav_calendar():
	"""Enqueue the fetch_events_from_dav_calendar function to run in the background"""
	check_rate_limit(f"user:{frappe.session.user}:enqueue_fetch_events_from_dav_calendar", 10, 60)
	frappe.enqueue(
		"erpnext_dav_integration.webdav_sync.api.fetch_events_from_dav_calendar", queue="long", timeout=6000
	)


@frappe.whitelist()
def sync_with_dav_calendar(event_names):
	"""
	Hook called on Event save
	Auto-syncs changes to CalDAV if event is connected
	"""
	check_rate_limit(f"user:{frappe.session.user}:sync_with_dav_calendar", 10, 60)
	if isinstance(event_names, str):
		event_names = json.loads(event_names)
	for event_name in event_names if isinstance(event_names, list) else [event_names]:
		doc = frappe.get_doc("Event", event_name)

		# Only sync if connected to CalDAV
		if not doc.caldav_event_id or not doc.caldav_account:
			return

		try:
			# Sync to CalDAV
			manager = WebDAVManager(doc.caldav_account)
			result = manager.update_event_in_calendar(doc)

			# Update metadata
			doc.caldav_etag = result["etag"]
			doc.caldav_sequence = result["sequence"]
			doc.caldav_sync_status = "Connected"
			doc.caldav_card_text = result.get("caldav_card_text")
			doc.save(ignore_permissions=True)
			return doc.name
		except Exception as e:
			doc.caldav_sync_status = "OutOfSync"
			frappe.msgprint(
				_("Event saved but couldn't sync to calendar: {0}").format(str(e)),
				alert=True,
				indicator="orange",
			)
			frappe.log_error(frappe.get_traceback(), "CalDAV Sync Error")


# ---------------------------
# ✨ CREATE IN CALDAV
# ---------------------------
@frappe.whitelist()
def create_caldav_event(event_name, calendar_name):
	"""
	Create new event in CalDAV
	Called when user clicks "Create in Calendar" button
	"""
	check_rate_limit(f"user:{frappe.session.user}:create_caldav_event", 10, 60)
	if not frappe.has_permission("Event", "write", event_name):
		frappe.throw(_("No permission"))

	event = frappe.get_doc("Event", event_name)

	if not event.caldav_account:
		frappe.throw(_("Please select a calendar account"))

	if not calendar_name:
		frappe.throw(_("Please select a calendar"))

	try:
		# Get calendar URL
		calendar_record = frappe.db.get_value(
			"DAV Account Calendar",
			{"parent": event.caldav_account, "display_name": calendar_name},
			["calendar_url"],
			as_dict=True,
		)

		if not calendar_record:
			frappe.throw(_("Calendar not found"))

		# Create in CalDAV
		manager = WebDAVManager(event.caldav_account)
		result = manager.create_event_in_calendar(calendar_record["calendar_url"], event)

		# Update Event
		event.caldav_event_id = result["caldav_event_id"]
		event.caldav_event_url = manager.base_url.rstrip("/") + "/" + result["caldav_url"].lstrip("/")
		event.caldav_uuid = result["caldav_uuid"]
		event.caldav_etag = result.get("etag")
		event.caldav_sync_status = "Connected"
		event.caldav_status = (result.get("status") or "CONFIRMED").title()
		if result.get("status") == "CANCELLED":
			event.status = "Cancelled"
		else:
			event.status = "Open"
		event.caldav_sequence = 0
		event.caldav_created = frappe.utils.now()
		event.caldav_card_text = result.get("caldav_card_text")

		event.save(ignore_permissions=True)

		frappe.msgprint(_("Event created in calendar successfully"), alert=True, indicator="green")

		return event.name

	except Exception as e:
		frappe.log_error(str(e), "Create Event Error")
		frappe.throw(_("Failed to create event: {0}").format(str(e)))


# ---------------------------
# 🔄 SYNC TO CALDAV
# ---------------------------


@frappe.whitelist()
def sync_to_caldav(event_name):
	"""
	Manually sync event to CalDAV
	Called when user clicks "Sync Now" button
	"""
	check_rate_limit(f"user:{frappe.session.user}:sync_to_caldav", 10, 60)
	if not frappe.has_permission("Event", "write", event_name):
		frappe.throw(_("No permission"))

	event = frappe.get_doc("Event", event_name)

	if not event.caldav_event_id:
		frappe.throw(_("This event is not connected to any calendar"))

	if not event.caldav_account:
		frappe.throw(_("Calendar account not found"))

	try:
		manager = WebDAVManager(event.caldav_account)
		result = manager.update_event_in_calendar(event)

		# Update metadata
		event.caldav_etag = result["etag"]
		event.caldav_sequence = result["sequence"]
		event.caldav_sync_status = "Connected"

		event.save(ignore_permissions=True)

		frappe.msgprint(_("Event synced to calendar successfully"), alert=True, indicator="green")

		return event.name

	except Exception as e:
		frappe.log_error(str(e), "Sync Event Error")
		frappe.throw(_("Failed to sync event: {0}").format(str(e)))


# ---------------------------
# ❌ DELETE FROM CALDAV
# ---------------------------


@frappe.whitelist()
def delete_caldav_event(event_name):
	"""
	Delete event from CalDAV
	Called when user clicks "Delete from Calendar" button
	"""
	check_rate_limit(f"user:{frappe.session.user}:delete_caldav_event", 10, 60)

	if not frappe.has_permission("Event", "delete", event_name):
		frappe.throw(_("No permission"))

	event = frappe.get_doc("Event", event_name)

	if not event.caldav_event_id:
		frappe.throw(_("This event is not connected to any calendar"))

	if not event.caldav_account:
		frappe.throw(_("Calendar account not found"))

	try:
		manager = WebDAVManager(event.caldav_account)
		manager.delete_event_from_calendar(event)

		# Clear CalDAV fields
		event.caldav_event_id = None
		event.caldav_event_url = None
		event.caldav_uuid = None
		event.caldav_etag = None
		event.caldav_sync_status = "Not Connected"
		event.caldav_sequence = 0
		event.caldav_created = None

		event.save(ignore_permissions=True)

		frappe.msgprint(_("Event deleted from calendar successfully"), alert=True, indicator="green")

		return event.name

	except Exception as e:
		frappe.log_error(str(e), "Delete Event Error")
		frappe.throw(_("Failed to delete event: {0}").format(str(e)))


# ---------------------------
# 🔄 REFRESH FROM CALDAV
# ---------------------------


@frappe.whitelist()
def refresh_from_caldav(event_name):
	"""
	Refresh event from CalDAV
	Called when user clicks "Refresh from Calendar" button
	"""
	check_rate_limit(f"user:{frappe.session.user}:refresh_from_caldav", 10, 60)
	if not frappe.has_permission("Event", "write", event_name):
		frappe.throw(_("No permission"))

	event = frappe.get_doc("Event", event_name)

	if not event.caldav_event_id or not event.caldav_account:
		frappe.throw(_("This event is not connected to any calendar"))

	try:
		# Fetch from CalDAV
		manager = WebDAVManager(event.caldav_account)
		frappe.get_doc("DAV Account", event.caldav_account)

		event_url = event.caldav_event_url
		if not event_url:
			frappe.throw(_("Event URL not found for this event"))

		caldav_event = manager.fetch_single_event(event_url)
		if not caldav_event:
			frappe.throw(_("Event not found in calendar"))
		caldaveventsycor = WebDAVSyncor()
		# Update fields (but keep private description)
		event.subject = caldav_event.get("summary")
		event.event_public_description = caldav_event.get("description")
		event.location = caldav_event.get("location")
		event.starts_on = caldaveventsycor._safe_datetime(caldav_event.get("dtstart"))
		event.ends_on = caldaveventsycor._safe_datetime(caldav_event.get("dtend"))
		event.caldav_etag = caldav_event.get("etag")
		event.caldav_sync_status = "Connected"
		event.caldav_status = caldav_event.get("status").title()
		event.event_public_description = caldav_event.get("description")
		if caldav_event.get("status") == "CANCELLED":
			event.status = "Cancelled"
		else:
			event.status = "Open"

		# Update participants (preserve send_invitation flags)
		existing_participants = {p.email: p for p in event.get("caldav_participants_table", [])}
		event.set("caldav_participants_table", [])

		for attendee in caldav_event.get("attendees", []):
			email = attendee["email"]
			existing = existing_participants.get(email)

			contact = frappe.db.get_value("Contact", {"email_id": email}, "name")

			event.append(
				"caldav_participants_table",
				{
					"email": email,
					"contact": contact,
					"invitation_status": attendee.get("partstat", "NEEDS-ACTION"),
					"send_invitation": existing.send_invitation if existing else False,
				},
			)

		event.save(ignore_permissions=True)

		frappe.msgprint(_("Event refreshed from calendar"), alert=True, indicator="green")

		return event.name

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Refresh Event Error")
		frappe.throw(_("Failed to refresh event: {0}").format(str(e)))


# ---------------------------
# 🔗 UNLINK FROM CALDAV
# ---------------------------


@frappe.whitelist()
def unlink_from_caldav(event_name):
	"""
	Disconnect event from CalDAV
	Called when user clicks "Unlink from Calendar" button
	"""
	check_rate_limit(f"user:{frappe.session.user}:unlink_from_caldav", 10, 60)
	if not frappe.has_permission("Event", "write", event_name):
		frappe.throw(_("No permission"))

	event = frappe.get_doc("Event", event_name)

	# Clear all CalDAV fields
	event.caldav_event_id = None
	event.caldav_event_url = None
	event.caldav_uuid = None
	event.caldav_etag = None
	event.caldav_account = None
	event.caldav_sync_status = "Not Connected"
	event.caldav_sequence = 0
	event.caldav_created = None

	# Move public description to private if needed
	if event.event_public_description and not event.event_private_description:
		event.event_private_description = event.event_public_description
		event.event_public_description = None

	event.save(ignore_permissions=True)

	frappe.msgprint(_("Event disconnected from calendar"), alert=True, indicator="green")

	return event.name


# ---------------------------
# 📧 SEND INVITATION
# ---------------------------


@frappe.whitelist()
def send_invitation(event_name, participant_email):
	"""
	Send calendar invitation to participant
	Called when user checks "Send Invitation" on a participant
	"""
	check_rate_limit(f"user:{frappe.session.user}:send_invitation", 10, 60)
	if not frappe.has_permission("Event", "write", event_name):
		frappe.throw(_("No permission"))

	event = frappe.get_doc("Event", event_name)

	if not event.caldav_event_id:
		frappe.throw(_("This event is not connected to any calendar"))

	# Find participant
	participant = None
	for p in event.get("caldav_participants_table", []):
		if p.email == participant_email:
			participant = p
			break

	if not participant:
		frappe.throw(_("Participant not found"))

	try:
		# Sync to calendar (which sends invitation)
		manager = WebDAVManager(event.caldav_account)
		result = manager.update_event_in_calendar(event)

		event.caldav_etag = result["etag"]
		event.caldav_sequence = result["sequence"]
		event.save(ignore_permissions=True)

		frappe.msgprint(_("Invitation sent to {0}").format(participant_email), alert=True, indicator="green")

		return True

	except Exception as e:
		frappe.log_error(str(e), "Send Invitation Error")
		frappe.throw(_("Failed to send invitation: {0}").format(str(e)))
