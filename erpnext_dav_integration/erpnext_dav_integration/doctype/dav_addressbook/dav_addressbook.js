// Copyright (c) 2026, b»robotized group and contributors
// For license information, please see license.txt

frappe.ui.form.on("DAV AddressBook", {
	dav_account: function (frm) {
		if (!frm.doc.dav_account) return;
		console.log("Fetching address books for DAV Account: " + frm.doc.dav_account);
		frappe.call({
			method: "erpnext_dav_integration.erpnext_dav_integration.doctype.dav_account.dav_account.get_address_books",
			args: {
				dav_account: frm.doc.dav_account,
			},
			callback: function (r) {
				if (r.message) {
					let address_books = "";
					let default_address_book = null;
					for (let i = 0; i < r.message.length; i++) {
						address_books += r.message[i].name + "\n";
						if (r.message[i].is_default) {
							default_address_book = r.message[i].name;
						}
					}

					frm.set_df_property("dav_addressbook", "options", address_books);
					if (default_address_book) {
						frm.set_value("dav_addressbook", default_address_book);
					}
				}
			},
		});
	},
	refresh: function (frm) {
		if (!frm.doc.dav_account) return;
		frappe.call({
			method: "erpnext_dav_integration.erpnext_dav_integration.doctype.dav_account.dav_account.get_address_books",
			args: {
				dav_account: frm.doc.dav_account,
			},
			callback: function (r) {
				if (r.message) {
					let address_books = "";
					for (let i = 0; i < r.message.length; i++) {
						address_books += r.message[i].name + "\n";
					}
					frm.set_df_property("dav_addressbook", "options", address_books);
				}
			},
		});
	},
	dav_addressbook(frm) {
		frappe.call({
			method: "erpnext_dav_integration.erpnext_dav_integration.doctype.dav_account.dav_account.get_address_book_url",
			args: {
				doc: frm.doc.dav_account,
				address_book_name: frm.doc.dav_addressbook,
			},
			callback: function (r) {
				if (r.message) {
					frm.set_value("dav_addressbook_url", r.message);
				}
			},
		});
	},
});
