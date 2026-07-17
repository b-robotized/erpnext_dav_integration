import frappe


def after_uninstall():
	# Restore the original Frappe setting
	frappe.db.set_value("DocType", "Event", "read_only", 1)
	frappe.clear_cache(doctype="Event")
