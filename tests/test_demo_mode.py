"""Tests for the web console's offline Demo Mode (`#/demo` in app.js).

Demo Mode is a hand-authored, read-only replay shown without any ChatGPT
Tunnel, Codex, or Claude Code connection. It must never call the backend API,
must never touch a real project/task, and must clearly label itself as
sample data — not a live AI session. Since `webui_static/app.js` is plain,
build-free JavaScript with no frontend test runner in this repo, these tests
verify that boundary the same way a reviewer would: by inspecting the
`renderDemo`/`demoFixtureEvents` function bodies in the shipped source.
"""

from pathlib import Path

APP_JS = (
    Path(__file__).resolve().parents[1]
    / "src/ccc_mcp/interfaces/webui_static/app.js"
)


def _function_body(name: str) -> str:
    """The exact source text of a top-level `function <name>(...) { ... }`,
    found by brace-matching from its opening `{`."""
    source = APP_JS.read_text(encoding="utf-8")
    marker = f"function {name}("
    start = source.index(marker)
    brace_start = source.index("{", start)
    depth = 0
    for i in range(brace_start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[brace_start : i + 1]
    raise AssertionError(f"Unbalanced braces while extracting function {name}")


def test_demo_page_makes_no_backend_calls():
    body = _function_body("renderDemo")
    assert "api(" not in body, "Demo Mode must never call the backend API"
    assert "fetch(" not in body, "Demo Mode must never make its own network request"


def test_demo_fixture_data_makes_no_backend_calls():
    body = _function_body("demoFixtureEvents")
    assert "api(" not in body
    assert "fetch(" not in body


def test_demo_page_is_clearly_labeled_as_sample_data():
    body = _function_body("renderDemo")
    assert "Demo Replay" in body
    assert "sample data, not a live AI session" in body
    assert "Set up a real project" in body


def test_demo_route_is_registered_independently_of_any_project():
    source = APP_JS.read_text(encoding="utf-8")
    # The route must resolve before the `!r.project` gate, so it never
    # requires (or implies) a registered/authorized project to open.
    route_fn = source[source.index("function route() {") : source.index("function route() {") + 2000]
    demo_line = route_fn.index('r.view === "demo"')
    project_gate_line = route_fn.index("!r.project")
    assert demo_line < project_gate_line
