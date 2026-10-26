"""Dashboard layout: Today, Forge (stages Ideas → Plan → Build), Research and System; each function in exactly one place."""
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

PAGE = (Path(__file__).resolve().parent.parent / "engine" / "dashboard.html").read_text()


class Ids(HTMLParser):
    """Collects every element id and the top-level section it sits in."""

    def __init__(self):
        super().__init__()
        self.ids, self.sections, self.where, self.section = [], [], {}, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "section":
            self.section = a.get("id")
            self.sections.append(self.section)
        if a.get("id"):
            self.ids.append(a["id"])
            self.where[a["id"]] = self.section

    def handle_endtag(self, tag):
        if tag == "section":
            self.section = None


class DashboardLayoutTest(unittest.TestCase):
    def setUp(self):
        self.p = Ids()
        self.p.feed(PAGE.split("<script>")[0])
        self.script = PAGE.split("<script>", 1)[1]

    def test_main_areas_and_forge_stages(self):
        self.assertEqual(self.p.sections, ["today", "plan", "build", "brain", "research", "system"])
        tabs = re.search(r"const TABS = (\[.*?\]);", self.script).group(1)
        self.assertEqual(re.findall(r'\["(\w+)",', tabs), ["today", "forge", "research", "system"])
        stages = re.search(r"const STAGES = (\[.*?\]);", self.script).group(1)
        self.assertEqual(re.findall(r'\["(\w+)","(\w+)"\]', stages), [("brain", "Ideas"), ("plan", "Plan"), ("build", "Build")])

    def test_every_sub_view_is_declared(self):
        views = re.search(r"const VIEWS = (\{.*?\]\]\});", self.script, re.S).group(1)
        for area in ("plan", "build", "research", "system"):
            body = PAGE.split(f'<section id="{area}"', 1)[1].split("</section>", 1)[0]
            declared = re.findall(r'\["(\w+)","', re.search(area + r":\[(.*?\])\]", views, re.S).group(1))
            self.assertEqual(re.findall(r'data-view="(\w+)"', body), declared, area)

    def test_today_has_no_detours_to_other_areas(self):
        today = PAGE.split('<section id="today"', 1)[1].split("</section>", 1)[0]
        self.assertNotIn("setTab(", today)
        for gone in ("tState", "agentLine"):
            self.assertNotIn(gone, self.p.ids)

    def test_ids_unique_and_every_script_reference_exists(self):
        dupes = {i for i in self.p.ids if self.p.ids.count(i) > 1}
        self.assertFalse(dupes, f"duplicate ids: {dupes}")
        made_by_script = set(re.findall(r'id="([\w-]+)"', self.script))  # e.g. the research settings form
        missing = set(re.findall(r'\$\("([\w-]+)"\)', self.script)) - set(self.p.ids) - made_by_script
        self.assertFalse(missing, f"script refers to ids not on the page: {missing}")
        areas = set(re.findall(r'\["(\w+)",', re.search(r"const TABS = (\[.*?\]);", self.script).group(1)))
        for target in re.findall(r"setTab\('(\w+)'", PAGE):
            self.assertIn(target, set(self.p.sections) | areas)

    def test_each_function_in_its_tab(self):
        home = {
            "today": ["glance", "dump", "mic", "needs", "proposed", "suggest", "recommended", "overdue"],
            "plan": ["goals", "phases", "allList", "tiles", "bars", "review", "dueSoon"],
            "build": ["pipeline", "agentLog", "agentLanes", "agentQueue", "agentConflicts", "tree", "agentRuns", "projList"],
            "brain": ["ideaList", "brainChanges", "savedFindings"],
            "research": ["researchFeed", "radarFilters", "researchSaved", "deepQ", "researchNotes", "researchForm"],
            "system": ["claudeConnection", "killBtn", "auto", "constitution", "updBody", "themeSeg", "recoveryCard", "usageTiles", "resList", "log"],
        }
        for section, ids in home.items():
            for i in ids:
                self.assertEqual(self.p.where.get(i), section, i)

    def test_duplicates_removed(self):
        for gone in ("radarTop", "todayIdeas", "goalProgress", "buildQueue"):
            self.assertNotIn(gone, self.p.ids)
        # one Re-evaluate button, one "+ Idea" button, one kill switch, one autonomy slider
        # it is the do-over-aware one (#36) and sits on Today next to the recommendations
        self.assertEqual(PAGE.count('onclick="replan(this)"'), 1)
        self.assertNotIn('rethink(', PAGE)
        self.assertLess(PAGE.index('id="today"'), PAGE.index('onclick="replan(this)"'))
        self.assertLess(PAGE.index('onclick="replan(this)"'), PAGE.index('id="plan"'))
        self.assertEqual(PAGE.count("openNew('ideas')"), 1)
        self.assertEqual(PAGE.count('onclick="killSwitch()"'), 1)
        self.assertEqual(PAGE.count('type="range"'), 1)

    def test_every_css_variable_is_defined(self):
        """An undefined var() makes a background transparent: the help and briefing dialogs became unreadable."""
        used = {v for v in re.findall(r"var\((--[\w-]+)", PAGE) if not v.endswith("-")}  # "--st-${key}" is filled in by script
        undefined = {v for v in used if not re.search(re.escape(v) + r"\s*:", PAGE)}
        self.assertFalse(undefined, f"CSS variables used but never defined: {undefined}")

    def test_old_saved_tabs_map_to_new_ones(self):
        old = re.search(r"const OLD_TABS = (\{.*?\});", self.script).group(1)
        for name in ("progress", "agent", "radar", "ideas", "all", "resources"):
            self.assertIn(f"{name}:", old)


if __name__ == "__main__":
    unittest.main()


class DashboardLookTest(unittest.TestCase):
    def test_tab_colours_status_and_theme(self):
        for tab in ("today", "forge", "research", "system"):
            self.assertIn(f"body[data-tab={tab}]", PAGE)
        for stage in ("brain", "plan", "build"):  # each Forge stage keeps its own colour
            self.assertIn(f"body[data-stage={stage}]", PAGE)
        for c in ("--ok:", "--wait:", "--bad:"):
            self.assertGreaterEqual(PAGE.count(c), 3)  # light, auto-dark, forced dark
        self.assertIn(':root[data-theme=dark]', PAGE)
        self.assertIn("function setTheme", PAGE)  # chosen under System → Appearance, not in the header
        self.assertNotIn('id="themeBtn"', PAGE)
        self.assertIn("min-height:42px", PAGE)
