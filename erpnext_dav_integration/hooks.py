app_name = "erpnext_dav_integration"
app_title = "Erpnext Dav Integration"
app_publisher = "b»robotized group"
app_description = "This is for Dav Integration"
app_email = "arun.govind@cloudconverge.io"
app_license = "agpl-3.0"

app_include_js = ["/assets/erpnext_dav_integration/js/webdav_file_browser23.js"]

doctype_js = {
    "Contact": "public/js/contact.js",
    "Event": "public/js/caldav_event.js",
    "Communication": "public/js/communication.js",
    "Project": "public/js/project.js",
    "Employee": "public/js/employee.js",
    "Contract": "public/js/contract.js"
}

doc_events = {
	"Contact": {
		"before_save": "erpnext_dav_integration.override.contact.sync_contact_to_carddav",
		"validate": "erpnext_dav_integration.override.contact.validate_contact",
	}
}
scheduler_events = {
	"cron": {
		"0 0 * * *": [
      "erpnext_dav_integration.scheduler.contact.update_all_dav_accounts_address_book_list",
      "erpnext_dav_integration.scheduler.event.sync_all_caldav_events"
      ],
		"0 15 * * *": [
			"erpnext_dav_integration.scheduler.contact.schedule_synchronization",
		],
	},
 	"hourly": [
        "erpnext_dav_integration.webdav_sync.scheduler.scheduled_file_sync",
    ]
}

doctype_list_js = {
    "Contact": "public/js/contact_list.js",
    "Event": "public/js/event_list.js"
}

override_doctype_class = {	
	"Event": "erpnext_dav_integration.override.event.CustomEvent"
}

after_uninstall = "erpnext_dav_integration.install.after_uninstall"