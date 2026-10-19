"""Assemble the published site from per-client build outputs (all encrypted) and the web templates.

site/
  index.html                 -> redirect to team/
  assets/                    logos etc.
  team/index.html            agency home (all clients), team/home.enc.json (TEAM_PASSWORD)
  <slug>/index.html          client page, <slug>/data.enc.json (client link key)
  <slug>/team/index.html     team page,   <slug>/team/data.enc.json (TEAM_PASSWORD)

A client whose build failed keeps its last published files (downloaded from the live site), so one broken
client never blanks the others. Env: TEAM_PASSWORD, DASHBOARD_MASTER_KEY, SITE_URL, TEAM_WRITE_TOKEN, GITHUB_REPOSITORY.
Usage: python engine/build_site.py [build_dir] [site_dir]
"""
import json
import os
import shutil
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
import crypto  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BUILD = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build"
SITE = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "site"
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
PW = os.environ.get("TEAM_PASSWORD", "").strip()
MASTER = os.environ.get("DASHBOARD_MASTER_KEY", "").strip()


def fetch(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "bright-dashboards"}), timeout=60) as r:
            return r.read()
    except Exception:
        return None


def token_check() -> list:
    """Alerts about the budget-saving token (one token for all clients)."""
    tok, repo = os.environ.get("TEAM_WRITE_TOKEN", "").strip(), os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not tok or not repo:
        return [{"sev": "med", "type": "token_missing", "msg": "שמירת תקציבים לא מוגדרת (חסר TEAM_WRITE_TOKEN)"}]
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}", headers={
        "Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json", "User-Agent": "bright-dashboards"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            push = json.load(r).get("permissions", {}).get("push")
            exp = r.headers.get("github-authentication-token-expiration")
    except Exception as e:
        return [{"sev": "high", "type": "token_invalid", "msg": f"הטוקן לשמירת תקציבים לא תקין ({e}) – יש ליצור טוקן חדש"}]
    if not push:
        return [{"sev": "high", "type": "token_invalid", "msg": "לטוקן אין הרשאת כתיבה – שמירת תקציבים לא תעבוד"}]
    if exp:
        left = (datetime.strptime(exp.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                - datetime.now(timezone.utc)).days
        print(f"write token ok, expires {exp} ({left} days)")
        if left <= 30:
            return [{"sev": "high" if left <= 7 else "med", "type": "token_expiry",
                     "msg": f"הטוקן לשמירת תקציבים יפוג בעוד {max(left, 0)} ימים – יש ליצור טוקן חדש ולעדכן את TEAM_WRITE_TOKEN"}]
    return []


def main():
    shutil.rmtree(SITE, ignore_errors=True)        # a local preview server may hold files open on Windows
    shutil.copytree(ROOT / "web" / "assets", SITE / "assets", dirs_exist_ok=True)
    web = lambda n: (ROOT / "web" / n).read_text(encoding="utf-8")
    (SITE / "index.html").write_text('<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex">'
                                     '<meta http-equiv="refresh" content="0;url=team/"><a href="team/">BRIGHT</a>', encoding="utf-8")
    (SITE / "team").mkdir(parents=True, exist_ok=True)
    (SITE / "team" / "index.html").write_text(web("home.html"), encoding="utf-8")

    # previous home (to keep summaries of clients that failed this run)
    prev = {}
    old = fetch(f"{SITE_URL}/team/home.enc.json") if SITE_URL else None
    if old and PW:
        try:
            prev = {c["slug"]: c for c in crypto.decrypt_password(json.loads(old), PW)["clients"]}
        except Exception as e:
            print("previous home not readable:", e)

    clients = []
    for cfg_path in sorted((ROOT / "clients").glob("*/client.json")):
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        slug = cfg["slug"]
        if cfg.get("status") == "inactive":
            continue
        out = SITE / slug
        (out / "team").mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text(web("client.html"), encoding="utf-8")
        (out / "team" / "index.html").write_text(web("team.html"), encoding="utf-8")
        b = BUILD / slug if (BUILD / slug).exists() else BUILD / f"build-{slug}"   # workflow artifacts are build-<slug>
        fresh = (b / "team.enc.json").exists() and (b / "client.enc.json").exists() and (b / "summary.json").exists()
        if fresh:
            shutil.copy(b / "client.enc.json", out / "data.enc.json")
            shutil.copy(b / "team.enc.json", out / "team" / "data.enc.json")
            summ = json.loads((b / "summary.json").read_text(encoding="utf-8"))
        else:  # keep last good version
            for rel in ("data.enc.json", "team/data.enc.json"):
                blob = fetch(f"{SITE_URL}/{slug}/{rel}") if SITE_URL else None
                if blob:
                    (out / rel).write_bytes(blob)
            summ = prev.get(slug, {"slug": slug, "name": cfg.get("name")})
        if os.environ.get("PLAIN") == "1":   # local preview only
            for src, dst in (("team.dev.json", out / "team" / "internal.dev.json"), ("client.dev.json", out / "client.dev.json")):
                if (b / src).exists():
                    shutil.copy(b / src, dst)
        summ.update({"fresh": fresh, "name": cfg.get("name"), "status": cfg.get("status", "active"), "owner": cfg.get("owner")})
        if MASTER:
            k = crypto.client_key_url(crypto.client_key(MASTER, slug, cfg.get("key_version", 1)))
            summ["link"] = f"{SITE_URL}/{slug}/#k={k}"
        summ["team"] = f"{SITE_URL}/{slug}/team/"
        clients.append(summ)
        print(f"{slug}: {'fresh' if fresh else 'KEPT PREVIOUS'}")

    home = {"generated": datetime.now(ZoneInfo("Asia/Jerusalem")).isoformat(timespec="minutes"),
            "alerts": token_check(), "clients": clients}
    if PW:
        (SITE / "team" / "home.enc.json").write_text(json.dumps(crypto.encrypt_password(home, PW)), encoding="utf-8")
    if os.environ.get("PLAIN") == "1":
        (SITE / "team" / "home.dev.json").write_text(json.dumps(home, ensure_ascii=False), encoding="utf-8")
    failed = [c["slug"] for c in clients if not c["fresh"]]
    print(f"site: {len(clients)} clients, {len(failed)} kept previous: {', '.join(failed)}")


if __name__ == "__main__":
    main()
