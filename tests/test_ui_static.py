"""Guards for the console's shape: three places, no reload-based updates, old addresses still land.

These read the shipped JavaScript. They cannot prove how a page behaves in a browser (that was checked
by driving a real browser; see the refactor report), but they stop the patterns that broke it from
coming back.
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "ccc_mcp" / "interfaces" / "webui_static"
SCRIPTS = sorted(STATIC.glob("*.js"))


def text(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_no_script_reloads_or_navigates_the_whole_page_to_show_new_state():
    forbidden = re.compile(
        r"location\s*\.\s*(reload|assign)\b|window\s*\.\s*location\s*=|location\s*\.\s*href\s*=|"
        r"history\s*\.\s*go\b|http-equiv", re.I)
    offenders = [f"{s.name}: {m.group(0)}" for s in SCRIPTS
                 for m in forbidden.finditer(s.read_text(encoding="utf-8"))]
    assert offenders == []
    assert "http-equiv" not in text("index.html")


def test_background_polling_goes_through_the_one_live_poll_helper():
    """A `setInterval` poller that repaints the page is how updates used to wipe what a person was
    doing. Pages poll with `livePoll` (backoff, a note when offline, nothing painted on failure)."""
    for name in ("tasks.js", "goals.js", "goalrun.js", "phases.js", "morph.js"):
        assert "setInterval" not in text(name), name
    app = text("app.js")
    assert app.count("setInterval") == 1   # the one-shot Codex/Claude sign-in log, patched in place
    assert "function livePoll" in app


def test_the_sidebar_has_exactly_three_places():
    app = text("app.js")
    items = re.findall(r'class="nav-item" data-nav="(\w+)"', app)
    assert items == ["tasks", "projects", "settings"]


def test_goals_are_not_a_separate_place():
    app = text("app.js")
    assert "goals" not in re.findall(r'data-nav="(\w+)"', app)
    # An old /goals address resolves to the Tasks page or a goal under it, never to its own page.
    assert 'view === "tasks" || view === "goals"' in app
    assert "renderGoals" not in text("goals.js")


def test_a_repainted_block_never_stacks_handlers_and_never_overwrites_what_is_typed():
    morph = text("morph.js")
    # field values and the open state of a <details> are never written by a morph
    assert '"value"' in morph and '"checked"' in morph and "DETAILS" in morph
    for name in ("phases.js", "goalrun.js"):
        assert re.search(r'addEventListener\("click"', text(name)) is None, name


def test_every_script_in_index_exists_and_load_order_puts_helpers_first():
    html = text("index.html")
    order = re.findall(r'<script src="([^"]+)"', html)
    assert order[0] == "i18n.js" and order[-1] == "app.js"
    assert order.index("morph.js") < order.index("tasks.js") < order.index("app.js")
    assert all((STATIC / name).is_file() for name in order)
    assert {p.name for p in SCRIPTS} == set(order)


def test_removed_pages_have_no_leftover_entry_points():
    app = text("app.js")
    for gone in ("renderDashboard", "renderRoleStudio", "renderDocs", "renderGlobalSkills",
                 "renderSkills", "renderStart", "renderDemo", "watchForChanges", "reload-banner"):
        assert gone not in app, gone
