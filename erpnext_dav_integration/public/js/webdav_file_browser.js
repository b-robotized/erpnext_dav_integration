/**
 * webdav_file_browser.js
 * ======================
 * Use Case 1: Browse WebDAV / Nextcloud files from the ERPNext email composer.
 *
 * What this file does:
 *   1.  Defines `WebDAVFileBrowser` – a reusable Frappe dialog that renders a
 *       navigable file browser backed by the webdav_email_api.py endpoints.
 *
 *   2.  Hooks into `frappe.views.CommunicationComposer` to inject a
 *       "Cloud storage" button beside the standard attachment controls.
 *
 *   3.  Hooks into the Communication doctype form so the button also appears
 *       when a Communication record is open directly.
 *
 */

frappe.provide("erpnext_dav_integration.webdav");

// ─────────────────────────────────────────────────────────────────────────────
// Icon map  (Tabler outline icon names per file type returned by the API)
// ─────────────────────────────────────────────────────────────────────────────
const ICON_MAP = {
    folder: "folder",
    pdf:    "picture_as_pdf",
    doc:    "description",
    xls:    "table_chart",
    ppt:    "slideshow",
    img:    "image",
    zip:    "folder_zip",
    txt:    "article",
    video:  "video_file",
    file:   "draft",
};

const ICON_COLOR = {
    folder: "#EF9F27",   // amber-400
    pdf:    "#E24B4A",   // red-400
    doc:    "#185FA5",   // blue-600
    xls:    "#3B6D11",   // green-600
    ppt:    "#993C1D",   // coral-600
    img:    "#1D9E75",   // teal-400
    zip:    "#888780",   // gray-400
    txt:    "#888780",
    video:  "#7F77DD",   // purple-400
    file:   "#888780",
};

function _icon_html(icon_type) {

    const name  = ICON_MAP[icon_type] || "draft";
    const color = ICON_COLOR[icon_type] || "var(--text-muted)";

    return `
        <span style="
            width:24px;
            height:24px;
            display:flex;
            align-items:center;
            justify-content:center;
        ">
            <span
                class="material-symbols-outlined"
                style="
                    font-size:20px;
                    color:${color};
                    line-height:1;
                "
            >
                ${name}
            </span>
        </span>
    `;
}


// ─────────────────────────────────────────────────────────────────────────────
// WebDAVFileBrowser  –  the dialog class
// ─────────────────────────────────────────────────────────────────────────────

erpnext_dav_integration.webdav.FileBrowser = class WebDAVFileBrowser {
    /**
     * @param {Object} opts
     * @param {string}   opts.doctype       - ERPNext doctype the file will attach to
     * @param {string}   opts.docname       - ERPNext document name
     * @param {string}   [opts.dav_account] - override; resolved server-side if omitted
     * @param {Function} [opts.on_attach]   - called with (file_doc) after copy-attach
     * @param {Function} [opts.on_link]     - called with (url, filename) after link insert
     */
    constructor(opts = {}) {
        this.doctype     = opts.doctype;
        this.docname     = opts.docname;
        this.dav_account = opts.dav_account || null;
        this.on_attach   = opts.on_attach   || (() => {});
        this.on_link     = opts.on_link     || (() => {});
        this.folder_pick = opts.folder_pick || false;
        this.file_pick   = opts.file_pick || false;
        // Navigation state
        this._path_stack = [];   // breadcrumb history: [{path, label}]
        this._current_path  = null;
        this._selected_items = [];   // {href, name, is_collection}
    }

    // ── public ───────────────────────────────────────────────────────────────

    show() {
        this._make_dialog();
        this.dialog.show();
        this._init_root();
    }

    // ── dialog construction ───────────────────────────────────────────────────

    _make_dialog() {
        this.dialog = new frappe.ui.Dialog({
            title:  __("Browse cloud storage"),
            size:   "large",
            fields: [
                {
                    fieldname:  "browser_html",
                    fieldtype:  "HTML",
                    options:    this._skeleton_html(),
                },
            ],
        });

        // Remove default primary button – we manage our own footer buttons
        this.dialog.set_primary_action(null);
        this.dialog.get_close_btn().show();

        this.$wrapper   = this.dialog.$wrapper;
        this.$breadcrumb = this.$wrapper.find(".wdav-breadcrumb");
        this.$btn_back = this.$wrapper.find(".wdav-btn-back");
        this.$file_list  = this.$wrapper.find(".wdav-file-list");
        this.$sel_bar    = this.$wrapper.find(".wdav-sel-bar");
        this.$btn_attach = this.$wrapper.find(".wdav-btn-attach");
        this.$btn_link   = this.$wrapper.find(".wdav-btn-link");
        // Footer button handlers
        this.$btn_attach.on("click", () => this._do_attach());
        this.$btn_link.on("click",   () => this._do_link());
        this.$btn_back.on("click", () => {
        if (this._path_stack.length > 1) {
            this._navigate_up_to(this._path_stack.length - 2);
        }
        });
        if (this.folder_pick) {
                this.dialog.$wrapper.find(".standard-actions").hide();
        }
        if (this.file_pick) {
                this.dialog.$wrapper.find(".standard-actions").hide();
                this.$btn_link.hide();
        }

    }

    _skeleton_html() {
        return `
            <style>
                .wdav-item:hover:not(.wdav-selected) { background-color: rgba(0,0,0,0.04) !important; }
                .wdav-item.wdav-selected             { background-color: rgba(30,100,241,0.08) !important; border-radius: 4px; }
            </style>
            <link
            rel="stylesheet"
            href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined"
            />
        <div class="wdav-browser" style="font-family:var(--font-sans,sans-serif);font-size:14px">
        <div style="display:flex;align-items:center;justify-content:space-between;
                    gap:8px;padding-bottom:6px">
            
            <button class="btn btn-default btn-sm wdav-btn-back" disabled>
                <i class="ti ti-arrow-left" style="font-size:14px"></i>
                ${__("Back")}
            </button>
        </div>
          <!-- breadcrumb -->
          <div class="wdav-breadcrumb"
               style="display:flex;align-items:center;flex-wrap:wrap;gap:4px;
                      padding:8px 0 10px;border-bottom:0.5px solid var(--color-border-tertiary);
                      margin-bottom:0;min-height:36px">
            <span style="font-size:13px;color:var(--color-text-tertiary)">${__("Loading…")}</span>
          </div>

          <!-- file list -->
          <div class="wdav-file-list"
               style="min-height:240px;max-height:340px;overflow-y:auto;
                      border-bottom:0.5px solid var(--color-border-tertiary)">
            ${this._loading_html()}
          </div>

          <!-- selection bar -->
          <div class="wdav-sel-bar"
               style="display:flex;align-items:center;gap:10px;
                      padding:10px 0;min-height:44px;
                      border-bottom:0.5px solid var(--color-border-tertiary)">
            <i class="ti ti-circle-dotted"
               style="font-size:16px;color:var(--color-text-tertiary)" aria-hidden="true"></i>
            <span style="font-size:13px;color:var(--color-text-tertiary)">${__("Select a file above")}</span>
          </div>

          <!-- action row -->
          <div style="display:flex;justify-content:flex-end;gap:8px;padding-top:12px">
            <button class="btn btn-default btn-sm wdav-btn-link" disabled
                    title="${__("Create a Nextcloud share link and insert it into the email body")}">
              <i class="ti ti-link" style="font-size:14px;vertical-align:-2px" aria-hidden="true"></i>
              ${this.file_pick ? "" : __("Insert share link")}
            </button>
            <button class="btn btn-primary btn-sm wdav-btn-attach" disabled>
              <i class="ti ti-paperclip" style="font-size:14px;vertical-align:-2px" aria-hidden="true"></i>
              ${__("Attach file")}
            </button>
          </div>

        </div>`;
    }

    // ── initialisation ────────────────────────────────────────────────────────

    _init_root() {
        console.log("Initializing WebDAV browser for account:", this.dav_account);  
        frappe.call({
            method: "erpnext_dav_integration.webdav_sync.webdav_email_api.get_root_folder",
            args: { dav_account: this.dav_account },
            callback: (r) => {
                if (r.message) {
                    const { path, dav_account } = r.message;
                    this.dav_account = dav_account;
                    this._navigate_to(path, __("Home"), true);
                } else {
                    this._show_error(__("Could not load your cloud storage account."));
                }
            },
            error: () => this._show_error(__("Connection to cloud storage failed.")),
        });
    }

    // ── navigation ────────────────────────────────────────────────────────────

    _navigate_to(path, label, is_root = false) {
        if (is_root) {
            this._path_stack = [];
        }
        this._path_stack.push({ path, label });
        this._current_path  = path;
        this._selected_item = null;

        this._render_breadcrumb();
        this._reset_selection();
        this._update_back_button();
        this._load_folder(path);
    }

    _navigate_up_to(index) {
        // index = target depth in _path_stack (0 = root)
        this._path_stack = this._path_stack.slice(0, index + 1);
        const target = this._path_stack[this._path_stack.length - 1];
        this._current_path  = target.path;
        this._selected_item = null;

        this._render_breadcrumb();
        this._reset_selection();
        this._load_folder(target.path);
    }

    // ── rendering ─────────────────────────────────────────────────────────────

    _render_breadcrumb() {
        const parts = this._path_stack.map((item, i) => {
            const is_last = i === this._path_stack.length - 1;
            if (is_last) {
                return `<span style="font-size:12px;font-weight:500;color:var(--color-text-primary)">${frappe.utils.escape_html(item.label)}</span>`;
            }
            return `
                <span class="wdav-bc-item" data-idx="${i}"
                      style="font-size:12px;color:var(--color-text-info);cursor:pointer;
                             text-decoration:underline;text-underline-offset:2px">
                    ${i === 0 ? '<i class="ti ti-home" style="font-size:12px" aria-hidden="true">All Files</i>' : frappe.utils.escape_html(item.label)} 
                </span>
                <span style="font-size:12px;color:var(--color-text-tertiary);user-select:none">/</span>`;
        });

        this.$breadcrumb.html(parts.join(""));

        // Breadcrumb click handlers
        this.$breadcrumb.find(".wdav-bc-item").on("click", (e) => {
            const idx = parseInt($(e.currentTarget).data("idx"), 10);
            this._navigate_up_to(idx);
        });
    }

    _load_folder(path) {
        this.$file_list.html(this._loading_html());
        console.log("Loading folder:", path, "for account:", this.dav_account);
        frappe.call({
            method: "erpnext_dav_integration.webdav_sync.webdav_email_api.list_folder",
            args:   { path, dav_account: this.dav_account },
            callback: (r) => {
                if (Array.isArray(r.message)) {
                    this._render_items(r.message);
                } else {
                    this._show_error(__("Failed to list folder contents."));
                }
            },
            error: () => this._show_error(__("Could not reach the cloud storage server.")),
        });
    }

    _render_items(items) {
        if (!items.length) {
            this.$file_list.html(`
                <div style="padding:40px 16px;text-align:center;color:var(--color-text-tertiary);font-size:13px">
                    <i class="ti ti-folder-off" style="font-size:24px;display:block;margin-bottom:8px" aria-hidden="true"></i>
                    ${__("This folder is empty")}
                </div>`);
            return;
        }

        const rows = items.map((item) => {
            const is_folder = item.is_collection;
            const meta = is_folder
                ? `<span style="font-size:11px;color:var(--color-text-tertiary)">${__("folder")}</span>`
                : `<span style="font-size:11px;color:var(--color-text-tertiary)">${frappe.utils.escape_html(item.size_display)}</span>`;

            return `
            <div class="wdav-item"
                 data-href="${frappe.utils.escape_html(item.href)}"
                 data-name="${frappe.utils.escape_html(item.name)}"
                 data-id="${frappe.utils.escape_html(item.id)}"
                 data-is-folder="${is_folder ? "1" : "0"}"
                 style="display:flex;align-items:center;gap:10px;
                        padding:9px 4px;
                        border-bottom:0.5px solid var(--color-border-tertiary);
                        cursor:pointer;border-radius:4px;transition:background 0.1s">
                <span style="width:22px;text-align:center;flex-shrink:0">${_icon_html(item.icon_type)}</span>
                <span style="flex:1;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                    ${frappe.utils.escape_html(item.name)}
                </span>
                ${meta}
                ${is_folder
                    ? `<i class="ti ti-chevron-right" style="font-size:14px;color:var(--color-text-tertiary)" aria-hidden="true"></i>`
                    : ""}
            </div>`;
        });

        this.$file_list.html(rows.join(""));

        // Item click handler
        this.$file_list.find(".wdav-item").on({
            click: (e) => {
        const $row      = $(e.currentTarget);
        const href      = $row.data("href");
        const name      = $row.data("name");
        const id        = $row.data("id");
        const is_folder = $row.data("is-folder") === "1" || $row.data("is-folder") === 1;

        if (is_folder) {

        if (this.folder_pick) {

            this.$file_list.find(".wdav-item")
                .removeClass("wdav-selected");

            $row.addClass("wdav-selected");

            this._selected_items = [{
                href,
                name,
                id,
                is_folder: true,
                is_collection: true,
            }];

            this._update_sel_bar();

        } else {
            this._navigate_to(href, name);
        }

        } else if (!this.folder_pick) {
        
            this._select_item(
                $row,
                { href, name, id },
                e.ctrlKey || e.metaKey
            );
        }
    },
    dblclick: (e) => {
        const $row = $(e.currentTarget);

        const is_folder =
            $row.data("is-folder") === "1" ||
            $row.data("is-folder") === 1;

        if (is_folder) {
            this._navigate_to(
                $row.data("href"),
                $row.data("name")
            );
        }
        }
        });
    }

    // ── selection ─────────────────────────────────────────────────────────────

    _select_item($row, item, is_ctrl) {
        if (is_ctrl) {
            // Toggle this item
            const idx = this._selected_items.findIndex(i => i.href === item.href);
            if (idx > -1) {
                this._selected_items.splice(idx, 1);
                $row.removeClass("wdav-selected");
            } else {
                this._selected_items.push(item);
                $row.addClass("wdav-selected");
            }
        } else {
            // Single select — clear previous
            this.$file_list.find(".wdav-item").removeClass("wdav-selected");
            this._selected_items = [item];
            $row.addClass("wdav-selected");
        }

        this._update_sel_bar();
    }
    _update_sel_bar() {
        if (this.folder_pick) {
            const item = this._selected_items[0];
            if (item) {
                this.$sel_bar.html(`
                    <i class="ti ti-folder"
                       style="font-size:16px;color:var(--color-text-info);flex-shrink:0" aria-hidden="true"></i>
                    <span style="font-size:13px;font-weight:500;flex:1;
                                 white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                        ${frappe.utils.escape_html(item.name)}
                    </span>
                `);
                this.$btn_attach.prop("disabled", false);
            } else {
                this.$btn_attach.prop("disabled", true);
            }
            return;
        }
        if (this.file_pick) {
            const item = this._selected_items[0];
            if (item) {
                this.$sel_bar.html(`
                    <i class="ti ti-file"
                       style="font-size:16px;color:var(--color-text-info);flex-shrink:0" aria-hidden="true"></i>
                    <span style="font-size:13px;font-weight:500;flex:1;
                                 white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                        ${frappe.utils.escape_html(item.name)}
                    </span>
                `);
                this.$btn_attach.prop("disabled", false);
            } else {
                this.$btn_attach.prop("disabled", true);
            }
            return;
        }
        const count = this._selected_items.length;
        
        if (count === 0) {
            this._reset_selection();
            return;
        }
    
        const label = count === 1
            ? frappe.utils.escape_html(this._selected_items[0].name)
            : __("{0} files selected", [count]);
    
        this.$sel_bar.html(`
            <i class="ti ti-paperclip"
               style="font-size:16px;color:var(--color-text-info);flex-shrink:0" aria-hidden="true"></i>
            <span style="font-size:13px;font-weight:500;flex:1;
                         white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                ${label}
            </span>
            <span style="font-size:12px;color:var(--color-text-secondary)">
                ${__("Ready to attach or share")}
            </span>`);
        
        this.$btn_attach.prop("disabled", false);
        this.$btn_link.prop("disabled", false);
    }

    _reset_selection() {
    this._selected_items = [];
    this.$file_list.find(".wdav-item").removeClass("wdav-selected");

    if (this.folder_pick) {
        this.$sel_bar.html(`
            <i class="ti ti-circle-dotted"
               style="font-size:16px;color:var(--color-text-tertiary)" aria-hidden="true"></i>
            <span style="font-size:13px;color:var(--color-text-tertiary)">${__("Click a folder to select it \u00b7 Double-click to open it")}</span>`);
    } else {
        this.$sel_bar.html(`
            <i class="ti ti-circle-dotted"
               style="font-size:16px;color:var(--color-text-tertiary)" aria-hidden="true"></i>
            <span style="font-size:13px;color:var(--color-text-tertiary)">${__("Select a file above")}</span>`);
    }

    this.$btn_attach.prop("disabled", true);
    this.$btn_link.prop("disabled", true);
}

    // ── actions ───────────────────────────────────────────────────────────────

    _do_attach() {
        if (this.folder_pick) {

            const folder = this._selected_items[0];

            if (!folder) {
                return;
            }

            this.on_attach({
                href: folder.href,
                name: folder.name,
                id: folder.id,
                is_folder: true
            });

            this.dialog.hide();
            return;
        }
        if (this.file_pick) {

            const file = this._selected_items[0];

            if (!file) {
                return;
            }

            this.on_attach({
                href: file.href,
                name: file.name,
                id: file.id,
                is_folder: false
            });

            this.dialog.hide();
            return;
        }
        if (!this._selected_items.length) return;

        const items = [...this._selected_items];
        let completed = 0;

        this._set_loading_state(__("Attaching {0} file(s)…", [items.length]));

        items.forEach(item => {
            frappe.call({
                method:  "erpnext_dav_integration.webdav_sync.webdav_email_api.attach_webdav_file",
                args: {
                    cloud_path:  item.href,
                    doctype:     this.doctype,
                    docname:     this.docname,
                    dav_account: this.dav_account,
                    is_private:  1,
                },
                callback: (r) => {
                    if (r.message) {
                        this.on_attach(r.message);
                        completed++;
                        if (completed === items.length) {
                            this._set_success_state(__("{0} file(s) attached.", [completed]));
                            setTimeout(() => this.dialog.hide(), 1200);
                        }
                    } else {
                        this._set_error_state(__("Attachment failed for {0}.", [item.name]));
                    }
                },
                error: () => this._set_error_state(__("Server error attaching {0}.", [item.name])),
            });
        });
    }

    _do_link() {
        if (!this._selected_items.length) return;

        frappe.prompt(
            [
                {
                    fieldname:  "expire_date",
                    fieldtype:  "Date",
                    label:      __("Expiry Date"),
                    description: __("Leave blank for default expiry"),
                    min_date: frappe.datetime.str_to_obj(frappe.datetime.now_date())
                },
            ],
            (values) => {
                this._create_share_links(values.expire_date || null);
            },
            __("Set Link Expiry"),
            __("Create Link")
        );
    }

    _create_share_links(expire_date) {
        const items = [...this._selected_items];
        let completed = 0;

        this._set_loading_state(__("Creating share links…"));

        items.forEach(item => {
            frappe.call({
                method:  "erpnext_dav_integration.webdav_sync.webdav_email_api.create_share_link",
                args: {
                    cloud_path:  item.href,
                    dav_account: this.dav_account,
                    expire_date: expire_date,   // null if not set
                },
                callback: (r) => {
                    if (r.message && r.message.url) {
                        this.on_link(r.message.url, item.name);
                        completed++;
                        if (completed === items.length) {
                            this._set_success_state(__("{0} link(s) inserted.", [completed]));
                            setTimeout(() => this.dialog.hide(), 1200);
                        }
                    } else {
                        this._set_error_state(__("Could not create share link for {0}.", [item.name]));
                    }
                },
                error: () => this._set_error_state(__("Server error for {0}.", [item.name])),
            });
        });
    }
    // ── status helpers ────────────────────────────────────────────────────────

    _loading_html() {
        return `
            <div style="display:flex;align-items:center;justify-content:center;
                        padding:40px;color:var(--color-text-tertiary);gap:8px;font-size:13px">
                <i class="ti ti-loader-2" style="font-size:20px" aria-hidden="true"></i>
                ${__("Loading…")}
            </div>`;
    }

    _set_loading_state(message) {
        this.$sel_bar.html(`
            <i class="ti ti-loader-2" style="font-size:16px;color:var(--color-text-info)" aria-hidden="true"></i>
            <span style="font-size:13px;color:var(--color-text-secondary)">${frappe.utils.escape_html(message)}</span>`);
        this.$btn_attach.prop("disabled", true);
        this.$btn_link.prop("disabled", true);
    }

    _set_success_state(message) {
        this.$sel_bar.html(`
            <i class="ti ti-circle-check" style="font-size:16px;color:var(--color-text-success)" aria-hidden="true"></i>
            <span style="font-size:13px;color:var(--color-text-success)">${frappe.utils.escape_html(message)}</span>`);
    }

    _set_error_state(message) {
        this.$sel_bar.html(`
            <i class="ti ti-alert-triangle" style="font-size:16px;color:var(--color-text-danger)" aria-hidden="true"></i>
            <span style="font-size:13px;color:var(--color-text-danger)">${frappe.utils.escape_html(message)}</span>`);
        this.$btn_attach.prop("disabled", false);
        this.$btn_link.prop("disabled", false);
    }

    _show_error(message) {
        this.$file_list.html(`
            <div style="padding:40px 16px;text-align:center;color:var(--color-text-danger);font-size:13px">
                <i class="ti ti-alert-circle" style="font-size:24px;display:block;margin-bottom:8px" aria-hidden="true"></i>
                ${frappe.utils.escape_html(message)}
            </div>`);
    }
    _update_back_button() {
    this.$btn_back.prop("disabled", this._path_stack.length <= 1);
    }
    _get_preview_html(item) {

    const is_image =
        ["jpg", "jpeg", "png", "gif", "webp", "bmp", "svg"]
            .includes((item.name || "").split(".").pop()?.toLowerCase());
    console.log("Checking if item is image for preview:", item.name, "→", is_image, "preview_url:", item.preview_url,"icon_type:", item.icon_type);
    if (is_image && item.preview_url) {
        return `
            <img
                src="${frappe.utils.escape_html(item.preview_url)}"
                alt="${frappe.utils.escape_html(item.name)}"
                style="
                    width:32px;
                    height:32px;
                    object-fit:cover;
                    border-radius:6px;
                    border:1px solid var(--color-border-secondary);
                    flex-shrink:0;
                    background:var(--color-bg-light);
                "
            />
        `;
    }

    return `
        <span style="width:22px;text-align:center;flex-shrink:0">
            ${_icon_html(item.icon_type)}
        </span>
    `;
    }
};

frappe.after_ajax(() => {

    if (!frappe.views?.CommunicationComposer) {
        return;
    }

    // Prevent double patching
    if (frappe.views.CommunicationComposer.__dav_extended) {
        return;
    }

    frappe.views.CommunicationComposer.__dav_extended = true;

    const Composer = frappe.views.CommunicationComposer;

    // Preserve original method
    const original_setup_attach = Composer.prototype.setup_attach;

    // Extend setup_attach
    Composer.prototype.setup_attach = function () {

        // Run original logic first
        original_setup_attach.call(this);

        const wrapper = $(this.dialog.fields_dict.select_attachments.wrapper);

        // Prevent duplicate button
        if (wrapper.find(".btn-cloud-storage").length) {
            return;
        }

        // Original Add Attachment button
        const $add_btn = wrapper.find(".add-more-attachments button");

        if (!$add_btn.length) {
            console.warn("Add attachment button not found");
            return;
        }

        // Create Cloud button
        const $cloud_btn = $(`
            <button
                type="button"
                class="btn btn-xs btn-default btn-cloud-storage"
                style="margin-left: 6px;"
            >
                ${frappe.utils.icon("folder-normal", "xs")}
                ${__("Cloud Storage")}
            </button>
        `);

        // Insert beside Add Attachment
        $add_btn.after($cloud_btn);

        // Click handler
        $cloud_btn.on("click", () => {

            const composer = this;

            const doctype = composer.doc?.doctype || "Communication";
            const docname = composer.doc?.name || "New Communication";

            const browser = new erpnext_dav_integration.webdav.FileBrowser({
                doctype,
                docname,

                // Attach file physically
                on_attach(file_doc) {

                    composer.attachments = composer.attachments || [];

                    const attachment = {
                        name: file_doc.file,
                        file_name: file_doc.file_name,
                        file_url: frappe.urllib.get_full_url(file_doc.file_url),
                    };

                    composer.attachments.push(attachment);

                    composer.render_attachment_rows(attachment);

                    frappe.show_alert({
                        message: __("{0} attached", [file_doc.file_name]),
                        indicator: "green",
                    });
                },

                // Insert public share link
                on_link(url, filename) {

                    const field = composer.dialog.fields_dict.content;

                    if (!field?.quill) {

                        // Fallback
                        const current = composer.get_email_content() || "";

                        composer.set_email_content(
                            current + `<p><a href="${url}">${filename}</a></p>`
                        );

                        return;
                    }

                    const quill = field.quill;

                    const index = quill.getLength() - 1;

                    quill.insertText(index, filename, "link", url);
                    quill.insertText(index + filename.length, "\n");
                },
            });

            browser.show();
        });
    };

});