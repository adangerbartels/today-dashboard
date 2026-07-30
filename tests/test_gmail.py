"""Gmail lane: unread that wants a human, mass mail excluded."""

import unittest

from today_dashboard.sources import gmail

from support import google_api, patched


def message(labels, headers):
    return {
        "labelIds": labels,
        "snippet": "",
        "payload": {"headers": [{"name": k, "value": v} for k, v in headers.items()]},
    }


MESSAGES = {
    "m1": message(["UNREAD", "IMPORTANT"], {
        "From": "Priya Nair <priya@acme.test>", "Subject": "Re: retry budget"}),
    "m2": message(["UNREAD"], {
        "From": "newsletter@bulk.test", "Subject": "Weekly digest",
        "List-Unsubscribe": "<mailto:x@y>"}),
    "m3": message(["UNREAD", "STARRED"], {
        "From": "sam@acme.test", "Subject": "ARM runners"}),
}


def fetch(cfg=None, *, more=True):
    listing = {"messages": [{"id": k} for k in MESSAGES]}
    if more:
        listing["nextPageToken"] = "more"

    def respond(url, params):
        if url.endswith("/messages"):
            return listing
        return MESSAGES[url.rstrip("/").split("/")[-1]]

    api = google_api({"/messages": respond})
    with patched(gmail.google_auth, api=api):
        result = gmail.fetch(cfg or {"refresh_token": "r"})
    result["_calls"] = api.calls
    return result


class Counting(unittest.TestCase):
    def test_count_from_listing(self):
        self.assertEqual(fetch()["count"], 3)

    def test_further_pages_flagged_rather_than_ignored(self):
        self.assertTrue(fetch()["count_is_partial"])

    def test_no_further_pages(self):
        self.assertFalse(fetch(more=False)["count_is_partial"])


class DefaultQuery(unittest.TestCase):
    def setUp(self):
        self.query = fetch()["_calls"][0][1]["q"]

    def test_primary_category_excludes_bulk(self):
        """Gmail has already sorted promotions/social/updates/forums away."""
        self.assertIn("category:primary", self.query)

    def test_unread_inbox_only(self):
        self.assertIn("is:unread", self.query)
        self.assertIn("in:inbox", self.query)

    def test_transactional_senders_excluded(self):
        for needle in ("-from:noreply", "-from:no-reply", "-from:donotreply"):
            self.assertIn(needle, self.query)

    def test_custom_query_replaces_the_default(self):
        result = fetch({"refresh_token": "r", "gmail_query": "is:unread from:boss@x.test"})
        self.assertEqual(result["_calls"][0][1]["q"], "is:unread from:boss@x.test")

    def test_blank_query_falls_back_to_the_default(self):
        result = fetch({"refresh_token": "r", "gmail_query": "   "})
        self.assertEqual(result["_calls"][0][1]["q"], gmail.DEFAULT_QUERY)


class SenderParsing(unittest.TestCase):
    def setUp(self):
        self.items = fetch()["items"]

    def test_display_name_and_address_split(self):
        self.assertEqual(self.items[0]["from_name"], "Priya Nair")
        self.assertEqual(self.items[0]["from_email"], "priya@acme.test")

    def test_bare_address_used_as_name(self):
        self.assertEqual(self.items[2]["from_name"], "sam@acme.test")

    def test_quoted_display_name_unwrapped(self):
        name, address = gmail._sender('"Nair, Priya" <priya@acme.test>')
        self.assertEqual(name, "Nair, Priya")
        self.assertEqual(address, "priya@acme.test")

    def test_empty_sender_is_tolerated(self):
        self.assertEqual(gmail._sender(None), (None, None))


class Flags(unittest.TestCase):
    def setUp(self):
        self.items = fetch()["items"]

    def test_important_label(self):
        self.assertTrue(self.items[0]["important"])

    def test_starred_label(self):
        self.assertTrue(self.items[2]["starred"])

    def test_list_unsubscribe_marks_bulk_that_slipped_through(self):
        self.assertTrue(self.items[1]["bulk"])

    def test_bulk_leakage_is_counted(self):
        self.assertEqual(fetch()["bulk_in_preview"], 1)


class Requests(unittest.TestCase):
    def test_metadata_headers_sent_as_repeated_params(self):
        """A list must not be encoded as its Python repr, or Gmail returns none."""
        detail = [params for url, params in fetch()["_calls"] if not url.endswith("/messages")]
        self.assertTrue(detail)
        self.assertIsInstance(detail[0]["metadataHeaders"], list)
        self.assertIn("Subject", detail[0]["metadataHeaders"])

    def test_bodies_are_never_requested(self):
        for url, params in fetch()["_calls"]:
            if not url.endswith("/messages"):
                self.assertEqual(params["format"], "metadata")


if __name__ == "__main__":
    unittest.main()
