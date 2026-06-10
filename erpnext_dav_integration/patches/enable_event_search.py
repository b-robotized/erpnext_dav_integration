import frappe


def execute():
    # Update the DocType metadata
    frappe.db.set_value("DocType", "Event", "read_only", 0)

    # Clear cache so metadata is reloaded
    frappe.clear_cache(doctype="Event")