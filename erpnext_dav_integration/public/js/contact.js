frappe.ui.form.on("Contact", {
	dav_addressbook: function (frm) {
		frm.set_value("custom_vcard_url", "");
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
