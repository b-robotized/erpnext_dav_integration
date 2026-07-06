# scheduler.py

import frappe

from .manager import WebDAVManager, WebDAVSyncor


def sync_all_caldav_events():
	"""Scheduled job to sync all CalDAV events"""
	accounts = frappe.get_list("DAV Account", filters={"sync_events": 1, "disabled": 0})

	for account in accounts:
		try:
			sync_user_caldav_events(account["name"])
		except Exception as e:
			frappe.log_error(f"Error syncing CalDAV for {account['name']}: {e!s}", title="CalDAV Sync Error")


def sync_user_caldav_events(dav_account_name):
	"""Sync events for a specific user"""
	dav_account = frappe.get_doc("DAV Account", dav_account_name)
	manager = WebDAVManager(dav_account_name)

	# Discover calendars if not cached
	if not dav_account.default_calendar_url:
		calendar_home = manager.get_calendar_home_set()
		dav_account.default_calendar_url = calendar_home
		dav_account.save(ignore_permissions=True)

	# Fetch and sync events from each calendar
	for calendar in dav_account.available_calendars:
		if calendar.sync_enabled:
			events = manager.fetch_events_from_calendar(calendar.calendar_url)
			for event_data in events:
				WebDAVSyncor.sync_caldav_to_erpnext(event_data, dav_account, calendar.calendar_url)


def scheduled_file_sync():
	"""
	Scheduled job: sync all project invoice / contract folders.
	Add to hooks.py:
	    scheduler_events = {
	        "hourly": ["your_app.webdav_manager.scheduled_file_sync"]
	    }
	"""
	accounts = frappe.get_all(
		"DAV Account",
		filters={"webdav_sync_enabled": 1},
		pluck="name",
	)
	for account_name in accounts:
		try:
			syncor = WebDAVSyncor(account_name)
			syncor.sync_all_project_folders()
		except Exception as exc:
			frappe.log_error(f"scheduled_file_sync – account {account_name}: {exc}")
