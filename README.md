# nc-bourbon-finder

Email alerts when allocated bourbons (Eagle Rare, Blanton's, ...) ship from the
NC ABC warehouse to watched local boards.

Data source: `https://abc2.nc.gov/Search/StockShippedData` - an unauthenticated
JSON feed of all shipments to all ~172 NC boards over a rolling 16-day window,
refreshed roughly daily (~11am ET).

A stdlib-only Python script (`check.py`) runs on a GitHub Actions cron, diffs the
feed against committed state (`state/latest.json`), and emails a summary via Gmail
SMTP when a watched product's shipment count to a watched board goes up.

See [SPEC.md](SPEC.md) for the full design and [PLAN.md](PLAN.md) for the original notes.

## How it works

- `config.toml` - the watchlists: which products (by 5-digit NC code) and which boards.
- `state/latest.json` - the last-seen snapshot, committed back on every change. Doubles as a free historical dataset.
- Each run: fetch feed → if the extract timestamp is unchanged, no-op → diff watched products for watched boards → email new/increased shipments → commit new state.

Alerts fire on **increases** only. A decrease just means an old shipment aged out of the 16-day window.

## Setup (one-time)

1. **Gmail app password.** With 2FA enabled, create one at
   https://myaccount.google.com/apppasswords (this is the value for `GMAIL_APP_PASSWORD`).
2. **Repo secrets** (Settings → Secrets and variables → Actions, or via `gh`):
   ```sh
   gh secret set GMAIL_ADDRESS         # the sending Gmail address
   gh secret set GMAIL_APP_PASSWORD    # the app password from step 1
   gh secret set ALERT_RECIPIENTS      # comma-separated recipient addresses
   ```
   Recipients live in a secret (not `config.toml`) so addresses stay out of the public repo.
3. **Initialize state.** Trigger the workflow once (Actions tab → `check` → Run workflow,
   or `gh workflow run check.yml`). The first run seeds `state/latest.json` and sends no alert.
4. **Confirm email** delivery: `python check.py --test-email` locally with the three env
   vars set, then check your inbox (and spam).

## Editing the watchlists

Edit `config.toml` and commit:

- **Products:** add `NCCODE = "Label"` under `[products]`. The code must be the exact
  5-digit zero-padded NC code from the feed (e.g. `27169`, or `00124` for code 124).
- **Boards:** add the exact `boardName` string to the `boards` array. It must match the
  feed exactly - a typo fails the workflow run (which GitHub emails you about), rather
  than silently missing alerts.

## Local usage

```sh
python check.py --dry-run      # full pipeline; print the would-be email, don't send or write state
python check.py --test-email   # send a test email to ALERT_RECIPIENTS (needs the env vars)
python check.py                # the real run (used by the workflow)
```

Requires Python 3.12+ (stdlib only - no dependencies to install).
