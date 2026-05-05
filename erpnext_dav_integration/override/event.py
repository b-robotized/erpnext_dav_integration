import frappe
from frappe import _
from erpnext_dav_integration.caldav_sync.manager import CalDAVManager
from frappe.desk.doctype.event.event import Event
import json


class CustomEvent(Event):
    def validate(self):
        """
        Called before Event is saved
        - Detect if caldav event was modified
        - Sync to CalDAV if needed
        """
        if self.create_in_caldav and not self.is_new():
            self.after_insert()
            return
        super().validate()
        # Only sync if already connected to CalDAV
        if not self.caldav_event_id or not self.caldav_account or not self.sync_with_caldav or self.create_in_caldav:
            return

        auto_sync = frappe.db.get_value("DAV Account", self.caldav_account, "auto_sync_events")

        if not auto_sync:
            # Mark as out-of-sync for manual sync later
            if self.has_value_changed():
                self.caldav_sync_status = 'OutOfSync'
                
            return

        # Check if fields changed
        changed_fields = self._get_changed_caldav_fields()
        
        if changed_fields:
            try:
                self._sync_to_caldav()
            except Exception as e:
                frappe.log_error(
                    f"Failed to sync Event {self.name} to CalDAV: {str(e)}",
                    "CalDAV Sync Error"
                )
                self.caldav_sync_status = 'OutOfSync'
                
    def on_update(self):
        super().on_update()
        self.sync_event_shares()
        
    def on_trash(self):
        super().on_trash()
        self.remove_event_shares()
    # ---------------------------
    # 📝 AFTER_INSERT HOOK
    # ---------------------------
    def after_insert(self):
        """
        Called after Event is inserted
        - Create new event in CalDAV if sync_to_caldav mode selected
        """
        self.sync_event_shares()
        
        if self.create_in_caldav and self.caldav_account:
            try:
                self._create_in_caldav()
            except Exception as e:
                frappe.log_error(frappe.get_traceback(), "CalDAV Create Error")
        
    # ---------------------------
    # ❌ AFTER_DELETE HOOK
    # ---------------------------
    # def after_delete(self):
    #     """
    #     Called after Event is deleted
    #     - Delete from CalDAV if it was connected
    #     """
        
    #     if not self.caldav_event_id or not self.caldav_account:
    #         return

    #     # Check if auto-delete is enabled
    #     auto_delete = frappe.db.get_value("DAV Account", self.caldav_account, "auto_delete_caldav_events")
        

    #     if not auto_delete:    
    #         return

    #     try:
    #         self._delete_from_caldav()
    #     except Exception as e:

    #         frappe.log_error(
    #             _("Event deleted from ERPNext but couldn't delete from calendar: {0}").format(str(e)),  
    #         )
    # ---------------------------
    # 🔗 SYNC METHODS (INTERNAL)
    # ---------------------------
    def _create_in_caldav(self):
        """Create new event in CalDAV"""

        if not self.caldav_account:
            frappe.throw("Please select a calendar account")
        # Get calendar URL
        calendar_url = frappe.db.get_value(
            'DAV Account Calendar',
            {'parent': self.caldav_account, 'display_name': self.selected_calendar},
            'calendar_url'
        )

        if not calendar_url:
            frappe.throw("Calendar not found")

        # Create in CalDAV
        manager = CalDAVManager(self.caldav_account)
        result = manager.create_event_in_calendar(calendar_url, self)
        # Update Event with CalDAV metadata
        self.caldav_event_id = result['caldav_event_id']
        self.caldav_event_url = result['caldav_url']
        self.caldav_uuid = result['caldav_uuid']
        self.caldav_etag = result.get('etag')
        self.caldav_sync_status = 'Connected'
        self.caldav_status = (result.get('status') or "Confirmed").title()
        if result.get('status') == "CANCELLED":
            self.status = "Cancelled"
        else:
            self.status = "Open"
        self.caldav_sequence = 0
        self.caldav_card_text = result.get('caldav_card_text')
        self.caldav_created = frappe.utils.now()

        # Clear sync fields
        self.create_in_caldav = 0
        self.save(ignore_permissions=True)

    def _sync_to_caldav(self):
        """Update existing event in CalDAV"""
        

        manager = CalDAVManager(self.caldav_account)
        result = manager.update_event_in_calendar(self)

        # Update ETag and sequence
        self.caldav_etag = result['etag']
        self.caldav_sequence = result['sequence']
        self.caldav_sync_status = 'Connected'
        self.caldav_card_text = result.get('caldav_card_text')

    def _delete_from_caldav(self):
        """Delete event from CalDAV"""
        
        manager = CalDAVManager(self.caldav_account)
        manager.delete_event_from_calendar(self)

    # Event still exists in ERPNext but marked as deleted from provider
    # (already deleted, so nothing to update)
    def _get_changed_caldav_fields(self):
        """
        Check if CalDAV-relevant fields changed
        Returns list of changed field names
        """
        if self.is_new():
            return
        caldav_fields = [
            'subject',
            'description',
            'location',
            'starts_on',
            'ends_on',
            'color',
            'caldav_participants_table'
        ]
        changed = []
        from_db = frappe.get_doc("Event", self.name)
        for field in caldav_fields:
            if self.has_value_changed(from_db.get(field), field):
                changed.append(field)
        return changed

    def has_value_changed(self,db_value, field=None):
        """
        Check if field value changed
        
        Args:
            field: Specific field to check, or None for any field
        """
        
        
        if field:
            old_val = db_value
            new_val = self.get(field)
            
            return old_val != new_val
        else:
            # Check all fields
            old_self = self.load_from_db()
            for field in self.meta.get_valid_columns():
                if old_self.get(field) != self.get(field):
                    return True
        
        return False
    
    def remove_event_shares(self):
        shares = frappe.get_all(
            "DocShare",
            filters={
                "share_doctype": "Event",
                "share_name": self.name
            },
            pluck="name"
        )

        for share in shares:
            frappe.delete_doc("DocShare", share)
            
    def sync_event_shares(doc):
        if doc.doctype != "Event":
            return

        users_to_share = set()

        # 🔹 Organizer
        if doc.caldav_organizer:
            users_to_share.add(doc.caldav_organizer)

        # 🔹 Participants
        for p in doc.get("caldav_participants_table", []):
            if p.email:
                users_to_share.add(p.email)

        # Remove current user (optional)
        users_to_share.discard(doc.owner)

        # 🔹 Existing shares
        existing_shares = frappe.get_all(
            "DocShare",
            filters={
                "share_doctype": "Event",
                "share_name": doc.name
            },
            fields=["name", "user"]
        )

        existing_users = {s.user for s in existing_shares}

        # 🔹 Add new shares
        for user in users_to_share - existing_users:
            if frappe.db.exists("User",user):
                try:
                    frappe.share.add(
                        "Event",
                        doc.name,
                        user=user,
                        read=1,
                        write=0,
                        share=0
                    )
                except Exception:
                    frappe.log_error(frappe.get_traceback(), "Share Add Failed")

        # 🔹 Remove outdated shares
        for share in existing_shares:
            if share.user not in users_to_share:
                try:
                    frappe.delete_doc("DocShare", share.name)
                except Exception:
                    frappe.log_error(frappe.get_traceback(), "Share Remove Failed")
