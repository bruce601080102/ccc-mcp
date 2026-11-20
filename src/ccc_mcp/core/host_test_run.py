"""Telling a live Host Test runner from the residue of one that died.

`task_controls.host_test_running` says "somebody is running this task's host tests", but a runner
that is killed (SIGKILL, power loss, an OOM kill) never gets to clear it, and a `finally` block
cannot help because the process never reaches it. So the flag alone proves nothing.

What proves a runner is alive is an operating-system file lease it holds for as long as it runs
(`locks/host-test-<project>-<task>.lock`). The kernel drops that lease the instant the holder
dies, however it died. The test process the runner starts *inherits the lease*, so if the runner
is killed while its test process lives, the lease stays held and nobody can start a second host
test beside the orphan. A caller that can take the lease therefore knows no runner and no test
process of that task is alive; one that cannot knows something really is running. No heartbeat,
timeout or guess is involved, so a slow process is never mistaken for a dead one.

When the lease is held but the recorded runner (pid and start time) is gone, the orphan is stopped
only if it is verifiably the recorded test process. Anything that cannot be identified, for example
an orphan whose pid was never recorded because the runner died in the instant after spawning it, is
left alone and the request is refused until it exits.

Limits, stated plainly:

* Automatic recovery is only done where the lease can be trusted: POSIX with a local file system
  (`lease_reliability`). On Windows (not verified), on network file systems and when the file system
  cannot be determined, a leftover flag stays blocked and says why. An operator who has checked that
  nothing is running may clear it with `ccc-mcp task recover --confirm-host-tests-stopped`.
* A flag with no run record was set by an earlier ccc-mcp version, which kept no lease. It cannot be
  told apart from a live run of that version, so it needs the same operator confirmation. Running
  an old and a new version against one home at the same time is not protected.
* The lease is a local file lock and does not span machines.
* A runner that is alive but hung keeps its lease; it is bounded by the test command's own timeout.
* The test process's own children are not holders of the lease; they are stopped with it.
"""

import os
import socket

import psutil

from ..infra.errors import BridgeError

ALREADY_RUNNING = "Host tests are already running for this task"

# What a recovered run records instead of a result. It is deliberately not a pass, and it is
# neither a failure of the tests (nothing was observed) nor "unverified because there was none".
INTERRUPTED_REASON = (
    "A host test run lost its runner before it finished (the process was stopped or crashed). "
    "Its result was discarded and is not counted as a pass. Run the host tests again."
)
LEGACY_RESIDUE_NOTE = (
    "The host-test flag was left set with no run record, as an earlier ccc-mcp version did when "
    "its runner was killed. Nothing is known about that run."
)


class HostTestBusy(BridgeError):
    """The lease is held: host tests are running for this task. `detail` is set when the runner
    that started them is gone yet something it started still holds the lease."""

    def __init__(self, detail=None):
        super().__init__(f"{ALREADY_RUNNING} ({detail})" if detail else ALREADY_RUNNING)
        self.detail = detail


def process_identity(pid):
    """A pid plus its start time: a pid alone can be reused by an unrelated process later."""
    return {
        "pid": pid,
        "create_time": psutil.Process(pid).create_time(),
        "host": socket.gethostname(),
    }


def child_identity(pid):
    """What is recorded about a provider CLI a worker started: pid and start time only."""
    try:
        return {"pid": pid, "create_time": psutil.Process(pid).create_time()}
    except psutil.Error:
        return None


def stop_process(child):
    """Stop the leftover test process a dead runner spawned, if and only if it is still the very
    process that was recorded. Returns "none" (nothing was recorded), "not_running", "terminated"
    or "failed"."""
    if not child:
        return "none"
    try:
        process = psutil.Process(child["pid"])
        if abs(process.create_time() - child["create_time"]) > 0.001:
            return "not_running"  # the pid now belongs to something else; never touch it
        if process.status() == psutil.STATUS_ZOMBIE:
            return "not_running"
        descendants = process.children(recursive=True)
        for item in reversed(descendants):
            try:
                item.kill()
            except psutil.NoSuchProcess:
                pass
        process.kill()
        _, alive = psutil.wait_procs([*descendants, process], timeout=5)
        return "failed" if alive else "terminated"
    except psutil.NoSuchProcess:
        return "not_running"
    except (psutil.AccessDenied, KeyError, TypeError, ValueError):
        return "failed"


NETWORK_FILESYSTEMS = frozenset({
    "nfs", "nfs3", "nfs4", "cifs", "smbfs", "smb", "smb3", "afpfs", "webdav", "davfs",
    "sshfs", "fuse.sshfs", "9p", "ceph", "glusterfs", "lustre", "ncpfs",
})


def lease_reliability(folder):
    """(True, "") when this platform and file system give a lease we can trust to prove that a
    runner is gone; (False, reason) otherwise. It only gates automatic *recovery*: where it
    says no, ccc-mcp keeps the old safe behaviour (a flagged task stays blocked) and says why,
    instead of pretending a lock proves anything."""
    if os.name != "posix":
        return False, (
            "file locks are only verified on POSIX systems; on this platform ccc-mcp cannot "
            "prove that an interrupted host test, and the process it started, are gone"
        )
    try:
        target = os.path.realpath(folder)
        owners = [
            part for part in psutil.disk_partitions(all=True)
            if target == part.mountpoint or target.startswith(part.mountpoint.rstrip("/") + "/")
        ]
        mount = max(owners, key=lambda part: len(part.mountpoint), default=None)
    except (OSError, psutil.Error):
        mount = None
    if mount is None:
        return False, "could not determine which file system holds the lock directory"
    if mount.fstype.lower() in NETWORK_FILESYSTEMS:
        return False, (
            f"the lock directory is on a network file system ({mount.fstype}), "
            "where file locks are not reliable"
        )
    return True, ""


def owner_alive(owner):
    """Whether the process recorded as a run's owner (pid and start time) still exists."""
    try:
        return abs(psutil.Process(owner["pid"]).create_time() - owner["create_time"]) <= 0.001
    except (psutil.NoSuchProcess, KeyError, TypeError):
        return False
    except psutil.Error:
        return True  # cannot tell: treat as alive, the safe answer


def outcome_of(result):
    if not result.get("executed_by_host"):
        return "not_executed"
    return "passed" if result.get("passed") else "failed"
