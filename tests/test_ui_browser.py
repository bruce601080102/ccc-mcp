# ruff: noqa: UP031
"""The console in a real browser: background updates never reload, never take away what a person is
typing or reading, and recover from a lost connection without repeating anything.

Headless Chrome is driven over the DevTools protocol (tests/browser_driver.py). The server is the real
console app on a local port; "the background" is a second service object writing to the same SQLite
home, exactly what ChatGPT's tools or a worker would do. No model is called. If no Chrome, Chromium
or Edge is installed these tests skip.
"""

import subprocess
import threading
import time

import pytest
import uvicorn

pytest.importorskip("aiohttp", reason="the browser driver needs aiohttp (pip install -e '.[dev]')")
from browser_driver import Browser, find_chrome, free_port  # noqa: E402
from test_goal_planner import good_plan

from ccc_mcp.interfaces.web import create_app
from ccc_mcp.service import CCCMCP

TOKEN = "browser-token"
pytestmark = pytest.mark.skipif(find_chrome() is None, reason="no Chrome, Chromium or Edge installed")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("browser")
    repo = root / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=T", "-c", "user.email=t@example.invalid",
                                                  "commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    home = root / "home"
    service = CCCMCP(home)
    service.register_project(repo, "demo", "browser tests")
    app = create_app(home, token=TOKEN)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    browser = Browser(find_chrome())
    background = CCCMCP(home)          # a second process-like writer on the same database
    yield {"base": f"http://127.0.0.1:{port}", "browser": browser, "bg": background, "repo": repo,
           "home": home, "repos": root}
    browser.close()
    server.should_exit = True
    thread.join(5)


def open_page(env, route):
    page = env["browser"].page("about:blank")
    page.goto(f"{env['base']}/#token={TOKEN}")
    page.wait_for("document.getElementById('sheet')")
    page.eval(f"location.hash = {route!r}; return 1;")
    return page


def mark(page):
    """Anything a reload would erase."""
    page.eval("window.__alive = Math.random(); return window.__alive;")
    return page.eval("return window.__alive;")


def not_reloaded(page, token):
    return page.eval("return window.__alive;") == token and page.eval(
        "return performance.getEntriesByType('navigation').length;") == 1


def caret(page, selector):
    return page.eval(f"const e = document.querySelector({selector!r}); "
                     "return [e.value, e.selectionStart, e.selectionEnd, document.activeElement === e];")


def test_tasks_page_updates_in_place_and_keeps_what_the_person_is_doing(env):
    bg = env["bg"]
    first = bg.start_goal_work("demo", "Build the invoicing module with tests", title="Invoicing",
                               plan=good_plan(2))
    page = open_page(env, "#/p/demo/tasks")
    page.wait_for("document.querySelector('[data-key=\"goal:%s\"]')" % first["id"])
    token = mark(page)

    page.eval("document.getElementById('work-text').focus(); return 1;")
    page.key("half a request")
    page.eval("const e = document.getElementById('work-text'); e.setSelectionRange(2, 6); return 1;")
    page.eval("document.querySelector('[data-toggle=\"%s\"]').click(); return 1;" % first["id"])
    assert page.eval("return document.querySelector('[data-key=\"goal:%s\"] .work-steps').hidden;"
                     % first["id"]) is True

    # background: a rename, a plan change, and a brand-new piece of work
    bg.rename_goal("demo", first["id"], "Invoicing (renamed elsewhere)")
    second = bg.start_goal_work("demo", "Write the quarterly tax export script", title="Tax export")
    page.wait_for("[...document.querySelectorAll('.work-group-title')]"
                  ".some(e => e.textContent.includes('renamed elsewhere')) && "
                  "document.querySelector('[data-key=\"goal:%s\"]')" % second["id"])

    assert not_reloaded(page, token)
    assert caret(page, "#work-text") == ["half a request", 2, 6, True]
    assert page.eval("return document.querySelector('[data-key=\"goal:%s\"] .work-steps').hidden;"
                     % first["id"]) is True           # the group stays collapsed
    page.close()


def test_a_goal_page_keeps_typed_text_focus_selection_and_open_sections(env):
    bg = env["bg"]
    goal = bg.start_goal_work("demo", "Add a refund workflow to the shop with tests", title="Refunds",
                              plan=good_plan(1))
    page = open_page(env, "#/p/demo/tasks/%s" % goal["id"])
    page.wait_for("document.getElementById('goal-change')")
    token = mark(page)
    page.eval("document.querySelector('details.goal-versions').open = true; "
              "const e = document.getElementById('goal-change'); e.focus(); return 1;")
    page.key("split phase one")
    page.eval("document.getElementById('goal-change').setSelectionRange(3, 8); return 1;")

    bg.plan_goal_work("demo", goal["id"], plan=good_plan(3), change_request="three phases")
    page.wait_for("document.querySelectorAll('.goal-phase').length === 3")

    assert not_reloaded(page, token)
    assert caret(page, "#goal-change") == ["split phase one", 3, 8, True]
    assert page.eval("return document.querySelector('details.goal-versions').open;") is True
    assert "(2)" in page.eval("return document.querySelector('details.goal-versions summary').textContent;")
    page.close()


def test_chat_does_not_snatch_the_scroll_position_and_a_lost_connection_repeats_nothing(env):
    bg = env["bg"]
    task = bg.create_task("demo", "Fix the rounding bug in totals")["id"]
    for i in range(30):
        bg.add_task_message("demo", task, f"early {i}: " + "lorem ipsum " * 12, pause=False)
    page = open_page(env, f"#/p/demo/tasks/{task}")
    page.wait_for("document.querySelectorAll('.conversation-feed > *').length >= 30")
    token = mark(page)
    page.eval("document.getElementById('task-message').focus(); return 1;")
    page.key("my correction")
    page.eval("const f = document.querySelector('.conversation-feed'); "
              "f.scrollTop = Math.floor((f.scrollHeight - f.clientHeight) / 2); return 1;")
    before = page.eval("return document.querySelector('.conversation-feed').scrollTop;")
    assert before > 100

    bg.add_task_message("demo", task, "arrived while reading", pause=False)
    page.wait_for("document.querySelector('.conversation-feed').textContent.includes('arrived while reading')")
    assert abs(page.eval("return document.querySelector('.conversation-feed').scrollTop;") - before) < 3
    assert page.eval("return !!document.querySelector('.feed-new-hint');") is True   # a hint, not a jump
    assert caret(page, "#task-message")[0] == "my correction"

    # the connection drops, messages keep arriving, then it comes back
    page.eval("window.__fetch = window.fetch; window.fetch = () => Promise.reject(new TypeError('offline')); return 1;")
    bg.add_task_message("demo", task, "during the outage", pause=False)
    page.wait_for("document.getElementById('live-offline')", timeout=30)
    assert "arrived while reading" in page.eval("return document.querySelector('.conversation-feed').textContent;")
    page.eval("window.fetch = window.__fetch; return 1;")
    page.wait_for("document.querySelector('.conversation-feed').textContent.includes('during the outage')", 40)
    page.wait_for("!document.getElementById('live-offline')")

    text = page.eval("return document.querySelector('.conversation-feed').textContent;")
    for phrase in ("arrived while reading", "during the outage"):
        assert text.count(phrase) == 1, phrase                 # nothing shown twice after reconnecting
    assert not_reloaded(page, token)
    page.close()


def test_two_tabs_see_the_same_state_and_an_outdated_action_is_refused_cleanly(env):
    bg = env["bg"]
    goal = bg.start_goal_work("demo", "Add rate limiting to the public API with tests", title="Rate limit",
                              plan=good_plan(1))
    a = open_page(env, "#/p/demo/tasks/%s" % goal["id"])
    b = open_page(env, "#/p/demo/tasks")
    a.wait_for("document.getElementById('goal-approve')")
    b.wait_for("document.querySelector('[data-key=\"goal:%s\"]')" % goal["id"])

    # tab B renames it through the console's own API; tab A shows the new name without a reload
    token = mark(a)
    b.eval("const r = await fetch('/api/projects/demo/goals/%s/rename', {method: 'POST', "
           "headers: {'X-CCC-UI-Token': '%s', 'Content-Type': 'application/json'}, "
           "body: JSON.stringify({title: 'Rate limit v2'})}); return r.status;" % (goal["id"], TOKEN))
    a.wait_for("document.querySelector('h1').textContent.includes('Rate limit v2')")
    assert not_reloaded(a, token)

    # tab A goes stale (its reads are held), the plan changes elsewhere, and A then tries to approve v1
    a.eval("window.__fetch = window.fetch; window.fetch = (u, o) => (!o || !o.method || o.method === 'GET') "
           "? new Promise((resolve, reject) => o.signal.addEventListener('abort', () => reject(o.signal.reason), {once: true})) "
           ": window.__fetch(u, o); return 1;")
    bg.plan_goal_work("demo", goal["id"], plan=good_plan(2), change_request="two phases")
    a.eval("document.getElementById('goal-approve').click(); return 1;")
    a.wait_for("document.getElementById('mf-0')")
    a.eval("document.getElementById('mf-0').focus(); return 1;")
    a.key("APPROVE %s v1" % goal["id"])
    a.eval("document.getElementById('modal-confirm').click(); return 1;")
    a.wait_for("document.getElementById('toast') && document.getElementById('toast').classList.contains('error')")
    locale = a.eval("return document.documentElement.lang;")
    message = a.eval("return document.getElementById('toast').textContent;")
    assert ("此計畫已不是最新版本" if locale == "zh-TW" else "no longer the current plan") in message
    assert bg.get_goal("demo", goal["id"])["approval"] is None          # nothing was approved

    a.eval("window.fetch = window.__fetch; return 1;")
    a.wait_for("document.querySelector('details.goal-versions summary').textContent.includes('(2)')", 25)
    assert not_reloaded(a, token)
    a.close()
    b.close()


def test_projects_and_settings_pages_obey_the_same_rule(env):
    bg = env["bg"]
    page = open_page(env, "#/")
    page.wait_for("document.querySelector('.project-row')")
    token = mark(page)
    other = env["repos"] / "second"
    other.mkdir()
    subprocess.run(["git", "-C", str(other), "init", "-q"], check=True, capture_output=True)
    bg.register_project(other, "second", "added in the background")
    page.wait_for("[...document.querySelectorAll('.project-row-name')].some(e => e.textContent === 'second')", 20)
    assert not_reloaded(page, token)

    page.eval("location.hash = '#/settings'; return 1;")
    page.wait_for("document.getElementById('connect-name') || document.getElementById('btn-disconnect')")
    token = mark(page)
    page.eval("const e = document.getElementById('connect-name'); if (e) e.focus(); return 1;")
    page.key("Work profile")
    time.sleep(5)                                     # two polls of the connection status go by
    assert not_reloaded(page, token)
    assert caret(page, "#connect-name")[0] == "Work profile"

    page.eval("location.hash = '#/p/demo/overview'; return 1;")
    page.wait_for("document.getElementById('test-command')")
    token = mark(page)
    page.eval("document.getElementById('test-command').focus(); return 1;")
    page.key("python -m pytest")
    bg.start_goal_work("demo", "An unrelated background change to the project", title="Noise")
    time.sleep(3.5)
    assert not_reloaded(page, token)
    assert caret(page, "#test-command")[0] == "python -m pytest"
    page.close()


def test_team_editor_in_traditional_chinese_keeps_drafts_and_saves_one_request(env):
    import base64
    import os
    from pathlib import Path

    page = open_page(env, "#/p/demo/overview")

    def set_value(selector, value):
        page.eval(
            "const el = document.querySelector(%r); el.value = %r;"
            " el.dispatchEvent(new Event('input', {bubbles: true}));"
            " el.dispatchEvent(new Event('change', {bubbles: true}));" % (selector, value))

    def click(selector):
        page.eval("document.querySelector(%r).click();" % selector)

    folder = os.environ.get("CCC_TEAM_EVIDENCE")

    def screenshot(name):
        if folder:
            data = page.send("Page.captureScreenshot", format="png")["data"]
            Path(folder, name).write_bytes(base64.b64decode(data))

    page.wait_for("document.querySelector('#team-workflow')")
    page.eval("window.__oldTeam = document.querySelector('#team-card');")
    set_value("#ui-locale", "zh-TW")
    page.wait_for("!window.__oldTeam.isConnected && document.querySelector('#team-card h2')?.textContent === 'AI 團隊與工作流'")
    token = mark(page)
    page.eval("window.__posts = []; const send = fetch; window.fetch = (url, options) => {"
              "if (options?.method === 'POST') window.__posts.push(url); return send(url, options); };")
    click("#team-new-role")
    set_value("#role-name", "規格把關員")
    set_value("#role-base", "SR")
    set_value("#role-text", "檢查需求是否可以驗收。")
    set_value("[data-field=role-provider]", "codex")
    click("#modal-confirm")
    page.wait_for("!document.querySelector('#modal-overlay') || document.querySelector('.team-dialog-error:not([hidden])')")
    assert page.eval("return document.querySelector('.team-dialog-error:not([hidden])')?.textContent ?? null;") is None
    assert page.eval("return document.querySelector('#team-dirty').textContent;") == "角色已儲存。"
    click("#team-new-workflow")
    set_value("#wf-name", "規格驗收團隊")
    while page.eval("return document.querySelectorAll('#wf-steps li').length;") < 5:
        click("#wf-add")
    for index, role in enumerate(["SA", "SD", "RD", "REVIEW", "SR"]):
        set_value(f'#wf-steps [data-i="{index}"] select', role)
    click('#wf-steps [data-i="4"] [data-up]')
    click("#modal-confirm")
    page.wait_for("!document.querySelector('#modal-overlay') && document.querySelectorAll('.team-step').length === 5")
    assert page.eval("return [...document.querySelectorAll('.team-step-name')].map(e => e.textContent.trim());") == [
        "分析師", "設計師", "開發者", "規格審查員", "程式碼審查員",
    ]
    click("#team-edit-workflow")
    role_id = env["bg"].custom_role_directory("demo")[-1]["id"]
    set_value('#wf-steps [data-i="3"] select', role_id)
    screenshot("team-workflow-editor-zh.png")
    click("#modal-confirm")
    page.wait_for("document.querySelector('.team-step [data-edit-role]')")
    click(".team-step [data-edit-role]")
    page.wait_for("document.querySelector('#role-text')")
    assert page.eval("return document.querySelector('#role-text').value;") == "檢查需求是否可以驗收。"
    set_value("#role-text", "確認每條需求都有具體驗收證據。")
    screenshot("team-role-editor-zh.png")
    click("#modal-confirm")
    page.wait_for("!document.querySelector('#modal-overlay')")
    set_value('[data-step="1"] [data-field=provider]', "codex")
    set_value('[data-step="2"] [data-field=provider]', "codex")
    click('[data-step="1"] [data-more]')
    page.eval("const input = document.querySelector('[data-step=\"1\"] [data-field=model]');"
              "input.value = ''; input.focus(); input.scrollIntoView({block: 'center'});")
    page.key("team-model")
    scroll = page.eval("return document.querySelector('#sheet').scrollTop;")
    env["bg"].start_goal_work("demo", "Team editor background activity", title="背景工作")
    time.sleep(3.5)
    assert page.eval("return document.querySelector('#sheet').scrollTop;") == scroll
    assert caret(page, '[data-step="1"] [data-field=model]') == ["team-model", 10, 10, True]
    click("#team-save")
    page.wait_for("document.querySelector('#team-dirty')?.textContent === '團隊設定已儲存。'")
    assert page.eval("return window.__posts.filter(p => p.endsWith('/config/team')).length;") == 1
    assert not_reloaded(page, token)
    page.eval("document.querySelector('#team-card').scrollIntoView({block: 'start'});")
    screenshot("team-custom-zh.png")

    reopened = open_page(env, "#/p/demo/overview")
    reopened.wait_for("document.querySelector('.team-step [data-edit-role]')")
    assert reopened.eval("return document.querySelector('[data-step=\"1\"] [data-field=model]').value;") == "team-model"
    assert reopened.eval("return document.querySelector('[data-step=\"1\"] [data-field=provider]').value;") == "codex"
    reopened.close()
    stale = open_page(env, "#/p/demo/overview")
    stale.wait_for("document.querySelector('#team-workflow')")
    set_value("#team-workflow", "content")
    assert page.eval("return [...document.querySelectorAll('.team-step-name')].map(e => e.textContent.trim());") == [
        "個人助理", "研究員", "撰稿人", "編輯", "事實查核員",
    ]
    assert page.eval("return document.activeElement.id;") == "team-workflow"
    click("#team-save")
    page.wait_for("document.querySelector('#team-dirty')?.textContent === '團隊設定已儲存。'")
    page.eval("document.querySelector('#team-card').scrollIntoView({block: 'start'});")
    screenshot("team-content-zh.png")

    stale.eval("const flow = document.querySelector('#team-workflow'); flow.value = 'standard';"
               "flow.dispatchEvent(new Event('change', {bubbles: true}));")
    stale.eval("document.querySelector('#team-save').click();")
    stale.wait_for("document.querySelector('#team-dirty')?.textContent.includes('另一個工作階段')")
    assert stale.eval("return document.querySelector('#team-workflow').value;") == "standard"
    stale.eval("document.querySelector('#team-discard').click();")
    stale.wait_for("document.querySelector('#team-workflow')?.value === 'content'")
    stale.close()

    saved_flow = env["bg"].workflow_catalog("demo")["workflows"][-1]["id"]
    set_value("#team-workflow", saved_flow)
    click("#team-save")
    page.wait_for("!document.querySelector('#team-delete-workflow')?.disabled")
    click("#team-delete-workflow")
    click("#modal-confirm")
    page.wait_for("!document.querySelector('#modal-overlay') && document.querySelector('#team-workflow')?.value === 'classic'")
    assert not_reloaded(page, token)
    page.close()


def test_traditional_chinese_main_pages_and_imported_role_confirmation(env):
    bg = env["bg"]
    source = env["repos"] / "team-source"
    target = env["repos"] / "team-import"
    source.mkdir()
    target.mkdir()
    for root in (source, target):
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
    source_service = CCCMCP(env["repos"] / "team-source-home")
    source_service.register_project(source, "team-source", "匯入測試")
    source_service.create_custom_role("team-source", "文字校訂", "ED",
                                      prompt={"mode": "freeform", "freeform": "核對具體用字與標點。"})
    imported = bg.import_project_bundle(source_service.export_project_bundle("team-source"), target)
    project = imported["project_id"]
    goal = bg.start_goal_work(project, "整理一份文字規範", title="文字規範", plan=good_plan(1))
    page = open_page(env, "#/")
    page.wait_for("document.querySelector('.project-row')")
    page.eval("const select = document.querySelector('#ui-locale'); select.value = 'zh-TW';"
              " select.dispatchEvent(new Event('change'));")
    page.wait_for("document.querySelector('#sheet h1')?.textContent === '專案'")
    page.eval("document.querySelector('#btn-import-project').click();")
    page.wait_for("document.querySelector('#modal-overlay h2')?.textContent.includes('匯入')")
    page.eval("document.querySelector('#modal-cancel').click();")
    page.eval(f"location.hash = '#/p/{project}/overview';")
    page.wait_for("document.querySelector('[data-review-role]')")
    page.eval("document.querySelector('[data-review-role]').click();")
    page.wait_for("document.querySelector('#modal-overlay h2')?.textContent === '信任這個匯入的角色'")
    assert "核對具體用字與標點。" in page.eval("return document.querySelector('.modal .body').textContent;")
    assert bg.pending_imported_roles(project)
    page.eval("document.querySelector('#modal-confirm').click();")
    page.wait_for("document.querySelector('#toast.error')?.textContent.includes('確認')")
    assert bg.pending_imported_roles(project)
    page.eval("const phrase = document.querySelector('#mf-0'); phrase.value = phrase.placeholder;"
              "document.querySelector('#modal-confirm').click();")
    page.wait_for("!document.querySelector('[data-review-role]')")
    assert bg.pending_imported_roles(project) == []

    page.eval(f"location.hash = '#/p/{project}/tasks';")
    page.wait_for("document.querySelector('#work-text')")
    assert page.eval("return document.querySelector('#work-start').textContent.includes('開始');")
    page.eval(f"location.hash = '#/p/{project}/tasks/{goal['id']}';")
    page.wait_for("document.querySelector('#goal-approve')")
    page.eval("document.querySelector('#goal-approve').click();")
    page.wait_for("document.querySelector('#modal-overlay h2')?.textContent.includes('核准')")
    assert "計畫" in page.eval("return document.querySelector('.modal .body').textContent;")
    page.eval("const phrase = document.querySelector('#mf-0'); phrase.value = phrase.placeholder;"
              "document.querySelector('#mf-1').checked = false; document.querySelector('#modal-confirm').click();")
    page.wait_for("document.querySelector('#toast')?.textContent === '計畫已核准。'")
    page.wait_for("!document.querySelector('#modal-overlay')")
    task = bg.create_task(project, "檢查工作紀錄")["id"]
    page.eval(f"location.hash = '#/p/{project}/tasks/{task}';")
    page.wait_for("document.querySelector('#sheet h1')?.textContent === '任務討論'")
    page.eval("location.hash = '#/settings';")
    page.wait_for("document.querySelector('#connect-name')")
    assert "連線" in page.eval("return document.querySelector('#sheet h1').textContent;")
    assert "連線" in page.eval("return document.querySelector('[for=connect-name]').textContent;")
    page.close()


def test_each_workflow_shows_its_own_roles_in_traditional_chinese_without_a_reload(env):
    page = open_page(env, "#/p/demo/overview")
    page.wait_for("document.querySelector('#team-workflow')")

    def choose(flow_id):
        page.eval("const select = document.querySelector('#team-workflow'); select.value = %r;"
                  " select.dispatchEvent(new Event('change', {bubbles: true})); return 1;" % flow_id)

    def team():
        return page.eval(
            "return {names: [...document.querySelectorAll('.team-step-name')].map(e => e.textContent.trim()),"
            " tools: [...document.querySelectorAll('.team-step-tool')].map(e => e.querySelector('select')?.value"
            " ?? e.textContent.trim()), hint: Boolean(document.querySelector('#team-goal-hint')),"
            " open: [...document.querySelectorAll('#team-card details')].some(e => e.open)"
            " || [...document.querySelectorAll('.team-step-more')].some(e => !e.hidden)};")

    page.eval("window.__oldTeam = document.querySelector('#team-card');"
              " const select = document.querySelector('#ui-locale'); select.value = 'zh-TW';"
              " select.dispatchEvent(new Event('change', {bubbles: true})); return 1;")
    # The sidebar is translated before the overview has finished fetching and rendering.
    page.wait_for("!window.__oldTeam.isConnected && "
                  "document.querySelector('#team-card h2')?.textContent === 'AI 團隊與工作流'")
    assert page.eval("return document.getElementById('skip-link').textContent;") == "跳到主要內容"
    assert page.eval("return document.querySelector('nav.sidebar').getAttribute('aria-label');") == "主選單"
    token = mark(page)
    choose("content")
    page.wait_for("document.querySelectorAll('.team-step').length === 5 && "
                  "document.querySelector('.team-step-name').textContent.trim() === '個人助理'")
    content = team()
    assert content["names"] == ["個人助理", "研究員", "撰稿人", "編輯", "事實查核員"]
    assert set(content["tools"]) == {"chatgpt"}
    assert content["hint"] is True and content["open"] is False
    assert "SD" not in " ".join(content["names"]) and "開發者" not in content["names"]
    choose("standard")
    page.wait_for("document.querySelector('.team-step-name').textContent.trim() === '分析師'")
    standard = team()
    assert standard["names"] == ["分析師", "設計師", "開發者", "程式碼審查員"]
    assert standard["hint"] is False and standard["open"] is False
    choose("life")
    page.wait_for("document.querySelector('.team-step-name').textContent.trim() === '個人助理'")
    assert team()["names"] == ["個人助理", "研究員", "資料分析師", "規劃師", "事實查核員"]
    assert not_reloaded(page, token)
    page.close()
