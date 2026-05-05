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
	async validate(frm) {
	    const createRoleIfMissing = async (role_name) => {
	        const exists = await frappe
	            .xcall("frappe.client.get", { doctype: "Role", name: role_name })
	            .then(() => true)
	            .catch(() => false);
		
	        if (exists) return;
		
	        try {
	            await frappe.xcall("frappe.client.insert", {
	                doc: {
	                    doctype: "Role",
	                    role_name: role_name,
	                },
	            });
	            frappe.msgprint(`Role "${role_name}" created successfully.`);
	        } catch (err) {
	            console.error(`Error creating role "${role_name}":`, err);
	            frappe.msgprint(`Failed to create role "${role_name}".`);
	        }
	    };
	
	    let tasks = [];
	
	    // 🔹 Address Books Roles
	    if (frm.doc.dav_address_books?.length) {
	        frm.doc.dav_address_books.forEach((row) => {
	            if (!row.address_book_name) return;
			
	            const role_name = `DAV Address Book: ${frm.doc.name} - ${row.address_book_name}`;
	            tasks.push(createRoleIfMissing(role_name));
	        });
	    }
	
	    // 🔥 Wait for all role creations before saving
	    await Promise.all(tasks);
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
