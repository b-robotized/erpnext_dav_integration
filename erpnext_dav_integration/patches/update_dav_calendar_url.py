import frappe
def execute():
    from frappe.utils import get_url
    from frappe import _

    for calendar in frappe.get_all("DAV Calendar", fields=["name", "dav_calendar", "dav_calendar_url", "dav_account"]):
        if not calendar.dav_calendar_url:
            url = get_calendar_url(calendar.dav_account, calendar.dav_calendar)
            frappe.db.set_value("DAV Calendar", calendar.name, "dav_calendar_url", url)
            frappe.db.commit()
            frappe.msgprint(
                _("Updated URL for DAV Calendar {0}").format(calendar.name)
            )

def get_calendar_url(doc, calendar_name):
	doc = frappe.get_doc("DAV Account", doc)
	for row in doc.available_calendars:
		if row.display_name == calendar_name:
			return row.calendar_url
	return None