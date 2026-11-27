"""Click through the main interactions and screenshot each step.

    python tests/interact.py http://127.0.0.1:8899/ /tmp/shots-i
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

base, out = sys.argv[1], Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
errors = []


def shot(page, name):
    page.wait_for_timeout(500)
    page.screenshot(path=str(out / f"{name}.png"), full_page=True)


with sync_playwright() as p:
    b = p.chromium.launch()
    for label, vp in (("desktop", {"width": 1366, "height": 900}), ("phone", {"width": 390, "height": 844})):
        page = b.new_page(viewport=vp)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(base + "#view=state")
        page.wait_for_selector("#sankey .node")
        # zoom into the biggest agency (node with role=button and kind agency is 2nd column)
        agency = page.locator("#sankey .node[role=button]").nth(6)
        agency_rect = agency.locator("rect.mark")
        name = agency.get_attribute("aria-label")
        agency_rect.click()
        page.wait_for_timeout(400)
        print(label, "clicked:", name, "->", page.locator("#state-headline").inner_text())
        shot(page, f"{label}-agency")
        # zoom into a department from here
        dept = page.locator("#sankey .node[role=button]").nth(5 if label == "desktop" else 5)
        dept.locator("rect.mark").click()
        page.wait_for_timeout(400)
        print(label, "dept:", page.locator("#state-headline").inner_text())
        print(label, "hash:", page.evaluate("location.hash"))
        shot(page, f"{label}-dept")
        page.click("#state-table-toggle")
        shot(page, f"{label}-dept-table")
        page.go_back()
        page.wait_for_timeout(500)
        print(label, "after back:", page.locator("#state-headline").inner_text())
        # fund view via keyboard
        page.goto(base + "#view=state")
        page.wait_for_selector("#sankey .node")
        page.locator("#sankey .node").first.focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
        print(label, "fund:", page.locator("#state-headline").inner_text())
        shot(page, f"{label}-fund")
        # local: counties, choose Fresno, police-like service
        page.goto(base + "#view=local&kind=counties")
        page.wait_for_selector("#profile-chart svg")
        page.fill("#entity-input", "Fresno")
        page.press("#entity-input", "Enter")
        page.wait_for_timeout(400)
        page.locator("#service-chips button", has_text="Police protection").click()
        print(label, "county:", page.locator("#local-headline").inner_text(), page.evaluate("location.hash"))
        shot(page, f"{label}-counties")
        # local: cities, nearest comparison, pick from rank chart
        page.goto(base + "#view=local&kind=cities&e=Pasadena&s=police&cmp=near")
        page.wait_for_selector("#rank-chart svg")
        page.locator("#rank-chart .bar-row").nth(0).click()
        page.wait_for_timeout(400)
        print(label, "picked:", page.locator("#local-headline").inner_text())
        shot(page, f"{label}-cities-near")
        page.close()
    b.close()
print("\n".join(errors) if errors else "no errors")
