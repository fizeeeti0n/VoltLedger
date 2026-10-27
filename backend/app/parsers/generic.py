"""Best-effort parser for DESCO and other utilities: CSV, XLSX, or table-based PDFs.

DESCO's real export format has not been verified, so this adapter maps columns by fuzzy
header matching and falls back to 'date + first amount' extraction. Results are flagged
with lower confidence so the user is told to double-check.
"""
from __future__ import annotations

import csv
import io
import re

import pdfplumber
from openpyxl import load_workbook

from .base import DATE_TOKEN, Meta, ParseError, Record, num, parse_date

# canonical field -> header keywords (lowercase, matched as substring)
HEADER_KEYS = {
    "date": ["recharge date", "date", "time", "তারিখ"],
    "amount": ["recharge amount", "recharge amt", "total amount", "token amount", "amount paid", "amount", "পরিমাণ", "রিচার্জ"],
    "energy": ["energy", "consumption cost", "energy cost", "এনার্জি"],
    "vat": ["vat", "ভ্যাট"],
    "demand": ["demand", "ডিমান্ড"],
    "rent": ["meter rent", "rent", "ভাড়া"],
    "service": ["service"],
    "arrear": ["arrear", "arear", "due", "বকেয়া"],
    "rebate": ["rebate", "ছাড়"],
    "other": ["other"],
    "by": ["recharge by", "channel", "gateway", "source", "bank", "medium", "by"],
}


def _map_headers(headers):
    """Return {field: column_index}. First matching keyword wins; each column used once."""
    cols, used = {}, set()
    norm = [re.sub(r"\s+", " ", (h or "")).strip().lower() for h in headers]
    for fld in ["date", "amount", "energy", "vat", "demand", "rent", "service", "arrear", "rebate", "other", "by"]:
        best = None
        for kw in HEADER_KEYS[fld]:
            for i, h in enumerate(norm):
                if i in used or not h:
                    continue
                if (kw == h) or (kw in h and not (fld == "amount" and any(x in h for x in ("vat", "demand", "rent", "energy", "service", "arrear", "rebate", "other", "date")))):
                    best = i
                    break
            if best is not None:
                break
        if best is not None:
            cols[fld] = best
            used.add(best)
    return cols


def _rows_to_records(rows, meta: Meta):
    """rows: list of lists of cell values (first row that maps date+amount is the header)."""
    header_idx, cols = None, {}
    for i, row in enumerate(rows[:40]):
        c = _map_headers([str(x) if x is not None else "" for x in row])
        if "date" in c and "amount" in c:
            header_idx, cols = i, c
            break
    if header_idx is None:
        return []
    records = []
    for row in rows[header_idx + 1:]:
        if not row or cols["date"] >= len(row):
            continue
        d = parse_date(row[cols["date"]])
        if not d:
            continue
        g = lambda f: num(row[cols[f]]) if f in cols and cols[f] < len(row) else 0.0
        by = str(row[cols["by"]]).strip() if "by" in cols and cols["by"] < len(row) and row[cols["by"]] else ""
        amt = g("amount")
        if amt <= 0:
            continue
        records.append(Record(date=d.isoformat(), amount=amt, service=g("service"), demand=g("demand"),
                              rent=g("rent"), arrear=g("arrear"), rebate=g("rebate"), other=g("other"),
                              vat=g("vat"), energy=g("energy"), by=by))
    missing = [f for f in ("energy", "vat") if f not in cols]
    if missing:
        meta.warnings.append("Columns not found: " + ", ".join(missing) + ". Charge breakdown will be incomplete.")
    return records


def _from_csv(data: bytes, meta):
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return _rows_to_records(list(csv.reader(io.StringIO(text), dialect)), meta)


def _from_xlsx(path, meta):
    wb = load_workbook(path, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        out = _rows_to_records(rows, meta)
        if out:
            break
    return out


def _pdf_text_and_rows(path):
    try:
        with pdfplumber.open(path) as pdf:
            if len(pdf.pages) > 30:
                raise ParseError("This PDF has too many pages for a ledger report.")
            text, rows = [], []
            for p in pdf.pages:
                text.append(p.extract_text() or "")
                for tb in p.extract_tables():
                    rows.extend(tb)
        return "\n".join(text), rows
    except ParseError:
        raise
    except Exception as e:
        raise ParseError("Could not read this PDF. It may be corrupted or password protected.") from e


def _fill_meta_from_text(meta: Meta, text: str):
    t = text.lower()
    if "desco" in t or "dhaka electric supply" in t:
        meta.utility = "DESCO"
    elif "dpdc" in t or "dhaka power distribution" in t:
        meta.utility = "DPDC"
    for label, attr in [(r"customer\s*(?:name|id)?", None), (r"meter\s*(?:no|number)\.?", "meter_no"),
                        (r"account\s*(?:no|number)\.?|customer\s*(?:no|number)\.?", "customer_no"),
                        (r"tariff", "tariff")]:
        if not attr:
            continue
        r = re.search(label + r"\s*[:\-]\s*([A-Za-z0-9\-/]+)", text, re.I)
        if r and not getattr(meta, attr):
            setattr(meta, attr, r.group(1))
    r = re.search(r"(?:customer\s*name|name)\s*[:\-]\s*(.+)", text, re.I)
    if r and not meta.customer_name:
        meta.customer_name = r.group(1).strip()[:80]


def _line_fallback(text: str, meta: Meta):
    """Last resort: every line with a date and >=1 number -> (date, first number >= 50)."""
    records = []
    for line in text.splitlines():
        dm = DATE_TOKEN.search(line)
        if not dm:
            continue
        d = parse_date(dm.group(1))
        if not d:
            continue
        rest = line.replace(dm.group(0), " ")
        nums = [num(x) for x in re.findall(r"-?\d[\d,]*\.?\d*", rest)]
        nums = [n for n in nums if n >= 50]
        if not nums:
            continue
        records.append(Record(date=d.isoformat(), amount=nums[0]))
    if records:
        meta.warnings.append("Could not detect table columns; only date and recharge amount were extracted. "
                             "Charge breakdown (energy, VAT, demand) is unavailable.")
    return records


def parse(path: str, filename: str, data: bytes):
    name = filename.lower()
    meta = Meta(utility="Unknown", confidence="medium")
    if name.endswith(".csv"):
        records = _from_csv(data, meta)
    elif name.endswith((".xlsx", ".xlsm")):
        records = _from_xlsx(path, meta)
    elif name.endswith(".pdf"):
        text, rows = _pdf_text_and_rows(path)
        _fill_meta_from_text(meta, text)
        records = _rows_to_records(rows, meta)
        if not records:
            records = _line_fallback(text, meta)
            meta.confidence = "low"
    else:
        raise ParseError("Unsupported file type. Upload a PDF, CSV or XLSX file.")

    if not records:
        raise ParseError("No recharge rows were found. The layout may not be supported yet.")
    if meta.confidence != "low":
        meta.confidence = "medium"
    if meta.utility == "Unknown":
        meta.utility = "Unknown utility"
    # Collapse exact duplicates (PDF tables repeated across pages)
    uniq = {}
    for r in records:
        uniq[(r.date, r.amount, r.energy, r.by)] = r
    return meta, sorted(uniq.values(), key=lambda x: x.date)
