import frappe
def execute():
    from frappe.utils import get_url
    from frappe import _

    for addressbook in frappe.get_all("DAV AddressBook", fields=["name", "dav_addressbook", "dav_addressbook_url", "dav_account"]):
        if not addressbook.dav_addressbook_url:
            url = get_address_book_url(addressbook.dav_account, addressbook.dav_addressbook)
            frappe.db.set_value("DAV AddressBook", addressbook.name, "dav_addressbook_url", url)
            frappe.db.commit()
            frappe.msgprint(
                _("Updated URL for DAV Address Book {0}").format(addressbook.name)
            )

def get_address_book_url(doc, address_book_name):
	doc = frappe.get_doc("DAV Account", doc)
	for row in doc.dav_address_books:
		if row.address_book_name == address_book_name:
			return row.url
	return None