
import pytest

from ccc_mcp.adapters import agent_setup
from ccc_mcp.infra.errors import BridgeError


class FakeResult:
    def __init__(self, returncode=0, stdout=b""):
        self.returncode = returncode
        self.stdout = stdout


def test_setup_provider_already_installed_and_logged_in(monkeypatch):
    monkeypatch.setattr(agent_setup, "native_executable", lambda name: "C:/codex/codex.exe")
    monkeypatch.setattr(
        agent_setup.subprocess, "run", lambda argv, **kwargs: FakeResult(returncode=0)
    )
    result = agent_setup.setup_provider("codex")
    assert result == {"provider": "codex", "path": "C:/codex/codex.exe", "logged_in": True}


def test_setup_provider_installs_then_logs_in(monkeypatch):
    calls = []
    resolve_calls = {"n": 0}

    def fake_native_executable(name):
        resolve_calls["n"] += 1
        if resolve_calls["n"] == 1:
            raise BridgeError("not found yet")
        return "C:/codex/codex.exe"

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["npm", "install"]:
            return FakeResult(returncode=0)
        # First status check after install: not logged in. Then login succeeds,
        # then the final status re-check succeeds.
        if argv[1:] == ["login", "status"]:
            return FakeResult(returncode=1 if len(calls) <= 2 else 0)
        return FakeResult(returncode=0)

    monkeypatch.setattr(agent_setup, "native_executable", fake_native_executable)
    monkeypatch.setattr(agent_setup.shutil, "which", lambda name: "C:/npm/npm.cmd")
    monkeypatch.setattr(agent_setup.subprocess, "run", fake_run)

    result = agent_setup.setup_provider("codex")
    assert result == {"provider": "codex", "path": "C:/codex/codex.exe", "logged_in": True}
    assert ["npm", "install", "-g", agent_setup.NPM_PACKAGE["codex"]] in calls
    assert ["C:/codex/codex.exe", "login"] in calls


def test_setup_provider_claude_requires_node_22(monkeypatch):
    monkeypatch.setattr(agent_setup, "native_executable", lambda name: (_ for _ in ()).throw(BridgeError("missing")))
    monkeypatch.setattr(
        agent_setup.subprocess,
        "run",
        lambda argv, **kwargs: FakeResult(stdout=b"18") if argv[0] == "node" else FakeResult(),
    )
    with pytest.raises(BridgeError, match="Node.js 22"):
        agent_setup.setup_provider("claude")


def test_ensure_npm_raises_without_winget(monkeypatch):
    monkeypatch.setattr(agent_setup.shutil, "which", lambda name: None)
    with pytest.raises(BridgeError, match="WinGet"):
        agent_setup._ensure_npm()


def test_setup_provider_rejects_unsupported_provider():
    with pytest.raises(BridgeError, match="Unsupported provider"):
        agent_setup.setup_provider("grok")


def test_setup_provider_raises_when_login_still_incomplete(monkeypatch):
    monkeypatch.setattr(agent_setup, "native_executable", lambda name: "C:/codex/codex.exe")
    monkeypatch.setattr(
        agent_setup.subprocess, "run", lambda argv, **kwargs: FakeResult(returncode=1)
    )
    with pytest.raises(BridgeError, match="login is still incomplete"):
        agent_setup.setup_provider("codex")
