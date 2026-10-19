"""Shared helpers for every client: config loading, period presets, name cleaning, KPI extraction, row builders.

The client is chosen with the CLIENT environment variable (slug = folder name under clients/).
Every per-client run is a separate process, so module-level CONFIG is safe.
"""
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SLUG = os.environ.get("CLIENT", "").strip()
CLIENT_DIR = ROOT / "clients" / SLUG
BUILD_DIR = ROOT / "build" / SLUG
CONFIG = json.loads((CLIENT_DIR / "client.json").read_text(encoding="utf-8")) if SLUG else {}

PLATFORMS = ("meta", "google", "linkedin", "tiktok")
CONNECTOR = {"meta": "facebook", "google": "google_ads", "linkedin": "linkedin", "tiktok": "tiktok"}


def accounts(platform: str) -> list:
    """[{'id': ..., 'currency': 'ILS'|'USD'}] for a platform (empty when the client does not use it)."""
    return [a if isinstance(a, dict) else {"id": a, "currency": "ILS"}
            for a in (CONFIG.get("accounts", {}).get(platform) or [])]


HISTORY_START = date.fromisoformat(CONFIG.get("history_start", "2026-01-01")) if SLUG else date(2026, 1, 1)
DATA_FILE = BUILD_DIR / "data.json"

_INVISIBLE = re.compile("[‎‏‪-‮⁦-⁩﻿]")


def clean(name) -> str:
    return re.sub(r"\s+", " ", _INVISIBLE.sub("", str(name or ""))).strip()


def num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def presets(as_of: date) -> dict:
    """Fixed periods, all ending at as_of (last full day). Must mirror web/client.html + team.html."""
    first_this = as_of.replace(day=1)
    last_prev = first_this - timedelta(days=1)
    q_start = date(as_of.year, ((as_of.month - 1) // 3) * 3 + 1, 1)
    prev_q_end = q_start - timedelta(days=1)
    prev_q_start = date(prev_q_end.year, ((prev_q_end.month - 1) // 3) * 3 + 1, 1)
    return {
        "last7": (as_of - timedelta(days=6), as_of),
        "last15": (as_of - timedelta(days=14), as_of),
        "last30": (as_of - timedelta(days=29), as_of),
        "thisMonth": (first_this, as_of),
        "lastMonth": (last_prev.replace(day=1), last_prev),
        "lastQuarter": (prev_q_start, prev_q_end),
    }


# ---------- KPI: what counts as the client's main result on each platform ----------
KPI = CONFIG.get("kpi", {})
CLICK_FIELD = {"meta": "link_clicks", "google": "clicks", "linkedin": "clicks", "tiktok": "clicks"}
SPEND_FIELD = {"meta": "spend", "google": "cost", "linkedin": "spend", "tiktok": "spend"}
IMPR_FIELD = {"meta": "impressions", "google": "impressions", "linkedin": "impressions", "tiktok": "impressions"}


def _as_list(v):
    return [] if not v else (v if isinstance(v, list) else [v])


def kpi_fields(p: str) -> list:
    """Windsor fields needed for the KPI count (+ value) on platform p."""
    if KPI.get("type") == "traffic":
        return []
    return _as_list(KPI.get(p)) + _as_list(KPI.get(p + "_value"))


def kpi_count(r: dict, p: str) -> float:
    if KPI.get("type") == "traffic":
        return num(r.get(CLICK_FIELD[p]))
    return sum(num(r.get(f)) for f in _as_list(KPI.get(p)))


def kpi_value(r: dict, p: str) -> float:
    return sum(num(r.get(f)) for f in _as_list(KPI.get(p + "_value")))


def row_fields(p: str) -> list:
    return ["date", "campaign", IMPR_FIELD[p], CLICK_FIELD[p], SPEND_FIELD[p]] + kpi_fields(p)


def fx_lookup(rates: dict):
    """rates: {'YYYY-MM-DD': usd_to_ils}. Returns f(date)->rate, carrying the last known rate forward."""
    days = sorted(rates)

    def f(d: str) -> float:
        best = None
        for k in days:
            if k <= d:
                best = rates[k]
            else:
                break
        return best if best is not None else (rates[days[0]] if days else 1.0)
    return f


def make_row(r: dict, p: str, rate: float = 1.0) -> dict:
    """Normalised daily campaign row. rate = account currency -> ILS for that day."""
    out = {"d": r["date"], "p": p, "c": clean(r.get("campaign")),
           "i": int(num(r.get(IMPR_FIELD[p]))), "k": int(num(r.get(CLICK_FIELD[p]))),
           "l": round(kpi_count(r, p), 2), "s": round(num(r.get(SPEND_FIELD[p])) * rate, 2)}
    v = kpi_value(r, p)
    if v:
        out["v"] = round(v * rate, 2)
    return out


def campaign_status(raw: str, end, as_of: date) -> str:
    """Normalise platform status to live / paused / ended. end = 'YYYY-MM-DD...' or None."""
    raw = (raw or "").upper()
    if raw in ("COMPLETED", "REMOVED", "ARCHIVED", "DELETED", "CANCELED"):
        return "ended"
    if end and str(end)[:10] <= as_of.isoformat():
        return "ended"
    if raw in ("ACTIVE", "ENABLED", "ENABLE", "CAMPAIGN_STATUS_ENABLE"):
        return "live"
    return "paused"


def write_data(as_of: date, rows: list, reach: dict, updated_iso: str, status: dict = None) -> dict:
    rows = [r for r in rows if r["i"] or r["k"] or r["l"] or r["s"]]
    rows.sort(key=lambda r: (r["d"], r["p"], r["c"]))
    pr = presets(as_of)
    out = {
        "client": {"slug": SLUG, "name": CONFIG.get("name"), "logo": CONFIG.get("logo"),
                   "platforms": [p for p in PLATFORMS if accounts(p)], "kpi": public_kpi()},
        "updated": updated_iso,
        "asOf": as_of.isoformat(),
        "presets": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in pr.items()},
        # reach[preset][platform] = {"total": n, "by": {campaign: n}}
        "reach": {k: {p: {"total": v["total"], "by": {clean(n): x for n, x in v.get("by", {}).items()}}
                      for p, v in plats.items()}
                  for k, plats in reach.items()},
        "status": {p: {clean(c): v for c, v in m.items()} for p, m in (status or {}).items()},
        "rows": rows,
    }
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[{SLUG}] data rows={len(rows)} asOf={as_of}")
    return out


def public_kpi() -> dict:
    """Labels the pages use for the main result."""
    t = KPI.get("type", "leads")
    base = {"leads": ("לידים", "ליד", "עלות לליד", "אחוז המרה"),
            "purchase": ("רכישות", "רכישה", "עלות לרכישה", "אחוז המרה"),
            "traffic": ("הקלקות", "הקלקה", "עלות להקלקה", "CTR")}[t]
    label = KPI.get("label") or base[0]
    return {"type": t, "label": label, "unit": KPI.get("unit") or base[1],
            "cpa": KPI.get("cpa_label") or base[2], "rate": base[3], "value": t == "purchase"}
