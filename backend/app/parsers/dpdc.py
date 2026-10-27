"""Parser for the DPDC 'Vending Ledger Report' PDF (prepaid meter recharge history)."""
from __future__ import annotations

import re

import pdfplumber

from .base import Meta, ParseError, Record, num, parse_date

# Sl. | date | 9 numeric columns | 'Recharge By'
_NUM = r"(-?\d[\d,]*\.?\d*)"
_ROW = re.compile(
    r"^\s*(\d{1,4})\s+(\d{1,2}-[A-Za-z]{3}-\d{2,4})\s+" + r"\s+".join([_NUM] * 9) + r"\s+(.+?)\s*$")


def _meta_from_text(text: str) -> Meta:
    m = Meta(utility="DPDC")

    def grab(pattern, flags=0):
        r = re.search(pattern, text, flags)
        return r.group(1).strip() if r else ""

    m.office = grab(r"NOCS\s*:\s*(.+?)(?:\s+Meter No|\n)")
    m.meter_no = grab(r"Meter No\s*:\s*(\S+)")
    m.customer_no = grab(r"Customer No\s*:\s*(\S+)")
    m.customer_name = grab(r"Customer Name\s*:\s*(.+?)(?:\s+Type\s*:|\n)")
    m.meter_type = grab(r"Type\s*:\s*(\S+)")
    m.load = grab(r"Sanc\./Conn\. Load\s*:\s*(.+?)(?:\s+Customer No|\n)")
    m.address = grab(r"Address\s*:\s*(.+?)(?:\s+Tariff\s*:|\n)")
    m.tariff = grab(r"Tariff\s*:\s*(\S+)")
    period = re.search(r"period of\s+(\d{1,2}-\d{1,2}-\d{4})\s+to\s+(\d{1,2}-[A-Za-z]{3}-\d{2,4})", text, re.I)
    if period:
        ps = parse_date(period.group(1))
        m.period_start = ps.isoformat() if ps else None
    upto = re.search(r"data upto\s+(\d{1,2}-[A-Za-z]{3}-\d{2,4})", text, re.I)
    end = parse_date(upto.group(1)) if upto else (parse_date(period.group(2)) if period else None)
    m.period_end = end.isoformat() if end else None
    return m


def looks_like_dpdc(text: str) -> bool:
    t = text.lower()
    return "dhaka power distribution" in t or ("vending ledger" in t and "dpdc" in t)


def parse(path: str):
    try:
        with pdfplumber.open(path) as pdf:
            if len(pdf.pages) > 30:
                raise ParseError("This PDF has too many pages for a ledger report.")
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    except ParseError:
        raise
    except Exception as e:  # corrupt / encrypted PDF
        raise ParseError("Could not read this PDF. It may be corrupted or password protected.") from e

    meta = _meta_from_text(text)
    records, seen = [], set()
    for line in text.splitlines():
        r = _ROW.match(line)
        if not r:
            continue
        sl = r.group(1)
        d = parse_date(r.group(2))
        if not d:
            continue
        vals = [num(x) for x in r.groups()[2:11]]
        key = (sl, d)
        if key in seen:
            continue
        seen.add(key)
        records.append(Record(
            date=d.isoformat(), amount=vals[0], service=vals[1], demand=vals[2], rent=vals[3],
            arrear=vals[4], rebate=vals[5], other=vals[6], vat=vals[7], energy=vals[8], by=r.group(12).strip()))

    if not records:
        raise ParseError("No recharge rows were found. Is this a DPDC 'Vending Ledger Report' PDF?")

    # Integrity check: amount = service + demand + rent + arrear + rebate + vat + energy
    bad = [r for r in records if abs(r.amount - (r.service + r.demand + r.rent + r.arrear + r.rebate + r.vat + r.energy)) > 1.0]
    if bad:
        meta.confidence = "medium"
        meta.warnings.append(
            f"{len(bad)} row(s) did not reconcile (amount != sum of charges). Check them against your PDF.")
    return meta, sorted(records, key=lambda x: x.date)
