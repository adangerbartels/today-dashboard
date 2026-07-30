"""Config resolution and saving, plus the settings-write validation layer."""

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from today_dashboard import config
from today_dashboard.sources import google_auth
from today_dashboard.sources.http_json import ApiError, _error_code

from support import patched


def pristine():
    """A default config, so the suite never reads the developer's own tokens."""
    return json.loads(json.dumps(config.DEFAULTS))


class HomeResolution(unittest.TestCase):
    """An installed copy must never write tokens into its own package directory."""

    def test_env_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patched(os, environ={**os.environ, "TODAY_HOME": tmp}):
                self.assertEqual(config.resolve_home(), Path(tmp).resolve())

    def test_local_config_json_keeps_a_clone_working(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "config.json").write_text("{}")
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                env = {k: v for k, v in os.environ.items() if k != "TODAY_HOME"}
                with patched(os, environ=env):
                    self.assertEqual(config.resolve_home(), Path(tmp).resolve())
            finally:
                os.chdir(cwd)

    def test_falls_back_to_xdg_not_the_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if k != "TODAY_HOME"}
            env["XDG_CONFIG_HOME"] = tmp
            cwd = os.getcwd()
            try:
                os.chdir(tempfile.gettempdir())
                with patched(os, environ=env):
                    home = config.resolve_home()
            finally:
                os.chdir(cwd)
            self.assertEqual(home, (Path(tmp) / config.APP_NAME).resolve())
            self.assertNotIn("today_dashboard", str(home))


class Masking(unittest.TestCase):
    def test_long_secret_shows_only_the_last_four(self):
        self.assertEqual(config.mask("ghp_1234567890abcdef"), "•" * 8 + "cdef")

    def test_short_secret_reveals_nothing(self):
        self.assertEqual(config.mask("abc"), "•" * 8)

    def test_empty_secret_has_no_hint(self):
        self.assertIsNone(config.mask(""))
        self.assertIsNone(config.mask(None))


class Saving(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        self.patch = patched(config, CONFIG_PATH=self.path)
        self.patch.__enter__()

    def tearDown(self):
        self.patch.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_written_owner_only(self):
        config.save_section("github", {"token": "secret"})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_other_sections_untouched(self):
        config.save_section("github", {"token": "a"})
        config.save_section("jira", {"api_token": "b"})
        raw = json.loads(self.path.read_text())
        self.assertEqual(raw["github"]["token"], "a")
        self.assertEqual(raw["jira"]["api_token"], "b")

    def test_unknown_section_rejected(self):
        with self.assertRaises(ValueError):
            config.save_section("evil", {"x": 1})

    def test_comment_keys_survive_a_save(self):
        self.path.write_text(json.dumps({"_comment": "keep me", "github": {}}))
        config.save_section("github", {"token": "a"})
        self.assertEqual(json.loads(self.path.read_text())["_comment"], "keep me")

    def test_comment_keys_ignored_when_loading(self):
        self.path.write_text(json.dumps({"github": {"_note": "x", "token": "a"}}))
        cfg = config.load()
        self.assertNotIn("_note", cfg["github"])
        self.assertEqual(cfg["github"]["token"], "a")


class ConfiguredChecks(unittest.TestCase):
    def base(self, **sections):
        cfg = json.loads(json.dumps(config.DEFAULTS))
        for name, values in sections.items():
            cfg[name].update(values)
        return cfg

    def test_jira_needs_url_and_token(self):
        self.assertFalse(config.jira_configured(self.base(jira={"base_url": "https://x"})))
        self.assertTrue(config.jira_configured(
            self.base(jira={"base_url": "https://x", "api_token": "t"})))

    def test_google_needs_client_and_consent(self):
        self.assertFalse(config.google_configured(
            self.base(google={"client_id": "a", "client_secret": "b"})))
        self.assertTrue(config.google_configured(
            self.base(google={"client_id": "a", "client_secret": "b", "refresh_token": "c"})))

    def test_catercow_satisfied_by_either_extractor(self):
        self.assertTrue(config.catercow_configured(self.base(catercow={"cookie": "c"})))
        self.assertTrue(config.catercow_configured(
            self.base(google={"client_id": "a", "client_secret": "b", "refresh_token": "c"})))
        self.assertFalse(config.catercow_configured(self.base()))


class Validation(unittest.TestCase):
    """server.validate_values, exercised without starting a server."""

    def setUp(self):
        from today_dashboard import server
        self.server = server
        self.reset = patched(server, CFG=pristine())
        self.reset.__enter__()

    def tearDown(self):
        self.reset.__exit__(None, None, None)

    def check(self, source, submitted):
        values = self.server.resolve_values(source, submitted)
        return self.server.validate_values(source, values)

    def test_jira_requires_a_url(self):
        self.assertIn("Site URL", self.check("jira", {"api_token": "t"}))

    def test_jira_rejects_a_non_http_url(self):
        self.assertIn("https://", self.check(
            "jira", {"base_url": "javascript:alert(1)", "api_token": "t"}))

    def test_google_rejects_a_client_id_of_the_wrong_shape(self):
        self.assertIn("apps.googleusercontent.com", self.check(
            "google", {"client_id": "nope", "client_secret": "s"}))

    def test_slack_accepts_a_rotating_token(self):
        self.assertIsNone(self.check("slack", {"token": "xoxe.xoxp-1-abc"}))

    def test_slack_accepts_a_static_token(self):
        self.assertIsNone(self.check("slack", {"token": "xoxp-1-abc"}))

    def test_slack_rejects_a_refresh_token_in_the_token_field(self):
        message = self.check("slack", {"token": "xoxe-1-refresh"})
        self.assertIn("refresh token", message)

    def test_slack_rejects_junk(self):
        self.assertIn("xoxp-", self.check("slack", {"token": "hello"}))

    def test_catercow_requires_at_least_one_weekday(self):
        self.assertIn("weekday", self.check(
            "catercow", {"cookie": "c", "lunch_days": []}))

    def test_catercow_rejects_a_broken_regex(self):
        message = self.check("catercow", {
            "cookie": "c", "lunch_days": [0], "selected_pattern": "([unclosed"})
        self.assertIn("regex", message)


class ValueCoercion(unittest.TestCase):
    def setUp(self):
        from today_dashboard import server
        self.server = server
        self.reset = patched(server, CFG=pristine())
        self.reset.__enter__()

    def tearDown(self):
        self.reset.__exit__(None, None, None)

    def resolve(self, source, submitted):
        return self.server.resolve_values(source, submitted)

    def test_blank_secret_keeps_the_stored_one(self):
        with patched(self.server, CFG={**self.server.CFG,
                                      "github": {**self.server.CFG["github"], "token": "kept"}}):
            self.assertEqual(self.resolve("github", {"token": ""})["token"], "kept")

    def test_string_list_trimmed_and_capped(self):
        values = self.resolve("github", {"orgs": ["  acme ", "", None] + ["x"] * 200})
        self.assertEqual(values["orgs"][0], "acme")
        self.assertLessEqual(len(values["orgs"]), 100)

    def test_non_list_ignored_rather_than_stored(self):
        self.assertIsInstance(self.resolve("github", {"orgs": "acme"})["orgs"], list)

    def test_integer_list_coerced(self):
        self.assertEqual(self.resolve("catercow", {"lunch_days": ["0", 2, "4"]})["lunch_days"],
                         [0, 2, 4])

    def test_bounded_integer_clamped(self):
        self.assertEqual(self.resolve("catercow", {"horizon_days": 999})["horizon_days"], 60)
        self.assertEqual(self.resolve("catercow", {"horizon_days": 0})["horizon_days"], 1)

    def test_garbage_integer_ignored(self):
        stored = self.server.CFG["catercow"]["horizon_days"]
        self.assertEqual(self.resolve("catercow", {"horizon_days": "abc"})["horizon_days"], stored)


class OAuthErrors(unittest.TestCase):
    """invalid_grant and invalid_client read alike but need opposite fixes."""

    def test_error_code_extracted_from_the_body(self):
        self.assertEqual(_error_code('{"error": "invalid_grant"}'), "invalid_grant")
        self.assertIsNone(_error_code("not json"))

    def test_revoked_grant_points_at_reconnecting(self):
        def boom(url, fields, timeout=20):
            raise ApiError("Token has been expired or revoked.", 400, None, "invalid_grant")

        with patched(google_auth, request_form=boom):
            with self.assertRaises(ApiError) as caught:
                google_auth.access_token({"refresh_token": "r", "client_id": "a",
                                          "client_secret": "b"})
        self.assertIn("revoked", str(caught.exception))
        self.assertIn("Reconnect", str(caught.exception))

    def test_bad_client_points_at_the_credentials(self):
        def boom(url, fields, timeout=20):
            raise ApiError("The provided client secret is invalid.", 401, None, "invalid_client")

        with patched(google_auth, request_form=boom):
            with self.assertRaises(ApiError) as caught:
                google_auth.access_token({"refresh_token": "r", "client_id": "a",
                                          "client_secret": "b"})
        self.assertIn("client ID and secret", str(caught.exception))

    def test_missing_refresh_token_is_explicit(self):
        with self.assertRaises(ApiError) as caught:
            google_auth.access_token({"refresh_token": ""})
        self.assertIn("isn't connected", str(caught.exception))


class OAuthFlow(unittest.TestCase):
    def test_consent_url_requests_offline_access_with_pkce(self):
        from urllib.parse import parse_qs, urlparse

        started = google_auth.begin("x.apps.googleusercontent.com", "http://127.0.0.1:1/cb")
        query = parse_qs(urlparse(started["url"]).query)
        self.assertEqual(query["access_type"][0], "offline")
        self.assertEqual(query["prompt"][0], "consent")
        self.assertEqual(query["code_challenge_method"][0], "S256")
        self.assertTrue(query["code_challenge"][0])
        google_auth._pending.pop(started["state"], None)

    def test_unknown_state_rejected(self):
        with self.assertRaises(ApiError) as caught:
            google_auth.complete("id", "secret", "never-issued", "code")
        self.assertIn("expired or was already used", str(caught.exception))

    def test_state_is_single_use(self):
        started = google_auth.begin("x.apps.googleusercontent.com", "http://127.0.0.1:1/cb")
        google_auth._pending.pop(started["state"], None)
        with self.assertRaises(ApiError):
            google_auth.complete("id", "secret", started["state"], "code")

    def test_loopback_redirect_normalised_to_a_literal_address(self):
        self.assertEqual(google_auth.redirect_uri("0.0.0.0", 8787),
                         "http://127.0.0.1:8787/oauth/google/callback")


if __name__ == "__main__":
    unittest.main()
