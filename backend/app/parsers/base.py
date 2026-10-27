"""Shared types and helpers for utility-ledger parsers."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Optional

MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


class ParseError(Exception):
    """Raised when a file cannot be understood. Message is safe to show to users."""


@dataclass
class Record:
    date: str  # ISO yyyy-mm-dd
    amount: float  # total Tk recharged
    service: float = 0.0
    demand: float = 0.0
    rent: float = 0.0
    arrear: float = 0.0
    rebate: float = 0.0
    other: float = 0.0
    vat: float = 0.0
    energy: float = 0.0
    by: str = ""


@dataclass
class Meta:
    utility: str = "Unknown"
    customer_name: str = ""
    customer_no: str = ""
    meter_no: str = ""
    tariff: str = ""
    load: str = ""
    address: str = ""
    meter_type: str = ""
    office: str = ""
    period_start: Optional[str] = None
    period_end: Optional[str] = None  # ledger "data up to" date
    confidence: str = "high"  # high | medium | low
    warnings: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def num(s) -> float:
    """Parse '1,234.50', '-9.28', '(9.28)', '' -> float."""
    if s is None:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace(",", "").replace("৳", "").replace("Tk.", "").replace("Tk", "").strip()
    if t in ("", "-", "--", "N/A", "NA"):
        return 0.0
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t)
    except ValueError:
        return 0.0
    return -v if neg else v


_DATE_FORMATS = [
    "%d-%b-%y", "%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y",
    "%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y",
    "%d-%b-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %I:%M:%S %p",
    "%d-%m-%Y %H:%M:%S",
]


def parse_date(s) -> Optional[date]:
    if s is None:
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    t = str(s).strip()
    if not t:
        return None
    t = re.sub(r"\s+", " ", t)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(t.title() if "%b" in fmt or "%B" in fmt else t, fmt).date()
        except ValueError:
            continue
    return None


DATE_TOKEN = re.compile(
    r"\b(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{1,2}[-\s][A-Za-z]{3,9}[-\s,]*\d{2,4}|\d{4}[-/]\d{2}[-/]\d{2})\b")
