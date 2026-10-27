"""Seasonal / monthly / burn-rate analysis for prepaid-meter recharge records.

Pure functions, no I/O. Seasons follow the common Bangladesh three-season grouping:
Winter Nov-Feb, Summer (pre-monsoon hot) Mar-May, Rainy (monsoon) Jun-Oct.
Spending (Tk) is used as the consumption proxy because ledgers show Taka, not kWh.
"""
from __future__ import annotations

import calendar
from collections import OrderedDict, defaultdict
from datetime import date, timedelta
from typing import Optional

SEASON_ORDER = ["Winter", "Summer", "Rainy"]
DEFAULT_SEASON_MAP = {1: "Winter", 2: "Winter", 3: "Summer", 4: "Summer", 5: "Summer",
                      6: "Rainy", 7: "Rainy", 8: "Rainy", 9: "Rainy", 10: "Rainy",
                      11: "Winter", 12: "Winter"}
SEASON_MONTHS_LABEL = {"Winter": "Nov-Feb", "Summer": "Mar-May", "Rainy": "Jun-Oct"}


def _d(s) -> date:
    return date.fromisoformat(s) if isinstance(s, str) else s


def _eom(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _r(x, n=2):
    return round(float(x), n)


def analyze(meta: dict, records: list, season_map: Optional[dict] = None) -> dict:
    smap = {int(k): v for k, v in (season_map or DEFAULT_SEASON_MAP).items()}
    recs = sorted(records, key=lambda r: r["date"])
    if not recs:
        raise ValueError("no records")
    first, last = _d(recs[0]["date"]), _d(recs[-1]["date"])
    data_end = _d(meta["period_end"]) if meta.get("period_end") else last
    if data_end < last:
        data_end = last
    start = _d(meta["period_start"]) if meta.get("period_start") else first.replace(day=1)
    start = min(start, first)

    # ---- monthly
    by_month = OrderedDict()
    y, m = start.year, start.month
    while (y, m) <= (data_end.year, data_end.month):
        by_month[(y, m)] = dict(recharge=0.0, energy=0.0, fixed=0.0, vat=0.0, rebate=0.0, count=0)
        m += 1
        if m == 13:
            y, m = y + 1, 1
    for r in recs:
        d = _d(r["date"])
        b = by_month[(d.year, d.month)]
        b["recharge"] += r["amount"]
        b["energy"] += r.get("energy", 0)
        b["fixed"] += r.get("demand", 0) + r.get("rent", 0) + r.get("service", 0)
        b["vat"] += r.get("vat", 0)
        b["rebate"] += r.get("rebate", 0)
        b["count"] += 1
    monthly = []
    for (y, m), b in by_month.items():
        ms = max(date(y, m, 1), start)
        me = min(_eom(y, m), data_end)
        days = (me - ms).days + 1
        full = _eom(y, m) <= data_end
        monthly.append(dict(
            key=f"{y}-{m:02d}", label=f"{calendar.month_abbr[m]}-{str(y)[2:]}", year=y, month=m,
            season=smap[m], days=days, full=full, count=b["count"], recharge=_r(b["recharge"]),
            energy=_r(b["energy"]), fixed=_r(b["fixed"]), vat=_r(b["vat"]), rebate=_r(b["rebate"]),
            per_day=_r(b["recharge"] / days, 1) if days else 0,
            per_30=_r(b["recharge"] / days * 30, 0) if days else 0))

    # ---- cycles (same-day recharges merged)
    merged = OrderedDict()
    for r in recs:
        merged[r["date"]] = merged.get(r["date"], 0) + r["amount"]
    dates = list(merged.keys())
    cycles = []
    for i, ds in enumerate(dates):
        d = _d(ds)
        nxt = _d(dates[i + 1]) if i + 1 < len(dates) else data_end
        days = (nxt - d).days
        closed = i + 1 < len(dates) and days > 0
        cycles.append(dict(date=ds, label=d.strftime("%d-%b"), month=d.month, season=smap[d.month],
                           amount=_r(merged[ds]), next=nxt.isoformat(), days=days, closed=closed,
                           per_day=_r(merged[ds] / days, 1) if closed else None,
                           per_30=_r(merged[ds] / days * 30, 0) if closed else None))

    # ---- seasons
    def season_rows(use_cycles: bool):
        out = []
        for s in SEASON_ORDER:
            if use_cycles:
                cs = [c for c in cycles if c["season"] == s and c["closed"]]
                days = sum(c["days"] for c in cs)
                tot = sum(c["amount"] for c in cs)
                n = len(cs)
            else:
                ms = [x for x in monthly if x["season"] == s and x["full"]]
                days = sum(x["days"] for x in ms)
                tot = sum(x["recharge"] for x in ms)
                n = len(ms)
            out.append(dict(season=s, months_label=SEASON_MONTHS_LABEL[s], n=n, days=days, total=_r(tot),
                            per_day=_r(tot / days, 1) if days else None,
                            per_30=_r(tot / days * 30, 0) if days else None))
        vals = [x["per_day"] for x in out if x["per_day"]]
        base = min(vals) if vals else None
        for x in out:
            x["index"] = _r(x["per_day"] / base, 2) if (x["per_day"] and base) else None
        return out

    seasons = season_rows(False)
    seasons_cycle = season_rows(True)
    present = [x for x in seasons if x["per_day"]]
    baseline = min(present, key=lambda x: x["per_day"])["season"] if present else None
    for x in seasons:
        x["share"] = _r(x["total"] / sum(r["amount"] for r in recs) * 100, 1)

    # ---- totals / KPIs
    tot = sum(r["amount"] for r in recs)
    energy = sum(r.get("energy", 0) for r in recs)
    vat = sum(r.get("vat", 0) for r in recs)
    fixed = sum(r.get("demand", 0) + r.get("rent", 0) + r.get("service", 0) for r in recs)
    rebate = sum(r.get("rebate", 0) for r in recs)
    closed = [c for c in cycles if c["closed"]]
    overall_days = sum(c["days"] for c in closed)
    overall_burn = sum(c["amount"] for c in closed) / overall_days if overall_days else None
    kpis = dict(total=_r(tot), n_recharges=len(recs), avg_recharge=_r(tot / len(recs)),
                energy=_r(energy), energy_share=_r(energy / tot * 100, 1) if tot else 0,
                fixed=_r(fixed), vat=_r(vat), rebate=_r(rebate),
                burn_per_day=_r(overall_burn, 1) if overall_burn else None,
                burn_per_30=_r(overall_burn * 30, 0) if overall_burn else None,
                first=first.isoformat(), last=last.isoformat(), data_end=data_end.isoformat(),
                days=(data_end - start).days + 1)

    # ---- extremes
    extremes = {}
    if closed:
        hi = max(closed, key=lambda c: c["per_day"])
        lo = min(closed, key=lambda c: c["per_day"])
        extremes = dict(highest=dict(per_day=hi["per_day"], date=hi["date"], days=hi["days"], amount=hi["amount"]),
                        lowest=dict(per_day=lo["per_day"], date=lo["date"], days=lo["days"], amount=lo["amount"]))
    full_months = [x for x in monthly if x["full"] and x["days"] >= 20]
    if full_months:
        pk = max(full_months, key=lambda x: x["per_day"])
        lw = min(full_months, key=lambda x: x["per_day"])
        extremes["peak_month"] = dict(label=pk["label"], per_30=pk["per_30"], recharge=pk["recharge"])
        extremes["low_month"] = dict(label=lw["label"], per_30=lw["per_30"], recharge=lw["recharge"])

    # ---- warnings and findings
    warnings = list(meta.get("warnings", []))
    missing = [s["season"] for s in seasons if not s["per_day"]]
    if missing:
        warnings.append("No complete-month data for: " + ", ".join(missing) + ". Seasonal comparison is partial.")
    warnings.append("Spending (Tk) is used as a proxy for consumption; ledgers do not show kWh. "
                    "Opening and closing prepaid balances are unknown, so the first and last months may be understated.")
    findings = _findings(seasons, baseline, extremes, kpis, monthly)

    return dict(meta=meta, kpis=kpis, monthly=monthly, seasons=seasons, seasons_cycle=seasons_cycle,
                baseline=baseline, cycles=cycles, extremes=extremes, findings=findings, warnings=warnings,
                records=recs, season_map={str(k): v for k, v in smap.items()})


def _tk(x):
    return f"Tk {x:,.0f}"


def _findings(seasons, baseline, ex, k, monthly):
    out = []
    by = {s["season"]: s for s in seasons if s["per_day"]}
    if len(by) >= 2:
        hi = max(by.values(), key=lambda s: s["per_day"])
        lo = min(by.values(), key=lambda s: s["per_day"])
        out.append(f"{hi['season']} is your heaviest season at about {_tk(hi['per_30'])} per 30 days, "
                   f"{hi['index']:.1f}x your lightest ({lo['season']}, {_tk(lo['per_30'])}).")
        for s in seasons:
            if s["per_day"] and s["season"] not in (hi["season"], lo["season"]):
                out.append(f"{s['season']} averages {_tk(s['per_30'])} per 30 days ({s['index']:.1f}x the lightest season).")
    elif by:
        s = next(iter(by.values()))
        out.append(f"Only {s['season']} has complete data: about {_tk(s['per_30'])} per 30 days.")
    if "highest" in ex:
        h, l = ex["highest"], ex["lowest"]
        out.append(f"Fastest-burning recharge: {_tk(h['amount'])} lasted {h['days']} days from "
                   f"{_d(h['date']).strftime('%d %b')} ({_tk(h['per_day'])}/day). "
                   f"Slowest: {l['days']} days from {_d(l['date']).strftime('%d %b')} ({_tk(l['per_day'])}/day).")
    if "peak_month" in ex and ex["peak_month"]["label"] != ex["low_month"]["label"]:
        out.append(f"Costliest full month: {ex['peak_month']['label']} (~{_tk(ex['peak_month']['per_30'])} per 30 days); "
                   f"cheapest: {ex['low_month']['label']} (~{_tk(ex['low_month']['per_30'])}).")
    out.append(f"Of every Tk 100 recharged, about Tk {k['energy_share']:.0f} is energy cost; the rest is VAT, "
               f"demand charge and meter rent (net of rebate).")
    return out
