// Shared helpers: formatting, DOM, tooltip, URL state, data loading.

export const $ = (sel, root = document) => root.querySelector(sel);

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

// ---------------------------------------------------------------- numbers
export function money(v, { digits } = {}) {
  if (v == null || !isFinite(v)) return "—";
  const a = Math.abs(v), sign = v < 0 ? "−" : "";
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(digits ?? (a >= 1e11 ? 0 : 1))}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(digits ?? (a >= 1e8 ? 0 : 1))}M`;
  if (a >= 1e4) return `${sign}$${Math.round(a / 1e3)}K`;
  return `${sign}$${Math.round(a).toLocaleString("en-US")}`;
}

export function moneyWords(v) {
  const a = Math.abs(v);
  if (a >= 1e9) return `$${(a / 1e9).toFixed(1)} billion`;
  if (a >= 1e6) return `$${(a / 1e6).toFixed(1)} million`;
  return `$${Math.round(a).toLocaleString("en-US")}`;
}

export const perRes = (v) => (v == null || !isFinite(v) ? "—" : `$${Math.round(v).toLocaleString("en-US")}`);
export const pct = (v) => (v == null || !isFinite(v) ? "—" : v >= 0.1 ? `${(v * 100).toFixed(0)}%` : v >= 0.001 ? `${(v * 100).toFixed(1)}%` : "<0.1%");
export const count = (v) => Math.round(v).toLocaleString("en-US");
export const dash = (label) => label.replace(/(\d{4})-(\d{2})/, "$1–$2");

export function median(values) {
  const a = values.filter((v) => v != null && isFinite(v)).sort((x, y) => x - y);
  if (!a.length) return null;
  const m = a.length >> 1;
  return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
}

export function truncate(text, max) {
  return text.length > max ? text.slice(0, max - 1).trimEnd() + "…" : text;
}

// ---------------------------------------------------------------- tooltip
const tip = () => document.getElementById("tooltip");

/** rows: array of [className, text] or {key: color, text} */
export function showTip(event, rows) {
  const t = tip();
  t.replaceChildren();
  for (const r of rows) {
    if (!r) continue;
    const line = el("div", { class: r.cls || "" });
    if (r.key) line.append(el("span", { class: "tt-key", style: `background:${r.key}` }));
    line.append(document.createTextNode(r.text));
    t.append(line);
  }
  t.hidden = false;
  moveTip(event);
}

export function moveTip(event) {
  const t = tip();
  if (t.hidden) return;
  let x, y;
  if (event && "clientX" in event && event.clientX) { x = event.clientX; y = event.clientY; }
  else if (event?.target?.getBoundingClientRect) {
    const r = event.target.getBoundingClientRect();
    x = r.right; y = r.top + r.height / 2;
  } else return;
  const w = t.offsetWidth, h = t.offsetHeight, pad = 14;
  let left = x + pad, top = y + pad;
  if (left + w > window.innerWidth - 8) left = x - w - pad;
  if (top + h > window.innerHeight - 8) top = y - h - pad;
  t.style.left = `${Math.max(8, left)}px`;
  t.style.top = `${Math.max(8, top)}px`;
}

export function hideTip() { tip().hidden = true; }

// ---------------------------------------------------------------- URL state
export function readHash() {
  return Object.fromEntries(new URLSearchParams(location.hash.slice(1)));
}

export function writeHash(state, { replace = true } = {}) {
  const clean = Object.fromEntries(Object.entries(state).filter(([, v]) => v != null && v !== ""));
  const next = "#" + new URLSearchParams(clean).toString();
  if (next === location.hash) return;
  if (replace) history.replaceState(null, "", next);
  else history.pushState(null, "", next);
}

// ---------------------------------------------------------------- data
const cache = new Map();
export function loadJSON(path) {
  if (!cache.has(path)) {
    cache.set(path, fetch(path, { cache: "no-cache" }).then((r) => {
      if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
      return r.json();
    }).catch((e) => { cache.delete(path); throw e; }));
  }
  return cache.get(path);
}

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function svgEl(tag, attrs = {}) {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, v);
  return n;
}
