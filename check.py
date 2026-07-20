#!/usr/bin/env python3
"""nc-bourbon-finder: email alerts for NC ABC allocated bourbon shipments.

Fetches the NC ABC StockShipped JSON feed, diffs whitelisted products against a
committed snapshot, and emails a summary when watched products ship (or restock)
to watched boards. Python 3.12 stdlib only.

See SPEC.md for the full behavior contract.
"""

import argparse
import json
import os
import smtplib
import sys
import tomllib
import urllib.request
from email.message import EmailMessage
from pathlib import Path

FEED_URL = "https://abc2.nc.gov/Search/StockShippedData"
HUMAN_URL = "https://abc2.nc.gov/Search/StockShipped"
LOCATOR_URL = "https://abc2.nc.gov/Search/ABCStoreLocator"
USER_AGENT = "nc-bourbon-finder (github actions cron)"
FETCH_TIMEOUT = 30

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.toml"
STATE_PATH = ROOT / "state" / "latest.json"
STORES_PATH = ROOT / "stores.json"


def load_config():
    with CONFIG_PATH.open("rb") as f:
        cfg = tomllib.load(f)
    products = {str(code): label for code, label in cfg.get("products", {}).items()}
    boards = list(cfg.get("boards", []))
    if not products:
        die("config.toml has no [products]")
    if not boards:
        die("config.toml has no boards")
    return products, boards


def fetch_feed():
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return json.load(resp)


def validate_config(feed, products, boards):
    codes = set(feed["lookups"]["codes"])
    valid_boards = set(feed["lookups"]["boards"])
    bad_codes = [c for c in products if c not in codes]
    bad_boards = [b for b in boards if b not in valid_boards]
    if bad_codes or bad_boards:
        msgs = []
        if bad_codes:
            hint = ""
            if any(len(c) != 5 for c in bad_codes):
                hint = " (codes must be 5-digit zero-padded, e.g. code 124 -> \"00124\")"
            msgs.append(f"unknown product codes: {bad_codes}{hint}")
        if bad_boards:
            msgs.append(f"unknown boards: {bad_boards}")
        die("; ".join(msgs))


def load_state():
    if not STATE_PATH.exists():
        return None
    with STATE_PATH.open() as f:
        return json.load(f)


def load_stores():
    """Optional board -> store map (from the state store locator). Missing file
    just disables store-level enrichment."""
    if not STORES_PATH.exists():
        return {}
    with STORES_PATH.open() as f:
        return json.load(f)


def store_hint(board, stores):
    """A short store-level suffix for an alert line.

    Single-store boards resolve to the exact store+phone (board == store).
    Multi-store boards can't be resolved from the board-level feed, so we say
    how many stores and point at the locator."""
    info = stores.get(board)
    if not info:
        return ""
    if info["single_store"] and info["stores"]:
        s = info["stores"][0]
        return f" @ {s['address']} ({s['phone']})"
    n = len(info["stores"])
    if n > 1:
        return f" (1 of {n} stores - which one TBD, see locator)"
    return ""


def write_state(extract_datetime, units):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATE_PATH.open("w") as f:
        json.dump(
            {"extractDatetime": extract_datetime, "units": units},
            f,
            sort_keys=True,
            indent=2,
        )
        f.write("\n")


def build_current(records, products):
    """Units for watched products across ALL boards. Keeps state small but lets
    the user add a board later without a spurious baseline alert."""
    current = {}
    for r in records:
        code = r["NCcode"]
        if code in products:
            current[f"{code}|{r['boardName']}"] = r["NUMUNITS"]
    return current


def diff(current, state, products, watched_boards):
    """Hits for watched boards where a pair is new or NUMUNITS increased."""
    prev = state["units"] if state else {}
    hits = []
    for key, total in current.items():
        code, board = key.split("|", 1)
        if board not in watched_boards:
            continue
        before = prev.get(key, 0)
        if total > before:
            hits.append(
                {
                    "code": code,
                    "label": products[code],
                    "board": board,
                    "delta": total - before,
                    "total": total,
                }
            )
    hits.sort(key=lambda h: h["delta"], reverse=True)
    return hits


def format_email(hits, extract_datetime, stores=None):
    stores = stores or {}
    lead = hits[0]
    subject = f"NC ABC: {lead['label']} +{lead['delta']} at {short_board(lead['board'])}"
    if len(hits) > 1:
        subject += f" (+{len(hits) - 1} more)"
    lines = [
        f"{h['label']} - {h['board']}: +{h['delta']} (now {h['total']} bottles)"
        f"{store_hint(h['board'], stores)}"
        for h in hits
    ]
    body = "\n".join(lines)
    body += f"\n\nExtract: {extract_datetime}\n{HUMAN_URL}"
    # Only point at the locator when we actually know a hit is multi-store.
    if any(h["board"] in stores and not stores[h["board"]]["single_store"] for h in hits):
        body += f"\nStore locator: {LOCATOR_URL}"
    body += "\n"
    return subject, body


def short_board(board):
    return board.replace(" ABC Board", "")


def send_email(subject, body):
    address = require_env("GMAIL_ADDRESS")
    password = require_env("GMAIL_APP_PASSWORD")
    recipients = parse_recipients()
    msg = EmailMessage()
    msg["From"] = address
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=FETCH_TIMEOUT) as smtp:
        smtp.starttls()
        smtp.login(address, password)
        smtp.send_message(msg)


def parse_recipients():
    raw = require_env("ALERT_RECIPIENTS")
    recipients = [r.strip() for r in raw.split(",") if r.strip()]
    if not recipients:
        die("ALERT_RECIPIENTS is empty")
    return recipients


def require_env(name):
    value = os.environ.get(name)
    if not value:
        die(f"missing required env var: {name}")
    return value


def die(msg, code=2):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def cmd_test_email():
    send_email(
        "nc-bourbon-finder: test email",
        "Hello from nc-bourbon-finder. SMTP plumbing works.\n",
    )
    print("test email sent")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the full pipeline but print the email instead of sending, and don't write state",
    )
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="send a fixed test email to verify SMTP, then exit",
    )
    args = parser.parse_args()

    if args.test_email:
        cmd_test_email()
        return

    products, boards = load_config()
    watched_boards = set(boards)

    feed = fetch_feed()
    validate_config(feed, products, boards)

    extract_datetime = feed["metadata"]["extractDatetime"]
    state = load_state()

    if state is not None and state.get("extractDatetime") == extract_datetime:
        print(f"no-op: extract unchanged ({extract_datetime})")
        return

    current = build_current(feed["records"], products)
    hits = diff(current, state, products, watched_boards)

    if state is None:
        write_state(extract_datetime, current)
        print("initialized state")
        return

    if hits:
        subject, body = format_email(hits, extract_datetime, load_stores())
        if args.dry_run:
            print(f"--- would send ---\nSubject: {subject}\n\n{body}")
        else:
            send_email(subject, body)

    if not args.dry_run:
        write_state(extract_datetime, current)

    print(f"{len(hits)} hit(s); state {'unchanged (dry-run)' if args.dry_run else 'updated'}")


if __name__ == "__main__":
    main()
