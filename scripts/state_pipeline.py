#!/usr/bin/env python3
"""State spending pipeline: Open FI$Cal monthly spending files -> small JSON summaries.

Open FI$Cal publishes every state expenditure transaction as one CSV per month
(about 1 GB each for recent years). This script streams those files, adds up the
amounts by fund group, department and spending type, and writes one small JSON
file per fiscal year for the website.

Subcommands
  plan   Print the fiscal years that need (re)processing, as `years=[...]`
         (GitHub Actions output format).
  fetch  Stream every monthly file for one fiscal year and write its summary.
  index  Move freshly fetched summaries into data/state and rebuild index.json.

Only the Python standard library is used, so the GitHub Action needs no installs.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shutil
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

POINTER_URL = (
    "https://adwoutputfilesadlsstore.blob.core.windows.net/transparency/"
    "MonthlySpendingTransactionPointer/MonthlySpendingTransactionPointer.csv"
)
SOURCE_PAGE = "https://open.fiscal.ca.gov/download-expenditures.html"
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "state"
USER_AGENT = "ca-money-pipeline (+https://github.com/kogason2010/ca-money)"

# Recent fiscal years keep receiving adjustment transactions for months after
# they close, so the newest two are always re-read. Older years are only read
# when they are missing or incomplete (or on a full rebuild).
ALWAYS_REFRESH_LATEST = 2

MONTHS = ["July", "August", "September", "October", "November", "December",
          "January", "February", "March", "April", "May", "June"]

csv.field_size_limit(1 << 24)


# ----------------------------------------------------------------- helpers
def log(*args):
    print(*args, file=sys.stderr, flush=True)


def fy_label(begin_year: int) -> str:
    """2025 -> '2025-26' (California's fiscal year runs July to June)."""
    return f"{begin_year}-{(begin_year + 1) % 100:02d}"


def period_month(begin_year: int, period: int) -> str:
    """Accounting period 1 is July of the begin year; period 12 is June."""
    year = begin_year if period <= 6 else begin_year + 1
    return f"{MONTHS[period - 1]} {year}"


def state_file_name(begin_year: int) -> str:
    return f"fy{fy_label(begin_year)}.json"


def open_url(url: str, timeout: int = 180):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


FILE_RE = re.compile(r"Spending_FY(\d{2})P(\d{2})\.csv$", re.I)


def read_pointer(text: str | None = None) -> list[dict]:
    """Parse the pointer CSV that lists every monthly file."""
    if text is None:
        with open_url(POINTER_URL, timeout=60) as resp:
            text = resp.read().decode("utf-8-sig", errors="replace")
    files = []
    for row in csv.DictReader(io.StringIO(text)):
        name = (row.get("FileName") or "").strip()
        m = FILE_RE.search(name)
        url = (row.get("Download") or "").strip()
        if not m or not url.startswith("https://"):
            continue
        files.append({
            "file": name,
            "beginYear": 2000 + int(m.group(1)),
            "period": int(m.group(2)),
            "url": url,
            "uploadDate": (row.get("UploadDate") or "").strip(),
            "size": (row.get("FileSize") or "").strip(),
        })
    files.sort(key=lambda f: (f["beginYear"], f["period"]))
    return files


def existing_summaries() -> dict[int, dict]:
    out = {}
    for path in sorted(DATA_DIR.glob("fy*.json")):
        try:
            doc = json.loads(path.read_text())
            out[int(doc["beginYear"])] = doc
        except Exception as exc:  # a broken file is simply reprocessed
            log(f"warning: could not read {path.name}: {exc}")
    return out


# -------------------------------------------------------------------- plan
def plan_years(pointer: list[dict], existing: dict[int, dict], full: bool) -> list[int]:
    by_year: dict[int, set[int]] = defaultdict(set)
    for f in pointer:
        by_year[f["beginYear"]].add(f["period"])
    years = sorted(by_year)
    latest = set(years[-ALWAYS_REFRESH_LATEST:])
    todo = []
    for y in years:
        have = existing.get(y)
        if full or y in latest or have is None:
            todo.append(y)
        elif set(have.get("periods", [])) != by_year[y]:
            todo.append(y)
    return todo


def cmd_plan(args):
    pointer = read_pointer()
    years = plan_years(pointer, existing_summaries(), args.full)
    log(f"pointer lists {len(pointer)} files; years to process: {years}")
    print(f"years={json.dumps(years)}")


# ------------------------------------------------------------------- fetch
NEEDED = ["business_unit", "agency_name", "department_name", "account",
          "account_type_1", "fund_code", "fund_group_1", "fund_description_1",
          "monetary_amount"]


def to_milli(text: str) -> int:
    """'-606.770' -> -606770 (thousandths of a dollar, exact integer sums)."""
    text = text.strip()
    if not text:
        return 0
    return int(round(float(text) * 1000))


def aggregate_rows(lines) -> dict:
    """Aggregate one monthly CSV (an iterable of text lines)."""
    reader = csv.reader(lines)
    header = [h.strip().lower() for h in next(reader)]
    missing = [c for c in NEEDED if c not in header]
    if missing:
        raise ValueError(f"missing columns {missing}; header was {header}")
    ix = {c: header.index(c) for c in NEEDED}
    i_bu, i_ag, i_dep = ix["business_unit"], ix["agency_name"], ix["department_name"]
    i_acct, i_type = ix["account"], ix["account_type_1"]
    i_fcode, i_fgrp, i_fdesc = ix["fund_code"], ix["fund_group_1"], ix["fund_description_1"]
    i_amt = ix["monetary_amount"]
    width = len(header)

    flows: dict[tuple, int] = defaultdict(int)
    funds: dict[tuple, int] = defaultdict(int)
    rows = bad = non_exp = 0
    for r in reader:
        if len(r) < width:
            if not r:
                continue
            bad += 1
            continue
        try:
            amt = to_milli(r[i_amt])
        except ValueError:
            bad += 1
            continue
        rows += 1
        if not r[i_acct].startswith("5"):
            non_exp += 1  # the files should only hold expenditure accounts (5xxxxxx)
        fgrp = r[i_fgrp].strip() or "Unspecified fund"
        flows[(fgrp, r[i_ag].strip() or "Unspecified agency", r[i_bu].strip(),
               r[i_dep].strip() or "Unspecified department",
               r[i_type].strip() or "Unspecified")] += amt
        funds[(fgrp, r[i_fcode].strip(), r[i_fdesc].strip())] += amt
    return {"flows": flows, "funds": funds, "rows": rows, "bad": bad, "nonExpense": non_exp}


def fetch_file(f: dict, attempts: int = 3) -> dict:
    for attempt in range(1, attempts + 1):
        started = time.time()
        try:
            with open_url(f["url"]) as resp:
                last_modified = resp.headers.get("Last-Modified", "")
                text = io.TextIOWrapper(resp, encoding="utf-8-sig", errors="replace", newline="")
                agg = aggregate_rows(text)
            agg["lastModified"] = last_modified
            log(f"  {f['file']}: {agg['rows']:,} rows in {time.time() - started:.0f}s"
                f" (skipped {agg['bad']}, non-expense accounts {agg['nonExpense']})")
            return agg
        except Exception as exc:
            log(f"  {f['file']}: attempt {attempt} failed: {exc}")
            if attempt == attempts:
                raise
            time.sleep(15 * attempt)
    raise RuntimeError("unreachable")


def build_summary(begin_year: int, per_file: list[tuple[dict, dict]]) -> dict:
    flows: dict[tuple, int] = defaultdict(int)
    funds: dict[tuple, int] = defaultdict(int)
    rows = 0
    sources = []
    for f, agg in per_file:
        for k, v in agg["flows"].items():
            flows[k] += v
        for k, v in agg["funds"].items():
            funds[k] += v
        rows += agg["rows"]
        sources.append({"file": f["file"], "period": f["period"],
                        "lastModified": agg.get("lastModified", ""), "rows": agg["rows"]})

    # Dictionaries, sorted so the output is stable from run to run.
    fund_names = sorted({k[0] for k in flows})
    agency_names = sorted({k[1] for k in flows})
    dept_keys = sorted({(k[2], k[3], k[1]) for k in flows})
    type_names = sorted({k[4] for k in flows})
    f_ix = {n: i for i, n in enumerate(fund_names)}
    a_ix = {n: i for i, n in enumerate(agency_names)}
    d_ix = {k: i for i, k in enumerate(dept_keys)}
    t_ix = {n: i for i, n in enumerate(type_names)}

    flow_rows = []
    for (fg, ag, bu, dep, typ), milli in flows.items():
        dollars = round(milli / 1000)
        if dollars:
            flow_rows.append([f_ix[fg], d_ix[(bu, dep, ag)], t_ix[typ], dollars])
    flow_rows.sort()

    fund_rows = []
    for (fg, code, desc), milli in funds.items():
        dollars = round(milli / 1000)
        if dollars:
            fund_rows.append([f_ix[fg], code, desc, dollars])
    fund_rows.sort(key=lambda r: (r[0], -r[3], r[1]))

    periods = sorted(f["period"] for f, _ in per_file)
    net = round(sum(flows.values()) / 1000)
    return {
        "schema": 1,
        "fiscalYear": fy_label(begin_year),
        "beginYear": begin_year,
        "periods": periods,
        "complete": periods == list(range(1, 13)),
        "through": period_month(begin_year, max(periods)) if periods else None,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {
            "name": "Open FI$Cal — Monthly Spending Transaction Files",
            "publisher": "California Department of FI$Cal",
            "url": SOURCE_PAGE,
            "files": sources,
        },
        "totals": {"net": net, "rows": rows},
        "funds": fund_names,
        "agencies": agency_names,
        "departments": [[bu, dep, a_ix[ag]] for (bu, dep, ag) in dept_keys],
        "spendTypes": type_names,
        "flows": flow_rows,
        "fundDetail": fund_rows,
    }


def cmd_fetch(args):
    pointer = read_pointer()
    files = [f for f in pointer if f["beginYear"] == args.year]
    if not files:
        sys.exit(f"no files listed for fiscal year beginning {args.year}")
    log(f"fiscal year {fy_label(args.year)}: {len(files)} monthly files")
    per_file = [(f, fetch_file(f)) for f in files]
    doc = build_summary(args.year, per_file)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / state_file_name(args.year)
    path.write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False))
    log(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB, net ${doc['totals']['net']:,})")


# ------------------------------------------------------------------- index
def build_index(summaries: dict[int, dict]) -> dict:
    years = []
    for y in sorted(summaries, reverse=True):
        d = summaries[y]
        years.append({
            "fiscalYear": d["fiscalYear"], "beginYear": y, "file": state_file_name(y),
            "complete": d["complete"], "through": d["through"],
            "net": d["totals"]["net"], "departments": len(d["departments"]),
        })
    complete = [y for y in years if y["complete"]]
    return {
        "schema": 1,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "default": (complete[0] if complete else years[0])["fiscalYear"] if years else None,
        "years": years,
        "source": {"name": "Open FI$Cal", "url": SOURCE_PAGE},
    }


def cmd_index(args):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    incoming = Path(args.incoming) if args.incoming else None
    if incoming and incoming.is_dir():
        for path in sorted(incoming.rglob("fy*.json")):
            doc = json.loads(path.read_text())  # validate before replacing
            assert doc.get("schema") == 1 and doc.get("flows"), f"bad summary {path}"
            shutil.copyfile(path, DATA_DIR / path.name)
            log(f"updated {path.name}")
    summaries = existing_summaries()
    index = build_index(summaries)
    (DATA_DIR / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False))
    log(f"index lists {len(index['years'])} fiscal years; default {index['default']}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("plan")
    sp.add_argument("--full", action="store_true", help="reprocess every fiscal year")
    sp.set_defaults(func=cmd_plan)
    sf = sub.add_parser("fetch")
    sf.add_argument("--year", type=int, required=True, help="fiscal year begin, e.g. 2024 for 2024-25")
    sf.add_argument("--out", default=str(DATA_DIR))
    sf.set_defaults(func=cmd_fetch)
    si = sub.add_parser("index")
    si.add_argument("--incoming", help="folder with freshly fetched fy*.json files")
    si.set_defaults(func=cmd_index)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
