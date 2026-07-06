import uuid

import frappe
import langcodes
import requests
from frappe.utils.password import get_decrypted_password
from requests.auth import HTTPBasicAuth


def sync_contact_to_carddav(doc, method):
	try:
		if not doc.custom_dav_account:
			return
		else:
			dav = frappe.get_doc("DAV Account", doc.custom_dav_account)
		if method == "before_insert" and not doc.custom_enable_dav_sync:
			return
		elif method == "before_insert" and doc.custom_enable_dav_sync:
			doc.dav_account = dav.name

		if frappe.flags.in_scheduled_job:
			return

		if not doc.custom_enable_dav_sync or not doc.custom_dav_address_book_url:
			return

		if not dav.base_url or not dav.username:
			return

		password = get_decrypted_password("DAV Account", dav.name, "app_password")
		if not password:
			return

		vcard = build_vcard(doc)
		try:
			if doc.custom_vcard_url:
				update_contact(dav, doc, vcard, password)
			else:
				create_contact(dav, doc, vcard, password)
		except Exception as e:
			frappe.log_error(f"Error syncing contact {doc.name} to CardDAV: {e!s}", "DAV Sync Error")
			doc.custom_sync_status = "Failed"
			doc.custom_last_sync = frappe.utils.now()
			raise
		for email in doc.get("email_ids", []) or []:
			if email.get("email_id"):
				if frappe.db.exists(
					"Contact Email", {"email_id": email.get("email_id"), "parent": ["!=", doc.name]}
				):
					frappe.db.sql(
						"""
						UPDATE `tabContact Email`
						SET `custom_duplicate` = 'Yes'
						WHERE email_id = %s
					""",
						(email.get("email_id"),),
					)
					email.custom_duplicate = "Yes"
	except Exception:
		frappe.log_error(frappe.get_traceback(), "DAV Sync Error")
		raise


def validate_contact(doc, method):
	if doc.is_new():
		return

	old_doc = frappe.get_doc(doc.doctype, doc.name)
	if not old_doc.custom_vcard_url:
		return
	if not old_doc.custom_dav_account:
		return
	if frappe.flags.in_scheduled_job:
		return
	if (
		old_doc.custom_dav_account != doc.custom_dav_account
		or old_doc.custom_dav_address_book_url != doc.custom_dav_address_book_url
	):
		delete_contact_from_carddav(old_doc, method)


def update_contact(dav, doc, vcard, password):
	contact_url = doc.custom_vcard_url

	res = requests.put(
		contact_url,
		data=vcard,
		headers={"Content-Type": "text/vcard"},
		auth=HTTPBasicAuth(dav.username, password),
	)

	if res.status_code not in (201, 204):
		frappe.log_error(f"Update failed: {res.status_code} - {res.text}", "DAV Sync Error")
	doc.custom_sync_status = "Success"
	doc.custom_last_sync = frappe.utils.now()


def create_contact(dav, doc, vcard, password):
	base = (
		doc.custom_dav_address_book_url.rstrip("/")
		if doc.custom_dav_address_book_url.startswith("http")
		else dav.base_url.rstrip("/") + doc.custom_dav_address_book_url.rstrip("/")
	)

	contact_url = f"{base}/{doc.custom_dav_uid or str(uuid.uuid4())}.vcf"
	res = requests.put(
		contact_url,
		data=vcard,
		headers={"Content-Type": "text/vcard"},
		auth=HTTPBasicAuth(dav.username, password),
	)

	if res.status_code not in (201, 204):
		frappe.log_error(f"Create failed: {res.status_code} - {res.text}", "DAV Sync Error")
	doc.custom_sync_status = "Success"
	doc.custom_last_sync = frappe.utils.now()
	doc.custom_vcard_url = contact_url


def _get_base_url(dav, doc):
	if doc.custom_dav_address_book_url.startswith("http"):
		return doc.custom_dav_address_book_url.rstrip("/")
	return dav.base_url.rstrip("/") + doc.custom_dav_address_book_url.rstrip("/")


def esc(val):
	"""Escape vCard special characters"""
	if not val:
		return ""
	return (
		str(val)
		.replace("\\", "\\\\")
		.replace("\n", "\\n")
		.replace("\r", "")
		.replace(";", "\\;")
		.replace(",", "\\,")
	)


def build_vcard(doc):
	first = doc.first_name or ""
	middle = doc.middle_name or ""
	last = doc.last_name or ""

	full_name = " ".join(filter(None, [first, middle, last])).strip()

	def nn(val):
		return str(val or "").replace("\n", " ").replace("\r", "").strip()

	# -----------------------------
	# UID (Required for DAV)
	# -----------------------------
	uid = doc.custom_dav_uid or str(uuid.uuid4())

	vcard = [
		"BEGIN:VCARD",
		"VERSION:3.0",
		f"UID:{uid}",
		f"FN:{esc(full_name)}",
		f"N:{esc(last)};{esc(first)};{esc(middle)};;",
	]

	# -----------------------------
	# ORG + TITLE
	# -----------------------------
	if doc.company_name:
		vcard.append(f"ORG:{esc(doc.company_name)}")

	if getattr(doc, "designation", None):
		vcard.append(f"TITLE:{esc(doc.designation)}")

	# -----------------------------
	# EMAIL
	# -----------------------------
	email_rows = doc.get("email_ids") or []
	if not email_rows and doc.email_id:
		email_rows = [{"email_id": doc.email_id, "custom_type": "HOME"}]

	for row in email_rows:
		if row.get("email_id"):
			vcard.append(
				f"EMAIL;TYPE={esc((row.get('custom_type') or 'HOME').upper())},INTERNET:{esc(row.get('email_id'))}"
			)

	# -----------------------------
	# PHONE
	# -----------------------------
	for row in doc.get("phone_nos") or []:
		if row.get("phone"):
			vcard.append(
				f"TEL;TYPE={esc((row.get('custom_type') or 'CELL').upper())}:{esc(row.get('phone'))}"
			)

	# -----------------------------
	# ADDRESS (STRICT FORMAT)
	# ADR:POBOX;EXT;STREET;CITY;REGION;POSTCODE;COUNTRY
	# -----------------------------
	for addr in doc.get("custom_addresses") or []:
		vcard.append(
			"ADR;TYPE={}:{};{};{};{};{};{};{}".format(
				esc((addr.get("type") or "HOME").upper()),
				esc(addr.get("po_box")),
				esc(addr.get("extended")),
				esc(addr.get("street")),
				esc(addr.get("city")),
				esc(addr.get("region")),
				esc(addr.get("code")),
				esc(addr.get("country")),
			)
		)

	# -----------------------------
	# URL (Safe)
	# -----------------------------
	if getattr(doc, "custom_website", None):
		vcard.append(f"URL:{esc(doc.custom_website)}")
	# ------------------------
	# Gender (RFC6350 style)
	# ------------------------
	if doc.custom_gender_code:
		vcard.append(f"GENDER:{nn(doc.custom_gender_code)}")
	# ------------------------
	# Languages
	# ------------------------
	if doc.custom_spoken_languages:
		for lang in doc.custom_spoken_languages.split(","):
			lang = lang.strip()
			try:
				code = langcodes.find(lang).language  # get ISO 639-1 code
				if code:
					vcard.append(f"LANG:{code}")
			except LookupError:
				pass  # skip invalid languages
	# -----------------------------
	# NOTE (Safe, escaped)
	# -----------------------------
	if getattr(doc, "custom_notes", None):
		vcard.append(f"NOTE:{esc(doc.custom_notes)}")

	if doc.custom_dob:
		vcard.append(f"BDAY:{nn(doc.custom_dob)}")
	# ------------------------
	# Social Profiles
	# ------------------------
	for social in doc.get("custom_social_profiles", []):
		vcard.append("X-SOCIALPROFILE;TYPE={}:{}".format(social.get("type", "OTHER"), nn(social.get("url"))))
	if doc.custom_manager_name:
		vcard.append(f"X-MANAGERSNAME:{nn(doc.custom_manager_name)}")

	# -----------------------------
	# END
	# -----------------------------
	vcard.append("END:VCARD")
	# CRLF REQUIRED
	updated_vcard = "\r\n".join(vcard) + "\r\n"

	# Save
	doc.custom_vcard = updated_vcard
	doc.custom_dav_uid = uid

	return updated_vcard


def delete_contact_from_carddav(doc, method):
	try:
		if not doc.custom_vcard_url:
			return
		if not doc.custom_dav_account:
			return

		dav = frappe.get_doc("DAV Account", doc.custom_dav_account)
		password = get_decrypted_password("DAV Account", dav.name, "app_password")

		res = requests.delete(doc.custom_vcard_url, auth=HTTPBasicAuth(dav.username, password))

		if res.status_code not in (200, 204):
			frappe.log_error(
				f"Failed to delete contact from CardDAV: {res.status_code} - {res.text}",
				"DAV Sync Delete Error",
			)

	except Exception:
		frappe.log_error(frappe.get_traceback(), "DAV Sync Delete Exception")
