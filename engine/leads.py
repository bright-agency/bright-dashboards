"""Valid leads from the client's LEAD.IM backup sheet -> daily counts per project / platform / campaign.

Only counts leave this module: no names, phones or e-mails are stored anywhere.
Sources:
  * history tab ("דאטה היסטורי - כל הפרוייקטים") for dates <= history_until
  * one tab per project for later dates (read live through the Windsor googlesheets connector)
Output rows: [date, project_key, platform, utm_campaign, count]
"""
import re
from collections import Counter
from datetime import date, datetime

from common import CONFIG, clean

META_SOURCES = {"facebook", "instagram", "ig", "fb", "meta", "an", "messenger"}
TEST_CAMPAIGNS = re.compile(r"test", re.I)
VALID_STATUS = {"חדש", "new"}
YES = {"כן", "yes", "true", "1"}


def parse_date(v, swapped_datetime=False):
    """LEAD.IM exports dd/mm/yyyy. In the history tab Google Sheets turned dates with day<=12 into real
    (US, month-first) dates, so for real datetime values day and month must be swapped back."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return date(v.year, v.day, v.month) if swapped_datetime else v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    m = re.match(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", s)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def project_for(sheet_campaign: str):
    s = sheet_campaign or ""
    for p in CONFIG["grouping"]["projects"]:
        if any(k in s for k in p["sheet"]):
            return p["key"]
    return None


def platform_for(source: str) -> str:
    s = (source or "").strip().lower()
    if s in META_SOURCES:
        return "meta"
    if s == "google":
        return "google"
    return "other"


def is_valid(rec: dict) -> bool:
    st = str(rec.get("סטטוס") or "").strip().lower()
    if st and st not in VALID_STATUS:
        return False
    for flag in ("סטטוס: כפול", "סטטוס: פרטים שגויים", "סטטוס: רשימה שחורה"):
        if str(rec.get(flag) or "").strip().lower() in YES:
            return False
    return True


def aggregate(records, project_key=None, swapped_datetime=False, d_from=None, d_to=None) -> Counter:
    """records: dicts keyed by the sheet's Hebrew headers. project_key: fixed project (project tabs)."""
    out = Counter()
    for r in records:
        if not is_valid(r):
            continue
        d = parse_date(r.get("תאריך"), swapped_datetime)
        if d is None or (d_from and d < d_from) or (d_to and d > d_to):
            continue
        pk = project_key or project_for(str(r.get("קמפיין") or ""))
        if not pk:
            continue
        camp = clean(str(r.get("utm_campaign") or ""))
        if TEST_CAMPAIGNS.search(camp):
            continue
        out[(d.isoformat(), pk, platform_for(r.get("utm_source")), camp)] += 1
    return out


def to_rows(counter: Counter) -> list:
    return [[d, p, pl, c, n] for (d, p, pl, c), n in sorted(counter.items())]


def history_until() -> date:
    return date.fromisoformat(CONFIG["leads"]["history_until"])
