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

- **Coverage.** Open FI$Cal covers about 79% of state budgetary expenditures (151 departments, 188 business units). Some large departments, including Caltrans, the DMV and the Department of Corrections and Rehabilitation, aren't in it. It is published with a lag of at least 60 days, and adjustments can still be posted to a month after it ends. Earlier fiscal years include fewer departments because departments joined FI$Cal over time. The site flags those years.
- **How state totals are computed.**
  - **All files are read.** The pipeline reads every monthly file Open FI$Cal has posted. It finds them by name, because the published download list can lag behind: in November 2026 it stopped at May 2026, while June to August 2026 were already posted.
  - **Each transaction counts in its own year.** Transactions go to the fiscal year and period recorded on them, not to the file they came in, because a monthly file can contain a few transactions from another fiscal year.
  - **Exact sums.** Amounts are added exactly and stored in cents.
- **State amounts are net.** Refunds, reversals and negative entries such as internal cost recovery reduce the totals, and labels show net amounts. The Sankey can't draw a negative band, so where a fund's net amount for a department is negative it's left out of the drawing, and the amount left out is shown under the chart. In agency and department views, spending types with a net negative amount appear as a grey "Negative entries" band flowing in, drawn at their exact total.
- **Spending types** are the state's account categories (`account_type_1`). Their descriptions on the site are based on the [State Administrative Manual, section 7670](https://www.dgs.ca.gov/Resources/SAM/TOC/7600/7670).
- **City and county amounts.**
  - **What's counted.** These are governmental-fund current expenditures, meaning day-to-day services. City-run enterprises (water, electric, airports, hospitals…) are left out, and capital outlay and debt service are reported separately.
  - **Source and population.** Figures appear as each local government reported them to the State Controller. Per-resident values use the population estimate in the same report.
  - **Comparison medians** leave out the place being compared.
  - **Years shown.** Only years that use the Controller's current report layout are shown (cities from 2017-18, counties from 2016-17).
  - **San Francisco** is a combined city and county that reports as a city, so its city figures include county services.
- **Service areas** group the Controller's report lines under the Controller's own section names. The mapping is defined in [`scripts/local_pipeline.py`](scripts/local_pipeline.py) (`CITY_GROUPS`, `COUNTY_GROUPS`), and the site lists the lines behind each total for the selected place.

## How it updates

[`.github/workflows/update-data.yml`](.github/workflows/update-data.yml) runs on the 9th of every month, whenever the pipeline scripts change, and on demand from the Actions tab.

1. **plan.** Runs the offline tests. It then finds every Open FI$Cal monthly file, checking the published list and probing the file names, and compares each file's date and size with the last run. If nothing changed, the state step is skipped. If anything changed, every file is read again, because a file can hold other years' transactions. Choose *full rebuild* on a manual run to force a read.
2. **state.** One job per file-year streams that year's monthly CSV files (up to ~1 GB each) and adds them up by each transaction's own fiscal year, fund group, department and spending type.
3. **publish.** Merges the partial results. If any file-year failed, it keeps the previous state data rather than publish a partial total. It writes `data/state/fy*.json`, refreshes city and county data from the State Controller's API (server-side sums, last 10 years) and commits any changes.

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
