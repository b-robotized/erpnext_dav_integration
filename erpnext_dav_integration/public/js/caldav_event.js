// caldav_event.js

frappe.ui.form.on('Event', {
    refresh: function(frm) {
        load_calendars(frm);
        // Add a custom button to "Refresh from Calendar"
        frm.add_custom_button(__('Refresh from Calendar'), function() {
            if (!frm.doc.caldav_event_id) {
                frappe.msgprint(__('This event is not connected to a CalDAV event.'));
                return;
            }
            frappe.call({
                method: 'erpnext_dav_integration.caldav_sync.api.refresh_from_caldav',
                args: {
                    event_name: frm.doc.name
                },
                freeze: true,
                freeze_message: __('Refreshing event from CalDAV...'),
                callback: function(response) {
                    
                }
            });
        },__("CalDAV"));
        frm.add_custom_button(__('Create CalDAV Event'), function() {
            if (frm.doc.caldav_event_id) {
                frappe.msgprint(__('This event is already connected to a CalDAV event.'));
                return;
            }
            frappe.call({
                method: 'erpnext_dav_integration.caldav_sync.api.create_caldav_event',
                args: {
                    event_name: frm.doc.name,
                    calendar_name: frm.doc.selected_calendar
                },
                freeze: true,
                freeze_message: __('Creating CalDAV event...'),
                callback: function(response) {
                    if (response.exc) {
                        console.error("Creation failed:", response.exc);
                        frappe.msgprint(__('Failed to create CalDAV event.'));
                        return;
                    }
                    if (response.message) {
                        frm.reload_doc();
                        
                    } else {
                        frappe.msgprint(__('No response from server.'));
                    }
                }
            });
        },__("CalDAV"));
        // frm.add_custom_button(__('Delete CalDAV Event'), function() {
        //     if (!frm.doc.caldav_event_id) {
        //         frappe.msgprint(__('This event is not connected to a CalDAV event.'));
        //         return;
        //     }
        //     frappe.confirm(
        //         __('Are you sure you want to delete the linked CalDAV event? This action cannot be undone.'),
        //         function() {
        //             frappe.call({
        //                 method: 'erpnext_dav_integration.caldav_sync.api.delete_caldav_event',
        //                 args: {
        //                     event_name: frm.doc.name
        //                 },
        //                 freeze: true,
        //                 freeze_message: __('Deleting CalDAV event...'),
        //                 callback: function(response) {
        //                     if (response.exc) {
        //                         frappe.msgprint(__('Failed to delete CalDAV event.'));
        //                         return;
        //                     }
        //                     if (response.message) {
        //                         frm.reload_doc();
                                
        //                     } else {
        //                         frappe.msgprint(__('No response from server.'));
        //                     }
        //                 }
        //             });
        //         }
        //     );  
        // },__("CalDAV"));
        frm.add_custom_button(__('Unlink from CalDAV'), function() {
            if (!frm.doc.caldav_event_id) {
                frappe.msgprint(__('This event is not connected to a CalDAV event.'));
                return;
            }
            frappe.confirm(
                __('Are you sure you want to unlink this event from the CalDAV event? This will not delete the CalDAV event.'),
                function() {
                    frappe.call({
                        method: 'erpnext_dav_integration.caldav_sync.api.unlink_from_caldav',
                        args: {
                            event_name: frm.doc.name
                        },
                        freeze: true,
                        freeze_message: __('Unlinking from CalDAV...'),
                        callback: function(response) {
                            if (response.exc) {
                                console.error("Unlinking failed:", response.exc);
                                frappe.msgprint(__('Failed to unlink from CalDAV.'));
                                return;
                            }
                            if (response.message) {
                                frm.reload_doc();
                                
                            } else {
                                frappe.msgprint(__('No response from server.'));
                            }
                        }
                    });
                }
            );
        },__("CalDAV"));
        
    },
    all_day: function(frm) {
        if (frm.doc.all_day && frm.doc.starts_on) {
            let end = frappe.datetime.add_days(frm.doc.starts_on, 0);
            end = frappe.datetime.get_datetime_as_string(end).split(" ")[0] + " 23:59:59";

            frm.set_value('ends_on', end);
        }
    },

    caldav_account: function(frm) {
        // make fields toggle if CalDAV account is selected
        let is_connected = frm.doc.caldav_account;
        frm.set_df_property('selected_calendar', 'read_only', !is_connected);
        frm.set_df_property('create_in_caldav', 'read_only', !is_connected);
        frm.set_df_property('caldav_organizer', 'read_only', !is_connected);
        frm.set_df_property('caldav_organizer_name', 'read_only', !is_connected);
        // When CalDAV account is selected, load available calendars
        if (frm.doc.caldav_account) {
            load_calendars(frm);
            // set Orgsnizer Name from session user
            let user = frappe.session.user;

            if (user && user !== "Guest") {
                if (!frm.doc.caldav_organizer) {
                    frm.set_value('caldav_organizer',
                        user !== "Administrator" ? user : null
                    );
                }
                frappe.db.get_value('User', user, 'full_name')
                    .then(r => {
                        if (r.message) {
                            frm.set_value('caldav_organizer_name', r.message.full_name);
                        }
                    });
            }
        }

    },
    selected_calendar: function(frm) {
        // When a calendar is selected, store the calendar URL in a hidden field
        if (frm.doc.selected_calendar && frm.doc.caldav_account) {
            frappe.call({
                method: 'erpnext_dav_integration.caldav_sync.api.get_calendar_url',
                args: {
                    dav_account: frm.doc.caldav_account,
                    calendar_name: frm.doc.selected_calendar
                },
                callback: function(r) {
                    if (r.message) {
                        frm.set_value('caldav_calendar_url', r.message[0]);
                        frm.set_value('color', r.message[1]);
                        frm.set_value('create_in_caldav', 1);
                    }
                }
            });
            // reset event_id to allow creating new event in selected calendar
            frm.set_value('caldav_event_id', null);
            frm.set_value('caldav_sync_status', 'Not Synced');
            frm.set_value('caldav_status', null);
            frm.set_value('caldav_etag', null);
            frm.set_value('caldav_event_url', null);
            frm.set_value('caldav_created', null);
            frm.set_value('caldav_sequence', null);
            frm.set_value('caldav_card_text', null);
            frm.set_value('caldav_etag', null);
        }
    },
    onload: function(frm) {
        if (frappe.session.user != "Administrator" && !frappe.user.has_role("System Manager") && frm.doc.owner != frappe.session.user && frm.doc.caldav_organizer != frappe.session.user ) {
            
            frm.set_df_property('event_public_description', 'read_only', true);
            frm.set_df_property('caldav_participants_table', 'read_only', true);
            frm.refresh_field('caldav_participants_table');

        }
    },
    status: function(frm){
        if(frm.doc.status == "Cancelled" && frm.doc.caldav_status != "Cancelled"){
            frm.set_value("caldav_status", "Cancelled")
        }else if(frm.doc.caldav_status != "Confirmed"){
            frm.set_value("caldav_status", "Confirmed")
        }
    }
});


// Handle participant table modifications
frappe.ui.form.on('Event CalDAV Participant', {
    send_invitation: function(frm, cdt, cdn) {
        // When checkbox is toggled, could trigger invitation send
        let row = locals[cdt][cdn];
        if (row.send_invitation && !frm.doc.caldav_event_id) {
            frappe.msgprint('Please connect to CalDAV first before sending invitations');
            row.send_invitation = 0;
            frm.refresh_field('caldav_participants_table');
        }
    }
});
function load_calendars(frm) {
    if (!frm.doc.caldav_account) return;

    frappe.call({
        method: 'erpnext_dav_integration.caldav_sync.api.get_calendars',
        args: {
            dav_account: frm.doc.caldav_account
        },
        callback: function(r) {
            if (r.message) {
                let options = r.message.map(c => c.display_name).join('\n');

                frm.set_df_property('selected_calendar', 'options', options);

                // IMPORTANT: refresh field to apply options
                frm.refresh_field('selected_calendar');
            }
        }
    });
}