"""Guards for the console's shape: four places, no reload-based updates, old addresses still land.

These read the shipped JavaScript. They cannot prove how a page behaves in a browser (that was checked
by driving a real browser; see the refactor report), but they stop the patterns that broke it from
coming back.
"""

import json
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


def test_the_sidebar_has_exactly_four_places():
    app = text("app.js")
    items = re.findall(r'class="nav-item" data-nav="(\w+)"', app)
    assert items == ["tasks", "projects", "skills", "settings"]


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
    for gone in ("renderDashboard", "renderRoleStudio", "renderDocs",
                 "renderStart", "renderDemo", "watchForChanges", "reload-banner"):
        assert gone not in app, gone


def _quoted(block):
    return [json.loads(m.group(0)) for m in re.finditer(r'"(?:[^"\\\n]|\\.)*"', block)]


def test_every_role_workflow_and_selection_mode_is_explained_in_both_languages():
    """The team card explains each choice in words, and every sentence has a Chinese translation."""
    from ccc_mcp.core.flows import BUILTIN_FLOWS, SELECTION_MODES
    from ccc_mcp.core.roles import ROLE_CATALOG

    team, i18n = text("team.js"), text("i18n.js")
    roles = team[team.index("const ROLE_HELP"):team.index("const roleHelp")]
    flows = team[team.index("const FLOW_HELP"):team.index("const flowTag")]
    modes = team[team.index("const SELECTION"):team.index("// ---- models and effort")]
    for role in set(ROLE_CATALOG) - {"PM"}:
        assert re.search(rf'(?:^|\s)"?{re.escape(role)}"?: "', roles, re.M), role
    for flow in BUILTIN_FLOWS:
        assert re.search(rf"^\s+{flow}: {{ tag:", flows, re.M), flow
    for mode in SELECTION_MODES:
        assert re.search(rf"^\s+{mode}: \[", modes, re.M), mode
    sentences = [q for block in (roles, flows, modes) for q in _quoted(block)
                 if len(q) > 12 and " " in q and q not in {"Provider default"}]
    assert len(sentences) >= 19 + 14 + 6
    missing = [q for q in sentences if json.dumps(q, ensure_ascii=False) + ":" not in i18n
               and json.dumps(q) + ":" not in i18n]
    assert missing == []


def test_the_model_field_is_a_list_read_from_the_cli_not_a_blank_to_type_into():
    team = text("team.js")
    assert 'api("/provider-models")' in team
    assert "CUSTOM" in team and "Other (type a name)" in team   # a typed name stays possible
    assert 'type="text" data-field="model"' in team            # ... and is the fallback with no list
    assert "/provider-models" not in text("app.js")


def test_skills_and_mcp_have_their_own_page_again():
    app, skills = text("app.js"), text("skills.js")
    assert 'parts[0] === "skills"' in app and "skillsUi.render()" in app
    for path in ("/skills/discover", "/skills/install-discovered", "/mcp/probe", "/mcp/install",
                 "/mcp/probe-by-name", "/mcp/install-by-name", "/mcp/calls"):
        assert path in skills, path


def test_the_provider_default_says_what_it_actually_is():
    team = text("team.js")
    assert "defaultLabel(defaultModel(provider))" in team
    assert "defaultLabel(defaultEffort(provider, model))" in team
    assert '"decided by the CLI": "' in text("i18n.js")


def test_a_task_between_steps_says_who_has_to_act_and_marks_the_step():
    app = text("app.js")
    for kind in ("pm", "question", "handoff", "retry", "running"):
        assert f"{kind}: [" in app
    assert 'class="task-next waiting ${waitingKind}"' in app
    assert " current running" in app and " current waiting" in app
    assert "/resume-dispatch" in app and 'id="task-resume-dispatch"' in app
    zh = text("i18n.js")
    for sentence in ("Waiting for ChatGPT (the PM)", "{role} did not start", "Start it again"):
        assert f'"{sentence}": "' in zh


def test_the_task_page_names_its_workflow_and_calls_roles_what_the_team_card_does():
    app = text("app.js")
    assert 'class="task-workflow"' in app and "workflow.selected_by" in app
    assert "const roleTitle" in app and "ROLE_TITLES" in app
    assert "roleTitle(role), flowDone(index)" in app      # the step list, not the bare code
    zh = text("i18n.js")
    for sentence in ("Picked by ccc-mcp from the task text", "The project's chosen workflow"):
        assert f'"{sentence}": "' in zh


def test_known_failure_reasons_are_explained_in_the_chat_and_in_the_technical_list():
    app, i18n = text("app.js"), text("i18n.js")
    assert "possible secret detected|execution timed out" in app
    for reason in ("possible secret detected", "execution timed out", "stopped by user|execution cancelled"):
        assert reason in i18n


def test_a_failed_package_install_is_not_reported_as_a_failed_test():
    app, i18n = text("app.js"), text("i18n.js")
    assert "function setupFailed" in app and "deps.installed === false" in app
    for key in ("Test environment setup failed", "The test environment could not be set up, so no test was run."):
        assert key in app and key in i18n


def test_the_test_language_and_version_card_is_visible_not_hidden_and_the_failure_links_to_it():
    app = text("app.js")
    assert 'id="project-runtime"' in app and "Runtime for tests (advanced)" not in app
    assert "data-focus-runtime" in app and "focusRuntime" in app
    assert app.index('id="project-runtime"') < app.index('${pageCard("language"')


def test_choosing_a_language_scans_for_installed_versions_and_manual_entry_stays():
    app = text("app.js")
    assert "/runtimes/discover?language=" in app and 'id="runtime-found"' in app
    assert 'id="runtime-version"' in app and 'id="runtime-executable"' in app
    for key in ("Installed on this computer", "Enter manually", "Scan this computer"):
        assert key in text("i18n.js")


def test_a_running_role_outranks_a_leftover_retry_record_in_the_waiting_card():
    app = text("app.js")
    assert 'handoff.waiting_on === "DISPATCH_RETRY_NEEDED" && runningExecution ? "AGENT_RUNNING"' in app
    assert "})[waitingOn]" in app


def test_a_task_blocked_on_unchecked_files_shows_a_clear_label_and_the_files():
    app, i18n = text("app.js"), text("i18n.js")
    assert "NEEDS_CONFIRMATION" in app and "status.brief?.comment_review" in app
    for key in ("NEEDS_CONFIRMATION", "Some changed files need your look"):
        assert key in i18n


def test_the_chat_room_grows_so_alerts_and_the_composer_never_squeeze_the_messages_out():
    css = text("app.css")
    tail = css[css.rindex("The chat room grows with what is in it"):]
    assert "height: auto" in tail and "overflow: clip" in tail
    assert ".conversation-feed" in tail and "min-height" in tail
    assert ".task-next { max-height" in tail and "overflow-y: auto" in tail


def test_the_task_page_can_start_the_host_tests_itself():
    app, i18n = text("app.js"), text("i18n.js")
    assert 'id="task-run-tests"' in app and "/run-tests" in app and "hostTestKickoff" in app
    for key in ("The host tests have not been run yet", "Run host tests", "The host tests are running."):
        assert key in i18n


def test_the_running_host_tests_card_comes_from_the_server_and_names_the_stage():
    app = text("app.js")
    assert "control.host_test_running" in app and "control.host_test_stage" in app
    assert "Installing the packages the project needs" in text("i18n.js")


def test_pressing_run_host_tests_repaints_the_cards_at_once_instead_of_leaving_the_button():
    app = text("app.js")
    assert "Boolean(testCard) || Boolean(reviewCard)" in app   # forces the full repaint, not the text-only one
    assert "await refresh(); setTimeout(refresh, 21000)" in app
    assert 'id="runtime-install"' in app and "install_packages: false" in app


def test_the_task_page_lists_what_is_missing_and_says_pm_means_chatgpt():
    app, i18n = text("app.js"), text("i18n.js")
    assert "What is still missing before this task can finish" in app
    for key in ("What is still missing before this task can finish", "The reviewer has not looked at the latest changes yet"):
        assert key in i18n
    assert "means ChatGPT, not you" in app


def test_removed_comments_and_unverifiable_files_are_listed_separately_with_what_to_do():
    app = text("app.js")
    assert "removedFiles" in app and "unverifiedFiles" in app
    assert "Ask the developer to redo it and keep the original comments" in text("i18n.js")


def test_the_person_can_close_a_task_and_it_shows_as_their_decision_not_as_verified():
    app, i18n = text("app.js"), text("i18n.js")
    assert 'id="task-close"' in app and "data-close-task" in app and "/close`" in app
    assert "CLOSED_BY_USER" in app and "CLOSED_BY_USER" in i18n
    assert "You closed this task. It was not verified by host tests." in i18n


def test_the_close_button_is_in_the_first_paint_of_the_head_and_in_the_live_update():
    app = text("app.js")
    assert app.count("${pauseButton}${closeButton}${stopButton}${deleteButton}") == 2
