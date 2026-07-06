/**
 * project_webdav.js
 * =================
 * Two custom buttons on the Project form:
 *
 *   [Nextcloud ▾]
 *     ├─ Select Nextcloud Folder   → opens WebDAV file browser in folder-pick
 *     │                              mode; saves chosen DAV path + Files-app
 *     │                              URL to the Project doc.
 *     └─ Open Nextcloud Folder     → opens the stored folder in the Nextcloud
 *                                    Files web app (only shown when a folder
 *                                    has already been selected).
 *
 * Fields written on Project (custom, created by webdav_erpnext_setup.py):
 *   technical_folder_url       – server-relative DAV path
 *                             e.g. /remote.php/dav/files/alice/Projects/Acme/
 *   technical_folder_internal_link    – Nextcloud Files app URL
 *                             e.g. https://cloud.example.com/apps/files/?dir=/Projects/Acme
 *   webdav_account          – DAV Account docname
 *
 * DAV-path → Files-app URL conversion (pure client-side, no extra API call):
 *   /remote.php/dav/files/{user}{folder_path}
 *   → {base_url}/apps/files/?dir={folder_path}
 */

/* global erpnext_dav_integration */

frappe.ui.form.on("Project", {
	refresh(frm) {
		if (frm.is_new()) {
			// hide both buttons for new records until
			// a DAV account is selected and saved
			frm.set_df_property("select_technical_folder", "hidden", true);
			frm.set_df_property("open_technical_folder", "hidden", true);
			frm.set_df_property("select_organizational_folder", "hidden", true);
			frm.set_df_property("open_organizational_folder", "hidden", true);
		}
	},
	select_technical_folder(frm) {
		_open_folder_picker(frm, "technical");
	},
	open_technical_folder(frm) {
		_open_in_nextcloud(frm, "technical");
	},
	select_organizational_folder(frm) {
		_open_folder_picker(frm, "organizational");
	},
	open_organizational_folder(frm) {
		_open_in_nextcloud(frm, "organizational");
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
		args: { dav_account: frm.doc.webdav_account || null },
		callback: (r) => {
			if (!r.message) {
				frappe.msgprint({
					title: __("Cloud storage unavailable"),
					message: __("No WebDAV account is linked to your profile or this project."),
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
		.attr("title", __("Save this folder to the Project"));

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
	//   /remote.php/dav/files/{user}/Projects/Acme Corp/
	const dav_path = item.href;
	const files_url = _dav_path_to_files_url(dav_path, base_url, item.id);
	if (folder_type == "technical") {
		// Persist to the Project doc immediately (no extra confirmation needed)
		frappe.db
			.set_value("Project", frm.docname, {
				technical_folder_url: dav_path,
				technical_folder_internal_link: files_url,
				webdav_account: dav_account,
			})
			.then(() => {
				frm.reload_doc();
				frappe.show_alert({
					message: __("Nextcloud folder linked to project"),
					indicator: "green",
				});
			});
	} else if (folder_type == "organizational") {
		// Persist to the Project doc immediately (no extra confirmation needed)
		frappe.db
			.set_value("Project", frm.docname, {
				organizational_folder_url: dav_path,
				organizational_folder_internal_link: files_url,
				webdav_account: dav_account,
			})
			.then(() => {
				frm.reload_doc();
				frappe.show_alert({
					message: __("Nextcloud folder linked to project"),
					indicator: "green",
				});
			});
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Open stored folder in Nextcloud Files web app
// ─────────────────────────────────────────────────────────────────────────────

function _open_in_nextcloud(frm, folder_type) {
	if (folder_type == "technical") {
		// Prefer the pre-built Files app URL if available
		let target = frm.doc.technical_folder_internal_link;

		// Fallback: rebuild from the DAV path on the fly if only the DAV path
		// was stored (e.g. records created before technical_folder_internal_link existed)
		if (!target && frm.doc.technical_folder_url) {
			// We need base_url; fetch it, then open
			frappe.call({
				method: "erpnext_dav_integration.webdav_sync.webdav_email_api.get_root_folder",
				args: { dav_account: frm.doc.webdav_account || null },
				callback: (r) => {
					if (r.message?.base_url) {
						const url = _dav_path_to_files_url(
							frm.doc.technical_folder_url,
							r.message.base_url
						);
						window.open(url, "_blank", "noopener,noreferrer");
					} else {
						frappe.msgprint({
							title: __("Cannot open folder"),
							message: __("No base URL found for the linked DAV account."),
							indicator: "red",
						});
					}
				},
			});
			return;
		}

		if (!target) {
			frappe.msgprint({
				title: __("No folder selected"),
				message: __(`Use "Select Nextcloud Folder" first.`),
				indicator: "orange",
			});
			return;
		}

		window.open(target, "_blank", "noopener,noreferrer");
	} else if (folder_type == "organizational") {
		// Prefer the pre-built Files app URL if available
		let target = frm.doc.organizational_folder_internal_link;

		// Fallback: rebuild from the DAV path on the fly if only the DAV path
		// was stored (e.g. records created before organizational_folder_internal_link existed)
		if (!target && frm.doc.organizational_folder_url) {
			// We need base_url; fetch it, then open
			frappe.call({
				method: "erpnext_dav_integration.webdav_sync.webdav_email_api.get_root_folder",
				args: { dav_account: frm.doc.webdav_account || null },
				callback: (r) => {
					if (r.message?.base_url) {
						const url = _dav_path_to_files_url(
							frm.doc.organizational_folder_url,
							r.message.base_url
						);
						window.open(url, "_blank", "noopener,noreferrer");
					} else {
						frappe.msgprint({
							title: __("Cannot open folder"),
							message: __("No base URL found for the linked DAV account."),
							indicator: "red",
						});
					}
				},
			});
			return;
		}

		if (!target) {
			frappe.msgprint({
				title: __("No folder selected"),
				message: __(`Use "Select Nextcloud Folder" first.`),
				indicator: "orange",
			});
			return;
		}

		window.open(target, "_blank", "noopener,noreferrer");
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Utility: convert a DAV path to a Nextcloud Files-app URL
//
//   DAV path:  /remote.php/dav/files/{user}/Projects/Acme Corp/
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
