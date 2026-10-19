"""Valid leads from a CRM backup Google Sheet (Rotem Shani: LEAD.IM), read live through the Windsor
`googlesheets` connector: one connected Windsor account per tab (history tab + one tab per project).
Only counts are produced; nothing about leads is ever written to the repo.
Local preview: falls back to build/<slug>/leads_history.json + leads_snapshot.json (build_leads_snapshot.py)."""
import json
from datetime import date, timedelta

from common import BUILD_DIR, CONFIG
import leads

FIELDS = ["תאריך", "קמפיין", "סטטוס", "סטטוס: כפול", "סטטוס: פרטים שגויים", "סטטוס: רשימה שחורה",
          "utm_source", "utm_campaign"]


def _tabs() -> list:
    return [CONFIG["leads"]["history_tab"]] + [p["tab"] for p in CONFIG["grouping"]["projects"]]


def sheet_accounts() -> dict:
    """tab name -> Windsor googlesheets account id."""
    from windsor import KEY, http_json
    body = http_json(f"https://connectors.windsor.ai/googlesheets/accounts?api_key={KEY}")
    items = body.get("data", body) if isinstance(body, dict) else body
    out = {}
    for it in items or []:
        label = " ".join(str(it.get(k) or "") for k in ("name", "account_name", "id", "account_id"))
        aid = str(it.get("id") or it.get("account_id") or "")
        for tab in _tabs():
            if tab in label:
                out[tab] = aid
    missing = [t for t in _tabs() if t not in out]
    if missing:
        raise RuntimeError("tabs not connected in Windsor: " + ", ".join(missing))
    return out


def load(get_unused=None) -> tuple:
    """(rows, source) - rows = [[date, project, platform, utm_campaign, n]], source = windsor | local | none."""
    from windsor import get_raw
    try:
        acc = sheet_accounts()
        until = leads.history_until()
        conn = CONFIG["leads"]["windsor_connector"]
        start, end = date(2024, 1, 1), date.today()
        c = leads.aggregate(get_raw(conn, acc[CONFIG["leads"]["history_tab"]], FIELDS, start, end),
                            swapped_datetime=True, d_to=until)
        for p in CONFIG["grouping"]["projects"]:
            c += leads.aggregate(get_raw(conn, acc[p["tab"]], FIELDS, start, end),
                                 project_key=p["key"], d_from=until + timedelta(days=1))
        rows = leads.to_rows(c)
        print(f"valid leads from Windsor sheets: {sum(r[4] for r in rows)}")
        return rows, "windsor"
    except Exception as e:
        print(f"valid leads NOT available via Windsor: {e}")
    files = [BUILD_DIR / n for n in ("leads_history.json", "leads_snapshot.json")]
    if all(f.exists() for f in files):
        return sum((json.loads(f.read_text(encoding="utf-8"))["rows"] for f in files), []), "local"
    return [], "none"
