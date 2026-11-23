// Routing between the two views, theme toggle.
import { readHash } from "./util.js";
import { showState } from "./state-view.js";
import { showLocal } from "./local-view.js";

async function route() {
  const view = readHash().view === "local" ? "local" : "state";
  document.getElementById("state-view").hidden = view !== "state";
  document.getElementById("local-view").hidden = view !== "local";
  document.querySelectorAll(".tabs a").forEach((a) => {
    if (a.dataset.view === view) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  document.title = view === "local"
    ? "City and county spending per resident · Where California's money goes"
    : "Where California's money goes";
  try {
    if (view === "local") await showLocal();
    else await showState();
  } catch (err) {
    console.error(err);
    const h = document.querySelector(`#${view}-view h1`);
    if (h) h.textContent = "Something went wrong loading this data.";
    const sub = document.querySelector(`#${view}-view .sub`);
    if (sub) sub.textContent = `${err.message}. Try reloading the page.`;
  }
}

// Tabs: switching views starts each view fresh from its own defaults.
document.querySelectorAll(".tabs a").forEach((a) => a.addEventListener("click", (e) => {
  e.preventDefault();
  if (readHash().view === a.dataset.view || (!readHash().view && a.dataset.view === "state")) return;
  history.pushState(null, "", `#view=${a.dataset.view}`);
  route();
}));
// Back/forward and edited URLs can fire both events; route once.
let pending = false;
const scheduleRoute = () => { if (pending) return; pending = true; setTimeout(() => { pending = false; route(); }, 0); };
window.addEventListener("popstate", scheduleRoute);
window.addEventListener("hashchange", scheduleRoute);

// Theme: follows the system until the person picks one.
document.querySelector(".theme-toggle").addEventListener("click", () => {
  const root = document.documentElement;
  const current = root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = current === "dark" ? "light" : "dark";
  root.dataset.theme = next;
  try { localStorage.setItem("ca-money-theme", next); } catch (e) { /* private mode */ }
  window.dispatchEvent(new Event("themechange"));
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => window.dispatchEvent(new Event("themechange")));

route();
