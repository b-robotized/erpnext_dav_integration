frappe.listview_settings['Contact'] = {
    onload: function(listview) {

        listview.page.add_inner_button('Sync All Contacts', function() {

            frappe.confirm(
                'Are you sure you want to sync all contacts?',
                function() {

                    frappe.call({
                        method: 'erpnext_dav_integration.scheduler.contact.synchronize_carddav_contacts',  // backend method
                        freeze: true,
                        freeze_message: 'Syncing contacts...',
                        callback: function(r) {
                            if (r.message) {
                                frappe.msgprint('Contacts synced successfully');
                                listview.refresh();
                            }
                        }
                    });

                }
            );

        });
        listview.page.add_inner_button('Delete Trashed Contacts', function() {
            frappe.confirm(
                'Are you sure you want to permanently delete all trashed contacts? This action cannot be undone.',
                function() {
                    frappe.call({
                        method: 'erpnext_dav_integration.scheduler.contact.deletion_of_trashed_contacts',
                        freeze: true,
                        freeze_message: 'Deleting trashed contacts...',
                        callback: function(r) {
                            if (r.message) {
                                frappe.msgprint('Trashed contacts deleted successfully');
                                listview.refresh();
                            }
                        }
                    });

                }
            );


        });

    }
};