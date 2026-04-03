import frappe
import json
from frappe.desk.doctype.workspace.workspace import save_page

def execute():
    workspace_name = "Integrations"

    if not frappe.db.exists("Workspace", workspace_name):
        return

    ws = frappe.get_doc("Workspace", workspace_name)
    ws.is_standard = 0

    # ========================
    # 1. EDITORJS BLOCKS
    # ========================
    try:
        blocks = frappe.parse_json(ws.content) or []
    except Exception:
        blocks = []

    exists = any(
        b.get("type") == "card" and 
        b.get("data", {}).get("card_name") == "Nextcloud Services"
        for b in blocks
    )

    if not exists:
        blocks.append({
            "type": "card",
            "data": {
                "card_name": "Nextcloud Services",
                "col": 4,
                "links": [
                    {"label": "DAV Account", "link_type": "DocType", "link_to": "DAV Account"},
                ]
            }
        })

    # ========================
    # 2. LINKS TABLE
    # ========================
    existing_links = {(d.type, d.label) for d in ws.links}

    if ("Card Break", "Nextcloud Services") not in existing_links:
        ws.append("links", {
            "type": "Card Break",
            "label": "Nextcloud Services"
        })

    for label in ["DAV Account"]:
        if ("Link", label) not in existing_links:
            ws.append("links", {
                "type": "Link",
                "label": label,
                "link_type": "DocType",
                "link_to": label
            })

    ws.save(ignore_permissions=True)

    # ========================
    # 3. SIDEBAR (🔥 NEW PART)
    # ========================
    sidebar = frappe.get_all(
        "Workspace Sidebar",
        filters={"name": workspace_name},
        limit=1
    )

    if sidebar:
        sidebar_doc = frappe.get_doc("Workspace Sidebar", sidebar[0].name)
    else:
        sidebar_doc = frappe.get_doc({
            "Workspace Sidebar","integrations"
        })

    existing_sidebar = {(d.type, d.label) for d in sidebar_doc.items}

    # Section Break
    if ("Section Break", "Nextcloud Services") not in existing_sidebar:
        sidebar_doc.append("items", {
            "type": "Section Break",
            "label": "Nextcloud Services",
            "icon": "integration"
        })

    # Links
    for label in ["DAV Account"]:
        if ("Link", label) not in existing_sidebar:
            sidebar_doc.append("items", {
                "type": "Link",
                "label": label,
                "link_type": "DocType",
                "link_to": label,
                "child": 1
            })

    sidebar_doc.save(ignore_permissions=True)

    # ========================
    # SAVE PAGE
    # ========================
    save_page(
        name=workspace_name,
        public=1,
        new_widgets=json.dumps([]),
        blocks=json.dumps(blocks)
    )

    frappe.clear_cache()
    frappe.db.commit()