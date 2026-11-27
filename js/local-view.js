// Cities & counties: per-resident spending on everyday services, compared with neighbors.
import {
  $, el, svgEl, money, perRes, pct, count, dash, median, truncate,
  showTip, moveTip, hideTip, readHash, writeHash, loadJSON, cssVar,
} from "./util.js";

const DATA = "data/local/";
const GEO = "data/geo/";
const DEFAULT_ENTITY = { cities: "Sacramento", counties: "Sacramento" };
const COMPARE = {
  cities: [["county", "Cities in the same county"], ["near", "Nearest cities"], ["size", "Cities of similar size"]],
  counties: [["neighbors", "Neighboring counties"], ["size", "Counties of similar size"], ["all", "All counties"]],
};

let index = null;
let geo = null;          // { counties: FeatureCollection, neighbors: Map, points: {} }
let doc = null;          // current year file
let rows = [];           // entities with derived numbers
let state = { kind: "cities", fy: null, e: null, s: "police", cmp: "county" };
let mounted = false;

const groups = () => index[state.kind].groups;
const groupLabel = (k) => (k === "total" ? "All services" : groups().find((g) => g.key === k)?.label || k);
const noun = () => (state.kind === "cities" ? "city" : "county");

// ---------------------------------------------------------------- data
function deriveRows(d) {
  const keys = d.groups;
  return d.entities.map(([name, extra, pop, values, capital, debt, lines]) => {
    const per = {};
    keys.forEach((k, i) => { per[k] = pop > 0 ? values[i] / pop : null; });
    const total = values.reduce((a, b) => a + b, 0);
    per.total = pop > 0 ? total / pop : null;
    return {
      name, pop, values: Object.fromEntries(keys.map((k, i) => [k, values[i]])), total, per,
      county: d.kind === "cities" ? extra : name, area: d.kind === "counties" ? extra : null,
      capital, debt, lines,
    };
  });
}

async function loadGeo() {
  if (geo) return geo;
  const topo = await loadJSON(GEO + "ca-counties.topo.json");
  const counties = topojson.feature(topo, topo.objects.counties);
  const nb = topojson.neighbors(topo.objects.counties.geometries);
  const neighbors = new Map(counties.features.map((f, i) => [f.properties.name, nb[i].map((j) => counties.features[j].properties.name)]));
  let points = {};
  try { points = (await loadJSON(GEO + "city-points.json")).points; } catch (e) { /* map shows counties only */ }
  geo = { counties, neighbors, points };
  return geo;
}

function selected() { return rows.find((r) => r.name === state.e) || null; }

function peers(sel) {
  if (!sel) return [];
  const others = rows.filter((r) => r.per.total != null);
  if (state.kind === "cities") {
    if (state.cmp === "county") return others.filter((r) => r.county === sel.county);
    if (state.cmp === "near") {
      const p = geo.points[sel.name];
      if (!p) return others.filter((r) => r.county === sel.county);
      const dist = (r) => { const q = geo.points[r.name]; return q ? d3.geoDistance([p[0], p[1]], [q[0], q[1]]) : Infinity; };
      return others.slice().sort((a, b) => dist(a) - dist(b)).slice(0, 13);
    }
  } else {
    if (state.cmp === "neighbors") {
      const names = new Set([sel.name, ...(geo.neighbors.get(sel.name) || [])]);
      return others.filter((r) => names.has(r.name));
    }
    if (state.cmp === "all") return others;
  }
  // similar size: closest populations on a log scale
  const lp = Math.log(sel.pop || 1);
  return others.slice().sort((a, b) => Math.abs(Math.log(a.pop || 1) - lp) - Math.abs(Math.log(b.pop || 1) - lp)).slice(0, state.kind === "cities" ? 15 : 11);
}

/** Describes the comparison group without the selected place, e.g. "the 12 nearest cities". */
function compareLabel(sel, set) {
  const n = set.filter((r) => r.name !== sel?.name).length;
  const plural = (one, many) => (n === 1 ? one : many);
  if (!sel || n === 0) return "";
  if (state.kind === "cities") {
    if (state.cmp === "county") return `the other ${n} ${plural("city", "cities")} in ${sel.county} County`;
    if (state.cmp === "near") return `the ${n} nearest ${plural("city", "cities")}`;
    return `the ${n} ${plural("city", "cities")} closest in population`;
  }
  if (state.cmp === "neighbors") return `the ${n} neighboring ${plural("county", "counties")}`;
  if (state.cmp === "all") return `the other ${n} counties`;
  return `the ${n} ${plural("county", "counties")} closest in population`;
}

const others = (sel, set) => set.filter((r) => r.name !== sel?.name);
const placeName = (r) => (state.kind === "cities" ? r.name : `${r.name} County`);

// ---------------------------------------------------------------- text
function renderText(sel, set) {
  const fy = dash(doc.fiscalYear);
  if (!sel) {
    $("#local-headline").textContent = state.kind === "cities" ? "What does your city spend per resident?" : "What does your county spend per resident?";
    $("#local-sub").textContent = `Pick a ${noun()} to see its day-to-day spending on services, and how that compares with its neighbors.`;
    return;
  }
  $("#local-headline").textContent = `${placeName(sel)} spent ${perRes(sel.per.total)} per resident on day-to-day services in ${fy}.`;
  const peersOnly = others(sel, set);
  const med = median(peersOnly.map((r) => r.per.total));
  const parts = [`Population ${count(sel.pop)}, as estimated in the same report.`];
  if (med != null && peersOnly.length >= 2) parts.push(`The median for ${compareLabel(sel, set)} was ${perRes(med)}.`);
  if (sel.capital || sel.debt) {
    parts.push(`Not included above: ${perRes(sel.capital / sel.pop)} per resident on capital projects and ${perRes(sel.debt / sel.pop)} on debt service.`);
  }
  if (state.kind === "cities" && sel.name === "San Francisco") {
    parts.push("San Francisco is a combined city and county, so its totals include services counties usually provide, such as jails, probation, public health and social services.");
  }
  $("#local-sub").textContent = parts.join(" ");
}

// ---------------------------------------------------------------- charts
function hbar({ host, data, valueKey, highlight, medianValue, onPick, rowH = 30, labelW = 190, fmt = perRes, ticks: showTicks, blue, clipAt }) {
  host.replaceChildren();
  if (!data.length) { host.append(el("p", { class: "empty", text: "No data for this selection." })); return; }
  const width = Math.max(280, host.clientWidth || 520);
  const stacked = width < 460;           // phones: label above the bar
  const lw = stacked ? 0 : labelW;
  const rh = stacked ? rowH + 14 : rowH;
  const valW = 64;
  const top = 6, height = top + data.length * rh + 26;
  let max = d3.max(data, (d) => Math.max(valueKey(d) || 0, showTicks ? d.tick || 0 : 0)) || 1;
  if (clipAt && clipAt < max) max = clipAt;  // extreme outliers run off the end, marked with a break
  const x = d3.scaleLinear().domain([0, Math.max(max, medianValue || 0)]).nice().range([lw + (stacked ? 2 : 0), width - valW]);
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img" });
  host.append(svg);
  const s = d3.select(svg);

  // gridlines + axis
  const ticks = x.ticks(stacked ? 3 : 5);
  s.append("g").selectAll("line").data(ticks).join("line")
    .attr("x1", (d) => x(d)).attr("x2", (d) => x(d)).attr("y1", top).attr("y2", height - 22)
    .attr("stroke", cssVar("--hair"));
  s.append("g").selectAll("text").data(ticks).join("text").attr("class", "tick-label")
    .attr("x", (d) => x(d)).attr("y", height - 6).attr("text-anchor", "middle")
    .text((d) => (fmt === perRes ? `$${d3.format(d >= 10000 ? "~s" : ",")(d)}` : money(d)));

  const bh = 14;
  const barY = stacked ? rh - bh - 6 : (rh - bh) / 2;
  const row = s.append("g").selectAll("g").data(data).join("g")
    .attr("class", (d) => `bar-row${highlight(d) ? " active" : ""}`)
    .attr("transform", (d, i) => `translate(0,${top + i * rh})`)
    .attr("tabindex", onPick ? 0 : null)
    .attr("role", onPick ? "button" : null)
    .attr("aria-label", (d) => `${d.label}: ${fmt(valueKey(d))}`);
  row.append("rect").attr("class", "hit").attr("x", 0).attr("y", 0).attr("width", width).attr("height", rh).attr("rx", 6);
  row.append("text").attr("class", (d) => `row-label${d.self ? " sel" : ""}`)
    .attr("x", stacked ? 2 : lw - 10).attr("y", stacked ? 14 : rh / 2).attr("dy", stacked ? 0 : "0.35em")
    .attr("text-anchor", stacked ? "start" : "end")
    .text((d) => (stacked ? d.label : truncate(d.label, Math.floor(labelW / 7.4))));
  // bar with a 4px rounded data end, square at the baseline
  row.append("path")
    .attr("fill", (d) => (blue || d.self ? cssVar("--s1") : cssVar("--dim-bar")))
    .attr("d", (d) => {
      const x0 = x(0), x1 = Math.max(x0, x(Math.min(valueKey(d) || 0, x.domain()[1]))), y0 = barY, r = Math.min(4, x1 - x0);
      if (x1 - x0 < 0.5) return "";
      return `M${x0},${y0}H${x1 - r}Q${x1},${y0} ${x1},${y0 + r}V${y0 + bh - r}Q${x1},${y0 + bh} ${x1 - r},${y0 + bh}H${x0}Z`;
    });
  if (showTicks) {
    row.filter((d) => d.tick != null).append("line").attr("class", "median-tick")
      .attr("x1", (d) => x(d.tick)).attr("x2", (d) => x(d.tick))
      .attr("y1", barY - 4).attr("y2", barY + bh + 4);
  }
  const clipped = (d) => (valueKey(d) || 0) > x.domain()[1];
  row.filter(clipped).append("path").attr("class", "break")
    .attr("d", () => { const bx = x.range()[1] - 14; return `M${bx},${barY + bh + 2}l5,${-bh - 4}M${bx + 5},${barY + bh + 2}l5,${-bh - 4}`; });
  row.append("text").attr("class", "row-value")
    .attr("x", (d) => (clipped(d) ? x.range()[1] + 6 : x(valueKey(d) || 0) + 6)).attr("y", barY + bh / 2).attr("dy", "0.35em")
    .text((d) => (valueKey(d) == null ? "—" : fmt(valueKey(d))));
  if (medianValue != null) {
    s.append("line").attr("class", "median-line").attr("x1", x(medianValue)).attr("x2", x(medianValue))
      .attr("y1", top - 2).attr("y2", height - 22);
  }
  row.on("pointerenter focus", (e, d) => showTip(e, d.tip))
    .on("pointermove", moveTip)
    .on("pointerleave blur", hideTip);
  if (onPick) {
    row.on("click", (e, d) => onPick(d))
      .on("keydown", (e, d) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(d); } });
  }
}

function renderProfile(sel, set) {
  const host = $("#profile-chart");
  $("#profile-title").textContent = sel ? `${placeName(sel)}: spending per resident` : "Spending per resident";
  if (!sel) { host.replaceChildren(el("p", { class: "empty", text: `Choose a ${noun()} above.` })); $("#profile-sub").textContent = ""; return; }
  const peersOnly = others(sel, set);
  $("#profile-sub").textContent = peersOnly.length >= 2
    ? `By service area in ${dash(doc.fiscalYear)}. The black tick marks the median for ${compareLabel(sel, set)}. Click a row to compare it.`
    : `By service area in ${dash(doc.fiscalYear)}. This comparison group has too few other places for a median; choose another under “Compare with”. Click a row to compare it.`;
  const data = groups().map((g) => {
    const v = sel.per[g.key];
    const tick = peersOnly.length >= 2 ? median(peersOnly.map((r) => r.per[g.key])) : null;
    return {
      key: g.key, label: g.label, v, tick, self: g.key === state.s,
      tip: [{ cls: "tt-value", text: `${perRes(v)} per resident` }, { cls: "tt-name", text: g.label },
        { cls: "tt-sub", text: `${money(sel.values[g.key])} in total` },
        { cls: "tt-sub", text: `Median for ${compareLabel(sel, set)}: ${perRes(tick)}` }],
    };
  });
  hbar({ host, data, valueKey: (d) => d.v, highlight: (d) => d.key === state.s, onPick: (d) => set_({ s: d.key }), ticks: true, blue: true, rowH: 30, labelW: 258 });
}

let showAllRanks = false;

function renderRank(sel, set) {
  const host = $("#rank-chart");
  const svc = groupLabel(state.s);
  if (!sel) { host.replaceChildren(); $("#rank-sub").textContent = ""; $("#rank-title").textContent = svc; return; }
  const groupText = compareLabel(sel, set);
  $("#rank-title").textContent = groupText ? `${svc}: ${placeName(sel)} and ${groupText}` : `${svc}: ${placeName(sel)}`;
  const med = median(others(sel, set).map((r) => r.per[state.s]));
  const ranked = set.filter((r) => r.per[state.s] != null).sort((a, b) => b.per[state.s] - a.per[state.s]);
  const me = ranked.findIndex((r) => r.name === sel.name);
  const LIMIT = 21;
  let shown = ranked;
  if (!showAllRanks && ranked.length > LIMIT) {
    const start = Math.max(0, Math.min(ranked.length - LIMIT, me - Math.floor(LIMIT / 2)));
    shown = ranked.slice(start, start + LIMIT);
  }
  const position = me >= 0 && ranked.length > 2 ? ` ${placeName(sel)} is ${ordinal(me + 1)} highest of these ${ranked.length}.` : "";
  const windowNote = shown.length < ranked.length ? ` Showing the ${shown.length} closest in rank.` : "";
  const medText = med != null && others(sel, set).length >= 2 ? ` The thin line is the median for ${groupText} (${perRes(med)}).` : "";
  $("#rank-sub").textContent = `Per resident, ${dash(doc.fiscalYear)}.${medText}${position}${windowNote}${ranked.length > 1 ? ` Click a bar to switch to that ${noun()}.` : ""}`;
  const data = shown.map((r) => ({
    name: r.name, label: r.name, v: r.per[state.s], self: r.name === sel.name,
    tip: [{ cls: "tt-value", text: `${perRes(r.per[state.s])} per resident` }, { cls: "tt-name", text: placeName(r) },
      { cls: "tt-sub", text: `${money(r.values[state.s] ?? r.total)} total, population ${count(r.pop)}` }],
  }));
  // Keep the scale readable when a few places with tiny populations are extreme.
  const vals = ranked.map((r) => r.per[state.s]);
  const q90 = d3.quantile(vals.slice().sort((a, b) => a - b), 0.9) || 0;
  const clipAt = Math.max(q90 * 1.4, (sel.per[state.s] || 0) * 1.25, (med || 0) * 2.5);
  hbar({ host, data, valueKey: (d) => d.v, highlight: (d) => d.self, medianValue: others(sel, set).length >= 2 ? med : null, onPick: (d) => set_({ e: d.name }), rowH: 26, labelW: 170, clipAt });
  if (ranked.length > LIMIT) {
    host.append(el("button", { type: "button", class: "ghost small", onclick: () => { showAllRanks = !showAllRanks; renderRank(sel, set); } },
      showAllRanks ? `Show the ${LIMIT} closest in rank` : `Show all ${ranked.length}`));
  }
  if (data.some((d) => d.v > clipAt)) host.append(el("p", { class: "small-note", text: "Bars marked with a break run past the end of the scale; their full amount is printed beside them." }));
  if (state.kind === "cities" && state.cmp === "county" && set.length <= 2) {
    host.append(el("p", { class: "small-note", text: `${sel.county} County has only ${set.length} ${set.length === 1 ? "city" : "cities"}; try “Nearest cities” for a wider comparison.` }));
  }
  if (state.kind === "cities" && set.some((r) => r.name === "San Francisco") && sel.name !== "San Francisco") {
    host.append(el("p", { class: "small-note", text: "San Francisco is a combined city and county, so its amounts include county services." }));
  }
}

function ordinal(n) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

function seqColors() { return [1, 2, 3, 4, 5].map((i) => cssVar(`--seq-${i}`)); }

function renderMap(sel, set) {
  const host = $("#map-chart");
  host.replaceChildren();
  const svc = groupLabel(state.s);
  $("#map-title").textContent = `${svc} per resident across California`;
  const width = Math.max(280, host.clientWidth || 480);
  const height = Math.round(width * 1.05);
  const proj = d3.geoAlbers().parallels([34, 40.5]).rotate([120, 0]).fitExtent([[6, 6], [width - 6, height - 6]], geo.counties);
  const path = d3.geoPath(proj);
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": `Map of ${svc.toLowerCase()} spending per resident` });
  host.append(svg);
  const s = d3.select(svg);

  const vals = rows.map((r) => r.per[state.s]).filter((v) => v != null && v > 0);
  const scale = d3.scaleQuantile().domain(vals).range(seqColors());
  const peerNames = new Set(set.map((r) => r.name));
  const byName = new Map(rows.map((r) => [r.name, r]));

  if (state.kind === "counties") {
    s.append("g").selectAll("path").data(geo.counties.features).join("path")
      .attr("d", path)
      .attr("class", (f) => `county choro${f.properties.name === sel?.name ? " sel" : peerNames.has(f.properties.name) ? " peer" : ""}`)
      .style("fill", (f) => { const r = byName.get(f.properties.name); const v = r?.per[state.s]; return v > 0 ? scale(v) : cssVar("--raised"); })
      .on("pointerenter", (e, f) => showTip(e, entityTip(byName.get(f.properties.name), f.properties.name)))
      .on("pointermove", moveTip).on("pointerleave", hideTip)
      .on("click", (e, f) => byName.has(f.properties.name) && set_({ e: f.properties.name }));
    s.select(".sel").raise();
    $("#map-sub").textContent = "Darker counties spend more per resident on this service. Click a county to select it.";
  } else {
    s.append("g").selectAll("path").data(geo.counties.features).join("path")
      .attr("d", path).attr("class", (f) => `county${f.properties.name === sel?.county ? " home" : ""}`);
    const pts = rows.filter((r) => geo.points[r.name]).map((r) => {
      const [lon, lat] = geo.points[r.name];
      const [px, py] = proj([lon, lat]);
      return { r, px, py };
    });
    const rad = d3.scaleSqrt().domain([0, d3.max(pts, (p) => p.r.pop) || 1]).range([2, 10]);
    pts.sort((a, b) => b.r.pop - a.r.pop);
    s.append("g").selectAll("circle").data(pts).join("circle")
      .attr("class", (p) => `dot${peerNames.has(p.r.name) && p.r.name !== sel?.name ? " peer" : ""}`)
      .attr("cx", (p) => p.px).attr("cy", (p) => p.py).attr("r", (p) => rad(p.r.pop))
      .attr("fill", (p) => { const v = p.r.per[state.s]; return v > 0 ? scale(v) : cssVar("--hair"); });
    const me = pts.find((p) => p.r.name === sel?.name);
    if (me) {
      s.append("circle").attr("class", "dot-sel").attr("cx", me.px).attr("cy", me.py).attr("r", rad(me.r.pop) + 4);
      s.append("text").attr("class", "map-label").attr("x", me.px + rad(me.r.pop) + 7).attr("y", me.py).attr("dy", "0.35em")
        .attr("text-anchor", me.px > width * 0.62 ? "end" : "start")
        .attr("dx", me.px > width * 0.62 ? -2 * (rad(me.r.pop) + 7) : 0)
        .text(me.r.name);
    }
    // nearest-dot hover/click layer (hit area much larger than the dots)
    const delaunay = d3.Delaunay.from(pts, (p) => p.px, (p) => p.py);
    const nearest = (e) => {
      const [mx, my] = d3.pointer(e, svg);
      const i = delaunay.find(mx, my);
      const p = pts[i];
      return p && Math.hypot(p.px - mx, p.py - my) < 22 ? p : null;
    };
    s.append("rect").attr("width", width).attr("height", height).attr("fill", "transparent")
      .style("cursor", "pointer")
      .on("pointermove", (e) => { const p = nearest(e); if (p) showTip(e, entityTip(p.r)); else hideTip(); })
      .on("pointerleave", hideTip)
      .on("click", (e) => { const p = nearest(e); if (p) set_({ e: p.r.name }); });
    $("#map-sub").textContent = pts.length
      ? "Each dot is a city, sized by population; darker dots spend more per resident on this service. Outlined dots are the comparison group."
      : "City locations are not available yet.";
  }
  // legend
  const legend = $("#map-legend");
  legend.replaceChildren();
  const q = scale.quantiles();
  legend.append(el("span", { text: "Per resident:" }),
    el("span", { class: "steps", "aria-hidden": "true" }, seqColors().map((c) => el("span", { style: `background:${c}` }))),
    el("span", { text: q.length ? `five equal-count groups, split at ${q.map(perRes).join(", ")}` : "" }),
    el("span", { class: "steps", "aria-hidden": "true" }, el("span", { style: `background:${state.kind === "counties" ? cssVar("--raised") : cssVar("--hair")};width:14px;box-shadow:0 0 0 1px ${cssVar("--base")} inset` })),
    el("span", { text: "none reported" }));
}

function entityTip(r, fallbackName) {
  if (!r && fallbackName === "San Francisco") {
    return [{ cls: "tt-name", text: "San Francisco" }, { cls: "tt-sub", text: "A combined city and county; it reports to the State Controller as a city. Switch to Cities to see it." }];
  }
  if (!r) return [{ cls: "tt-name", text: fallbackName ? `${fallbackName} County` : "" }, { cls: "tt-sub", text: "No report for this year" }];
  const v = r.per[state.s];
  return [{ cls: "tt-value", text: `${perRes(v)} per resident` },
    { cls: "tt-name", text: state.kind === "cities" ? `${r.name} (${r.county} County)` : `${r.name} County` },
    { cls: "tt-sub", text: `${groupLabel(state.s)}, population ${count(r.pop)}` }];
}

function renderLines(sel) {
  const host = $("#lines-table");
  host.replaceChildren();
  if (!sel) { $("#lines-sub").textContent = ""; return; }
  $("#lines-sub").textContent = `The State Controller's report lines behind each service area for ${placeName(sel)}, ${dash(doc.fiscalYear)}.`;
  const lines = sel.lines.map(([li, v]) => ({ group: doc.lines[li][0], line: doc.lines[li][1], v }));
  const order = new Map(groups().map((g, i) => [g.key, i]));
  lines.sort((a, b) => (a.group === state.s ? -1 : 0) - (b.group === state.s ? -1 : 0) || order.get(a.group) - order.get(b.group) || b.v - a.v);
  const body = el("tbody");
  let current = null;
  for (const l of lines) {
    if (l.group !== current) {
      current = l.group;
      const sum = d3.sum(lines.filter((x) => x.group === current), (x) => x.v);
      body.append(el("tr", { class: `group${current === state.s ? " self" : ""}` },
        el("th", { scope: "rowgroup", text: groupLabel(current) }),
        el("td", { class: "num", text: money(sum) }), el("td", { class: "num", text: perRes(sum / sel.pop) })));
    }
    body.append(el("tr", { class: "line" }, el("td", { text: l.line }),
      el("td", { class: "num", text: money(l.v) }), el("td", { class: "num", text: perRes(l.v / sel.pop) })));
  }
  const table = el("table", { class: "data lines-table" },
    el("thead", {}, el("tr", {}, el("th", { text: "Service area and report line" }),
      el("th", { class: "num", text: "Amount" }), el("th", { class: "num", text: "Per resident" }))), body);
  host.append(el("div", { class: "table-wrap" }, table));
}

function renderChips() {
  let chips = $("#service-chips");
  if (!chips) {
    chips = el("div", { class: "chips", id: "service-chips", role: "group", "aria-label": "Service area" });
    $("#local-view .filters").after(chips);
  }
  chips.replaceChildren(...[{ key: "total", label: "All services" }, ...groups()].map((g) =>
    el("button", { type: "button", "aria-pressed": String(g.key === state.s), onclick: () => set_({ s: g.key }) }, g.label)));
}

// ---------------------------------------------------------------- state
function syncControls() {
  document.querySelectorAll("#kind-seg button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.kind === state.kind)));
  $("#entity-label").textContent = state.kind === "cities" ? "City" : "County";
  const list = $("#entity-list");
  list.replaceChildren(...rows.map((r) => el("option", { value: r.name })));
  $("#entity-input").value = state.e || "";
  const ys = $("#local-year");
  ys.replaceChildren(...index[state.kind].years.map((y) => el("option", { value: y }, dash(y))));
  ys.value = state.fy;
  const cs = $("#compare-set");
  cs.replaceChildren(...COMPARE[state.kind].map(([k, label]) => el("option", { value: k }, label)));
  cs.value = state.cmp;
}

function renderSource() {
  let note = $("#local-source");
  if (!note) {
    note = el("p", { class: "small-note", id: "local-source" });
    document.querySelector(".local-grid").after(note);
  }
  const src = index[state.kind].source || {};
  const updated = src.updatedAt ? new Date(src.updatedAt + "T12:00:00").toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" }) : null;
  note.textContent = `Source: ${src.name || "State Controller's Office"} (${src.publisher || "By the Numbers"})${updated ? `, dataset updated ${updated}` : ""}. Figures are as each government reported them.`;
}

function render() {
  const sel = selected();
  const set = peers(sel);
  renderChips();
  renderText(sel, set);
  renderProfile(sel, set);
  renderMap(sel, set);
  renderRank(sel, set);
  renderLines(sel);
  renderSource();
  writeHash({ view: "local", kind: state.kind, fy: state.fy, e: state.e, s: state.s, cmp: state.cmp });
}

async function load() {
  const years = index[state.kind].years;
  if (!years.includes(state.fy)) state.fy = years[0];
  doc = await loadJSON(`${DATA}${state.kind}-${state.fy}.json`);
  rows = deriveRows(doc);
  if (!rows.some((r) => r.name === state.e)) state.e = rows.some((r) => r.name === DEFAULT_ENTITY[state.kind]) ? DEFAULT_ENTITY[state.kind] : rows[0]?.name;
  if (state.s !== "total" && !doc.groups.includes(state.s)) state.s = doc.groups[0];
  if (!COMPARE[state.kind].some(([k]) => k === state.cmp)) state.cmp = COMPARE[state.kind][0][0];
}

async function set_(patch) {
  hideTip();
  if (patch.e || patch.s || patch.cmp || patch.kind || patch.fy) showAllRanks = false;
  const kindChanged = patch.kind && patch.kind !== state.kind;
  state = { ...state, ...patch };
  if (kindChanged) { state.e = null; state.s = "total"; state.cmp = COMPARE[state.kind][0][0]; }
  if (kindChanged || patch.fy) await load();
  syncControls();
  render();
}

export async function showLocal() {
  const h = readHash();
  if (!mounted) {
    mounted = true;
    try {
      [index] = await Promise.all([loadJSON(DATA + "index.json"), loadGeo()]);
    } catch (e) {
      $("#local-headline").textContent = "City and county data is not available yet.";
      $("#local-sub").textContent = "The monthly update job has not finished its first run. Check back after it completes.";
      document.querySelector(".local-grid").hidden = true;
      return;
    }
    document.querySelectorAll("#kind-seg button").forEach((b) => b.addEventListener("click", () => set_({ kind: b.dataset.kind })));
    const input = $("#entity-input");
    const pick = () => {
      const v = input.value.trim().toLowerCase();
      const r = rows.find((x) => x.name.toLowerCase() === v) || rows.find((x) => x.name.toLowerCase().startsWith(v) && v.length >= 3);
      if (r) set_({ e: r.name });
    };
    input.addEventListener("change", pick);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") pick(); });
    $("#local-year").addEventListener("change", (e) => set_({ fy: e.target.value }));
    $("#compare-set").addEventListener("change", (e) => set_({ cmp: e.target.value }));
    let t;
    window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => { if (!$("#local-view").hidden && doc) render(); }, 150); });
    window.addEventListener("themechange", () => { if (!$("#local-view").hidden && doc) render(); });
  }
  if (!index) return;
  const kind = h.kind === "counties" ? "counties" : "cities";
  state = {
    kind, fy: h.fy || null, e: h.e || null,
    s: h.s || "total", cmp: h.cmp || COMPARE[kind][0][0],
  };
  await load();
  const sel0 = selected();
  if (!h.cmp && kind === "cities" && sel0 && rows.filter((r) => r.county === sel0.county).length < 4) state.cmp = "near";
  syncControls();
  render();
}
