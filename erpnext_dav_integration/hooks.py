app_name = "erpnext_dav_integration"
app_title = "Erpnext Dav Integration"
app_publisher = "b»robotized group"
app_description = "This is for Dav Integration"
app_email = "arun.govind@cloudconverge.io"
app_license = "agpl-3.0"

doctype_js = {"Contact": "public/js/contact.js"}

doc_events = {
    "Contact": {
        "before_save": "erpnext_dav_integration.override.contact.sync_contact_to_carddav",
        "on_trash": "erpnext_dav_integration.override.contact.delete_contact_from_carddav"
    }
}
scheduler_events = {
    "cron": {
        "0 0 * * *": [
            "erpnext_dav_integration.scheduler.contact.update_all_dav_accounts_address_book_list"
        ],
        "*/15 * * * *": [
            "erpnext_dav_integration.scheduler.contact.schedule_synchronization",
            "erpnext_dav_integration.scheduler.contact.deletion_of_trashed_contacts",
            ]
    }
}

doctype_list_js = {
  "Contact": "public/js/contact_list.js"
}