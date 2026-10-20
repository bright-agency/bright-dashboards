"""Valid leads from a CRM backup Google Sheet (Rotem Shani: LEAD.IM), read live through the Windsor
`googlesheets` connector. Every tab is connected in Windsor separately; its account id is
"<spreadsheet_id>-<gid>" (gid = the number after #gid= in the tab's URL). Config: leads.history_gid and
grouping.projects[].gid. Only counts are produced; nothing about leads is ever written to the repo.
Local preview: falls back to build/<slug>/leads_history.json + leads_snapshot.json (build_leads_snapshot.py)."""
import json
from datetime import date, timedelta

from common import BUILD_DIR, CONFIG
import leads

# Windsor field ids (spaces -> "_") -> the sheet's header names used by leads.aggregate.
# "#" (row number) keeps every lead a separate row (Windsor would merge identical rows otherwise).
FIELDS = {"#": "#", "תאריך": "תאריך", "קמפיין": "קמפיין", "סטטוס": "סטטוס",
          "סטטוס:_כפול": "סטטוס: כפול", "סטטוס:_פרטים_שגויים": "סטטוס: פרטים שגויים",
          "סטטוס:_רשימה_שחורה": "סטטוס: רשימה שחורה", "utm_source": "utm_source", "utm_campaign": "utm_campaign"}


def tabs() -> list:
    """[(tab name, gid, project_key or None for the history tab)]"""
    L = CONFIG["leads"]
    out = [(L["history_tab"], L.get("history_gid"), None)]
    for p in CONFIG["grouping"]["projects"]:          # gid may be a list: several tabs feed one project
        gids = p["gid"] if isinstance(p.get("gid"), list) else [p.get("gid")]
        out += [(p["tab"] + (f" #{i + 1}" if i else ""), g, p["key"]) for i, g in enumerate(gids)]
    return out


def _records(get_raw, account, start, end) -> list:
    rows = get_raw(CONFIG["leads"]["windsor_connector"], account, list(FIELDS), start, end)
    return [{FIELDS.get(k, k): v for k, v in r.items()} for r in rows]


def load(get_unused=None) -> tuple:
    """(rows, source, missing_tabs) - rows = [[date, project, platform, utm_campaign, n]],
    source = windsor | partial | local | none."""
    from windsor import get_raw
    sid, until = CONFIG["leads"]["spreadsheet_id"], leads.history_until()
    start, end = date(2024, 1, 1), date.today()
    c, missing = leads.Counter(), []
    for tab, gid, pk in tabs():
        if not gid:
            missing.append(tab)
            continue
        try:
            recs = _records(get_raw, f"{sid}-{gid}", start, end)
        except Exception as e:
            print(f"leads tab '{tab}' not readable: {e}")
            missing.append(tab)
            continue
        if pk is None:
            c += leads.aggregate(recs, swapped_datetime=True, d_to=until)
        else:
            c += leads.aggregate(recs, project_key=pk, d_from=until + timedelta(days=1))
    if len(missing) < len(tabs()):
        rows = leads.to_rows(c)
        print(f"valid leads from Windsor sheets: {sum(r[4] for r in rows)}; missing tabs: {missing or 'none'}")
        return rows, ("partial" if missing else "windsor"), missing
    print("valid leads NOT available via Windsor (no tab connected)")
    files = [BUILD_DIR / n for n in ("leads_history.json", "leads_snapshot.json")]
    if all(f.exists() for f in files):
        return sum((json.loads(f.read_text(encoding="utf-8"))["rows"] for f in files), []), "local", []
    return [], "none", missing
