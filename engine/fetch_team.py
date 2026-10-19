"""Per-client build step 2: team payload (+ client payload) -> encrypted files in build/<slug>/.

Runs after fetch_client.py. Env: CLIENT, WINDSOR_API_KEY, TEAM_PASSWORD, DASHBOARD_MASTER_KEY, TEAM_WRITE_TOKEN,
GITHUB_REPOSITORY. Outputs (all safe to publish):
  build/<slug>/team.enc.json    team payload, TEAM_PASSWORD
  build/<slug>/client.enc.json  client page data, per-client link key
  build/<slug>/summary.json     small numbers for the team home page (bundled + encrypted by build_site.py)
Local preview: PLAIN=1 also writes build/<slug>/team.dev.json + client.dev.json (git-ignored).
"""
import json
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
from common import BUILD_DIR, CLIENT_DIR, CONFIG, DATA_FILE, HISTORY_START, SLUG, accounts, kpi_fields  # noqa: E402
import crypto  # noqa: E402

M = lambda: ["impressions", "link_clicks", "spend"] + kpi_fields("meta")
G = lambda: ["impressions", "clicks", "cost"] + kpi_fields("google")
L = lambda: ["impressions", "clicks", "spend"] + kpi_fields("linkedin")


def datasets() -> dict:
    """name -> (platform, fields, period); period "detail" = last detail_days, "history" = history_start..as_of."""
    return {
        "meta_ads":     ("meta", ["date", "campaign", "adset_name", "ad_id", "ad_name"] + M(), "detail"),
        "meta_ad_info": ("meta", ["ad_id", "ad_name", "adset_name", "adset_id", "campaign", "campaign_id",
                                  "thumbnail_url", "adset_effective_status", "ad_preview_shareable_link"], "detail"),
        "meta_place":   ("meta", ["date", "campaign", "publisher_platform", "platform_position"] + M(), "detail"),
        "meta_device":  ("meta", ["date", "campaign", "device_platform"] + M(), "detail"),
        "meta_demo":    ("meta", ["date", "campaign", "age", "gender"] + M(), "detail"),
        "meta_camp":    ("meta", ["campaign", "campaign_id", "campaign_lifetime_budget", "campaign_daily_budget",
                                  "campaign_start_time", "campaign_stop_time", "campaign_effective_status"], "history"),
        "g_adgroup":    ("google", ["date", "campaign", "ad_group_name", "ad_group_status"] + G(), "detail"),
        "g_adgroup_info": ("google", ["campaign", "campaign_id", "ad_group_name", "ad_group_id"], "detail"),
        "g_device":     ("google", ["date", "campaign", "device"] + G(), "detail"),
        "li_creative":  ("linkedin", ["date", "campaign", "creative_id", "sponsored_creative_content_title",
                                      "creative_content_data_share_ad_context_dsc_name"] + L(), "detail"),
        "li_creative_info": ("linkedin", ["creative_id", "campaign", "campaign_id", "ad_post_url"], "detail"),
        "li_camp":      ("linkedin", ["campaign", "campaign_id", "campaign_group_name", "campaign_start_date",
                                      "campaign_end_date", "campaign_total_budget_amount", "campaign_daily_budget_amount",
                                      "campaign_status"], "history"),
    }


# Windsor rejects some action fields together with breakdowns (e.g. omni purchases x placement/age/device).
SWAP = {"actions_omni_purchase": "actions_offsite_conversion_fb_pixel_purchase",
        "action_values_omni_purchase": "action_values_offsite_conversion_fb_pixel_purchase"}
BASE = {"date", "campaign", "impressions", "link_clicks", "clicks", "spend", "cost"}


def fetch_with_fallback(get, name, p, fields, d0, d1):
    """Full fields -> website-purchase fields (renamed back) -> no KPI fields. A failing breakdown never fails the client."""
    swapped = [SWAP.get(f, f) for f in fields]
    attempts = [(fields, {}), (swapped, {v: k for k, v in SWAP.items()}),
                ([f for f in fields if f in BASE or not (f.startswith("actions_") or f.startswith("action_values")
                                                        or f in ("conversions", "conversion_value", "oneclickleads",
                                                                 "externalwebsiteconversions", "complete_payment",
                                                                 "total_complete_payment_rate"))], {})]
    last = None
    for i, (flds, back) in enumerate(attempts):
        if i and flds == attempts[i - 1][0]:
            continue
        try:
            rows = get(p, flds, d0, d1)
            for r in rows:
                for new, old in back.items():
                    if new in r:
                        r[old] = r.pop(new)
            if i:
                print(f"[{SLUG}] {name}: fallback #{i} used ({last})")
            return rows
        except Exception as e:
            last = e
    print(f"[{SLUG}] WARNING {name} skipped: {last}")
    return []


def load_budgets(password: str) -> dict:
    path = CLIENT_DIR / "budgets.enc.json"
    if not path.exists() or not password:
        return {}
    try:
        return crypto.decrypt_password(json.loads(path.read_text(encoding="utf-8")), password).get("courses", {})
    except Exception as e:  # never break the refresh because of the budgets file
        print(f"[{SLUG}] could not read budgets: {e}")
        return {}


def write_config():
    tok, repo = os.environ.get("TEAM_WRITE_TOKEN", "").strip(), os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not tok or not repo:
        return None
    return {"repo": repo, "path": f"clients/{SLUG}/budgets.enc.json", "branch": "main", "token": tok}


def summary(payload: dict, client: dict) -> dict:
    """Numbers for the agency home page: last 7/30 days, alerts, update time."""
    as_of = client["asOf"]
    d7, d30 = [(date.fromisoformat(as_of) - timedelta(days=n)).isoformat() for n in (6, 29)]
    out = {}
    for name, d0 in (("d7", d7), ("d30", d30)):
        s = k = l = v = 0.0
        for r in client["rows"]:
            if r["d"] >= d0:
                s += r["s"]; k += r["k"]; l += r["l"]; v += r.get("v", 0)
        if payload["meta"].get("validLeads"):
            days = payload["days"]
            l = sum(n for d, ci, n in payload.get("leadRows", []) if days[d] >= d0)
        out[name] = {"s": round(s), "k": int(k), "l": round(l, 1), "v": round(v)}
    al = payload.get("alerts", [])
    return {"slug": SLUG, "name": CONFIG.get("name"), "asOf": as_of, "generated": payload["meta"]["generated"],
            "platforms": client["client"]["platforms"], "kpi": payload["meta"]["kpi"], **out,
            "alerts": {"high": sum(a["sev"] == "high" for a in al), "med": sum(a["sev"] == "med" for a in al)},
            "top": [a["msg"] for a in al if a["sev"] == "high"][:3], "status": CONFIG.get("status", "active"),
            "owner": CONFIG.get("owner")}


def main():
    from windsor import KEY, get, usd_ils
    plain = os.environ.get("PLAIN") == "1"
    pw = os.environ.get("TEAM_PASSWORD", "").strip()
    master = os.environ.get("DASHBOARD_MASTER_KEY", "").strip()
    if not KEY:
        sys.exit("WINDSOR_API_KEY is not set")
    client = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    as_of = date.fromisoformat(client["asOf"])
    detail_from = as_of - timedelta(days=CONFIG.get("detail_days", 120) - 1)
    raw = {}
    for name, (p, fields, period) in datasets().items():
        if not accounts(p):
            continue
        d0 = detail_from if period == "detail" else HISTORY_START
        raw[name] = fetch_with_fallback(get, name, p, fields, d0, as_of)
    if accounts("linkedin"):
        raw["li_demo"] = {}
        for key, (a, b) in client["presets"].items():
            a, b = date.fromisoformat(a), date.fromisoformat(b)
            raw["li_demo"][key] = {"func": get("linkedin", ["member_job_function"] + L(), a, b),
                                   "senior": get("linkedin", ["member_seniority"] + L(), a, b)}
    leads_source = None
    if CONFIG.get("leads", {}).get("source"):
        import leads_sheet
        raw["leads"], leads_source = leads_sheet.load(get)
        if leads_source == "none":          # CRM not connected yet -> platform results, with a warning in the page
            raw.pop("leads")
    fx = usd_ils(HISTORY_START, as_of)
    now = datetime.now(ZoneInfo("Asia/Jerusalem")).isoformat(timespec="minutes")
    budgets = load_budgets(pw)
    engine = __import__("group_course" if CONFIG.get("grouping", {}).get("mode") == "course" else "group_project")
    payload = engine.build(client, raw, fx, now, budgets)
    payload["meta"]["write"] = write_config()
    payload["meta"]["leadsSource"] = leads_source
    payload["meta"]["leadsMissing"] = leads_source == "none"
    print(f"[{SLUG}] team payload: campaigns={len(payload['campaigns'])} units={len(payload['units'])} "
          f"alerts={len(payload['alerts'])} budgets={len(budgets)} leads={leads_source}")

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    (BUILD_DIR / "summary.json").write_text(json.dumps(summary(payload, client), ensure_ascii=False), encoding="utf-8")
    if pw:
        (BUILD_DIR / "team.enc.json").write_text(json.dumps(crypto.encrypt_password(payload, pw)), encoding="utf-8")
    if master:
        key = crypto.client_key(master, SLUG, CONFIG.get("key_version", 1))
        (BUILD_DIR / "client.enc.json").write_text(json.dumps(crypto.encrypt_key(client, key)), encoding="utf-8")
    if plain:
        (BUILD_DIR / "team.dev.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        (BUILD_DIR / "client.dev.json").write_text(json.dumps(client, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
