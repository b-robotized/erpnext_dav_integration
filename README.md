
# ERPNext DAV Integration Manual (Contacts & Calendar)

## Table of Contents
- [Overview](#overview)
- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [User Guide](#user-guide)
- [Limitations](#limitations)

**Note:** This integration supports both CardDAV (contacts) and CalDAV (calendar events) synchronization.

## 1. Overview

The ERPNext DAV Integration app connects your ERPNext instance with WebDAV servers (such as Nextcloud, ownCloud, or other compatible providers). It enables seamless synchronization of contacts and calendar events using industry-standard protocols.

### What is DAV?

DAV (Distributed Authoring and Versioning) is an extension of HTTP that allows collaborative content management.

This integration supports:
- **CardDAV** → Contact (vCard) synchronization
- **CalDAV** → Calendar (iCalendar) event synchronization

### Publisher
- **Organization:** b-robotized group
- **Email:** arun.govind@cloudconverge.io
- **License:** AGPL-3.0

---

## 2. Features

### Core Capabilities
- Contact synchronization between ERPNext and CardDAV
- Calendar event synchronization between ERPNext and CalDAV
- Multi-address book support
- Multi-calendar support
- Automatic address book discovery
- Automatic calendar discovery
- Real-time sync on contact and event updates
- Contact and event deletion sync
- Scheduled synchronization
- Address book and calendar management
- Rich contact and event field support

### Supported Contact Attributes
- Full name
- Email addresses
- Phone numbers
- Mobile numbers
- Website
- Social media profiles
- Address details

### Supported Calendar Attributes
- Event title
- Event description
- Start and end times
- Recurrence rules
- Attendees
- Location
- Event status

---

## 3. Prerequisites

### System Requirements
- ERPNext / Frappe v16.0.0+
- Python 3.10+
- Bench environment installed

### DAV Server Requirements
- Supported servers (e.g., Nextcloud v20+)
- **Credentials:**
  - Base URL
  - Username
  - Password / App Password
- CardDAV endpoint access
- CalDAV endpoint access

### Knowledge Requirements
- Basic ERPNext usage
- Understanding of Contacts
- Access to DAV server configuration

---

## 4. Installation

### Step 1: Install via Bench
```bash
cd /path/to/frappe-bench
bench get-app https://github.com/b-robotized/erpnext_dav_integration.git
bench --site your-site.com install-app erpnext_dav_integration
bench --site your-site.com migrate
```

### Step 2: Verify Installation
1. Go to ERPNext
2. Search "DAV Account"
3. Confirm list view is available

### Step 3: Enable Scheduler
```bash
bench --site your-site.com show-config scheduler
bench restart
```

### Step 4: Clear Cache
```bash
bench --site your-site.com clear-cache
```

---

## 5. Configuration

### Creating a DAV Account

#### Step 1: Access Form
1. Open ERPNext
2. Search "DAV Account"
3. Click New

#### Step 2: Fill Details

| Field | Description | Example |
|-------|-------------|---------|
| Account Name | Unique name | Nextcloud Main |
| Base URL | DAV server URL | https://nextcloud.example.com |
| Username | Login username | john.doe |
| App Password | Secure password | •••••••• |
| Default Address Book URL | Base CardDAV path | /remote.php/dav/addressbooks |

#### Step 3: Discover Address Books
1. Click **Update Address Book List**
2. Select default address book

### Creating a DAV Calendar

#### Step 1: Access DAV Account
1. Open the DAV Account created above
2. Navigate to Calendar section

#### Step 2: Discover Calendars
1. Click **Update Calendar List**
2. Select default calendar

### Contact Mapping

#### Identity Fields
| ERPNext | vCard |
|---------|-------|
| First Name | N (given) |
| Last Name | N (family) |
| Full Name | FN |
| UID | UID |

#### Organization
| ERPNext | vCard |
|---------|-------|
| Company | ORG |
| Department | ORG |
| Designation | TITLE |

#### Contact Info
| ERPNext | vCard |
|---------|-------|
| Email | EMAIL |
| Phone | TEL |
| Website | URL |

#### Address
| ERPNext | vCard |
|---------|-------|
| Address | ADR |

#### Personal
| ERPNext | vCard |
|---------|-------|
| DOB | BDAY |
| Gender | GENDER |
| Photo | PHOTO |

#### Social / Custom
| ERPNext | vCard |
|---------|-------|
| Social Profiles | X-SOCIALPROFILE |
| Manager | X-MANAGERSNAME |
| Languages | LANG |

#### Metadata
| ERPNext | vCard |
|---------|-------|
| Notes | NOTE |
| Modified | REV |

### Event Mapping (CalDAV)

#### Event Details
| ERPNext | iCalendar |
|---------|----------|
| Event Title | SUMMARY |
| Description | DESCRIPTION |
| Start Date/Time | DTSTART |
| End Date/Time | DTEND |
| Location | LOCATION |
| Status | STATUS |
| UID | UID |

#### Recurrence
| ERPNext | iCalendar |
|---------|----------|
| Repeat Frequency | RRULE |
| Repeat Until | UNTIL |
| Repeat Every N | INTERVAL |

#### Attendees
| ERPNext | iCalendar |
|---------|----------|
| Attendees | ATTENDEE |

---

## 6. User Guide

### Contact Synchronization

#### Manual Sync
1. Open Contact
2. Edit details
3. Save
4. → Automatically synced

#### Social Profiles
1. Add in Contact Social Profile table
2. Automatically included in sync

#### Multiple Address Books
1. Select DAV Address Book in Contact
2. Save to sync into specific book

#### Deleting Contacts
- Deleting in ERPNext removes from DAV (if permitted)

### Calendar Event Synchronization

#### Manual Sync
1. Open Event
2. Edit details (title, start/end time, attendees, etc.)
3. Save
4. → Automatically synced to CalDAV

#### Select Calendar
1. Assign event to DAV Calendar in Event form
2. Save to sync into specific calendar

#### Deleting Events
- Deleting in ERPNext removes from DAV calendar (if permitted)

### Scheduled Sync
- **00:00** → Full sync (contacts & events)
- **00:15** → Incremental sync + deletion (contacts & events)

---

## 7. Limitations

### 7.1 No Strict Deduplication

The integration does not enforce strict duplicate prevention.

#### Reason
Sync relies on:
- vCard UID
- vCard URL

External systems may have:
- Missing UID
- Duplicate entries
- Inconsistent identifiers

#### Technical Constraint

Preventing duplicates would require modifying:
- UID
- Email
- vCard structure

#### Why Not Done
- Alters original DAV data
- Breaks bi-directional sync
- Risks overwriting external records

#### Result
- Duplicate contacts may occur
- Especially during:
  - Initial sync
  - Back-sync from DAV
  - Systems with poor UID handling

### 7.2 Image Sync Limitations

#### Behavior
- Images are imported from DAV → ERPNext
- Stored as Base64

#### Limitations
- Large images may:
  - Exceed field size
  - Fail to store
- ERPNext → DAV image sync: ❌ Not supported

### 7.4 Event Timezone Handling

#### Behavior
- Events are synced with timezone information
- Times are converted to UTC for storage

#### Limitations
- Timezone conversions depend on DAV server support
- All-day events sync as UTC times
- Recurring events follow iCalendar RRULE standards

### 7.5 Attendee Sync Limitations

#### Behavior
- Attendee email addresses are synced
- Attendee status (accepted/declined) may not sync bi-directionally

#### Limitations
- Some servers don't support full attendee management
- Response tracking may be limited
- RSVP functionality depends on DAV server capabilities

### 7.3 DAV Permission Constraints

#### Problem
Operations depend on DAV account permissions.

#### Affected Operations
- Updating contacts
- Deleting contacts
- Updating events
- Deleting events

#### Errors
- 403 Forbidden
- 401 Unauthorized

#### Impact
- Partial sync failures
- Data inconsistency

#### Requirement
Full permissions needed:
- Read
- Write
- Delete

#### Result
Without proper access:
- Sync integrity cannot be guaranteed
- Errors must be handled/logged
