# Copyright (c) 2026, b»robotized group and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class DAVCalendar(Document):
	def after_insert(self):
		frappe.db.set_value(
			"Event",
			{"selected_calendar": self.dav_calendar, "caldav_account": self.dav_account},
			"dav_calendar",
			self.name,
			update_modified=False,
		)

	def on_trash(self):
		frappe.db.set_value(
			"Event",
			{"selected_calendar": self.dav_calendar, "caldav_account": self.dav_account},
			"dav_calendar",
			None,
			update_modified=False,
		)

	def validate(self):
		if not self.dav_account:
			frappe.throw(_("DAV Account is required for Calendar"))
		if not self.dav_calendar:
			frappe.throw(_("DAV Calendar URL is required"))
		# Ensure uniqueness of dav_calendar per dav_account
		existing = frappe.get_all(
			"DAV Calendar",
			filters={
				"dav_account": self.dav_account,
				"dav_calendar": self.dav_calendar,
				"name": ["!=", self.name],
			},
			limit=1,
		)
		if existing:
			frappe.throw(_("This DAV Calendar is already linked to the selected DAV Account."))
