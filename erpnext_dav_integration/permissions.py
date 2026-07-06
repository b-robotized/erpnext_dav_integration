# app/permissions.py

import frappe


def _get_user_address_book_pairs(user: str) -> list[tuple[str, str]]:
	"""
	Parses the user's roles and returns a list of (account_name, address_book_name)
	tuples for every 'DAV Address Book: <account> - <book>' role they hold.
	"""
	prefix = "DAV Address Book: "
	pairs = []

	for role in frappe.get_roles(user):
		if not role.startswith(prefix):
			continue
		# Strip prefix → "My Account - My Book"
		remainder = role[len(prefix) :]
		if " - " not in remainder:
			continue
		account, book = remainder.split(" - ", 1)
		pairs.append((account.strip(), book.strip()))

	return pairs


# ── permission_query_conditions ──────────────────────────────────────────────


def get_contact_permission_query(user: str | None = None) -> str:
	if not user:
		user = frappe.session.user

	if "System Manager" in frappe.get_roles(user):
		return ""

	pairs = _get_user_address_book_pairs(user)

	if not pairs:
		# No DAV roles → no access to any DAV-linked contact
		return "1=0"

	conditions = " OR ".join(
		f"""(
            `tabContact`.custom_dav_account      = {frappe.db.escape(account)}
            AND `tabContact`.custom_dav_address_book = {frappe.db.escape(book)}
        )"""
		for account, book in pairs
	)

	return f"({conditions})"


# ── has_permission ────────────────────────────────────────────────────────────


def contact_has_permission(doc, ptype: str = "read", user: str | None = None) -> bool:
	if not user:
		user = frappe.session.user

	if "System Manager" in frappe.get_roles(user):
		return True

	pairs = _get_user_address_book_pairs(user)

	return (doc.custom_dav_account, doc.custom_dav_address_book) in pairs


def get_event_permission_query(user: str | None = None) -> str:
	return ""


def event_has_permission(doc, ptype: str = "read", user: str | None = None) -> bool:
	return True
