# ERPNext DAV Integration Manual

## Table of Contents
1. [Overview](#overview)
2. [Features](#features)
3. [Prerequisites](#prerequisites)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [User Guide](#user-guide)



---

## Overview

The **ERPNext DAV Integration** app connects your ERPNext site with WebDAV servers (such as Nextcloud, ownCloud, or any compatible WebDAV server). This integration enables seamless synchronization of contacts between ERPNext and external DAV systems through the CardDAV protocol.

### What is DAV?
DAV (Distributed Authoring and Versioning) is a protocol that extends HTTP for collaborative editing. This app primarily uses:
- **CardDAV**: For contact (vCard) synchronization

### Publisher
- **Published by**: b»robotized group
- **Email**: arun.govind@cloudconverge.io
- **License**: AGPL-3.0

---

## Features

### Core Capabilities
- ✅ **Contact Synchronization**: Automatically sync ERPNext contacts to CardDAV address books
- ✅ **Multi-Address Book Support**: Configure multiple address books per DAV account
- ✅ **Automatic Discovery**: Auto-detect available address books from your DAV server
- ✅ **Real-time Sync**: Push updates to CardDAV whenever contacts are modified in ERPNext
- ✅ **Contact Deletion**: Remove contacts from CardDAV when deleted from ERPNext
- ✅ **Scheduled Synchronization**: Automatic sync at configurable intervals
- ✅ **Address Book Management**: Manage multiple CardDAV address books per account
- ✅ **Contact Details Support**: Store contact information including name, email, phone, and social profiles

### Supported Contact Attributes
- Full name
- Email addresses
- Phone numbers
- Mobile numbers
- Website
- Social media profiles
- Address information

---

## Prerequisites

Before installing the ERPNext DAV Integration app, ensure you have:

### System Requirements
1. **ERPNext / Frappe v16.0.0 or later**
   - Verify with: `bench version`
   
2. **Python 3.10 or higher**
   - Verify with: `python3 --version`

3. **Bench Environment**
   - Must be installed via Frappe Bench
   - Access to command line in the bench directory

### DAV Server Requirements
1. **Supported DAV Servers**
   - Nextcloud (recommended) v20+

   
2. **DAV Account Credentials**
   - Base URL of your DAV server (e.g., `https://nextcloud.example.com`)
   - Username
   - Password or App Password (App Password recommended for security)
   - Access to CardDAV endpoints


### Knowledge Requirements
- Basic familiarity with ERPNext
- Understanding of contacts in ERPNext
- Access to your DAV server administration

---

## Installation

### Step 1: Install the App via Bench

```bash
# Navigate to your bench directory
cd /path/to/frappe-bench

# Install the app
bench get-app https://github.com/b-robotized/erpnext_dav_integration.git

# Install the app on your site
bench --site your-site.com install-app erpnext_dav_integration

# Migrate the database
bench --site your-site.com migrate
```

### Step 2: Verify Installation

1. Navigate to your ERPNext site
2. Go to **Home** → **Awesome Bar** → Search for "DAV Account"
3. You should see the DAV Account list view

### Step 3: Enable Scheduler (Important)

The app relies on scheduled jobs for synchronization. Ensure the scheduler is enabled:

```bash
# Check if scheduler is running
bench --site your-site.com show-config scheduler

# If not running, restart
bench restart
```

### Step 4: Clear Cache

```bash
bench --site your-site.com clear-cache
```

---

## Configuration

### Creating a DAV Account

#### Step 1: Access DAV Account Form

1. Open your ERPNext site
2. Search for **"DAV Account"** in the Awesome Bar
3. Click **+ New**

#### Step 2: Fill in Account Details

| Field | Description | Example |
|-------|-------------|---------|
| **Account Name** | Unique identifier for this DAV account | `Nextcloud Main` |
| **Base URL** | Root URL of your DAV server | `https://nextcloud.example.com` |
| **Username** | Login username for the DAV server | `john.doe` |
| **App Password** | Password or App Password (encrypted) | `••••••••` |
| **Default Address Book URL** | Default path to address books | `/remote.php/dav/addressbooks` |

#### Step 3: Configure Address Books

1. After saving, click **Update Address Book List** to discover available address books
2. The app will fetch all CardDAV address books from your server
3. Each discovered address book appears in the **DAV Address Books** table:
   - **Address Book Name**: Display name
   - **URL**: Full path to the address book
   - **Is Default**: Mark one as default for primary syncs

#### Example Configuration for Nextcloud

```
Account Name: Nextcloud Production
Base URL: https://nextcloud.company.com
Username: erp_sync_user
App Password: [Generated from Nextcloud admin panel]
Default Address Book URL: /remote.php/dav/addressbooks
```

**To create an App Password in Nextcloud:**
1. Login to Nextcloud
2. Click your profile → Settings
3. Navigate to "Personal" → "Security"
4. Under "App passwords", give it a name and click "Generate"
5. Copy the generated password and paste into ERPNext

### Contact Mapping

Contacts in ERPNext are automatically synced to CardDAV with the following comprehensive mapping:

#### Name & Identity Fields

| ERPNext Field | CardDAV Field | vCard Property | Notes |
|--------------|---------------|--------|-------|
| First Name | Given Name | N (given) | Extracted from N.value.given |
| Last Name | Family Name | N (family) | Extracted from N.value.family |
| Middle Name | Additional Name | N (additional) | Extracted from N.value.additional |
| Full Name | Formatted Name | FN | Complete display name |
| UID | Unique ID | UID | Auto-generated UUID for vCard identification |

#### Organization & Position

| ERPNext Field | CardDAV Field | vCard Property | Notes |
|--------------|---------------|--------|-------|
| Company Name | Organization | ORG (first component) | Extracted from ORG value[0] |
| Department | Organizational Unit | ORG (second component) | Extracted from ORG value[1] |
| Designation | Job Title | TITLE | Professional title/position |

#### Contact Information

| ERPNext Field | CardDAV Field | vCard Property | Notes |
|--------------|---------------|--------|-------|
| Email IDs | Email Address | EMAIL | Multiple emails supported with type (INTERNET, WORK, HOME, OTHER) |
| Phone Numbers | Telephone | TEL | Multiple phones supported with type classification |
| Website | URL | URL | Homepage or website link |

#### Physical Location

| ERPNext Field | CardDAV Field | vCard Property | Notes |
|--------------|---------------|--------|-------|
| Addresses | Address | ADR | Multiple addresses supported: street, city, region, postal code, country |

#### Personal Information

| ERPNext Field | CardDAV Field | vCard Property | Notes |
|--------------|---------------|--------|-------|
| Date of Birth | Birthday | BDAY | Birth date in YYYY-MM-DD format |
| Gender | Gender | GENDER | Gender code (M/F/O/U) |
| Image/Photo | Photo | PHOTO | Base64-encoded image (PNG/JPEG) with ENCODING=b and TYPE specification |

#### Professional & Social

| ERPNext Field | CardDAV Field | vCard Property | Notes |
|--------------|---------------|--------|-------|
| Social Profiles | Social Profile | X-SOCIALPROFILE | Multiple profiles: Facebook, Twitter, LinkedIn, Instagram, GitHub, etc. (custom extension) |
| Manager Name | Manager | X-MANAGERSNAME | Manager's name (custom extension) |
| Spoken Languages | Language | LANG | Multiple languages separated by comma (custom extension) |

#### Metadata & System Fields

| ERPNext Field | CardDAV Field | vCard Property | Notes |
|--------------|---------------|--------|-------|
| Notes | Comment/Notes | NOTE | Additional notes or comments |
| Modified Date | Revision | REV | Last modification timestamp (ISO 8601 format) |
| vCard Text | - | N/A | Raw vCard content stored in cr_vcard_text field |

## User Guide

### Adding and Syncing Contacts

#### Manual Sync (Immediate)

1. Open a **Contact** in ERPNext
2. Edit the contact details (name, email, phone, etc.)
3. Click **Save**
4. The app automatically syncs the contact to your configured CardDAV account

#### Automatic Sync (Scheduled)

The app includes automatic synchronization:
- **Daily Full Sync**: Every day at 00:00 (midnight) - updates all address book lists
- **Periodic Sync**: Every day 00:15 (midnight) - synchronizes pending changes
- **Deletion Cleanup**: Every 00:15 (midnight) - removes deleted contacts from CardDAV

### Creating Contacts with Social Profiles

The app supports storing social media profiles:

1. Open or create a **Contact**
2. In the **Contact Social Profile** table, add social media links:
   - Facebook
   - Twitter
   - LinkedIn
   - Instagram
   - GitHub
   - etc.
3. Save the contact
4. Social profiles are included in CardDAV sync

### Managing Multiple Address Books

If your DAV account has multiple address books:

1. Open the **Contact** form
2. Look for the **DAV Address Book** field
3. Select which address book to sync this contact to
4. Save the contact

### Deleting Contacts

When you delete a contact from ERPNext:

1. The contact is automatically deleted from the CardDAV address book
2. After synchronization, the contact is permanently deleted from ERPNext

### Limitation 

Cannot Enforce Deduplication Without Data Mutation

To truly prevent duplicates, we would need to:

Normalize or rewrite:
UID
Emails
Structure

But:

❌ Modifying vCard → changes remote record
❌ Not acceptable in bi-directional sync