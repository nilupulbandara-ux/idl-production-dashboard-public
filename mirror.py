#!/usr/bin/env python3
"""Mirror IDL production dashboard feed into one bundle.json and POST it to FLOW_URL.

Stdlib only. Env: FLOW_URL (secret, never printed), DRY_RUN=1 (fetch+validate only).
Exit non-zero on any fetch/validation/upload failure (nothing uploaded, last.hash kept).
"""
import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "https://idl-production-dashboard.nilupulbandara.chatgpt.site"
PLAN_START = dt.date(2026, 9, 1)
HERE = os.path.dirname(os.path.abspath(__file__))
HASH_FILE = os.path.join(HERE, "last.hash")
UA = "Mozilla/5.0 IDL-Mirror"


class Missing(Exception):
    """Endpoint answered but has no data (4xx)."""


def get_raw(rel, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(BASE + "/" + rel, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            text = raw.decode("utf-8").lstrip("\ufeff")
            json.loads(text)  # must parse
            return text
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code not in (403, 408, 429):
                raise Missing(f"{rel}: HTTP {e.code}")
            last = f"{rel}: HTTP {e.code}"
        except Exception as e:  # network, decode, bad JSON
            last = f"{rel}: {type(e).__name__}"
        time.sleep(2 * (i + 1))
    raise RuntimeError(last)


def main():
    items = {}
    items["api/feed"] = get_raw("api/feed")
    months = json.loads(items["api/feed"]).get("availableMonths") or []
    if not months:
        raise RuntimeError("availableMonths empty")
    for m in months:
        items[f"api/feed?month={m}"] = get_raw(f"api/feed?month={m}")
        try:
            items[f"api/performance?period={m}"] = get_raw(f"api/performance?period={m}")
        except Missing as e:
            print(f"skipped {e}")
    try:
        items["api/daily-plan?pair=1"] = get_raw("api/daily-plan?pair=1")
    except Missing as e:
        print(f"skipped {e}")
    colombo_today = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)).date()
    d = PLAN_START
    while d <= colombo_today + dt.timedelta(days=3):
        k = d.isoformat()
        try:
            items[f"api/daily-plan?date={k}"] = get_raw(f"api/daily-plan?date={k}")
        except Missing:
            pass  # no plan that day
        d += dt.timedelta(days=1)

    body = ",".join(json.dumps(k) + ":" + v for k, v in items.items())
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest().upper()
    fetched = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bundle = '{"_fetchedAtUtc":"%s","_count":%d,%s}' % (fetched, len(items), body)
    json.loads(bundle)  # final validation
    data = bundle.encode("utf-8")
    print(f"items={len(items)} size={len(data)/1024:.0f}KB sha={sha[:12]}")

    if os.environ.get("DRY_RUN") == "1":
        print("dry run: no POST")
        return 0
    old = ""
    if os.path.exists(HASH_FILE):
        with open(HASH_FILE, encoding="ascii") as f:
            old = f.read().strip()
    if old == sha:
        print("unchanged, skip upload")
        return 0
    url = os.environ.get("FLOW_URL", "").strip()
    if not url:
        raise RuntimeError("FLOW_URL not set")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception as e:
        raise RuntimeError(f"POST failed: {type(e).__name__}")  # no URL in message
    if status != 200:
        raise RuntimeError(f"POST returned HTTP {status}, need 200")
    with open(HASH_FILE, "w", encoding="ascii", newline="\n") as f:
        f.write(sha + "\n")
    print("uploaded ok, hash updated")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)
