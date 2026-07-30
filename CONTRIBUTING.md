# Contributing

## Getting set up

```bash
git clone https://github.com/adangerbartels/today-dashboard && cd today-dashboard
PYTHONPATH=src python3 -m unittest discover -s tests -t tests
```

The tests need the package importable. Either install it editable
(`pip install -e .`) or prefix with `PYTHONPATH=src`. There are no test
dependencies and no network access — the suite runs in well under a second.

```bash
python3 today.py          # runs from the clone, nothing installed
```

## Ground rules

**No dependencies.** Not a stylistic preference: this program holds your API
tokens, and every added package is another maintainer who could reach them. If
something seems to need a library, it probably needs twenty lines of
`urllib` instead. `pyproject.toml` has an empty `dependencies` list and it should
stay that way.

**Every lane is a filter, not a feed.** The value here is subtraction. A lane
earns its place by showing less than the underlying tool does. If a change makes
a lane show *more*, it needs a good argument.

**Never go quiet on failure.** An empty lane must mean "nothing needs you", never
"something broke and I didn't mention it". If a source can't be read, say so in
the lane; if a count can't be established, show `—` rather than `0`; if results
were truncated or filtered, say how many were withheld. Most of the bugs found
in this project so far were variations on a lane looking reassuring when it
wasn't.

**Errors should name the fix.** `HTTP 500` is not a message. Translate provider
errors into the action the reader should take — which scope to add, which button
to press, which value to re-copy.

## Adding an integration

A source module is a plain module in `src/today_dashboard/sources/` exposing:

```python
def fetch(cfg, ...):
    """Return a dict with at least {"items": [...]}. Raise ApiError to fail."""

def verify(cfg, ...):
    """Prove the credentials work. Return {"account", "count", "notes": [...]}."""
```

Route all HTTP through `sources/http_json.py` so error handling, gzip and
timeouts stay consistent, and raise `ApiError` for anything the user must act on.
Then wire it up:

| Where | What to add |
| --- | --- |
| `config.py` | defaults, `SECRET_KEYS`, `SOURCE_ENV`, `SECTIONS`, a `*_configured()` |
| `server.py` | a `_load_*()` feed loader, `SOURCE_FIELDS`, validation, verify branch |
| `fixtures.py` | demo data, so the lane is visible before anyone configures it |
| `static/index.html` | a lane and a wizard panel |
| `static/app.js` | a `render*()` added to the lane list in `render()` |
| `tests/` | filtering rules, and at least one degraded path |

Each renderer is called inside its own `try`/`catch`, so a bug in one lane
degrades that lane instead of blanking the dashboard. Keep it that way.

## Secrets

Never commit `config.json`, `data/` or `backups/` — all three are gitignored.
Before opening a pull request, check you haven't pasted a real token, hostname or
account name into a test fixture, a docstring or an example. Fixtures use
`.test` domains and obviously fake identifiers on purpose.

## Style

Match the surrounding code: standard library idioms, comments that explain *why*
rather than restating the line, and no type annotations (the codebase has none —
adding them piecemeal is worse than either extreme).
