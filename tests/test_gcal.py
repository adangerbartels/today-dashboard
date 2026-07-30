"""Calendar lane: today's remaining events, past ones excluded."""

import unittest
from datetime import timedelta

from today_dashboard.sources import gcal
from today_dashboard.sources.http_json import ApiError

from support import google_api, local, patched

NOW = local()


def event(eid, title, start, end, **extra):
    node = {
        "id": eid,
        "summary": title,
        "status": extra.pop("status", "confirmed"),
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "htmlLink": f"https://calendar.google.com/{eid}",
    }
    node.update(extra)
    return node


def all_day(eid, title, day):
    return {
        "id": eid, "summary": title, "status": "confirmed",
        "start": {"date": day.date().isoformat()},
        "end": {"date": (day + timedelta(days=1)).date().isoformat()},
    }


EVENTS = [
    event("over", "Already over", NOW - timedelta(hours=2), NOW - timedelta(hours=1)),
    event("now", "In progress", NOW - timedelta(minutes=10), NOW + timedelta(minutes=20),
          hangoutLink="https://meet.google.com/abc"),
    event("soon", "Coming up", NOW + timedelta(hours=3), NOW + timedelta(hours=4),
          attendees=[{"email": "me@x.com", "self": True, "responseStatus": "accepted"},
                     {"email": "other@x.com"}]),
    event("no", "Declined", NOW + timedelta(hours=1), NOW + timedelta(hours=2),
          attendees=[{"email": "me@x.com", "self": True, "responseStatus": "declined"}]),
    event("gone", "Cancelled", NOW + timedelta(hours=1), NOW + timedelta(hours=2),
          status="cancelled"),
    event("rsvp", "Needs answer", NOW + timedelta(hours=2), NOW + timedelta(hours=3),
          attendees=[{"email": "me@x.com", "self": True, "responseStatus": "needsAction"}]),
    all_day("holiday", "Company holiday", NOW),
]

CFG = {"refresh_token": "r", "calendar_ids": ["primary"], "max_events": 20}


def fetch(**overrides):
    api = google_api({"/events": {"items": EVENTS}})
    with patched(gcal.google_auth, api=api):
        result = gcal.fetch({**CFG, **overrides})
    result["_calls"] = api.calls
    return result


class Filtering(unittest.TestCase):
    def setUp(self):
        self.result = fetch()
        self.titles = [item["title"] for item in self.result["items"]]

    def test_finished_event_excluded(self):
        self.assertNotIn("Already over", self.titles)

    def test_declined_event_excluded(self):
        self.assertNotIn("Declined", self.titles)

    def test_cancelled_event_excluded(self):
        self.assertNotIn("Cancelled", self.titles)

    def test_in_progress_event_sorts_first(self):
        self.assertEqual(self.titles[0], "In progress")

    def test_all_day_kept_as_context(self):
        self.assertIn("Company holiday", self.titles)

    def test_all_day_is_not_flagged_in_progress(self):
        """It spans "now", but badging a holiday as live would be wrong."""
        holiday = next(i for i in self.result["items"] if i["title"] == "Company holiday")
        self.assertFalse(holiday["in_progress"])

    def test_all_day_sorts_above_upcoming(self):
        self.assertLess(self.titles.index("Company holiday"), self.titles.index("Coming up"))

    def test_no_private_keys_leak(self):
        for item in self.result["items"]:
            self.assertFalse([k for k in item if k.startswith("_")])


class Details(unittest.TestCase):
    def setUp(self):
        self.items = {i["title"]: i for i in fetch()["items"]}

    def test_video_link_from_hangout(self):
        self.assertEqual(self.items["In progress"]["video_url"], "https://meet.google.com/abc")

    def test_conference_entry_point_used_when_no_hangout(self):
        node = event("c", "Conf", NOW + timedelta(hours=1), NOW + timedelta(hours=2),
                     conferenceData={"entryPoints": [
                         {"entryPointType": "phone", "uri": "tel:+1"},
                         {"entryPointType": "video", "uri": "https://zoom.test/1"}]})
        api = google_api({"/events": {"items": [node]}})
        with patched(gcal.google_auth, api=api):
            item = gcal.fetch(CFG)["items"][0]
        self.assertEqual(item["video_url"], "https://zoom.test/1")

    def test_attendees_counted(self):
        self.assertEqual(self.items["Coming up"]["attendee_count"], 2)

    def test_needs_response_flagged(self):
        self.assertTrue(self.items["Needs answer"]["needs_response"])

    def test_minutes_until_absent_while_in_progress(self):
        self.assertIsNone(self.items["In progress"]["minutes_until"])

    def test_utc_z_suffix_parsed(self):
        """Google sends UTC as a trailing Z, which older fromisoformat rejects."""
        start = (NOW + timedelta(hours=1)).astimezone().isoformat()
        node = {"id": "z", "summary": "Zulu", "status": "confirmed",
                "start": {"dateTime": "2099-01-01T10:00:00Z"},
                "end": {"dateTime": "2099-01-01T11:00:00Z"}}
        parsed, is_all_day = gcal._parse(node["start"], NOW.tzinfo)
        self.assertIsNotNone(parsed)
        self.assertFalse(is_all_day)
        self.assertIsNotNone(start)


class Query(unittest.TestCase):
    def test_time_min_is_now_which_is_what_drops_the_past(self):
        result = fetch()
        params = result["_calls"][0][1]
        self.assertEqual(params["timeMin"][:16], NOW.isoformat()[:16])

    def test_time_max_is_end_of_today(self):
        self.assertEqual(fetch()["_calls"][0][1]["timeMax"][11:16], "23:59")

    def test_recurring_events_expanded(self):
        self.assertEqual(fetch()["_calls"][0][1]["singleEvents"], "true")


class Options(unittest.TestCase):
    def test_skip_declined_off_keeps_declined(self):
        titles = [i["title"] for i in fetch(skip_declined=False)["items"]]
        self.assertIn("Declined", titles)

    def test_include_all_day_off_drops_all_day(self):
        titles = [i["title"] for i in fetch(include_all_day=False)["items"]]
        self.assertNotIn("Company holiday", titles)


class Degradation(unittest.TestCase):
    def test_one_unreadable_calendar_does_not_blank_the_lane(self):
        def api(cfg, url, params=None, timeout=20):
            if "broken" in url:
                raise ApiError("Not Found")
            return {"items": [EVENTS[1]]}

        with patched(gcal.google_auth, api=api):
            result = gcal.fetch({**CFG, "calendar_ids": ["primary", "broken"]})
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(len(result["errors"]), 1)


if __name__ == "__main__":
    unittest.main()
