# Security

## What this program holds

Running Today means giving it read access to your calendar, mail, issue tracker
and chat. It stores the credentials for that access on disk. Treat
`config.json` as you would an SSH private key.

| | |
| --- | --- |
| Where | `./config.json` next to a clone, otherwise `~/.config/today-dashboard/config.json` |
| Permissions | file `0600`, directory `0700`, written atomically |
| Contents | Jira API token, GitHub token, Google OAuth client secret and refresh token, Slack token (and refresh token), CaterCow session cookie |

`today where` prints the exact paths.

## Design choices worth knowing

**Localhost only, no authentication.** The server binds `127.0.0.1` and anyone
who can reach it can read your data and change your settings. That is a
deliberate trade for a personal tool. If you put it behind a tunnel, a reverse
proxy or `0.0.0.0`, put real authentication in front of it first.

**Tokens are write-only over HTTP.** The browser can send a credential to the
server but can never read one back. `GET /api/settings` returns masked hints
(`••••••••9f2a`) that prove something is stored without disclosing it, so a
stale tab or a screenshot cannot leak a token. The setup wizard also clears and
re-masks its fields when dismissed.

**Read-only scopes.** Every integration asks for the least it can:
`gmail.readonly`, `calendar.readonly`, Slack `*:read`/`*:history`, a GitHub token
you scope yourself. Nothing in this project writes to a remote service. Gmail is
requested at `readonly` rather than `metadata` only because the metadata scope
rejects the search parameter the filtering depends on; message bodies are never
fetched.

**Google OAuth** uses the installed-app loopback flow with PKCE. Only the
refresh token is persisted; access tokens live in memory. Disconnecting revokes
the grant with Google, not just locally.

**No dependencies.** The standard library only, so there is no third-party
package in the path that handles your tokens.

**Outbound requests** go only to the APIs of services you configure:
`*.atlassian.net`, `api.github.com`, `*.googleapis.com`, `accounts.google.com`,
`slack.com`, and the CaterCow host if you enable it. Nothing is sent anywhere
else, and there is no telemetry.

## Backups

`today backup` writes snapshots of `config.json` — including the tokens — to
`backups/` at `0600`. They never leave the machine. Delete them if you rotate
credentials and want the old ones gone.

## Reporting a vulnerability

Open a GitHub issue for anything low-risk. For something that would expose
credentials, please use GitHub's private vulnerability reporting on the
repository's Security tab rather than a public issue.

There is no security guarantee attached to this software; see the MIT licence.
