"""Shared helpers for the Stage B (phase run) tests: a project that is ready to run a phase, a
host test that fails on a script, and a driver that does what the spawned workers would do."""

import json
import sys

from ccc_mcp.worker import run_execution

ACTIVE = ("STARTING", "RUNNING")
REQUIREMENT = "Build a slug tool: a slugify function with tests."


def criterion(name, command):
    return {"id": name, "statement": f"{name} holds",
            "verify": {"kind": "host_test", "command": list(command)}}


def plan_dict(command, phases=2, manual_in=None, budget=None):
    """A valid plan whose host_test criteria are exactly the project's configured test command."""
    def phase(i):
        acceptance = [criterion(f"C{i}", command)]
        if manual_in == i:
            acceptance.append({"id": f"M{i}", "statement": "A person reads the README",
                               "verify": {"kind": "manual", "description": "Read it"}})
        return {"id": f"P{i}", "title": f"Phase {i}", "scope": f"Do stage {i}",
                "outputs": [f"thing {i}"], "depends_on": [f"P{i - 1}"] if i > 1 else [],
                "acceptance": acceptance, "workflow": "standard", "risk": "low"}

    plan = {"summary": "A plan", "in_scope": ["the goal"], "out_of_scope": [],
            "phases": [phase(i) for i in range(1, phases + 1)],
            "final_acceptance": [criterion("F", command)], "decisions_needed": [],
            "assumptions": [], "risks": []}
    if budget:
        plan["budget"] = {"max_attempts_per_phase": 3, "max_agent_dispatches": 20,
                          "max_wall_clock_minutes": 60, **budget}
    return plan


def scripted_test(tmp_path, outcomes):
    """A test command that follows `outcomes` ('fail'/'pass'/'crash'/'hang') one call at a time,
    and prints something a failure signature can be taken from."""
    script = tmp_path / "gate_test.py"
    state = tmp_path / "gate_state.json"
    state.write_text(json.dumps({"calls": 0, "outcomes": outcomes}))
    script.write_text(
        "import json, sys, time\n"
        f"path = {str(state)!r}\n"
        "data = json.load(open(path))\n"
        "n = data['calls']; data['calls'] += 1\n"
        "json.dump(data, open(path, 'w'))\n"
        "what = data['outcomes'][min(n, len(data['outcomes']) - 1)]\n"
        "if what == 'hang':\n"
        "    time.sleep(120)\n"
        "if what.startswith('fail'):\n"
        "    print('FAILED test_slug.py::test_value - assert 1 == 3 (' + what + ')')\n"
        "    print('E   assert 1 == 3')\n"
        "    sys.exit(1)\n"
        "print('1 passed in 0.01s')\n"
    )
    return [sys.executable, str(script)]


def custom_plan(command, structure, final_manual=False, budget=None, manual_in=None):
    """A plan whose phases are `structure`: [(id, [depends_on...]), ...], in that order."""
    plan = plan_dict(command, 1, manual_in, budget)
    phases = []
    for pid, depends in structure:
        acceptance = [criterion(f"C-{pid}", command)]
        if manual_in == pid:
            acceptance.append({"id": f"M-{pid}", "statement": "A person reads the README",
                               "verify": {"kind": "manual", "description": "Read it"}})
        phases.append({"id": pid, "title": f"Phase {pid}", "scope": f"Do stage {pid}",
                       "outputs": [f"thing {pid}"], "depends_on": list(depends),
                       "acceptance": acceptance, "workflow": "standard", "risk": "low"})
    plan["phases"] = phases
    if final_manual:
        plan["final_acceptance"].append(
            {"id": "FM", "statement": "A person is happy with the result",
             "verify": {"kind": "manual", "description": "Look at it"}})
    return plan


def approve_current(bridge, goal_id):
    current = bridge.get_goal("demo", goal_id)
    bridge.approve_goal_local("demo", goal_id, current["plan"]["version"], current["plan"]["sha256"])
    return bridge.get_goal("demo", goal_id)


def make_ready_goal(bridge, command, *, phases=2, manual_in=None, title="Slug tool", budget=None,
                    plan=None):
    """A goal with an approved plan, in a project that is authorized and has a test command."""
    bridge.authorize_project_local("demo")
    bridge.configure_workflow("demo", "standard")
    bridge.configure_test_command("demo", command)
    goal = bridge.create_goal("demo", REQUIREMENT, title=title)
    bridge.import_goal_plan(
        "demo", goal["id"], plan or plan_dict(command, phases, manual_in, budget), 0)
    current = bridge.get_goal("demo", goal["id"])
    bridge.approve_goal_local("demo", goal["id"], current["plan"]["version"], current["plan"]["sha256"])
    return bridge.get_goal("demo", goal["id"])


def drive(bridge, run_id, rounds=40):
    """Do what the spawned worker processes would: run every queued execution, then let the
    phase runner look. Stops when nothing is left to do."""
    for _ in range(rounds):
        queued = bridge.store.all(
            "SELECT id FROM agent_executions WHERE status='QUEUED' AND cancel_requested=0 "
            "ORDER BY created")
        for row in queued:
            run_execution(bridge, row["id"])
        step = bridge.step_phase_run(run_id)
        run = bridge.store.one("SELECT status FROM goal_phase_runs WHERE id=?", (run_id,))
        still = bridge.store.one(
            "SELECT COUNT(*) AS n FROM agent_executions WHERE status='QUEUED' AND cancel_requested=0")
        if run["status"] not in ACTIVE and not still["n"]:
            break
        if not queued and not step.get("acted") and not still["n"]:
            break
    return bridge.get_phase_run("demo", run_id)


# ---- a scripted native CLI that really edits files -------------------------------------------------------
#
# Evidence level, said plainly. This is a SIMULATED model: a small script installed as `codex` and
# `claude`. Everything around it is the real thing: the real Codex and Claude adapters build its
# command line and read its structured answer, the real worker function runs the execution, the
# developer role really changes the files of the project's workspace, and the host test is a real
# `pytest` subprocess. No model, and no paid provider, is involved anywhere.

FAKE_PHASE_CLI = r'''#!{python}
import hashlib, json, os, re, sys
argv = sys.argv[1:]
HELP = " ".join(["--print --output-format --json-schema --tools --permission-mode",
    "--setting-sources --strict-mcp-config --mcp-config --no-session-persistence --safe-mode",
    "--sandbox --json --output-schema --output-last-message --ignore-user-config",
    "--skip-git-repo-check --disable-slash-commands --allowedTools"])
if "--version" in argv:
    print("fake-cli 1.0"); sys.exit(0)
if "--help" in argv:
    print(HELP); sys.exit(0)

config = json.load(open(os.environ["FAKE_CLI_CONFIG"]))
prompt = sys.stdin.read()
payload, _ = json.JSONDecoder().raw_decode(prompt, prompt.index('{{"context_package"'))
package, instruction = payload["context_package"], payload["instruction"]
schema, role = package.get("report_schema", "agent_report"), package["role"]
cwd = os.getcwd()
_found = re.search(r"THIS TASK IS PHASE (\S+)", prompt)
phase = _found.group(1) if _found else (package.get("content") or {{}}).get("phase", {{}}).get("id")
_per_phase = config.get("phases", {{}}).get(phase, {{}})


def cfg(name, default=None):
    """A setting for this phase if the config has one for it, else the general one."""
    return _per_phase.get(name, config.get(name, default))


counter_path = os.environ["FAKE_CLI_CONFIG"] + ".counts"
counts = json.load(open(counter_path)) if os.path.exists(counter_path) else {{}}
key = "PM" if schema == "goal_repair" else role
counts[key] = counts.get(key, 0) + 1
json.dump(counts, open(counter_path, "w"))
nth = counts[key]
with open(os.environ["FAKE_CLI_LOG"], "a") as log:
    log.write(json.dumps({{
        "role": role, "schema": schema, "nth": nth, "argv": argv, "cwd": cwd, "phase": phase,
        "instruction": instruction, "content": package.get("content"),
    }}) + "\n")

if config.get("sleep", {{}}).get(key):
    import time
    time.sleep(config["sleep"][key])

if key in config.get("fail", {{}}) and nth in config["fail"][key]:
    print("simulated provider crash", file=sys.stderr); sys.exit(3)

def write_report(report):
    if "--output-last-message" in argv:
        open(argv[argv.index("--output-last-message") + 1], "w").write(json.dumps(report))
    else:
        print(json.dumps({{"structured_output": report, "is_error": False}}))

if schema == "goal_repair":
    from ccc_mcp.adapters.agents.fake import scripted_goal_repair
    answer = scripted_goal_repair(cfg("pm_scenario", "success"), package)
    fix = cfg("pm_fix")
    if fix:
        answer["spec"]["instructions"] += (
            " REPLACE_IN {{file}}: {{old}} -> {{new}}".format(**fix))
        answer["spec"]["change_scope"] = [fix["file"]]
    write_report(answer)
    sys.exit(0)

changed = []
if role in ("RD", "SD/RD") and schema == "agent_report":
    variants = cfg("variants")
    if variants:                       # a developer that never gets it right, differently each time
        item = variants[min(nth, len(variants)) - 1]
        open(os.path.join(cwd, item["file"]), "w").write(item["content"])
        changed.append(item["file"])
    elif "REPAIR (attempt" in instruction:
        for file, old, new in re.findall(r"REPLACE_IN (\S+): (.*?) -> (.*?)(?:\n|$)", instruction):
            path = os.path.join(cwd, file)
            text = open(path).read()
            if old in text:
                open(path, "w").write(text.replace(old, new))
                changed.append(file)
    else:
        for item in cfg("initial", []):
            open(os.path.join(cwd, item["file"]), "w").write(item["content"])
            changed.append(item["file"])
    for item in cfg("also_write", []):
        if nth in item.get("on", [nth]):
            open(os.path.join(cwd, item["file"]), "w").write(item["content"])
            changed.append(item["file"])

report = {{
    "summary": f"{{role}} did its part (call {{nth}}); changed {{changed or 'nothing'}}",
    "findings": [], "risks": [], "next_action": "Run the host tests",
    "tests": [], "needs_revision": False, "simulated": False, "context_updates": [],
    "document": "",
}}
write_report(report)
'''

APP_STUB = "def slugify(text):\n    raise NotImplementedError\n"
APP_TEST = (
    "from app import slugify\n\n\n"
    "def test_words():\n    assert slugify('Hello World') == 'hello-world'\n\n\n"
    "def test_extra_spaces():\n    assert slugify('  A  B ') == 'a-b'\n"
)
BUGGY = 'def slugify(text):\n    return text.lower().replace(" ", "-")\n'
FIXED = 'def slugify(text):\n    return "-".join(text.lower().split())\n'


def make_slug_project(bridge, tmp_path):
    """Replace the shared project's files with a tiny, isolated, committed Python project whose
    tests fail until slugify is right."""
    import subprocess

    root = tmp_path / "repository"
    (root / "app.py").write_text(APP_STUB, encoding="utf-8")
    (root / "test_app.py").write_text(APP_TEST, encoding="utf-8")
    for args in (["add", "."], ["-c", "user.name=T", "-c", "user.email=t@example.invalid",
                                "commit", "-q", "-m", "slug project"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    return root


def install_fake_cli(tmp_path, monkeypatch, **config):
    """Put the scripted CLI first on PATH as `codex` and `claude`; returns (config path, log path).
    `config` may hold: initial, variants, pm_fix, pm_scenario, fail, also_write, sleep."""
    import os
    import stat

    folder = tmp_path / "bin"
    folder.mkdir(exist_ok=True)
    for name in ("codex", "claude"):
        script = folder / name
        script.write_text(FAKE_PHASE_CLI.format(python=sys.executable), encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    path, log = tmp_path / "fake_cli.json", tmp_path / "fake_cli.log"
    path.write_text(json.dumps(config), encoding="utf-8")
    log.write_text("")
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLI_CONFIG", str(path))
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    return path, log


def cli_calls(log):
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


# ---- Stage C: a goal with two dependent phases ---------------------------------------------------------------

CLI_MODULE = 'from app import slugify\n\n\ndef title_url(title):\n    return "/posts/" + slugify(title)\n'
CLI_TEST = (
    "from cli import title_url\n\n\n"
    "def test_title_url():\n    assert title_url('Hello World') == '/posts/hello-world'\n"
)
CLI_MODULE_BAD = 'from app import slugify\n\n\ndef title_url(title):\n    return "/post/" + slugify(title)\n'
PHASE2_FILES = [{"file": "cli.py", "content": CLI_MODULE}, {"file": "test_cli.py", "content": CLI_TEST}]
PHASES = {
    "P1": {"initial": [{"file": "app.py", "content": BUGGY}]},
    "P2": {"initial": PHASE2_FILES},
}


def live_phase_runs(bridge):
    return bridge.store.all(
        "SELECT id FROM goal_phase_runs WHERE status IN ('STARTING','RUNNING') ORDER BY created")


def drive_goal(bridge, goal_run_id, rounds=120):
    """Do what the spawned worker processes would, for a whole goal run: run every queued execution,
    let each active phase run look, then let the goal run look. Stops when a round changes nothing."""
    for _ in range(rounds):
        queued = bridge.store.all(
            "SELECT id FROM agent_executions WHERE status='QUEUED' AND cancel_requested=0 "
            "ORDER BY created")
        for row in queued:
            run_execution(bridge, row["id"])
        acted = bool(queued)
        for row in live_phase_runs(bridge):
            acted |= bool(bridge.step_phase_run(row["id"]).get("acted"))
        step = bridge.step_goal_run(goal_run_id)
        acted |= bool(step.get("acted"))
        status = bridge.store.one("SELECT status FROM goal_runs WHERE id=?", (goal_run_id,))["status"]
        if not acted and status in ("RUNNING", "VERIFYING") and not live_phase_runs(bridge):
            break
        if not acted:
            break
    return bridge.get_goal_run("demo", goal_run_id)
