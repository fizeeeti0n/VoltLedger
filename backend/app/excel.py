"""Build the formula-driven Excel report from parsed records + analysis."""
from __future__ import annotations

import datetime as dt
import io
import os
import shutil
import subprocess
import tempfile

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as CL

from .analysis import DEFAULT_SEASON_MAP, SEASON_MONTHS_LABEL, SEASON_ORDER

FN, BLUE = "Arial", "0000FF"
HDR = PatternFill("solid", fgColor="1F3864")
SUB = PatternFill("solid", fgColor="D9E2F3")
TOT = PatternFill("solid", fgColor="F2F2F2")
_t = Side(style="thin", color="BFBFBF")
BORDER = Border(left=_t, right=_t, top=_t, bottom=_t)


def _f(bold=False, color="000000", size=10, italic=False):
    return Font(name=FN, bold=bold, color=color, size=size, italic=italic)


def put(ws, ref, v, fmt=None, bold=False, color="000000", fill=None, align=None, border=True,
        italic=False, size=10, wrap=False):
    c = ws[ref]
    c.value = v
    c.font = _f(bold, color, size, italic)
    if fmt:
        c.number_format = fmt
    if fill:
        c.fill = fill
    if border:
        c.border = BORDER
    if align or wrap:
        c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)
    return c


def header(ws, row, labels):
    for i, lab in enumerate(labels, 1):
        put(ws, f"{CL(i)}{row}", lab, bold=True, color="FFFFFF", fill=HDR, align="center", wrap=True)
    ws.row_dimensions[row].height = 32


def title(ws, text, sub=None):
    put(ws, "A1", text, bold=True, size=14, border=False, color="1F3864")
    if sub:
        put(ws, "A2", sub, italic=True, border=False, color="595959")


def widths(ws, spec):
    for k, w in spec.items():
        ws.column_dimensions[k].width = w


def build_workbook(analysis: dict) -> bytes:
    meta, recs = analysis["meta"], analysis["records"]
    smap = {int(k): v for k, v in analysis["season_map"].items()}
    monthly, cycles = analysis["monthly"], analysis["cycles"]
    k = analysis["kpis"]

    wb = Workbook()
    ws_sum = wb.active
    ws_sum.title = "Summary"
    ws_sea, ws_mon, ws_cyc = (wb.create_sheet(n) for n in ("Seasonal", "Monthly", "Cycles"))
    ws_led, ws_ass, ws_cht = (wb.create_sheet(n) for n in ("Ledger", "Assumptions", "Charts"))
    D = dt.date.fromisoformat
    utility = meta.get("utility") or "Utility"

    # ------------------------------------------------ Assumptions
    ws = ws_ass
    title(ws, "Assumptions, Season Definitions & Notes",
          "Blue = inputs. Edit the season map below and every sheet recalculates.")
    put(ws, "A4", "Ledger start date")
    put(ws, "B4", D(k["first"]).replace(day=1) if not meta.get("period_start") else D(meta["period_start"]),
        "dd-mmm-yyyy", color=BLUE)
    put(ws, "A5", "Ledger end date (data up to)")
    put(ws, "B5", D(k["data_end"]), "dd-mmm-yyyy", color=BLUE)
    put(ws, "C4", f"Source: {utility} ledger report", border=False, italic=True)
    put(ws, "C5", "Source: ledger footer / latest recharge date", border=False, italic=True)
    header(ws, 8, ["Month #", "Month", "Season"])
    mn = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for m in range(1, 13):
        r = 8 + m
        put(ws, f"A{r}", m, color=BLUE, align="center")
        put(ws, f"B{r}", mn[m - 1], color=BLUE, align="center")
        put(ws, f"C{r}", smap.get(m, DEFAULT_SEASON_MAP[m]), color=BLUE, align="center")
    notes = [
        "SEASONS (Bangladesh): Winter = Nov-Feb, Summer / pre-monsoon hot season = Mar-May, Rainy / monsoon = Jun-Oct. "
        "October is treated as late monsoon. Change the map above to use your own definition.",
        "RECHARGE IS NOT kWh: ledgers show Taka paid, not units consumed. Spending is used as a proxy for consumption.",
        "PREPAID BALANCE CARRY-OVER: unspent balance at the start and end of the period is unknown, so the first and the "
        "last (partial) months can be understated. Partial months are excluded from the like-for-like season comparison.",
        "BURN-RATE METHOD (Cycles sheet): recharge amount / days until the next recharge, assuming the balance was close to "
        "zero at each top-up. Same-day recharges are merged. The latest cycle is excluded because it is still running.",
        "LEDGER CHECK: Amount = Service + Demand + Meter Rent + Arrear + Rebate + VAT + Energy. 'Other Charge' mirrors the "
        "rebate in DPDC ledgers and is not added. Non-zero values in the Check column flag rows to review.",
    ] + [f"WARNING: {w}" for w in analysis["warnings"] if not w.startswith("Spending (Tk)")]
    put(ws, "A23", "NOTES", bold=True, border=False, size=11)
    for i, n in enumerate(notes):
        r = 24 + i
        ws.merge_cells(f"A{r}:H{r}")
        put(ws, f"A{r}", f"{i + 1}. {n}", border=False, wrap=True)
        ws[f"A{r}"].alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[r].height = 46
    widths(ws, {"A": 28, "B": 16, "C": 14, "D": 12, "E": 12, "F": 12, "G": 12, "H": 12})
    SEAS = "Assumptions!$C$9:$C$20"
    MNUM = "Assumptions!$A$9:$A$20"

    # ------------------------------------------------ Ledger
    ws = ws_led
    title(ws, "Vending Ledger (source data, oldest first)", "Blue = parsed source values; black = formulas.")
    header(ws, 3, ["#", "Recharge Date", "Month key", "Season", "Recharge Amount (Tk)", "Service Charge",
                   "Demand Charge", "Meter Rent", "Arrear Amt.", "Rebate", "Other Charge", "VAT",
                   "Energy Amt. (Tk)", "Recharge By", "Check (should be 0)"])
    L0 = 4
    L1 = L0 + len(recs) - 1
    for i, r in enumerate(recs):
        row = L0 + i
        put(ws, f"A{row}", i + 1, align="center")
        put(ws, f"B{row}", D(r["date"]), "dd-mmm-yy", color=BLUE, align="center")
        put(ws, f"C{row}", f"=YEAR(B{row})*100+MONTH(B{row})", "0", align="center")
        put(ws, f"D{row}", f"=INDEX({SEAS},MATCH(MONTH(B{row}),{MNUM},0))", align="center")
        for col, key in zip("EFGHIJKLM", ["amount", "service", "demand", "rent", "arrear", "rebate", "other", "vat", "energy"]):
            put(ws, f"{col}{row}", r.get(key, 0), "#,##0.00", color=BLUE)
        put(ws, f"N{row}", r.get("by", ""), color=BLUE)
        put(ws, f"O{row}", f"=ROUND(E{row}-(F{row}+G{row}+H{row}+I{row}+J{row}+L{row}+M{row}),2)", "#,##0.00")
    LT = L1 + 1
    put(ws, f"A{LT}", "Total", bold=True, fill=TOT)
    for col in "BCDN":
        put(ws, f"{col}{LT}", None, fill=TOT)
    for col in "EFGHIJKLMO":
        put(ws, f"{col}{LT}", f"=SUM({col}{L0}:{col}{L1})", "#,##0.00", bold=True, fill=TOT)
    widths(ws, {"A": 6, "B": 13, "C": 10, "D": 10, "E": 14, "F": 10, "G": 10, "H": 10, "I": 10,
                "J": 10, "K": 11, "L": 10, "M": 13, "N": 22, "O": 12})
    ws.freeze_panes = "A4"
    LR = lambda c: f"Ledger!${c}${L0}:${c}${L1}"

    # ------------------------------------------------ Monthly
    ws = ws_mon
    title(ws, "Monthly Breakdown", "Calendar-month view. Partial months are flagged and excluded from season comparisons.")
    header(ws, 4, ["Month", "Month key", "Month start", "Season", "Days covered", "Full month?", "# Recharges",
                   "Total Recharge (Tk)", "Energy Amt. (Tk)", "Demand + Rent (Tk)", "VAT (Tk)", "Rebate (Tk)",
                   "Tk per day", "Tk per 30 days"])
    M0 = 5
    M1 = M0 + len(monthly) - 1
    for i, mo in enumerate(monthly):
        r = M0 + i
        put(ws, f"A{r}", f'=TEXT(C{r},"mmm-yy")', align="center")
        put(ws, f"B{r}", mo["year"] * 100 + mo["month"], "0", color=BLUE, align="center")
        put(ws, f"C{r}", f"=DATE(INT(B{r}/100),MOD(B{r},100),1)", "dd-mmm-yy", align="center")
        put(ws, f"D{r}", f"=INDEX({SEAS},MATCH(MOD(B{r},100),{MNUM},0))", align="center")
        put(ws, f"E{r}", f"=MIN(EOMONTH(C{r},0),Assumptions!$B$5)-MAX(C{r},Assumptions!$B$4)+1", "0", align="center")
        put(ws, f"F{r}", f'=IF(EOMONTH(C{r},0)<=Assumptions!$B$5,"Yes","Partial")', align="center")
        put(ws, f"G{r}", f"=COUNTIF({LR('C')},B{r})", "0", align="center")
        put(ws, f"H{r}", f"=SUMIFS({LR('E')},{LR('C')},B{r})", "#,##0")
        put(ws, f"I{r}", f"=SUMIFS({LR('M')},{LR('C')},B{r})", "#,##0.00")
        put(ws, f"J{r}", f"=SUMIFS({LR('G')},{LR('C')},B{r})+SUMIFS({LR('H')},{LR('C')},B{r})+SUMIFS({LR('F')},{LR('C')},B{r})", "#,##0")
        put(ws, f"K{r}", f"=SUMIFS({LR('L')},{LR('C')},B{r})", "#,##0.00")
        put(ws, f"L{r}", f"=SUMIFS({LR('J')},{LR('C')},B{r})", "#,##0.00")
        put(ws, f"M{r}", f"=H{r}/E{r}", "#,##0.0")
        put(ws, f"N{r}", f"=M{r}*30", "#,##0")
    MT = M1 + 1
    put(ws, f"A{MT}", "Total", bold=True, fill=TOT)
    for col in "BCDF":
        put(ws, f"{col}{MT}", None, fill=TOT)
    put(ws, f"E{MT}", f"=SUM(E{M0}:E{M1})", "0", bold=True, fill=TOT, align="center")
    put(ws, f"G{MT}", f"=SUM(G{M0}:G{M1})", "0", bold=True, fill=TOT, align="center")
    for col, fmt in zip("HIJKL", ["#,##0", "#,##0.00", "#,##0", "#,##0.00", "#,##0.00"]):
        put(ws, f"{col}{MT}", f"=SUM({col}{M0}:{col}{M1})", fmt, bold=True, fill=TOT)
    put(ws, f"M{MT}", f"=H{MT}/E{MT}", "#,##0.0", bold=True, fill=TOT)
    put(ws, f"N{MT}", f"=M{MT}*30", "#,##0", bold=True, fill=TOT)
    widths(ws, {"A": 10, "B": 10, "C": 13, "D": 10, "E": 10, "F": 11, "G": 11, "H": 15, "I": 14,
                "J": 15, "K": 11, "L": 11, "M": 10, "N": 12})
    ws.freeze_panes = "A5"
    MR = lambda c: f"Monthly!${c}${M0}:${c}${M1}"

    # ------------------------------------------------ Cycles
    ws = ws_cyc
    title(ws, "Recharge Cycles & Burn Rate",
          "Burn rate = recharge / days until the next recharge (assumes balance near zero at each top-up).")
    header(ws, 4, ["Cycle #", "Recharge date", "Month #", "Season", "Recharge amount (Tk)", "Next recharge date",
                   "Days to next", "Included? (1 = yes)", "Burn rate (Tk/day)", "Tk per 30 days", "Label"])
    C0 = 5
    C1 = C0 + len(cycles) - 1
    for i, c in enumerate(cycles):
        r = C0 + i
        put(ws, f"A{r}", i + 1, align="center")
        put(ws, f"B{r}", D(c["date"]), "dd-mmm-yy", color=BLUE, align="center")
        put(ws, f"C{r}", f"=MONTH(B{r})", align="center")
        put(ws, f"D{r}", f"=INDEX({SEAS},MATCH(C{r},{MNUM},0))", align="center")
        put(ws, f"E{r}", f"=SUMIFS({LR('E')},{LR('B')},B{r})", "#,##0")
        put(ws, f"F{r}", f"=B{r + 1}" if r < C1 else "=Assumptions!$B$5", "dd-mmm-yy", align="center")
        put(ws, f"G{r}", f"=F{r}-B{r}", "0", align="center")
        put(ws, f"H{r}", 1 if c["closed"] else 0, "0", color=BLUE, align="center")
        put(ws, f"I{r}", f'=IF(H{r}=1,E{r}/G{r},"")', "#,##0.0")
        put(ws, f"J{r}", f'=IF(H{r}=1,I{r}*30,"")', "#,##0")
        put(ws, f"K{r}", f'=TEXT(B{r},"dd-mmm")', align="center")
    put(ws, f"A{C1 + 2}", "The latest cycle is excluded: its balance may still be unspent at the ledger end date.",
        border=False, italic=True, color="595959")
    widths(ws, {"A": 9, "B": 14, "C": 9, "D": 10, "E": 16, "F": 16, "G": 11, "H": 12, "I": 14, "J": 13, "K": 10})
    ws.freeze_panes = "A5"
    CR = lambda c: f"Cycles!${c}${C0}:${c}${C1}"

    # ------------------------------------------------ Seasonal
    ws = ws_sea
    title(ws, "Seasonal Analysis (Bangladesh: Winter / Summer / Rainy)",
          "Season boundaries come from the Assumptions sheet. Spending (Tk) is the consumption proxy.")
    put(ws, "A3", "A. All recorded data (calendar-month method, includes partial months)", bold=True, border=False, size=11)
    header(ws, 4, ["Season", "Calendar months", "Months of data", "Days covered", "# Recharges",
                   "Total Recharge (Tk)", "Energy Amt. (Tk)", "Demand + Rent (Tk)", "Share of total",
                   "Tk per day", "Tk per 30 days"])
    for i, s in enumerate(SEASON_ORDER):
        r = 5 + i
        put(ws, f"A{r}", s, bold=True)
        put(ws, f"B{r}", SEASON_MONTHS_LABEL[s], color=BLUE)
        put(ws, f"C{r}", f"=COUNTIF({MR('D')},A{r})", "0", align="center")
        put(ws, f"D{r}", f"=SUMIFS({MR('E')},{MR('D')},A{r})", "0", align="center")
        put(ws, f"E{r}", f"=SUMIFS({MR('G')},{MR('D')},A{r})", "0", align="center")
        put(ws, f"F{r}", f"=SUMIFS({MR('H')},{MR('D')},A{r})", "#,##0")
        put(ws, f"G{r}", f"=SUMIFS({MR('I')},{MR('D')},A{r})", "#,##0.00")
        put(ws, f"H{r}", f"=SUMIFS({MR('J')},{MR('D')},A{r})", "#,##0")
        put(ws, f"I{r}", f"=IFERROR(F{r}/$F$8,0)", "0.0%")
        put(ws, f"J{r}", f'=IFERROR(F{r}/D{r},"")', "#,##0.0")
        put(ws, f"K{r}", f'=IFERROR(J{r}*30,"")', "#,##0")
    put(ws, "A8", "Total", bold=True, fill=TOT)
    put(ws, "B8", None, fill=TOT)
    for col, fmt in zip("CDEFGH", ["0", "0", "0", "#,##0", "#,##0.00", "#,##0"]):
        put(ws, f"{col}8", f"=SUM({col}5:{col}7)", fmt, bold=True, fill=TOT, align="center" if col in "CDE" else None)
    put(ws, "I8", "=SUM(I5:I7)", "0.0%", bold=True, fill=TOT)
    put(ws, "J8", '=IFERROR(F8/D8,"")', "#,##0.0", bold=True, fill=TOT)
    put(ws, "K8", '=IFERROR(J8*30,"")', "#,##0", bold=True, fill=TOT)

    put(ws, "A10", "B. Like-for-like comparison (complete months only)", bold=True, border=False, size=11)
    header(ws, 11, ["Season", "Days", "Total Recharge (Tk)", "Tk per day", "Tk per 30 days", "Index vs lightest (x)"])
    for i, s in enumerate(SEASON_ORDER):
        r = 12 + i
        put(ws, f"A{r}", s, bold=True)
        put(ws, f"B{r}", f'=SUMIFS({MR("E")},{MR("D")},A{r},{MR("F")},"Yes")', "0", align="center")
        put(ws, f"C{r}", f'=SUMIFS({MR("H")},{MR("D")},A{r},{MR("F")},"Yes")', "#,##0")
        put(ws, f"D{r}", f'=IFERROR(C{r}/B{r},"")', "#,##0.0")
        put(ws, f"E{r}", f'=IFERROR(D{r}*30,"")', "#,##0")
        put(ws, f"F{r}", f'=IFERROR(D{r}/MIN($D$12:$D$14),"")', '0.00"x"')

    put(ws, "A16", "C. Cross-check: burn-rate method (recharge cycles; season = month the cycle starts)", bold=True, border=False, size=11)
    header(ws, 17, ["Season", "Cycles", "Days", "Recharge (Tk)", "Tk per day", "Tk per 30 days", "Index vs lightest (x)"])
    for i, s in enumerate(SEASON_ORDER):
        r = 18 + i
        put(ws, f"A{r}", s, bold=True)
        put(ws, f"B{r}", f"=COUNTIFS({CR('D')},A{r},{CR('H')},1)", "0", align="center")
        put(ws, f"C{r}", f"=SUMIFS({CR('G')},{CR('D')},A{r},{CR('H')},1)", "0", align="center")
        put(ws, f"D{r}", f"=SUMIFS({CR('E')},{CR('D')},A{r},{CR('H')},1)", "#,##0")
        put(ws, f"E{r}", f'=IFERROR(D{r}/C{r},"")', "#,##0.0")
        put(ws, f"F{r}", f'=IFERROR(E{r}*30,"")', "#,##0")
        put(ws, f"G{r}", f'=IFERROR(E{r}/MIN($E$18:$E$20),"")', '0.00"x"')

    put(ws, "A22", "D. Extremes (single recharge cycles)", bold=True, border=False, size=11)
    put(ws, "A23", "Item", bold=True, color="FFFFFF", fill=HDR)
    put(ws, "B23", "Value", bold=True, color="FFFFFF", fill=HDR, align="center")
    ex = [
        ("Highest burn rate (Tk/day)", f"=MAX({CR('I')})", "#,##0.0"),
        ("  ...cycle starting", f"=INDEX({CR('B')},MATCH(B24,{CR('I')},0))", "dd-mmm-yy"),
        ("Lowest burn rate (Tk/day)", f"=MIN({CR('I')})", "#,##0.0"),
        ("  ...cycle starting", f"=INDEX({CR('B')},MATCH(B26,{CR('I')},0))", "dd-mmm-yy"),
    ]
    for i, (lab, f, fmt) in enumerate(ex):
        put(ws, f"A{24 + i}", lab)
        put(ws, f"B{24 + i}", f, fmt, align="center")
    widths(ws, {"A": 30, "B": 22, "C": 16, "D": 13, "E": 13, "F": 17, "G": 18, "H": 16, "I": 12, "J": 11, "K": 13})

    # ------------------------------------------------ Summary
    ws = ws_sum
    title(ws, f"Electricity Usage Report - {utility} Prepaid Meter",
          f"Seasonal consumption analysis for Bangladesh | {k['first']} to {k['data_end']}")
    put(ws, "A4", "Customer details", bold=True, border=False, size=11)
    info = [("Customer name", meta.get("customer_name", "")), ("Customer no.", meta.get("customer_no", "")),
            ("Meter no.", meta.get("meter_no", "")), ("Office", meta.get("office", "")),
            ("Address", meta.get("address", "")), ("Tariff / Type", " / ".join(x for x in (meta.get("tariff"), meta.get("meter_type")) if x)),
            ("Sanctioned load", meta.get("load", ""))]
    for i, (a, b) in enumerate(info):
        r = 5 + i
        put(ws, f"A{r}", a, bold=True, fill=SUB)
        ws.merge_cells(f"B{r}:C{r}")
        put(ws, f"B{r}", b, color=BLUE)
        ws[f"C{r}"].border = BORDER
    put(ws, "E4", "Key figures", bold=True, border=False, size=11)
    kp = [
        ("Total recharged (Tk)", f"=Ledger!E{LT}", "#,##0"),
        ("Number of recharges", f"=COUNTA({LR('B')})", "0"),
        ("Average recharge (Tk)", "=G5/G6", "#,##0"),
        ("Energy cost portion (Tk)", f"=Ledger!M{LT}", "#,##0"),
        ("Energy share of recharge", "=IFERROR(G8/G5,0)", "0.0%"),
        ("Demand charge + meter rent (Tk)", f"=Ledger!F{LT}+Ledger!G{LT}+Ledger!H{LT}", "#,##0"),
        ("VAT paid (Tk)", f"=Ledger!L{LT}", "#,##0"),
        ("Overall burn rate (Tk/day)", f'=IFERROR(SUMIFS({CR("E")},{CR("H")},1)/SUMIFS({CR("G")},{CR("H")},1),"")', "#,##0.0"),
    ]
    for i, (a, f, fmt) in enumerate(kp):
        r = 5 + i
        ws.merge_cells(f"E{r}:F{r}")
        put(ws, f"E{r}", a, bold=True, fill=SUB)
        ws[f"F{r}"].border = BORDER
        put(ws, f"G{r}", f, fmt, align="right")
    put(ws, "A14", "Seasonal snapshot (complete months, like-for-like)", bold=True, border=False, size=11)
    header(ws, 15, ["Season", "Tk per day", "Tk per 30 days", "Index vs lightest", "Burn-rate method Tk/30d"])
    for i, s in enumerate(SEASON_ORDER):
        r = 16 + i
        put(ws, f"A{r}", f"=Seasonal!A{12 + i}", bold=True)
        put(ws, f"B{r}", f"=Seasonal!D{12 + i}", "#,##0.0")
        put(ws, f"C{r}", f"=Seasonal!E{12 + i}", "#,##0")
        put(ws, f"D{r}", f"=Seasonal!F{12 + i}", '0.00"x"')
        put(ws, f"E{r}", f"=Seasonal!F{18 + i}", "#,##0")
    put(ws, "A20", "Key findings (generated when the report was created)", bold=True, border=False, size=11)
    for i, text in enumerate(analysis["findings"]):
        r = 21 + i
        ws.merge_cells(f"A{r}:I{r}")
        put(ws, f"A{r}", text, border=False, wrap=True)
        ws.row_dimensions[r].height = 30
    r = 21 + len(analysis["findings"]) + 1
    put(ws, f"A{r}", "Limitations: spending (Tk) is a proxy for kWh; opening/closing prepaid balances are unknown. See the Assumptions sheet.",
        border=False, italic=True, color="595959")
    widths(ws, {"A": 22, "B": 14, "C": 16, "D": 16, "E": 24, "F": 14, "G": 14, "H": 10, "I": 10})

    # ------------------------------------------------ Charts
    ws = ws_cht
    title(ws, "Charts")
    c1 = BarChart(); c1.type = "col"; c1.title = "Monthly recharge (Tk)"; c1.y_axis.title = "Taka"
    c1.add_data(Reference(ws_mon, min_col=8, min_row=4, max_row=M1), titles_from_data=True)
    c1.set_categories(Reference(ws_mon, min_col=1, min_row=M0, max_row=M1))
    c1.legend = None; c1.height = 8; c1.width = 16
    ws.add_chart(c1, "A3")
    c2 = BarChart(); c2.type = "col"; c2.title = "Spend per 30 days by season (Tk)"; c2.y_axis.title = "Taka per 30 days"
    c2.add_data(Reference(ws_sea, min_col=5, min_row=11, max_row=14), titles_from_data=True)
    c2.set_categories(Reference(ws_sea, min_col=1, min_row=12, max_row=14))
    c2.legend = None; c2.height = 8; c2.width = 16
    ws.add_chart(c2, "K3")
    last_closed = max((i for i, c in enumerate(cycles) if c["closed"]), default=0)
    c3 = LineChart(); c3.title = "Burn rate per recharge cycle (Tk/day)"; c3.y_axis.title = "Tk per day"
    c3.add_data(Reference(ws_cyc, min_col=9, min_row=4, max_row=C0 + last_closed), titles_from_data=True)
    c3.set_categories(Reference(ws_cyc, min_col=11, min_row=C0, max_row=C0 + last_closed))
    c3.legend = None; c3.height = 8; c3.width = 32
    ws.add_chart(c3, "A21")

    for w in wb.worksheets:
        w.sheet_view.showGridLines = False
    wb.calculation.fullCalcOnLoad = True
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    if os.environ.get("VOLT_RECALC") == "1":
        data = _recalc(data) or data
    return data


def _recalc(data: bytes):
    """Optional: round-trip through LibreOffice so cached values exist for previewers (adds ~3-8 s)."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "in.xlsx")
        out = os.path.join(td, "out")
        os.makedirs(out)
        with open(src, "wb") as f:
            f.write(data)
        try:
            subprocess.run([soffice, "--headless", "--convert-to", "xlsx", "--outdir", out, src],
                           check=True, timeout=60, capture_output=True)
            with open(os.path.join(out, "in.xlsx"), "rb") as f:
                return f.read()
        except Exception:
            return None
