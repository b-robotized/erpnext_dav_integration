frappe.listview_settings["Event"] = {
	onload: function (listview) {
		// Add a menu item for syncing with DAV Calendar
		// listview.page.add_menu_item(__("Sync with DAV Calendar"), function () {
		// 	let selected_events = listview.get_checked_items();

		// 	if (!selected_events.length) {
		// 		frappe.msgprint(__("No events selected."));
		// 		return;
		// 	}

		// 	let event_names = selected_events.map((e) => e.name);

		// 	frappe.call({
		// 		method: "erpnext_dav_integration.webdav_sync.api.sync_with_dav_calendar",
		// 		args: {
		// 			event_names: event_names,
		// 		},
		// 		freeze: true,
		// 		freeze_message: __("Syncing events with DAV Calendar..."),

		// 		callback: function (response) {
		// 			if (response.exc) {
		// 				console.error("Sync failed:", response.exc);
		// 				frappe.msgprint(__("Failed to sync events with DAV Calendar."));
		// 				return;
		// 			}

		// 			if (response.message) {
		// 				frappe.msgprint({
		// 					title: __("Success"),
		// 					message: __("Selected events synced successfully."),
		// 					indicator: "green",
		// 				});
		// 			} else {
		// 				frappe.msgprint(__("No response from server."));
		// 			}
		// 		},
		// 	});
		// });
		// Add a menu item for generating iCalendar file

		// Add a menu item for fetching events from DAV Calendar
		listview.page.add_menu_item(__("Fetch Events from DAV Calendar"), function () {
			// Call the server-side method to fetch events from DAV Calendar
			frappe.call({
				method: "erpnext_dav_integration.webdav_sync.api.enqueue_fetch_events_from_dav_calendar",
				freeze: true,
				freeze_message: __("Fetching events from DAV Calendar..."),
				callback: function (response) {
					if (response.message) {
						frappe.show_alert({
							message: __("Events synced successfully"),
							indicator: "green",
						});
						listview.refresh();
					} else if (response.exc) {
						frappe.msgprint(__("Failed to fetch events from DAV Calendar."));
					}
				},
			});
		});
	},
};
