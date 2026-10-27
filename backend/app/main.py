"""VoltLedger API: upload a DPDC/DESCO prepaid ledger -> instant seasonal report + Excel."""
from __future__ import annotations

import os
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date
from typing import List, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, db
from .analysis import analyze
from .excel import build_workbook
from .parsers import parse_upload
from .parsers.base import ParseError

MAX_UPLOAD = int(os.environ.get("VOLT_MAX_UPLOAD_MB", "10")) * 1024 * 1024
FRONTEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))


@asynccontextmanager
async def lifespan(app):
    db.init()
    yield


app = FastAPI(title="VoltLedger", version="1.0.0", lifespan=lifespan)

# ---------------------------------------------------------------- basic in-memory rate limit
_hits = defaultdict(deque)


def rate_limit(key: str, limit: int, window: int = 60):
    now = time.time()
    q = _hits[key]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many requests. Please wait a minute and try again.")
    q.append(now)


def client_ip(req: Request) -> str:
    return (req.headers.get("x-forwarded-for", "").split(",")[0].strip()) or (req.client.host if req.client else "?")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'")
    return resp


@app.exception_handler(ParseError)
async def parse_error_handler(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=422)


# ---------------------------------------------------------------- models
class RecordIn(BaseModel):
    date: date
    amount: float = Field(gt=0, lt=10_000_000)
    service: float = 0
    demand: float = 0
    rent: float = 0
    arrear: float = 0
    rebate: float = 0
    other: float = 0
    vat: float = 0
    energy: float = 0
    by: str = Field(default="", max_length=80)


class MetaIn(BaseModel):
    utility: str = Field(default="Unknown", max_length=40)
    customer_name: str = Field(default="", max_length=120)
    customer_no: str = Field(default="", max_length=40)
    meter_no: str = Field(default="", max_length=40)
    tariff: str = Field(default="", max_length=20)
    load: str = Field(default="", max_length=40)
    address: str = Field(default="", max_length=200)
    meter_type: str = Field(default="", max_length=20)
    office: str = Field(default="", max_length=80)
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    confidence: str = "high"
    warnings: List[str] = []


class ReportIn(BaseModel):
    meta: MetaIn
    records: List[RecordIn] = Field(min_length=1, max_length=3000)
    season_map: Optional[dict] = None
    filename: str = Field(default="ledger", max_length=200)


class Creds(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(min_length=8, max_length=200)


def _norm(rep: ReportIn):
    meta = rep.meta.model_dump(mode="json")
    records = [r.model_dump(mode="json") for r in rep.records]
    return meta, records


def _run(meta, records, season_map=None):
    try:
        return analyze(meta, records, season_map)
    except ValueError as e:
        raise HTTPException(422, f"Could not analyze this ledger: {e}")


# ---------------------------------------------------------------- auth
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@app.post("/api/auth/register")
def register(c: Creds, req: Request):
    rate_limit("reg:" + client_ip(req), 10, 3600)
    email = c.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "Enter a valid email address.")
    if db.get_user_by_email(email):
        raise HTTPException(409, "An account with this email already exists.")
    uid = db.create_user(email, auth.hash_password(c.password))
    return {"token": auth.make_token(uid), "email": email}


@app.post("/api/auth/login")
def login(c: Creds, req: Request):
    rate_limit("login:" + client_ip(req), 20, 300)
    u = db.get_user_by_email(c.email.strip().lower())
    if not u or not auth.verify_password(c.password, u["pw_hash"]):
        raise HTTPException(401, "Incorrect email or password.")
    return {"token": auth.make_token(u["id"]), "email": u["email"]}


@app.get("/api/auth/me")
def me(user=Depends(auth.current_user)):
    return {"email": user["email"]}


# ---------------------------------------------------------------- core: analyze + export (public)
@app.post("/api/analyze")
async def analyze_upload(req: Request, file: UploadFile = File(...)):
    rate_limit("an:" + client_ip(req), 30)
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, f"File too large (limit {MAX_UPLOAD // 1024 // 1024} MB).")
    if not data:
        raise HTTPException(422, "The uploaded file is empty.")
    t0 = time.time()
    meta, records = parse_upload(file.filename or "upload", data)
    result = _run(meta.to_dict(), [asdict(r) for r in records])
    result["filename"] = (file.filename or "ledger")[:200]
    result["elapsed_ms"] = int((time.time() - t0) * 1000)
    return result


@app.post("/api/export")
def export_xlsx(rep: ReportIn, req: Request):
    rate_limit("ex:" + client_ip(req), 30)
    meta, records = _norm(rep)
    a = _run(meta, records, rep.season_map)
    return _xlsx_response(a, meta)


def _xlsx_response(a, meta):
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{meta.get('utility', 'Utility')}_{meta.get('customer_no') or 'report'}")
    return Response(
        build_workbook(a),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}_Seasonal_Report.xlsx"'})


# ---------------------------------------------------------------- saved reports (auth)
@app.post("/api/reports")
def save_report(rep: ReportIn, user=Depends(auth.current_user)):
    meta, records = _norm(rep)
    a = _run(meta, records, rep.season_map)
    title = f"{meta.get('utility', 'Utility')} {meta.get('customer_no') or ''} ({a['kpis']['first']} to {a['kpis']['data_end']})".replace("  ", " ")
    rid = db.save_report(user["id"], title, meta.get("utility", ""), rep.filename, meta, records)
    return {"id": rid, "title": title}


@app.get("/api/reports")
def list_reports(user=Depends(auth.current_user)):
    return db.list_reports(user["id"])


def _load(user, rid):
    import json
    r = db.get_report(user["id"], rid)
    if not r:
        raise HTTPException(404, "Report not found.")
    meta, records = json.loads(r["meta_json"]), json.loads(r["records_json"])
    return r, meta, records


@app.get("/api/reports/{rid}")
def get_report(rid: int, user=Depends(auth.current_user)):
    r, meta, records = _load(user, rid)
    a = _run(meta, records)
    a["filename"] = r["filename"]
    a["id"] = rid
    return a


@app.get("/api/reports/{rid}/xlsx")
def report_xlsx(rid: int, user=Depends(auth.current_user)):
    _, meta, records = _load(user, rid)
    return _xlsx_response(_run(meta, records), meta)


@app.delete("/api/reports/{rid}")
def delete_report(rid: int, user=Depends(auth.current_user)):
    if not db.delete_report(user["id"], rid):
        raise HTTPException(404, "Report not found.")
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True}


# ---------------------------------------------------------------- frontend
@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND, "index.html"))


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
