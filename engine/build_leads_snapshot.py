"""Local only: turn an .xlsx export of the LEAD.IM backup sheet into count files (no personal data).

Usage: python -I scripts/build_leads_snapshot.py <export.xlsx>
Writes:
  scripts/leads_history.json   - history tab, dates <= history_until (fixed forever)
  scripts/leads_snapshot.json  - project tabs, dates > history_until (fallback until the Windsor sheets
                                 connection is live; the workflow overrides it with fresh data)
"""
import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(__file__))
import openpyxl  # noqa: E402

from common import CLIENT_DIR, CONFIG  # noqa: E402
import leads  # noqa: E402


def records(ws):
    rows = ws.iter_rows(values_only=True)
    head = next(rows)
    for r in rows:
        if any(r):
            yield dict(zip(head, r))


wb = openpyxl.load_workbook(sys.argv[1], read_only=True)
until = leads.history_until()
hist = leads.aggregate(records(wb[CONFIG["leads"]["history_tab"]]), swapped_datetime=True, d_to=until)
snap = leads.Counter()
for p in CONFIG["grouping"]["projects"]:
    snap += leads.aggregate(records(wb[p["tab"]]), project_key=p["key"], d_from=until + timedelta(days=1))

for name, c in (("leads_history.json", hist), ("leads_snapshot.json", snap)):
    rows = leads.to_rows(c)
    (CLIENT_DIR.parent.parent / "build" / CONFIG["slug"] / name).write_text(json.dumps({"rows": rows}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    days = [r[0] for r in rows]
    print(f"{name}: {sum(r[4] for r in rows)} leads, {len(rows)} rows, {min(days)} -> {max(days)}")
