"""RA-004 visual and accessibility check.

Starts the local service against a temporary database, renders the app with
Playwright/Chromium at the reference viewport and compares panel geometry with
assets/reference/measurements.json. Writes artifacts to the given output folder.

Usage: python scripts/visual_check.py <output-dir> [--chromium PATH]
"""
import argparse, json, subprocess, sys, tempfile, time
from pathlib import Path
from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "assets/reference"
SELECTORS = {
    "topbar": "header",
    "navigation": "nav",
    "knowledgeMap": "section.map",
    "researchRoadmap": "section.roadmap",
    "focus": "aside section.focus",
    "selectedNode": "aside section.inspector",
    "quickNote": "aside section.note",
}


def start_service(db):
    proc = subprocess.Popen([sys.executable, "-m", "services.atlas.server", "--port", "0", "--db", db],
                            cwd=ROOT, stdout=subprocess.PIPE, text=True)
    return proc, proc.stdout.readline().strip().split()[-1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--chromium")
    args = parser.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    meas = json.loads((REF / "measurements.json").read_text(encoding="utf-8"))
    ax, ay, aw, ah = meas["regions"]["application"]
    ref = Image.open(REF / "reasoning-atlas-windows-mockup.png").convert("RGB").crop((ax, ay, ax + aw, ay + ah))
    ref.save(out / "reference-crop.png")
    report = {"viewport": [aw, ah], "regions": {}, "checks": {}}

    with tempfile.TemporaryDirectory() as folder:
        proc, url = start_service(folder + "/visual.db")
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=args.chromium) if args.chromium else pw.chromium.launch()
                page = browser.new_page(viewport={"width": aw, "height": ah})
                page.goto(url); page.wait_for_selector("text=Local", timeout=10000); time.sleep(0.5)
                page.screenshot(path=out / "implementation-1394x855.png")
                for name, sel in SELECTORS.items():
                    box = page.locator(sel).first.bounding_box()
                    rx, ry, rw, rh = meas["regions"][name]
                    expected = [rx - ax, ry - ay, rw, rh]
                    actual = [round(box[k]) for k in ("x", "y", "width", "height")]
                    delta = [a - e for a, e in zip(actual, expected)]
                    report["regions"][name] = {"expected": expected, "actual": actual, "delta": delta,
                        "widthWithin2pct": abs(delta[2]) <= 0.02 * aw, "heightWithin2pct": abs(delta[3]) <= 0.02 * ah}

                # Keyboard: Tab reaches graph nodes and Enter selects one.
                page.locator(".graph-node").nth(2).focus()
                page.keyboard.press("Enter")
                report["checks"]["keyboardSelectsNode"] = page.locator(".inspector h3").inner_text().strip().endswith("Symbolic Logic")
                page.keyboard.press("/")
                report["checks"]["slashFocusesSearch"] = page.evaluate("document.activeElement.id") == "search"
                page.keyboard.type("evolution")
                report["checks"]["searchFiltersMap"] = page.locator(".graph-node.filtered").count() == 8
                page.screenshot(path=out / "search-filter.png")
                page.fill("#search", "")
                fresh = browser.new_page(viewport={"width": aw, "height": ah})
                fresh.goto(url); fresh.wait_for_selector("text=Local", timeout=10000)
                tabs = []
                for _ in range(4):
                    fresh.keyboard.press("Tab")
                    tabs.append(fresh.evaluate("document.activeElement.getAttribute('aria-label')||document.activeElement.textContent.trim().slice(0,30)"))
                report["checks"]["tabOrderStart"] = tabs
                report["checks"]["skipLinkFirst"] = tabs[0] == "Skip to workspace"
                fresh.close()
                report["checks"]["unbuiltLabeled"] = page.locator(".nav-item.unbuilt").count()
                page.locator(".nav-item.unbuilt").first.click(force=True)  # aria-disabled, still opens its placeholder
                report["checks"]["unbuiltPlaceholder"] = page.locator("text=Not built yet").count() > 0
                page.locator(".progress-link").click(); time.sleep(0.3)
                report["checks"]["progressTasksShown"] = page.locator("article.task").count()
                page.screenshot(path=out / "build-progress.png")

                # Offline state: stop the service and wait for the banner.
                page.locator(".nav-item").first.click()
                proc.kill(); proc.wait()
                page.wait_for_selector("text=Local service is offline", timeout=12000)
                report["checks"]["offlineBannerShown"] = True
                page.screenshot(path=out / "offline.png")
                proc, url = start_service(folder + "/visual.db")

                for label, size, scale in (("1280x800", (1280, 800), 1), ("zoom200", (697, 427), 2)):
                    p = browser.new_page(viewport={"width": size[0], "height": size[1]}, device_scale_factor=scale)
                    p.goto(url); p.wait_for_selector("text=Local", timeout=10000); time.sleep(0.4)
                    overflow = p.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                    report["checks"][f"{label}HorizontalOverflowPx"] = overflow
                    p.screenshot(path=out / f"{label}.png", full_page=True)
                browser.close()
        finally:
            proc.kill(); proc.wait()

    impl = Image.open(out / "implementation-1394x855.png").convert("RGB")
    side = Image.new("RGB", (aw * 2 + 20, ah), (20, 20, 20))
    side.paste(ref, (0, 0)); side.paste(impl, (aw + 20, 0))
    draw = ImageDraw.Draw(side)
    for name, r in report["regions"].items():
        ex, ey, ew, eh = r["expected"]
        for off in (0, aw + 20):
            draw.rectangle((ex + off, ey, ex + off + ew, ey + eh), outline=(0, 255, 160), width=2)
        x, y, w, h = r["actual"]
        draw.rectangle((x + aw + 20, y, x + aw + 20 + w, y + h), outline=(255, 60, 60), width=2)
    side.save(out / "side-by-side.png")
    Image.blend(ref, impl, 0.5).save(out / "overlay.png")
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
