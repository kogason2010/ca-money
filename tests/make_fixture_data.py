"""Write SYNTHETIC data files (random numbers, invented department names) so the
site can be exercised offline before the real pipeline has run.

    python tests/make_fixture_data.py /tmp/site/data

Never point this at the repo's own data/ folder.
"""
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import local_pipeline as lp  # noqa: E402

out = Path(sys.argv[1])
assert "ca-money/data" not in str(out.resolve()), "refusing to overwrite real data"
rng = random.Random(7)

# ---------------------------------------------------------------- state
funds = ["Bond Funds", "Federal Funds", "General Fund", "Other NonGovt Cost Funds", "Special Funds"]
agencies = ["Bus Consumer Srvcs & Housing", "Corrections & Rehabilitation", "Education", "Environmental Protection",
            "General Government", "Health and Human Services", "Legislative, Judicial, & Exec", "Natural Resources",
            "Transportation", "Labor & Workforce Development", "Govt Operations"]
types = ["Personal services", "Operating Expense & Equipment", "Local Assistance", "Internal Cost Recovery",
         "Capital Outlay", "Special Items of Expense"]
(out / "state").mkdir(parents=True, exist_ok=True)
years = []
for by in range(2016, 2026):
    n_dept = 40 if by < 2019 else 150
    depts = [[f"{1000 + i}", f"Example Department {i}", rng.randrange(len(agencies))] for i in range(n_dept)]
    flows = []
    for d in range(n_dept):
        scale = math.exp(rng.gauss(18, 1.8))
        for f in range(len(funds)):
            if rng.random() < 0.45:
                for t in range(len(types)):
                    if rng.random() < 0.5:
                        v = scale * rng.random()
                        if types[t] == "Internal Cost Recovery":
                            v = -v * 0.3
                        flows.append([f, d, t, round(v)])
    periods = list(range(1, 13)) if by < 2025 else list(range(1, 12))
    doc = {"schema": 1, "fiscalYear": f"{by}-{(by + 1) % 100:02d}", "beginYear": by, "periods": periods,
           "complete": len(periods) == 12, "through": "June" if len(periods) == 12 else "May 2026",
           "generatedAt": "2026-11-22T00:00:00Z", "source": {"files": []},
           "totals": {"net": sum(f[3] for f in flows), "rows": 123456}, "funds": funds, "agencies": agencies,
           "departments": depts, "spendTypes": types, "flows": flows,
           "fundDetail": [[f, f"{f:04d}", f"Example fund {f}", 1000000] for f in range(len(funds))]}
    name = f"fy{doc['fiscalYear']}.json"
    (out / "state" / name).write_text(json.dumps(doc))
    years.append({"fiscalYear": doc["fiscalYear"], "beginYear": by, "file": name, "complete": doc["complete"],
                  "through": doc["through"], "net": doc["totals"]["net"], "departments": n_dept})
years.sort(key=lambda y: -y["beginYear"])
(out / "state" / "index.json").write_text(json.dumps({"schema": 1, "default": "2024-25", "years": years}))

# ---------------------------------------------------------------- local
(out / "local").mkdir(parents=True, exist_ok=True)
(out / "geo").mkdir(parents=True, exist_ok=True)
counties = ["Los Angeles", "Orange", "San Diego", "Sacramento", "Alameda", "Fresno", "Kern", "Shasta", "Humboldt", "Inyo",
            "San Francisco", "Riverside", "San Bernardino", "Santa Clara", "Placer"]
centers = {"Los Angeles": (-118.2, 34.1), "Orange": (-117.8, 33.7), "San Diego": (-117.0, 32.9), "Sacramento": (-121.4, 38.5),
           "Alameda": (-122.1, 37.7), "Fresno": (-119.8, 36.7), "Kern": (-119.0, 35.4), "Shasta": (-122.3, 40.6),
           "Humboldt": (-124.0, 40.8), "Inyo": (-118.3, 37.0), "San Francisco": (-122.44, 37.77),
           "Riverside": (-116.8, 33.8), "San Bernardino": (-117.2, 34.6), "Santa Clara": (-121.9, 37.3), "Placer": (-121.1, 38.9)}
points = {}
for kind in ("cities", "counties"):
    groups = [g[0] for g in lp.DATASETS[kind]["groups"]]
    lines = [[g, f"Example line for {g}"] for g in groups]
    index_years = []
    for end in range(2016, 2026):
        ents = []
        if kind == "cities":
            names = ["Sacramento", "Pasadena", "Los Angeles", "Industry"] + [f"Example City {i}" for i in range(120)]
            for i, nm in enumerate(names):
                c = counties[i % len(counties)] if i > 3 else ["Sacramento", "Los Angeles", "Los Angeles", "Los Angeles"][i]
                pop = 219 if nm == "Industry" else int(math.exp(rng.gauss(10.5, 1.2)))
                vals = [round(pop * rng.uniform(0, 600)) for _ in groups]
                if rng.random() < 0.3:
                    vals[1] = 0
                ents.append([nm, c, pop, vals, round(pop * 120), round(pop * 40), [[j, v] for j, v in enumerate(vals) if v]])
                cx, cy = centers[c]
                points[nm] = [round(cx + rng.gauss(0, 0.3), 4), round(cy + rng.gauss(0, 0.25), 4), c]
        else:
            topo = json.loads((Path(__file__).resolve().parent.parent / "data/geo/ca-counties.topo.json").read_text())
            for g in topo["objects"]["counties"]["geometries"]:
                nm = g["properties"]["name"]
                pop = int(math.exp(rng.gauss(12, 1.4)))
                vals = [round(pop * rng.uniform(20, 900)) for _ in groups]
                ents.append([nm, 1000, pop, vals, round(pop * 90), round(pop * 30), [[j, v] for j, v in enumerate(vals) if v]])
        fy = f"{end - 1}-{end % 100:02d}"
        doc = {"schema": 1, "kind": kind, "fiscalYear": fy, "sourceYear": end, "generatedAt": "2026-11-22T00:00:00Z",
               "groups": groups, "lines": lines, "entities": ents}
        (out / "local" / f"{kind}-{fy}.json").write_text(json.dumps(doc))
        index_years.append(fy)
    idx_path = out / "local" / "index.json"
    idx = json.loads(idx_path.read_text()) if idx_path.exists() else {"schema": 1}
    idx[kind] = {"years": sorted(index_years, reverse=True),
                 "groups": [{"key": k, "label": label, "lines": ls} for k, label, ls in lp.DATASETS[kind]["groups"]],
                 "source": {"name": "synthetic"}}
    idx_path.write_text(json.dumps(idx))
(out / "geo" / "city-points.json").write_text(json.dumps({"points": points}))
print("synthetic data written to", out)
