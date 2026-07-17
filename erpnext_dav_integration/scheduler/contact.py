import base64
import html
import re
import uuid
from datetime import datetime
from io import BytesIO

import frappe
import langcodes
import requests
import vobject
from defusedxml import ElementTree as ET
from frappe import _
from frappe.utils.password import get_decrypted_password
from requests.auth import HTTPBasicAuth

try:
	from PIL import Image

	HAS_PIL = True
except ImportError:
	HAS_PIL = False


def get_dav_accounts(user: str | None = None):
	filters = {"enabled": 1}

	if user:
		filters["user"] = user

	accounts = frappe.get_all(
		"DAV Account",
		filters=filters,
		fields=[
			"name",
			"base_url",
			"username",
			"app_password",
			"webdav_root_url",
			"default_calendar_url",
			"default_addressbook_url",
		],
	)

	if not accounts:
		frappe.throw(_("No active DAV Account found"))

	return accounts


def preprocess_vcard(vcard_string):
	# Split vCard string into lines
	lines = vcard_string.split("\n")
	# Remove the unwanted line
	lines = [line for line in lines if "_$!<HomePage>!$_" not in line]
	# Join the lines back to a string
	vcard_string = "\n".join(lines)

	return vcard_string


def _compress_image(image_data, target_size=40000, max_quality=95, min_quality=10):
	"""
	Compress image to target size using PIL.
	Reduces quality progressively if needed to meet target size.
	Returns compressed image bytes.
	"""
	if not HAS_PIL:
		return image_data

	try:
		# Open image from bytes
		img = Image.open(BytesIO(image_data))

		# Convert RGBA to RGB if needed (for JPEG compression)
		if img.mode in ("RGBA", "LA", "P"):
			rgb_img = Image.new("RGB", img.size, (255, 255, 255))
			rgb_img.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
			img = rgb_img

		# Try progressive quality reduction
		for quality in range(max_quality, min_quality - 1, -5):
			output = BytesIO()
			img.save(output, format="JPEG", quality=quality, optimize=True)
			compressed = output.getvalue()

			if len(compressed) <= target_size:
				return compressed

		# If still too large, reduce dimensions
		ratio = (target_size / len(compressed)) ** 0.5
		new_size = (int(img.width * ratio), int(img.height * ratio))

		if new_size[0] > 0 and new_size[1] > 0:
			img = img.resize(new_size, Image.Resampling.LANCZOS)
			output = BytesIO()
			img.save(output, format="JPEG", quality=min_quality, optimize=True)
			return output.getvalue()

		return image_data
	except Exception as e:
		frappe.log_error(f"Image compression failed: {e!s}", "DAV Sync Compression Error")
		return image_data


gender_map = {"M": "Male", "F": "Female", "O": "Other", "N": "None", "U": "Unknown"}


@frappe.whitelist()
def create_and_update_contacts_from_vcf(
	vcf_content: str,
	dav_account_name: str | None = None,
	address_book_url: str | None = None,
	address_book: str | None = None,
	vcard_url: str | None = None,
):
	contact_names = []

	try:
		# Parse VCF once
		vcards = list(vobject.readComponents(vcf_content))

		for vcard in vcards:
			try:
				# ------------------------
				# Extract Name
				# ------------------------
				first_name = ""
				last_name = ""
				middle_name = ""

				if hasattr(vcard, "n"):
					last_name = str(vcard.n.value.family or "")
					first_name = str(vcard.n.value.given or "")
					middle_name = str(vcard.n.value.additional or "")
				elif hasattr(vcard, "fn"):
					first_name = str(vcard.fn.value)
				else:
					first_name = "Contact"
					last_name = "No Name"

				full_name = (
					str(vcard.fn.value)
					if hasattr(vcard, "fn") and vcard.fn.value
					else " ".join(filter(None, [first_name, middle_name, last_name]))
				)

				# ------------------------
				# Organization
				# ------------------------
				org_value = ""
				dept_value = ""

				if hasattr(vcard, "org"):
					if isinstance(vcard.org.value, list):
						org_value = str(vcard.org.value[0]) if vcard.org.value else ""
						dept_value = str(vcard.org.value[1]) if len(vcard.org.value) > 1 else ""
					else:
						org_value = str(vcard.org.value)

				# ------------------------
				# Designation
				# ------------------------
				designation = str(vcard.title.value) if hasattr(vcard, "title") else ""

				# ------------------------
				# UID (IMPORTANT)
				# ------------------------
				uid = _extract_uid_from_vcard(vcard.serialize())

				# ------------------------
				# Find Existing Contact
				# ------------------------
				existing_contact = None
				# Priority 1: UID
				if uid:
					existing_contact = frappe.db.exists("Contact", {"custom_vcard_url": vcard_url})

				emails = vcard.contents.get("email", [])

				for email in emails:
					email_value = str(email.value)
					if frappe.db.exists(
						"Contact Email", {"email_id": email_value, "parent": ["!=", existing_contact]}
					):
						frappe.db.sql(
							"""
							UPDATE `tabContact Email`
							SET `custom_duplicate` = 'Yes'
							WHERE email_id = %s AND parent != %s
						""",
							(email_value, existing_contact),
						)
					else:
						frappe.db.sql(
							"""
							UPDATE `tabContact Email`
							SET `custom_duplicate` = 'No'
							WHERE email_id = %s
						""",
							(email_value,),
						)

				# ------------------------
				# Create or Update
				# ------------------------
				if existing_contact:
					contact_doc = frappe.get_doc("Contact", existing_contact)
					if not contact_doc.custom_enable_dav_sync:
						continue  # Skip if DAV sync is not enabled for this contact
				else:
					contact_doc = frappe.new_doc("Contact")
				if vcard.serialize() == contact_doc.custom_vcard:
					continue  # Skip if vCard content is unchanged
				# ------------------------
				# Set Fields
				# ------------------------

				socials = vcard.contents.get("x-socialprofile", [])
				contact_doc.custom_social_profiles = []
				for social in socials:
					contact_doc.append(
						"custom_social_profiles",
						{"type": social.params.get("TYPE", ["OTHER"])[0], "url": str(social.value)},
					)

				if hasattr(vcard, "note"):
					contact_doc.custom_notes = str(vcard.note.value)
				addresses = vcard.contents.get("adr", [])
				contact_doc.custom_addresses = []
				for adr in addresses:
					value = adr.value

					full_address = " ".join(
						filter(
							None,
							[
								value.box,
								value.extended,
								value.street,
								value.city,
								value.region,
								value.code,
								value.country,
							],
						)
					)
					contact_doc.append(
						"custom_addresses",
						{
							"type": adr.params.get("TYPE", ["OTHER"])[0],
							"street": value.street,
							"city": value.city,
							"region": value.region,
							"code": value.code,
							"country": value.country,
							"full_address": full_address,
							"po_box": value.box,
							"extended": value.extended,
						},
					)
				if hasattr(vcard, "url"):
					contact_doc.custom_url = str(vcard.url.value)
				if hasattr(vcard, "gender"):
					contact_doc.custom_gender_code = str(vcard.gender.value)
					contact_doc.gender = gender_map.get(str(vcard.gender.value))
				if hasattr(vcard, "url"):
					contact_doc.custom_website = str(vcard.url.value)
				managers = vcard.contents.get("x-managersname", [])
				if managers:
					contact_doc.custom_manager_name = str(managers[0].value)
				languages = vcard.contents.get("lang", [])
				contact_doc.custom_spoken_languages = (
					", ".join([langcodes.get(lang.value).display_name() for lang in languages])
					if languages
					else None
				)
				contact_doc.first_name = first_name
				contact_doc.last_name = last_name
				contact_doc.full_name = full_name
				contact_doc.middle_name = middle_name
				contact_doc.company_name = org_value
				contact_doc.department = dept_value
				contact_doc.designation = designation
				contact_doc.custom_last_sync = datetime.now()
				contact_doc.custom_dav_account = dav_account_name
				contact_doc.dav_addressbook = frappe.db.get_value(
					"DAV AddressBook", {"dav_account": dav_account_name, "dav_addressbook": address_book}
				)
				contact_doc.custom_dav_uid = uid
				contact_doc.custom_vcard = vcard.serialize()
				contact_doc.custom_sync_status = "Success"
				contact_doc.custom_vcard_url = vcard_url
				contact_doc.custom_dob = str(vcard.bday.value) if hasattr(vcard, "bday") else None
				contact_doc.custom_dav_address_book = address_book
				contact_doc.custom_dav_address_book_url = address_book_url

				# ------------------------
				# Reset child tables
				# ------------------------
				contact_doc.set("email_ids", [])
				contact_doc.set("phone_nos", [])

				# ------------------------
				# Email
				# ------------------------
				emails = vcard.contents.get("email", [])
				for email in emails:
					if not email.value:
						continue
					email_value = str(email.value)
					contact_doc.append(
						"email_ids",
						{
							"email_id": email_value,
							"is_primary": 1 if not contact_doc.get("email_ids") else 0,
							"custom_type": email.params.get("TYPE", ["HOME"])[0] if email.params else "HOME",
						},
					)

				# ------------------------
				# Phones
				# ------------------------
				if hasattr(vcard, "tel"):
					for tel in vcard.tel_list:
						raw_phone = str(tel.value) if tel.value else ""
						phone_number = re.sub(r"[^\d+]", "", raw_phone)
						if phone_number:
							contact_doc.append(
								"phone_nos",
								{
									"phone": phone_number,
									"custom_type": tel.params.get("TYPE", ["HOME"])[0]
									if tel.params
									else "HOME",
								},
							)
				if hasattr(vcard, "photo"):
					try:
						photo = vcard.photo

						photo_data = photo.value

						# Handle binary data
						if isinstance(photo_data, bytes):
							# Binary photo data - detect type and encode
							if photo_data.startswith(b"\x89PNG"):
								mime_type = "image/png"
							elif photo_data.startswith(b"\xff\xd8\xff"):
								mime_type = "image/jpeg"
							elif photo_data.startswith(b"GIF8"):
								mime_type = "image/gif"
							elif photo_data.startswith(b"RIFF") and b"WEBP" in photo_data[:20]:
								mime_type = "image/webp"
							else:
								# Fall back to TYPE param if available
								mime_type = (
									photo.params.get("TYPE", ["image/png"])[0]
									if hasattr(photo, "params")
									else "image/png"
								)

							# Compress if too large
							compressed_data = photo_data
							if len(photo_data) > 50000 and HAS_PIL:
								compressed_data = _compress_image(photo_data, target_size=40000)

							# Check final size before encoding (max 40KB binary → ~53KB base64)
							max_binary_size = 40000
							if len(compressed_data) <= max_binary_size:
								base64_data = base64.b64encode(compressed_data).decode("utf-8")
								contact_doc.image = f"data:{mime_type};base64,{base64_data}"

						# Handle string data (already base64 or data URL)
						elif isinstance(photo_data, str):
							# Check string size (max 60KB to stay safe)
							max_string_size = 60000
							if len(photo_data) <= max_string_size:
								if photo_data.startswith("data:"):
									# Already a data URL
									contact_doc.image = photo_data
								else:
									# Assume it's base64 encoded string
									mime_type = (
										photo.params.get("TYPE", ["image/png"])[0]
										if hasattr(photo, "params")
										else "image/png"
									)
									contact_doc.image = f"data:{mime_type};base64,{photo_data}"
					except Exception as photo_error:
						frappe.log_error(
							f"Error processing photo for {full_name}: {photo_error!s}",
							"DAV Sync Photo Error",
						)
				# ------------------------
				# Save
				# ------------------------
				contact_doc.save(ignore_permissions=True)
				contact_names.append(contact_doc.name)

			except Exception:
				frappe.log_error(message=frappe.get_traceback(), title=f"VCF Contact Error: {full_name}")
				continue

		frappe.db.commit()
		return contact_names

	except Exception:
		frappe.log_error(message=frappe.get_traceback(), title="VCF Import Error")
		return []


@frappe.whitelist()
def synchronize_carddav_contacts():
	"""Synchronize contacts from CardDAV server to Frappe."""
	frappe.flags.in_scheduled_job = True
	dav = frappe.get_doc("DAV Account", {"enabled": 1, "default": 1})
	if not dav:
		frappe.throw(_("No active default DAV Account found for synchronization."))
	else:
		base = (dav.base_url or "").strip().rstrip("/")

		username = dav.username
		password = get_decrypted_password("DAV Account", dav.name, "app_password")

		# Fetch all vCards from CardDAV server
		all_vcard_entries = fetch_vcards_from_carddav(base, username, password, dav.name)

		for entry in all_vcard_entries:
			vcard_string = entry["vcard"]
			# Preprocess vCard string
			vcard_string = preprocess_vcard(vcard_string)
			vcard = vobject.readOne(vcard_string)
			uid = vcard.uid.value if hasattr(vcard, "uid") else None
			if not uid:
				continue  # If no UID, skip to the next vCard

			create_and_update_contacts_from_vcf(
				vcard_string,
				dav_account_name=dav.name,
				address_book_url=entry["address_book_url"],
				address_book=entry["address_book"],
				vcard_url=entry["vcard_url"],
			)
	# After processing all contacts, print a duplication report
	duplicate_emails = frappe.db.sql(
		"""
		SELECT email_id, COUNT(*)
		FROM `tabContact Email`
		WHERE custom_duplicate = 'Yes'
		GROUP BY email_id
		HAVING COUNT(*) > 1
	""",
		as_dict=True,
	)
	if duplicate_emails:
		report = '<table class="table table-bordered">'
		report += "<thead><tr><th>Email</th><th>Count</th></tr></thead>"
		report += "<tbody>"
		for row in duplicate_emails:
			report += f"<tr><td>{frappe.utils.escape_html(row.email_id)}</td><td>{row['COUNT(*)']}</td></tr>"
		report += "</tbody></table>"
		frappe.msgprint(report, _("Duplicate Emails Found"), allow_dangerous_html=True)
	frappe.db.commit()


def fetch_vcards_from_carddav(base_url, username, password, dav_account_name=None):
	headers = {
		"Depth": "1",
		"Content-Type": "application/xml",
	}

	body = """<?xml version="1.0" encoding="UTF-8"?>
		<d:propfind xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
			<d:prop>
				<d:displayname />
				<d:resourcetype />
			</d:prop>
		</d:propfind>
		"""

	# Step 1: Get addressbooks
	discovery_url = f"{base_url}/remote.php/dav/addressbooks/users/{username}/"

	response = requests.request(
		"PROPFIND",
		discovery_url,
		data=body,
		headers=headers,
		auth=HTTPBasicAuth(username, password),
	)

	if response.status_code != 207:
		raise Exception(f"Failed to fetch addressbooks: {response.status_code}")

	addressbooks = parse_addressbooks(response.text)
	addressbook_data = frappe.db.get_all(
		"DAV AddressBook",
		filters={"dav_account": dav_account_name},
		fields=["dav_addressbook", "dav_addressbook_url"],
	)

	all_vcard_entries = []
	xml_body = """<?xml version="1.0" encoding="UTF-8"?>
        <d:propfind xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
            <d:prop>
                <d:getetag />
                <card:address-data />
            </d:prop>
        </d:propfind>
    """
	# Step 2: Loop each addressbook
	for ab in addressbooks:
		ab_url = base_url.rstrip("/") + ab.get("href", "")
		if ab.get("href", "") not in [entry["dav_addressbook_url"] for entry in addressbook_data]:
			continue  # Skip if addressbook URL is not in the DAV AddressBook table
		res = requests.request(
			"PROPFIND",
			ab_url,
			headers=headers,
			data=xml_body,
			auth=HTTPBasicAuth(username, password),
		)

		if res.status_code != 207:
			continue

		vcards = parse_vcards_with_href(res.text, base_url)

		for vcard in vcards:
			all_vcard_entries.append(
				{
					"vcard": vcard["vcard"],
					"address_book": ab.get("displayname", "Unnamed Address Book"),
					"address_book_url": ab_url,
					"vcard_url": vcard["href"],
				}
			)

	return all_vcard_entries


def parse_vcards_with_href(xml_text, base_url):
	ns = {"d": "DAV:", "card": "urn:ietf:params:xml:ns:carddav"}

	xml_text = re.sub(r"[^\x09\x0A\x0D\x20-\x7F\u00A0-\uD7FF\uE000-\uFFFD]", "", xml_text)
	results = []
	# ✅ DO NOT html.unescape here
	root = ET.fromstring(xml_text)
	# ✅ Clean bad chars

	for response in root.findall("d:response", ns):
		href_el = response.find("d:href", ns)
		status_el = response.find(".//d:status", ns)

		# ✅ skip non-200
		if status_el is None or "200 OK" not in status_el.text:
			continue

		address_data_el = response.find(".//card:address-data", ns)

		if href_el is not None and address_data_el is not None:
			href = href_el.text

			# ✅ unescape ONLY here
			raw_vcard = address_data_el.text or ""
			vcard = html.unescape(raw_vcard)

			results.append({"href": base_url.rstrip("/") + href, "vcard": vcard})

	return results


def parse_addressbooks(xml_text):
	ns = {"d": "DAV:", "card": "urn:ietf:params:xml:ns:carddav"}

	root = ET.fromstring(xml_text)
	addressbooks = []

	for response in root.findall("d:response", ns):
		href = response.find("d:href", ns)
		resourcetype = response.find(".//d:resourcetype", ns)
		if resourcetype is not None and resourcetype.find("card:addressbook", ns) is not None:
			addressbooks.append(
				{
					"href": href.text,
					"displayname": response.find(".//d:displayname", ns).text
					if response.find(".//d:displayname", ns) is not None
					else "Unnamed Address Book",
				}
			)

	return addressbooks


def _extract_uid_from_vcard(vcard_text):
	"""
	Extract UID from vCard text.
	If UID is missing, generate a stable fallback UUID.
	"""
	try:
		match = re.search(r"UID:(.+)", vcard_text)
		if match:
			uid = match.group(1).strip()
			return uid

		# Fallback: generate deterministic UID from content
		return str(uuid.uuid5(uuid.NAMESPACE_DNS, vcard_text))

	except Exception:
		return str(uuid.uuid4())


@frappe.whitelist()
def schedule_synchronization():
	frappe.flags.in_scheduled_job = True
	frappe.enqueue(
		synchronize_carddav_contacts,
		queue="long",
		timeout=30000,
		is_async=True,
		job_name="Synchronize CardDAV contacts",
	)


def schedule_deletion_of_trashed_contacts():
	frappe.flags.in_scheduled_job = True
	frappe.enqueue(
		deletion_of_trashed_contacts,
		queue="long",
		timeout=30000,
		is_async=True,
		job_name="Deletion of trashed CardDAV contacts",
	)


@frappe.whitelist()
def deletion_of_trashed_contacts():
	frappe.flags.in_scheduled_job = True
	enabled_sync_contacts = frappe.get_all(
		"Contact",
		filters={"custom_enable_dav_sync": 1, "custom_vcard_url": ["is", "set"]},
		fields=["name", "custom_vcard_url", "custom_dav_account"],
	)
	for contact in enabled_sync_contacts:
		dav = frappe.get_doc("DAV Account", {"name": contact.custom_dav_account})
		res = requests.get(
			contact.custom_vcard_url,
			auth=HTTPBasicAuth(dav.username, get_decrypted_password("DAV Account", dav.name, "app_password")),
		)
		if res.status_code == 404:
			try:
				frappe.delete_doc("Contact", contact.name)
			except Exception:
				pass


def update_all_dav_accounts_address_book_list():
	dav_accounts = frappe.get_all("DAV Account", filters={"enabled": True})
	for dav in dav_accounts:
		try:
			dav_doc = frappe.get_doc("DAV Account", dav.name)
			dav_doc.update_address_book_list()
		except Exception:
			frappe.log_error(
				frappe.get_traceback(), f"Failed to update address book list for DAV Account: {dav.name}"
			)
