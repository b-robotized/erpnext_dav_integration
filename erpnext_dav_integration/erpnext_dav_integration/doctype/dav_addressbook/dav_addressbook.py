# Copyright (c) 2026, b»robotized group and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class DAVAddressBook(Document):
	def after_insert(self):
		frappe.db.set_value(
			"Contact",
			{"custom_dav_address_book": self.dav_addressbook, "custom_dav_account": self.dav_account},
			"dav_addressbook",
			self.name,
			update_modified=False,
		)

	def on_trash(self):
		frappe.db.set_value(
			"Contact",
			{"custom_dav_address_book": self.dav_addressbook, "custom_dav_account": self.dav_account},
			"dav_addressbook",
			None,
			update_modified=False,
		)

	def validate(self):
		if not self.dav_account:
			frappe.throw(_("DAV Account is required for Address Book"))
		if not self.dav_addressbook:
			frappe.throw(_("DAV Address Book URL is required"))
		# Ensure uniqueness of dav_addressbook per dav_account
		existing = frappe.get_all(
			"DAV AddressBook",
			filters={
				"dav_account": self.dav_account,
				"dav_addressbook": self.dav_addressbook,
				"name": ["!=", self.name],
			},
			limit=1,
		)
		if existing:
			frappe.throw(_("This DAV Address Book is already linked to the selected DAV Account."))
