# Copyright (c) 2026, CloudConverge and contributors
# For license information, please see license.txt

from xml.etree import ElementTree as ET

import frappe
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

	def fetch_and_store_address_books(self):

		self.dav_address_books = []

		base_url = self.base_url.rstrip("/")
		username = self.username
		password = get_decrypted_password("DAV Account", self.name, "app_password")

		# CardDAV principal URL (sometimes /principals/users/<user>/)
		url = f"{base_url}/{self.default_addressbook_url.strip().lstrip('/')}/users/{username}/"

		headers = {"Depth": "1", "Content-Type": "application/xml"}

		# PROPFIND body
		body = """<?xml version="1.0" encoding="UTF-8"?>
        <d:propfind xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
            <d:prop>
                <d:displayname />
                <d:resourcetype />
            </d:prop>
        </d:propfind>
        """

		response = requests.request(
			"PROPFIND", url, data=body, headers=headers, auth=HTTPBasicAuth(username, password)
		)

		if response.status_code not in [207, 200]:
			frappe.throw(f"CardDAV Error: {response.status_code} - {response.text}")

		root = ET.fromstring(response.content)

		ns = {"d": "DAV:"}

		for resp in root.findall("d:response", ns):
			href = resp.find("d:href", ns)
			displayname = resp.find(".//d:displayname", ns)

			if href is None:
				continue

			name = displayname.text if displayname is not None else href.text

			# Filter only addressbook collections (basic filtering)
			if "addressbook" in href.text.lower():
				if not name:
					name = "Default"
				self.append("dav_address_books", {"address_book_name": name, "url": href.text})

	@frappe.whitelist()
	def update_address_book_list(self):
		self.dav_address_books = []
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

