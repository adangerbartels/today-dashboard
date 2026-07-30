"""Slack lane: unread counts, mentions, and token rotation."""

import time
import unittest

from today_dashboard.sources import slack
from today_dashboard.sources.http_json import ApiError

from support import patched, slack_api

ME = "U_ME"

HISTORY = [
    {"ts": "101.0", "user": "U_A", "text": "deploy is red"},
    {"ts": "102.0", "user": "U_A", "text": f"<@{ME}> can you look?"},
    {"ts": "103.0", "user": ME, "text": "on it"},                       # mine
    {"ts": "104.0", "user": "U_B", "text": "<!here> heads up"},          # broadcast
    {"ts": "105.0", "user": "U_C", "subtype": "channel_join", "text": ""},
]

CHANNELS = [
    {"id": "C1", "name": "eng-platform", "is_private": False},
    {"id": "C2", "name": "random", "is_private": False},
    {"id": "C3", "name": "leadership", "is_private": True},
]


def api(*, is_bot=False, last_read="100.0", reported=None, history=HISTORY,
        history_error=None, valid_token=None):
    def auth(query, token):
        if valid_token and token != valid_token:
            return {"ok": False, "error": "token_expired"}
        out = {"ok": True, "user": "austen", "user_id": ME, "team": "Acme", "team_id": "T1"}
        if is_bot:
            out["bot_id"] = "B1"
        return out

    def info(query, token):
        node = {"id": query["channel"], "name": query["channel"].lower()}
        if last_read:
            node["last_read"] = last_read
        if reported is not None:
            node["unread_count_display"] = reported
        return {"ok": True, "channel": node}

    def hist(query, token):
        if history_error:
            return {"ok": False, "error": history_error}
        return {"ok": True, "messages": history, "has_more": False}

    return slack_api({
        "auth.test": auth,
        "users.conversations": {"ok": True, "channels": CHANNELS,
                                "response_metadata": {"next_cursor": ""}},
        "conversations.info": info,
        "conversations.history": hist,
    })


BASE = {"token": "xoxp-user", "channels": ["C1"],
        "known_channels": [{"id": "C1", "name": "eng-platform"}]}


class Counting(unittest.TestCase):
    def fetch(self, **overrides):
        with patched(slack, request_json=api(**overrides.pop("api", {}))):
            return slack.fetch({**BASE, **overrides})

    def test_own_messages_excluded(self):
        self.assertEqual(self.fetch()["items"][0]["count"], 3)

    def test_join_and_leave_noise_excluded(self):
        self.assertEqual(self.fetch()["items"][0]["count"], 3)

    def test_direct_and_broadcast_mentions_counted(self):
        self.assertEqual(self.fetch()["items"][0]["mentions"], 2)

    def test_slack_own_tally_preferred_when_present(self):
        result = self.fetch(api={"reported": 9})
        self.assertEqual(result["items"][0]["count"], 9)

    def test_mentions_still_derived_from_history(self):
        result = self.fetch(api={"reported": 9})
        self.assertEqual(result["items"][0]["mentions"], 2)

    def test_only_chosen_channels_are_polled(self):
        result = self.fetch(channels=["C1", "C3"])
        self.assertEqual(sorted(i["id"] for i in result["items"]), ["C1", "C3"])

    def test_totals_summed(self):
        self.assertEqual(self.fetch(channels=["C1", "C3"])["total"], 6)

    def test_no_selection_is_reported_not_silently_empty(self):
        self.assertTrue(self.fetch(channels=[])["needs_selection"])


class BotToken(unittest.TestCase):
    def test_falls_back_to_a_time_window(self):
        with patched(slack, request_json=api(is_bot=True, last_read=None)):
            result = slack.fetch({**BASE, "token": "xoxb-bot"})
        self.assertEqual(result["items"][0]["mode"], "recent")
        self.assertEqual(result["mode"], "recent")

    def test_warns_rather_than_reporting_zero_unread(self):
        with patched(slack, request_json=api(is_bot=True, last_read=None)):
            notes = slack.verify({**BASE, "token": "xoxb-bot", "channels": []})["notes"]
        self.assertTrue(any("bot token" in n["message"] for n in notes))


class Blindness(unittest.TestCase):
    """Nothing readable must not look like nothing unread."""

    def setUp(self):
        with patched(slack, request_json=api(history_error="missing_scope")):
            self.result = slack.fetch({**BASE, "channels": ["C1", "C2", "C3"]})

    def test_readable_count_is_zero(self):
        self.assertEqual(self.result["readable"], 0)

    def test_repeated_error_collapsed_to_one_message(self):
        self.assertEqual(len(self.result["errors"]), 1)

    def test_error_names_the_missing_scope(self):
        self.assertIn("missing a scope", self.result["errors"][0])


class TokenShapes(unittest.TestCase):
    def test_static_user_and_bot_tokens(self):
        self.assertEqual(slack.token_kind("xoxp-1-a"), "user")
        self.assertEqual(slack.token_kind("xoxb-1-a"), "bot")

    def test_rotating_tokens_recognised(self):
        """The prefix is xoxe.xoxp-, not xoxe- — a dot, not a dash."""
        self.assertEqual(slack.token_kind("xoxe.xoxp-1-a"), "user")
        self.assertEqual(slack.token_kind("xoxe.xoxb-1-a"), "bot")
        self.assertTrue(slack.is_rotating("xoxe.xoxp-1-a"))

    def test_refresh_token_is_not_an_access_token(self):
        self.assertIsNone(slack.token_kind("xoxe-1-refresh"))
        self.assertFalse(slack.is_rotating("xoxe-1-refresh"))

    def test_junk_rejected(self):
        self.assertIsNone(slack.token_kind("nonsense"))
        self.assertIsNone(slack.token_kind(""))


class Rotation(unittest.TestCase):
    def setUp(self):
        self.rotated = []
        self.cfg = {
            **BASE, "token": "xoxe.xoxp-1-old", "refresh_token": "xoxe-1-old",
            "client_id": "cid", "client_secret": "csec",
        }

    def form(self, fields, timeout=20):
        self.assertEqual(fields["grant_type"], "refresh_token")
        self.assertEqual(fields["refresh_token"], "xoxe-1-old")
        return {"ok": True,
                "authed_user": {"access_token": "xoxe.xoxp-1-new",
                                "refresh_token": "xoxe-1-new"},
                "expires_in": 43200}

    def test_refreshes_before_expiry(self):
        cfg = {**self.cfg, "expires_at": int(time.time()) + 60}
        with patched(slack, request_json=api(valid_token="xoxe.xoxp-1-new"),
                     request_form=lambda url, fields, timeout=20: self.form(fields)):
            result = slack.fetch(cfg, on_rotate=self.rotated.append)
        self.assertTrue(self.rotated)
        self.assertEqual(self.rotated[0]["refresh_token"], "xoxe-1-new")
        self.assertEqual(result["items"][0]["count"], 3)

    def test_does_not_burn_a_refresh_token_while_still_fresh(self):
        cfg = {**self.cfg, "token": "xoxe.xoxp-1-new", "expires_at": int(time.time()) + 40000}
        with patched(slack, request_json=api(valid_token="xoxe.xoxp-1-new"),
                     request_form=lambda url, fields, timeout=20: self.form(fields)):
            slack.fetch(cfg, on_rotate=self.rotated.append)
        self.assertEqual(self.rotated, [])

    def test_retries_once_when_the_token_dies_mid_flight(self):
        cfg = {**self.cfg, "expires_at": 0}  # expiry unknown
        with patched(slack, request_json=api(valid_token="xoxe.xoxp-1-new"),
                     request_form=lambda url, fields, timeout=20: self.form(fields)):
            result = slack.fetch(cfg, on_rotate=self.rotated.append)
        self.assertTrue(self.rotated)
        self.assertEqual(result["items"][0]["count"], 3)

    def test_rotating_token_without_credentials_is_flagged(self):
        with patched(slack, request_json=api()):
            result = slack.fetch({**BASE, "token": "xoxe.xoxp-1-old"})
        self.assertTrue(result["rotating"])
        self.assertFalse(result["can_renew"])

    def test_spent_refresh_token_explained(self):
        cfg = {**self.cfg, "expires_at": 1}
        with patched(slack, request_json=api(valid_token="never"),
                     request_form=lambda *a, **k: {"ok": False, "error": "invalid_refresh_token"}):
            with self.assertRaises(ApiError) as caught:
                slack.fetch(cfg)
        self.assertIn("already have been used", str(caught.exception))


class Errors(unittest.TestCase):
    def test_invalid_auth_translated(self):
        with patched(slack, request_json=lambda url, **k: {"ok": False, "error": "invalid_auth"}):
            with self.assertRaises(ApiError) as caught:
                slack.fetch(BASE)
        self.assertEqual(str(caught.exception), "Slack rejected this token")

    def test_missing_scope_names_the_scope(self):
        payload = {"ok": False, "error": "missing_scope", "needed": "channels:history"}
        with patched(slack, request_json=lambda url, **k: payload):
            with self.assertRaises(ApiError) as caught:
                slack.fetch(BASE)
        self.assertIn("channels:history", str(caught.exception))

    def test_server_error_on_a_rotating_token_explains_expiry(self):
        """Slack answers 500 with an empty body for an expired rotating token."""
        def boom(url, headers=None, timeout=20):
            raise ApiError("HTTP 500", 500)

        with patched(slack, request_json=boom):
            with self.assertRaises(ApiError) as caught:
                slack.fetch({**BASE, "token": "xoxe.xoxp-1-dead"})
        self.assertIn("12 hours", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
