# Tech Spec: `nc-abc-alerts` - email alerts for NC ABC allocated bourbon shipments

Status: ready to implement. Derived from `PLAN.md`; feed schema below verified live against `https://abc2.nc.gov/Search/StockShippedData` on 2026-07-20 (extract `2026-07-20 11:03:56`, 85,511 records).

## 1. Context

The NC ABC Commission publishes an unauthenticated JSON feed of every warehouse shipment to every local ABC board over a rolling ~16-day window. It refreshes roughly daily at ~11:0x ET (`metadata.extractDatetime`). The user (a software engineer) wants near-real-time email alerts, for 1-5 recipients, when whitelisted products (e.g. Eagle Rare 10Y, Blanton's) ship to watched boards in western NC.

Because upstream refreshes only ~daily, polling several times a day is already "instant" relative to the data. Reliability and simplicity matter more than frequency. The design is a GitHub Actions cron that runs a single stdlib-only Python script and commits the last-seen snapshot back to the repo (git-scraping), which also yields a free historical dataset.

Naming note: `PLAN.md` referred to a repo `nc-abc-alerts`, but this project lives in and keeps the name `nc-bourbon-finder`.

## 2. Verified feed schema (source of truth)

Endpoint: `GET https://abc2.nc.gov/Search/StockShippedData` → `application/json`, ~18 MB.

```
{
  "metadata": { "extractDatetime": "2026-07-20 11:03:56" },   // local ET, space-separated
  "lookups": {
    "codes":    ["00026", "00028", ...],   // len 2710, parallel to products[]
    "products": ["-196 Combo ...",  ...],   // codes[i] <-> products[i]
    "boards":   ["Alamance Municipal ABC Board", ...]  // len 172, flat list of exact names
  },
  "records": [
    { "NUMUNITS": 6, "ProductName": "Wyoming Whiskey Small Batch .75L",
      "boardName": "Wake County ABC Board", "website": "www.wakeabc.com",
      "item_id": 159783, "NCcode": "00026" },
    ...   // 85,511 rows
  ]
}
```

Verified invariants the implementation relies on:

- **`NCcode` is always a 5-char zero-padded string.** All codes in both `records` and `lookups.codes` have length 5. Config product codes MUST be 5-digit zero-padded strings to match (`27169`, `27090` already are; a hypothetical code 124 would be `"00124"`).
- **`(NCcode, boardName)` is unique across records** - each record already is the per-(product, board) aggregate. No summing required; the diff key is exactly this pair.
- **`NUMUNITS`** is the count over the rolling ~16-day window (a decrease = old shipment aged off, not a return).
- **`website`** is per board and may be empty (`""`, e.g. Woodfin). Do not rely on it for links.
- **`lookups` is three parallel arrays**, not a dict. Validate a product code with `code in set(lookups["codes"])` and a board with `board in set(lookups["boards"])`.
- Target products confirmed present: `27169` = "Eagle Rare 10Y .75L", `27090` = "Blanton's Single Barrel .75L". All 9 WNC boards below confirmed present.

## 3. Repo layout

```
check.py                      # entire program, Python 3.12 stdlib only
config.toml                   # products + boards watchlists (public; NO recipients)
state/latest.json             # committed snapshot (git-scraped state + free dataset)
.github/workflows/check.yml   # cron + workflow_dispatch
README.md                     # setup: app password, secrets, editing watchlists
```

## 4. `config.toml`

```toml
# boards is a root-level key and MUST come before the [products] table -
# in TOML any key after a table header belongs to that table.
boards = [                     # exact boardName strings (verified against lookups.boards)
  "Asheville ABC Board",
  "Black Mountain ABC Board",
  "Weaverville ABC Board",
  "Woodfin ABC Board",
  "Fletcher ABC Board",
  "Brevard ABC Board",
  "Canton ABC Board",
  "Waynesville ABC Board",
  "Marion ABC Board",
]

[products]                     # 5-digit zero-padded NCcode -> label used in email subject
27169 = "Eagle Rare 10Y"
27090 = "Blanton's Single Barrel"
```

All 9 seed boards are verified to exist in the current feed. Parse with `tomllib` (stdlib in 3.11+). TOML bare keys accept leading digits, so `27169` parses as the string key `"27169"`.

Recipients are NOT in this file. They live in the `ALERT_RECIPIENTS` repo secret (comma-separated) to keep addresses out of the public repo.

## 5. `state/latest.json` schema

```json
{
  "extractDatetime": "2026-07-20 11:03:56",
  "units": {
    "27169|Asheville ABC Board": 12,
    "27090|Woodfin ABC Board": 6
  }
}
```

- Key format: `f"{NCcode}|{boardName}"`.
- **Store all watched products across ALL boards**, not just watched boards. This keeps the map small (currently only ~4 rows total for the 2 products) while letting the user add a board later without it firing a spurious baseline alert (the board's history is already in state).
- Alerts, however, are only emitted for **watched boards** (section 6).
- File is written with `sort_keys=True, indent=2` and a trailing newline for stable, reviewable git diffs.

## 6. `check.py` - behavior

Single file, stdlib only (`urllib.request`, `json`, `tomllib`, `smtplib`, `email.message`, `argparse`, `os`, `sys`, `pathlib`). Target ~150-200 lines.

### Main flow

1. Parse args (section 6.3) and load `config.toml`.
2. **Fetch** feed via `urllib.request` with a timeout (30s) and a `User-Agent` header. On any fetch/JSON error, print to stderr and exit non-zero (workflow fails → GitHub emails owner).
3. **Validate config against `lookups`** (section 6.1). On any unknown code/board, exit non-zero.
4. Load `state/latest.json` if present (else `state = None`, first-run).
5. **No-op short-circuit:** if `state` exists and `feed.metadata.extractDatetime == state["extractDatetime"]`, print `no-op: extract unchanged (<ts>)` and exit 0. Do not write state, do not send mail.
6. **Build `current` units map** for watched products across ALL boards: `{f"{r['NCcode']}|{r['boardName']}": r['NUMUNITS'] for r in records if r['NCcode'] in watched_products}`.
7. **Diff** (section 6.2) to produce alert hits, restricted to watched boards.
8. If first-run (`state is None`): write state, send NO alerts, print `initialized state`, exit 0.
9. If hits and not `--dry-run`: send one summary email (section 6.4).
10. Write new state (`{extractDatetime, units: current}`) unless `--dry-run`.
11. Print a one-line summary (`N hit(s); state updated`) and exit 0.

### 6.1 Config validation

```
codes = set(feed["lookups"]["codes"])
boards = set(feed["lookups"]["boards"])
bad_codes  = [c for c in config products      if c not in codes]
bad_boards = [b for b in config["boards"]     if b not in boards]
if bad_codes or bad_boards: -> stderr, exit 2
```

Typos surface as a failed workflow run (auto-emailed by GitHub), not silent misses.

### 6.2 Diff algorithm

For each key in `current` whose board is in the watched-boards set:

- `prev = state["units"].get(key, 0)` (missing = 0, so a brand-new pair counts as new).
- If `current[key] > prev`: emit a hit `{code, product_label, board, delta = current[key]-prev, total = current[key]}`.
- Decreases and unchanged values produce no hit.

Accepted, documented edge (from PLAN.md): because `NUMUNITS` is a rolling 16-day aggregate, an age-off exactly cancelling a same-size new shipment nets to zero change and is missed. Alert-on-increase-only is otherwise correct.

### 6.3 CLI flags

- `--dry-run`: run the full pipeline including diff; print the would-be email body to stdout; do NOT send email and do NOT write state.
- `--test-email`: send a fixed "hello from nc-abc-alerts" message to `ALERT_RECIPIENTS` to verify SMTP plumbing, then exit (skips fetch/diff).

### 6.4 Email

- Transport: `smtplib.SMTP("smtp.gmail.com", 587)` → `starttls()` → `login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)`.
- Build with `email.message.EmailMessage`. `From` = `GMAIL_ADDRESS`, `To` = recipients list, `Subject` = e.g. `NC ABC: Eagle Rare 10Y +12 at Asheville (+2 more)` (lead with the biggest/first hit; summarize count).
- Body (plain text): one line per hit - `<label> - <board>: +<delta> (now <total> bottles)` - followed by the extract timestamp and the human page link `https://abc2.nc.gov/Search/StockShipped`.
- One email per run listing all hits.

### 6.5 Environment variables

`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `ALERT_RECIPIENTS` (comma-separated). Required whenever email is actually sent; `--dry-run` must work without them.

## 7. `.github/workflows/check.yml`

- **Triggers:** `workflow_dispatch` + one cron `30 14-18 * * *` (UTC) = hourly at :30 from ~10:30a to 2:30p ET during EDT, bracketing the ~11:0x ET extract with retry redundancy. The no-op short-circuit makes extra polls free.
  - DST note: Actions cron is UTC-only. During EST this window shifts to 9:30a-1:30p ET, still bracketing 11am. To hold 10:30a-2:30p ET year-round, switch to `30 15-19 * * *` at the fall transition.
  - Cadence is only proven from two ~11am datapoints; use the `state/latest.json` commit history to measure the true refresh rate after ~2 weeks and tighten/widen the window.
- **`permissions: contents: write`** (to push state commits).
- **Steps:** checkout → `actions/setup-python` (3.12) → `python check.py` → if `state/latest.json` changed, commit as `github-actions[bot]` with message `chore(state): extract <extractDatetime>` and push.
- No custom dead-man's switch: fetch errors, config typos, and SMTP failures exit non-zero → GitHub's built-in failure notification emails the owner.

## 8. Secrets & one-time setup (user-assisted)

1. User creates a Gmail app password at `https://myaccount.google.com/apppasswords` (requires 2FA).
2. `gh repo create nc-abc-alerts --public` (or rename this repo); push scaffold.
3. `gh secret set` × 3: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `ALERT_RECIPIENTS`.
4. Trigger `workflow_dispatch` once to initialize `state/latest.json` (no alert on first run).

## 9. Verification

1. **Diff logic (local):** save a feed snapshot, hand-edit `state/latest.json` to decrement a watched pair (or delete it), run `python check.py --dry-run` → confirm the increase is detected and the email body prints. Also confirm a no-op when `extractDatetime` matches.
2. **SMTP:** `python check.py --test-email` (with the three env vars set) → confirm the message lands (check spam).
3. **End-to-end:** push, trigger `workflow_dispatch` → confirm (a) `state/latest.json` committed by the bot, (b) no alert on first run, (c) a second dispatch is a silent no-op (same `extractDatetime`).
4. **Live:** after the next real ~11am ET extract, confirm either a state-update commit (no watched change) or an alert email (watched shipment).

## 10. Out of scope

Frontend/UI; per-recipient filtering; product/board management outside hand-edited config; historical backfill beyond what git-scraping naturally accumulates; alerting on decreases.
