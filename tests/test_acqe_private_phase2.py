"""Public checks for Phase 2; real private answers live outside this repository."""

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "benchmarks/toy-agents/run.py"


def load_run():
    spec = importlib.util.spec_from_file_location("acqe_private_run", RUN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def run_module():
    return load_run()


def test_public_cases_have_only_agent_visible_fields(run_module):
    assert len(run_module.PUBLIC_CASES) >= 4
    for public in run_module.PUBLIC_CASES.values():
        assert set(public) == {"issue", "buggy", "visible"}
        assert "defect_label" not in public
        assert "hidden" not in public
        assert "own_good" not in public


def test_private_fixture_loader_rejects_repo_and_symlink_paths(run_module, tmp_path):
    with pytest.raises(RuntimeError, match="outside the source repository"):
        run_module.load_private_fixture(RUN)
    target = tmp_path / "fixture.json"
    target.write_text("{}")
    target.chmod(0o644)
    alias = tmp_path / "alias.json"
    alias.symlink_to(target)
    with pytest.raises(RuntimeError, match="must not be a symlink"):
        run_module.load_private_fixture(alias)
    if os.name != "nt":  # Owner and group/other permission bits only exist on POSIX.
        with pytest.raises(RuntimeError, match="readable by group or others"):
            run_module.load_private_fixture(target)
    nested_repo = tmp_path / "other-repo"
    nested_repo.mkdir()
    subprocess.run(["git", "init", "-q", str(nested_repo)], check=True)
    inside = nested_repo / "private.json"
    inside.write_text("{}")
    inside.chmod(0o600)
    with pytest.raises(RuntimeError, match="any Git repository"):
        run_module.load_private_fixture(inside)


def test_clone_preserves_symlinks_instead_of_copying_host_targets(run_module, tmp_path):
    source = run_module.make_repo(tmp_path, "clamp", "source")
    host_only = tmp_path / "host-only"
    host_only.write_text("host canary")
    (source / "external-link").symlink_to(host_only)
    cloned = run_module._clone_repo(source, tmp_path / "clone")
    assert (cloned / "external-link").is_symlink()
    # Windows reports the target with a "\\?\" long-path prefix; resolve() compares the same path.
    assert (cloned / "external-link").readlink().resolve() == host_only.resolve()


def test_case_wall_timer_interrupts_and_restores_alarm(run_module):
    if not hasattr(signal, "setitimer"):
        pytest.skip("POSIX timer required")
    original = signal.getsignal(signal.SIGALRM)
    with pytest.raises(TimeoutError, match="total wall-time"):
        with run_module.case_wall_timer(0.02):
            time.sleep(0.2)
    assert signal.getsignal(signal.SIGALRM) == original
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


def test_docker_case_budget_is_hard_even_between_calls(run_module):
    isolation = object.__new__(run_module.ACQEDockerIsolation)
    isolation.deadline = time.monotonic() - 1
    with pytest.raises(TimeoutError, match="total time budget"):
        isolation._remaining(900)
    isolation.case_call_start = 0
    isolation.calls = [{} for _ in range(5)]
    with pytest.raises(RuntimeError, match="Agent-call limit of five"):
        isolation.run_codex(Path("/work"), "prompt", "read-only")


def test_scoped_auth_file_strips_refresh_token_and_self_deletes(run_module, tmp_path):
    """The Agent container never sees the Host's real refresh_token (ACQE-GAP-ANALYSIS §18)."""
    docker_module = sys.modules[run_module.ACQEDockerIsolation.__module__]
    isolation = object.__new__(run_module.ACQEDockerIsolation)
    isolation.root = tmp_path
    auth = tmp_path / "real-auth.json"
    auth.write_text(json.dumps({
        "auth_mode": "chatgpt", "OPENAI_API_KEY": None, "last_refresh": "2026-01-01",
        "tokens": {
            "id_token": "id-secret", "access_token": "access-secret",
            "refresh_token": "refresh-secret", "account_id": "acct-secret",
        },
    }))
    isolation.auth = auth

    with isolation._scoped_auth_file() as scoped_path:
        assert scoped_path.parent == tmp_path / "scoped-auth"
        if os.name != "nt":  # chmod 0o600 has no effect on Windows.
            assert oct(scoped_path.stat().st_mode & 0o777) == "0o600"
        written = json.loads(scoped_path.read_text())
        assert written["tokens"]["refresh_token"] == docker_module._REVOKED_REFRESH_SENTINEL
        # The access token (what Codex needs to call the model) is preserved...
        assert written["tokens"]["access_token"] == "access-secret"
        # ...but the renewable, standing credential never reaches the copy at all.
        assert "refresh-secret" not in scoped_path.read_text()
    # And the scoped copy does not outlive the one container call that used it.
    assert not scoped_path.exists()


def test_agent_argv_never_mounts_the_real_auth_file(run_module, tmp_path):
    isolation = object.__new__(run_module.ACQEDockerIsolation)
    isolation.base = tmp_path
    isolation.network = "net"
    isolation.proxy = "proxy"
    isolation.auth = tmp_path / "real-auth.json"
    isolation.cache = tmp_path / "cache.json"
    repo = tmp_path / "case-repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    fake_scoped = tmp_path / "scoped-auth" / "fake.json"
    fake_scoped.parent.mkdir()
    fake_scoped.write_text("{}")

    _name, argv = isolation._agent_argv(repo, "read-only", ["echo", "hi"], fake_scoped)

    joined = " ".join(argv)
    assert str(isolation.auth) not in joined
    assert str(fake_scoped) in joined


def private_path():
    value = os.environ.get("ACQE_PRIVATE_FIXTURE")
    if not value:
        pytest.skip("Set ACQE_PRIVATE_FIXTURE for the opt-in Host-only isolation test")
    return Path(value)


@pytest.mark.real_agents
def test_private_fixture_container_canaries_and_host_verdicts(run_module):
    """No model calls: real Docker mount/network checks and private scoring."""
    fixture = run_module.load_private_fixture(private_path())
    assert not fixture.is_relative_to(ROOT)
    for name in run_module.PUBLIC_CASES:
        run_module.assert_fixture_not_in_committed_head(name)
    with run_module.ACQEDockerIsolation("gpt-6.1-sol", "medium", fixture, ROOT) as isolation:
        private_token = run_module._HOST_PRIVATE_ROOT.set(str(isolation.private))
        active_token = run_module._ACTIVE_ISOLATION.set(isolation)
        try:
            for name in run_module.PUBLIC_CASES:
                isolation.begin_case(300)
                repo = run_module.make_repo(isolation.base, name, "preflight")
                isolation._canary(repo, "workspace-write")
                isolation._canary(repo, "read-only")
                if name == next(iter(run_module.PUBLIC_CASES)):
                    with pytest.raises(TimeoutError, match="Agent container exceeded"):
                        isolation._run_agent_container(
                            repo, "read-only", ["sleep", "10"], timeout=1,
                        )
                    with pytest.raises(TimeoutError, match="total wall-time"):
                        with run_module.case_wall_timer(1):
                            isolation._run_agent_container(
                                repo, "read-only", ["sleep", "10"], timeout=30,
                            )
                    remaining = isolation._docker(
                        "ps", "--filter", "name=acqe-agent-", "--format", "{{.Names}}"
                    )
                    assert not remaining.stdout.strip()
                visible = run_module.pytest(repo, "tests")
                hidden = run_module.host_test_in_shadow(
                    repo, "tests/test_hidden.py", run_module.REVIEW_TASKS[name]["hidden"]
                )
                expected = 0 if run_module.REVIEW_TASKS[name]["defect_label"] == "none" else 1
                assert visible.returncode == 0
                assert hidden.returncode == expected
                assert not list(repo.rglob("*hidden*"))

                control = isolation.private / f"{name}-reference"
                shutil.copytree(repo, control, symlinks=True)
                (control / "mod.py").write_text(run_module.REVIEW_TASKS[name]["fixed"])
                (control / "tests/test_hidden.py").write_text(
                    run_module.REVIEW_TASKS[name]["hidden"]
                )
                assert isolation.run_pytest(control, "tests/test_hidden.py").returncode == 0

            name = next(
                case for case in run_module.PUBLIC_CASES
                if run_module.REVIEW_TASKS[case]["confirmation_witness"]
            )
            isolation.begin_case(300)
            repo = run_module.make_repo(isolation.base, name, "finding")
            secret = run_module.REVIEW_TASKS[name]
            verified = run_module.host_verify_findings(
                [
                    ("major", secret["confirmation_claim_terms"][0], secret["confirmation_witness"]),
                    ("minor", "unrelated claim", "unrelated evidence"),
                ], name, repo,
            )
            assert [item["verification_status"] for item in verified] == ["confirmed", "supported"]
            assert run_module.case_from(name, "host", repo, None)["resolved"] is False
        finally:
            run_module._ACTIVE_ISOLATION.reset(active_token)
            run_module._HOST_PRIVATE_ROOT.reset(private_token)


@pytest.mark.real_agents
def test_private_cases_mocked_b0_b1_b2_use_one_frozen_input(run_module, tmp_path):
    """Real private Host verdicts, mock Codex output; no model call."""
    run_module.load_private_fixture(private_path())
    clean_name = next(
        case for case in run_module.PUBLIC_CASES
        if run_module.REVIEW_TASKS[case]["defect_label"] == "none"
    )
    defect_name = next(
        case for case in run_module.PUBLIC_CASES
        if run_module.REVIEW_TASKS[case]["defect_label"] != "none"
    )

    class FakeIsolation:
        model = "gpt-6.1-sol"
        effort = "medium"

        def __init__(self, base):
            self.base = base
            self.calls = []

        def run_codex(self, repo, prompt, sandbox):
            self.calls.append((str(repo), prompt, sandbox))
            events = [
                {"type": "item.completed", "item": {"type": "agent_message", "text": "No findings."}},
                {"type": "turn.completed", "usage": {
                    "input_tokens": 20, "cached_input_tokens": 8, "output_tokens": 3,
                }},
            ]
            return subprocess.CompletedProcess(
                args=["codex"], returncode=0,
                stdout="\n".join(json.dumps(event) for event in events) + "\n", stderr="",
            )

    for name in (clean_name, defect_name):
        base = tmp_path / name
        base.mkdir()
        isolation = FakeIsolation(base)
        results = run_module.run_controlled_review_experiment(
            base, name, model=isolation.model, effort=isolation.effort, isolation=isolation,
        )
        assert len(isolation.calls) == 3
        assert len({case["run_envelope"]["repo_commit"] for case in results.values()}) == 1
        expected_resolved = name == clean_name
        assert all(case["resolved"] == expected_resolved for case in results.values())
        assert not any(case.get("findings") for case in results.values())
        assert all(not case.get("fix_attempted", False) for case in results.values())
        for _, prompt, _ in isolation.calls:
            assert run_module.REVIEW_TASKS[name]["hidden"] not in prompt
        assert [mode for _, _, mode in isolation.calls] == [
            "workspace-write", "read-only", "read-only",
        ]
