import frappe

from erpnext_dav_integration.webdav_sync.manager import FolderProvisioner


def on_project_after_insert(doc, method=None):
	"""
	Auto-provision Nextcloud project folder whenever a new Project is saved.
	Hook: Project → after_insert
	"""
	account_name = frappe.db.get_value(
		"DAV Account", {"default": 1, "enabled": 1}, "name"
	) or frappe.db.get_value("DAV Account", {"enabled": 1}, "name")
	if not account_name:
		return

	try:
		provisioner = FolderProvisioner(account_name)
		provisioner.provision_project_folders(doc)
	except Exception as exc:
		frappe.log_error(f"on_project_after_insert – folder provisioning failed: {exc}")
