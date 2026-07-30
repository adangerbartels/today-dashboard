"""CaterCow lane: which lunch days have no selection yet."""

import unittest
from datetime import date

from today_dashboard.sources import catercow
from today_dashboard.sources.http_json import ApiError

from support import google_api, patched

WEDNESDAY = date(2026, 7, 29)

SUBJECTS = {
    "a": ("Your meal selection on Wednesday 7/29 is confirmed", "Mon, 27 Jul 2026 10:00:00 -0700"),
    "b": ("Your meal selection on Thursday 7/30 is confirmed", "Mon, 27 Jul 2026 10:00:00 -0700"),
    "c": ("Your meal selection on Friday 7/31 is confirmed", "Mon, 27 Jul 2026 10:00:00 -0700"),
    "d": ("Your meal selection on Friday 7/31 is confirmed", "Mon, 27 Jul 2026 10:01:00 -0700"),
    "e": ("CaterCow Password Reset Instructions", "Mon, 27 Jul 2026 09:00:00 -0700"),
}

CFG = {"use_email": True, "lunch_days": [0, 1, 2, 3], "horizon_days": 14}


class FrozenDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 7, 29)


def fetch(**overrides):
    def respond(url, params):
        if url.endswith("/messages"):
            return {"messages": [{"id": k} for k in SUBJECTS]}
        subject, sent = SUBJECTS[url.rstrip("/").split("/")[-1]]
        return {"payload": {"headers": [{"name": "Subject", "value": subject},
                                        {"name": "Date", "value": sent}]}}

    api = google_api({"/messages": respond})
    with patched(catercow.google_auth, api=api), patched(catercow, date=FrozenDate):
        return catercow.fetch({**CFG, **overrides}, {"refresh_token": "r"})


class SubjectParsing(unittest.TestCase):
    def parse(self, subject):
        match = catercow.CONFIRM_RE.search(subject)
        return (int(match.group("month")), int(match.group("day"))) if match else None

    def test_weekday_and_date(self):
        self.assertEqual(self.parse("Your meal selection on Monday 8/3 is confirmed"), (8, 3))

    def test_two_digit_day(self):
        self.assertEqual(self.parse("Your meal selection on Friday 7/31 is confirmed"), (7, 31))

    def test_weekday_optional(self):
        self.assertEqual(self.parse("your meal selection on 12/25 is confirmed"), (12, 25))

    def test_abbreviated_weekday_with_comma(self):
        self.assertEqual(self.parse("Your meal selection on Mon, 8/3 is confirmed"), (8, 3))

    def test_explicit_year_accepted(self):
        self.assertEqual(self.parse("Your meal selection on Wednesday 7/29/2026 is confirmed"),
                         (7, 29))

    def test_unrelated_mail_ignored(self):
        for subject in ("CaterCow Password Reset Instructions", "How can I help?",
                        "Your order shipped"):
            self.assertIsNone(self.parse(subject))


class YearInference(unittest.TestCase):
    """Subjects carry no year, so it comes from the message date."""

    def test_same_month(self):
        self.assertEqual(catercow._resolve_year(7, 31, date(2026, 7, 27)), date(2026, 7, 31))

    def test_next_month(self):
        self.assertEqual(catercow._resolve_year(8, 3, date(2026, 7, 27)), date(2026, 8, 3))

    def test_december_mail_for_a_january_lunch_rolls_forward(self):
        self.assertEqual(catercow._resolve_year(1, 5, date(2026, 12, 28)), date(2027, 1, 5))

    def test_january_mail_for_a_december_lunch_rolls_back(self):
        self.assertEqual(catercow._resolve_year(12, 30, date(2027, 1, 2)), date(2026, 12, 30))

    def test_explicit_two_digit_year(self):
        self.assertEqual(catercow._resolve_year(7, 29, date(2026, 7, 27), "26"), date(2026, 7, 29))

    def test_explicit_four_digit_year(self):
        self.assertEqual(catercow._resolve_year(7, 29, date(2026, 7, 27), "2026"), date(2026, 7, 29))

    def test_leap_day_resolves_instead_of_being_dropped(self):
        """A dropped date would wrongly read as "not selected"."""
        self.assertEqual(catercow._resolve_year(2, 29, date(2026, 2, 1)), date(2028, 2, 29))


class UpcomingDays(unittest.TestCase):
    def test_includes_today_when_today_qualifies(self):
        days = catercow.upcoming_lunch_days([0, 1, 2, 3], 14, WEDNESDAY)
        self.assertEqual(days[0], WEDNESDAY)

    def test_never_includes_the_past(self):
        days = catercow.upcoming_lunch_days([0, 1, 2, 3], 14, WEDNESDAY)
        self.assertTrue(all(day >= WEDNESDAY for day in days))

    def test_only_chosen_weekdays(self):
        days = catercow.upcoming_lunch_days([0, 1, 2, 3], 14, WEDNESDAY)
        self.assertEqual(sorted({d.weekday() for d in days}), [0, 1, 2, 3])

    def test_friday_included_when_chosen(self):
        days = catercow.upcoming_lunch_days([0, 1, 2, 3, 4], 14, WEDNESDAY)
        self.assertIn(4, {d.weekday() for d in days})

    def test_horizon_respected(self):
        days = catercow.upcoming_lunch_days([0, 1, 2, 3, 4, 5, 6], 3, WEDNESDAY)
        self.assertEqual(len(days), 3)

    def test_no_weekdays_means_nothing(self):
        self.assertEqual(catercow.upcoming_lunch_days([], 14, WEDNESDAY), [])

    def test_out_of_range_weekdays_ignored(self):
        self.assertEqual(catercow.upcoming_lunch_days([9, -1], 14, WEDNESDAY), [])


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.result = fetch()

    def test_confirmed_days_excluded_from_pending(self):
        labels = [i["label"] for i in self.result["items"]]
        self.assertNotIn("Wednesday 7/29", labels)
        self.assertNotIn("Thursday 7/30", labels)

    def test_first_pending_is_the_next_unconfirmed_lunch_day(self):
        self.assertEqual(self.result["items"][0]["label"], "Monday 8/3")

    def test_duplicate_confirmations_collapse(self):
        self.assertEqual(self.result["selected"], ["2026-07-29", "2026-07-30"])

    def test_non_confirmation_mail_scanned_but_ignored(self):
        self.assertEqual(self.result["scanned"], 5)

    def test_source_reported(self):
        self.assertEqual(self.result["sources"], ["email"])

    def test_friday_counts_once_enabled(self):
        self.assertIn("2026-07-31", fetch(lunch_days=[0, 1, 2, 3, 4])["selected"])


class Degradation(unittest.TestCase):
    def test_no_extractor_available_is_reported(self):
        with patched(catercow, date=FrozenDate):
            result = catercow.fetch(CFG, {})
        self.assertTrue(result["unconfigured"])
        self.assertTrue(result["warnings"])

    def test_gmail_failure_does_not_claim_everything_is_picked(self):
        def boom(cfg, url, params=None, timeout=20):
            raise ApiError("Gmail unavailable")

        with patched(catercow.google_auth, api=boom), patched(catercow, date=FrozenDate):
            result = catercow.fetch(CFG, {"refresh_token": "r"})
        self.assertTrue(result["warnings"])
        self.assertEqual(result["sources"], [])
        self.assertTrue(result["unconfigured"])


if __name__ == "__main__":
    unittest.main()
