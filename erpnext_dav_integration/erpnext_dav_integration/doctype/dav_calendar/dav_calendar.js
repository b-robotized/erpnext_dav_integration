// Copyright (c) 2026, b»robotized group and contributors
// For license information, please see license.txt

frappe.ui.form.on("DAV Calendar", {
	refresh(frm) {
		load_calendars(frm);
	},
	dav_account(frm) {
		load_calendars(frm);
	},
	dav_calendar(frm) {
		if (!frm.doc.dav_account || !frm.doc.dav_calendar) return;

		frappe.call({
			method: "erpnext_dav_integration.webdav_sync.api.get_calendar_url",
			args: {
				dav_account: frm.doc.dav_account,
				calendar_name: frm.doc.dav_calendar,
			},
			callback: function (r) {
				if (r.message) {
					console.log("Setting dav_calendar_url to", r.message);
					frm.set_value("dav_calendar_url", r.message[0]);
				}
			},
		});
	},
});
function load_calendars(frm) {
	if (!frm.doc.dav_account) return;

	frappe.call({
		method: "erpnext_dav_integration.webdav_sync.api.get_calendars",
		args: {
			dav_account: frm.doc.dav_account,
		},
		callback: function (r) {
			if (r.message) {
				let options = r.message.map((c) => c.display_name).join("\n");
				let default_calendar = r.message.find((c) => c.is_default);

				frm.set_df_property("dav_calendar", "options", options);
				// IMPORTANT: refresh field to apply options
				if (!frm.doc.dav_calendar) {
					frm.set_value(
						"dav_calendar",
						default_calendar ? default_calendar.display_name : null
					);
				}
				frm.refresh_field("dav_calendar");
			}
		},
	});
}
