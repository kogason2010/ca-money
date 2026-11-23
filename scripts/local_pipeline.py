#!/usr/bin/env python3
"""City and county pipeline: State Controller "By the Numbers" -> small JSON files.

Every California city and county files an annual Financial Transactions Report
with the State Controller's Office (SCO), which publishes it on
bythenumbers.sco.ca.gov. This script asks that site's open-data API for each
year's spending lines (already summed on the server), groups them into
everyday service areas (police, fire, parks...) and writes one file per year.

Subcommands
  all     Cities, counties and city map points (what the GitHub Action runs).
  cities  Only city files.
  counties Only county files.
  geo     Refresh data/geo/city-points.json from CDTFA city boundaries.

Only the Python standard library is used.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "local"
GEO_DIR = ROOT / "data" / "geo"
SODA = "https://bythenumbers.sco.ca.gov/resource/{id}.json"
VIEW = "https://bythenumbers.sco.ca.gov/api/views/{id}.json"
USER_AGENT = "ca-money-pipeline (+https://github.com/kogason2010/ca-money)"
YEARS_TO_KEEP = 10

CITY_POINTS_LAYER = ("https://services6.arcgis.com/snwvZ3EmaoXJiugR/arcgis/rest/services/"
                     "City_and_County_Boundary_Line_Changes/FeatureServer/0/query")
# SCO entity name -> CDTFA boundary name, where the two spell a city differently.
CITY_ALIASES = {
    "Amador": "Amador City",
    "California City": "California",
    "El Paso De Robles": "Paso Robles",
    "La Canada Flintridge": "La Cañada Flintridge",
    "Mt. Shasta": "Mount Shasta",
    "San Buenaventura": "Ventura",
}

# --------------------------------------------------------------- groupings
# Each service area lists the SCO report lines it adds up. Lines are matched on
# the "Subcategory 2" text with the "_Current Expenditures" suffix removed; any
# line not listed falls back to its section (Subcategory 1) default.
CITY_GROUPS = [
    ("police", "Police", ["Police"]),
    ("fire", "Fire & emergency medical", ["Fire", "Emergency Medical Services"]),
    ("safety_other", "Other public safety", []),
    ("streets", "Streets, trees & lighting",
     ["Streets/Highways/Storm Drains", "Streets -Trees/Landscaping", "Street Lighting"]),
    ("transit", "Transit, parking & airports", []),
    ("parks", "Parks, recreation & culture", []),
    ("libraries", "Libraries", ["Libraries"]),
    ("community", "Housing & community development", []),
    ("sanitation", "Sewers & trash", ["Sewers", "Solid Waste"]),
    ("health", "Health", []),
    ("admin", "City administration", []),
    ("other", "Utilities & other", []),
]
CITY_SECTION_DEFAULT = {
    "Public Safety": "safety_other",
    "Transportation": "transit",
    "Culture and Leisure": "parks",
    "Community Development": "community",
    "Health": "health",
    "General Government": "admin",
    "Public Utilities": "other",
    "Other Expenditures": "other",
}
CITY_CATEGORIES = [
    "General Government and Public Safety",
    "Transportation and Community Development",
    "Health and Culture and Leisure",
    "Public Utilities and Other Expenditures",
]

COUNTY_GROUPS = [
    ("sheriff", "Sheriff & police", ["Police Protection"]),
    ("corrections", "Jails & corrections", ["Detention and Correction"]),
    ("courts", "Courts, DA & public defender", ["Judicial"]),
    ("fire", "Fire protection", ["Fire Protection"]),
    ("safety_other", "Other public protection", []),
    ("health", "Health & sanitation", ["Health", "Sanitation"]),
    ("roads", "Roads & public works", ["Public Ways and Facilities"]),
    ("assistance", "Public assistance & social services", []),
    ("recreation", "Libraries, education & recreation", []),
    ("admin", "County administration", []),
]
COUNTY_SECTION_DEFAULT = {  # keyed by Category for counties
    "Public Protection": "safety_other",
    "Public Ways and Facilities, Health, and Sanitation": "health",
    "Public Assistance": "assistance",
    "Education and Recreation and Cultural Services": "recreation",
    "General Government": "admin",
}
COUNTY_CATEGORIES = list(COUNTY_SECTION_DEFAULT)
CAPITAL_CATEGORY = "Debt Service and Capital Outlay"

DATASETS = {
    "cities": {"id": "ju3w-4gxp", "value": "value", "name_extra": ["county"],
               "categories": CITY_CATEGORIES, "groups": CITY_GROUPS,
               "title": "City Financial Transactions Report — Expenditures"},
    "counties": {"id": "uctr-c2j8", "value": "values", "name_extra": ["area_in_sq_miles"],
                 "categories": COUNTY_CATEGORIES, "groups": COUNTY_GROUPS,
                 "title": "County Financial Transactions Report — Expenditures"},
}


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def get_json(url: str, params: dict | None = None, attempts: int = 4):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            log(f"  request failed (attempt {attempt}): {exc}")
            if attempt == attempts:
                raise
            time.sleep(10 * attempt)


def fy_label_from_end(end_year: int) -> str:
    """SCO labels fiscal years by the year they end: 2025 -> '2024-25'."""
    return f"{end_year - 1}-{end_year % 100:02d}"


def line_name(subcategory_2: str) -> str:
    return subcategory_2.replace("_Current Expenditures", "").strip()


def bucket_for(kind: str, category: str, sub1: str, line: str) -> str | None:
    """Which service area a report line belongs to (None = not counted)."""
    if category == CAPITAL_CATEGORY:
        return {"Capital Outlay": "capital", "Debt Service": "debt"}.get(sub1)
    groups = DATASETS[kind]["groups"]
    if kind == "cities":
        if category not in CITY_CATEGORIES:
            return None
        for key, _label, lines in groups:
            if line in lines:
                return key
        return CITY_SECTION_DEFAULT.get(sub1, "other")
    if category not in COUNTY_CATEGORIES:
        return None
    for key, _label, lines in groups:
        if sub1 in lines:
            return key
    return COUNTY_SECTION_DEFAULT[category]


def to_number(text) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------ fetch + shape
def fetch_year(kind: str, end_year: int) -> list[dict]:
    ds = DATASETS[kind]
    cats = ds["categories"] + [CAPITAL_CATEGORY]
    cat_list = ",".join("'" + c.replace("'", "''") + "'" for c in cats)
    group_cols = ["entity_name", "estimated_population", *ds["name_extra"],
                  "category", "subcategory_1", "subcategory_2"]
    rows, offset, page = [], 0, 50000
    while True:
        batch = get_json(SODA.format(id=ds["id"]), {
            "$select": ",".join(group_cols) + f",sum({ds['value']}) as v",
            "$where": f"fiscal_year={end_year} AND category in({cat_list}) AND {ds['value']} != 0",
            "$group": ",".join(group_cols),
            "$order": ",".join(group_cols),
            "$limit": page,
            "$offset": offset,
        })
        rows.extend(batch)
        if len(batch) < page:
            return rows
        offset += page


def shape_year(kind: str, end_year: int, rows: list[dict]) -> dict:
    ds = DATASETS[kind]
    keys = [g[0] for g in ds["groups"]]
    k_ix = {k: i for i, k in enumerate(keys)}
    entities: dict[str, dict] = {}
    line_names: dict[str, int] = {}
    unknown = defaultdict(float)
    for r in rows:
        name = (r.get("entity_name") or "").strip()
        if not name:
            continue
        e = entities.setdefault(name, {
            "name": name, "county": (r.get("county") or "").strip(),
            "pop": 0, "area": to_number(r.get("area_in_sq_miles")),
            "v": [0.0] * len(keys), "capital": 0.0, "debt": 0.0, "lines": defaultdict(float),
        })
        e["pop"] = max(e["pop"], int(to_number(r.get("estimated_population"))))
        cat, sub1 = r.get("category", ""), r.get("subcategory_1", "")
        line = line_name(r.get("subcategory_2", ""))
        value = to_number(r.get("v"))
        b = bucket_for(kind, cat, sub1, line)
        if b is None:
            unknown[cat] += value
            continue
        if b in ("capital", "debt"):
            e[b] += value
            continue
        e["v"][k_ix[b]] += value
        label = line if kind == "cities" else f"{sub1} — {line}" if line and line != sub1 else sub1
        li = line_names.setdefault(f"{b}|{label}", len(line_names))
        e["lines"][li] += value
    if unknown:
        log(f"  note: ignored categories {dict(unknown)}")

    lines_out = [k.split("|", 1) for k, _ in sorted(line_names.items(), key=lambda kv: kv[1])]
    out_entities = []
    for e in sorted(entities.values(), key=lambda e: e["name"]):
        row = [e["name"], e["county"] if kind == "cities" else round(e["area"]), e["pop"],
               [round(x) for x in e["v"]], round(e["capital"]), round(e["debt"]),
               [[li, round(v)] for li, v in sorted(e["lines"].items()) if round(v)]]
        out_entities.append(row)
    return {
        "schema": 1,
        "kind": kind,
        "fiscalYear": fy_label_from_end(end_year),
        "sourceYear": end_year,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "groups": keys,
        "lines": lines_out,  # [groupKey, report line]
        "columns": ["name", "county" if kind == "cities" else "areaSqMi", "population",
                    "groupValues", "capitalOutlay", "debtService", "lineValues"],
        "entities": out_entities,
    }


def dataset_meta(kind: str) -> dict:
    ds = DATASETS[kind]
    meta = get_json(VIEW.format(id=ds["id"]))
    updated = meta.get("rowsUpdatedAt")
    return {
        "name": meta.get("name") or ds["title"],
        "description": meta.get("description", ""),
        "publisher": "California State Controller's Office — By the Numbers",
        "url": f"https://bythenumbers.sco.ca.gov/d/{ds['id']}",
        "updatedAt": datetime.fromtimestamp(updated, timezone.utc).strftime("%Y-%m-%d") if updated else None,
    }


def run_kind(kind: str) -> dict:
    ds = DATASETS[kind]
    latest = get_json(SODA.format(id=ds["id"]), {"$select": "max(fiscal_year) as y"})[0]["y"]
    latest = int(float(latest))
    years = list(range(latest - YEARS_TO_KEEP + 1, latest + 1))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for y in years:
        rows = fetch_year(kind, y)
        doc = shape_year(kind, y, rows)
        if len(doc["entities"]) < (400 if kind == "cities" else 50):
            raise RuntimeError(f"{kind} {y}: only {len(doc['entities'])} entities — refusing to write")
        path = DATA_DIR / f"{kind}-{doc['fiscalYear']}.json"
        path.write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False))
        written.append(doc["fiscalYear"])
        log(f"{kind} {doc['fiscalYear']}: {len(rows):,} lines, {len(doc['entities'])} entities,"
            f" {path.stat().st_size / 1024:.0f} KB")
    # drop files that fell out of the window
    for old in DATA_DIR.glob(f"{kind}-*.json"):
        if old.stem.split("-", 1)[1] not in written:
            old.unlink()
    return {
        "years": sorted(written, reverse=True),
        "groups": [{"key": k, "label": label, "lines": lines} for k, label, lines in ds["groups"]],
        "source": dataset_meta(kind),
    }


def write_index(parts: dict):
    path = DATA_DIR / "index.json"
    index = json.loads(path.read_text()) if path.exists() else {"schema": 1}
    index.update(parts)
    index["generatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps(index, indent=1, ensure_ascii=False))


# ---------------------------------------------------------------------- geo
def cmd_geo(_args=None):
    """City centroids for the map, from CDTFA's city boundary layer."""
    try:
        data = get_json(CITY_POINTS_LAYER, {
            "where": "CITY <> 'Unincorporated'", "outFields": "COUNTY,CITY,Shape__Area",
            "returnGeometry": "false", "returnCentroid": "true", "outSR": "4326",
            "resultRecordCount": "2000", "f": "json"})
        best = {}
        for f in data["features"]:
            a, c = f["attributes"], f.get("centroid") or {}
            key = a["CITY"]
            if "x" in c and (key not in best or best[key][0] < a["Shape__Area"]):
                best[key] = (a["Shape__Area"], round(c["x"], 4), round(c["y"], 4),
                             a["COUNTY"].replace(" County", ""))
        reverse = {v: k for k, v in CITY_ALIASES.items()}
        points = {reverse.get(name, name): [lon, lat, county]
                  for name, (_a, lon, lat, county) in sorted(best.items())}
        if len(points) < 450:
            raise RuntimeError(f"only {len(points)} city points")
        GEO_DIR.mkdir(parents=True, exist_ok=True)
        (GEO_DIR / "city-points.json").write_text(json.dumps({
            "source": "California Department of Tax and Fee Administration — city boundaries (centroids)",
            "url": CITY_POINTS_LAYER.rsplit("/query", 1)[0],
            "points": points}, ensure_ascii=False, separators=(",", ":")))
        log(f"city points: {len(points)}")
    except Exception as exc:  # the map keeps the previous points file
        log(f"warning: city points not refreshed: {exc}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("what", choices=["all", "cities", "counties", "geo"])
    args = p.parse_args(argv)
    parts = {}
    if args.what in ("all", "cities"):
        parts["cities"] = run_kind("cities")
    if args.what in ("all", "counties"):
        parts["counties"] = run_kind("counties")
    if parts:
        write_index(parts)
    if args.what in ("all", "geo"):
        cmd_geo()


if __name__ == "__main__":
    main()
