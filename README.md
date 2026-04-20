ERPNext DAV Integration Manual
Table of Contents
Overview
Features
Prerequisites
Installation
Configuration
User Guide
Limitations
1. Overview

The ERPNext DAV Integration app connects your ERPNext instance with WebDAV servers (such as Nextcloud, ownCloud, or other compatible providers). It enables seamless synchronization of contacts using the CardDAV protocol.

What is DAV?

DAV (Distributed Authoring and Versioning) is an extension of HTTP that allows collaborative content management.

This integration primarily uses:

CardDAV → Contact (vCard) synchronization
Publisher
Organization: b»robotized group
Email: arun.govind@cloudconverge.io
License: AGPL-3.0
2. Features
Core Capabilities
Contact synchronization between ERPNext and CardDAV
Multi-address book support
Automatic address book discovery
Real-time sync on contact updates
Contact deletion sync
Scheduled synchronization
Address book management
Rich contact field support
Supported Contact Attributes
Full name
Email addresses
Phone numbers
Mobile numbers
Website
Social media profiles
Address details
3. Prerequisites
System Requirements
ERPNext / Frappe v16.0.0+
Python 3.10+
Bench environment installed
DAV Server Requirements
Supported servers (e.g., Nextcloud v20+)
Credentials:
Base URL
Username
Password / App Password
CardDAV endpoint access
Knowledge Requirements
Basic ERPNext usage
Understanding of Contacts
Access to DAV server configuration
4. Installation
Step 1: Install via Bench
cd /path/to/frappe-bench

bench get-app https://github.com/b-robotized/erpnext_dav_integration.git
bench --site your-site.com install-app erpnext_dav_integration
bench --site your-site.com migrate
Step 2: Verify Installation
Go to ERPNext
Search “DAV Account”
Confirm list view is available
Step 3: Enable Scheduler
bench --site your-site.com show-config scheduler
bench restart
Step 4: Clear Cache
bench --site your-site.com clear-cache
5. Configuration
Creating a DAV Account
Step 1: Access Form
Open ERPNext
Search DAV Account
Click New
Step 2: Fill Details
Field	Description	Example
Account Name	Unique name	Nextcloud Main
Base URL	DAV server URL	https://nextcloud.example.com

Username	Login username	john.doe
App Password	Secure password	••••••••
Default Address Book URL	Base CardDAV path	/remote.php/dav/addressbooks
Step 3: Discover Address Books
Click Update Address Book List
Select default address book
Contact Mapping
Identity Fields
ERPNext	vCard
First Name	N (given)
Last Name	N (family)
Full Name	FN
UID	UID
Organization
ERPNext	vCard
Company	ORG
Department	ORG
Designation	TITLE
Contact Info
ERPNext	vCard
Email	EMAIL
Phone	TEL
Website	URL
Address
ERPNext	vCard
Address	ADR
Personal
ERPNext	vCard
DOB	BDAY
Gender	GENDER
Photo	PHOTO
Social / Custom
ERPNext	vCard
Social Profiles	X-SOCIALPROFILE
Manager	X-MANAGERSNAME
Languages	LANG
Metadata
ERPNext	vCard
Notes	NOTE
Modified	REV
6. User Guide
Manual Sync
Open Contact
Edit details
Save
→ Automatically synced
Scheduled Sync
00:00 → Full sync
00:15 → Incremental sync + deletion
Social Profiles
Add in Contact Social Profile table
Automatically included in sync
Multiple Address Books
Select DAV Address Book in Contact
Save to sync into specific book
Deleting Contacts
Deleting in ERPNext removes from DAV (if permitted)
7. Limitations
7.1 No Strict Deduplication

The integration does not enforce strict duplicate prevention.

Reason
Sync relies on:
vCard UID
vCard URL
External systems may have:
Missing UID
Duplicate entries
Inconsistent identifiers
Technical Constraint

Preventing duplicates would require modifying:

UID
Email
vCard structure
Why Not Done
Alters original DAV data
Breaks bi-directional sync
Risks overwriting external records
Result
Duplicate contacts may occur
Especially during:
Initial sync
Back-sync from DAV
Systems with poor UID handling
7.2 Image Sync Limitations
Behavior
Images are imported from DAV → ERPNext
Stored as Base64
Limitations
Large images may:
Exceed field size
Fail to store
ERPNext → DAV image sync:
❌ Not supported
7.3 DAV Permission Constraints
Problem

Operations depend on DAV account permissions.

Affected Operations
Updating contacts
Deleting contacts
Errors
403 Forbidden
401 Unauthorized
Impact
Partial sync failures
Data inconsistency
Requirement

Full permissions needed:

Read
Write
Delete
Result

Without proper access:

Sync integrity cannot be guaranteed
Errors must be handled/logged