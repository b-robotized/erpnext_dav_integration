# Copyright (c) 2026, b»robotized group and contributors
# For license information, please see license.txt

from xml.etree import ElementTree as ET

import frappe
from frappe import _
import requests
from frappe.model.document import Document
from frappe.utils.password import get_decrypted_password
from requests.auth import HTTPBasicAuth

class DAVAccount(Document):
    def validate(self):
        self.sync_address_books_on_save()
    def sync_address_books_on_save(self):
        # If already populated, skip
        if self.dav_address_books:
            return
        try:
            self.fetch_and_store_address_books()
            
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "DAV Address Book Fetch Failed")
            frappe.msgprint(f"Failed to fetch address books: {e}")
    @frappe.whitelist()
    def discover_calendars(self):
        """Refresh calendar list from CalDAV provider"""
        from erpnext_dav_integration.caldav_sync.manager import CalDAVManager
        manager = CalDAVManager(self.name)
        try:
            # Discover calendar home
            if not self.default_calendar_url:
                calendar_home = manager.get_calendar_home_set()
                self.default_calendar_url = calendar_home
            # Discover calendars
            calendars = manager.discover_calendars()
            # Update available calendars
            self.available_calendars = []
            for cal in calendars:
                self.append('available_calendars', {
	                'calendar_url': cal['url'],
	                'display_name': cal['name'],
					'calendar_color': cal.get('color', '#000000'),
	                'sync_enabled': 1,
					'timezone': cal.get('timezone')
				})
            self.save(ignore_permissions=True)
            frappe.msgprint(
	            _("Discovered {0} calendars").format(len(calendars)),
	            alert=True
             )
        except Exception as e:
            frappe.msgprint(
	            _("Error discovering calendars: {0}").format(str(e)),
	            alert=True,
	            indicator='red'
            )
    def fetch_and_store_address_books(self):
        base_url = self.base_url.rstrip("/")
        username = self.username
        password = get_decrypted_password("DAV Account", self.name, "app_password")

        url = f"{base_url}/{self.default_addressbook_url.strip().lstrip('/')}/users/{username}/"

        headers = {"Depth": "1", "Content-Type": "application/xml"}

        body = """<?xml version="1.0" encoding="UTF-8"?>
        <d:propfind xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
            <d:prop>
                <d:displayname />
                <d:resourcetype />
            </d:prop>
        </d:propfind>
        """

        response = requests.request(
            "PROPFIND",
            url,
            data=body,
            headers=headers,
            auth=HTTPBasicAuth(username, password),
        )

        if response.status_code not in [207, 200]:
            frappe.throw(f"CardDAV Error: {response.status_code} - {response.text}")

        root = ET.fromstring(response.content)
        ns = {
            "d": "DAV:",
            "card": "urn:ietf:params:xml:ns:carddav"
        }
        server_books = {}
        for resp in root.findall("d:response", ns):
            href = resp.find("d:href", ns)
            displayname = resp.find(".//d:displayname", ns)
            resourcetype = resp.find(".//d:resourcetype", ns)
            if href is None or resourcetype is None:
                continue

            # # ✅ Proper CardDAV check
            # if resourcetype.find("card:addressbook", ns) is None:
            #     continue

            name = displayname.text if displayname is not None and displayname.text is not None else "Default"
            if not name:
                name = "Default"

            server_books[href.text] = name

        # 🔵 Step 2: Map existing rows
        existing_books = {row.url: row for row in self.dav_address_books}
        # 🔵 Step 3: Add or update
        for url, name in server_books.items():
            if url in existing_books:
                # ✅ Update name only, keep user flags
                existing_books[url].address_book_name = name
            else:
                frappe.msgprint(f"Found new address book: {name} url: {url}")
                # ➕ New address book
                self.append("dav_address_books", {
                    "address_book_name": name,
                    "url": url
                })

        # 🔵 Step 4: Remove deleted ones
        self.dav_address_books = [
            row for row in self.dav_address_books
            if row.url and row.address_book_name and row.url in server_books
        ]

        # 🔵 Step 5: Save to persist selections
        self.save(ignore_permissions=True)

    @frappe.whitelist()
    def update_address_book_list(self):
        self.fetch_and_store_address_books()
        self.save(ignore_permissions=True, ignore_version=True)

@frappe.whitelist()
def get_default_address_book(dav_account):
	if not dav_account:
		return []

	doc = frappe.get_doc("DAV Account", dav_account)
	for row in doc.dav_address_books:
		if row.is_default and row.address_book_name != "Default":
			return {"name": row.address_book_name, "value": row.url}
	index = 0
	for row in doc.dav_address_books:
		if row.address_book_name != "Default" and index == 0:
			index += 1
			return {"name": row.address_book_name, "value": row.url}

	return [{"name": row.address_book_name, "value": row.url} for row in doc.dav_address_books]

@frappe.whitelist()
def get_default_dav_account():
	dav = frappe.get_doc("DAV Account", {"default": 1, "enabled": 1}) or frappe.get_doc("DAV Account", {"default": 0, "enabled": 1}) or None
	if dav:
		return dav.name
	return None

from urllib.parse import urljoin


@frappe.whitelist()
def create_address_book(docname, address_book_name):
	doc = frappe.get_doc("DAV Account", docname)

	if not doc.base_url or not doc.username:
		frappe.throw("DAV credentials missing")

	username = doc.username
	password = get_decrypted_password("DAV Account", doc.name, "app_password")
	if not password:
		frappe.throw("DAV credentials missing")

	base_url = doc.base_url.rstrip("/") + "/"

	# 👉 CardDAV address book home path
	# Prefer the configured default_addressbook_url, fallback to common Nextcloud path
	addressbook_home = (doc.default_addressbook_url or "/remote.php/dav/addressbooks").strip("/")

	book_slug = address_book_name.strip().lower().replace(" ", "-")
	create_url = urljoin(base_url, f"{addressbook_home}/users/{username}/{book_slug}/")

	# Avoid duplicate local entries when name already exists
	existing_book = next(
		(
			x
			for x in doc.dav_address_books
			if x.address_book_name and x.address_book_name.strip().lower().replace(" ", "-") == book_slug
		),
		None,
	)
	if existing_book:
		frappe.msgprint(f"Address book '{address_book_name}' already exists locally")
		return True

	headers = {"Content-Type": "application/xml; charset=utf-8"}

	# 📌 CardDAV MKCOL XML body
	body = f"""<?xml version="1.0" encoding="UTF-8"?>
    <d:mkcol xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
        <d:set>
            <d:prop>
                <d:displayname>{address_book_name}</d:displayname>
                <d:resourcetype>
                    <d:collection/>
                    <card:addressbook/>
                </d:resourcetype>
            </d:prop>
        </d:set>
    </d:mkcol>
    """

	response = requests.request(
		"MKCOL",
		create_url,
		data=body.encode("utf-8"),
		headers=headers,
		auth=HTTPBasicAuth(username, password),
	)

	add_to_child_table = False

	if response.status_code == 201:
		add_to_child_table = True
	elif response.status_code == 405:
		# Already exists on server
		frappe.msgprint(f"Address book '{address_book_name}' already exists on server")

		existing_child = next(
			(x for x in doc.dav_address_books if x.url and x.url.rstrip("/") == create_url.rstrip("/")), None
		)

		if not existing_child:
			add_to_child_table = True
	else:
		frappe.throw(f"Failed to create address book: {response.status_code} - {response.text}")
	proper_url = (
		create_url.replace(base_url, "/").rstrip("/") if create_url.startswith(base_url) else create_url
	)
	# ✅ Add to child table after creation or existing server dir
	if add_to_child_table:
		doc.append(
			"dav_address_books", {"address_book_name": address_book_name, "url": proper_url, "is_default": 0}
		)
		doc.save(ignore_permissions=True)

	return True


@frappe.whitelist()
def get_address_book_url(doc, address_book_name):
	doc = frappe.get_doc("DAV Account", doc)
	for row in doc.dav_address_books:
		if row.address_book_name == address_book_name:
			return row.url
	return None


@frappe.whitelist()
def delete_address_book(docname, address_book_url):
	doc = frappe.get_doc("DAV Account", docname)
	url_to_delete = address_book_url

	if not url_to_delete:
		frappe.throw("Address book not found")

	username = doc.username
	password = get_decrypted_password("DAV Account", doc.name, "app_password")
	if not password:
		frappe.throw("DAV credentials missing")

	headers = {"Content-Type": "application/xml; charset=utf-8"}

	response = requests.request(
		"DELETE",
		urljoin(doc.base_url.rstrip("/") + "/", url_to_delete.lstrip("/")),
		headers=headers,
		auth=HTTPBasicAuth(username, password),
	)

	if response.status_code not in [200, 204]:
		frappe.throw(f"Failed to delete address book: {response.status_code} - {response.text}")

