# Plan: `nc-abc-alerts` — email alerts for NC ABC allocated bourbon shipments

## Context

Prior research (this session) established that the NC ABC Commission exposes an unauthenticated JSON feed, `https://abc2.nc.gov/Search/StockShippedData`, containing every warehouse shipment to every local board over a rolling 16-day window (~83k records, refreshed roughly daily ~11am ET; `metadata.extractDatetime` marks each extract). Records are aggregates per (product, board): `{NUMUNITS, ProductName, boardName, NCcode, item_id}`.

The user (a software engineer) wants near-real-time email alerts when whitelisted products (e.g., Eagle Rare 10Y = 27169, Blanton's = 27090) ship to watched boards, for 1–5 recipients. Decisions made via Q&A:

- **Runner: GitHub Actions** (user preference; "lazy persistent" beats the Pi). Public repo → unlimited free minutes; no hardware.
- **Email: Gmail SMTP with an app password** (easiest persistent option; works from Actions runners via `smtp.gmail.com:587` + repo secrets).
- **Scope: configurable boards, seeded with a WNC region default** (Buncombe's four boards plus nearby western-NC boards).
- No frontend. Config edited by hand in the repo.

Since the upstream feed refreshes ~daily, polling every 15–60 min is already "instant" relative to the data; reliability matters more than frequency.

## Approach (chosen over alternatives)

**GitHub Actions cron + git-scraping state + stdlib-only Python.** State (the last seen snapshot) is committed back to the repo on change — this doubles as a free historical dataset of shipments and survives runner ephemerality. Rejected: Raspberry Pi (more real-time but requires hardware babysitting the user doesn't want); Fly.io (costs money, overkill for a 30-second daily diff).

## Repo layout (new public repo: `~/Code/nc-abc-alerts`)

```
check.py                      # the whole program, Python 3.12 stdlib only (~150-200 lines)
config.toml                   # products + boards watchlists (NOT recipients — see secrets)
state/latest.json             # committed snapshot: extractDatetime + {"<NCcode>|<board>": units}
.github/workflows/check.yml   # cron + workflow_dispatch
README.md                     # setup: app password, secrets, adding products/boards
```

## Components

### `config.toml`
```toml
[products]            # NC code -> human label (label used in email subject)
27169 = "Eagle Rare 10Y"
27090 = "Blanton's Single Barrel"

boards = [            # exact boardName strings from the feed
  "Asheville ABC Board", "Black Mountain ABC Board",
  "Weaverville ABC Board", "Woodfin ABC Board",
  # ...seed ~6 more WNC boards (Henderson County, Fletcher, Brevard,
  # Canton, Haywood/Waynesville, Marion) — exact names verified against
  # the feed's `lookups` key at implementation time
]
```
Recipients live in a repo secret (`ALERT_RECIPIENTS`, comma-separated), not in the public repo, to avoid email harvesting.

### `check.py` (single file, no deps)
Flow: fetch feed (urllib) → if `extractDatetime` == state's, exit 0 silently (no-op run) → build current map for **whitelisted products across ALL boards** (keeps state small but lets the user add a board later without spurious-baseline issues) → diff against `state/latest.json` for **watched boards only**: alert when a (product, board) pair is new or `NUMUNITS` increased → send one summary email per run via smtplib/STARTTLS listing all hits (product, board, +delta, total bottles, link to https://abc2.nc.gov/Search/StockShipped) → write new state file.

- Config validation: fail loudly (non-zero exit) if a configured board name or product code doesn't appear in the feed's `lookups` — typos surface as a failed workflow, which GitHub auto-emails the owner.
- Env vars: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `ALERT_RECIPIENTS`.
- Flags: `--dry-run` (print would-be email, don't send, don't write state), `--test-email` (send a hello-world email to verify SMTP plumbing).
- First-ever run (no state file): initialize state, send no alerts.
- Known accepted edge: `NUMUNITS` is a 16-day rolling aggregate, so an age-off coinciding with a same-size new shipment nets to no change and is missed; alert-on-increase-only is otherwise correct (decreases are just old shipments aging out).

### `.github/workflows/check.yml`
- Triggers: `workflow_dispatch` + one cron: `30 14-18 * * *` (UTC) = hourly at :30 from 10:30a to 2:30p ET, 5 runs/day, bracketing the ~11am ET extract with retry redundancy. The extract timestamp is empirically ~11:0x ET (verified from two independent snapshots 13 months apart: `2025-06-23 11:01:27` and `2026-07-20 11:03:56`); the 4-hour window absorbs jitter and transient fetch failures. The script no-ops when `extractDatetime` is unchanged, so extra polls are free. Free on a public repo; Actions cron jitter is fine given daily upstream cadence.
  - **DST note:** GitHub Actions cron is UTC-only. `30 14-18` is exact during EDT (summer); during EST (winter) it shifts to 9:30a-1:30p ET, which still comfortably brackets the 11am extract. If you'd rather hold 10:30a-2:30p ET year-round, bump the cron to `30 15-19 * * *` at the fall DST transition.
  - **Confirm cadence from git history:** true update frequency isn't proven (only two historical datapoints, both ~11am). The git-scraped `state/latest.json` commit history *is* the cadence measurement — after ~2 weeks live, tighten or widen this window based on the observed `extractDatetime` rate.
- Steps: checkout → setup-python → `python check.py` → if `state/latest.json` changed, commit & push with message `chore(state): extract <extractDatetime>` (use the standard `github-actions[bot]` commit pattern; `permissions: contents: write`).
- Workflow failures (fetch errors, config typos, SMTP failures) rely on GitHub's built-in failure-notification emails to the repo owner — no custom dead-man's switch needed.

## Setup steps (one-time, user-assisted)

1. Create Gmail app password (user does this at https://myaccount.google.com/apppasswords — requires 2FA).
2. `gh repo create nc-abc-alerts --public`; push scaffold.
3. `gh secret set` × 3: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `ALERT_RECIPIENTS`.
4. Run `workflow_dispatch` once to initialize state; run `check.py --test-email` locally or via a dispatch input to confirm delivery.

## Verification

1. **Unit-ish local test:** run `python check.py --dry-run` against live feed with a hand-edited `state/latest.json` (decrement Eagle Rare's Asheville units) → confirm the diff detects the increase and prints the email body.
2. **SMTP test:** `--test-email` → confirm email lands in inbox (check spam).
3. **End-to-end:** push, trigger `workflow_dispatch`, confirm: state file committed by the bot, no alert on first run, second dispatch is a silent no-op (same extractDatetime).
4. **Live validation:** after the next real upstream extract (~11am ET next business day), confirm either a state-update commit (no watched changes) or an alert email (watched shipment).
