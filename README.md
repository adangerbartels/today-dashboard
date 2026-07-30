# Today

A one-screen dashboard of the work that actually needs you — your calendar, your
in-progress issues, the pull requests waiting on you, unread mail that isn't
bulk, and the Slack channels you care about.

**Every lane is a filter, not a feed.** The point is subtraction. Your calendar
already shows you today; this shows what's left of it. GitHub already lists your
pull requests; this shows the ones that need a decision. If a lane isn't showing
you less than the tool it came from, it isn't earning its space.

**No dependencies.** Python standard library only — no `pip install` of anything
else, no build step, no third-party package in the path that handles your API
tokens.

```bash
pipx install today-dashboard
today
```

Then open <http://127.0.0.1:8787>. It runs on realistic demo data until you
connect anything, so you can see whether you want it before setting it up.

Prefer a clone? That works identically, with nothing to install:

```bash
git clone https://github.com/OWNER/today-dashboard && cd today-dashboard
python3 today.py
```

---

## The lanes

| Lane | Source | What's in it |
| --- | --- | --- |
| **Rest of today** | Google Calendar | Today's remaining events. Finished ones gone, declined and cancelled dropped |
| **Lunch to pick** | CaterCow | Upcoming lunch days with no selection yet |
| **To do** | local | Tasks you type in, stored as JSON next to your config |
| **In progress** | Jira | Issues assigned to you whose status category is *In Progress* |
| **Needs attention** | GitHub | Open non-draft PRs tripping at least one attention rule |
| **Unread mail** | Gmail | Unread in Primary only — no promotions, social, updates or forums |
| **Slack** | Slack | Unread counts for channels you mark important, mentions first |

Nothing is required. Each integration is independent, and any you skip keeps
showing demo data behind an amber banner.

Every event, issue, PR and email has a **+** that copies it into your to-do list
with a link back, so the thing you've decided to do next lives in one place
instead of across seven tabs.

### How the filtering works

- **Calendar** — `timeMin=now` is the mechanism: Google matches events whose
  *end* is at or after now, so a finished meeting disappears while one you're
  sitting in stays and gets a **Now** badge. Events you declined and cancelled
  ones are dropped. All-day events are kept as day context but never badged
  "Now" — a company holiday isn't a meeting.
- **GitHub** — see the rules table below. Drafts are excluded; a draft isn't
  asking anything of anyone yet.
- **Gmail** — `category:primary` does the heavy lifting, because Gmail has
  already sorted bulk mail into promotions/social/updates/forums. On top of that
  the default query drops `noreply`-style senders, and anything still carrying a
  `List-Unsubscribe` header gets a **Bulk** chip so you can see the filter
  leaking rather than trusting it blindly.
- **Slack** — only channels you tick are polled at all. Within them your own
  messages and join/leave noise don't count, and messages mentioning you
  (directly or via `@here`/`@channel`) are counted separately and sorted first.
- **CaterCow** — derived rather than fetched, since there's no API. Past days are
  gone and confirmed days disappear, leaving only what still needs a decision.

### What makes a pull request need attention

A PR appears only if it trips one of these. The strongest reason sets the card's
colour stripe and its sort position.

| Reason | Condition | `github.rules` key |
| --- | --- | --- |
| **Review requested** | You're a requested reviewer and haven't reviewed | `review_requested` |
| **CI failing** | Your PR's latest commit has a failing check rollup | `ci_failing` |
| **Changes requested** | Your PR's review decision is `CHANGES_REQUESTED` | `changes_requested` |
| **Conflicts** | Your PR is `CONFLICTING` with its base | `conflicts` |
| **Ready to merge** | Approved, mergeable, checks green, not a draft | `ready_to_merge` |
| **New activity** | Someone *else* commented or reviewed since you looked | `new_activity` |

"New activity" deliberately ignores your own comments and your own pushes — only
another person speaking counts. The 👁 button (or **Mark N as seen**) resets that
baseline. Team review requests are covered: `review-requested:@me` matches PRs
where review was asked of a team you belong to, not just of you.

Set any rules key to `false` to drop that reason. `github.include_drafts: true`
brings drafts back.

## Setting it up

Press <kbd>s</kbd>, click the gear, or click **Set up connections** in the demo
banner. Each panel takes credentials, checks them against the live API *before
saving*, and tells you who you authenticated as and what the lane will hold. On
success it writes config and the lane goes live immediately — no restart.

**Test only** never writes. If a check fails but you want to store the values
anyway, the button becomes **Save anyway**. **Disconnect** removes one credential
and drops that lane back to demo data.

Editing `config.json` by hand works just as well — copy
[`config.example.json`](config.example.json) and fill in what you have.
`today where` prints the path.

<details>
<summary><b>Jira</b></summary>

Create a token at
<https://id.atlassian.com/manage-profile/security/api-tokens>, then supply your
site URL, the email on the account, and the token.

For Jira Server / Data Center leave the email blank and put a personal access
token in the token field — it's then sent as a bearer token.

Override `jira.jql` to change which issues appear. The default is:

```
assignee = currentUser() AND statusCategory = "In Progress" ORDER BY updated DESC
```
</details>

<details>
<summary><b>GitHub</b> — including organisations and SAML SSO</summary>

**If you work across more than one organisation, use a classic token** with
`repo` and `read:org`. A fine-grained token is scoped to a *single* owner, so with
several orgs it will quietly show you part of your work. A fine-grained token is
fine for one org and needs **Pull requests: read**, **Contents: read** and
**Commit statuses: read**.

Two things silently hide org work, and both are detected and reported rather than
left for you to notice:

- **SAML SSO.** Orgs enforcing it need the token *authorised* for that org
  specifically — open the token on github.com and use **Configure SSO**. Until
  then GitHub returns results with those orgs omitted and no error. The
  `X-GitHub-SSO` header is read and surfaced in both the wizard and the lane.
- **Missing scopes.** No `repo` means private repos are invisible; no `read:org`
  means your org list can't be read.

`github.orgs` is a list of owner logins to include — empty means every owner the
token can see, so newly joined orgs appear on their own. Your own username counts
as an owner. The wizard discovers your orgs on a successful check and caches the
list, so an org you filtered out is still listed and re-addable later.

If more PRs match than `github.max_results`, the lane says so rather than
implying it's showing everything.
</details>

<details>
<summary><b>Google</b> (Calendar + Gmail) — needs an OAuth client</summary>

Both lanes come from one connection, so there's one panel for both. Unlike the
others this needs an OAuth client rather than a token, because Google has no
personal-token equivalent for these APIs.

1. In [Google Cloud](https://console.cloud.google.com/apis/credentials), enable
   the **Gmail API** and **Google Calendar API**.
2. Credentials → Create credentials → OAuth client ID → type **Desktop app**.
   Desktop clients accept any `127.0.0.1` redirect, so there's nothing to
   register; the wizard shows the URI it will use.
3. If your consent screen is in Testing mode, add your address under **Test
   users**.
4. Paste the client ID and secret, press **Connect with Google**, approve in the
   tab that opens. The panel updates itself and discovers your calendars.

Access is read-only (`gmail.readonly`, `calendar.readonly`, `userinfo.email`), the
flow uses PKCE, and only the refresh token is stored.

**When Google stops working**, two failures look alike and are told apart by the
error code:

| Google says | Means | Fix |
| --- | --- | --- |
| `invalid_grant` | The sign-in was revoked. Client ID and secret are fine. | **Reconnect with Google** |
| `invalid_client` | Client ID and secret don't match an OAuth client. | Re-copy them, then reconnect |

Grants get revoked when you regenerate the client secret, when access is removed
at [myaccount.google.com/permissions](https://myaccount.google.com/permissions),
or **after 7 days if your consent screen is still in Testing mode** — publish it,
or expect to reconnect weekly. Reconnect is always available, even while a token
is stored, so a dead grant is never a dead end.
</details>

<details>
<summary><b>Slack</b> — including rotating <code>xoxe.xoxp-</code> tokens</summary>

Create an app at [api.slack.com/apps](https://api.slack.com/apps), add these
**user token scopes**, install it, and copy the token:

`channels:read`, `groups:read`, `channels:history`, `groups:history`

**Use a user token.** Unread state belongs to a person, so a bot token (`xoxb-`)
cannot know what you've read. That case is detected: counts fall back to "the
last 24 hours" and the lane says so rather than reporting zero.

If your app has token rotation enabled, the token starts `xoxe.xoxp-` and **Slack
expires it after 12 hours**. Fill in **Token rotation** and it renews itself:

| Field | Where | Looks like |
| --- | --- | --- |
| Refresh token | issued with the access token | `xoxe-1-…` |
| Client ID | app → Basic Information | `1234567890.1234567890` |
| Client secret | app → Basic Information | 32 hex chars |

Refresh tokens are single-use, so each new pair is written to disk the instant it
arrives, under a lock so two concurrent refreshes can't spend the same one.

Two errors worth knowing: `xoxe-1-…` in the token field is a *refresh* token, not
an access token (the wizard says so and points at the right field); and Slack
answers a shape-valid-but-invalid rotating token with a bare **HTTP 500**, which
almost always means expired or truncated, so it's translated for you.
</details>

<details>
<summary><b>CaterCow</b> — no API, so this one is inferred</summary>

CaterCow has no public API, no developer docs and no calendar feed, so
"selected" is derived rather than queried. Two extractors, either or both:

**From confirmation emails (default).** Subjects read `Your meal selection on
Monday 8/3 is confirmed`, which is stable and parseable. Needs nothing beyond
your Google connection and reads Gmail **metadata only** — subjects and dates,
never bodies.

**From a session cookie (optional).** Sign in, copy the cookie from DevTools →
Application → Cookies. Weigh it up first: a session cookie is a full credential
for your account, it dies with the session, and CaterCow's signed-in HTML isn't
something this project can see — so the date pattern is a configurable regex
rather than a tested parser. **Inspect that page** in the wizard reports what a
fetch returned (whether it looks signed in, dates seen, which patterns matched)
so you can tune `catercow.selected_pattern`.

The email route is the verified one. Configure which days count:

```json
"catercow": { "lunch_days": [0, 1, 2, 3, 4], "horizon_days": 14 }
```

`0` is Monday. A day appears only if it's in that list, isn't in the past, and has
no confirmation. If neither extractor can read anything the lane says so, rather
than implying every day is sorted.
</details>

### Environment variables

Any of these override the file, handy if you keep secrets elsewhere:

`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_JQL`, `GITHUB_TOKEN`,
`GITHUB_EXTRA_QUERY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_REFRESH_TOKEN`, `GMAIL_QUERY`, `SLACK_TOKEN`, `CATERCOW_COOKIE`,
`TODAY_HOST`, `TODAY_PORT`, `TODAY_HOME`

If one shadows something you save in the wizard, the wizard tells you instead of
letting the save vanish into a void.

## Keyboard

| Key | Action |
| --- | --- |
| `n` | Focus the new-task field |
| `r` | Refresh now |
| `f` | Cycle the PR filter (All / To review / Mine) |
| `s` | Open the connections wizard |
| `Esc` | Close the wizard, or leave a text field |

## Commands

```bash
today                  # run the dashboard
today serve --port 9000
today where            # print config, data and backup locations
today backup           # snapshot config.json (0600, never leaves the machine)
today backup --list
today backup --restore
```

`--restore` takes the newest snapshot or a named one, and snapshots the current
config first so restoring can't lose what you had.

## Your credentials

`config.json` holds live API tokens. It's written `0600` in a `0700` directory,
atomically, and **tokens are write-only over HTTP** — the browser can send one but
never read one back. `GET /api/settings` returns masked hints like `••••••••9f2a`
that prove something is stored without disclosing it.

The server binds `127.0.0.1` and has no authentication, which is the right trade
for a personal tool. Don't expose it without putting real auth in front.

Full detail in [SECURITY.md](SECURITY.md).

## How it works

- `server.py` — routing and feed assembly. All remote sources are fetched
  concurrently, then cached for `server.cache_ttl_seconds` (default 60s) so the
  UI's auto-refresh doesn't burn API quota. `?refresh=1` bypasses it.
- `sources/*.py` — one module per integration, each exposing `fetch()` and
  `verify()`. All HTTP goes through `sources/http_json.py`.
- `static/` — one HTML file, one stylesheet, two scripts. No framework, no build.

A failing source degrades to a message inside its own lane, and each renderer runs
in its own `try`/`catch`, so one bad lane can't blank the other six.

```
src/today_dashboard/
  cli.py             today serve | backup | where
  server.py          HTTP server, settings API, OAuth callback
  config.py          config resolution, 0600 saving, masking
  store.py           todos, seen baselines, TTL cache
  backup.py          snapshot / restore
  fixtures.py        demo data for every source
  sources/
    http_json.py     urllib JSON + form helper, ApiError
    jira.py  github.py  google_auth.py  gcal.py  gmail.py  slack.py  catercow.py
  static/            index.html  styles.css  app.js  settings.js
tests/               unittest, no dependencies, no network
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — in particular the two rules that shape
this codebase: no dependencies, and never let a lane go quiet on failure.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -t tests
```

## Licence

[MIT](LICENSE).
