"""Parser registry: detect the utility/format and return (Meta, [Record])."""
from __future__ import annotations

import os
import tempfile

import pdfplumber

from . import dpdc, generic
from .base import Meta, ParseError, Record  # noqa: F401


def _pdf_head_text(path: str) -> str:
    try:
        with pdfplumber.open(path) as pdf:
            return (pdf.pages[0].extract_text() or "") if pdf.pages else ""
    except Exception:
        return ""


def parse_upload(filename: str, data: bytes):
    """Parse uploaded bytes. The file is written to a private temp path and always removed."""
    ext = os.path.splitext(filename.lower())[1]
    if ext not in (".pdf", ".csv", ".xlsx", ".xlsm"):
        raise ParseError("Unsupported file type. Upload a PDF, CSV or XLSX file.")
    fd, path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if ext == ".pdf":
            if not data.startswith(b"%PDF"):
                raise ParseError("This file is not a valid PDF.")
            head = _pdf_head_text(path)
            if dpdc.looks_like_dpdc(head):
                return dpdc.parse(path)
        return generic.parse(path, filename, data)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
