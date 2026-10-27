# VoltLedger

Upload a DPDC (or DESCO) prepaid vending ledger and get a Bangladesh seasonal consumption report
(Winter / Summer / Rainy) plus a formula-driven Excel workbook in about a second.

## Run locally

```bash
cd backend
pip install -r requirements.txt
export VOLT_SECRET="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000. No build step: the frontend is plain HTML/CSS/JS served by FastAPI
(Chart.js is vendored in `frontend/vendor/`).

## Run with Docker

```bash
docker build -t voltledger .
docker run -p 8000:8000 -e VOLT_SECRET=change-me-to-a-long-random-string -v voltledger-data:/app/backend/data voltledger
```

## Tests

```bash
cd backend && pip install pytest httpx && pytest -q
```

The suite parses `samples/dpdc_sample.pdf` and asserts exact totals (19 recharges, Tk 38,850),
season figures, Excel structure, auth isolation between users and bad-input handling.

## How it works

| Step | Where |
|---|---|
| Detect utility, parse rows | `backend/app/parsers/` (`dpdc.py` exact; `generic.py` best effort for DESCO, CSV, XLSX) |
| Seasons, monthly, burn-rate cycles, findings | `backend/app/analysis.py` |
| Excel workbook (7 sheets, ~450 live formulas, 3 charts) | `backend/app/excel.py` |
| API, auth (PBKDF2 + JWT), SQLite | `backend/app/main.py`, `auth.py`, `db.py` |
| UI | `frontend/` |

API:

- `POST /api/analyze` (multipart `file`): public, returns the full analysis JSON. The file is parsed in memory/temp and deleted; only parsed rows can be saved, and only if the user signs in.
- `POST /api/export`: public, builds the `.xlsx` from parsed records.
- `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`
- `POST/GET /api/reports`, `GET /api/reports/{id}`, `GET /api/reports/{id}/xlsx`, `DELETE /api/reports/{id}`

## Method and limits (also stated inside every report)

- Seasons: Winter Nov-Feb, Summer Mar-May, Rainy Jun-Oct. Editable in the workbook's Assumptions sheet.
- Ledgers contain Taka, not kWh, so spending is the consumption proxy. Add meter readings for a true units analysis.
- Burn rate = recharge / days until the next recharge, assuming a near-zero balance at each top-up.
- Partial months (the month the ledger ends in) are excluded from the season comparison.

## DESCO support: please read

Only the **DPDC** layout was available for development, so only DPDC is verified. The DESCO path
(`parsers/generic.py`) maps columns by header names (date, amount, energy, VAT, demand, rent...) and
works for CSV/XLSX/table PDFs, but it has **not** been tested on a real DESCO export. Results from it are
flagged "medium/low confidence" in the UI. To make it exact, add a `desco.py` parser modelled on
`dpdc.py` and register it in `parsers/__init__.py`, using a real DESCO sample as the test fixture.

## Production checklist

- Set `VOLT_SECRET` (otherwise tokens reset on every restart).
- Put it behind HTTPS (Caddy, Nginx, Fly.io, Railway, Render...).
- SQLite is fine for a single instance; swap `db.py` for Postgres to scale out.
- The rate limiter is in-memory per process; use a shared store (Redis) if you run several workers.
- Optional: `VOLT_RECALC=1` round-trips the Excel file through LibreOffice so cached values appear in
  phone/preview apps (adds a few seconds; needs `soffice` installed). Excel and Google Sheets calculate on open anyway.
- Add email verification, password reset and billing (e.g. Stripe) before charging for it.

## Deploy: Render (backend) + Vercel (frontend)

1. Push this repo to GitHub.
2. **Render**: New > Blueprint > select the repo (uses `render.yaml`). Note the URL, e.g. `https://voltledger-api.onrender.com`.
3. Edit `frontend/vercel.json` and replace `voltledger-api.onrender.com` with your Render hostname. Commit and push.
4. **Vercel**: Add New Project > import the repo > set **Root Directory** to `frontend`, Framework Preset **Other**, no build command. Deploy.

The frontend calls `/api/...` on its own domain and `vercel.json` proxies those calls to Render, so no CORS setup is needed.
`/static/...` is rewritten to the root so the same files work on both hosts.

Free-tier notes: a free Render service sleeps after 15 minutes idle (first request takes about a minute; the page pings `/api/health` on load to wake it)
and its disk is wiped on restart, so accounts and saved reports reset. Uploading a ledger and downloading the Excel report need no database and still work.
To keep accounts, use a paid plan with a persistent disk (see the commented lines in `render.yaml`) or move `db.py` to a hosted Postgres.
