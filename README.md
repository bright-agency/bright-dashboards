# BRIGHT dashboards (all clients)

One engine, one folder per client. Site: https://bright-agency.github.io/bright-dashboards/

| URL | What | Access |
|---|---|---|
| `/team/` | agency home: every client, alerts, freshness, client links | `TEAM_PASSWORD` |
| `/<slug>/team/` | campaigners dashboard (groups, budgets, alerts, ads, breakdowns) | `TEAM_PASSWORD` |
| `/<slug>/#k=…` | client dashboard | key in the link (copy it from the home page) |

## How it works
- `.github/workflows/build.yml` runs 4×/day. It builds every client in parallel (`engine/fetch_client.py` → `engine/fetch_team.py`), then `engine/build_site.py` assembles the site and deploys it to GitHub Pages.
- **No data is committed**, only code, configs and the encrypted budgets. If a client fails, it keeps its last published version.
- Everything published is encrypted (see `engine/crypto.py`). Team pages use PBKDF2 with the team password. Client pages use a per-client key derived from `DASHBOARD_MASTER_KEY`; bump `key_version` in client.json to revoke a client link.
- Budgets are edited in the team page and saved encrypted to `clients/<slug>/budgets.enc.json` through `TEAM_WRITE_TOKEN`.

## Secrets (4, never per client)
`WINDSOR_API_KEY`, `TEAM_PASSWORD`, `DASHBOARD_MASTER_KEY` (long random string), and `TEAM_WRITE_TOKEN` (fine-grained, this repo only, Contents read/write).

## Client config: `clients/<slug>/client.json`
- `accounts`: `{meta|google|linkedin|tiktok: [{id, currency}]}`. USD accounts are converted at the daily rate.
- `kpi.type`: one of
  - `leads`: Windsor fields per platform, e.g. `meta: [instant-form, pixel]`, `google: conversions`
  - `purchase`: adds `*_value` fields, which drive revenue and ROAS
  - `traffic`: clicks
- `kpi.label`: optional display name (e.g. "פניות").
- `grouping.mode`: one of
  - `course`: course name + date in the campaign name (FORE)
  - `project`: `projects[].match` keywords; without projects it is one group for the whole account, with a monthly budget
- `leads` (optional): CRM valid leads from a Google Sheet via the Windsor `googlesheets` connector (Rotem Shani / LEAD.IM).
- `status`: `active` / `paused` / `inactive` (inactive = not built).

## Add a client
1. `python tools/make_clients.py ../bright-office/clients/registry.yaml` creates configs for new paid clients. Add their KPI to the table in the script first.
2. Or write `clients/<slug>/client.json` by hand (copy a similar client).
3. Push, then run the workflow (Actions → Build dashboards → Run, `only=<slug>`).
4. Copy the client link from the team home page.

## Local preview
`tools/seed_local.py` builds a client from Windsor MCP dumps with `PLAIN=1`. Then run `engine/build_site.py` and serve `site/`.
