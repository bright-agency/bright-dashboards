"""Client data (build/<slug>/data.json, never committed): daily campaign rows for every platform/account,
reach per preset period, current campaign status. Usage: CLIENT=<slug> python engine/fetch_client.py"""
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
from common import HISTORY_START, PLATFORMS, SLUG, accounts, campaign_status, make_row, presets, row_fields, write_data  # noqa: E402
from windsor import KEY, get, rate_for, usd_ils  # noqa: E402

REACH = {"meta": "reach", "linkedin": "approximate_unique_impressions", "tiktok": "reach"}
STATUS = {
    "meta": (["campaign", "campaign_effective_status", "campaign_stop_time"], "campaign_effective_status", "campaign_stop_time"),
    "google": (["campaign", "campaign_status"], "campaign_status", None),
    "linkedin": (["campaign", "campaign_status", "campaign_end_date"], "campaign_status", "campaign_end_date"),
}


def main():
    if not KEY:
        sys.exit("WINDSOR_API_KEY is not set")
    now = datetime.now(ZoneInfo("Asia/Jerusalem"))
    as_of = (now - timedelta(days=1)).date()
    fx = usd_ils(HISTORY_START, as_of)
    plats = [p for p in PLATFORMS if accounts(p)]

    rows = []
    for p in plats:
        for r in get(p, row_fields(p), HISTORY_START, as_of):
            if r.get("date") and r.get("campaign"):
                rows.append({**make_row(r, p, rate_for(r, fx)), "a": r["_acc"]})

    # Reach is not additive across days: account totals for every preset, per-campaign only for 7 days (alerts).
    reach = {}
    for key, (a, b) in presets(as_of).items():
        reach[key] = {}
        for p in plats:
            if p not in REACH:
                continue
            tot = get(p, ["account_id", REACH[p]], a, b)
            block = {"total": int(sum(float(r.get(REACH[p]) or 0) for r in tot)), "by": {}}
            if key == "last7":
                block["by"] = {r["campaign"]: int(float(r.get(REACH[p]) or 0))
                               for r in get(p, ["campaign", REACH[p]], a, b) if r.get("campaign")}
            reach[key][p] = block

    status = {}
    for p in plats:
        if p not in STATUS:
            continue
        fields, st_f, end_f = STATUS[p]
        status[p] = {r["campaign"]: campaign_status(r.get(st_f), r.get(end_f) if end_f else None, as_of)
                     for r in get(p, fields, HISTORY_START, as_of) if r.get("campaign")}

    if not rows:
        sys.exit(f"[{SLUG}] Windsor returned no rows")
    write_data(as_of, rows, reach, now.isoformat(timespec="minutes"), status)


if __name__ == "__main__":
    main()
