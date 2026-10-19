"""Builds the internal (campaigners / agent) payload: course grouping, budget pacing, alerts, drill-down rows.

Input:
  client  - the parsed data.json (rows, status, reach, presets, asOf)
  raw     - dict of raw Windsor datasets (see DATASETS in fetch_internal.py)
  fx      - f(date)->USD/ILS rate
Output: a JSON-serialisable dict. Schema is documented in team/AGENT.md.
"""
import re
from collections import defaultdict
from datetime import date, timedelta

from common import CONFIG, clean, kpi_count, num, public_kpi

TYPES = {"leadgen": "LeadGen", "leadgen - remarketing": "Remarketing", "website leads": "Website Leads",
         "engagement": "Engagement", "engagment": "Engagement", "awarness": "Awareness", "awareness": "Awareness",
         "brand awareness": "Brand Awareness", "search": "Search", "pmax": "PMax", "gdn": "GDN", "youtube": "YouTube"}
LEAD_TYPES = {"LeadGen", "Remarketing", "Website Leads", "Search", "PMax"}
# lead alerts only where our lead metric is the campaign goal (Website Leads = pixel leads, not counted here)
ALERT_LEAD_TYPES = {"LeadGen", "Remarketing", "Search", "PMax"}
BRAND_KEY = "__brand"
_DATE = re.compile(r"\(?\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b\)?")
_HEB = re.compile(r"[֐-׿]")


def _d(day, month, year) -> date:
    y = int(year)
    return date(y + 2000 if y < 100 else y, int(month), int(day))


def parse_name(raw: str) -> dict:
    """'Bright | LeadGen | קורס X 13.10.26 | 22,000 | 16.7.26' -> type, course, course_date, budget, launch."""
    s = clean(raw)
    parts = [p.strip() for p in (s.split("|") if "|" in s else s.split(" - ")) if p.strip()]
    parts = [p for p in parts if p.lower() != "bright"]
    out = {"type": "", "course": None, "course_date": None, "budget": None, "launch": None, "brand": False}
    if parts and parts[0].lower() in TYPES:
        out["type"] = TYPES[parts[0].lower()]
        parts = parts[1:]
    for p in parts:
        if re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{2,4}", p):
            try:
                out["launch"] = _d(*p.split("."))
            except ValueError:
                pass
        elif re.fullmatch(r"\d{1,3}(,\d{3})+|\d{3,6}", p):
            out["budget"] = float(p.replace(",", ""))
        elif _HEB.search(p) and out["course"] is None:
            m = _DATE.search(p)
            if m:
                try:
                    out["course_date"] = _d(*m.groups())
                except ValueError:
                    pass
                p = _DATE.sub("", p)
            out["course"] = re.sub(r"\s{2,}", " ", p.replace("()", "")).strip()
        elif p.lower() in TYPES and not out["type"]:
            out["type"] = TYPES[p.lower()]
    low = s.lower()
    if "brand" in low or out["course"] is None or (out["course"] or "").startswith("קורסים עתידיים"):
        out["brand"] = True
    return out


def norm_course(name: str) -> str:
    n = re.sub(r"^קורס\s+", "", name or "")
    n = re.sub(r"[\"'׳״:()\-–,.]", " ", n)
    return re.sub(r"\s+", " ", n).strip().lower()


class Index:
    """Value -> small int, keeps payload compact."""
    def __init__(self):
        self.items, self.pos = [], {}

    def __call__(self, key, make=None):
        if key not in self.pos:
            self.pos[key] = len(self.items)
            self.items.append(make() if make else key)
        return self.pos[key]


def build(client: dict, raw: dict, fx, generated: str, budgets: dict = None) -> dict:
    """budgets: manual course budgets edited in the team page {course_key: {budget, by, at}}."""
    budgets = budgets or {}
    cfg = CONFIG.get("alerts", {})
    as_of = date.fromisoformat(client["asOf"])
    detail_from = (as_of - timedelta(days=CONFIG.get("detail_days", 120) - 1)).isoformat()
    status = client.get("status", {})

    # ---------- campaign registry from the daily client rows
    camp_rows = defaultdict(list)                       # (p, name) -> rows
    for r in client["rows"]:
        camp_rows[(r["p"], r["c"])].append(r)

    info = {}                                           # (p, name) -> platform settings
    for r in raw.get("meta_camp", []):
        n = clean(r.get("campaign"))
        info[("meta", n)] = {
            "daily": num(r.get("campaign_daily_budget")) / 100 or None,     # minor units -> ILS
            "lifetime": num(r.get("campaign_lifetime_budget")) or None,
            "start": (r.get("campaign_start_time") or "")[:10] or None,
            "end": (r.get("campaign_stop_time") or "")[:10] or None}
    rate_now = fx(as_of.isoformat())
    for r in raw.get("li_camp", []):
        n = clean(r.get("campaign"))
        info[("linkedin", n)] = {
            "daily": round(num(r.get("campaign_daily_budget_amount")) * rate_now, 0) or None,
            "lifetime": round(num(r.get("campaign_total_budget_amount")) * rate_now, 0) or None,
            "start": r.get("campaign_start_date"), "end": r.get("campaign_end_date"),
            "group": clean(r.get("campaign_group_name"))}

    parsed = {k: parse_name(k[1]) for k in camp_rows}

    # ---------- courses: dated campaigns define them
    courses = {}
    for (p, n), pr in parsed.items():
        if pr["brand"] or not pr["course_date"]:
            continue
        key = f"{norm_course(pr['course'])}|{pr['course_date'].isoformat()}"
        c = courses.setdefault(key, {"key": key, "name": pr["course"], "start": pr["course_date"], "launch": None})
        if len(pr["course"]) > len(c["name"]):
            c["name"] = pr["course"]
        first_row = min(r["d"] for r in camp_rows[(p, n)])
        launch = pr["launch"] or date.fromisoformat((info.get((p, n)) or {}).get("start") or first_row)
        launch = min(launch, date.fromisoformat(first_row))
        c["launch"] = min(c["launch"], launch) if c["launch"] else launch

    def match_course(pr, day: str):
        """Undated campaign (e.g. Google 'קורס משא ומתן'): pick the matching course running on that day."""
        nn = norm_course(pr["course"])
        cands = [c for c in courses.values()
                 if norm_course(c["name"]).startswith(nn) or nn.startswith(norm_course(c["name"]))]
        if not cands:
            return None
        d = date.fromisoformat(day)
        live = [c for c in cands if c["launch"] - timedelta(days=30) <= d <= c["start"]]
        if live:
            return min(live, key=lambda c: c["start"])["key"]
        future = [c for c in cands if c["start"] >= d]
        return (min(future, key=lambda c: c["start"]) if future else max(cands, key=lambda c: c["start"]))["key"]

    # ---------- campaign entries (one per platform campaign x course) + compact daily rows
    days = Index()
    camps = Index()
    camp_meta = {}
    rows = []
    for (p, n), lst in camp_rows.items():
        pr = parsed[(p, n)]
        for r in lst:
            if pr["brand"]:
                ck = BRAND_KEY
            elif pr["course_date"]:
                ck = f"{norm_course(pr['course'])}|{pr['course_date'].isoformat()}"
            else:
                ck = match_course(pr, r["d"]) or BRAND_KEY
            ci = camps((p, n, ck))
            if ci not in camp_meta:
                inf = info.get((p, n), {})
                camp_meta[ci] = {"p": p, "name": n, "course": ck, "type": pr["type"],
                                 "status": status.get(p, {}).get(n), "budget": pr["budget"],
                                 "daily": inf.get("daily"), "start": inf.get("start"), "end": inf.get("end"),
                                 "launch": pr["launch"].isoformat() if pr["launch"] else None}
            rows.append([days(r["d"]), ci, r["i"], r["k"], r["l"], round(r["s"], 2)])
    campaigns = [camp_meta[i] for i in range(len(camps.items))]
    day_list = days.items
    # Platform ids -> deep links (Ads Manager / Google Ads / Campaign Manager)
    acc = {p: str((lambda a: a["id"] if isinstance(a, dict) else a)((CONFIG["accounts"].get(p) or [""])[0]))
           for p in ("meta", "google", "linkedin")}
    g_cust = acc["google"].replace("-", "")
    cid = {}
    for ds, p in (("meta_camp", "meta"), ("meta_ad_info", "meta"), ("li_camp", "linkedin"),
                  ("li_creative_info", "linkedin"), ("g_adgroup_info", "google")):
        for r in raw.get(ds, []):
            if r.get("campaign") and r.get("campaign_id"):
                cid[(p, clean(r["campaign"]))] = str(r["campaign_id"])
    for c in campaigns:
        i = cid.get((c["p"], c["name"]))
        c["id"] = i
        c["url"] = None if not i else {
            "meta": f"https://adsmanager.facebook.com/adsmanager/manage/adsets?act={acc['meta']}&selected_campaign_ids={i}",
            "google": f"https://ads.google.com/aw/adgroups?campaignId={i}&__e={g_cust}",
            "linkedin": f"https://www.linkedin.com/campaignmanager/accounts/{acc['linkedin']}/campaigns/{i}/creatives",
        }[c["p"]]

    # An undated campaign split across several courses is only "live" for the current course.
    split = defaultdict(list)
    for ci, c in enumerate(campaigns):
        split[(c["p"], c["name"])].append(ci)
    for cis in split.values():
        if len(cis) > 1:
            for ci in cis:
                ck = campaigns[ci]["course"]
                if ck != BRAND_KEY and courses[ck]["start"] < as_of:
                    campaigns[ci]["status"] = "ended"

    # ---------- course aggregates + pacing
    def window(rows_, ci_set, d_from, d_to):
        t = {"i": 0, "k": 0, "l": 0.0, "s": 0.0}
        for d, ci, i, k, l, s in rows_:
            if ci in ci_set and d_from <= day_list[d] <= d_to:
                t["i"] += i; t["k"] += k; t["l"] += l; t["s"] += s
        return t

    by_course = defaultdict(set)
    for ci, c in enumerate(campaigns):
        by_course[c["course"]].add(ci)

    iso = as_of.isoformat()
    course_out = []
    for key, cis in by_course.items():
        if key == BRAND_KEY:
            c = {"key": key, "name": "מותג וכללי", "start": None, "launch": None}
        else:
            c = courses[key]
        life = window(rows, cis, "0000", iso)
        last7 = window(rows, cis, (as_of - timedelta(days=6)).isoformat(), iso)
        budgeted = {ci for ci in cis if campaigns[ci]["budget"]}
        name_budget = sum(campaigns[ci]["budget"] for ci in budgeted) or None
        manual = (budgets.get(key) or {}).get("budget")
        if manual:                      # manual course budget covers every platform of the course
            budgeted, budget, source = set(cis), float(manual), "manual"
        else:
            budget, source = name_budget, ("name" if name_budget else None)
        b_life = window(rows, budgeted, "0000", iso)          # pacing compares only campaigns that carry a budget
        b_last7 = window(rows, budgeted, (as_of - timedelta(days=6)).isoformat(), iso)
        live_cis = [ci for ci in cis if campaigns[ci]["status"] == "live"]
        o = {"key": key, "name": c["name"], "brand": key == BRAND_KEY,
             "start": c["start"].isoformat() if c["start"] else None,
             "launch": c["launch"].isoformat() if c["launch"] else None,
             "campaigns": sorted(cis), "live": len(live_cis),
             "budget": budget, "budgetSource": source, "budgetName": name_budget, "budgetMissing": sorted({campaigns[ci]["p"] for ci in cis if not campaigns[ci]["budget"]
                                                        and campaigns[ci]["type"] in LEAD_TYPES}),
             "dailySet": round(sum(campaigns[ci]["daily"] or 0 for ci in live_cis), 0) or None,
             "spent": round(life["s"], 2), "leads": life["l"], "budgetSpent": round(b_life["s"], 2),
             "cpl": round(life["s"] / life["l"], 2) if life["l"] else None,
             "avg7": round(last7["s"] / 7, 2)}
        if c["start"]:
            days_left = (c["start"] - as_of).days
            o["daysLeft"] = days_left
            o["phase"] = "ended" if days_left < 0 else ("live" if live_cis else "paused")
            if budget and c["launch"] and c["start"] > c["launch"]:
                total = (c["start"] - c["launch"]).days + 1
                elapsed = max(0, min((min(as_of, c["start"]) - c["launch"]).days + 1, total))
                expected = budget * elapsed / total
                o.update({"expected": round(expected, 0), "elapsedPct": round(elapsed / total, 3),
                          "pace": round(b_life["s"] / expected, 3) if expected else None,
                          "remaining": round(budget - b_life["s"], 0), "budgetAvg7": round(b_last7["s"] / 7, 2)})
                if days_left > 0:
                    o["requiredDaily"] = round(max(0, budget - b_life["s"]) / days_left, 0)
                    o["projected"] = round(b_life["s"] + o["budgetAvg7"] * days_left, 0)
        else:
            o["phase"] = "live" if live_cis else "paused"
        course_out.append(o)
    phase_rank = {"live": 0, "paused": 1, "ended": 2}

    def sort_key(o):   # brand last; live/paused by nearest start date; ended most recent first
        ordn = date.fromisoformat(o["start"]).toordinal() if o["start"] else 0
        return (o["brand"], phase_rank.get(o["phase"], 3), -ordn if o["phase"] == "ended" else ordn)
    course_out.sort(key=sort_key)
    cidx = {o["key"]: i for i, o in enumerate(course_out)}
    for c in campaigns:
        c["course"] = cidx[c["course"]]

    # ---------- drill-down units (ad set / ad group / ad / creative)
    camp_lookup = defaultdict(list)            # (p, name) -> [ci...]
    for ci, c in enumerate(campaigns):
        camp_lookup[(c["p"], c["name"])].append(ci)

    def ci_for(p, name, day):
        lst = camp_lookup.get((p, clean(name)))
        if not lst:
            return None
        if len(lst) == 1:
            return lst[0]
        pr = parse_name(name)
        ck = match_course(pr, day) if not pr["brand"] else BRAND_KEY
        for ci in lst:
            if course_out[campaigns[ci]["course"]]["key"] == ck:
                return ci
        return lst[0]

    thumbs = {str(r.get("ad_id")): r.get("thumbnail_url") for r in raw.get("meta_ad_info", []) if r.get("ad_id")}
    previews = {str(r.get("ad_id")): r.get("ad_preview_shareable_link") for r in raw.get("meta_ad_info", []) if r.get("ad_id")}
    li_posts = {str(r.get("creative_id")): r.get("ad_post_url") for r in raw.get("li_creative_info", []) if r.get("creative_id")}
    g_groups = {(clean(r.get("campaign")), clean(r.get("ad_group_name"))): str(r.get("ad_group_id"))
                for r in raw.get("g_adgroup_info", []) if r.get("ad_group_id")}

    def unit_links(p, ci, set_name, ad_id):
        c = campaigns[ci]
        if p == "meta":
            return {"preview": previews.get(ad_id),
                    "manage": f"https://adsmanager.facebook.com/adsmanager/manage/ads?act={acc['meta']}&selected_ad_ids={ad_id}"}
        if p == "linkedin":
            return {"post": li_posts.get(ad_id), "manage": c.get("url")}
        g = g_groups.get((c["name"], set_name))
        return {"manage": f"https://ads.google.com/aw/ads?campaignId={c['id']}&adGroupId={g}&__e={g_cust}" if g and c.get("id")
                else c.get("url")}
    set_status = {(clean(r.get("campaign")), clean(r.get("adset_name"))): r.get("adset_effective_status")
                  for r in raw.get("meta_ad_info", [])}
    units = Index()
    unit_rows = []

    def add_unit(p, ci, set_name, ad_name, ad_id, thumb, st, d, i, k, l, s):
        ui = units((p, ci, set_name, ad_name, ad_id),
                   lambda: {"p": p, "c": ci, "set": set_name, "ad": ad_name, "id": ad_id, "thumb": thumb, "setStatus": st,
                            "links": {k: v for k, v in unit_links(p, ci, set_name, ad_id).items() if v}})
        unit_rows.append([days(d), ui, int(i), int(k), round(l, 2), round(s, 2)])

    for r in raw.get("meta_ads", []):
        ci = ci_for("meta", r.get("campaign"), r["date"])
        if ci is None or r["date"] < detail_from:
            continue
        add_unit("meta", ci, clean(r.get("adset_name")), clean(r.get("ad_name")), str(r.get("ad_id")),
                 thumbs.get(str(r.get("ad_id"))), set_status.get((clean(r.get("campaign")), clean(r.get("adset_name")))),
                 r["date"], num(r.get("impressions")), num(r.get("link_clicks")),
                 kpi_count(r, "meta"), num(r.get("spend")))
    for r in raw.get("g_adgroup", []):
        ci = ci_for("google", r.get("campaign"), r["date"])
        if ci is None or r["date"] < detail_from:
            continue
        add_unit("google", ci, clean(r.get("ad_group_name")), None, None, None, r.get("ad_group_status"),
                 r["date"], num(r.get("impressions")), num(r.get("clicks")), kpi_count(r, "google"), num(r.get("cost")))
    for r in raw.get("li_creative", []):
        ci = ci_for("linkedin", r.get("campaign"), r["date"])
        if ci is None or r["date"] < detail_from:
            continue
        title = clean(r.get("sponsored_creative_content_title") or r.get("creative_content_data_share_ad_context_dsc_name")
                      or f"Creative {r.get('creative_id')}")
        add_unit("linkedin", ci, campaigns[ci]["name"], title, str(r.get("creative_id")), None, None,
                 r["date"], num(r.get("impressions")), num(r.get("clicks")), kpi_count(r, "linkedin"),
                 num(r.get("spend")) * fx(r["date"]))

    # ---------- breakdowns (daily x campaign x dimension)
    dims = {}
    bd = {}

    def breakdown(name, dataset, p, dim_fn, i_f, k_f, l_f, s_f, usd=False):
        idx = Index()
        out = []
        for r in raw.get(dataset, []):
            if r["date"] < detail_from:
                continue
            ci = ci_for(p, r.get("campaign"), r["date"])
            if ci is None:
                continue
            s = num(r.get(s_f)) * (fx(r["date"]) if usd else 1)
            out.append([days(r["date"]), ci, idx(dim_fn(r)), int(num(r.get(i_f))), int(num(r.get(k_f))),
                        round(kpi_count(r, p), 2), round(s, 2)])
        dims[name], bd[name] = idx.items, out

    meta_m = ("impressions", "link_clicks", "actions_onsite_conversion_lead_grouped", "spend")
    breakdown("meta_place", "meta_place", "meta",
              lambda r: f"{r.get('publisher_platform')}·{r.get('platform_position')}", *meta_m)
    breakdown("meta_device", "meta_device", "meta", lambda r: r.get("device_platform") or "unknown", *meta_m)
    breakdown("meta_age", "meta_demo", "meta", lambda r: r.get("age") or "unknown", *meta_m)
    breakdown("meta_gender", "meta_demo", "meta", lambda r: r.get("gender") or "unknown", *meta_m)
    breakdown("google_device", "g_device", "google", lambda r: r.get("device") or "unknown",
              "impressions", "clicks", "conversions", "cost")

    li_demo = {}
    for key, payload in (raw.get("li_demo") or {}).items():      # preset -> {"func": rows, "senior": rows}
        end = client["presets"][key][1]
        rate = fx(end)
        li_demo[key] = {
            dim: [[r.get(field), int(num(r.get("impressions"))), int(num(r.get("clicks"))),
                   int(kpi_count(r, "linkedin")), round(num(r.get("spend")) * rate, 2)] for r in payload.get(dim, [])]
            for dim, field in (("func", "member_job_function"), ("senior", "member_seniority"))}

    # ---------- alerts
    alerts = []

    def win_c(ci, d_from, d_to):
        return window(rows, {ci}, d_from, d_to)

    y = iso
    d3 = (as_of - timedelta(days=2)).isoformat()
    d7 = (as_of - timedelta(days=6)).isoformat()
    d28 = (as_of - timedelta(days=27)).isoformat()
    d8 = (as_of - timedelta(days=7)).isoformat()
    reach7 = (client.get("reach", {}).get("last7", {}) or {}).get("meta", {}).get("by", {})
    for ci, c in enumerate(campaigns):
        if c["status"] != "live":
            continue
        base = {"p": c["p"], "campaign": ci, "course": c["course"]}
        yday = win_c(ci, y, y)
        if yday["s"] == 0:
            alerts.append({**base, "sev": "med", "type": "no_spend", "msg": "באוויר אבל לא הוציא כסף אתמול"})
        if c["type"] in ALERT_LEAD_TYPES:
            w3 = win_c(ci, d3, y)
            m28 = win_c(ci, d28, y)
            cpl28 = m28["s"] / m28["l"] if m28["l"] else None
            thr = max(cfg.get("no_leads_min_spend", 300), 2 * cpl28 if cpl28 else 0)
            if w3["l"] == 0 and w3["s"] >= thr:
                alerts.append({**base, "sev": "high", "type": "no_leads",
                               "msg": f"אפס לידים ב-3 הימים האחרונים למרות הוצאה של ₪{w3['s']:,.0f}", "value": round(w3["s"])})
            w7, prev = win_c(ci, d7, y), win_c(ci, d28, d8)
            if w7["l"] >= 3 and prev["l"] >= 5:
                c7, cp = w7["s"] / w7["l"], prev["s"] / prev["l"]
                if c7 > cfg.get("cpl_rise_ratio", 1.3) * cp:
                    alerts.append({**base, "sev": "med", "type": "cpl_up", "value": round(c7 / cp - 1, 3),
                                   "msg": f"עלות לליד ב-7 ימים ₪{c7:,.0f} לעומת ₪{cp:,.0f} ב-3 השבועות שלפני (+{(c7 / cp - 1) * 100:.0f}%)"})
        if c["p"] == "meta" and reach7.get(c["name"]):
            imp7 = win_c(ci, d7, y)["i"]
            freq = imp7 / reach7[c["name"]]
            if freq > cfg.get("meta_frequency_max", 3.5):
                alerts.append({**base, "sev": "med", "type": "frequency", "value": round(freq, 2),
                               "msg": f"תדירות גבוהה ב-7 ימים: {freq:.1f} (קהל נשחק)"})
    for i, o in enumerate(course_out):
        if o["brand"] or o["phase"] == "ended":
            if o["phase"] == "ended" and o["live"]:
                alerts.append({"sev": "high", "type": "live_after_start", "course": i,
                               "msg": f"מועד הקורס עבר ({o['start']}) אבל {o['live']} קמפיינים עדיין באוויר"})
            continue
        pace = o.get("pace")
        if pace is not None and o.get("elapsedPct", 0) >= 0.2:
            if pace < cfg.get("pace_low", 0.8):
                alerts.append({"sev": "med", "type": "under_pace", "course": i, "value": pace,
                               "msg": f"מתחת לקצב התקציב: הוצא ₪{o['budgetSpent']:,.0f} מתוך ₪{o['expected']:,.0f} צפוי ({pace * 100:.0f}%)"})
            elif pace > cfg.get("pace_high", 1.15):
                alerts.append({"sev": "high" if (o.get("projected") or 0) > (o["budget"] or 0) * 1.1 else "med",
                               "type": "over_pace", "course": i, "value": pace,
                               "msg": f"מעל קצב התקציב: הוצא ₪{o['budgetSpent']:,.0f} מתוך ₪{o['expected']:,.0f} צפוי ({pace * 100:.0f}%)"})
        if o.get("projected") and o.get("budget") and o["projected"] > o["budget"] * 1.1:
            alerts.append({"sev": "med", "type": "projected_over", "course": i,
                           "msg": f"בקצב הנוכחי צפוי לסיים ב-₪{o['projected']:,.0f} מול תקציב ₪{o['budget']:,.0f}"})
        if 0 <= o.get("daysLeft", 99) <= cfg.get("course_soon_days", 7) and o["live"]:
            alerts.append({"sev": "info", "type": "course_soon", "course": i,
                           "msg": f"הקורס נפתח בעוד {o['daysLeft']} ימים – לוודא תזמון סגירת הקמפיינים"})
    sev_rank = {"high": 0, "med": 1, "info": 2}
    alerts.sort(key=lambda a: sev_rank[a["sev"]])

    return {
        "meta": {"client": CONFIG.get("name"), "generated": generated, "asOf": client["asOf"], "currency": "ILS",
                 "detailFrom": detail_from, "presets": client["presets"], "schema": 2, "mode": "course",
                 "slug": CONFIG.get("slug"), "kpi": public_kpi(), "validLeads": False,
                 "platforms": client["client"]["platforms"]},
        "days": day_list,
        "courses": course_out,
        "campaigns": campaigns,
        "rows": rows,
        "units": units.items,
        "unitRows": unit_rows,
        "dims": dims,
        "breakdowns": bd,
        "liDemo": li_demo,
        "reach": client.get("reach", {}),
        "alerts": alerts,
    }
