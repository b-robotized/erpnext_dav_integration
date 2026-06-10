// Copyright (c) 2026, CloudConverge and contributors
// For license information, please see license.txt

frappe.ui.form.on("DAV Account", {
	refresh(frm) {
		frm.add_custom_button("Create Address Book", () => {
			frappe.prompt(
				[
					{
						label: "Address Book Name",
						fieldname: "address_book_name",
						fieldtype: "Data",
						reqd: 1,
					},
				],
				(values) => {
					frappe.call({
						method: "erpnext_dav_integration.erpnext_dav_integration.doctype.dav_account.dav_account.create_address_book",
						args: {
							docname: frm.doc.name,
							address_book_name: values.address_book_name,
						},
						callback: function (r) {
							if (!r.exc) {
								frappe.msgprint("Address Book Created Successfully");
								frm.reload_doc();
							}
						},
					});
				}
			);
		});
		frm.add_custom_button("Sync Address Books", () => {
			frappe.call({
				method: "update_address_book_list",
				doc: frm.doc,
				callback: function (r) {
					if (!r.exc) {
						frappe.msgprint("Address Books Synchronized Successfully");
						frm.reload_doc();
					}
				},
			});
		});
	},
	default(frm) {
		if (frm.doc.__islocal) {
			frm.set_value("enabled", 1);
		}
	},
	validate(frm) {
		if (frm.doc.enabled && frm.doc.default) {
			// Check if there's another enabled account marked as default
			frappe.call({
				method: "frappe.client.get_list",
				args: {
					doctype: "DAV Account",
					filters: {
						enabled: 1,
						default: 1,
						name: ["!=", frm.doc.name],
					},
					fields: ["name"],
				},
				callback: function (r) {
					if (r.message && r.message.length > 0) {
						frm.set_value("default", 0);
						frappe.msgprint(
							"Another DAV Account is already marked as default. Please uncheck the default option for the other account before saving this one.",
							"Default Account Conflict"
						);
						
					}
				},
			});
		}
	},
	discover_calendars_btn(frm) {
		frappe.call({
			method: "discover_calendars",
			doc: frm.doc,
			callback: function (r) {
				if (!r.exc) {
					frappe.msgprint("Calendar Discovery Completed");
					frm.reload_doc();
				}
			},
		});
	}
});

frappe.ui.form.on("DAV Address Book", {
	before_dav_address_books_remove(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		// confirmation dialog before deleting the address book
		frappe.confirm(
			"Are you sure you want to delete this address book? This action cannot be undone.",
			function () {
				// User clicked "Yes"
				delete_address_book(frm, row);
			},
			function () {
				frm.reload_doc();
			}
		);
	},
});

function delete_address_book(frm, row) {
	frappe.call({
		method: "erpnext_dav_integration.erpnext_dav_integration.doctype.dav_account.dav_account.delete_address_book",
		args: {
			docname: frm.doc.name,
			address_book_url: row.url,
		},
		callback: function (r) {
			if (!r.exc) {
				frappe.msgprint("Address Book Deleted Successfully");
				frm.save();
			}
		},
	});
}
