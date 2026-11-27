"""Temporary diagnostic: which transactions would move year if fiscal year
came from accounting_date instead of fiscal_year_begin?"""
import csv, io, sys
from collections import defaultdict
sys.path.insert(0, "scripts")
import state_pipeline as sp

year = int(sys.argv[1])
files = [f for f in sp.discover_files() if f["fileYear"] == year]
moved = defaultdict(int); count = defaultdict(int); per = defaultdict(int)
for f in files:
    with sp.request(f["url"]) as resp:
        r = csv.reader(io.TextIOWrapper(resp, encoding="utf-8-sig", errors="replace", newline=""))
        h = next(r); ix = {c: h.index(c) for c in h}
        for row in r:
            if len(row) < len(h): continue
            fy = int(row[ix["fiscal_year_begin"]]); d = row[ix["accounting_date"]]
            try: y, m = int(d[:4]), int(d[5:7])
            except ValueError: continue
            dfy = y if m >= 7 else y - 1
            if dfy != fy:
                k = (fy, dfy, row[ix["agency_name"]])
                moved[k] += sp.to_milli(row[ix["monetary_amount"]]); count[k] += 1
                per[(fy, dfy, row[ix["accounting_period"]], row[ix["account_type_1"]])] += sp.to_milli(row[ix["monetary_amount"]])
    print(f"{f['file']} done", flush=True)
print("== by fiscal_year_begin, date-FY, agency (dollars, rows)")
for k, v in sorted(moved.items(), key=lambda kv: -abs(kv[1]))[:40]:
    print(k, round(v / 1000), count[k])
print("== by fy, date-FY, period, type")
for k, v in sorted(per.items(), key=lambda kv: -abs(kv[1]))[:25]:
    print(k, round(v / 1000))
