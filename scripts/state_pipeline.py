#!/usr/bin/env python3
"""State spending pipeline: Open FI$Cal monthly spending files -> small JSON summaries.

Open FI$Cal publishes every state expenditure transaction as one CSV per month
(about 1 GB each for recent years). This script streams those files, adds up
the amounts by fiscal year, fund group, department and spending type, and
writes one small JSON file per fiscal year for the website.

Accuracy rules (checked against Open FI$Cal's own "Spending Annual Summary"):
  * Files are discovered by probing their predictable names. The published
    file list (the "pointer" CSV) can lag behind the files actually posted.
  * Each transaction is assigned to the fiscal year and period in its own
    columns (fiscal_year_begin, accounting_period), not to the file it came
    from: a monthly file can hold a few transactions of another fiscal year.
  * Amounts are added up exactly (in thousandths of a dollar) and stored in
    cents, so totals don't drift from rounding.

Subcommands
  plan   Find every monthly file, compare with the last run, and print the
         file-years to read (`years=[...]`, GitHub Actions output format).
         Anything changed -> every file is read again, because a file can hold
         transactions for other fiscal years.
  fetch  Stream every monthly file of one file-year; write a partial result.
  index  Merge partial results into data/state/fy*.json and index.json.

Only the Python standard library is used, so the GitHub Action needs no installs.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

BLOB = "https://adwoutputfilesadlsstore.blob.core.windows.net/transparency/"
POINTER_URL = BLOB + "MonthlySpendingTransactionPointer/MonthlySpendingTransactionPointer.csv"
FILE_URL = BLOB + "MonthlySpendingTransactionFiles/Spending_FY{yy:02d}P{pp:02d}.csv"
SOURCE_PAGE = "https://open.fiscal.ca.gov/download-expenditures.html"
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "state"
SCHEMA = 2
FIRST_FILE_YEAR = 2016  # first fiscal year Open FI$Cal publishes files for
USER_AGENT = "ca-money-pipeline (+https://github.com/kogason2010/ca-money)"

MONTHS = ["July", "August", "September", "October", "November", "December",
          "January", "February", "March", "April", "May", "June"]

csv.field_size_limit(1 << 24)


# ----------------------------------------------------------------- helpers
def log(*args):
    print(*args, file=sys.stderr, flush=True)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fy_label(begin_year: int) -> str:
    """2025 -> '2025-26' (California's fiscal year runs July to June)."""
    return f"{begin_year}-{(begin_year + 1) % 100:02d}"


def period_month(begin_year: int, period: int) -> str:
    """Accounting period 1 is July of the begin year; period 12 is June."""
    year = begin_year if period <= 6 else begin_year + 1
    return f"{MONTHS[period - 1]} {year}"


def state_file_name(begin_year: int) -> str:
    return f"fy{fy_label(begin_year)}.json"


def request(url: str, method: str = "GET", timeout: int = 180):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


FILE_RE = re.compile(r"Spending_FY(\d{2})P(\d{2})\.csv$", re.I)


def read_pointer(text: str | None = None) -> list[dict]:
    """Parse the published list of monthly files (may lag the real files)."""
    if text is None:
        with request(POINTER_URL, timeout=60) as resp:
            text = resp.read().decode("utf-8-sig", errors="replace")
    files = []
    for row in csv.DictReader(io.StringIO(text)):
        name = (row.get("FileName") or "").strip()
        m = FILE_RE.search(name)
        if m:
            files.append({"file": name, "fileYear": 2000 + int(m.group(1)), "period": int(m.group(2))})
    return files


def head(url: str) -> dict | None:
    for attempt in range(3):
        try:
            with request(url, method="HEAD", timeout=30) as resp:
                return {"lastModified": resp.headers.get("Last-Modified", ""),
                        "size": int(resp.headers.get("Content-Length") or 0)}
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            time.sleep(2 + attempt * 3)
        except Exception:
            time.sleep(2 + attempt * 3)
    raise RuntimeError(f"could not check {url}")


def discover_files(pointer: list[dict] | None = None, probe=head) -> list[dict]:
    """Every monthly file that exists: listed ones plus any found by name."""
    pointer = pointer if pointer is not None else read_pointer()
    last_year = max([f["fileYear"] for f in pointer] + [datetime.now(timezone.utc).year])
    candidates = [(y, p) for y in range(FIRST_FILE_YEAR, last_year + 2) for p in range(1, 13)]

    def check(yp):
        y, p = yp
        url = FILE_URL.format(yy=y % 100, pp=p)
        info = probe(url)
        return None if info is None else {"file": url.rsplit("/", 1)[1], "fileYear": y, "period": p, "url": url, **info}

    with ThreadPoolExecutor(max_workers=8) as pool:
        found = [f for f in pool.map(check, candidates) if f]
    listed = {f["file"] for f in pointer}
    unlisted = [f["file"] for f in found if f["file"] not in listed]
    missing = sorted(listed - {f["file"] for f in found})
    if unlisted:
        log(f"note: {len(unlisted)} files exist but are not in the published list: {unlisted}")
    if missing:
        log(f"warning: listed but not found: {missing}")
    return sorted(found, key=lambda f: (f["fileYear"], f["period"]))


def read_index() -> dict:
    path = DATA_DIR / "index.json"
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


# -------------------------------------------------------------------- plan
def plan_years(files: list[dict], index: dict, full: bool) -> list[int]:
    """All file-years when anything changed (or on a full run), else none."""
    current = {f["file"]: [f["lastModified"], f["size"]] for f in files}
    previous = index.get("sourceFiles") or {}
    changed = full or index.get("schema") != SCHEMA or current != previous
    return sorted({f["fileYear"] for f in files}) if changed else []


def cmd_plan(args):
    files = discover_files()
    years = plan_years(files, read_index(), args.full)
    log(f"{len(files)} monthly files found; file-years to read: {years or 'none (no changes)'}")
    print(f"years={json.dumps(years)}")


# ------------------------------------------------------------------- fetch
NEEDED = ["business_unit", "agency_name", "department_name", "account",
          "account_type_1", "fund_code", "fund_group_1", "fund_description_1",
          "fiscal_year_begin", "accounting_period", "monetary_amount"]


def to_milli(text: str) -> int:
    """'-606.770' -> -606770 (thousandths of a dollar, exact integer sums)."""
    text = text.strip()
    if not text:
        return 0
    return int(round(float(text) * 1000))


def new_bucket():
    return {"flows": defaultdict(int), "funds": defaultdict(int), "names": {},
            "periods": defaultdict(int), "rows": 0}


def aggregate_rows(lines, file_year: int | None = None) -> dict:
    """Aggregate one monthly CSV (an iterable of text lines), by each row's fiscal year."""
    reader = csv.reader(lines)
    header = [h.strip().lower() for h in next(reader)]
    missing = [c for c in NEEDED if c not in header]
    if missing:
        raise ValueError(f"missing columns {missing}; header was {header}")
    ix = {c: header.index(c) for c in NEEDED}
    i_bu, i_ag, i_dep = ix["business_unit"], ix["agency_name"], ix["department_name"]
    i_acct, i_type = ix["account"], ix["account_type_1"]
    i_fcode, i_fgrp, i_fdesc = ix["fund_code"], ix["fund_group_1"], ix["fund_description_1"]
    i_fy, i_per, i_amt = ix["fiscal_year_begin"], ix["accounting_period"], ix["monetary_amount"]
    width = len(header)

    years: dict[int, dict] = {}
    rows = bad = non_exp = other_year = 0
    for r in reader:
        if len(r) < width:
            if r:
                bad += 1
            continue
        try:
            amt = to_milli(r[i_amt])
            fy = int(r[i_fy])
            period = int(r[i_per] or 0)
        except ValueError:
            bad += 1
            continue
        rows += 1
        if file_year is not None and fy != file_year:
            other_year += 1
        if not r[i_acct].startswith("5"):
            non_exp += 1  # the files should only hold expenditure accounts (5xxxxxx)
        b = years.get(fy)
        if b is None:
            b = years[fy] = new_bucket()
        bu = r[i_bu].strip()
        fgrp = r[i_fgrp].strip()
        b["flows"][(fgrp, bu, r[i_type].strip())] += amt
        b["funds"][(fgrp, r[i_fcode].strip(), r[i_fdesc].strip())] += amt
        b["names"][bu] = (r[i_dep].strip(), r[i_ag].strip())  # the latest name wins
        b["periods"][period] += 1
        b["rows"] += 1
    return {"years": years, "rows": rows, "bad": bad, "nonExpense": non_exp, "otherYear": other_year}


def merge_into(target: dict, part: dict):
    """Add one aggregate's per-year buckets into target (same structure)."""
    for fy, b in part["years"].items():
        t = target.setdefault(fy, new_bucket())
        for k, v in b["flows"].items():
            t["flows"][k] += v
        for k, v in b["funds"].items():
            t["funds"][k] += v
        t["names"].update(b["names"])
        for k, v in b["periods"].items():
            t["periods"][k] += v
        t["rows"] += b["rows"]


def fetch_file(f: dict, attempts: int = 3) -> dict:
    for attempt in range(1, attempts + 1):
        started = time.time()
        try:
            with request(f["url"]) as resp:
                last_modified = resp.headers.get("Last-Modified", "")
                text = io.TextIOWrapper(resp, encoding="utf-8-sig", errors="replace", newline="")
                agg = aggregate_rows(text, f["fileYear"])
            agg["lastModified"] = last_modified
            log(f"  {f['file']}: {agg['rows']:,} rows in {time.time() - started:.0f}s"
                f" (skipped {agg['bad']}, non-expense {agg['nonExpense']},"
                f" other fiscal years {agg['otherYear']})")
            return agg
        except Exception as exc:
            log(f"  {f['file']}: attempt {attempt} failed: {exc}")
            if attempt == attempts:
                raise
            time.sleep(15 * attempt)
    raise RuntimeError("unreachable")


def part_to_json(file_year: int, files: list[dict], merged: dict, stats: dict) -> dict:
    out = {"fileYear": file_year, "files": files, "stats": stats, "years": {}}
    for fy, b in merged.items():
        out["years"][str(fy)] = {
            "flows": [[*k, v] for k, v in b["flows"].items()],
            "funds": [[*k, v] for k, v in b["funds"].items()],
            "names": b["names"],
            "periods": {str(k): v for k, v in b["periods"].items()},
            "rows": b["rows"],
        }
    return out


def part_from_json(doc: dict) -> dict:
    years = {}
    for fy, b in doc["years"].items():
        nb = new_bucket()
        for *k, v in b["flows"]:
            nb["flows"][tuple(k)] += v
        for *k, v in b["funds"]:
            nb["funds"][tuple(k)] += v
        nb["names"].update({bu: tuple(x) for bu, x in b["names"].items()})
        for k, v in b["periods"].items():
            nb["periods"][int(k)] += v
        nb["rows"] = b["rows"]
        years[int(fy)] = nb
    return {"years": years}


def cmd_fetch(args):
    files = [f for f in discover_files() if f["fileYear"] == args.year]
    if not files:
        sys.exit(f"no files found for file-year {args.year}")
    log(f"file-year {fy_label(args.year)}: {len(files)} monthly files")
    merged: dict[int, dict] = {}
    stats = {"rows": 0, "skipped": 0, "nonExpense": 0, "otherYear": 0}
    sources = []
    for f in files:
        agg = fetch_file(f)
        merge_into(merged, agg)
        for k, src in (("rows", "rows"), ("skipped", "bad"), ("nonExpense", "nonExpense"), ("otherYear", "otherYear")):
            stats[k] += agg[src]
        sources.append({"file": f["file"], "fileYear": f["fileYear"], "period": f["period"],
                        "lastModified": agg["lastModified"] or f["lastModified"], "size": f["size"],
                        "rows": agg["rows"]})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"part-{args.year}.json"
    path.write_text(json.dumps(part_to_json(args.year, sources, merged, stats), separators=(",", ":"), ensure_ascii=False))
    log(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB): {stats}")


# ------------------------------------------------------------------- build
FUND_MISSING = "Fund group not recorded"
TYPE_MISSING = "Spending type not recorded"


def build_summary(fy: int, b: dict, sources: list[dict]) -> dict:
    names = b["names"]
    # Dictionaries, sorted so the output is stable from run to run.
    fund_names = sorted({k[0] or FUND_MISSING for k in b["flows"]})
    dept_bus = sorted({k[1] for k in b["flows"]})
    agency_names = sorted({names.get(bu, ("", ""))[1] or "Agency not recorded" for bu in dept_bus})
    type_names = sorted({k[2] or TYPE_MISSING for k in b["flows"]})
    f_ix = {n: i for i, n in enumerate(fund_names)}
    a_ix = {n: i for i, n in enumerate(agency_names)}
    d_ix = {bu: i for i, bu in enumerate(dept_bus)}
    t_ix = {n: i for i, n in enumerate(type_names)}
    cents = lambda milli: int(round(milli / 10))  # noqa: E731

    flows = []
    for (fg, bu, typ), milli in b["flows"].items():
        c = cents(milli)
        if c:
            flows.append([f_ix[fg or FUND_MISSING], d_ix[bu], t_ix[typ or TYPE_MISSING], c])
    flows.sort()
    fund_detail = []
    for (fg, code, desc), milli in b["funds"].items():
        c = cents(milli)
        if c:
            fund_detail.append([f_ix[fg or FUND_MISSING], code, desc, c])
    fund_detail.sort(key=lambda r: (r[0], -r[3], r[1]))

    periods = sorted(p for p in b["periods"] if 1 <= p <= 12)
    net_cents = cents(sum(b["flows"].values()))
    departments = []
    for bu in dept_bus:
        dep, ag = names.get(bu, ("", ""))
        departments.append([bu, dep or f"Business unit {bu}", a_ix[ag or "Agency not recorded"]])
    return {
        "schema": SCHEMA,
        "unit": "cents",
        "fiscalYear": fy_label(fy),
        "beginYear": fy,
        "periods": periods,
        "complete": periods == list(range(1, 13)),
        "through": period_month(fy, max(periods)) if periods else None,
        "generatedAt": now_iso(),
        "source": {
            "name": "Open FI$Cal — Monthly Spending Transaction Files",
            "publisher": "California Department of FI$Cal",
            "url": SOURCE_PAGE,
            "filesUpdated": max((s["lastModified"] for s in sources), key=_http_date, default=""),
            "files": [s["file"] for s in sources],
        },
        "totals": {"netCents": net_cents, "net": round(net_cents / 100), "rows": b["rows"]},
        "funds": fund_names,
        "agencies": agency_names,
        "departments": departments,
        "spendTypes": type_names,
        "flows": flows,
        "fundDetail": fund_detail,
    }


def _http_date(text: str) -> float:
    try:
        return datetime.strptime(text, "%a, %d %b %Y %H:%M:%S GMT").timestamp()
    except (TypeError, ValueError):
        return 0.0


def build_index(summaries: dict[int, dict], sources: list[dict], stats: dict) -> dict:
    years = []
    for y in sorted(summaries, reverse=True):
        d = summaries[y]
        years.append({
            "fiscalYear": d["fiscalYear"], "beginYear": y, "file": state_file_name(y),
            "complete": d["complete"], "periods": d["periods"], "through": d["through"],
            "net": d["totals"]["net"], "departments": len(d["departments"]),
        })
    complete = [y for y in years if y["complete"]]
    return {
        "schema": SCHEMA,
        "generatedAt": now_iso(),
        "default": (complete[0] if complete else years[0])["fiscalYear"] if years else None,
        "years": years,
        "source": {"name": "Open FI$Cal", "url": SOURCE_PAGE,
                   "filesUpdated": max((s["lastModified"] for s in sources), key=_http_date, default="")},
        "stats": stats,
        "sourceFiles": {s["file"]: [s["lastModified"], s["size"]] for s in sources},
    }


def cmd_index(args):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    incoming = Path(args.incoming) if args.incoming else None
    parts = sorted(incoming.rglob("part-*.json")) if incoming and incoming.is_dir() else []
    expected = set(json.loads(args.expected)) if args.expected else set()
    got = {int(p.stem.split("-")[1]) for p in parts}
    if not parts:
        log("no new state data; keeping existing files")
        return
    if expected and got != expected:
        # A file can hold other years' transactions, so a partial set of
        # file-years would give wrong totals. Keep the previous data instead.
        log(f"warning: expected file-years {sorted(expected)}, got {sorted(got)}; keeping existing files")
        return
    merged: dict[int, dict] = {}
    sources, stats = [], defaultdict(int)
    for p in parts:
        doc = json.loads(p.read_text())
        merge_into(merged, part_from_json(doc))
        sources.extend(doc["files"])
        for k, v in doc["stats"].items():
            stats[k] += v
    summaries = {}
    for fy, b in sorted(merged.items()):
        if fy < 2000 or b["rows"] == 0:
            log(f"warning: ignoring {b['rows']} rows with fiscal year {fy}")
            continue
        summaries[fy] = build_summary(fy, b, [s for s in sources])
        path = DATA_DIR / state_file_name(fy)
        path.write_text(json.dumps(summaries[fy], separators=(",", ":"), ensure_ascii=False))
        log(f"{path.name}: {b['rows']:,} rows, periods {summaries[fy]['periods']},"
            f" net ${summaries[fy]['totals']['netCents'] / 100:,.2f}")
    for old in DATA_DIR.glob("fy*.json"):
        if int(old.stem[2:6]) not in summaries:
            old.unlink()
    index = build_index(summaries, sources, dict(stats))
    (DATA_DIR / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False))
    log(f"index lists {len(index['years'])} fiscal years; default {index['default']}; stats {dict(stats)}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("plan")
    sp.add_argument("--full", action="store_true", help="read every file even if nothing changed")
    sp.set_defaults(func=cmd_plan)
    sf = sub.add_parser("fetch")
    sf.add_argument("--year", type=int, required=True, help="file-year (begin year), e.g. 2024 for FY24 files")
    sf.add_argument("--out", default="out/state")
    sf.set_defaults(func=cmd_fetch)
    si = sub.add_parser("index")
    si.add_argument("--incoming", help="folder with part-*.json files from fetch")
    si.add_argument("--expected", default=os.environ.get("EXPECTED_YEARS", ""), help="JSON list of file-years planned")
    si.set_defaults(func=cmd_index)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
