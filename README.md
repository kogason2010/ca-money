# Where California's money goes

**Live site:** https://kogason2010.github.io/ca-money/

An interactive look at public spending in California, built from official open data and refreshed automatically every month.

- **State spending.** A Sankey diagram follows state money from fund groups (General Fund, special funds, federal funds…) to agencies and departments. Click any band to zoom in: an agency shows its departments and what they spent the money on, and a department shows its funding and spending types. Every view can be shown as a table, and every zoom level has its own shareable link.
- **Cities and counties.** Pick a city or county to see its day-to-day spending per resident on police, fire, streets, parks, libraries and other services. You can compare it with the same county, the nearest cities, or places of similar size, on a map and in ranked charts.

## Data sources

| What | Source | Published by |
| --- | --- | --- |
| State expenditures | [Open FI$Cal monthly spending transaction files](https://open.fiscal.ca.gov/download-expenditures.html) | California Department of FI$Cal |
| City spending | [City Financial Transactions Report, Expenditures](https://bythenumbers.sco.ca.gov/) (dataset `ju3w-4gxp`) | California State Controller's Office |
| County spending | [County Financial Transactions Report, Expenditures](https://bythenumbers.sco.ca.gov/) (dataset `uctr-c2j8`) | California State Controller's Office |
| City locations | City boundary layer (centroids) | California Department of Tax and Fee Administration |
| County shapes | [us-atlas](https://github.com/topojson/us-atlas) (U.S. Census Bureau cartographic boundaries) | |

### What the numbers mean

- **Coverage.** Open FI$Cal covers about 79% of state budgetary expenditures (151 departments, 188 business units). It is published with a lag of at least 60 days, and adjustments can still be posted to a month after it ends. Earlier fiscal years include fewer departments because departments joined FI$Cal over time. The site flags those years.
- **State amounts are net.** Refunds, reversals and internal cost recovery reduce the totals. The Sankey can't draw a negative band, so where a fund's net amount for a department is negative it's left out, and the amount left out is shown under the chart. Inside department views, negative spending-type entries appear as a grey "Cost recovery & offsets" source, so the bands still add up.
- **City and county amounts** are governmental-fund current expenditures (day-to-day services). City-run enterprises (water, electric, airports, hospitals…) are left out, and capital outlay and debt service are reported separately. Figures appear as each local government reported them to the State Controller. Per-resident values use the population estimate from the same report.
- **Service areas** group the Controller's report lines. The mapping is defined in [`scripts/local_pipeline.py`](scripts/local_pipeline.py) (`CITY_GROUPS`, `COUNTY_GROUPS`), and the site lists the lines behind each total for the selected place.

## How it updates

[`.github/workflows/update-data.yml`](.github/workflows/update-data.yml) runs on the 9th of every month, whenever the pipeline scripts change, and on demand from the Actions tab.

1. **plan.** Runs the offline tests, then reads Open FI$Cal's file list and decides which fiscal years to re-read: always the newest two (late adjustments), plus any year that is missing or incomplete. Choose *full rebuild* on a manual run to re-read everything.
2. **state.** One job per fiscal year streams that year's monthly CSV files (up to ~1 GB each) and adds them up by fund group, department and spending type. Nothing is stored except the totals.
3. **publish.** Writes `data/state/fy*.json`, refreshes city and county data from the State Controller's API (server-side sums, last 10 years), and commits any changes.

Everything is plain Python standard library. There are no dependencies to install.

## Repository layout

```
index.html, css/, js/       the site (vanilla JS + D3, no build step)
vendor/, fonts/             D3, d3-sankey, topojson-client, Public Sans
data/state/                 one summary per fiscal year + index.json
data/local/                 cities-YYYY-YY.json, counties-YYYY-YY.json + index.json
data/geo/                   California counties (TopoJSON) and city points
scripts/                    state_pipeline.py, local_pipeline.py
tests/                      offline tests, synthetic data generator, screenshot scripts
```

## Running locally

```sh
python -m http.server 8000          # then open http://localhost:8000
python -m unittest discover tests   # pipeline tests (no network)

# refresh data yourself (needs internet)
python scripts/local_pipeline.py all
python scripts/state_pipeline.py fetch --year 2024 && python scripts/state_pipeline.py index
```

Third-party code keeps its own license: D3, d3-sankey, topojson-client and us-atlas (ISC), and Public Sans (SIL Open Font License, see `fonts/`).
