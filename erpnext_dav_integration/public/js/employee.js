/* global erpnext_dav_integration */

frappe.ui.form.on("Employee", {
	select_employee_private_folder(frm) {
		_open_folder_picker(frm, "private");
	},
	open_employee_private_folder(frm) {
		_open_in_nextcloud(frm, "private");
	},
	select_company_shared_folder(frm) {
		_open_folder_picker(frm, "shared");
	},
	open_company_shared_folder(frm) {
		_open_in_nextcloud(frm, "shared");
	},
});

// ─────────────────────────────────────────────────────────────────────────────
// Open the WebDAV FileBrowser in folder_pick mode
// ─────────────────────────────────────────────────────────────────────────────

function _open_folder_picker(frm, folder_type) {
	// Fetch base_url first (one lightweight call) so the Files-app URL can be
	// built client-side without a second round-trip after folder selection.
	frappe.call({
		method: "erpnext_dav_integration.webdav_sync.webdav_email_api.get_root_folder",
		args: { dav_account: null },
		callback: (r) => {
			if (!r.message) {
				frappe.msgprint({
					title: __("Cloud storage unavailable"),
					message: __("No WebDAV account is linked to your profile or this Employee."),
					indicator: "red",
				});
				return;
			}
			_launch_browser(frm, r.message, folder_type);
		},
	});
}

function _launch_browser(frm, root_info, folder_type) {
	// root_info = { path, dav_account, username, base_url }
	const { dav_account, base_url } = root_info;

	const browser = new erpnext_dav_integration.webdav.FileBrowser({
		doctype: frm.doctype,
		docname: frm.docname,
		dav_account: dav_account,
		folder_pick: true,
		on_attach(item) {
			if (item.is_folder) {
				_handle_folder_selected(frm, item, base_url, dav_account, folder_type);
			}
		},
		on_link: () => {}, // not used here
	});

	browser.show();

	// Customise labels after show() so the DOM is live
	_customise_browser_labels(browser);
}

// Relabel the FileBrowser dialog for folder-selection context
function _customise_browser_labels(browser) {
	const $w = browser.dialog.$wrapper;

	// Dialog title
	$w.closest(".modal").find(".modal-title").text(__("Select a Nextcloud folder"));

	// "Attach file" → "Select this folder"
	$w.find(".wdav-btn-attach")
		.html(
			`
            <i class="ti ti-folder-check"
               style="font-size:14px;vertical-align:-2px"
               aria-hidden="true"></i>
            ${__("Select this folder")}`
		)
		.attr("title", __("Save this folder to the Employee"));

	// Hide "Insert share link" (not relevant here)
	$w.find(".wdav-btn-link").hide();

	// Update hint text in the selection bar
	$w.find(".wdav-sel-bar span").text(
		__("Click a folder to select it \u00b7 Double-click to open it")
	);
}

// ─────────────────────────────────────────────────────────────────────────────
// Handle a confirmed folder selection
// ─────────────────────────────────────────────────────────────────────────────

function _handle_folder_selected(frm, item, base_url, dav_account, folder_type) {
	// item.href is the server-relative DAV path:
	//   /remote.php/dav/files/{user}/Employees/Acme Corp/
	const dav_path = item.href;
	const files_url = _dav_path_to_files_url(dav_path, base_url, item.id);
	if (folder_type == "private") {
		// Persist to the Employee doc immediately (no extra confirmation needed)
		frappe.db
			.set_value("Employee", frm.docname, {
				employee_private_folder_internal_link: files_url,
			})
			.then(() => {
				frm.reload_doc();
				frappe.show_alert({
					message: __("Nextcloud folder linked to Employee"),
					indicator: "green",
				});
			});
	} else if (folder_type == "shared") {
		// Persist to the Employee doc immediately (no extra confirmation needed)
		frappe.db
			.set_value("Employee", frm.docname, {
				company_shared_folder_internal_link: files_url,
			})
			.then(() => {
				frm.reload_doc();
				frappe.show_alert({
					message: __("Nextcloud folder linked to Employee"),
					indicator: "green",
				});
			});
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Open stored folder in Nextcloud Files web app
// ─────────────────────────────────────────────────────────────────────────────

function _open_in_nextcloud(frm, folder_type) {
	if (folder_type == "private") {
		let target = frm.doc.employee_private_folder_internal_link;
		window.open(target, "_blank", "noopener,noreferrer");
	} else if (folder_type == "shared") {
		let target = frm.doc.company_shared_folder_internal_link;
		window.open(target, "_blank", "noopener,noreferrer");
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Utility: convert a DAV path to a Nextcloud Files-app URL
//
//   DAV path:  /remote.php/dav/files/{user}/Employees/Acme Corp/
//   Files URL: {base_url}/f/{file_id}
//
// If a file_id is available, use direct /f/{file_id}; otherwise fall back to
// the standard Files app folder browser URL.
// ─────────────────────────────────────────────────────────────────────────────

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
