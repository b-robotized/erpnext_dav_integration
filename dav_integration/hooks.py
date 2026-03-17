# -*- coding: utf-8 -*-

app_name = "dav_integration"
app_title = "DAV Integration"
app_publisher = ""
app_description = "Generic WebDAV/CalDAV/CardDAV integration"
app_icon = "octicon octicon-cloud"
app_color = "grey"
app_email = ""
app_license = "MIT"

# Enqueue periodic sync (adjust the cron if you like)
scheduler_events = {
    "cron": {
        "*/10 * * * *": [
            "dav_integration.jobs.sync.enqueue_all"
        ]
    }
}
