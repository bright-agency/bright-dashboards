"""One-off generator: clients/<slug>/client.json from bright-office/clients/registry.yaml + the KPI decisions
below (based on a 90-day Windsor survey, 2026-10-19). Re-running never overwrites an existing client.json
unless --force. Usage: python tools/make_clients.py <registry.yaml> [--force]"""
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FORM = "actions_onsite_conversion_lead_grouped"
WEB = "actions_offsite_conversion_fb_pixel_lead"
PUR, PURV = "actions_omni_purchase", "action_values_omni_purchase"
REG = "actions_complete_registration"
MSG = "actions_onsite_conversion_messaging_conversation_started_7d"
G_CONV, G_VAL = "conversions", "conversion_value"
LI_FORM, LI_WEB = "oneclickleads", "externalwebsiteconversions"
TT_CONV, TT_PAY, TT_PAYV = "conversions", "complete_payment", "total_complete_payment_rate"
USD_ACCOUNTS = {"333744113679519", "353849333", "28945428411721154", "1287304675315270"}

LEADS = lambda meta, label=None: {"type": "leads", "meta": meta, "google": G_CONV, "linkedin": LI_WEB, "tiktok": TT_CONV,
                                  **({"label": label} if label else {})}
PURCHASE = {"type": "purchase", "meta": PUR, "meta_value": PURV, "google": G_CONV, "google_value": G_VAL,
            "linkedin": LI_WEB, "tiktok": TT_PAY, "tiktok_value": TT_PAYV}
TRAFFIC = {"type": "traffic"}

KPI = {
    "shomrat-hazorea": PURCHASE, "milega": PURCHASE, "milkandbutterflies": PURCHASE, "eset": PURCHASE, "tactit": PURCHASE,
    "rotem-shani": LEADS(FORM), "reichman-exec-ed": {**LEADS(FORM), "linkedin": LI_FORM},
    "shenkar-handesaim": LEADS([FORM, WEB]), "elevation": LEADS([WEB, FORM]), "iac-animation": LEADS([WEB, FORM]),
    "hagag": LEADS([FORM, WEB]), "federal-assets": LEADS(WEB), "phototeva": LEADS([WEB, FORM]), "spot": LEADS(FORM),
    "weisman": LEADS(MSG, "פניות"), "hadas-wilf": LEADS([REG, FORM], "הרשמות ולידים"), "meala": LEADS([], "המרות"),
    "inspire-pt": LEADS(FORM), "kidma-gaz": LEADS([MSG, WEB], "פניות"), "the-service": LEADS([]),
    "profit": TRAFFIC, "oded-karbachik": LEADS(FORM), "hila-blan": TRAFFIC, "readytax": LEADS(REG, "הרשמות"),
    "cyberlion": LEADS([], "המרות"), "rideeazy": LEADS(WEB),
}
SPECIAL = {
    "reichman-exec-ed": {"logo": "fore-logo-heb-blue.jpg", "grouping": {"mode": "course"},
                         "alerts": {"course_soon_days": 7}},
    "rotem-shani": {
        "grouping": {"mode": "project", "projects": [
            {"key": "bs", "name": "בית שמש · עדן", "match": ["בית שמש", "פרויקט עדן"], "sheet": ["בית שמש"], "tab": "לידים - בית שמש"},
            {"key": "pt", "name": "פתח תקווה · 2MORROW", "match": ["פתח תקווה"], "sheet": ["פ\"ת", "פתח תקווה"], "tab": "לידים - פתח תקווה"},
            {"key": "gv", "name": "גבעתיים · We G שדה בוקר", "match": ["גבעתיים"], "sheet": ["גבעתיים We G"], "tab": "לידים - גבעתיים - שדה בוקר"},
            {"key": "ra", "name": "רעננה · We R", "match": ["רעננה"], "sheet": ["רעננה"], "tab": "לידים - רעננה"}],
            "audiences": ["קרנות השוטרים", "משקיעים", "משפחות", "דירות גן", "פנטהאוזים", "מתעניינים בנדל\"ן",
                          "3 חדרים", "6 חדרים", "English", "French"]},
        "leads": {"source": "leadim_sheet", "spreadsheet_id": "1UA8cBmP8SUEd0sqTvm9WCVAhLQLUH4xP1FJzYJVv1wk",
                  "history_tab": "דאטה היסטורי - כל הפרוייקטים", "history_until": "2026-08-25",
                  "windsor_connector": "googlesheets"}},
}
PLAT_KEYS = {"meta": "meta", "google_ads": "google", "linkedin": "linkedin", "tiktok": "tiktok"}
ALERTS = {"no_leads_min_spend": 300, "cpl_rise_ratio": 1.3, "meta_frequency_max": 3.5, "pace_low": 0.8, "pace_high": 1.15}


def main():
    reg = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
    force = "--force" in sys.argv
    made = []
    for c in reg["clients"]:
        if "paid" not in c.get("services", []) or c.get("status") == "inactive" or c["id"] not in KPI:
            continue
        accs = {}
        for k, plat in PLAT_KEYS.items():
            ids = (c.get("accounts") or {}).get(k) or []
            if ids:
                cur = "USD" if plat == "linkedin" else None
                accs[plat] = [{"id": str(i), "currency": cur or ("USD" if str(i) in USD_ACCOUNTS else "ILS")} for i in ids]
        sp = SPECIAL.get(c["id"], {})
        cfg = {"slug": c["id"], "name": c["name"], "status": c.get("status", "active"), "owner": c.get("owner"),
               "accounts": accs, "kpi": KPI[c["id"]], "grouping": sp.get("grouping", {"mode": "project"}),
               "history_start": "2026-01-01", "detail_days": 120, "key_version": 1,
               "alerts": {**ALERTS, **sp.get("alerts", {})}}
        for k in ("logo", "leads"):
            if k in sp:
                cfg[k] = sp[k]
        path = ROOT / "clients" / c["id"] / "client.json"
        if path.exists() and not force:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        made.append(c["id"])
    print(len(made), "clients:", ", ".join(made))


if __name__ == "__main__":
    main()
