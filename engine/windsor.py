"""Windsor.ai REST access (needs env WINDSOR_API_KEY) + USD->ILS rates."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from common import CONNECTOR, accounts, fx_lookup

API = "https://connectors.windsor.ai/{connector}"
KEY = os.environ.get("WINDSOR_API_KEY", "").strip()
CALLS = {"n": 0}


def http_json(url: str, tries: int = 3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bright-dashboards/1.0"})
            with urllib.request.urlopen(req, timeout=240) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code < 500 and e.code != 429:
                raise                      # 4xx (except rate limit) will not fix itself
            if attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))
            print(f"  retry after {e}")


def get_raw(connector: str, account: str, fields: list, date_from, date_to) -> list:
    if not account:
        return []
    CALLS["n"] += 1
    qs = urllib.parse.urlencode({
        "api_key": KEY, "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
        "fields": ",".join(fields), "select_accounts": account})
    try:
        body = http_json(API.format(connector=connector) + "?" + qs)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Windsor {connector} {account} HTTP {e.code} fields={','.join(fields)}") from None
    return body.get("data", []) if isinstance(body, dict) else body


def get(platform: str, fields: list, date_from, date_to) -> list:
    """All accounts of a platform; every row gets _acc (account id) and _cur (account currency)."""
    out = []
    for a in accounts(platform):
        for r in get_raw(CONNECTOR[platform], a["id"], fields, date_from, date_to):
            r["_acc"], r["_cur"] = a["id"], a.get("currency", "ILS")
            out.append(r)
    return out


def usd_ils(date_from, date_to):
    url = f"https://api.frankfurter.dev/v1/{(date_from - timedelta(days=7)).isoformat()}..{date_to.isoformat()}?from=USD&to=ILS"
    body = http_json(url)
    return fx_lookup({d: v["ILS"] for d, v in body["rates"].items()})


def rate_for(row: dict, fx, day: str = None) -> float:
    return fx(day or row.get("date") or "9999") if row.get("_cur", "ILS") == "USD" else 1.0
