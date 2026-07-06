import frappe

from erpnext_dav_integration.webdav_sync.manager import FolderProvisioner


def on_employee_after_insert(doc, method=None):
	"""
	Auto-provision Nextcloud folders whenever a new Employee is saved.
	Hook: Employee → after_insert
	"""
	account_name = frappe.db.get_value(
		"DAV Account", {"default": 1, "enabled": 1}, "name"
	) or frappe.db.get_value("DAV Account", {"enabled": 1}, "name")
	if not account_name:
		return

	try:
		provisioner = FolderProvisioner(account_name)
		provisioner.provision_employee_folders(doc)
	except Exception as exc:
		frappe.log_error(f"on_employee_after_insert – folder provisioning failed: {exc}")
