"""Local test: build one client from Windsor MCP dumps (no API key needed), same outputs as the workflow.

Usage: CLIENT=<slug> PLAIN=1 TEAM_PASSWORD=... DASHBOARD_MASTER_KEY=... python tools/seed_local.py <as_of> name=dump [...]
  names: meta_rows / google_rows / linkedin_rows / tiktok_rows (daily campaign rows), meta_status (dated rows with
  campaign_effective_status), plus any fetch_team dataset name (meta_ads, meta_ad_info, g_adgroup, ...).
"""
import json
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
from common import BUILD_DIR, CONFIG, SLUG, accounts, campaign_status, clean, make_row, write_data  # noqa: E402
import crypto  # noqa: E402
import fetch_team  # noqa: E402


def load(path):
    d = json.load(open(path, encoding="utf-8"))
    return d.get("result") or d.get("data") or []


as_of = date.fromisoformat(sys.argv[1])
raw = {}
for arg in sys.argv[2:]:
    name, path = arg.split("=", 1)
    raw[name] = load(path)
cur = {p: (accounts(p)[0] if accounts(p) else {"id": "", "currency": "ILS"}) for p in ("meta", "google", "linkedin", "tiktok")}
rows = []
for p in ("meta", "google", "linkedin", "tiktok"):
    for r in raw.pop(f"{p}_rows", []):
        if r.get("date") and r["date"] <= as_of.isoformat() and r.get("campaign"):
            r["_acc"] = r.get("account_id") or cur[p]["id"]
            rows.append({**make_row(r, p, 1.0), "a": r["_acc"]})
status = {"meta": {}, "google": {}}
latest = {}
for r in sorted(raw.get("meta_status", []), key=lambda r: r.get("date", "")):
    latest[clean(r["campaign"])] = r
for n, r in latest.items():
    status["meta"][n] = campaign_status(r.get("campaign_effective_status"), r.get("campaign_stop_time"), as_of)
raw["meta_camp"] = list(latest.values())
for r in raw.get("g_adgroup", []):
    status["google"][clean(r["campaign"])] = campaign_status(r.get("campaign_status"), None, as_of)
raw.setdefault("g_adgroup_info", raw.get("g_adgroup", []))

now = datetime.now(ZoneInfo("Asia/Jerusalem")).isoformat(timespec="minutes")
client = write_data(as_of, rows, {}, now, status)
if CONFIG.get("leads", {}).get("source"):
    import leads_sheet
    raw["leads"], src = leads_sheet.load()
pw = os.environ.get("TEAM_PASSWORD", "")
engine = __import__("group_course" if CONFIG.get("grouping", {}).get("mode") == "course" else "group_project")
payload = engine.build(client, raw, lambda d: 1.0, now, fetch_team.load_budgets(pw))
payload["meta"]["write"] = None
payload["meta"]["leadsSource"] = locals().get("src")
if payload["meta"].get("validLeads"):
    fetch_team.sheet_leads_to_client(client, payload)
BUILD_DIR.mkdir(parents=True, exist_ok=True)
(BUILD_DIR / "summary.json").write_text(json.dumps(fetch_team.summary(payload, client), ensure_ascii=False), encoding="utf-8")
if pw:
    (BUILD_DIR / "team.enc.json").write_text(json.dumps(crypto.encrypt_password(payload, pw)), encoding="utf-8")
master = os.environ.get("DASHBOARD_MASTER_KEY", "")
if master:
    key = crypto.client_key(master, SLUG, CONFIG.get("key_version", 1))
    (BUILD_DIR / "client.enc.json").write_text(json.dumps(crypto.encrypt_key(client, key)), encoding="utf-8")
(BUILD_DIR / "team.dev.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
(BUILD_DIR / "client.dev.json").write_text(json.dumps(client, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"[{SLUG}] ok: campaigns={len(payload['campaigns'])} groups={len(payload['courses'])} alerts={len(payload['alerts'])}")
