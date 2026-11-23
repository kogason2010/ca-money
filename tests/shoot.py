"""Screenshot the site at desktop and phone sizes, light and dark, and report console errors.

    python tests/shoot.py http://127.0.0.1:8899/ /tmp/shots [hash ...]
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

base, out = sys.argv[1], Path(sys.argv[2])
hashes = sys.argv[3:] or ["#view=state", "#view=local"]
out.mkdir(parents=True, exist_ok=True)
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    for scheme in ("light", "dark"):
        for name, vp in (("desktop", {"width": 1366, "height": 900}), ("phone", {"width": 390, "height": 844})):
            ctx = browser.new_context(viewport=vp, color_scheme=scheme, device_scale_factor=1)
            page = ctx.new_page()
            page.on("console", lambda m, n=name: m.type in ("error", "warning") and errors.append(f"[{n}] {m.type}: {m.text}"))
            page.on("pageerror", lambda e, n=name: errors.append(f"[{n}] pageerror: {e}"))
            for h in hashes:
                page.goto(base + h)
                page.wait_for_timeout(900)
                slug = h.replace("#", "").replace("=", "-").replace("&", "_")[:60] or "home"
                page.screenshot(path=str(out / f"{slug}-{name}-{scheme}.png"), full_page=True)
            ctx.close()
    browser.close()
print("\n".join(errors) if errors else "no console errors")
