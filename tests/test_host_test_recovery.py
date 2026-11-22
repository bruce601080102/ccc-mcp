"""Stage 0: a Host Test whose runner was killed must not block its Task forever.

The residue of a killed runner (`task_controls.host_test_running = 1`) used to be cleared only by a
`finally` block, which a SIGKILL never reaches. These tests kill a real runner process and check
that ccc-mcp tells a live runner from a dead one, never counts the interrupted run as a pass, and
keeps an audit trail of what happened.
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import psutil
import pytest

from ccc_mcp.adapters.storage import Store
from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.service import CCCMCP
from ccc_mcp.worker import run_execution

RUNNER = (
    "import sys\n"
    "from ccc_mcp.service import CCCMCP\n"
    "CCCMCP(sys.argv[1]).run_configured_test_command(sys.argv[2], sys.argv[3])\n"
)
UNRECORDED_RUNNER = (
    "import sys\n"
    "from ccc_mcp.core.tasks import TaskHub\n"
    "TaskHub._host_test_child_recorder = lambda self, run_id: (lambda pid: None)\n"
    + RUNNER
)
PASSING = [sys.executable, "-c", "print('ok')"]
LEGACY_SQL = Path(__file__).parent / "fixtures" / "legacy_schema_0_4_0.sql"
TASK = "TASK-0001"


def sleeping_command(tag):
    """A test command that outlives the runner, so the 'orphan' case is real."""
    return [sys.executable, "-c", f"import time; time.sleep(120)  # {tag}"]


def processes_with(tag):
    return [
        p for p in psutil.process_iter(["cmdline"])
        if tag in " ".join(p.info["cmdline"] or [])
    ]


def wait_for(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached in time")


# These kill a runner with SIGKILL or rely on file locks, which ccc-mcp itself only verifies on POSIX
# (the product refuses to recover automatically elsewhere). The rest run on every platform.
POSIX_ONLY = {
    "test_reproduction_a_killed_runner_must_not_block_the_task_forever",
    "test_after_sigkill_the_residue_is_recognised_and_recorded_as_interrupted",
    "test_an_interrupted_test_is_never_read_as_a_pass_by_the_review_gate",
    "test_a_dead_runner_does_not_keep_a_stop_request_stuck",
    "test_an_unstoppable_orphan_keeps_the_conservative_state",
    "test_after_a_service_restart_the_host_test_is_run_again_from_scratch",
    "test_the_database_is_reopened_from_disk_before_recovering",
    "test_an_earlier_pass_is_not_reused_after_an_interrupted_rerun",
    "test_recovering_twice_records_one_interruption_and_changes_nothing_more",
    "test_resuming_after_an_interrupted_host_test_dispatches_the_next_role_once",
    "test_a_legacy_database_gains_only_the_ledger_and_keeps_working",
    "test_an_orphan_that_was_never_recorded_still_blocks_a_rerun",
    "test_a_recorded_child_that_no_longer_matches_is_never_trusted",
    "test_the_reliability_check_names_network_file_systems_and_other_platforms",
    "test_two_interruptions_then_a_pass_leave_one_unbroken_chain",
    "test_an_interrupted_test_cannot_pass_a_review_gate",
}


@pytest.fixture(autouse=True)
def posix_only(request):
    if os.name == "nt" and request.node.originalname in POSIX_ONLY:
        pytest.skip("SIGKILL and file-lock recovery are only verified on POSIX")


@pytest.fixture
def tag():
    return "ccc-hosttest-" + uuid.uuid4().hex[:12]


@pytest.fixture(autouse=True)
def reap(tag):
    runners = []
    yield runners
    for proc in runners:
        try:
            psutil.Process(proc.pid).kill()
        except psutil.NoSuchProcess:
            pass
        proc.wait(10)
    for leftover in processes_with(tag):
        try:
            leftover.kill()
        except psutil.NoSuchProcess:
            pass


def dispatch_fake(bridge, role):
    result = bridge.dispatch_agent(
        "demo", TASK, "codex" if role in {"SA", "REVIEW"} else "claude", role,
        bridge.latest("demo")["version"], "Perform role", adapter="fake",
    )
    run_execution(bridge, result["execution_id"])
    return result["execution_id"]


def prepare_task(bridge):
    """A task whose implementation step already ran, so a host test has a workspace to use.
    `bridge.implementation_id` is that step's execution, still waiting to be advanced."""
    bridge.authorize_project_local("demo")
    assert bridge.create_task("demo", "Check the host tests")["id"] == TASK
    sa = dispatch_fake(bridge, "SA")
    result = next(
        item for item in bridge.latest("demo")["snapshot"]["agent_results"]
        if item["execution_id"] == sa
    )
    patch = {
        "base_context_version": bridge.latest("demo")["version"],
        "changes": {"summary": "PM reviewed SA."},
        "source_execution_ids": [sa], "source_task_ids": [TASK],
        "source_document_refs": [result["source_document_ref"]],
        "update_reason": "PM verified", "semantic_review_ack": True,
    }
    implementation = bridge.advance_project(
        "demo", patch, "Implement the change", adapter="fake")["next_execution"]
    run_execution(bridge, implementation["execution_id"])
    bridge.implementation_id = implementation["execution_id"]
    return bridge


@pytest.fixture
def ready(bridge, human):
    return prepare_task(bridge)


def start_runner(bridge, reap, script=RUNNER):
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(bridge.home), "demo", TASK],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    reap.append(process)
    return process


def running_host_test(bridge, tag, reap):
    """A runner process that is in the middle of a Host Test."""
    bridge.configure_test_command("demo", sleeping_command(tag))
    process = start_runner(bridge, reap)
    wait_for(lambda: bridge.task_control("demo", TASK)["host_test_running"] and any(
        row["child"] for row in ledger(bridge) if row["status"] == "RUNNING"))
    return process


def kill(process):
    psutil.Process(process.pid).kill()
    process.wait(10)


def host_test_evidence(bridge):
    rows = bridge.store.all(
        "SELECT content FROM artifacts WHERE project_id='demo' "
        "AND path LIKE ? ORDER BY created, path",
        (f"%/tasks/{TASK}/executions/TEST-%/TEST-RESULT.md",),
    )
    return [json.loads(row["content"].rsplit("\n\n", 1)[-1]) for row in rows]


def ledger(bridge):
    return bridge.store.all("SELECT * FROM host_test_runs ORDER BY started, id")


# 1 ---------------------------------------------------------------------------------------


def test_a_normal_host_test_runs_records_evidence_and_releases_everything(ready):
    ready.configure_test_command("demo", PASSING)

    result = ready.run_configured_test_command("demo", TASK)

    assert result["executed_by_host"] is True and result["passed"] is True
    control = ready.task_control("demo", TASK)
    assert control["host_test_running"] == 0
    rows = ledger(ready)
    assert [row["status"] for row in rows] == ["FINISHED"]
    assert json.loads(rows[0]["detail"])["outcome"] == "passed"
    # The lease is free again, so an immediate second run starts normally.
    assert ready.run_configured_test_command("demo", TASK)["passed"] is True
    assert [row["status"] for row in ledger(ready)] == ["FINISHED", "FINISHED"]


def test_a_refused_start_leaves_no_residue(ready):
    ready.configure_test_command("demo", PASSING)
    ready.pause_task("demo", TASK)
    with pytest.raises(Exception, match="paused"):
        ready.run_configured_test_command("demo", TASK)
    assert ready.task_control("demo", TASK)["host_test_running"] == 0
    assert ledger(ready) == []
    ready.resume_task("demo", TASK)
    assert ready.run_configured_test_command("demo", TASK)["passed"] is True


# 2 ---------------------------------------------------------------------------------------


def test_a_second_runner_in_the_same_process_is_refused_while_one_is_running(
    ready, monkeypatch
):
    started, release = threading.Event(), threading.Event()

    def slow(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return {"executed_by_host": True, "passed": True, "command": ["pytest"]}

    monkeypatch.setattr("ccc_mcp.adapters.test_runner.execute_test_command", slow)
    thread = threading.Thread(target=ready.run_configured_test_command, args=("demo", TASK))
    thread.start()
    assert started.wait(5)
    with pytest.raises(BridgeError, match="already running"):
        ready.run_configured_test_command("demo", TASK)
    assert [row["status"] for row in ledger(ready)] == ["RUNNING"]
    release.set()
    thread.join(10)
    assert [row["status"] for row in ledger(ready)] == ["FINISHED"]
    assert ready.task_control("demo", TASK)["host_test_running"] == 0


def test_a_live_runner_in_another_process_is_not_mistaken_for_a_dead_one(ready, tag, reap):
    live = running_host_test(ready, tag, reap)

    with pytest.raises(BridgeError, match="already running"):
        ready.run_configured_test_command("demo", TASK)
    reaped = ready.recover_workers("demo")

    assert reaped["interrupted_host_tests"] == []
    assert ready.task_control("demo", TASK)["host_test_running"] == 1
    assert [row["status"] for row in ledger(ready)] == ["RUNNING"]
    assert processes_with(tag), "the live test must not have been touched"
    assert psutil.Process(live.pid).is_running()


# 3 ---------------------------------------------------------------------------------------


def test_reproduction_a_killed_runner_must_not_block_the_task_forever(ready, tag, reap):
    """The reported bug, through public API only: SIGKILL the runner mid-test, then ask for the
    Host Test again. Before the fix this raised 'Host tests are already running' for ever."""
    kill(running_host_test(ready, tag, reap))
    assert ready.task_control("demo", TASK)["host_test_running"] == 1

    ready.configure_test_command("demo", PASSING)
    result = ready.run_configured_test_command("demo", TASK)

    assert result["executed_by_host"] is True and result["passed"] is True


def test_after_sigkill_the_residue_is_recognised_and_recorded_as_interrupted(
    ready, tag, reap
):
    """The original bug: before the fix this state was permanent."""
    runner = running_host_test(ready, tag, reap)
    kill(runner)
    assert ready.task_control("demo", TASK)["host_test_running"] == 1, "the finally never ran"

    reaped = ready.recover_workers("demo")

    assert [item["task_id"] for item in reaped["interrupted_host_tests"]] == [TASK]
    assert reaped["automatic_replay"] is False
    assert ready.task_control("demo", TASK)["host_test_running"] == 0
    rows = ledger(ready)
    assert [row["status"] for row in rows] == ["INTERRUPTED"]
    detail = json.loads(rows[0]["detail"])
    assert detail["orphan"] == "terminated"
    wait_for(lambda: not processes_with(tag))
    [evidence] = host_test_evidence(ready)
    assert evidence["interrupted"] is True
    assert evidence["executed_by_host"] is False and evidence["passed"] is False
    assert ready._host_tests_unverified("demo", TASK) is True
    assert ready._host_test_failed("demo", TASK) is False


def test_an_interrupted_test_is_never_read_as_a_pass_by_the_review_gate(ready, tag, reap):
    kill(running_host_test(ready, tag, reap))
    ready.recover_workers("demo")

    outcome, instruction = ready._review_outcome("demo", TASK)

    assert outcome == "revise"
    assert "interrupted" in instruction.lower()
    status = ready.get_task_status("demo", TASK)["brief"]["tests"]
    assert status["executed_by_host"] is False and status["passed"] is False


def test_a_dead_runner_does_not_keep_a_stop_request_stuck(ready, tag, reap):
    runner = running_host_test(ready, tag, reap)
    assert ready.cancel_task("demo", TASK)["status"] == "STOPPING"
    kill(runner)
    assert ready.get_task_status("demo", TASK)["brief"]["control"]["status"] == "STOPPING"

    ready.recover_workers("demo")

    assert ready.get_task_status("demo", TASK)["brief"]["control"]["status"] == "CANCELLED"


def test_an_unstoppable_orphan_keeps_the_conservative_state(ready, tag, reap, monkeypatch):
    kill(running_host_test(ready, tag, reap))
    monkeypatch.setattr("ccc_mcp.core.tasks.stop_process", lambda child: "failed")

    with pytest.raises(BridgeError, match="could not be stopped"):
        ready.run_configured_test_command("demo", TASK)

    assert ready.task_control("demo", TASK)["host_test_running"] == 1
    assert [row["status"] for row in ledger(ready)] == ["RUNNING"]
    assert host_test_evidence(ready) == []


# 4 ---------------------------------------------------------------------------------------


def test_after_a_service_restart_the_host_test_is_run_again_from_scratch(ready, tag, reap):
    kill(running_host_test(ready, tag, reap))

    restarted = CCCMCP(ready.home)
    restarted.configure_test_command("demo", PASSING)
    result = restarted.run_configured_test_command("demo", TASK)

    assert result["executed_by_host"] is True and result["passed"] is True
    kinds = [bool(item.get("interrupted")) for item in host_test_evidence(restarted)]
    assert kinds == [True, False], "the interruption is recorded before the rerun's result"
    rows = ledger(restarted)
    assert [row["status"] for row in rows] == ["INTERRUPTED", "FINISHED"]
    assert json.loads(rows[1]["detail"])["recovers"] == rows[0]["id"]
    assert restarted.task_control("demo", TASK)["host_test_running"] == 0
    wait_for(lambda: not processes_with(tag))


def test_the_database_is_reopened_from_disk_before_recovering(ready, tag, reap):
    kill(running_host_test(ready, tag, reap))
    home = ready.home
    del ready

    reopened = CCCMCP(home)

    assert reopened.task_control("demo", TASK)["host_test_running"] == 1
    assert [item["task_id"] for item in reopened.recover_workers("demo")[
        "interrupted_host_tests"]] == [TASK]
    assert reopened.task_control("demo", TASK)["host_test_running"] == 0


# 5 ---------------------------------------------------------------------------------------


def test_an_earlier_pass_is_not_reused_after_an_interrupted_rerun(ready, tag, reap):
    ready.configure_test_command("demo", PASSING)
    assert ready.run_configured_test_command("demo", TASK)["passed"] is True
    assert ready._host_tests_unverified("demo", TASK) is False

    kill(running_host_test(ready, tag, reap))
    ready.recover_workers("demo")

    latest = ready._latest_test_result("demo", TASK)
    assert latest["interrupted"] is True and latest["passed"] is False
    assert ready._host_tests_unverified("demo", TASK) is True

    ready.configure_test_command("demo", PASSING)
    ready.run_configured_test_command("demo", TASK)
    assert ready._host_tests_unverified("demo", TASK) is False
    assert ready._latest_test_result("demo", TASK).get("interrupted") is None


# 6 ---------------------------------------------------------------------------------------


def test_recovering_twice_records_one_interruption_and_changes_nothing_more(ready, tag, reap):
    kill(running_host_test(ready, tag, reap))

    first = ready.recover_workers("demo")
    second = ready.recover_workers("demo")

    assert len(first["interrupted_host_tests"]) == 1
    assert second["interrupted_host_tests"] == []
    assert [row["status"] for row in ledger(ready)] == ["INTERRUPTED"]
    assert len(host_test_evidence(ready)) == 1


def test_resuming_after_an_interrupted_host_test_dispatches_the_next_role_once(
    ready, monkeypatch
):
    """advance -> host test dies -> resume: one rerun, one REVIEW, however often it is repeated."""
    ready.configure_test_command("demo", PASSING)

    original = ready.run_configured_test_command
    crashed = {"done": False}

    def dies_once(project_id, task_id):
        if not crashed["done"]:
            crashed["done"] = True
            owner = {"pid": 2**22 + 7, "create_time": 1.0, "host": "gone"}
            with ready.store.transaction() as conn:
                conn.execute(
                    "INSERT INTO host_test_runs(id,project_id,task_id,owner,status,started) "
                    "VALUES('HTRUN-dead',?,?,?,'RUNNING',?)",
                    (project_id, task_id, json.dumps(owner), "2026-01-01T00:00:00+00:00"),
                )
                conn.execute(
                    "INSERT INTO task_controls(project_id,task_id,host_test_running) VALUES(?,?,1) "
                    "ON CONFLICT(project_id,task_id) DO UPDATE SET host_test_running=1",
                    (project_id, task_id),
                )
            raise RuntimeError("the process died here")
        return original(project_id, task_id)

    monkeypatch.setattr(ready, "run_configured_test_command", dies_once)
    with pytest.raises(RuntimeError, match="died"):
        ready.auto_advance_low_risk(ready.implementation_id, allow_fake=True)
    assert ready._pending_dispatch_row("demo", TASK) is not None

    resumed = ready.resume_pending_dispatch("demo")
    again = ready.resume_pending_dispatch("demo")
    ready.recover_workers("demo")

    assert resumed["resumed"] is True and resumed["test_evidence"]["passed"] is True
    assert again["resumed"] is False
    reviews = [e for e in ready.get_task_status("demo", TASK)["executions"] if e["role"] == "REVIEW"]
    assert len(reviews) == 1
    assert [row["status"] for row in ledger(ready)] == ["INTERRUPTED", "FINISHED"]
    assert [bool(item.get("interrupted")) for item in host_test_evidence(ready)] == [True, False]


# 7 ---------------------------------------------------------------------------------------


def legacy_service(tmp_path):
    home = tmp_path / "legacy-home"
    home.mkdir()
    with sqlite3.connect(home / "bridge.sqlite3") as connection:
        connection.executescript(LEGACY_SQL.read_text(encoding="utf-8"))
    repo = tmp_path / "legacy-repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "."], [
        "-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    service = CCCMCP(home)
    service.register_project(repo, "demo", "legacy database project")
    return service


def test_a_legacy_database_gains_only_the_ledger_and_keeps_working(tmp_path, human):
    service = legacy_service(tmp_path)
    path = service.store.path
    with sqlite3.connect(path) as connection:
        names = {row[1] for row in connection.execute("PRAGMA table_info(task_controls)")}
    assert names == {
        "project_id", "task_id", "paused", "stop_requested", "stop_reason",
        "stop_requested_at", "host_test_running",
    }, "task_controls itself must not change"
    Store(service.home)  # reopening is idempotent

    prepare_task(service)
    task = TASK
    state_before = service.get_task_status("demo", task)["state"]
    service.configure_test_command("demo", PASSING)
    assert service.run_configured_test_command("demo", task)["passed"] is True

    # A flag left behind by the previous version has no ledger row and no lease: ccc-mcp cannot
    # tell it from a live run, so it asks an operator instead of guessing.
    with service.store.transaction() as conn:
        conn.execute(
            "INSERT INTO task_controls(project_id,task_id,host_test_running) VALUES('demo',?,1) "
            "ON CONFLICT(project_id,task_id) DO UPDATE SET host_test_running=1", (task,))
    with pytest.raises(BridgeError, match="no run record"):
        service.run_configured_test_command("demo", task)
    refused = service.recover_workers("demo")
    assert refused["interrupted_host_tests"] == []
    assert "confirm-host-tests-stopped" in refused["host_tests_not_recovered"][0]["error"]
    assert service.task_control("demo", task)["host_test_running"] == 1

    confirmed = service.recover_workers("demo", operator_confirmed=True)
    assert [item["task_id"] for item in confirmed["interrupted_host_tests"]] == [task]
    latest = service._latest_test_result("demo", task)
    assert latest["interrupted"] is True
    assert latest["interrupted_run"]["legacy_residue"] is True
    assert latest["detected"]["trigger"] == "operator_confirmed"
    assert service.run_configured_test_command("demo", task)["passed"] is True
    assert service.get_task_status("demo", task)["state"] == state_before, "no state change"


def test_the_ledger_cannot_be_rewritten_or_deleted(ready):
    ready.configure_test_command("demo", PASSING)
    ready.run_configured_test_command("demo", TASK)
    with sqlite3.connect(ready.store.path) as connection:
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            connection.execute("DELETE FROM host_test_runs")
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            connection.execute("UPDATE host_test_runs SET status='RUNNING'")
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            connection.execute("UPDATE host_test_runs SET owner='{}'")


def test_project_history_clearing_and_bundles_cope_with_the_ledger(ready):
    ready.configure_test_command("demo", PASSING)
    ready.run_configured_test_command("demo", TASK)

    bundle = ready.export_project_bundle("demo")
    assert "host_test_runs" not in json.loads(bundle)["payload"]["tables"]

    ready.clear_project_history_web("demo", "CLEAR HISTORY demo")
    assert ledger(ready) == []
    assert os.path.exists(ready.store.path)


# P0: a dead runner's test process must never overlap a new run ---------------------------


def test_an_orphan_that_was_never_recorded_still_blocks_a_rerun(ready, tag, reap):
    """The runner is killed after the test process started but before its pid reached the
    database. Nothing says who the orphan is, so a rerun must be refused, not run beside it."""
    ready.configure_test_command("demo", sleeping_command(tag))
    runner = start_runner(ready, reap, UNRECORDED_RUNNER)
    wait_for(lambda: ready.task_control("demo", TASK)["host_test_running"] and processes_with(tag))
    kill(runner)
    assert [row["child"] for row in ledger(ready)] == [None], "the pid was never recorded"

    ready.configure_test_command("demo", PASSING)
    with pytest.raises(BridgeError, match="already running"):
        ready.run_configured_test_command("demo", TASK)

    assert processes_with(tag), "the unidentified process must not be touched"
    assert [row["status"] for row in ledger(ready)] == ["RUNNING"]
    assert host_test_evidence(ready) == []
    recovery = ready.recover_workers("demo")
    assert recovery["interrupted_host_tests"] == []
    assert [item["task_id"] for item in recovery["host_tests_not_recovered"]] == [TASK]

    for leftover in processes_with(tag):
        leftover.kill()
    wait_for(lambda: not processes_with(tag))
    assert ready.run_configured_test_command("demo", TASK)["passed"] is True
    assert [bool(item.get("interrupted")) for item in host_test_evidence(ready)] == [True, False]


def test_a_recorded_child_that_no_longer_matches_is_never_trusted(ready, tag, reap):
    """The recovery cannot confirm the child's identity (the recorded start time does not match
    the live process, as after pid reuse): it must refuse rather than stop or ignore it."""
    kill(running_host_test(ready, tag, reap))
    row = ledger(ready)[0]
    child = json.loads(row["child"])
    child["create_time"] += 1000
    with sqlite3.connect(ready.store.path) as connection:
        connection.execute("UPDATE host_test_runs SET child=? WHERE id=?",
                           (json.dumps(child), row["id"]))
    ready.configure_test_command("demo", PASSING)

    with pytest.raises(BridgeError, match="already running"):
        ready.run_configured_test_command("demo", TASK)

    assert processes_with(tag), "a process we cannot identify is left alone"
    assert [r["status"] for r in ledger(ready)] == ["RUNNING"]
    assert host_test_evidence(ready) == []


def test_simultaneous_runners_in_separate_processes_never_overlap(ready, tmp_path, reap):
    log = tmp_path / "overlap.log"
    ready.configure_test_command("demo", [sys.executable, "-c", (
        "import os, time\n"
        f"fd = os.open({str(log)!r}, os.O_WRONLY | os.O_APPEND | os.O_CREAT)\n"
        "os.write(fd, b'start\\n'); time.sleep(0.8); os.write(fd, b'end\\n')\n")])
    runners = [start_runner(ready, reap) for _ in range(5)]
    for runner in runners:
        runner.wait(60)

    events = log.read_text().split()
    assert events and len(events) % 2 == 0
    assert events == ["start", "end"] * (len(events) // 2), "two host tests overlapped"
    assert [row["status"] for row in ledger(ready)] == ["FINISHED"] * (len(events) // 2)


# P1: the manual `task test` entry point shares the same exclusion -------------------------


def test_a_manual_test_run_is_refused_while_an_automatic_one_is_running(ready, tag, reap):
    from ccc_mcp.adapters.test_runner import run_tests_local

    live = running_host_test(ready, tag, reap)
    ready.configure_test_command("demo", PASSING)

    with pytest.raises(BridgeError, match="already running"):
        run_tests_local(ready, "demo", TASK)

    assert psutil.Process(live.pid).is_running() and processes_with(tag)
    assert [row["status"] for row in ledger(ready)] == ["RUNNING"]


def test_an_automatic_run_is_refused_while_a_manual_one_is_running(ready, monkeypatch):
    from ccc_mcp.adapters.test_runner import run_tests_local

    started, release, calls = threading.Event(), threading.Event(), []

    def slow_first(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            started.set()
            assert release.wait(10)
        return {"executed_by_host": True, "passed": True, "command": ["pytest"]}

    monkeypatch.setattr("ccc_mcp.adapters.test_runner.execute_test_command", slow_first)
    manual = threading.Thread(target=run_tests_local, args=(ready, "demo", TASK))
    manual.start()
    assert started.wait(5)

    with pytest.raises(BridgeError, match="already running"):
        ready.run_configured_test_command("demo", TASK)
    assert len(calls) == 1, "the refused run must not have started a test"
    release.set()
    manual.join(10)
    assert [row["status"] for row in ledger(ready)] == ["FINISHED"]
    assert ready.task_control("demo", TASK)["host_test_running"] == 0


def test_the_manual_test_command_still_works_as_before(ready):
    """Same result as always, also on a paused task (the CLI never refused that)."""
    from ccc_mcp.adapters.test_runner import run_tests_local

    ready.configure_test_command("demo", PASSING)
    ready.pause_task("demo", TASK)

    result = run_tests_local(ready, "demo", TASK)

    assert result["executed_by_host"] is True and result["passed"] is True
    assert [row["status"] for row in ledger(ready)] == ["FINISHED"]
    assert ready.task_control("demo", TASK)["host_test_running"] == 0


# Platform limits: no reliable lock, no automatic recovery ----------------------------------


def test_where_locks_are_not_reliable_a_leftover_is_never_recovered_automatically(
    ready, tag, reap, monkeypatch
):
    kill(running_host_test(ready, tag, reap))
    for leftover in processes_with(tag):
        leftover.kill()
    wait_for(lambda: not processes_with(tag))
    monkeypatch.setattr(
        "ccc_mcp.core.tasks.lease_reliability",
        lambda folder: (False, "the lock directory is on a network file system (nfs)"),
    )
    ready.configure_test_command("demo", PASSING)

    with pytest.raises(BridgeError, match="network file system"):
        ready.run_configured_test_command("demo", TASK)
    refused = ready.recover_workers("demo")

    assert refused["interrupted_host_tests"] == []
    assert "network file system" in refused["host_tests_not_recovered"][0]["error"]
    assert ready.task_control("demo", TASK)["host_test_running"] == 1
    assert [row["status"] for row in ledger(ready)] == ["RUNNING"]
    assert host_test_evidence(ready) == []
    # Only an operator's explicit confirmation clears it, and that is recorded as such.
    confirmed = ready.recover_workers("demo", operator_confirmed=True)
    assert len(confirmed["interrupted_host_tests"]) == 1
    assert host_test_evidence(ready)[0]["detected"]["trigger"] == "operator_confirmed"


def test_a_normal_run_still_works_where_locks_are_not_reliable(ready, monkeypatch):
    monkeypatch.setattr("ccc_mcp.core.tasks.lease_reliability", lambda folder: (False, "unknown"))
    ready.configure_test_command("demo", PASSING)
    assert ready.run_configured_test_command("demo", TASK)["passed"] is True


def test_the_reliability_check_names_network_file_systems_and_other_platforms(
    tmp_path, monkeypatch
):
    from collections import namedtuple

    from ccc_mcp.core import host_test_run

    part = namedtuple("part", "mountpoint fstype")
    monkeypatch.setattr(host_test_run.psutil, "disk_partitions", lambda all: [
        part("/", "apfs"), part(str(tmp_path.parent), "nfs4")])
    ok, why = host_test_run.lease_reliability(tmp_path)
    assert ok is False and "nfs4" in why
    monkeypatch.setattr(host_test_run.psutil, "disk_partitions", lambda all: [part("/", "apfs")])
    assert host_test_run.lease_reliability(tmp_path) == (True, "")
    monkeypatch.setattr(host_test_run.psutil, "disk_partitions", lambda all: [])
    assert host_test_run.lease_reliability(tmp_path)[0] is False
    monkeypatch.setattr(host_test_run.os, "name", "nt")
    ok, why = host_test_run.lease_reliability(tmp_path)
    assert ok is False and "POSIX" in why


def test_the_operator_confirmation_is_a_cli_option_and_not_an_mcp_argument(tmp_path, human):
    import inspect

    from typer.testing import CliRunner

    from ccc_mcp.interfaces.cli import app
    from ccc_mcp.interfaces.server import create_server

    runner = CliRunner()
    shown = runner.invoke(app, ["task", "recover", "--help"])
    assert shown.exit_code == 0 and "--confirm-host-tests-stopped" in shown.output
    source = inspect.getsource(create_server)
    recover = source[source.index("async def recover_workers"):]
    assert "operator_confirmed" not in recover.split("@server.tool")[0]


# Audit trail ---------------------------------------------------------------------------------


def test_two_interruptions_then_a_pass_leave_one_unbroken_chain(ready, tag, reap):
    kill(running_host_test(ready, tag, reap))
    ready.configure_test_command("demo", sleeping_command(tag))
    second = start_runner(ready, reap)
    wait_for(lambda: len([r for r in ledger(ready) if r["status"] == "RUNNING"]) == 1
             and any(r["status"] == "INTERRUPTED" for r in ledger(ready)))
    wait_for(lambda: any(r["child"] for r in ledger(ready) if r["status"] == "RUNNING"))
    kill(second)
    ready.configure_test_command("demo", PASSING)
    ready.run_configured_test_command("demo", TASK)

    rows = ledger(ready)
    assert [row["status"] for row in rows] == ["INTERRUPTED", "INTERRUPTED", "FINISHED"]
    assert json.loads(rows[1]["detail"])["detected"]["trigger"] == "rerun"
    assert [json.loads(r["detail"]).get("recovers") for r in rows[1:]] == [rows[0]["id"], rows[1]["id"]]
    evidence = host_test_evidence(ready)
    assert [bool(item.get("interrupted")) for item in evidence] == [True, True, False]
    assert sum(1 for row in rows if row["status"] == "INTERRUPTED") == sum(
        1 for item in evidence if item.get("interrupted")
    ), "every interrupted row has its own evidence"
    for row in rows[:2]:
        assert json.loads(row["detail"])["evidence"].endswith("/TEST-RESULT.md")


def test_an_interrupted_test_cannot_pass_a_review_gate(ready, tag, reap):
    from ccc_mcp.core.flows import judge_gate  # noqa: F401  (the gate's own rules stay unchanged)

    ready.configure_test_command("demo", PASSING)
    assert ready.run_configured_test_command("demo", TASK)["passed"] is True
    kill(running_host_test(ready, tag, reap))
    ready.recover_workers("demo")
    source = {"id": "EXEC-gate", "result": json.dumps({"report": {"needs_revision": False}}),
              "role_ref": None, "step_id": None}

    gate = ready._flow_record_gate("demo", TASK, 2, "REVIEW", source)

    assert gate["verdict"] == "NEEDS_REVISION"
    assert "interrupted" in gate["reason"]


def test_ledger_status_changes_follow_the_append_only_rules(ready):
    ready.configure_test_command("demo", PASSING)
    ready.run_configured_test_command("demo", TASK)
    run_id = ledger(ready)[0]["id"]
    with ready.store.transaction() as conn:
        conn.execute(
            "INSERT INTO host_test_runs(id,project_id,task_id,owner,status,started) "
            "VALUES('HTRUN-open','demo',?,'{}','RUNNING','2026-01-01T00:00:00+00:00')", (TASK,))
        conn.execute("UPDATE host_test_runs SET child='{}' WHERE id='HTRUN-open'")
        conn.execute("UPDATE host_test_runs SET status='INTERRUPTED',finished='x' "
                     "WHERE id='HTRUN-open'")
    with sqlite3.connect(ready.store.path) as connection:
        for sql in (
            "UPDATE host_test_runs SET status='FINISHED' WHERE id='HTRUN-open'",
            "UPDATE host_test_runs SET status='INTERRUPTED' WHERE id=:run",
            "UPDATE host_test_runs SET detail='{}' WHERE id=:run",
            "UPDATE host_test_runs SET started='x' WHERE id=:run",
            "DELETE FROM host_test_runs WHERE id=:run",
        ):
            with pytest.raises(sqlite3.DatabaseError, match="immutable"):
                connection.execute(sql, {"run": run_id})
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO host_test_runs(id,project_id,task_id,owner,status,started) "
                "VALUES('HTRUN-bad','demo','T','{}','DONE','x')")


def test_a_run_that_someone_else_already_closed_does_not_clear_the_flag(ready):
    ready.configure_test_command("demo", PASSING)
    with ready.host_test_session("demo", TASK) as outcome:
        outcome[0] = "passed"
        with ready.store.transaction() as conn:  # reconciled elsewhere while it was running
            conn.execute("UPDATE host_test_runs SET status='INTERRUPTED',finished='x' "
                         "WHERE status='RUNNING'")
    assert ready.task_control("demo", TASK)["host_test_running"] == 1
    assert [row["status"] for row in ledger(ready)] == ["INTERRUPTED"]
