"""BUG-RESUME-001: a worker that was just (re)spawned must not look like an orphan.

Orphan judgement is based on the time since the worker was spawned (recorded in
options.worker_spawned_at), not on the execution's creation time. A paused task that is
resumed hours later has an old execution row but a freshly spawned worker.
"""

import json
import subprocess
from datetime import UTC, datetime, timedelta

import portalocker
import pytest
from conftest import approve, dispatch  # noqa: F401

from ccc_mcp.core import tasks as tasks_module
from ccc_mcp.core.tasks import ORPHAN_GRACE_SECONDS


def _ago(seconds):
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


def _set_created(bridge, execution_id, seconds_ago):
    bridge.store.execute(
        "UPDATE agent_executions SET created=? WHERE id=?", (_ago(seconds_ago), execution_id)
    )


def _set_spawned(bridge, execution_id, seconds_ago):
    bridge.store.execute(
        "UPDATE agent_executions SET options=json_set(options,'$.worker_spawned_at',?) WHERE id=?",
        (_ago(seconds_ago), execution_id),
    )


def _options(bridge, execution_id):
    row = bridge.store.one("SELECT options FROM agent_executions WHERE id=?", (execution_id,))
    return json.loads(row["options"])


@pytest.fixture
def spawns(bridge, monkeypatch):
    """Use the real spawn_worker but stub only the worker process start, recording it."""
    recorded = []

    class SubprocessProxy:
        def __getattr__(self, name):
            return getattr(subprocess, name)

        def Popen(self, argv, **kwargs):
            if "--execution" in argv:
                recorded.append(argv[argv.index("--execution") + 1])
                return None
            return subprocess.Popen(argv, **kwargs)

    monkeypatch.setattr(tasks_module, "subprocess", SubprocessProxy())
    monkeypatch.setattr(bridge, "spawn_worker", type(bridge).spawn_worker.__get__(bridge))
    return recorded


def _queued(bridge, human, spawned_seconds_ago=None, created_seconds_ago=120, pause=False):
    bridge.create_task("demo", "新增 value API", ["API 回傳 200"])
    queued = dispatch(bridge)
    eid = queued["execution_id"]
    if pause:
        bridge.pause_task("demo", "TASK-0001")
    _set_created(bridge, eid, created_seconds_ago)
    if spawned_seconds_ago is not None:
        _set_spawned(bridge, eid, spawned_seconds_ago)
    return eid


def test_resumed_worker_that_has_not_locked_yet_is_not_blocked(bridge, human, spawns):
    eid = _queued(bridge, human, pause=True)
    spawns.clear()  # dispatch itself spawned a worker; only the resume spawn matters

    bridge.resume_task("demo", "TASK-0001")  # records the spawn time, starts the process
    brief = bridge.get_task_status("demo", "TASK-0001")["brief"]

    assert spawns == [eid]
    assert brief["progress"]["orphaned"] is False
    assert brief["waiting_on"] == "AGENT_RUNNING"


def test_resume_spawns_exactly_one_worker_per_queued_execution(bridge, human, spawns):
    eid = _queued(bridge, human, pause=True)
    spawns.clear()

    bridge.resume_task("demo", "TASK-0001")
    bridge.resume_task("demo", "TASK-0001")  # not paused any more: must not spawn again

    assert spawns == [eid]


def test_spawn_records_time_and_keeps_other_options(bridge, human, spawns):
    eid = _queued(bridge, human, created_seconds_ago=0)
    before = _options(bridge, eid)

    bridge.spawn_worker(eid)
    after = _options(bridge, eid)

    assert "worker_spawned_at" in after
    assert datetime.fromisoformat(after["worker_spawned_at"]).tzinfo is not None
    without_key = lambda o: {k: v for k, v in o.items() if k != "worker_spawned_at"}  # noqa: E731
    assert without_key(after) == without_key(before)


def test_true_orphan_beyond_grace_is_still_flagged(bridge, human):
    eid = _queued(bridge, human, spawned_seconds_ago=ORPHAN_GRACE_SECONDS + 25)

    brief = bridge.get_task_status("demo", "TASK-0001")["brief"]

    assert brief["progress"]["orphaned"] is True
    assert brief["waiting_on"] == "BLOCKED"
    assert eid


def test_legacy_execution_without_spawn_time_keeps_old_behaviour(bridge, human):
    _queued(bridge, human, spawned_seconds_ago=None, created_seconds_ago=120)

    brief = bridge.get_task_status("demo", "TASK-0001")["brief"]

    assert brief["progress"]["orphaned"] is True
    assert brief["waiting_on"] == "BLOCKED"


def test_live_lock_beyond_grace_is_not_orphaned(bridge, human):
    eid = _queued(bridge, human, spawned_seconds_ago=ORPHAN_GRACE_SECONDS + 25)
    lockfile = bridge.home / "locks" / f"{eid}.lock"
    lockfile.parent.mkdir(exist_ok=True)

    with portalocker.Lock(lockfile, timeout=5):
        brief = bridge.get_task_status("demo", "TASK-0001")["brief"]

    assert brief["progress"]["orphaned"] is False
    assert brief["waiting_on"] == "AGENT_RUNNING"


def test_no_lock_probe_inside_grace_window(bridge, human, monkeypatch):
    _queued(bridge, human, spawned_seconds_ago=1)
    probes = []
    real_lock = portalocker.Lock

    def recording_lock(path, *args, **kwargs):
        if str(path).endswith(".lock") and "locks" in str(path) and "/locks/EXEC-" in str(path):
            probes.append(str(path))
        return real_lock(path, *args, **kwargs)

    monkeypatch.setattr(tasks_module.portalocker, "Lock", recording_lock)

    brief = bridge.get_task_status("demo", "TASK-0001")["brief"]

    assert probes == []
    assert brief["waiting_on"] == "AGENT_RUNNING"


def test_recover_workers_skips_worker_that_is_starting(bridge, human):
    eid = _queued(bridge, human, spawned_seconds_ago=1)

    result = bridge.recover_workers("demo")

    assert result["interrupted_executions"] == []
    assert bridge.store.one("SELECT status FROM agent_executions WHERE id=?", (eid,))["status"] == "QUEUED"
    assert bridge.task("demo", "TASK-0001")["state"] != "BLOCKED"


def test_recover_workers_still_recovers_true_orphan(bridge, human):
    eid = _queued(bridge, human, spawned_seconds_ago=ORPHAN_GRACE_SECONDS + 25)

    result = bridge.recover_workers("demo")

    assert result["interrupted_executions"] == [eid]
    assert bridge.task("demo", "TASK-0001")["state"] == "BLOCKED"


def test_status_read_does_not_change_task_state(bridge, human):
    _queued(bridge, human, spawned_seconds_ago=1)
    before = bridge.task("demo", "TASK-0001")["state"]

    bridge.get_task_status("demo", "TASK-0001")

    assert bridge.task("demo", "TASK-0001")["state"] == before
