"""Run real Codex agents on small self-made bug-fix tasks, several ways, and compare.

  direct    one `codex exec` on the issue text                             (Arm A)
  b_retry   one `codex exec`, plus one bounded TEST-GUIDED RETRY           (Arm B_retry)
            follow-up if the host-run visible tests fail the first time —
            see the "b_retry is not a review" note below before using this
            arm's results to say anything about review quality.
  ccc-mcp   the SA -> SD/RD -> host tests -> REVIEW workflow, all roles on Codex
                                                                            (Arm C0)

See docs/development/ACQE-GAP-ANALYSIS.md §§11–15 for the arm plan and handoff.
The separate `review-experiment` command contains the B0/B1/B2 controlled-review
prototype. C1 and D are still unimplemented.

IMPORTANT — `b_retry` is NOT a review arm. It re-runs the exact same agent-visible test
suite as feedback on failure; nothing inspects the implementation for problems the
visible tests don't already check. A defect the visible tests don't cover is invisible
to this arm on every round, pass or fail. Do not report `b_retry`'s results as evidence
about "review effectiveness" — it only answers a narrower question: does one bounded
test-guided retry help over a single unreviewed attempt. The conceptually different
`B_review` is now prototyped separately as B1/B2 under `review-experiment`, with
an actual critique pass even when visible tests already pass.

Ground truth is a hidden test file the agent never sees. Everything runs in a throwaway
directory and a throwaway ccc-mcp home. This SPENDS Codex quota; run one task first:

    python benchmarks/toy-agents/run.py run --out OUT_DIR clamp
    python benchmarks/toy-agents/run.py run --out OUT_DIR       # every task
    python benchmarks/toy-agents/run.py run --out OUT_DIR --arms direct,b_retry,ccc clamp

`--arms` defaults to `direct,ccc` — unchanged from before `b_retry` existed, so existing
invocations without `--arms` spend exactly the same quota, in the same order, writing
the same two files, as before this round's change. `run_direct`, `run_ccc`, and
`case_from` were not modified at all this round (see §11.5/§11.6 of the Gap Analysis doc
for why, and what that means for the "actual model version" gap noted there).

Manual run (e.g. ChatGPT-only through the ccc-mcp UI, no Codex quota used here):

    python benchmarks/toy-agents/run.py prepare WORK_DIR   # makes the buggy repos + ISSUES.md
    ... register each repo as a ccc-mcp project and let the agent fix it ...
    python benchmarks/toy-agents/run.py judge WORK_DIR     # hidden test + score per task

Writes one JSONL file per selected arm (direct.jsonl / b_retry.jsonl / ccc.jsonl, each
with tokens_total and resolved) to OUT_DIR, ready for `ccc-mcp compare-tokens` and
`ccc-mcp validate-scoring`.
"""

import argparse
import ast
import asyncio
import contextvars
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from acqe_docker import ACQEDockerIsolation  # noqa: E402
from acqe_public_cases import PUBLIC_CASES  # noqa: E402
from toybench import REVIEW_ONLY_TASKS, TASKS  # noqa: E402

from ccc_mcp.adapters.agents.base import native_executable  # noqa: E402
from ccc_mcp.core.flows import parse_findings  # noqa: E402
from ccc_mcp.infra.codex_account import read_account_usage  # noqa: E402
from ccc_mcp.infra.telemetry import usage_from_output  # noqa: E402
from ccc_mcp.service import CCCMCP  # noqa: E402
from ccc_mcp.worker import run_execution  # noqa: E402

ISSUES = {
    "clamp": "clamp(x, lo, hi) in mod.py should keep x within the range [lo, hi], but values above hi are not limited. Fix it.",
    "slugify": "slugify in mod.py should return a lowercase slug with words joined by single hyphens. Extra spaces currently produce extra hyphens. Fix it.",
    "average": "average in mod.py crashes on an empty list. It should return None for an empty list. Fix it.",
    "first_word": "first_word in mod.py should return the first word of the string, or an empty string if there is none. It misbehaves with leading spaces and empty input. Fix it.",
    "parse_bool": "parse_bool in mod.py should treat 'true', '1' and 'yes' (any letter case, surrounding whitespace ignored) as True and everything else as False. Fix it.",
    "dedupe": "dedupe in mod.py should remove duplicates while keeping the order in which items first appear. Fix it.",
    "architecture_risk": "Review the small cache in mod.py. Its current callers reuse a key without changing its source. Preserve the documented behavior; report any architectural assumptions that need more evidence before changing the design.",
}

REVIEW_TASKS = {**TASKS, **REVIEW_ONLY_TASKS}
_HOST_PRIVATE_ROOT = contextvars.ContextVar("acqe_host_private_root", default=None)
_ACTIVE_ISOLATION = contextvars.ContextVar("acqe_active_isolation", default=None)


@contextmanager
def case_wall_timer(seconds):
    """Interrupt the complete private Case, including Host work, at its deadline."""
    previous = signal.getsignal(signal.SIGALRM)

    def expired(signum, frame):
        raise TimeoutError("ACQE Case total wall-time limit exceeded")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def load_private_fixture(path):
    """Join public tasks with Host-only answers without copying them to Agent repos."""
    candidate = Path(path)
    if candidate.is_symlink():
        raise RuntimeError("Private Fixture path must not be a symlink")
    target = candidate.resolve(strict=True)
    if target.is_relative_to(ROOT):
        raise RuntimeError("Private Fixture must be outside the source repository")
    containing_repo = subprocess.run(
        ["git", "-C", str(target.parent), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False, timeout=10,
    )
    if containing_repo.returncode == 0:
        raise RuntimeError("Private Fixture must not be inside any Git repository")
    if target.stat().st_uid != os.getuid():
        raise RuntimeError("Private Fixture must be owned by the Host user")
    if target.stat().st_mode & 0o077:
        raise RuntimeError("Private Fixture must not be readable by group or others")
    document = json.loads(target.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1 or set(document.get("cases", {})) != set(PUBLIC_CASES):
        raise RuntimeError("Private Fixture schema or case set does not match public cases")
    required = {"hidden", "own_good", "fixed", "confirmation_witness",
                "confirmation_claim_terms", "defect_label"}
    for name, public in PUBLIC_CASES.items():
        secret = document["cases"][name]
        if not required <= secret.keys():
            raise RuntimeError(f"Private Fixture for {name} is incomplete")
        ISSUES[name] = public["issue"]
        REVIEW_TASKS[name] = {**public, **secret}
    return target


def assert_fixture_not_in_committed_head(name):
    """Fail closed if a would-be private answer is already in the public source.

    The generated repo has no origin remote, but the original repository's
    committed Fixture can be fetched separately. OS file rules cannot make a
    committed answer private. Inspect source literals without executing them.
    """
    source = git(ROOT, "show", "HEAD:tests/toybench.py", capture=True)
    published = {"hidden": set(), "own_good": set(), "fixed": set()}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg in published:
                    try:
                        value = ast.literal_eval(keyword.value)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(value, str):
                        published[keyword.arg].add(value)
    exposed = [
        field for field, values in published.items()
        if REVIEW_TASKS[name].get(field) in values
    ]
    if exposed:
        raise RuntimeError(
            f"Private Fixture for {name} has committed {exposed} in HEAD; "
            "refusing the Codex experiment"
        )


def git(repo, *args, capture=False):
    # Auto maintenance would detach after a commit and briefly hold .git/objects/maintenance.lock,
    # which breaks any copy of the repo made while it runs.
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "core.hooksPath=/dev/null", "-c", "maintenance.auto=false", "-c", "gc.auto=0",
         "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    )
    return done.stdout if capture else None


def pytest(repo, *paths):
    isolation = _ACTIVE_ISOLATION.get()
    if isolation is not None:
        return isolation.run_pytest(repo, *paths)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *paths],
        cwd=repo, capture_output=True, text=True, check=False,
    )


def host_test_in_shadow(repo, relative_path, test_source):
    """Run a private host test in a disposable copy, never in an agent repo.

    Pytest may leave bytecode after the .py file is removed. Keeping the whole
    private test run in this shadow prevents later agents from reading that cache.
    """
    with tempfile.TemporaryDirectory(
        prefix="acqe-host-test-", dir=_HOST_PRIVATE_ROOT.get()
    ) as folder:
        shadow = Path(folder) / "repo"
        shutil.copytree(
            repo, shadow, symlinks=True,
            ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__"),
        )
        target = shadow / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(test_source)
        return pytest(shadow, relative_path)


def make_repo(base, name, label):
    spec = REVIEW_TASKS[name]
    repo = Path(base) / f"{name}-{label}"
    (repo / "tests").mkdir(parents=True)
    (repo / "conftest.py").write_text("import sys\nsys.path.insert(0, '.')\n")
    (repo / "mod.py").write_text(spec["buggy"])
    (repo / "tests" / "test_visible.py").write_text(spec["visible"])
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def single_module(name):
    return f"{name}_mod"


# --- Run metadata (ACQE Phase 1 reliability/reproducibility pass) ---------------------
#
# Every arm attaches one of these to its case via case_from()'s existing `extra`
# parameter — purely additive, case_from() itself is never modified. See
# docs/development/ACQE-GAP-ANALYSIS.md §12 for the full design rationale and the
# fairness gaps this does and doesn't close.

_CODEX_VERSION_CACHE = {}


def codex_cli_version():
    """Best-effort `codex --version` string, cached per process. Confirmed from source
    (infra/telemetry.py's usage_from_output()) that Codex's own --json exec output never
    exposes which model served a turn — `reported_models` is only ever populated for the
    claude branch — so the CLI version is the most specific execution-config fact this
    harness can honestly record for Codex runs today. See model_record()'s
    "observed_model", which is correspondingly always None for codex."""
    if "v" not in _CODEX_VERSION_CACHE:
        try:
            path = native_executable("codex")
            result = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=30, check=False,
            )
            _CODEX_VERSION_CACHE["v"] = (result.stdout or result.stderr).strip() or None
        except Exception:
            _CODEX_VERSION_CACHE["v"] = None
    return _CODEX_VERSION_CACHE["v"]


def repo_commit(repo):
    """The baseline commit `make_repo` created — the "Repository 基準 Commit" every arm
    starts from. None if it can't be read (should not happen for a harness-made repo)."""
    try:
        return git(repo, "rev-parse", "HEAD", capture=True).strip()
    except Exception:
        return None


def _elapsed_seconds(created, finished):
    if not created or not finished:
        return None
    try:
        return round((datetime.fromisoformat(finished) - datetime.fromisoformat(created)).total_seconds(), 2)
    except (TypeError, ValueError):
        return None


def model_record(provider, requested_model=None, observed_model=None, requested_effort=None,
                 cli_version_override=None):
    """One honest, consistent model-identity record, the same shape for every arm. Never
    upgrades "requested" into "observed": `model_verification_status` is "confirmed" only
    when an `observed_model` was actually extracted from the CLI's own output, never from
    what was merely asked for. Today this is "unknown" for every arm in this harness: no
    arm passes an explicit --model override (`requested_model` is honestly None, not
    guessed), and Codex's --json output doesn't expose model identity at all (see
    codex_cli_version()'s docstring) — an acknowledged, pre-existing gap, not fixed here;
    see docs/development/ACQE-GAP-ANALYSIS.md §12's correctness notes."""
    if observed_model:
        status = "confirmed"
    elif requested_model:
        status = "requested_unconfirmed"
    else:
        status = "unknown"
    return {
        "provider": provider,
        "requested_model": requested_model,
        "observed_model": observed_model,
        "requested_effort": requested_effort,
        "model_verification_status": status,
        "cli_version": (cli_version_override or codex_cli_version()) if provider == "codex" else None,
    }


def run_envelope(*, run_id, case_id, arm_id, repo, budget, tool_permissions, stages,
                  provider, requested_model=None, observed_model=None, requested_effort=None,
                  cli_version_override=None):
    """The full "how was this actually run" record every arm attaches to its case.
    `stages` is a list of per-agent-call dicts (each at least `{"name", "seconds"}`), so a
    run with retries or multiple roles keeps its full timing breakdown, not just one
    total. Returned pre-wrapped under the `run_envelope` key, ready to merge into
    case_from()'s `extra` dict."""
    return {
        "run_envelope": {
            "run_id": run_id,
            "case_id": case_id,
            "arm_id": arm_id,
            "repo_commit": repo_commit(repo),
            "budget": budget,
            "tool_permissions": tool_permissions,
            "stages": stages,
            **model_record(provider, requested_model, observed_model, requested_effort,
                           cli_version_override),
        }
    }


def judge(repo, name, sub=None):
    """Diff, the host-visible test run, and the hidden-test verdict, in that order, so the
    hidden file is never present while the agent works. `sub` is the task's folder inside
    a single combined repository (None for one repository per task)."""
    git(repo, "add", "-A")
    diff = git(repo, "diff", "--cached", "HEAD", "--", sub or ".", capture=True)
    tests = f"{sub}/tests" if sub else "tests"
    local = pytest(repo, tests)
    hidden = f"{tests}/" + (f"test_{name}_hidden.py" if sub else "test_hidden.py")
    text = REVIEW_TASKS[name]["hidden"]
    source = text.replace("from mod import", f"from {single_module(name)} import") if sub else text
    resolved = host_test_in_shadow(repo, hidden, source).returncode == 0
    return diff, local, resolved


def case_from(name, label, repo, tokens, extra=None, sub=None):
    diff, local, resolved = judge(repo, name, sub)
    total = None
    if tokens:
        total = tokens.get("input_tokens", 0) + tokens.get("output_tokens", 0)
    case = {
        "instance_id": name,
        "resolved": resolved,
        "role": "SD/RD",
        "diff": diff,
        "test_result": {"executed_by_host": True, "passed": local.returncode == 0, "stdout": local.stdout},
        "report": {"summary": f"Fixed {name}", "next_action": "", "findings": []},
        "acceptance": [f"{name} behaves as the issue describes"],
        "static_findings": [] if diff else None,  # production has no lint target without a diff
        "tokens": tokens or {},
        "tokens_total": total,
        "usage_complete": total is not None,
        "arm": label,
    }
    case.update(extra or {})
    return case


_SANDBOX_TOOL_PERMISSIONS = {
    "codex_sandbox_mode": "workspace-write", "approval_policy": "never",
    "skills": "none (no Skills installed in this toy benchmark)",
    "mcp_tools": "none (no MCP servers configured in this toy benchmark)",
    "structured_output": "none — unlike ccc-mcp's real codex adapter (adapters/agents/"
                          "codex.py), this arm never passes --output-schema/"
                          "--output-last-message; see ACQE-GAP-ANALYSIS.md §13.1",
}


def codex_exec_argv(repo, model=None, effort=None, sandbox="workspace-write"):
    """The exact `codex exec` invocation `direct`/`b_retry` use. `model`/`effort`, when
    given, are inserted with the identical flag syntax ccc-mcp's own real codex adapter
    uses (`adapters/agents/codex.py`'s `--model <x>` / `-c model_reasoning_effort=<x>`,
    at the same position right after `exec`) — a pinned experiment requests the same
    thing, the same way, on every arm. Default (`model`/`effort` both None, `sandbox`
    `workspace-write`) is byte-identical to this harness's pre-pinning argv, so existing
    callers that never pass any of these are unaffected. `sandbox="read-only"` is used
    by the Controlled Review Experiment's review stage (Decision 2, §14) — a reviewer
    that structurally cannot write files can't blur into a second implementation
    attempt, mirroring this codebase's own `READ_ONLY_ROLES` design for its real AR
    role."""
    argv = [
        native_executable("codex"), "-a", "never", "exec", "--ignore-user-config",
        "--skip-git-repo-check", "--sandbox", sandbox, "--json",
        "--cd", str(repo), "-",
    ]
    if model:
        argv[4:4] = ["--model", model]
    if effort:
        argv[4:4] = ["-c", "model_reasoning_effort=" + effort]
    return argv


class ACQEIsolation:
    """macOS OS sandbox for the controlled review experiment.

    The current generated case repository, system runtime, a clean temporary
    HOME, and two Codex credential/cache files are reachable. The original
    developer HOME, other temporary paths, mounted volumes, private host-test
    directory, Docker socket, and sibling case repositories are denied.
    Every model call first repeats an actual filesystem canary and login check.
    """

    _ACCESS_PROBE = (
        "import json, pathlib, sys\n"
        "paths=json.loads(sys.argv[1])\n"
        "def readable(path):\n"
        " try:\n"
        "  pathlib.Path(path).read_bytes(); return True\n"
        " except OSError: return False\n"
        "print(json.dumps({key:readable(value) for key,value in paths.items()}))\n"
    )

    def __init__(self, model, effort):
        if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
            raise RuntimeError("Controlled review requires a tested macOS OS sandbox")
        if not model or not effort:
            raise RuntimeError("Controlled review requires explicit --model and --effort")
        cache = Path.home() / ".codex" / "models_cache.json"
        auth = Path.home() / ".codex" / "auth.json"
        if not cache.is_file() or not auth.is_file():
            raise RuntimeError("Codex model cache and login must be available locally")
        data = json.loads(cache.read_text())
        selected = next((item for item in data.get("models", []) if item.get("slug") == model), None)
        levels = {item.get("effort") for item in (selected or {}).get("supported_reasoning_levels", [])}
        if not selected or effort not in levels:
            raise RuntimeError(f"Model {model!r} with effort {effort!r} is not in the local Codex cache")
        self.model, self.effort = model, effort
        self.auth, self.cache = auth.resolve(), cache.resolve()
        self._temporary = None
        self._token = None
        self._isolation_token = None
        self.calls = []

    def __enter__(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="acqe-isolated-", dir="/private/tmp")
        self.root = Path(self._temporary.name)
        self.base = self.root / "cases"
        self.private = self.root / "host-private"
        self.profiles = self.root / "profiles"
        self.home = self.root / "agent-home"
        for path in (self.base, self.private, self.profiles, self.home / ".codex", self.root / "agent-tmp"):
            path.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        (self.private / "canary").write_text(uuid.uuid4().hex)
        (self.home / ".codex" / "auth.json").symlink_to(self.auth)
        (self.home / ".codex" / "models_cache.json").symlink_to(self.cache)
        self.env = {
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
            "HOME": str(self.home),
            "CODEX_HOME": str(self.home / ".codex"),
            "TMPDIR": str(self.root / "agent-tmp"),
            "LANG": "C.UTF-8",
        }
        self._token = _HOST_PRIVATE_ROOT.set(str(self.private))
        self._isolation_token = _ACTIVE_ISOLATION.set(self)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._isolation_token is not None:
            _ACTIVE_ISOLATION.reset(self._isolation_token)
        if self._token is not None:
            _HOST_PRIVATE_ROOT.reset(self._token)
        if self._temporary is not None:
            self._temporary.cleanup()

    def _profile(self, repo):
        repo = Path(repo).resolve()
        if repo.parent != self.base.resolve() or not repo.is_dir():
            raise RuntimeError("Agent repo is outside the isolated cases directory")
        denied = [Path("/Users"), Path("/Volumes"), Path("/Network"), self.private]
        denied += [path for path in self.base.iterdir() if path.is_dir() and path.resolve() != repo]
        profile = [
            "(version 1)",
            "(allow default)",
            '(deny file-read* (subpath "/private/tmp"))',
            '(deny file-write* (subpath "/private/tmp"))',
            '(allow file-read* (literal "/private/tmp"))',
            f"(allow file-read* (subpath {json.dumps(str(self.root))}))",
            f"(allow file-write* (subpath {json.dumps(str(self.root))}))",
            f"(deny file-write* (subpath {json.dumps(str(self.profiles))}))",
            *(f"(deny file-read* (subpath {json.dumps(str(path))}))" for path in denied),
            *(f"(deny file-write* (subpath {json.dumps(str(path))}))" for path in denied),
            '(deny network-outbound (remote ip "localhost:*"))',
            '(deny network-outbound (remote unix-socket))',
            '(deny file-read* (literal "/var/run/docker.sock"))',
            '(deny file-write* (literal "/var/run/docker.sock"))',
            f"(allow file-read* (literal {json.dumps(str(self.auth))}))",
            f"(allow file-read* (literal {json.dumps(str(self.cache))}))",
        ]
        target = self.profiles / f"{repo.name}-{uuid.uuid4().hex}.sb"
        target.write_text("\n".join(profile) + "\n")
        return target

    def _test_profile(self, repo, private):
        """Keep agent-authored test code away from host secrets and other arms."""
        repo = Path(repo).resolve()
        expected_parent = self.private if private else self.base
        actual_parent = repo.parent.parent if private else repo.parent
        if not repo.is_dir() or actual_parent.resolve() != expected_parent.resolve():
            raise RuntimeError("Test repository is outside the controlled sandbox")
        denied = [Path("/Users"), Path("/Volumes"), Path("/Network"),
                  self.base if private else self.private]
        if not private:
            denied += [path for path in self.base.iterdir() if path.is_dir() and path.resolve() != repo]
        profile = [
            "(version 1)", "(allow default)",
            '(deny file-read* (subpath "/private/tmp"))',
            '(deny file-write* (subpath "/private/tmp"))',
            '(allow file-read* (literal "/private/tmp"))',
            f"(allow file-read* (subpath {json.dumps(str(self.root))}))",
            f"(allow file-write* (subpath {json.dumps(str(self.root))}))",
            f"(deny file-write* (subpath {json.dumps(str(self.profiles))}))",
            *(f"(deny file-read* (subpath {json.dumps(str(path))}))" for path in denied),
            *(f"(deny file-write* (subpath {json.dumps(str(path))}))" for path in denied),
            "(deny network*)",
            '(allow file-read* (literal "/Users"))',
            *(f"(allow file-read* (literal {json.dumps(str(path))}))"
              for path in reversed((ROOT / ".venv").parents)
              if path == Path.home() or Path.home() in path.parents),
            f"(allow file-read* (subpath {json.dumps(str(ROOT / '.venv'))}))",
        ]
        target = self.profiles / f"pytest-{uuid.uuid4().hex}.sb"
        target.write_text("\n".join(profile) + "\n")
        return target

    def run_pytest(self, repo, *paths):
        repo = Path(repo).resolve()
        private = repo.parent.parent.resolve() == self.private.resolve()
        profile = self._test_profile(repo, private)
        env = {**self.env, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
        result = subprocess.run(
            self._wrapped(profile, [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *paths]),
            cwd=repo, env=env, capture_output=True, text=True, timeout=120, check=False,
        )
        if result.returncode not in (0, 1) or (
            result.returncode == 1 and " failed" not in result.stdout
        ):
            raise RuntimeError("Sandboxed host pytest did not produce a valid test verdict")
        return result

    def _wrapped(self, profile, argv):
        architecture = "-arm64" if platform.machine().startswith("arm") else "-x86_64"
        return ["/usr/bin/arch", architecture, "/usr/bin/sandbox-exec", "-f", str(profile), *argv]

    def _canary(self, repo, profile):
        link = Path(repo) / f".acqe-private-link-{uuid.uuid4().hex}"
        link.symlink_to(self.private / "canary")
        paths = {
            "public": str(Path(repo) / "mod.py"),
            "private_absolute": str(self.private / "canary"),
            "private_data_volume": "/System/Volumes/Data" + str(self.private / "canary"),
            "private_parent": "../../host-private/canary",
            "private_symlink": str(link),
            "fixture_absolute": str(ROOT / "tests" / "toybench.py"),
            "fixture_data_volume": "/System/Volumes/Data" + str(ROOT / "tests" / "toybench.py"),
            "shared_context": str(ROOT / ".ccc-mcp" / "CONTEXT.md"),
            "shared_context_data_volume": "/System/Volumes/Data" + str(ROOT / ".ccc-mcp" / "CONTEXT.md"),
        }
        sibling = next(
            (path / "mod.py" for path in self.base.iterdir() if path.is_dir()
             and path.resolve() != Path(repo).resolve() and (path / "mod.py").is_file()),
            None,
        )
        if sibling:
            paths["sibling_arm"] = str(sibling)
            paths["sibling_data_volume"] = "/System/Volumes/Data" + str(sibling)
        try:
            probe = subprocess.run(
                self._wrapped(profile, ["/usr/bin/python3", "-c", self._ACCESS_PROBE, json.dumps(paths)]),
                cwd=repo, env=self.env, capture_output=True, text=True, timeout=30, check=False,
            )
        finally:
            link.unlink(missing_ok=True)
        try:
            observed = json.loads(probe.stdout) if probe.returncode == 0 else {}
        except ValueError:
            observed = {}
        if observed != {key: key == "public" for key in paths}:
            raise RuntimeError("OS isolation canary failed; refusing the Codex experiment")

    def run_codex(self, repo, prompt, sandbox, timeout=900):
        profile = self._profile(repo)
        self._canary(repo, profile)
        login = subprocess.run(
            self._wrapped(profile, [native_executable("codex"), "login", "status"]),
            cwd=repo, env=self.env, capture_output=True, text=True, timeout=30, check=False,
        )
        if login.returncode != 0 or "Logged in" not in (login.stdout + login.stderr):
            raise RuntimeError("Codex login failed inside OS isolation; refusing experiment")
        argv = codex_exec_argv(repo, self.model, self.effort, sandbox)
        argv.insert(3, "--no-daemon")
        argv.insert(5, "--ephemeral")
        started = time.time()
        record = {
            "repo": Path(repo).name, "sandbox": sandbox, "prompt": prompt,
            "requested_model": self.model, "requested_effort": self.effort,
            "os_canary": "passed", "sandboxed_login": "passed",
            "profile_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
        }
        try:
            result = subprocess.run(
                self._wrapped(profile, argv),
                input=prompt, cwd=repo, env=self.env,
                capture_output=True, text=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            record.update({"seconds": round(time.time() - started, 2), "status": "timeout"})
            self.calls.append(record)
            raise
        record.update({
            "seconds": round(time.time() - started, 2), "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr,
        })
        self.calls.append(record)
        return result


def run_direct(base, name, run_id=None, model=None, effort=None):
    run_id = run_id or uuid.uuid4().hex[:12]
    repo = make_repo(base, name, "direct")
    prompt = ISSUES[name] + "\nMake the change in this repository. The existing tests should keep passing."
    started = time.time()
    stage_started = time.time()
    done = subprocess.run(
        codex_exec_argv(repo, model, effort),
        input=prompt, capture_output=True, text=True, timeout=900, check=False,
    )
    stage_seconds = round(time.time() - stage_started, 2)
    usage = usage_from_output("codex", done.stdout)
    case = case_from(
        name, "direct", repo, usage.get("tokens") if usage["available"] else None,
        run_envelope(
            run_id=run_id, case_id=name, arm_id="direct", repo=repo,
            budget={"max_agent_calls": 1, "max_retries": 0},
            tool_permissions=_SANDBOX_TOOL_PERMISSIONS,
            stages=[{"name": "implement", "seconds": stage_seconds}],
            provider="codex", requested_model=model, requested_effort=effort,
        ),
    )
    case["seconds"] = round(time.time() - started)
    return case


def _merge_tokens(usages):
    """Sum the numeric token fields across several `usage_from_output(...)["tokens"]`
    dicts (one per codex exec call), skipping any round where usage wasn't available."""
    merged = {}
    for tokens in usages:
        if not tokens:
            continue
        for key, value in tokens.items():
            merged[key] = merged.get(key, 0) + value
    return merged or None


def run_b_retry(base, name, max_follow_ups=1, run_id=None, model=None, effort=None):
    """Arm B_retry (ACQE REQ-009) — Test-Guided Retry, NOT a review. A single agent, plus
    one bounded retry opportunity gated purely on the host-run agent-visible test suite
    — no multi-role workflow, no ccc-mcp service, and critically, no inspection step of
    any kind. One `codex exec` attempt; if the visible tests fail, one follow-up
    `codex exec` call is given that failure's stdout as feedback and tries again; if they
    already pass, nothing further happens — a defect the visible tests don't cover is
    never looked at, on any round. Never sees the hidden test.

    This is deliberately named `b_retry`, not `review`: see this file's module docstring
    and docs/development/ACQE-GAP-ANALYSIS.md §11.6 for why conflating "retried after a
    failing test" with "reviewed" would misrepresent what this arm measures. The
    conceptually different `B_review` arm (explicit critique, independent of whether
    tests already pass, host-verified findings) is specified there but not implemented
    here."""
    run_id = run_id or uuid.uuid4().hex[:12]
    repo = make_repo(base, name, "b_retry")
    prompt = ISSUES[name] + "\nMake the change in this repository. The existing tests should keep passing."
    started = time.time()
    usages, passed_per_round, stages = [], [], []
    rounds_used = 0
    for round_index in range(max_follow_ups + 1):
        stage_started = time.time()
        done = subprocess.run(
            codex_exec_argv(repo, model, effort),
            input=prompt, capture_output=True, text=True, timeout=900, check=False,
        )
        usage = usage_from_output("codex", done.stdout)
        usages.append(usage.get("tokens") if usage["available"] else None)
        rounds_used += 1
        result = pytest(repo, "tests")
        passed = result.returncode == 0
        passed_per_round.append(passed)
        stages.append({
            "name": f"implement_round_{round_index + 1}",
            "seconds": round(time.time() - stage_started, 2),
            "visible_tests_passed": passed,
        })
        if passed or round_index == max_follow_ups:
            break
        prompt = (
            ISSUES[name]
            + "\nYour previous attempt did not pass the test suite. Host test output:\n"
            + result.stdout[-4000:]
            + "\nFix it so the tests pass."
        )
    case = case_from(
        name, "b_retry", repo, _merge_tokens(usages),
        {
            "rounds_used": rounds_used, "passed_per_round": passed_per_round,
            **run_envelope(
                run_id=run_id, case_id=name, arm_id="b_retry", repo=repo,
                budget={"max_agent_calls": max_follow_ups + 1, "max_retries": max_follow_ups},
                tool_permissions=_SANDBOX_TOOL_PERMISSIONS,
                stages=stages,
                provider="codex", requested_model=model, requested_effort=effort,
            ),
        },
    )
    case["seconds"] = round(time.time() - started)
    return case


def pending_patch(bridge, execution_id):
    result = next(
        item for item in bridge.latest("demo")["snapshot"]["agent_results"]
        if item["execution_id"] == execution_id
    )
    return {
        "base_context_version": bridge.latest("demo")["version"],
        "changes": {"summary": f"PM reviewed {result['role']} evidence."},
        "source_execution_ids": [execution_id],
        "source_task_ids": [result["task_id"]],
        "source_document_refs": [result["source_document_ref"]],
        "update_reason": "PM verified the completed Agent result",
        "semantic_review_ack": True,
    }


def run_ccc(base, name, max_steps=8, run_id=None, model=None, effort=None):
    run_id = run_id or uuid.uuid4().hex[:12]
    os.environ["CCC_MCP_ENABLE_REAL_AGENTS"] = "1"
    repo = make_repo(base, name, "ccc")
    service = CCCMCP(Path(base) / f"{name}-home")
    service.spawn_worker = lambda execution_id: None
    service.register_project(repo, "demo", "toy benchmark")
    for role in ("SA", "SD/RD", "REVIEW"):
        # Same model/effort pinning mechanism as direct/b_retry's codex_exec_argv(),
        # routed through ccc-mcp's own real config path (configure_role ->
        # resolve_agent_settings -> adapters/agents/codex.py's --model/-c
        # model_reasoning_effort=) instead of a raw CLI flag, since this arm never
        # calls codex directly — see ACQE-GAP-ANALYSIS.md §13.1.
        service.configure_role("demo", role, "codex", model=model, effort=effort)
    service.configure_test_command("demo", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"])
    service.authorize_project_local("demo")

    started = time.time()
    first = service.start_project_request("demo", ISSUES[name])
    task_id, execution_id = first["workstream"]["id"], first["sa_execution"]["execution_id"]
    run_execution(service, execution_id)
    instruction = "Approved plan: implement exactly the fix described in the issue."
    for _ in range(max_steps):
        step = service.advance_project("demo", pending_patch(service, execution_id), instruction)
        instruction = None
        following = step.get("next_execution")
        if not following:
            break
        execution_id = following["execution_id"]
        run_execution(service, execution_id)

    exported = service.export_scoring_cases("demo")
    usage = next((c for c in exported if c["task_id"] == task_id), {})
    tokens = usage.get("tokens") or None
    executions = service.get_task_result("demo", task_id)
    rounds = [r["role"] for r in executions]
    # `options.agent_settings` is this role's resolved {provider, model, effort,
    # quota_fallback} at the moment it was dispatched (core/tasks.py's dispatch_agent
    # writes it verbatim) — the real *requested* config, not a guess. This harness never
    # sets an explicit model/effort, so these are honestly None today, same as the other
    # two arms; if a future run does set one, this reports it accurately per execution.
    stages, requested_models, requested_efforts = [], set(), set()
    for execution in executions:
        settings = (execution.get("options") or {}).get("agent_settings") or {}
        requested_models.add(settings.get("model"))
        requested_efforts.add(settings.get("effort"))
        stages.append({
            "name": execution["role"], "execution_id": execution["id"],
            "status": execution["status"],
            "seconds": _elapsed_seconds(execution.get("created"), execution.get("finished")),
        })
    requested_model = next(iter(requested_models)) if len(requested_models) == 1 else None
    requested_effort = next(iter(requested_efforts)) if len(requested_efforts) == 1 else None
    review_gate_max_rounds = service.get_project_settings("demo")["review_gate"]["max_rounds"]
    case = case_from(
        name, "ccc-mcp", repo, tokens,
        {
            "rounds": rounds, "usage_complete": usage.get("usage_complete", False),
            "task_state": service.task("demo", task_id)["state"],
            **run_envelope(
                run_id=run_id, case_id=name, arm_id="ccc", repo=repo,
                budget={"max_role_dispatches": max_steps, "review_gate_max_rounds": review_gate_max_rounds},
                tool_permissions={
                    "codex_sandbox_mode": "workspace-write (via ccc-mcp real adapter)",
                    "skills": "none installed in this toy benchmark",
                    "mcp_tools": "none installed in this toy benchmark",
                    "project_authorization": "read-write (authorize_project_local)",
                },
                stages=stages,
                provider="codex", requested_model=requested_model, requested_effort=requested_effort,
            ),
        },
    )
    if usage.get("tokens_total") is not None:
        case["tokens_total"] = usage["tokens_total"]
    case["seconds"] = round(time.time() - started)
    return case


# --- Controlled Review Experiment: B0 / B1 / B2 (ACQE Decision 2, round 5) ------------
#
# Decision 1 (§14) splits ACQE's experiments into two kinds: the A/B_retry/C0 arms above
# are an End-to-End Product Comparison — each keeps its own tool's native mechanism
# (ccc's structured `--output-schema` dispatch included), deliberately NOT forced to
# match, because unifying them would itself change what's being measured. What follows
# is the other kind: a Controlled Review Experiment, built specifically to hold the
# model, the starting code, the tool permissions, and the review budget constant, so
# that what varies between B1 and B2 is the review *strategy* and nothing else. See
# docs/development/ACQE-GAP-ANALYSIS.md §14 for the full design and the Confounders this
# does/doesn't close.

REVIEW_LENSES = {
    "Architecture": "Check interface boundaries, lifecycle assumptions, and coupling. Distinguish observed failures from risks that need more evidence.",
    "Concurrency": "Check shared state, ordering, and synchronization. Distinguish observed failures from risks that need more evidence.",
    "Data Integrity": "Check input, output, and state invariants. Distinguish observed failures from risks that need more evidence.",
}


def select_review_lens(issue_text):
    """Choose a broad lens from the public issue only, never fixture ground truth.

    The ordered rules are intentionally coarse. They cannot encode a case's planted
    defect, hidden tests, or private metadata, and unknown tasks get Data Integrity.
    """
    words = issue_text.lower()
    if any(term in words for term in ("concurr", "thread", "race", "async", "lock")):
        return "Concurrency"
    if any(term in words for term in ("architecture", "cache", "interface", "lifecycle", "module")):
        return "Architecture"
    return "Data Integrity"

_REVIEW_INSTRUCTIONS = (
    "For each specific failure condition you find, write a line in exactly this form: "
    "`[blocker|major|minor|assumption] <claim> evidence: <specific evidence>` (the same "
    "convention ccc-mcp's own reviewers use). If you cannot back a claim with concrete "
    "evidence, do not raise it. If you find nothing you can back with evidence, say so "
    "explicitly instead of inventing a problem."
)


def general_review_prompt(name, diff_text, visible_stdout, visible_passed):
    """B1: an ordinary, non-contextualized review — deliberately the same instructions
    regardless of which task this is, so the only thing that varies between B1 and B2 is
    whether the prompt names a specific risk area (Decision 2's "difference concentrated
    in review strategy, not extra tools/permissions")."""
    return (
        "You are reviewing a colleague's implementation attempt — you did not write it. "
        "Do not edit any files; this is a read-only review. Look at the diff and the "
        "host's own visible-test run below, then identify any specific failure "
        "conditions, even ones the shown tests don't already catch. "
        f"{_REVIEW_INSTRUCTIONS}\n\n"
        f"Diff:\n{diff_text}\n\n"
        f"Host visible-test run ({'passed' if visible_passed else 'FAILED'}):\n{visible_stdout}\n"
    )


def contextual_review_prompt(name, diff_text, visible_stdout, visible_passed):
    """B2 adds a generic lens selected solely from the public issue text."""
    lens = select_review_lens(ISSUES[name])
    return (
        "You are reviewing a colleague's implementation attempt — you did not write it. "
        "Do not edit any files; this is a read-only review. "
        f"Use the {lens} review lens: {REVIEW_LENSES[lens]} "
        "Look at the diff and the host's own "
        f"visible-test run below. {_REVIEW_INSTRUCTIONS}\n\n"
        f"Diff:\n{diff_text}\n\n"
        f"Host visible-test run ({'passed' if visible_passed else 'FAILED'}):\n{visible_stdout}\n"
    )


def extract_agent_text(stdout):
    """Every `agent_message` item's text from a codex --json exec stream, in order,
    joined — the raw material parse_findings() looks for `[severity] claim evidence:
    ...` lines inside. Confirmed against a real captured stream (see
    benchmarks/toy-agents/smoke-test-evidence/) rather than assumed."""
    texts = []
    for line in stdout.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get("type") == "item.completed" and (item.get("item") or {}).get("type") == "agent_message":
            text = item["item"].get("text")
            if text:
                texts.append(text)
    return "\n".join(texts)


def require_codex_success(result, stage):
    """Do not spend subsequent experiment calls after a failed Codex invocation."""
    if result.returncode != 0:
        raise RuntimeError(f"{stage} Codex call failed with exit code {result.returncode}")


def host_verify_findings(findings, name, repo):
    """Confirm only a finding tied to its own host-authored, reproducible probe.

    The private probe's exact witness must appear somewhere in the finding's own
    text (claim or evidence — parse_findings() only splits a Finding in two at the
    literal word "evidence:"; which half a reviewer's concrete example lands in is
    a wording choice, not a signal of whether it found the thing the witness
    checks for). One witness can confirm at most one finding; ambiguity stays
    Supported. An arbitrary test failure, agent assertion, or final task outcome
    is never confirmation.
    """
    spec = REVIEW_TASKS[name]
    witness = spec.get("confirmation_witness")
    claim_terms = spec.get("confirmation_claim_terms", ())
    candidates = [
        i for i, (_, claim, evidence) in enumerate(findings)
        if witness and evidence and witness in f"{claim} {evidence}"
        and any(re.search(r"\b" + re.escape(term) + r"\b", claim.lower()) for term in claim_terms)
    ]
    confirmed = set()
    if len(candidates) == 1 and spec.get("own_good"):
        result = host_test_in_shadow(repo, "tests/test_own_good_probe.py", spec["own_good"])
        # A defective probe or an unrelated fixture failure is not evidence.
        # The same host-owned test must pass against the fixture's known-good
        # reference implementation before its failure can confirm this claim.
        if result.returncode == 1 and "1 failed" in result.stdout:
            with tempfile.TemporaryDirectory(
                prefix="acqe-probe-control-", dir=_HOST_PRIVATE_ROOT.get()
            ) as control_dir:
                control = Path(control_dir)
                (control / "tests").mkdir()
                (control / "conftest.py").write_text("import sys\nsys.path.insert(0, '.')\n")
                (control / "mod.py").write_text(spec["fixed"])
                (control / "tests" / "test_own_good_probe.py").write_text(spec["own_good"])
                if pytest(control, "tests/test_own_good_probe.py").returncode == 0:
                    confirmed.add(candidates[0])
    out = []
    for index, (severity, claim, evidence) in enumerate(findings):
        if not evidence:
            status = "hypothesis"
        elif index in confirmed:
            status = "confirmed"
        else:
            status = "supported"
        item = {"severity": severity, "claim": claim, "evidence": evidence, "verification_status": status}
        if index in confirmed:
            item["host_confirmation"] = {"probe": "own_good", "witness": witness}
        out.append(item)
    return out


def run_frozen_implementation(base, name, run_id, model, effort, isolation):
    """Shared Step 1 for B0/B1/B2: ONE agent implementation attempt, committed so every
    arm starts from the identical frozen output instead of three independent (and
    therefore non-comparable) generations — Decision 2's "avoid first-round generation
    differences" requirement."""
    repo = make_repo(base, name, "frozen")
    prompt = ISSUES[name] + "\nMake the change in this repository. The existing tests should keep passing."
    stage_started = time.time()
    done = isolation.run_codex(repo, prompt, "workspace-write")
    require_codex_success(done, "frozen implementation")
    usage = usage_from_output("codex", done.stdout)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "frozen implementation attempt", "--allow-empty")
    tokens = usage.get("tokens") if usage["available"] else None
    stage = {"name": "implement_frozen", "seconds": round(time.time() - stage_started, 2),
             "tokens": tokens}
    return repo, stage, tokens


def _clone_repo(src, dest):
    shutil.copytree(src, dest, symlinks=True)
    return dest


def run_b0(frozen_repo, implement_stage, implement_tokens, base, name, run_id, model, effort,
           isolation=None):
    """B0: the frozen implementation, scored as-is — zero additional agent calls. The
    no-review baseline inside the Controlled Review Experiment (distinct from the
    End-to-End arms' `direct`, and distinct from `B_retry2`, which is a budget-matched
    placebo control, not a zero-cost baseline — Decision 2 keeps both, for different
    questions)."""
    repo = _clone_repo(frozen_repo, Path(base) / f"{name}-b0")
    case = case_from(
        name, "b0", repo, implement_tokens,
        run_envelope(
            run_id=run_id, case_id=name, arm_id="b0", repo=repo,
            budget={"max_agent_calls": 1, "max_retries": 0},
            tool_permissions={**_SANDBOX_TOOL_PERMISSIONS,
                              **getattr(isolation, "permissions", {
                                  "outer_os_sandbox": "macOS sandbox-exec; private fixture and sibling arms denied",
                                  "session_mode": "--no-daemon --ephemeral --ignore-user-config",
                              })},
            stages=[implement_stage],
            provider="codex", requested_model=model, requested_effort=effort,
            cli_version_override=getattr(isolation, "cli_version", None),
        ),
    )
    case["seconds"] = implement_stage["seconds"]
    return case


def run_review_arm(base, name, arm_id, prompt_fn, frozen_repo, implement_stage, implement_tokens,
                    run_id, model, effort, isolation, max_fix_attempts=1):
    """Shared Step 2+ for B1/B2: clone the frozen implementation, run exactly ONE
    read-only review call regardless of whether the visible tests already pass (Decision
    2's "B1/B2 must review unconditionally"), parse findings with core/flows.py's own
    existing grammar (reused, not reinvented — the public Finding protocol is untouched),
    host-verify them (host_verify_findings(), never a blanket upgrade), then attempt a
    fix only if a confirmed/supported blocking finding justifies it, bounded by
    `max_fix_attempts`. The reviewer never sees the hidden test: it does not exist
    anywhere on disk at any point this function runs (same guarantee already tested for
    `run_b_retry`, Gap Analysis §12.2 item 3) — the review prompt only ever contains the
    diff and the *visible* test's own output, both already host-verified data."""
    repo = _clone_repo(frozen_repo, Path(base) / f"{name}-{arm_id}")
    local = pytest(repo, "tests")
    visible_passed = local.returncode == 0
    diff = git(repo, "diff", "HEAD~1", "HEAD", "--", ".", capture=True)

    review_started = time.time()
    review_done = isolation.run_codex(
        repo, prompt_fn(name, diff, local.stdout, visible_passed), "read-only"
    )
    require_codex_success(review_done, f"{arm_id} review")
    review_usage = usage_from_output("codex", review_done.stdout)
    findings = [
        finding for finding in parse_findings(extract_agent_text(review_done.stdout).splitlines())
        if finding[0] is not None
    ]
    verified = host_verify_findings(findings, name, repo)

    stages = [implement_stage, {
        "name": "review", "seconds": round(time.time() - review_started, 2),
        "visible_tests_passed": visible_passed, "findings_count": len(verified),
        "tokens": review_usage.get("tokens") if review_usage["available"] else None,
    }]
    usages = [implement_tokens, review_usage.get("tokens") if review_usage["available"] else None]

    blocking = [
        f for f in verified
        if f["severity"] in ("blocker", "major") and f["verification_status"] in ("confirmed", "supported")
    ]
    fix_attempted = False
    if blocking and max_fix_attempts > 0:
        fix_attempted = True
        fix_started = time.time()
        feedback = "\n".join(f"- [{f['severity']}] {f['claim']} (evidence: {f['evidence']})" for f in blocking)
        fix_prompt = (
            ISSUES[name] + "\nA reviewer found the following issues with your previous "
            "attempt. Fix them.\n" + feedback
        )
        fix_done = isolation.run_codex(repo, fix_prompt, "workspace-write")
        require_codex_success(fix_done, f"{arm_id} fix")
        fix_usage = usage_from_output("codex", fix_done.stdout)
        local = pytest(repo, "tests")
        stages.append({
            "name": "fix", "seconds": round(time.time() - fix_started, 2),
            "visible_tests_passed": local.returncode == 0,
            "tokens": fix_usage.get("tokens") if fix_usage["available"] else None,
        })
        usages.append(fix_usage.get("tokens") if fix_usage["available"] else None)

    case = case_from(
        name, arm_id, repo, _merge_tokens(usages),
        {
            "findings": verified, "fix_attempted": fix_attempted,
            **run_envelope(
                run_id=run_id, case_id=name, arm_id=arm_id, repo=repo,
                budget={
                    "max_agent_calls": 2 + (1 if max_fix_attempts else 0),
                    "max_review_calls": 1, "max_fix_attempts": max_fix_attempts,
                },
                tool_permissions={
                    **_SANDBOX_TOOL_PERMISSIONS,
                    "review_sandbox_mode": "read-only",
                    **getattr(isolation, "permissions", {
                        "outer_os_sandbox": "macOS sandbox-exec; private fixture and sibling arms denied",
                        "session_mode": "--no-daemon --ephemeral --ignore-user-config",
                    }),
                },
                stages=stages,
                provider="codex", requested_model=model, requested_effort=effort,
                cli_version_override=getattr(isolation, "cli_version", None),
            ),
        },
    )
    case["seconds"] = round(sum(s["seconds"] for s in stages), 2)
    return case


def run_controlled_review_experiment(
    base, name, run_id=None, model=None, effort=None, max_fix_attempts=1, isolation=None,
):
    """Orchestrates B0/B1/B2 from one shared frozen implementation. Returns
    `{"b0": case, "b1": case, "b2": case}`. See this section's module-level comment and
    Gap Analysis §14 for the full design."""
    if max_fix_attempts not in (0, 1):
        raise ValueError("max_fix_attempts must be 0 or 1 for this bounded prototype")
    if isolation is None or Path(base).resolve() != Path(isolation.base).resolve():
        raise RuntimeError("Controlled review requires a verified OS isolation runner")
    if model != isolation.model or effort != isolation.effort:
        raise RuntimeError("Requested model and effort differ from the isolation runner")
    run_id = run_id or uuid.uuid4().hex[:12]
    frozen_repo, implement_stage, implement_tokens = run_frozen_implementation(
        base, name, run_id, model, effort, isolation
    )
    b0 = run_b0(frozen_repo, implement_stage, implement_tokens, base, name, run_id, model,
                effort, isolation)
    b1 = run_review_arm(
        base, name, "b1", general_review_prompt, frozen_repo, implement_stage, implement_tokens,
        run_id, model, effort, isolation, max_fix_attempts,
    )
    b2 = run_review_arm(
        base, name, "b2", contextual_review_prompt, frozen_repo, implement_stage, implement_tokens,
        run_id, model, effort, isolation, max_fix_attempts,
    )
    return {"b0": b0, "b1": b1, "b2": b2}


def save_review_artifacts(isolation, name, out, run_id, first_call):
    """Persist the public frozen input and complete call streams for arm replay."""
    out = Path(out)
    calls = isolation.calls[first_call:]
    (out / f"{name}-agent-calls.jsonl").write_text(
        "".join(json.dumps(call, ensure_ascii=False) + "\n" for call in calls),
        encoding="utf-8",
    )
    frozen = isolation.base / f"{name}-frozen"
    frozen_commit = None
    bundle_hash = None
    try:
        commits = git(frozen, "rev-list", "--count", "HEAD", capture=True).strip() if frozen.is_dir() else None
    except (OSError, subprocess.CalledProcessError):
        commits = None
    if commits == "2":
        bundle = out / f"{name}-frozen.bundle"
        git(frozen, "bundle", "create", str(bundle), "--all")
        frozen_commit = repo_commit(frozen)
        bundle_hash = hashlib.sha256(bundle.read_bytes()).hexdigest()
    manifest = {
        "run_id": run_id, "case_id": name, "public_issue": ISSUES[name],
        "frozen_commit": frozen_commit,
        "frozen_bundle_sha256": bundle_hash,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_sha256": hashlib.sha256(
            Path(getattr(isolation, "fixture_path", ROOT / "tests" / "toybench.py")).read_bytes()
        ).hexdigest(),
        "requested_model": isolation.model, "requested_effort": isolation.effort,
        "cli_version": getattr(isolation, "cli_version", None) or codex_cli_version(),
        "agent_calls": len(calls),
        "agent_image": getattr(isolation, "image", None),
        "agent_image_id": getattr(isolation, "image_id", None),
        "scorer_image_id": getattr(isolation, "scorer_image_id", None),
        "docker_runner_sha256": hashlib.sha256(
            (ROOT / "benchmarks/toy-agents/acqe_docker.py").read_bytes()
        ).hexdigest() if getattr(isolation, "image", None) else None,
        "egress_proxy_sha256": hashlib.sha256(
            (ROOT / "benchmarks/toy-agents/docker/egress_proxy.py").read_bytes()
        ).hexdigest() if getattr(isolation, "image", None) else None,
    }
    (out / f"{name}-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def quota():
    data = asyncio.run(read_account_usage(20))
    return [
        {"window_mins": w["duration_mins"], "used_percent": w["used_percent"]}
        for w in data.get("rate_limits") or []
    ]


def prepare(folder):
    """Create one buggy repository per task, plus ISSUES.md with the text to give the
    agent. Use these as ccc-mcp projects (or hand them to any agent), then `judge`."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["# Issues (give each one to the agent as the task)", ""]
    for name in TASKS:
        make_repo(folder, name, "task")
        lines += [f"## {name}  (project folder: {name}-task)", "", ISSUES[name], ""]
    (folder / "ISSUES.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Prepared {len(TASKS)} repositories in {folder}; issue texts are in ISSUES.md")


def prepare_single(folder):
    """One git repository holding every task in its own folder (<name>/<name>_mod.py), so it
    can be registered as a single ccc-mcp project. Module names are unique per task so a
    plain `pytest` from the root runs all visible tests together."""
    folder = Path(folder)
    if folder.exists() and any(folder.iterdir()):
        sys.exit(f"{folder} is not empty; pick a new folder")
    folder.mkdir(parents=True)
    (folder / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n\n"
        "for path in Path(__file__).parent.iterdir():\n"
        "    if (path / 'tests').is_dir():\n"
        "        sys.path.insert(0, str(path))\n"
    )
    lines = [
        "# Issues (one repository, one folder per task)",
        "",
        "Register this folder as ONE ccc-mcp project. Host test command: `python -m pytest -q`.",
        "Give the agent one issue at a time and keep changes inside that task's folder.",
        "",
    ]
    for name, spec in TASKS.items():
        module = single_module(name)
        (folder / name / "tests").mkdir(parents=True)
        (folder / name / f"{module}.py").write_text(spec["buggy"])
        (folder / name / "tests" / f"test_{name}_visible.py").write_text(
            spec["visible"].replace("from mod import", f"from {module} import")
        )
        issue = ISSUES[name].replace("mod.py", f"{name}/{module}.py")
        lines += [f"## {name}", "", issue, ""]
    (folder / "ISSUES.md").write_text("\n".join(lines), encoding="utf-8")
    git(folder, "init", "-q")
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", "base")
    print(f"Prepared one repository with {len(TASKS)} tasks in {folder}; issue texts are in ISSUES.md")


def judge_folder(folder):
    """After an agent has worked on the prepared repositories: run the hidden test and the
    rule-based scorer on whatever it left behind, and print one line per task."""
    from ccc_mcp.evaluation_engine import validation

    cases = []
    single = (Path(folder) / ".git").exists()
    for name in TASKS:
        if single:
            if (Path(folder) / name).is_dir():
                cases.append(case_from(name, "judged", Path(folder), None, sub=name))
            continue
        repo = Path(folder) / f"{name}-task"
        if not repo.is_dir():
            continue
        cases.append(case_from(name, "judged", repo, None))
    for case in cases:
        scored = validation.score_case(case)
        print(
            f"{case['instance_id']:<12} {'RESOLVED' if case['resolved'] else 'not resolved':<13} "
            f"score={scored['quality_score']} status={scored['task_status']}"
        )
    if len(cases) >= 2:
        print(json.dumps(validation.validate(cases)["verdict"]))
    out = Path(folder) / "cases.jsonl"
    out.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n", encoding="utf-8")
    print("cases written to", out)


ARMS = {
    "direct": (run_direct, "direct.jsonl"),
    "b_retry": (run_b_retry, "b_retry.jsonl"),
    "ccc": (run_ccc, "ccc.jsonl"),
}


def parse_arms(raw):
    """`"direct,b_retry,ccc"` -> `["direct", "b_retry", "ccc"]`, validated against `ARMS`.
    Blank entries (from a stray comma, e.g. `"direct,,ccc"`) are silently dropped — they
    carry no meaning. Everything else that could silently waste Codex quota or silently
    do nothing is a hard, explicit error instead: an empty/all-blank `--arms`, an unknown
    arm name, or the same arm named more than once (which would otherwise run that arm
    twice per task without warning)."""
    selected = [label.strip() for label in raw.split(",") if label.strip()]
    if not selected:
        sys.exit("--arms must name at least one arm (got nothing usable after parsing "
                  f"{raw!r}); choose from {sorted(ARMS)}")
    unknown = [label for label in selected if label not in ARMS]
    if unknown:
        sys.exit(f"Unknown arm(s) {unknown}; choose from {sorted(ARMS)}")
    seen = set()
    duplicates = sorted({label for label in selected if label in seen or seen.add(label)})
    if duplicates:
        sys.exit(f"Duplicate arm(s) {duplicates} in --arms; name each arm once")
    return selected


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run", help="Run Codex directly and through ccc-mcp (spends Codex quota)")
    runner.add_argument("tasks", nargs="*", default=list(TASKS))
    runner.add_argument("--out", type=Path, required=True)
    runner.add_argument(
        "--arms", default="direct,ccc",
        help="Comma-separated arms to run: direct, b_retry (ACQE Arm B_retry — one "
             "bounded test-guided retry follow-up on a failing visible-test run; NOT a "
             "review, see this file's module docstring), ccc. "
             "Default is unchanged from before b_retry existed: direct,ccc.",
    )
    runner.add_argument(
        "--model", default=None,
        help="Pin every arm to the same explicit Codex model (same --model flag on "
             "direct/b_retry's raw codex exec and on ccc's configured roles). Omit to "
             "use each call's own CLI/account default, exactly as before this flag "
             "existed — the default behavior is unchanged either way.",
    )
    runner.add_argument(
        "--effort", default=None,
        help="Pin every arm to the same reasoning effort (same -c "
             "model_reasoning_effort=... on every arm). Omit for each call's default.",
    )
    reviewer = sub.add_parser(
        "review-experiment",
        help="ACQE Decision 2's B0/B1/B2 Controlled Review Experiment (spends Codex quota)",
    )
    reviewer.add_argument("tasks", nargs="*", default=list(REVIEW_TASKS))
    reviewer.add_argument("--out", type=Path, required=True)
    reviewer.add_argument("--model", default=None, help="Pin B0/B1/B2 to the same explicit Codex model.")
    reviewer.add_argument("--effort", default=None, help="Pin B0/B1/B2 to the same reasoning effort.")
    reviewer.add_argument("--private-fixture", type=Path, default=None,
                          help="Host-only private Fixture outside the Git worktree; uses Docker isolation.")
    reviewer.add_argument("--max-case-seconds", type=int, default=1800,
                          help="Hard total wall-time limit per private Case (default 1800 seconds).")
    reviewer.add_argument(
        "--max-fix-attempts", type=int, default=1,
        help="Cap on B1/B2's post-review fix call (0 disables fixing; review still runs).",
    )
    preparer = sub.add_parser("prepare", help="Create the buggy repositories for a manual run")
    preparer.add_argument("folder")
    preparer.add_argument("--single", action="store_true", help="One repository with a folder per task")
    sub.add_parser("judge", help="Score what an agent left in the prepared repositories").add_argument("folder")
    args = parser.parse_args()
    if args.command == "prepare":
        return prepare_single(args.folder) if args.single else prepare(args.folder)
    if args.command == "judge":
        return judge_folder(args.folder)
    if args.command == "review-experiment":
        if not args.model or not args.effort:
            parser.error("review-experiment requires explicit --model and --effort")
        if args.private_fixture:
            load_private_fixture(args.private_fixture)
        if args.max_case_seconds < 60 or args.max_case_seconds > 4500:
            parser.error("--max-case-seconds must be between 60 and 4500")
        if args.max_fix_attempts not in (0, 1):
            parser.error("review-experiment --max-fix-attempts must be 0 or 1")
        unknown = [name for name in args.tasks if name not in REVIEW_TASKS]
        if unknown:
            parser.error(f"Unknown review task(s): {unknown}")
        if args.out.exists() and any(args.out.iterdir()):
            parser.error("review-experiment --out must be a new or empty directory")
        if not args.out.resolve().is_relative_to(Path.home().resolve()):
            parser.error("review-experiment --out must be inside the host HOME, which the agent cannot read")
        for name in args.tasks:
            assert_fixture_not_in_committed_head(name)
        isolation = (
            ACQEDockerIsolation(args.model, args.effort, args.private_fixture, ROOT)
            if args.private_fixture else ACQEIsolation(args.model, args.effort)
        )
        args.out.mkdir(parents=True, exist_ok=True)
        print("quota before:", json.dumps(quota()), flush=True)
        run_id = uuid.uuid4().hex[:12]
        print("run_id:", run_id, flush=True)
        with isolation:
            private_token = _HOST_PRIVATE_ROOT.set(str(isolation.private))
            active_token = _ACTIVE_ISOLATION.set(isolation)
            try:
                for name in args.tasks:
                    if args.private_fixture:
                        isolation.begin_case(args.max_case_seconds)
                    first_call = len(isolation.calls)
                    try:
                        timer = case_wall_timer(args.max_case_seconds) if args.private_fixture else nullcontext()
                        with timer:
                            results = run_controlled_review_experiment(
                                isolation.base, name, run_id=run_id, model=args.model,
                                effort=args.effort, max_fix_attempts=args.max_fix_attempts,
                                isolation=isolation,
                            )
                    finally:
                        save_review_artifacts(isolation, name, args.out, run_id, first_call)
                    for arm_id, case in results.items():
                        with open(args.out / f"{arm_id}.jsonl", "a", encoding="utf-8") as handle:
                            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
                        print(name, arm_id, "resolved" if case["resolved"] else "NOT resolved",
                              "tokens", case["tokens_total"], f"{case['seconds']}s",
                              "findings", len(case.get("findings", [])), flush=True)
            finally:
                _ACTIVE_ISOLATION.reset(active_token)
                _HOST_PRIVATE_ROOT.reset(private_token)
        print("quota after:", json.dumps(quota()), flush=True)
        return
    arms = ARMS
    selected = parse_arms(args.arms)
    args.out.mkdir(parents=True, exist_ok=True)
    print("quota before:", json.dumps(quota()), flush=True)
    run_id = uuid.uuid4().hex[:12]
    print("run_id:", run_id, flush=True)
    with tempfile.TemporaryDirectory(prefix="ccc-toy-agents-") as base:
        for name in args.tasks:
            for label in selected:
                run_one, target = arms[label]
                case = run_one(base, name, run_id=run_id, model=args.model, effort=args.effort)
                with open(args.out / target, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(case, ensure_ascii=False) + "\n")
                print(name, label, "resolved" if case["resolved"] else "NOT resolved",
                      "tokens", case["tokens_total"], f"{case['seconds']}s", flush=True)
    print("quota after:", json.dumps(quota()), flush=True)


if __name__ == "__main__":
    main()
