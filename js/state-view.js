// State spending: a zoomable Sankey from fund groups to agencies to departments.
import {
  $, el, money, moneyWords, pct, dash, truncate, showTip, moveTip, hideTip,
  readHash, writeHash, loadJSON, cssVar,
} from "./util.js";

const DATA = "data/state/";

// Fixed fund -> color slot, so a fund keeps its color in every view and year.
const FUND_ORDER = ["General Fund", "Special Funds", "Federal Funds", "Bond Funds", "Other NonGovt Cost Funds"];
const FUND_LABEL = {
  "Other NonGovt Cost Funds": "Other non-governmental cost funds",
  "Other NonGovt Cost Fund": "Other non-governmental cost funds",
};
const AGENCY_LABEL = {
  "Bus Consumer Srvcs & Housing": "Business, Consumer Services & Housing",
  "Legislative, Judicial, & Exec": "Legislative, Judicial & Executive",
  "Corrections & Rehabilitation": "Corrections & Rehabilitation",
  "Labor & Workforce Development": "Labor & Workforce Development",
  "Govt Operations": "Government Operations",
};
const OFFSET_NAME = "Cost recovery & offsets";
const OTHER_DEPTS = "Other departments";

let index = null;
let doc = null;    // current fiscal-year file
let prep = null;   // precomputed sums for doc
let zoom = { type: "all" };
let showTable = false;
let mounted = false;

const fundName = (f) => FUND_LABEL[doc.funds[f]] || doc.funds[f];
const agencyName = (a) => AGENCY_LABEL[doc.agencies[a]] || doc.agencies[a];
const deptName = (d) => doc.departments[d][1];

function fundSlot(f) {
  const name = doc.funds[f];
  const known = FUND_ORDER.indexOf(name);
  if (known >= 0) return known + 1;
  const unknown = doc.funds.filter((n) => !FUND_ORDER.includes(n)).sort();
  const slot = FUND_ORDER.length + 1 + unknown.indexOf(name);
  return slot <= 7 ? slot : null;
}
const fundOrder = (f) => { const s = fundSlot(f); return s ?? 99; };
const fundColor = (f) => { const s = fundSlot(f); return cssVar(s ? `--s${s}` : "--s-other"); };

// ---------------------------------------------------------------- prepare
function prepare(d) {
  const F = d.funds.length, D = d.departments.length;
  const netFD = new Float64Array(F * D);
  const netDT = Array.from({ length: D }, () => new Map());
  for (const [f, dep, t, v] of d.flows) {
    netFD[f * D + dep] += v;
    netDT[dep].set(t, (netDT[dep].get(t) || 0) + v);
  }
  const drawn = (f, dep) => Math.max(0, netFD[f * D + dep]);
  const deptDrawn = new Float64Array(D), deptNet = new Float64Array(D);
  let excluded = 0;
  for (let f = 0; f < F; f++) for (let dep = 0; dep < D; dep++) {
    const v = netFD[f * D + dep];
    deptNet[dep] += v;
    if (v > 0) deptDrawn[dep] += v; else excluded += v;
  }
  const agencyOf = d.departments.map((x) => x[2]);
  const agencyNet = new Float64Array(d.agencies.length);
  agencyOf.forEach((a, dep) => { agencyNet[a] += deptNet[dep]; });
  const fundNet = new Float64Array(F);
  for (let f = 0; f < F; f++) for (let dep = 0; dep < D; dep++) fundNet[f] += netFD[f * D + dep];
  return { F, D, netFD, netDT, drawn, deptDrawn, deptNet, agencyOf, agencyNet, fundNet, excluded };
}

// ---------------------------------------------------------------- graphs
function addLink(map, source, target, value, f) {
  if (!(value > 0)) return;
  const key = `${source}|${target}|${f}`;
  const l = map.get(key);
  if (l) l.value += value; else map.set(key, { source, target, value, fund: f });
}

function graphOverview() {
  const { F, D, drawn, deptDrawn, agencyOf } = prep;
  const total = d3.sum(deptDrawn);
  const ranked = d3.range(D).filter((x) => deptDrawn[x] > 0).sort((a, b) => deptDrawn[b] - deptDrawn[a]);
  const shown = new Set(ranked.filter((x, i) => i < 22 && deptDrawn[x] >= total * 0.004));
  const agencyVal = new Float64Array(doc.agencies.length);
  ranked.forEach((x) => { agencyVal[agencyOf[x]] += deptDrawn[x]; });
  const agencyRank = d3.range(doc.agencies.length).sort((a, b) => agencyVal[b] - agencyVal[a]);
  const aOrder = new Map(agencyRank.map((a, i) => [a, i]));

  const nodes = [], links = new Map();
  for (let f = 0; f < F; f++) nodes.push({ id: `f${f}`, kind: "fund", f, name: fundName(f), order: fundOrder(f) });
  agencyRank.forEach((a, i) => { if (agencyVal[a] > 0) nodes.push({ id: `a${a}`, kind: "agency", a, name: agencyName(a), order: i }); });
  for (const x of shown) nodes.push({ id: `d${x}`, kind: "dept", d: x, name: deptName(x), order: aOrder.get(agencyOf[x]) * 1e3 - deptDrawn[x] / total });
  nodes.push({ id: "other", kind: "other", name: `${OTHER_DEPTS} (${ranked.length - shown.size})`, order: 1e9 });

  for (const x of ranked) {
    const a = agencyOf[x];
    for (let f = 0; f < F; f++) {
      const v = drawn(f, x);
      addLink(links, `f${f}`, `a${a}`, v, f);
      addLink(links, `a${a}`, shown.has(x) ? `d${x}` : "other", v, f);
    }
  }
  return { nodes, links: [...links.values()], heads: ["Fund group", "Agency", "Department"], total };
}

function typeStage(nodes, links, deptIds) {
  // Spending types for a set of departments, with a balancing source for
  // negative entries (cost recovery etc.) so every band adds up.
  const { netDT, deptDrawn } = prep;
  const typeVal = new Map();
  let offsets = 0;
  for (const group of deptIds) {
    const [nodeId, members] = group;
    let pos = 0, inflow = 0;
    for (const x of members) {
      inflow += deptDrawn[x];
      for (const [t, v] of netDT[x]) if (v > 0) {
        pos += v;
        typeVal.set(t, (typeVal.get(t) || 0) + v);
        addLink(links, nodeId, `t${t}`, v, -1);
      }
    }
    const bal = pos - inflow;
    if (bal > 0.5) { offsets += bal; addLink(links, "offset", nodeId, bal, -2); }
  }
  [...typeVal.entries()].sort((a, b) => b[1] - a[1]).forEach(([t], i) =>
    nodes.push({ id: `t${t}`, kind: "type", t, name: doc.spendTypes[t], order: i }));
  if (offsets > 0) nodes.push({ id: "offset", kind: "offset", name: OFFSET_NAME, order: 1e9 });
}

function graphAgency(a) {
  const { F, D, drawn, deptDrawn, agencyOf } = prep;
  const members = d3.range(D).filter((x) => agencyOf[x] === a && deptDrawn[x] > 0).sort((p, q) => deptDrawn[q] - deptDrawn[p]);
  const shown = members.slice(0, members.length > 24 ? 23 : 24);
  const rest = members.slice(shown.length);
  const nodes = [], links = new Map();
  for (let f = 0; f < F; f++) nodes.push({ id: `f${f}`, kind: "fund", f, name: fundName(f), order: fundOrder(f) });
  shown.forEach((x, i) => nodes.push({ id: `d${x}`, kind: "dept", d: x, name: deptName(x), order: i }));
  if (rest.length) nodes.push({ id: "other", kind: "other", name: `${OTHER_DEPTS} (${rest.length})`, order: 1e9 });
  for (const x of members) for (let f = 0; f < F; f++) addLink(links, `f${f}`, rest.includes(x) ? "other" : `d${x}`, drawn(f, x), f);
  const groups = shown.map((x) => [`d${x}`, [x]]);
  if (rest.length) groups.push(["other", rest]);
  typeStage(nodes, links, groups);
  return { nodes, links: [...links.values()], heads: ["Fund group", "Department", "Spending type"], total: d3.sum(members, (x) => deptDrawn[x]) };
}

function graphFund(f) {
  const { D, drawn, agencyOf } = prep;
  const vals = d3.range(D).map((x) => drawn(f, x));
  const total = d3.sum(vals);
  const ranked = d3.range(D).filter((x) => vals[x] > 0).sort((p, q) => vals[q] - vals[p]);
  const shown = new Set(ranked.filter((x, i) => i < 20 && vals[x] >= total * 0.004));
  const agencyVal = new Map();
  ranked.forEach((x) => agencyVal.set(agencyOf[x], (agencyVal.get(agencyOf[x]) || 0) + vals[x]));
  const agencyRank = [...agencyVal.keys()].sort((p, q) => agencyVal.get(q) - agencyVal.get(p));
  const aOrder = new Map(agencyRank.map((a, i) => [a, i]));
  const nodes = [{ id: `f${f}`, kind: "fund", f, name: fundName(f), order: 0, static: true }], links = new Map();
  agencyRank.forEach((a, i) => nodes.push({ id: `a${a}`, kind: "agency", a, name: agencyName(a), order: i }));
  for (const x of shown) nodes.push({ id: `d${x}`, kind: "dept", d: x, name: deptName(x), order: aOrder.get(agencyOf[x]) * 1e3 - vals[x] / total });
  if (ranked.length > shown.size) nodes.push({ id: "other", kind: "other", name: `${OTHER_DEPTS} (${ranked.length - shown.size})`, order: 1e9 });
  for (const x of ranked) {
    addLink(links, `f${f}`, `a${agencyOf[x]}`, vals[x], f);
    addLink(links, `a${agencyOf[x]}`, shown.has(x) ? `d${x}` : "other", vals[x], f);
  }
  return { nodes, links: [...links.values()], heads: ["Fund group", "Agency", "Department"], total };
}

function graphDept(x) {
  const { F, drawn, deptDrawn } = prep;
  const nodes = [], links = new Map();
  for (let f = 0; f < F; f++) nodes.push({ id: `f${f}`, kind: "fund", f, name: fundName(f), order: fundOrder(f) });
  nodes.push({ id: `d${x}`, kind: "dept", d: x, name: deptName(x), order: 0, static: true });
  for (let f = 0; f < F; f++) addLink(links, `f${f}`, `d${x}`, drawn(f, x), f);
  typeStage(nodes, links, [[`d${x}`, [x]]]);
  return { nodes, links: [...links.values()], heads: ["Fund group", "Department", "Spending type"], total: deptDrawn[x] };
}

function currentGraph(twoLevels) {
  const g = zoom.type === "agency" ? graphAgency(zoom.a)
    : zoom.type === "fund" ? graphFund(zoom.f)
      : zoom.type === "dept" ? graphDept(zoom.d) : graphOverview();
  if (twoLevels) {
    // Phones show two levels at a time; tapping a band goes one level deeper.
    const drop = zoom.type === "all" || zoom.type === "fund" ? new Set(["dept", "other"]) : new Set(["type", "offset"]);
    const gone = new Set(g.nodes.filter((n) => drop.has(n.kind)).map((n) => n.id));
    g.nodes = g.nodes.filter((n) => !gone.has(n.id));
    g.links = g.links.filter((l) => !gone.has(l.source) && !gone.has(l.target));
    g.heads = g.heads.slice(0, 2);
  }
  // keep only nodes that carry a link
  const used = new Set(g.links.flatMap((l) => [l.source, l.target]));
  g.nodes = g.nodes.filter((n) => used.has(n.id));
  return g;
}

// ---------------------------------------------------------------- render
function linkColor(l) {
  return l.fund >= 0 ? fundColor(l.fund) : cssVar("--s-other");
}

function zoomTarget(n) {
  if (n.static) return null;
  if (n.kind === "fund") return { type: "fund", f: n.f };
  if (n.kind === "agency") return { type: "agency", a: n.a };
  if (n.kind === "dept") return { type: "dept", d: n.d };
  return null;
}

function renderSankey() {
  const svg = d3.select("#sankey");
  const frame = $("#sankey-frame .scroll-x");
  const twoLevels = frame.clientWidth < 640;
  const width = twoLevels ? Math.max(300, frame.clientWidth) : Math.max(760, frame.clientWidth);
  const g = currentGraph(twoLevels);
  svg.classed("two-levels", twoLevels);
  $("#sankey-frame .hint").textContent = twoLevels
    ? "This screen shows two levels at a time. Tap a fund, agency or department to go one level deeper."
    : "Click or press Enter on a fund, agency or department to zoom in. Hover for amounts.";
  const byCol = d3.rollup(g.nodes, (v) => v.length, (n) => n.kind);
  const tallest = Math.max(...byCol.values());
  const height = Math.round(Math.max(420, Math.min(1100, tallest * 25 + 70)));
  const narrow = width < 940;
  const ml = twoLevels ? 96 : narrow ? 128 : 170, mr = twoLevels ? 168 : narrow ? 178 : 238, mt = 30, mb = 8;

  const sankey = d3.sankey()
    .nodeId((d) => d.id)
    .nodeWidth(14)
    .nodePadding(Math.max(6, Math.min(14, (height - 80) / tallest / 3)))
    .nodeAlign(d3.sankeyJustify)
    .nodeSort((a, b) => a.order - b.order)
    .linkSort((a, b) => (a.source === b.source
      ? (a.target.y0 - b.target.y0) || (fundOrderOf(a) - fundOrderOf(b))
      : (a.source.y0 - b.source.y0) || (fundOrderOf(a) - fundOrderOf(b))))
    .extent([[ml, mt], [width - mr, height - mb]]);
  const graph = sankey({ nodes: g.nodes.map((n) => ({ ...n })), links: g.links.map((l) => ({ ...l })) });
  g.total = d3.sum(graph.nodes.filter((n) => n.depth === 0), (n) => n.value);

  svg.attr("viewBox", `0 0 ${width} ${height}`).attr("width", width).attr("height", height);
  svg.selectAll("*").remove();
  svg.classed("hovering", false);

  // column headings
  const cols = d3.groups(graph.nodes, (n) => n.depth).sort((a, b) => a[0] - b[0]);
  const lastDepth = d3.max(graph.nodes, (n) => n.depth);
  svg.append("g").selectAll("text").data(cols).join("text")
    .attr("class", "col-head")
    .attr("x", ([depth, ns]) => depth === 0 ? ns[0].x1 : depth === lastDepth ? ns[0].x0 : ns[0].x0)
    .attr("y", 14)
    .attr("text-anchor", ([depth]) => depth === 0 ? "end" : "start")
    .text(([depth]) => g.heads[Math.min(depth, g.heads.length - 1)]);

  const link = svg.append("g").selectAll("path").data(graph.links).join("path")
    .attr("class", "link")
    .attr("d", d3.sankeyLinkHorizontal())
    .attr("stroke", linkColor)
    .attr("stroke-width", (d) => Math.max(1, d.width))
    .on("pointerenter", (e, d) => { focusLinks([d]); showTip(e, linkTip(d, g)); })
    .on("pointermove", moveTip)
    .on("pointerleave", () => { focusLinks(null); hideTip(); });

  // DOM (and tab) order follows the picture: column by column, top to bottom.
  const ordered = graph.nodes.slice().sort((a, b) => a.depth - b.depth || a.y0 - b.y0);
  const node = svg.append("g").selectAll("g").data(ordered).join("g")
    .attr("class", (d) => `node${zoomTarget(d) ? "" : " static"}`)
    .attr("tabindex", 0)
    .attr("role", (d) => (zoomTarget(d) ? "button" : "img"))
    .attr("aria-label", (d) => `${d.name}, ${money(d.value)}${zoomTarget(d) ? ". Press Enter to zoom in." : ""}`);
  node.append("rect").attr("class", "hit")
    .attr("x", (d) => d.x0 - 6).attr("y", (d) => d.y0 - 2)
    .attr("width", (d) => d.x1 - d.x0 + 12).attr("height", (d) => Math.max(8, d.y1 - d.y0 + 4))
    .attr("fill", "transparent").attr("stroke", "none");
  node.append("rect").attr("class", "mark")
    .attr("x", (d) => d.x0).attr("y", (d) => d.y0)
    .attr("width", (d) => d.x1 - d.x0).attr("height", (d) => Math.max(1, d.y1 - d.y0))
    .attr("rx", 2)
    .attr("fill", (d) => (d.kind === "fund" ? fundColor(d.f) : cssVar("--ink-2")));

  const enter = (e, d) => {
    const ls = [...d.sourceLinks, ...d.targetLinks];
    focusLinks(ls);
    showTip(e, nodeTip(d, g));
  };
  node.on("pointerenter", enter).on("focus", enter)
    .on("pointermove", moveTip)
    .on("pointerleave blur", () => { focusLinks(null); hideTip(); })
    .on("click", (e, d) => go(zoomTarget(d)))
    .on("keydown", (e, d) => {
      if ((e.key === "Enter" || e.key === " ") && zoomTarget(d)) { e.preventDefault(); go(zoomTarget(d)); }
    });

  // labels
  const maxChars = (d) => (twoLevels ? (d.depth === 0 ? 13 : 23)
    : d.depth === 0 ? (narrow ? 17 : 23) : d.depth === lastDepth ? (narrow ? 22 : 30) : (narrow ? 20 : 28));
  const label = node.filter((d) => d.y1 - d.y0 >= 9 || d.kind === "fund" && d.y1 - d.y0 >= 4)
    .append("text").attr("class", "label")
    .attr("x", (d) => (d.depth === 0 ? d.x0 - 8 : d.x1 + 8))
    .attr("y", (d) => (d.y0 + d.y1) / 2)
    .attr("text-anchor", (d) => (d.depth === 0 ? "end" : "start"));
  label.each(function (d) {
    const t = d3.select(this);
    const tall = d.y1 - d.y0 >= 30;
    t.append("tspan").attr("x", t.attr("x")).attr("dy", tall ? "-0.2em" : "0.35em").text(truncate(d.name, maxChars(d)));
    if (tall) t.append("tspan").attr("class", "amt").attr("x", t.attr("x")).attr("dy", "1.25em").text(money(d.value));
  });

  function focusLinks(ls) {
    if (!ls) { svg.classed("hovering", false); link.classed("on", false); return; }
    const on = new Set(ls);
    svg.classed("hovering", true);
    link.classed("on", (l) => on.has(l));
  }

  renderLegend(graph);
  renderTable(graph, g);
}

function fundOrderOf(l) { return l.fund >= 0 ? fundOrder(l.fund) : 100 + Math.abs(l.fund); }

function nodeTip(d, g) {
  const rows = [{ cls: "tt-value", text: money(d.value) }, { cls: "tt-name", text: d.name }];
  const offset = d.targetLinks.find((l) => l.source.kind === "offset");
  if (offset) rows.push({ cls: "tt-sub", text: `${money(d.value - offset.value)} after ${money(offset.value)} of cost recovery & offsets` });
  if (d.kind === "dept") rows.push({ cls: "tt-sub", text: agencyName(prep.agencyOf[d.d]) });
  if (d.kind === "offset") rows.push({ cls: "tt-sub", text: "Negative entries, mostly internal cost recovery (costs charged back to other programs), that offset this spending." });
  else if (g.total) rows.push({ cls: "tt-sub", text: `${pct(d.value / g.total)} of the amount shown` });
  if (zoomTarget(d)) rows.push({ cls: "tt-sub", text: "Click to zoom in" });
  return rows;
}

function linkTip(l, g) {
  const rows = [{ cls: "tt-value", text: money(l.value) },
    { cls: "tt-name", text: `${l.source.name} to ${l.target.name}` }];
  if (l.fund >= 0 && l.source.kind !== "fund") rows.push({ key: fundColor(l.fund), text: fundName(l.fund) });
  rows.push({ cls: "tt-sub", text: `${pct(l.value / l.target.value)} of ${truncate(l.target.name, 40)}` });
  return rows;
}

function renderLegend(graph) {
  const box = $("#fund-legend");
  box.replaceChildren();
  const funds = [...new Set(graph.links.filter((l) => l.fund >= 0).map((l) => l.fund))].sort((a, b) => fundOrder(a) - fundOrder(b));
  for (const f of funds) box.append(el("span", { class: "key" }, el("span", { class: "sw", style: `background:${fundColor(f)}` }), fundName(f)));
  if (graph.links.some((l) => l.fund < 0)) {
    box.append(el("span", { class: "key" }, el("span", { class: "sw", style: `background:${cssVar("--s-other")}` }), "Spending type (not split by fund)"));
  }
}

function renderTable(graph, g) {
  const wrap = $("#state-table");
  wrap.hidden = !showTable;
  if (!showTable) return;
  const rows = [...graph.links].sort((a, b) => b.value - a.value);
  const table = el("table", { class: "data" },
    el("caption", { class: "sr-only" }, "Flows shown in the chart"),
    el("thead", {}, el("tr", {}, el("th", { text: "From" }), el("th", { text: "To" }), el("th", { text: "Fund group" }),
      el("th", { class: "num", text: "Amount" }), el("th", { class: "num", text: "Share" }))),
    el("tbody", {}, rows.map((l) => el("tr", {},
      el("td", { text: l.source.name }), el("td", { text: l.target.name }),
      el("td", { text: l.fund >= 0 ? fundName(l.fund) : "—" }),
      el("td", { class: "num", text: money(l.value) }), el("td", { class: "num", text: pct(l.value / g.total) })))));
  wrap.replaceChildren(table);
}

// ---------------------------------------------------------------- text
function headline() {
  const fy = dash(doc.fiscalYear);
  const partial = !doc.complete;
  if (zoom.type === "all") {
    const net = doc.totals.net;
    return [partial ? `So far in ${fy}, California state departments have recorded ${moneyWords(net)} in spending.`
      : `In ${fy}, California state departments recorded ${moneyWords(net)} in spending.`,
    "Follow it from the funds that paid for it to the agencies and departments that spent it."];
  }
  if (zoom.type === "agency") {
    const n = prep.agencyOf.filter((a, x) => a === zoom.a && prep.deptDrawn[x] > 0).length;
    return [`${agencyName(zoom.a)}: ${moneyWords(prep.agencyNet[zoom.a])} in ${fy}.`,
      `${n} department${n === 1 ? "" : "s"} in this agency, by the funds that paid for them and what the money was spent on.`];
  }
  if (zoom.type === "fund") {
    return [`${fundName(zoom.f)}: ${moneyWords(prep.fundNet[zoom.f])} of state spending in ${fy}.`,
      "Which agencies and departments this group of funds paid for."];
  }
  const x = zoom.d;
  return [`${deptName(x)}: ${moneyWords(prep.deptNet[x])} in ${fy}.`,
    `Part of ${agencyName(prep.agencyOf[x])}. Where its money came from and what it was spent on.`];
}

function renderText() {
  const [h, sub] = headline();
  $("#state-headline").textContent = h;
  $("#state-sub").textContent = sub;

  const crumbs = $("#state-crumbs");
  crumbs.replaceChildren();
  const trail = [{ label: "All spending", z: { type: "all" } }];
  if (zoom.type === "agency") trail.push({ label: agencyName(zoom.a), z: zoom });
  if (zoom.type === "fund") trail.push({ label: fundName(zoom.f), z: zoom });
  if (zoom.type === "dept") {
    const a = prep.agencyOf[zoom.d];
    trail.push({ label: agencyName(a), z: { type: "agency", a } }, { label: deptName(zoom.d), z: zoom });
  }
  trail.forEach((c, i) => {
    if (i) crumbs.append(el("span", { class: "sep", "aria-hidden": "true" }, "›"));
    const last = i === trail.length - 1;
    crumbs.append(el("button", { type: "button", "aria-current": last ? "true" : null, onclick: last ? null : () => go(c.z) }, c.label));
  });

  const notes = [];
  if (!doc.complete) notes.push(`${dash(doc.fiscalYear)} is still in progress: these figures run from July ${doc.beginYear} through ${doc.through}, and recent months may still change.`);
  const latest = index.years.find((y) => y.complete) || index.years[0];
  const me = index.years.find((y) => y.fiscalYear === doc.fiscalYear);
  if (me && latest && me.departments < latest.departments * 0.75) {
    notes.push(`Fewer departments appear in Open FI$Cal for ${dash(doc.fiscalYear)} (${me.departments}) than for ${dash(latest.fiscalYear)} (${latest.departments}), so its totals are not comparable with later years.`);
  }
  const notice = $("#state-notice");
  notice.textContent = notes.join(" ");
  notice.hidden = !notes.length;

  // panel below the chart
  const below = $("#state-below");
  below.replaceChildren();
  if (zoom.type === "fund") {
    const rows = doc.fundDetail.filter((r) => r[0] === zoom.f).slice(0, 12);
    below.append(el("div", { class: "panel" },
      el("h2", { text: `Largest funds in ${fundName(zoom.f)}` }),
      el("p", { class: "panel-sub", text: `Net spending charged to each fund in ${dash(doc.fiscalYear)}.` }),
      el("table", { class: "data" },
        el("thead", {}, el("tr", {}, el("th", { text: "Fund" }), el("th", { text: "Code" }), el("th", { class: "num", text: "Amount" }))),
        el("tbody", {}, rows.map((r) => el("tr", {}, el("td", { text: r[2] || "—" }), el("td", { text: r[1] }), el("td", { class: "num", text: money(r[3]) })))))));
  }
  const excluded = Math.abs(prep.excluded);
  below.append(el("p", { class: "small-note" },
    `Source: Open FI$Cal monthly spending files, ${doc.totals.rows.toLocaleString("en-US")} transactions. `,
    excluded >= 1 ? `Net-negative fund amounts left out of the drawing this year: ${money(excluded)}. ` : "",
    `Processed ${new Date(doc.generatedAt).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" })}.`));
}

// ---------------------------------------------------------------- state + routing
function zoomToHash() {
  const base = { view: "state", fy: doc?.fiscalYear };
  if (zoom.type === "agency") base.agency = doc.agencies[zoom.a];
  if (zoom.type === "fund") base.fund = doc.funds[zoom.f];
  if (zoom.type === "dept") base.dept = doc.departments[zoom.d][0] + ":" + doc.departments[zoom.d][1];
  return base;
}

function zoomFromHash(h) {
  if (h.agency) { const a = doc.agencies.indexOf(h.agency); if (a >= 0) return { type: "agency", a }; }
  if (h.fund) { const f = doc.funds.indexOf(h.fund); if (f >= 0) return { type: "fund", f }; }
  if (h.dept) {
    const exact = doc.departments.findIndex((x) => `${x[0]}:${x[1]}` === h.dept);
    const byBu = exact >= 0 ? exact : doc.departments.findIndex((x) => x[0] === h.dept.split(":")[0]);
    if (byBu >= 0) return { type: "dept", d: byBu };
  }
  return { type: "all" };
}

function go(z) {
  if (!z) return;
  hideTip();
  zoom = z;
  writeHash(zoomToHash(), { replace: false });
  draw();
  $("#state-headline").scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}

function draw() {
  renderText();
  renderSankey();
}

async function loadYear(fy, h = {}) {
  const entry = index.years.find((y) => y.fiscalYear === fy) || index.years.find((y) => y.fiscalYear === index.default) || index.years[0];
  const frame = $("#sankey-frame");
  frame.style.opacity = doc ? "0.55" : "";
  doc = await loadJSON(DATA + entry.file);
  prep = prepare(doc);
  frame.style.opacity = "";
  $("#state-year").value = doc.fiscalYear;
  zoom = zoomFromHash(h);
  writeHash(zoomToHash());
  draw();
}

export async function showState() {
  const h = readHash();
  if (!mounted) {
    mounted = true;
    try {
      index = await loadJSON(DATA + "index.json");
    } catch (e) {
      $("#state-headline").textContent = "State spending data is not available yet.";
      $("#state-sub").textContent = "The monthly update job has not finished its first run. Check back after it completes.";
      $("#sankey-frame").hidden = true;
      return;
    }
    const sel = $("#state-year");
    for (const y of index.years) {
      sel.append(el("option", { value: y.fiscalYear }, `${dash(y.fiscalYear)}${y.complete ? "" : " (in progress)"}`));
    }
    sel.addEventListener("change", () => loadYear(sel.value, {}).then(() => writeHash(zoomToHash(), { replace: false })));
    $("#state-table-toggle").addEventListener("click", (e) => {
      showTable = !showTable;
      e.currentTarget.setAttribute("aria-pressed", String(showTable));
      e.currentTarget.textContent = showTable ? "Hide table" : "Show as table";
      renderSankey();
    });
    let t;
    window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => { if (!$("#state-view").hidden && doc) renderSankey(); }, 150); });
    window.addEventListener("themechange", () => { if (!$("#state-view").hidden && doc) renderSankey(); });
  }
  if (!index) return;
  const fy = h.fy || index.default;
  if (!doc || doc.fiscalYear !== fy) await loadYear(fy, h);
  else { zoom = zoomFromHash(h); draw(); }
}
