/* global erpnext_dav_integration */

frappe.ui.form.on("Contract", {
	refresh(frm) {
		if (frm.is_new()) {
			frm.set_value("contract_status", "Not Imported");
			frm.set_df_property("select_contract_file", "hidden", true);
		} else {
			frm.set_df_property("select_contract_file", "hidden", false);
		}
	},
	select_contract_file(frm) {
		_open_file_picker(frm);
	},
	open_contract_file(frm) {
		_open_in_nextcloud(frm);
	},
});

function _open_file_picker(frm) {
	// Fetch base_url first (one lightweight call) so the Files-app URL can be
	// built client-side without a second round-trip after folder selection.
	frappe.call({
		method: "erpnext_dav_integration.webdav_sync.webdav_email_api.get_root_folder",
		args: { dav_account: null },
		callback: (r) => {
			if (!r.message) {
				frappe.msgprint({
					title: __("Cloud storage unavailable"),
					message: __("No WebDAV account is linked to your profile or this contract."),
					indicator: "red",
				});
				return;
			}
			_launch_browser(frm, r.message);
		},
	});
}

function _launch_browser(frm, root_info) {
	// root_info = { path, dav_account, username, base_url }
	const { dav_account, base_url } = root_info;
	const browser = new erpnext_dav_integration.webdav.FileBrowser({
		doctype: frm.doctype,
		docname: frm.docname,
		dav_account: dav_account,
		folder_pick: false,
		file_pick: true,
		on_attach(item) {
			if (!item.is_folder) {
				_handle_file_selected(frm, item, base_url, dav_account);
			}
		},
	});
	browser.show();
}

function _handle_file_selected(frm, item, base_url, dav_account) {
	const dav_path = item.href;
	const files_url = _dav_path_to_files_url(dav_path, base_url, item.id);

	frappe.db
		.set_value(frm.doctype, frm.docname, {
			nextcloud_internal_link: files_url,
			contract_status: "Imported",
		})
		.then(() => {
			frm.reload_doc();
			frappe.show_alert({
				message: __("Nextcloud file linked to Contract"),
				indicator: "green",
			});
		});
}

function _open_in_nextcloud(frm) {
	const file_url = frm.doc.nextcloud_internal_link;
	if (!file_url) {
		// If file_url is not provided, it means the user clicked "Open Contract File"
		// without selecting a file first. Show an error message.
		frappe.msgprint({
			title: __("No file selected"),
			message: __("Please select a contract file first."),
			indicator: "red",
		});
		return;
	}
	// Open the file URL in Nextcloud
	window.open(file_url, "_blank");
}

function _dav_path_to_files_url(dav_path, base_url, file_id) {
	const clean_base = (base_url || "").replace(/\/+$/, "");
	if (file_id) {
		return `${clean_base}/f/${file_id}`;
	}

	const match = dav_path.match(/\/remote\.php\/dav\/files\/[^/]+(.*)/);
	let folder_path = match ? match[1] : "/";
	folder_path = decodeURIComponent(folder_path);
	folder_path = folder_path.replace(/\/$/, "") || "/";
	return `${clean_base}/apps/files/?dir=${encodeURIComponent(folder_path)}`;
}
