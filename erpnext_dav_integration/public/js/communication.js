/* global erpnext_dav_integration */

frappe.ui.form.on("Communication", {
	refresh(frm) {
		console.log("Communication refresh triggered");
		if (frm.doc.__islocal) return;

		frm.add_custom_button(
			__("Cloud storage"),
			() => {
				const browser = new erpnext_dav_integration.webdav.FileBrowser({
					doctype: frm.doctype,
					docname: frm.docname,
					on_attach: () => frm.reload_doc(),
					on_link(url, filename) {
						// Insert link into the communication content field
						const $content =
							frm.fields_dict.content &&
							frm.fields_dict.content.$input_wrapper.find(
								".ql-editor, [contenteditable]"
							);

						if ($content && $content.length) {
							// Quill editor: insert at current cursor or end
							const quill = frm.fields_dict.content.quill_editor;
							if (quill) {
								const index = quill.getLength();
								quill.insertText(index, "\n", "silent");
								quill.insertEmbed(index + 1, "link", {
									href: url,
									innerText: filename,
								});
							} else {
								$content.append(
									`<p><a href="${frappe.utils.escape_html(
										url
									)}">${frappe.utils.escape_html(filename)}</a></p>`
								);
							}
						} else {
							// Fallback: copy to clipboard
							frappe.utils.copy_to_clipboard(url);
							frappe.show_alert({
								message: __("Share link copied to clipboard"),
								indicator: "green",
							});
						}
					},
				});
				browser.show();
			},
			__("Attachments")
		);
	},
});

// ─────────────────────────────────────────────────────────────────────────────
// Helper: insert a hyperlink into the CommunicationComposer editor
// ─────────────────────────────────────────────────────────────────────────────

function _insert_link_into_composer(composer, url, filename) {
	const $editor = composer.dialog?.$wrapper?.find(".ql-editor, [contenteditable]").first();
	const label = filename || url;

	if ($editor && $editor.length) {
		if (composer.editor && typeof composer.editor.insert_html === "function") {
			composer.editor.insert_html(
				`<a href="${frappe.utils.escape_html(url)}">${frappe.utils.escape_html(label)}</a>`
			);
		} else {
			$editor.append(
				`<p><a href="${frappe.utils.escape_html(url)}">${frappe.utils.escape_html(
					label
				)}</a></p>`
			);
		}
		return;
	}

	frappe.utils.copy_to_clipboard(url);
	frappe.show_alert({
		message: __("Share link copied to clipboard"),
		indicator: "green",
	});
}

frappe.views.CommunicationComposer.prototype._inject_cloud_storage_button = function () {
	const composer = this;

	// Prevent duplicate buttons
	if (composer.dialog.$wrapper.find(".wdav-open-browser").length) {
		return;
	}

	// Actual attachment button container in current Frappe
	const $attach_btn = composer.dialog.$wrapper.find(".add-more-attachments button");

	if (!$attach_btn.length) {
		console.warn("Attachment button not found");
		return;
	}

	const $cloud_btn = $(`
        <button type="button"
                class="btn btn-xs btn-default wdav-open-browser"
                style="margin-left: 6px;"
                title="${__("Browse WebDAV / Nextcloud files")}">
            ${frappe.utils.icon("folder-normal", "xs")}
            ${__("Cloud Storage")}
        </button>
    `);

	// Insert beside Add Attachment button
	$attach_btn.after($cloud_btn);

	$cloud_btn.on("click", () => {
		const doctype = composer.doc?.doctype || "Communication";
		const docname = composer.doc?.name || "new-communication-1";

		const browser = new erpnext_dav_integration.webdav.FileBrowser({
			doctype,
			docname,

			on_attach(file_doc) {
				// Push into attachment list
				composer.attachments = composer.attachments || [];

				composer.attachments.push({
					name: file_doc.file,
					file_name: file_doc.file_name,
					file_url: file_doc.file_url,
				});

				// Re-render attachment rows
				composer.render_attachment_rows({
					name: file_doc.file,
					file_name: file_doc.file_name,
					file_url: frappe.urllib.get_full_url(file_doc.file_url),
				});

				frappe.show_alert({
					message: __("{0} attached", [file_doc.file_name]),
					indicator: "green",
				});
			},

			on_link(url, filename) {
				_insert_link_into_composer(composer, url, filename);
			},
		});

		browser.show();
	});
};

// ─────────────────────────────────────────────────────────────────────────────
// Hook 1: CommunicationComposer (email compose dialog)
//
// The composer is used when clicking "New Email", "Reply", "Forward" on any
// document.  We extend its prototype once it's available.
// ─────────────────────────────────────────────────────────────────────────────

frappe.after_ajax(() => {
	// Poll until CommunicationComposer is defined (it loads asynchronously)
	const try_hook_composer = () => {
		if (!frappe.views || !frappe.views.CommunicationComposer) {
			return;
		}

		const _orig_make = frappe.views.CommunicationComposer.prototype.make;

		frappe.views.CommunicationComposer.prototype.make = function () {
			_orig_make.call(this);
			this._inject_cloud_storage_button();
		};

		frappe.views.CommunicationComposer.prototype._inject_cloud_storage_button = function () {
			const composer = this;

			// The attachment area lives in .attachment-control inside the dialog
			const $attach_area = composer.dialog.$wrapper
				.find(".attachment-control, .btn-attach-files")
				.first();

			if (!$attach_area.length) return;

			const $cloud_btn = $(`
                <button type="button"
                        class="btn btn-default btn-xs wdav-open-browser"
                        style="margin-left:6px"
                        title="${__("Browse Nextcloud / WebDAV files")}">
                    <i class="ti ti-cloud-upload" style="font-size:14px;vertical-align:-2px" aria-hidden="true"></i>
                    ${__("Cloud storage")}
                </button>`);

			$attach_area.after($cloud_btn);

			$cloud_btn.on("click", () => {
				// The composer may not have saved the Communication doc yet;
				// use a transient docname if needed.
				const doctype = "Communication";
				const docname =
					composer.doc && composer.doc.name ? composer.doc.name : "new-communication-1";

				const browser = new erpnext_dav_integration.webdav.FileBrowser({
					doctype,
					docname,

					// After a copy-attach, refresh the composer's attachment list
					on_attach(file_doc) {
						// Add the file to the composer's attachment list so it
						// gets included when the email is sent
						if (composer.attachments) {
							composer.attachments.push({
								file_url: file_doc.file_url,
								file_name: file_doc.file_name,
							});
						}
						// Refresh the attachment preview area if it exists
						if (typeof composer.reload_attachment_html === "function") {
							composer.reload_attachment_html();
						}
						frappe.show_alert({
							message: __("{0} added to email", [file_doc.file_name]),
							indicator: "green",
						});
					},

					// After link creation, insert a hyperlink into the email body
					on_link(url, filename) {
						_insert_link_into_composer(composer, url, filename);
					},
				});

				browser.show();
			});
		};
	};

	// Try immediately; retry for up to 5 s if the composer hasn't loaded yet
	try_hook_composer();
	const interval = setInterval(() => {
		if (frappe.views && frappe.views.CommunicationComposer) {
			try_hook_composer();
			clearInterval(interval);
		}
	}, 500);
	setTimeout(() => clearInterval(interval), 5000);
});
