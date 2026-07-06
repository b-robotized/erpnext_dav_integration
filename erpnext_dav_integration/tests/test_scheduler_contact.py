from unittest.mock import MagicMock, patch

from frappe.tests import IntegrationTestCase

from erpnext_dav_integration.scheduler import contact as contact_scheduler


class TestCardDAVContactSync(IntegrationTestCase):
	@patch("erpnext_dav_integration.scheduler.contact.parse_vcards_with_href")
	@patch("erpnext_dav_integration.scheduler.contact.parse_addressbooks")
	@patch("erpnext_dav_integration.scheduler.contact.requests.request")
	def test_fetch_vcards_uses_dav_account_filter(
		self, mock_request, mock_parse_addressbooks, mock_parse_vcards_with_href
	):
		class DummyResponse:
			def __init__(self, status_code=207, text=""):
				self.status_code = status_code
				self.text = text

		mock_request.side_effect = [DummyResponse(207, "<xml/"), DummyResponse(207, "<xml/>")]
		mock_parse_addressbooks.return_value = [
			{"href": "/addressbooks/user", "displayname": "My Book"}
		]
		mock_parse_vcards_with_href.return_value = [
			{"href": "/card.vcf", "vcard": "BEGIN:VCARD\r\nFN:Jane\r\nEND:VCARD"}
		]

		mock_get_all = MagicMock(
			return_value=[
				{
					"dav_addressbook": "My Book",
					"dav_addressbook_url": "https://example.com/addressbooks/user",
				}
			]
		)

		with patch("erpnext_dav_integration.scheduler.contact.frappe.db.get_all", mock_get_all):
			entries = contact_scheduler.fetch_vcards_from_carddav(
				"https://example.com", "user", "pass", "dav-account"
			)

		self.assertEqual(len(entries), 1)
		self.assertEqual(entries[0]["address_book"], "My Book")
		mock_get_all.assert_called_once_with(
			"DAV AddressBook",
			filters={"dav_account": "dav-account"},
			fields=["dav_addressbook", "dav_addressbook_url"],
		)
