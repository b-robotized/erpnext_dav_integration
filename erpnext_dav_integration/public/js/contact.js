frappe.ui.form.on("Contact", {
	refresh: function(frm) {
		// fetch default dav accont and address book on load
		if (frm.is_new()) {
			frappe.call({
				method: "erpnext_dav_integration.erpnext_dav_integration.doctype.dav_account.dav_account.get_default_dav_account",
				callback: function (r) {
					if (r.message) {						
						frm.set_value("custom_dav_account", r.message || null);
						frm.trigger("custom_dav_account");
					}
				},
			});
		}
	},
	custom_dav_account: function (frm) {
		if (!frm.doc.custom_dav_account) return;

		frappe.call({
			method: "erpnext_dav_integration.erpnext_dav_integration.doctype.dav_account.dav_account.get_default_address_book",
			args: {
				dav_account: frm.doc.custom_dav_account,
			},
			callback: function (r) {
				if (r.message) {
					let default_address_book = r.message.name;
					frm.set_value("custom_dav_address_book", default_address_book || null);
				}
			},
		});
	},
	custom_enable_dav_sync: function(frm) {
		if (frm.doc.custom_enable_dav_sync) {
			frappe.call({
				method: "erpnext_dav_integration.erpnext_dav_integration.doctype.dav_account.dav_account.get_default_dav_account",
				callback: function (r) {
					if (r.message) {						
						frm.set_value("custom_dav_account", r.message || null);
						frm.trigger("custom_dav_account");
					}
				},
			});
		} else {
			frm.set_value("custom_dav_account", null);
			frm.set_value("custom_dav_address_book", null);
			frm.set_value("custom_dav_address_book_url", null);
			frm.set_value("custom_vcard_url", null);
			frm.set_value("custom_dav_uid", null);
			frm.set_value("custom_sync_status", null);
			frm.set_value("custom_last_sync", null);
			frm.set_value("custom_vcard", null);
			frm.refresh_field("custom_dav_account");
			frm.refresh_field("custom_dav_address_book");
			frm.refresh_field("custom_dav_address_book_url");
			frm.refresh_field("custom_vcard_url");
			frm.refresh_field("custom_dav_uid");
			frm.refresh_field("custom_sync_status");
			frm.refresh_field("custom_last_sync");
			frm.refresh_field("custom_vcard");

		}
	},
	custom_dav_address_book: function (frm) {
		if (!frm.doc.custom_dav_address_book) return;

		frappe.call({
			method: "erpnext_dav_integration.erpnext_dav_integration.doctype.dav_account.dav_account.get_address_book_url",
			args: {
				address_book_name: frm.doc.custom_dav_address_book,
				doc: frm.doc.custom_dav_account,
			},
			callback: function (r) {
				if (r.message) {
					frm.set_value("custom_dav_address_book_url", r.message);
					frm.refresh_field("custom_dav_address_book_url");
				}
			},
		});
	},
	gender: function (frm) {
		if (frm.doc.gender) {
			// map gender to gender code
			let gender_code = "";
			if (frm.doc.gender === "Male") {
				gender_code = "M";
			} else if (frm.doc.gender === "Female") {
				gender_code = "F";
			} else if (frm.doc.gender === "Other") {
				gender_code = "O";
			} else if (frm.doc.gender === "Unknown") {
				gender_code = "U";
			} else if (frm.doc.gender === "None") {
				gender_code = "N";
			} else {
				gender_code = "O";
			}
			frm.set_value("custom_gender_code", gender_code);
		} else {
			frm.set_value("custom_gender_code", "");
		}
	},
});
