"""Team payload for clients managed by PROJECT (or as one whole account when no projects are configured):
project grouping by campaign name, audience/type tags, optional valid leads from a CRM sheet, monthly budget
pacing, alerts, drill-down rows and breakdowns.

Input:
  client  - parsed build/<slug>/data.json
  raw     - Windsor datasets (see fetch_team.DATASETS); raw["leads"] = CRM valid-lead counts when the client has them
  fx      - f(date)->USD/ILS
  budgets - {"<group_key>|YYYY-MM": {budget, by, at}}
Groups are under the payload key "courses" (shared page code with the course engine).
"""
import calendar
from collections import defaultdict
from datetime import date, timedelta

from common import CONFIG, clean, kpi_count, kpi_value, num, public_kpi

BRAND_KEY = "__brand"
ALL_KEY = "__all"
LEAD_TYPES = {"Push", "Remarketing", "PMAX", "Search", "Search Brand", "LeadGen", "Sales", "Conversions"}
UNASSIGNED = "לא משויך לקמפיין"
G = CONFIG.get("grouping", {})
PROJECTS = G.get("projects", [])


def group_of(name: str):
    s = clean(name)
    for p in PROJECTS:
        if any(m in s for m in p["match"]):
            return p["key"]
    return BRAND_KEY if PROJECTS else ALL_KEY


def audience_of(name: str) -> str:
    low = clean(name).lower()
    for a in G.get("audiences", []):
        if a.lower() in low:
            return a
    return "כללי"


def type_of(name: str) -> str:
    low = clean(name).lower()
    if "pmax" in low or "performance max" in low:
        return "PMAX"
    if "search" in low or "חיפוש" in low:
        return "Search Brand" if ("brand" in low or "מותג" in low) else "Search"
    if "remarketing" in low or "רימרקטינג" in low or "retargeting" in low:
        return "Remarketing"
    if any(k in low for k in ("engagement", "מעורבות", "page likes", "awarness", "awareness", "reach", "video views", "brand")):
        return "Brand"
    if any(k in low for k in ("sales", "purchase", "מכירות", "catalog", "dpa", "advantage+ shopping")):
        return "Sales"
    if any(k in low for k in ("leadgen", "lead", "לידים", "push", "conversions", "המרות")):
        return "Push"
    return ""


class Index:
    def __init__(self):
        self.items, self.pos = [], {}

    def __call__(self, key, make=None):
        if key not in self.pos:
            self.pos[key] = len(self.items)
            self.items.append(make() if make else key)
        return self.pos[key]


def build(client: dict, raw: dict, fx, generated: str, budgets: dict = None) -> dict:
    budgets = budgets or {}
    cfg = CONFIG.get("alerts", {})
    as_of = date.fromisoformat(client["asOf"])
    iso = as_of.isoformat()
    detail_from = (as_of - timedelta(days=CONFIG.get("detail_days", 120) - 1)).isoformat()
    status = client.get("status", {})
    acc_cur = {}
    for p, lst in CONFIG.get("accounts", {}).items():
        for a in lst or []:
            a = a if isinstance(a, dict) else {"id": a}
            acc_cur[str(a["id"])] = a.get("currency", "ILS")
    rate = lambda r, d=None: fx(d or r.get("date") or iso) if acc_cur.get(str(r.get("_acc")), r.get("_cur", "ILS")) == "USD" else 1.0
    gnames = {p["key"]: p["name"] for p in PROJECTS}
    gnames[ALL_KEY] = CONFIG.get("name", "כל הקמפיינים")
    gnames[BRAND_KEY] = "מותג וכללי"
    valid_mode = bool(raw.get("leads") is not None and CONFIG.get("leads", {}).get("source"))

    # ---------- platform settings (daily budgets, ids, accounts)
    info, cid, cacc = {}, {}, {}
    for r in raw.get("meta_camp", []):
        n = clean(r.get("campaign"))
        info[("meta", n)] = {"daily": num(r.get("campaign_daily_budget")) / 100 * rate(r, iso) or None,
                             "start": (r.get("campaign_start_time") or "")[:10] or None,
                             "end": (r.get("campaign_stop_time") or "")[:10] or None}
    for r in raw.get("li_camp", []):
        n = clean(r.get("campaign"))
        info[("linkedin", n)] = {"daily": round(num(r.get("campaign_daily_budget_amount")) * rate(r, iso)) or None,
                                 "start": r.get("campaign_start_date"), "end": r.get("campaign_end_date")}
    for ds, p in (("meta_camp", "meta"), ("meta_ad_info", "meta"), ("g_adgroup_info", "google"),
                  ("li_camp", "linkedin"), ("li_creative_info", "linkedin")):
        for r in raw.get(ds, []):
            if r.get("campaign") and r.get("campaign_id"):
                cid[(p, clean(r["campaign"]))] = str(r["campaign_id"])
            if r.get("campaign") and r.get("_acc"):
                cacc[(p, clean(r["campaign"]))] = str(r["_acc"])

    def camp_url(p, n, i):
        a = cacc.get((p, n)) or ""
        if not i:
            return None
        if p == "meta":
            return f"https://adsmanager.facebook.com/adsmanager/manage/adsets?act={a}&selected_campaign_ids={i}"
        if p == "google":
            return f"https://ads.google.com/aw/adgroups?campaignId={i}&__e={a.replace('-', '')}"
        if p == "linkedin":
            return f"https://www.linkedin.com/campaignmanager/accounts/{a}/campaigns/{i}/creatives"
        return None

    # ---------- campaigns + compact daily rows
    days, camps = Index(), Index()
    campaigns, rows, val_rows = [], [], []

    def new_campaign(p, n, gk, typ=None, st=None, pseudo=False):
        i = cid.get((p, n))
        inf = info.get((p, n), {})
        campaigns.append({"p": p, "name": n, "course": gk, "type": typ if typ is not None else type_of(n),
                          "aud": "כללי" if pseudo else audience_of(n), "status": st, "budget": None,
                          "daily": inf.get("daily"), "start": inf.get("start"), "end": inf.get("end"),
                          "launch": None, "id": i, "url": None if pseudo else camp_url(p, n, i), "pseudo": pseudo})

    for r in client["rows"]:
        if r.get("a"):
            cacc.setdefault((r["p"], r["c"]), str(r["a"]))
    for r in client["rows"]:
        ci = camps((r["p"], r["c"]))
        if ci == len(campaigns):
            new_campaign(r["p"], r["c"], group_of(r["c"]), st=status.get(r["p"], {}).get(r["c"]))
        di = days(r["d"])
        rows.append([di, ci, r["i"], r["k"], r["l"], round(r["s"], 2)])
        if r.get("v"):
            val_rows.append([di, ci, r["v"]])

    # ---------- valid leads: CRM sheet when configured, else the platform KPI itself
    by_name = {(c["p"], c["name"]): ci for ci, c in enumerate(campaigns)}
    lead_rows, unmatched = [], 0
    if valid_mode:
        spend_day = defaultdict(float)
        for d, ci, i, k, l, s in rows:
            spend_day[(ci, d)] += s
        pmax_by = defaultdict(list)
        for ci, c in enumerate(campaigns):
            if c["p"] == "google" and c["type"] == "PMAX" and c["course"] not in (BRAND_KEY, ALL_KEY):
                pmax_by[c["course"]].append(ci)

        def pseudo(p, gk):
            key = (p, f"{UNASSIGNED}·{gk}")
            if key not in by_name:
                camps(key)
                new_campaign(p, UNASSIGNED, gk, typ="", pseudo=True)
                by_name[key] = len(campaigns) - 1
            return by_name[key]

        for d, gk, platform, camp, n in raw.get("leads", []):
            if d < CONFIG.get("history_start", "2000") or d > iso:
                continue
            p = "google" if platform == "google" else "meta"
            ci = by_name.get((p, clean(camp)))
            if ci is None and p == "google" and pmax_by.get(gk):
                di = days.pos.get(d)
                ci = max(pmax_by[gk], key=lambda x: spend_day.get((x, di), 0))
            if ci is None:
                ci = pseudo(p, gk)
                unmatched += n
            lead_rows.append([days(d), ci, n])
    else:
        lead_rows = [[d, ci, l] for d, ci, i, k, l, s in rows if l]

    day_list = days.items

    def window(ci_set, d_from, d_to):
        t = {"i": 0, "k": 0, "l": 0.0, "s": 0.0, "v": 0.0, "r": 0.0}
        for d, ci, i, k, l, s in rows:
            if ci in ci_set and d_from <= day_list[d] <= d_to:
                t["i"] += i; t["k"] += k; t["l"] += l; t["s"] += s
        for d, ci, v in lead_rows:
            if ci in ci_set and d_from <= day_list[d] <= d_to:
                t["v"] += v
        for d, ci, v in val_rows:
            if ci in ci_set and d_from <= day_list[d] <= d_to:
                t["r"] += v
        return t

    # ---------- groups
    by_group = defaultdict(set)
    for ci, c in enumerate(campaigns):
        by_group[c["course"]].add(ci)
    month = as_of.strftime("%Y-%m")
    dim = calendar.monthrange(as_of.year, as_of.month)[1]
    m_start = as_of.replace(day=1).isoformat()
    d7 = (as_of - timedelta(days=6)).isoformat()
    groups = []
    for key in [p["key"] for p in PROJECTS] + [ALL_KEY, BRAND_KEY]:
        cis = by_group.get(key, set())
        if not cis:
            continue
        life, last7, mtd = window(cis, "0000", iso), window(cis, d7, iso), window(cis, m_start, iso)
        live = [ci for ci in cis if campaigns[ci]["status"] == "live"]
        o = {"key": key, "name": gnames.get(key, key), "brand": key == BRAND_KEY,
             "phase": "live" if live else "paused", "live": len(live), "campaigns": sorted(cis),
             "dailySet": round(sum(campaigns[ci]["daily"] or 0 for ci in live), 0) or None,
             "spent": round(life["s"], 2), "leads": life["l"], "valid": life["v"], "revenue": round(life["r"], 2),
             "cpl": round(life["s"] / life["v"], 2) if life["v"] else None,
             "avg7": round(last7["s"] / 7, 2), "month": month, "monthSpent": round(mtd["s"], 2), "monthValid": mtd["v"]}
        b = (budgets.get(f"{key}|{month}") or {}).get("budget")
        if b and not o["brand"]:
            elapsed, left = as_of.day, dim - as_of.day
            expected = b * elapsed / dim
            o.update({"budget": b, "expected": round(expected), "pace": round(mtd["s"] / expected, 3) if expected else None,
                      "elapsedPct": round(elapsed / dim, 3), "remaining": round(b - mtd["s"]),
                      "requiredDaily": round(max(0, b - mtd["s"]) / left) if left else None,
                      "projected": round(mtd["s"] + last7["s"] / 7 * left)})
        groups.append(o)
    gidx = {o["key"]: i for i, o in enumerate(groups)}
    for c in campaigns:
        c["course"] = gidx[c["course"]]

    # ---------- drill-down units
    camp_lookup = {(c["p"], c["name"]): ci for ci, c in enumerate(campaigns) if not c["pseudo"]}
    thumbs = {str(r.get("ad_id")): r.get("thumbnail_url") for r in raw.get("meta_ad_info", []) if r.get("ad_id")}
    previews = {str(r.get("ad_id")): r.get("ad_preview_shareable_link") for r in raw.get("meta_ad_info", []) if r.get("ad_id")}
    ad_acc = {str(r.get("ad_id")): str(r.get("_acc") or "") for r in raw.get("meta_ad_info", []) if r.get("ad_id")}
    set_status = {(clean(r.get("campaign")), clean(r.get("adset_name"))): r.get("adset_effective_status")
                  for r in raw.get("meta_ad_info", [])}
    g_groups = {(clean(r.get("campaign")), clean(r.get("ad_group_name"))): str(r.get("ad_group_id"))
                for r in raw.get("g_adgroup_info", []) if r.get("ad_group_id")}
    li_posts = {str(r.get("creative_id")): r.get("ad_post_url") for r in raw.get("li_creative_info", []) if r.get("creative_id")}
    units, unit_rows = Index(), []

    def unit_links(p, ci, set_name, ad_id):
        c = campaigns[ci]
        if p == "meta":
            return {"preview": previews.get(ad_id),
                    "manage": f"https://adsmanager.facebook.com/adsmanager/manage/ads?act={ad_acc.get(ad_id, '')}&selected_ad_ids={ad_id}"}
        if p == "linkedin":
            return {"post": li_posts.get(ad_id), "manage": c.get("url")}
        g = g_groups.get((c["name"], set_name))
        a = (cacc.get(("google", c["name"])) or "").replace("-", "")
        return {"manage": f"https://ads.google.com/aw/ads?campaignId={c['id']}&adGroupId={g}&__e={a}"
                if g and c.get("id") else c.get("url")}

    def add_unit(p, ci, set_name, ad_name, ad_id, thumb, st, d, i, k, l, s):
        ui = units((p, ci, set_name, ad_name, ad_id),
                   lambda: {"p": p, "c": ci, "set": set_name, "ad": ad_name, "id": ad_id, "thumb": thumb, "setStatus": st,
                            "links": {k_: v for k_, v in unit_links(p, ci, set_name, ad_id).items() if v}})
        unit_rows.append([days(d), ui, int(i), int(k), round(l, 2), round(s, 2)])

    for r in raw.get("meta_ads", []):
        ci = camp_lookup.get(("meta", clean(r.get("campaign"))))
        if ci is None or r["date"] < detail_from:
            continue
        add_unit("meta", ci, clean(r.get("adset_name")), clean(r.get("ad_name")), str(r.get("ad_id")),
                 thumbs.get(str(r.get("ad_id"))), set_status.get((clean(r.get("campaign")), clean(r.get("adset_name")))),
                 r["date"], num(r.get("impressions")), num(r.get("link_clicks")), kpi_count(r, "meta"),
                 num(r.get("spend")) * rate(r))
    for r in raw.get("g_adgroup", []):
        ci = camp_lookup.get(("google", clean(r.get("campaign"))))
        if ci is None or r["date"] < detail_from:
            continue
        add_unit("google", ci, clean(r.get("ad_group_name")) or "Performance Max", None, None, None,
                 r.get("ad_group_status"), r["date"], num(r.get("impressions")), num(r.get("clicks")),
                 kpi_count(r, "google"), num(r.get("cost")) * rate(r))
    for r in raw.get("li_creative", []):
        ci = camp_lookup.get(("linkedin", clean(r.get("campaign"))))
        if ci is None or r["date"] < detail_from:
            continue
        title = clean(r.get("sponsored_creative_content_title") or r.get("creative_content_data_share_ad_context_dsc_name")
                      or f"Creative {r.get('creative_id')}")
        add_unit("linkedin", ci, campaigns[ci]["name"], title, str(r.get("creative_id")), None, None,
                 r["date"], num(r.get("impressions")), num(r.get("clicks")), kpi_count(r, "linkedin"),
                 num(r.get("spend")) * rate(r))

    # ---------- breakdowns
    dims, bd = {}, {}

    def breakdown(name, dataset, p, dim_fn):
        idx, out = Index(), []
        i_f, k_f, s_f = ("impressions", "link_clicks", "spend") if p == "meta" else ("impressions", "clicks", "cost")
        for r in raw.get(dataset, []):
            if r["date"] < detail_from:
                continue
            ci = camp_lookup.get((p, clean(r.get("campaign"))))
            if ci is None:
                continue
            out.append([days(r["date"]), ci, idx(dim_fn(r)), int(num(r.get(i_f))), int(num(r.get(k_f))),
                        round(kpi_count(r, p), 2), round(num(r.get(s_f)) * rate(r), 2)])
        dims[name], bd[name] = idx.items, out

    breakdown("meta_place", "meta_place", "meta", lambda r: f"{r.get('publisher_platform')}·{r.get('platform_position')}")
    breakdown("meta_device", "meta_device", "meta", lambda r: r.get("device_platform") or "unknown")
    breakdown("meta_age", "meta_demo", "meta", lambda r: r.get("age") or "unknown")
    breakdown("meta_gender", "meta_demo", "meta", lambda r: r.get("gender") or "unknown")
    breakdown("google_device", "g_device", "google", lambda r: r.get("device") or "unknown")

    li_demo = {}
    for key, payload in (raw.get("li_demo") or {}).items():
        rt = fx(client["presets"][key][1]) if any(v == "USD" for v in acc_cur.values()) else 1.0
        li_demo[key] = {dim_: [[r.get(field), int(num(r.get("impressions"))), int(num(r.get("clicks"))),
                                int(kpi_count(r, "linkedin")), round(num(r.get("spend")) * rt, 2)] for r in payload.get(dim_, [])]
                        for dim_, field in (("func", "member_job_function"), ("senior", "member_seniority"))}

    # ---------- alerts (valid/main results drive the result alerts)
    kp = public_kpi()
    alerts = []
    d3, d28, d8 = [(as_of - timedelta(days=n)).isoformat() for n in (2, 27, 7)]
    reach7 = (client.get("reach", {}).get("last7", {}) or {}).get("meta", {}).get("by", {})
    unit = "תקינים" if valid_mode else kp["label"]
    for ci, c in enumerate(campaigns):
        if c["status"] != "live" or c["pseudo"]:
            continue
        base = {"p": c["p"], "campaign": ci, "course": c["course"]}
        if window({ci}, iso, iso)["s"] == 0:
            alerts.append({**base, "sev": "med", "type": "no_spend", "msg": "באוויר אבל לא הוציא כסף אתמול"})
        if kp["type"] != "traffic" and c["type"] in LEAD_TYPES:
            w3, m28 = window({ci}, d3, iso), window({ci}, d28, iso)
            cpl28 = m28["s"] / m28["v"] if m28["v"] else None
            thr = max(cfg.get("no_leads_min_spend", 300), 2 * cpl28 if cpl28 else 0)
            if w3["v"] == 0 and w3["s"] >= thr:
                alerts.append({**base, "sev": "high", "type": "no_leads", "value": round(w3["s"]),
                               "msg": f"אפס {unit} ב-3 הימים האחרונים למרות הוצאה של ₪{w3['s']:,.0f}"})
            w7, prev = window({ci}, d7, iso), window({ci}, d28, d8)
            if w7["v"] >= 3 and prev["v"] >= 5:
                c7, cp = w7["s"] / w7["v"], prev["s"] / prev["v"]
                if c7 > cfg.get("cpl_rise_ratio", 1.3) * cp:
                    alerts.append({**base, "sev": "med", "type": "cpl_up", "value": round(c7 / cp - 1, 3),
                                   "msg": f"{kp['cpa']} ב-7 ימים ₪{c7:,.0f} לעומת ₪{cp:,.0f} ב-3 השבועות שלפני (+{(c7 / cp - 1) * 100:.0f}%)"})
        if c["p"] == "meta" and reach7.get(c["name"]):
            freq = window({ci}, d7, iso)["i"] / reach7[c["name"]]
            if freq > cfg.get("meta_frequency_max", 3.5):
                alerts.append({**base, "sev": "med", "type": "frequency", "value": round(freq, 2),
                               "msg": f"תדירות גבוהה ב-7 ימים: {freq:.1f} (קהל נשחק)"})
    for i, o in enumerate(groups):
        pace = o.get("pace")
        if pace is None or o["elapsedPct"] < 0.2:
            continue
        if pace < cfg.get("pace_low", 0.8):
            alerts.append({"sev": "med", "type": "under_pace", "course": i, "value": pace,
                           "msg": f"מתחת לקצב התקציב החודשי: הוצא ₪{o['monthSpent']:,.0f} מתוך ₪{o['expected']:,.0f} צפוי ({pace * 100:.0f}%)"})
        elif pace > cfg.get("pace_high", 1.15):
            alerts.append({"sev": "high" if o["projected"] > o["budget"] * 1.1 else "med", "type": "over_pace", "course": i,
                           "value": pace,
                           "msg": f"מעל קצב התקציב החודשי: הוצא ₪{o['monthSpent']:,.0f} מתוך ₪{o['expected']:,.0f} צפוי ({pace * 100:.0f}%)"})
    alerts.sort(key=lambda a: {"high": 0, "med": 1, "info": 2}[a["sev"]])

    return {
        "meta": {"client": CONFIG.get("name"), "slug": CONFIG.get("slug"), "generated": generated, "asOf": client["asOf"],
                 "currency": "ILS", "detailFrom": detail_from, "presets": client["presets"], "schema": 2, "mode": "project",
                 "single": not PROJECTS, "validLeads": valid_mode, "kpi": kp, "month": month, "leadsUnassigned": unmatched,
                 "platforms": client["client"]["platforms"]},
        "days": day_list, "courses": groups, "campaigns": campaigns, "rows": rows, "leadRows": lead_rows,
        "valRows": val_rows, "units": units.items, "unitRows": unit_rows, "dims": dims, "breakdowns": bd,
        "liDemo": li_demo, "reach": client.get("reach", {}), "alerts": alerts,
    }
