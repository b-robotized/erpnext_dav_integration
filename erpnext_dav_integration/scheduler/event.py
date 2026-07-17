import re
from datetime import datetime, timedelta

import frappe
import pytz
from frappe import _

from erpnext_dav_integration.webdav_sync.manager import WebDAVManager, WebDAVSyncor

# ---------------------------
# 🔄 HOURLY SYNC
# ---------------------------


def sync_all_caldav_events():
	"""
	Hourly scheduled job to sync all CalDAV events

	Called every hour by Frappe scheduler
	"""
	try:
		# Get all enabled DAV accounts
		accounts = frappe.db.get_list(
			"DAV Account",
			filters={"enabled": 1},
			fields=["name", "username", "base_url"],
		)

		if not accounts:
			frappe.logger().info("No DAV accounts to sync")
			return

		frappe.logger().info(f"Starting CalDAV sync for {len(accounts)} accounts")

		for account in accounts:
			try:
				sync_user_caldav_events(account["name"])
			except Exception as e:
				frappe.log_error(f"Error syncing CalDAV for {account['name']}: {e!s}", "CalDAV Sync Error")

		frappe.logger().info("CalDAV sync completed")

	except Exception as e:
		frappe.log_error(str(e), "CalDAV Scheduler Error")


# ---------------------------
# 👤 SYNC SINGLE ACCOUNT
# ---------------------------


def sync_user_caldav_events(dav_account_name):
	"""
	Sync all events for a specific user account

	Args:
	    dav_account_name: Name of DAV Account doc
	"""

	dav_account = frappe.get_doc("DAV Account", dav_account_name)

	# Check if account is still enabled
	if not dav_account.enabled:
		frappe.logger().info(f"Skipping disabled account: {dav_account_name}")
		return

	frappe.logger().info(f"Syncing CalDAV for account: {dav_account_name}")

	try:
		manager = WebDAVManager(dav_account_name)

		# Discover calendars if not cached
		if not dav_account.default_calendar_url:
			calendar_home = manager.get_calendar_home_set()
			dav_account.default_calendar_url = calendar_home
			dav_account.save(ignore_permissions=True)
			frappe.logger().info(f"Discovered calendar home: {calendar_home}")

		# Get calendars
		calendars = manager.discover_calendars()

		if not calendars:
			frappe.logger().warning(f"No calendars found for {dav_account_name}")
			return

		frappe.logger().info(f"Found {len(calendars)} calendars")

		# Sync events from each calendar
		for calendar in calendars:
			if not calendar.get("url"):
				continue

			sync_single_calendar(dav_account, calendar)
		# Check for deleted events
		check_deleted_events(dav_account_name)

		frappe.logger().info(f"Sync completed for {dav_account_name}")

	except Exception as e:
		frappe.log_error(f"Failed to sync {dav_account_name}: {e!s}", "CalDAV Sync Error")
		raise


# ---------------------------
# 📅 SYNC SINGLE CALENDAR
# ---------------------------


def sync_single_calendar(dav_account, calendar):
	"""
	Sync all events from a single calendar

	Args:
	    dav_account: DAV Account doc
	    calendar: dict with 'url', 'name', 'getctag'
	"""

	try:
		calendar_url = calendar["url"]
		calendar_name = calendar["name"]

		frappe.logger().info(f"Syncing calendar: {calendar_name}")

		# Create manager
		manager = WebDAVManager(dav_account.name)

		# Check if calendar changed using ctag
		last_sync = frappe.db.get_value(
			"DAV Account Calendar", {"parent": dav_account.name, "calendar_url": calendar_url}, "last_ctag"
		)

		current_ctag = calendar.get("getctag")

		# Skip if nothing changed (optimization)
		if last_sync and last_sync == current_ctag:
			frappe.logger().info(f"Calendar {calendar_name} unchanged, skipping")
			return

		# Fetch all events
		events = manager.fetch_events_from_calendar(calendar_url)
		frappe.logger().info(f"Found {len(events)} events in {calendar_name}")

		synced_count = 0

		# Sync each event
		for event_data in events:
			try:
				event = WebDAVSyncor.sync_caldav_to_erpnext(event_data, dav_account, calendar_url)

				if event:
					synced_count += 1

			except Exception as e:
				frappe.log_error(
					f"Failed to sync event {event_data.get('uid')}: {e!s}", "Calendar Event Sync Error"
				)

		# Update last sync time
		frappe.db.set_value(
			"DAV Account Calendar",
			{"parent": dav_account.name, "calendar_url": calendar_url},
			{"last_ctag": current_ctag, "last_sync": frappe.utils.now()},
		)

		frappe.logger().info(f"Synced {synced_count} events from {calendar_name}")

	except Exception as e:
		frappe.log_error(f"Failed to sync calendar {calendar.get('name')}: {e!s}", "Calendar Sync Error")


# ---------------------------
# 🔍 CHECK DELETED EVENTS
# ---------------------------


def check_deleted_events(dav_account_name):
	"""
	Check if any synced events were deleted from CalDAV

	Args:
	    dav_account_name: Name of DAV Account
	"""

	try:
		frappe.log_error(f"Check for deleted events {dav_account_name}2")
		frappe.logger().info(f"Checking for deleted events in {dav_account_name}")

		dav_account = frappe.get_doc("DAV Account", dav_account_name)
		if not dav_account.enabled:
			return
		if not dav_account.auto_delete_caldav_events:
			return
		manager = WebDAVManager(dav_account_name)
		frappe.log_error(f"Check for deleted events {dav_account_name}3")

		# Get all currently synced events for this account
		synced_events = frappe.db.get_list(
			"Event",
			filters={
				"caldav_account": dav_account_name,
				"caldav_event_id": ("is", "set"),
				"sync_with_caldav": 1,
			},
			fields=["name", "caldav_event_id", "caldav_event_url", "subject"],
		)

		if not synced_events:
			return
		# Get all events currently in CalDAV
		calendars = manager.discover_calendars()
		caldav_event_urls = set()

		for calendar in calendars:
			caldav_event_urls.update(manager.fetch_event_hrefs_from_calendar(calendar["url"]))

		# Find missing events
		deleted_count = 0
		for synced_event in synced_events:
			if not synced_event.get("caldav_event_url"):
				continue

			local_url = manager._normalize_caldav_event_url(synced_event["caldav_event_url"])
			if local_url not in caldav_event_urls:
				# Event was deleted from CalDAV
				handle_caldav_event_deletion(synced_event["caldav_event_id"], dav_account_name, synced_event)
				deleted_count += 1

		if deleted_count > 0:
			frappe.logger().warning(f"Found {deleted_count} deleted events")

	except Exception as e:
		frappe.log_error(f"Error checking deleted events: {e!s}", "Check Deletion Error")


# ---------------------------
# ❌ HANDLE DELETED EVENT
# ---------------------------


def handle_caldav_event_deletion(caldav_event_id, dav_account_name, event_record):
	"""
	Handle event deleted from CalDAV

	Args:
	    caldav_event_id: Event UID
	    dav_account_name: DAV Account name
	    event_record: Event record info
	"""

	try:
		event = frappe.get_doc("Event", event_record["name"])

		# Avoid re-processing
		if event.caldav_sync_status == "Deleted in Provider":
			return

		# Mark as cancelled
		event.event_type = "Cancelled"
		event.caldav_sync_status = "Deleted in Provider"
		event.save(ignore_permissions=True)

		frappe.logger().warning(f"Event {event_record['name']} deleted from CalDAV")

		# Create Todo for the owner
		create_deletion_todo(event, event_record)

		# Send notification
		send_deletion_notification(event_record)

	except Exception as e:
		frappe.log_error(f"Failed to handle deletion of {caldav_event_id}: {e!s}", "Deletion Handler Error")


# ---------------------------
# 📌 CREATE DELETION TODO
# ---------------------------


def create_deletion_todo(event, event_record):
	"""
	Create Todo for event owner about deletion
	"""

	try:
		frappe.get_doc(
			{
				"doctype": "ToDo",
				"owner": event.owner,
				"reference_type": "Event",
				"reference_name": event_record["name"],
				"title": f"Event '{event_record['subject']}' was deleted from calendar",
				"description": (
					f"The event '{event_record['subject']}' was removed from your CalDAV provider. "
					f"The event has been marked as cancelled in ERPNext. "
					f"Please review and take any necessary action."
				),
				"priority": "High",
			}
		).insert(ignore_permissions=True)

		frappe.logger().info(f"Created Todo for {event.owner}")

	except Exception as e:
		frappe.log_error(f"Failed to create Todo: {e!s}", "Todo Creation Error")


# ---------------------------
# 🔔 SEND DELETION NOTIFICATION
# ---------------------------


def send_deletion_notification(event_record):
	"""
	Send real-time notification about deletion
	"""

	try:
		event = frappe.get_doc("Event", event_record["name"])

		frappe.publish_realtime(
			"event_caldav_deleted",
			{
				"event": event_record["name"],
				"subject": event_record["subject"],
				"message": f"Event '{event_record['subject']}' was deleted from your calendar",
			},
			user=event.owner,
		)

		frappe.logger().info(f"Sent notification to {event.owner}")

	except Exception as e:
		frappe.log_error(f"Failed to send notification: {e!s}", "Notification Error")


# ---------------------------
# 🧹 CLEANUP STALE EVENTS
# ---------------------------


def cleanup_stale_events():
	"""
	Cleanup events marked as deleted from provider for > 30 days

	Called weekly or monthly
	"""

	try:
		frappe.logger().info("Starting cleanup of stale events")

		# Find events deleted 30+ days ago
		cutoff_date = datetime.now(pytz.UTC) - timedelta(days=30)

		stale_events = frappe.db.get_list(
			"Event",
			filters={"caldav_sync_status": "Deleted in Provider", "modified": ("<", cutoff_date)},
			fields=["name", "subject"],
		)

		if not stale_events:
			frappe.logger().info("No stale events to cleanup")
			return

		frappe.logger().info(f"Found {len(stale_events)} stale events")

		# Archive or delete old events
		for event_record in stale_events:
			try:
				# Option 1: Just archive (set as cancelled, don't delete)
				# This preserves history

				frappe.get_doc("Event", event_record["name"])
				# Already marked as cancelled, so just leave it

				frappe.logger().info(f"Archived: {event_record['name']}")

			except Exception as e:
				frappe.log_error(f"Failed to cleanup {event_record['name']}: {e!s}", "Cleanup Error")

		frappe.logger().info("Cleanup completed")

	except Exception as e:
		frappe.log_error(str(e), "Cleanup Error")


# ---------------------------
# 📊 SYNC STATISTICS
# ---------------------------


def log_sync_statistics(dav_account_name):
	"""
	Log sync statistics for monitoring
	"""

	try:
		# Get sync stats
		total_events = frappe.db.count("Event", filters={"caldav_account": dav_account_name})

		connected_events = frappe.db.count(
			"Event", filters={"caldav_account": dav_account_name, "caldav_sync_status": "Connected"}
		)

		out_of_sync = frappe.db.count(
			"Event", filters={"caldav_account": dav_account_name, "caldav_sync_status": "OutOfSync"}
		)

		deleted = frappe.db.count(
			"Event", filters={"caldav_account": dav_account_name, "caldav_sync_status": "Deleted in Provider"}
		)

		frappe.logger().info(
			f"Sync stats for {dav_account_name}: "
			f"Total={total_events}, Connected={connected_events}, "
			f"OutOfSync={out_of_sync}, Deleted={deleted}"
		)

	except Exception as e:
		frappe.log_error(f"Failed to log statistics: {e!s}")
