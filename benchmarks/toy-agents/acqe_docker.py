"""Container boundary for private ACQE cases; no private answers enter Agent mounts.

Credential isolation (ACQE-GAP-ANALYSIS.md §18): the Host's long-lived
~/.codex/auth.json is read by this module only and is never bind-mounted
into an Agent container. Every container invocation instead gets a
freshly-minted, per-call copy with ``tokens.refresh_token`` replaced by
``_REVOKED_REFRESH_SENTINEL`` -- a value the OpenAI token endpoint will
reject -- so a container can present only the current access token, cannot
renew it, and never receives the account's standing refresh credential.
That scoped file lives for exactly one container invocation and is deleted
immediately afterward. This bounds exposure; it does not eliminate it. See
§18 for why full elimination (a credential-injecting broker so the
container never holds bearer material at all) is not implemented here.
"""

import contextlib
import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

IMAGE = "ccc-acqe-codex:0.163.0"
SCORER_IMAGE = "python:3.11-bookworm"
# Not a secret itself -- a deliberately invalid refresh_token value handed to
# every Agent container in place of the Host's real one. Codex CLI can still
# present the still-valid access_token it is minted alongside, but any
# attempt to use this value to mint a *new* access token is fail-closed: the
# OpenAI token endpoint rejects it rather than silently granting fresh access.
_REVOKED_REFRESH_SENTINEL = "revoked-for-isolated-agent-container"


class ACQEDockerIsolation:
    """Run Codex on an internal network and Host tests in networkless containers."""

    def __init__(self, model, effort, fixture_path, source_root):
        if not model or not effort:
            raise RuntimeError("Private comparison requires explicit model and effort")
        self.model, self.effort = model, effort
        self.image = IMAGE
        self.cli_version = "codex-cli 0.163.0"
        self.image_id = None
        self.scorer_image_id = None
        self.permissions = {
            "outer_os_sandbox": "Docker mount namespace; current public repo only",
            "codex_sandbox_mode": "external Docker boundary; Codex internal sandbox bypassed",
            "agent_network": "internal Docker network with OpenAI HTTPS allowlist proxy",
            "review_sandbox_mode": "read-only bind mount",
            "session_mode": "--no-daemon --ephemeral --ignore-user-config",
            "credential_mount": (
                "per-call scoped auth.json only; refresh_token replaced with a "
                "revoked sentinel; Host's real auth.json is never mounted; scoped "
                "copy is deleted immediately after each container invocation"
            ),
        }
        self.fixture_path = Path(fixture_path).resolve(strict=True)
        self.source_root = Path(source_root).resolve()
        self.auth = Path.home() / ".codex" / "auth.json"
        self.cache = Path.home() / ".codex" / "models_cache.json"
        if not self.auth.is_file() or not self.cache.is_file():
            raise RuntimeError("Codex login and model cache are required")
        cache = json.loads(self.cache.read_text())
        selected = next((item for item in cache.get("models", []) if item.get("slug") == model), None)
        levels = {item.get("effort") for item in (selected or {}).get("supported_reasoning_levels", [])}
        if not selected or effort not in levels:
            raise RuntimeError("Requested model/effort is absent from the local Codex cache")
        self.calls = []
        self._temporary = None
        self._active = set()
        self.deadline = None
        self.case_call_start = None
        self.network = "acqe-net-" + uuid.uuid4().hex[:12]
        self.proxy = "acqe-proxy-" + uuid.uuid4().hex[:12]
        self._network_created = False
        self._proxy_started = False

    @staticmethod
    def _docker(*argv, check=True, timeout=60):
        result = subprocess.run(
            ["docker", *argv], capture_output=True, text=True, timeout=timeout, check=False,
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"Docker command failed ({result.returncode}): {result.stderr.strip()}")
        return result

    def __enter__(self):
        image = self._docker("image", "inspect", "--format", "{{.Id}}", IMAGE)
        self.image_id = image.stdout.strip()
        scorer = self._docker("image", "inspect", "--format", "{{.Id}}", SCORER_IMAGE)
        self.scorer_image_id = scorer.stdout.strip()
        version = self._docker(
            "run", "--rm", "--platform", "linux/amd64", "--network", "none",
            IMAGE, "codex", "--version",
        )
        if self.cli_version not in version.stdout:
            raise RuntimeError("Pinned container Codex CLI version mismatch")
        self._temporary = tempfile.TemporaryDirectory(prefix="acqe-container-", dir="/private/tmp")
        self.root = Path(self._temporary.name)
        self.root.chmod(0o700)
        self.base = self.root / "cases"
        self.private = self.root / "host-private"
        self.base.mkdir()
        self.private.mkdir()
        (self.private / "canary").write_text(uuid.uuid4().hex)
        try:
            self._docker("network", "create", "--internal", self.network)
            self._network_created = True
            proxy_file = self.source_root / "benchmarks/toy-agents/docker/egress_proxy.py"
            self._docker(
                "run", "-d", "--rm", "--platform", "linux/amd64", "--name", self.proxy,
                "--network", self.network, "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "64",
                "--memory", "256m", "--user", "10001:10001",
                "--tmpfs", "/tmp:rw,nosuid,size=16m",
                "--mount", f"type=bind,src={proxy_file},dst=/proxy.py,readonly",
                SCORER_IMAGE, "python3", "/proxy.py",
            )
            self._proxy_started = True
            self._docker("network", "connect", "bridge", self.proxy)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        for name in list(self._active):
            self._docker("rm", "-f", name, check=False)
        self._active.clear()
        if self._proxy_started:
            self._docker("rm", "-f", self.proxy, check=False)
            self._proxy_started = False
        if self._network_created:
            self._docker("network", "rm", self.network, check=False)
            self._network_created = False
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None

    def begin_case(self, max_seconds):
        if not 60 <= max_seconds <= 4500:
            raise ValueError("max_case_seconds must be between 60 and 4500")
        self.deadline = time.monotonic() + max_seconds
        self.case_started = time.monotonic()
        self.case_call_start = len(self.calls)

    def _remaining(self, requested):
        if self.deadline is None:
            raise RuntimeError("Case time budget was not initialized")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("ACQE Case total time budget exhausted")
        return max(1, min(requested, remaining))

    @contextlib.contextmanager
    def _scoped_auth_file(self):
        """Mint a container-visible auth file that cannot renew itself.

        Reads the Host's real ~/.codex/auth.json (never passed to a
        container as a mount source) and writes a 0600 copy, under this
        run's private root, whose ``refresh_token`` is the revoked sentinel
        instead of the real value. The copy is deleted in ``finally``, so it
        exists only for the lifetime of the one container invocation that
        requested it.
        """
        real = json.loads(self.auth.read_text())
        tokens = dict(real.get("tokens") or {})
        tokens["refresh_token"] = _REVOKED_REFRESH_SENTINEL
        scoped = {
            "auth_mode": real.get("auth_mode"),
            "OPENAI_API_KEY": real.get("OPENAI_API_KEY"),
            "last_refresh": real.get("last_refresh"),
            "tokens": tokens,
        }
        directory = self.root / "scoped-auth"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / (uuid.uuid4().hex + ".json")
        path.write_text(json.dumps(scoped))
        path.chmod(0o600)
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)

    def _agent_argv(self, repo, sandbox, command, auth_path):
        repo = Path(repo).resolve()
        if repo.parent != self.base.resolve() or not repo.is_dir():
            raise RuntimeError("Agent repository is outside isolated cases")
        mount = f"type=bind,src={repo},dst=/work"
        if sandbox == "read-only":
            mount += ",readonly"
        elif sandbox != "workspace-write":
            raise RuntimeError("Unsupported Agent mount permission")
        name = "acqe-agent-" + uuid.uuid4().hex[:12]
        proxy_url = f"http://{self.proxy}:3128"
        argv = [
            "docker", "run", "--rm", "-i", "--platform", "linux/amd64",
            "--name", name, "--network", self.network, "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "128", "--memory", "2g", "--cpus", "2",
            "--user", "0:0", "--workdir", "/work",
            "--tmpfs", "/tmp:rw,nosuid,size=128m",
            "--tmpfs", "/home/acqe/.codex:rw,nosuid,size=64m",
            "--mount", mount,
            "--mount", f"type=bind,src={repo / '.git'},dst=/work/.git,readonly",
            "--mount", f"type=bind,src={auth_path},dst=/home/acqe/.codex/auth.json,readonly",
            "--mount", f"type=bind,src={self.cache},dst=/home/acqe/.codex/models_cache.json,readonly",
            "--env", "HOME=/home/acqe", "--env", "CODEX_HOME=/home/acqe/.codex",
            "--env", f"HTTPS_PROXY={proxy_url}", "--env", f"HTTP_PROXY={proxy_url}",
            "--env", f"ALL_PROXY={proxy_url}", "--env", "NO_PROXY=",
            IMAGE, *command,
        ]
        return name, argv

    def _run_agent_container(self, repo, sandbox, command, input_text=None, timeout=30):
        with self._scoped_auth_file() as auth_path:
            name, argv = self._agent_argv(repo, sandbox, command, auth_path)
            self._active.add(name)
            try:
                return subprocess.run(
                    argv, input=input_text, capture_output=True, text=True,
                    timeout=self._remaining(timeout), check=False,
                )
            except subprocess.TimeoutExpired as error:
                self._docker("rm", "-f", name, check=False)
                raise TimeoutError("ACQE Agent container exceeded its time budget") from error
            except BaseException:
                self._docker("rm", "-f", name, check=False)
                raise
            finally:
                self._active.discard(name)

    def _canary(self, repo, sandbox):
        link = Path(repo) / (".acqe-canary-" + uuid.uuid4().hex)
        link.symlink_to(self.private / "canary")
        paths = {
            "public": "/work/mod.py",
            "host_private": str(self.private / "canary"),
            "private_fixture": str(self.fixture_path),
            "source_history": str(self.source_root / ".git"),
            "symlink": "/work/" + link.name,
        }
        probe = (
            "import json,pathlib,sys\n"
            "p=json.loads(sys.argv[1])\n"
            "def readable(x):\n"
            " try:\n"
            "  target=pathlib.Path(x)\n"
            "  list(target.iterdir()) if target.is_dir() else target.read_bytes()\n"
            "  return True\n"
            " except OSError: return False\n"
            "print(json.dumps({k:readable(v) for k,v in p.items()}))\n"
        )
        try:
            result = self._run_agent_container(
                repo, sandbox, ["python3", "-c", probe, json.dumps(paths)], timeout=30,
            )
        finally:
            link.unlink(missing_ok=True)
        try:
            observed = json.loads(result.stdout) if result.returncode == 0 else {}
        except ValueError:
            observed = {}
        if observed != {key: key == "public" for key in paths}:
            raise RuntimeError("Container file canary failed; refusing Agent call")
        write_path = Path(repo) / (".acqe-write-" + uuid.uuid4().hex)
        write_probe = (
            "import pathlib,sys\n"
            "try: pathlib.Path(sys.argv[1]).write_text('canary'); print('writable')\n"
            "except OSError: print('denied')\n"
        )
        try:
            write_result = self._run_agent_container(
                repo, sandbox,
                ["python3", "-c", write_probe, "/work/" + write_path.name], timeout=30,
            )
        finally:
            write_path.unlink(missing_ok=True)
        expected_write = "denied" if sandbox == "read-only" else "writable"
        if write_result.returncode != 0 or write_result.stdout.strip() != expected_write:
            raise RuntimeError("Container write-permission canary failed; refusing Agent call")
        git_write = self._run_agent_container(
            repo, sandbox,
            ["python3", "-c", write_probe, "/work/.git/hooks/acqe-canary"], timeout=30,
        )
        if git_write.returncode != 0 or git_write.stdout.strip() != "denied":
            raise RuntimeError("Git metadata mount is writable; refusing Agent call")
        network_probe = (
            "import socket,sys\n"
            "s=socket.create_connection((sys.argv[1],3128),3)\n"
            "s.sendall(b'CONNECT localhost:443 HTTP/1.1\\r\\nHost: localhost\\r\\n\\r\\n')\n"
            "print(s.recv(80).split(b'\\r\\n',1)[0].decode())\n"
        )
        denied = self._run_agent_container(
            repo, sandbox, ["python3", "-c", network_probe, self.proxy], timeout=30,
        )
        if denied.returncode != 0 or "403 Forbidden" not in denied.stdout:
            raise RuntimeError("Container network canary failed; refusing Agent call")
        credential_probe = (
            "import json\n"
            "d=json.load(open('/home/acqe/.codex/auth.json'))\n"
            "print(d.get('tokens',{}).get('refresh_token',''))\n"
        )
        credential = self._run_agent_container(repo, sandbox, ["python3", "-c", credential_probe], timeout=30)
        if credential.returncode != 0 or credential.stdout.strip() != _REVOKED_REFRESH_SENTINEL:
            raise RuntimeError("Credential-scope canary failed; refusing Agent call")

    def run_codex(self, repo, prompt, sandbox, timeout=900):
        if self.case_call_start is None or len(self.calls) - self.case_call_start >= 5:
            raise RuntimeError("ACQE Case Agent-call limit of five reached")
        self._canary(repo, sandbox)
        login = self._run_agent_container(repo, sandbox, ["codex", "login", "status"], timeout=30)
        if login.returncode != 0 or "Logged in" not in login.stdout + login.stderr:
            raise RuntimeError("Codex login failed inside Agent container")
        command = [
            "codex", "--no-daemon", "exec", "--dangerously-bypass-approvals-and-sandbox",
            "--ignore-user-config", "--ephemeral", "--skip-git-repo-check", "--json",
            "--model", self.model, "-c", "model_reasoning_effort=" + self.effort,
            "--cd", "/work", "-",
        ]
        started = time.monotonic()
        record = {
            "repo": Path(repo).name, "sandbox": sandbox, "prompt": prompt,
            "requested_model": self.model, "requested_effort": self.effort,
            "isolation": "internal Docker network, allowlist proxy, current repo mount only",
            "os_canary": "passed", "sandboxed_login": "passed",
            "credential_scope": "passed (access-token only; refresh_token revoked-sentinel)",
        }
        try:
            result = self._run_agent_container(
                repo, sandbox, command, input_text=prompt, timeout=timeout,
            )
        except TimeoutError:
            record.update(seconds=round(time.monotonic() - started, 2), status="timeout")
            self.calls.append(record)
            raise
        usage = {}
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "turn.completed":
                for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                    value = (event.get("usage") or {}).get(key)
                    if isinstance(value, int) and value >= 0:
                        usage[key] = usage.get(key, 0) + value
        record.update({
            "seconds": round(time.monotonic() - started, 2),
            "returncode": result.returncode, "tokens": usage or None,
            "stdout": result.stdout, "stderr": result.stderr,
        })
        self.calls.append(record)
        return result

    def run_pytest(self, repo, *paths):
        """Run Agent-authored code in a networkless, read-only scorer container."""
        repo = Path(repo).resolve()
        if not repo.is_relative_to(self.root) or not repo.is_dir():
            raise RuntimeError("Host scorer repository is outside private Case root")
        pattern = Path(paths[0]).name if paths and Path(paths[0]).suffix == ".py" else "test*.py"
        command = [
            "docker", "run", "--rm", "--platform", "linux/amd64", "--network", "none",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "64", "--memory", "512m", "--user", "10001:10001",
            "--tmpfs", "/tmp:rw,nosuid,size=32m",
            "--mount", f"type=bind,src={repo},dst=/work,readonly",
            "--workdir", "/work", "--env", "PYTHONDONTWRITEBYTECODE=1",
            SCORER_IMAGE, "python3", "-m", "unittest", "discover", "-s", "tests",
            "-p", pattern, "-v",
        ]
        result = subprocess.run(
            command, capture_output=True, text=True,
            timeout=self._remaining(120), check=False,
        )
        output = result.stdout + result.stderr
        if "Ran " not in output or result.returncode not in (0, 1):
            raise RuntimeError("Networkless Host scorer failed without a valid test verdict")
        if "FAILED (failures=1)" in output:
            output += "\n1 failed\n"
        return SimpleNamespace(returncode=result.returncode, stdout=output, stderr=result.stderr)
