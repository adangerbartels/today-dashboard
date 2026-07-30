"""GitHub lane: attention rules, drafts, org scoping, coverage warnings."""

import unittest

from today_dashboard.sources import github

from support import github_search, patched, pull_request

CFG = {"token": "t", "extra_query": "", "max_results": 50, "rules": {}, "orgs": []}


def fetch(review=(), mine=(), cfg=None, **search):
    with patched(github, request_json=github_search(review, mine, **search)):
        return github.fetch({**CFG, **(cfg or {})}, {})


class Reasons(unittest.TestCase):
    def test_review_requested_from_the_review_search(self):
        result = fetch(review=[pull_request(1)])
        self.assertIn("review-requested", result["items"][0]["reasons"])

    def test_failing_checks_on_my_own_pr(self):
        result = fetch(mine=[pull_request(2, author="me", checks="FAILURE")])
        self.assertIn("ci-failing", result["items"][0]["reasons"])

    def test_changes_requested_on_my_own_pr(self):
        result = fetch(mine=[pull_request(3, author="me", decision="CHANGES_REQUESTED")])
        self.assertIn("changes-requested", result["items"][0]["reasons"])

    def test_conflicts_on_my_own_pr(self):
        result = fetch(mine=[pull_request(4, author="me", mergeable="CONFLICTING")])
        self.assertIn("conflicts", result["items"][0]["reasons"])

    def test_ready_to_merge(self):
        result = fetch(mine=[pull_request(5, author="me", decision="APPROVED")])
        self.assertIn("ready-to-merge", result["items"][0]["reasons"])

    def test_approved_but_failing_is_not_ready_to_merge(self):
        result = fetch(mine=[pull_request(6, author="me", decision="APPROVED",
                                          checks="FAILURE")])
        self.assertNotIn("ready-to-merge", result["items"][0]["reasons"])

    def test_my_own_comments_are_not_new_activity(self):
        pr = pull_request(7, author="me", commenter="me")
        result = fetch(mine=[pr])
        self.assertNotIn("new-activity", (result["items"] or [{"reasons": []}])[0]["reasons"])

    def test_a_rule_can_be_switched_off(self):
        result = fetch(mine=[pull_request(8, author="me", checks="FAILURE")],
                       cfg={"rules": {"ci_failing": False, "new_activity": False}})
        self.assertEqual(result["items"], [])

    def test_strongest_reason_drives_the_sort(self):
        review = [pull_request(10, node_id="a")]
        mine = [pull_request(11, author="me", checks="FAILURE", node_id="b")]
        keys = [i["reasons"][0] for i in fetch(review=review, mine=mine)["items"]]
        self.assertEqual(keys[0], "review-requested")


class Drafts(unittest.TestCase):
    def setUp(self):
        self.review = [pull_request(20, node_id="r1"),
                       pull_request(21, draft=True, node_id="r2")]
        self.mine = [pull_request(30, author="me", checks="FAILURE", node_id="m1"),
                     pull_request(31, author="me", draft=True, checks="FAILURE", node_id="m2")]

    def test_drafts_hidden_by_default(self):
        result = fetch(review=self.review, mine=self.mine)
        self.assertFalse(any(i["is_draft"] for i in result["items"]))

    def test_hidden_count_reported_rather_than_silently_dropped(self):
        result = fetch(review=self.review, mine=self.mine)
        self.assertEqual(result["drafts_hidden"], 2)

    def test_include_drafts_brings_them_back(self):
        result = fetch(review=self.review, mine=self.mine, cfg={"include_drafts": True})
        self.assertEqual(sum(1 for i in result["items"] if i["is_draft"]), 2)
        self.assertEqual(result["drafts_hidden"], 0)


class OrgScoping(unittest.TestCase):
    def setUp(self):
        self.review = [pull_request(40, repo="acme/platform", node_id="a"),
                       pull_request(41, repo="beta/api", node_id="b")]
        self.mine = [pull_request(50, repo="acme/web", author="me", checks="FAILURE", node_id="c"),
                     pull_request(51, repo="me/personal", author="me", checks="FAILURE", node_id="d")]

    def keys(self, orgs):
        result = fetch(review=self.review, mine=self.mine, cfg={"orgs": orgs})
        return sorted(i["repo"] for i in result["items"]), result["filtered_owners"]

    def test_no_filter_includes_everything(self):
        keys, hidden = self.keys([])
        self.assertEqual(len(keys), 4)
        self.assertEqual(hidden, [])

    def test_single_org(self):
        keys, hidden = self.keys(["acme"])
        self.assertEqual(keys, ["acme/platform", "acme/web"])
        self.assertEqual(hidden, ["beta", "me"])

    def test_matching_is_case_insensitive(self):
        self.assertEqual(self.keys(["ACME"])[0], ["acme/platform", "acme/web"])

    def test_own_account_is_an_owner_too(self):
        keys, _ = self.keys(["me"])
        self.assertEqual(keys, ["me/personal"])

    def test_hidden_owners_are_named(self):
        self.assertEqual(self.keys(["nope"])[1], ["acme", "beta", "me"])


class Coverage(unittest.TestCase):
    def test_truncation_reported(self):
        result = fetch(mine=[pull_request(60, author="me", checks="FAILURE")], mine_total=40)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["total_open"], 40)

    def test_not_truncated_when_counts_match(self):
        self.assertFalse(fetch(mine=[pull_request(61, author="me", checks="FAILURE")])["truncated"])

    def test_saml_partial_results_detected(self):
        result = fetch(sso="partial-results; organizations=1,2")
        self.assertEqual(result["sso"], "partial")

    def test_no_sso_header_means_no_warning(self):
        self.assertIsNone(fetch()["sso"])


class Diagnostics(unittest.TestCase):
    def notes(self, scopes, sso=None, orgs=(({"login": "acme"}),)):
        return github.diagnose(scopes, sso, {"orgs": list(orgs)})

    def test_fine_grained_token_warned_about_single_owner_limit(self):
        messages = " ".join(m for _, m in self.notes(None))
        self.assertIn("fine-grained", messages)

    def test_full_classic_scopes_are_clean(self):
        self.assertEqual(self.notes(["repo", "read:org"]), [])

    def test_missing_repo_scope(self):
        messages = " ".join(m for _, m in self.notes(["read:org"]))
        self.assertIn("no repo scope", messages)

    def test_public_repo_only(self):
        messages = " ".join(m for _, m in self.notes(["public_repo", "read:org"]))
        self.assertIn("public_repo", messages)

    def test_missing_read_org(self):
        messages = " ".join(m for _, m in self.notes(["repo"]))
        self.assertIn("read:org", messages)

    def test_saml_omission_explained(self):
        messages = " ".join(m for _, m in self.notes(["repo", "read:org"], sso="partial"))
        self.assertIn("SAML", messages)


if __name__ == "__main__":
    unittest.main()
