import json
import sys

import pytest
from typer.testing import CliRunner

from ccc_mcp.core import flows
from ccc_mcp.core.flows import judge_adversarial, parse_findings
from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.interfaces.cli import app
from ccc_mcp.worker import run_execution

FULL = ["SA", "SD", "AR", "SR", "SEC", "RD", "REVIEW", "SEC", "QA"]


def pending_patch(bridge, execution_id):
    result = next(
        item
        for item in bridge.latest("demo")["snapshot"]["agent_results"]
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


def queued(bridge, task_id):
    return bridge.store.one(
        "SELECT id,role FROM agent_executions WHERE project_id='demo' AND task_id=? "
        "AND status='QUEUED'",
        (task_id,),
    )


def roles_run(bridge, task_id):
    rows = bridge.store.all(
        "SELECT role FROM agent_executions WHERE project_id='demo' AND task_id=? ORDER BY created",
        (task_id,),
    )
    return [row["role"] for row in rows]


def drive(bridge, task_id, script, plan="Approved plan", limit=40):
    """Play the worker and the PM until nothing is queued: SA's result is refined by the PM
    with a plan, every later step advances the way the worker's auto-advance does."""
    for _ in range(limit):
        item = queued(bridge, task_id)
        if not item:
            return
        run_execution(bridge, item["id"])
        if item["role"] == "SA":
            bridge.advance_project(
                "demo", pending_patch(bridge, item["id"]), plan,
                adapter="fake", fake_scenario=script,
            )
        else:
            bridge.auto_advance_low_risk(item["id"], allow_fake=True)
    raise AssertionError("workflow did not settle")


def start(bridge, script="success", workflow="full"):
    bridge.authorize_project_local("demo")
    bridge.configure_test_command("demo", [sys.executable, "-c", "print('ok')"])
    bridge.configure_workflow("demo", workflow)
    task_id = bridge.start_project_request(
        "demo", "Add a feature", adapter="fake", fake_scenario=script
    )["workstream"]["id"]
    return task_id


@pytest.fixture
def full_flow(bridge, human):
    return bridge


def test_full_workflow_runs_every_step_in_order_and_completes(full_flow):
    bridge = full_flow
    task_id = start(bridge)
    drive(bridge, task_id, "success")

    assert roles_run(bridge, task_id) == FULL
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"
    assert [g["verdict"] for g in bridge.flow_gate_history("demo", task_id)] == ["PASS"] * 6


def test_design_step_is_read_only_and_the_builder_gets_a_worktree(full_flow):
    bridge = full_flow
    task_id = start(bridge)
    drive(bridge, task_id, "success")

    rows = {
        r["role"]: r
        for r in bridge.store.all(
            "SELECT role,worktree,options FROM agent_executions WHERE project_id='demo' "
            "AND task_id=?",
            (task_id,),
        )
    }
    assert json.loads(rows["SD"]["options"])["read_only"] is True
    assert rows["SD"]["worktree"] is None
    assert rows["RD"]["worktree"]
    assert rows["AR"]["worktree"] is None


def test_the_workflow_is_pinned_when_a_task_starts(full_flow):
    bridge = full_flow
    task_id = start(bridge)
    bridge.configure_workflow("demo", "classic")

    pinned = bridge._task_flow("demo", task_id)
    assert pinned["id"] == "full"
    assert [s["role"] for s in pinned["steps"]] == FULL


def test_classic_tasks_keep_no_flow_row(bridge, human):
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Something")["id"]
    assert bridge._task_flow("demo", task_id) is None
    assert bridge.get_project_settings("demo")["workflow"] == "classic"


def test_a_failed_gate_sends_work_back_to_the_producer_and_reruns_what_follows(full_flow):
    bridge = full_flow
    task_id = start(bridge, "AR:revision,success")
    drive(bridge, task_id, "AR:revision,success")

    assert roles_run(bridge, task_id) == [
        "SA", "SD", "AR", "SD", "AR", "SR", "SEC", "RD", "REVIEW", "SEC", "QA",
    ]
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"
    verdicts = [(g["role"], g["verdict"]) for g in bridge.flow_gate_history("demo", task_id)]
    assert verdicts[:2] == [("AR", "NEEDS_REVISION"), ("AR", "PASS")]
    redo = bridge.store.all(
        "SELECT instruction FROM agent_executions WHERE project_id='demo' AND task_id=? "
        "AND role='SD' ORDER BY created",
        (task_id,),
    )[1]["instruction"]
    assert "Scripted blocking problem" in redo
    assert "evidence: fake adapter scenario" in redo


def test_a_gate_that_keeps_failing_stops_at_the_limit_and_waits_for_the_pm(full_flow):
    bridge = full_flow
    task_id = start(bridge, "AR:revision")
    drive(bridge, task_id, "AR:revision")

    # max_rounds defaults to 2: the first failure bounces, the second escalates.
    assert roles_run(bridge, task_id) == ["SA", "SD", "AR", "SD", "AR"]
    brief = bridge.get_task_status("demo", task_id)["brief"]
    assert brief["waiting_on"] == "PM_FLOW_DECISION"
    assert brief["workflow"]["pending_decision"]["role"] == "AR"
    assert "limit" in brief["workflow"]["pending_decision"]["reason"]


def test_the_round_limit_is_configurable_and_capped(full_flow):
    bridge = full_flow
    bridge.configure_review_gate("demo", max_rounds=3)
    task_id = start(bridge, "AR:revision")
    drive(bridge, task_id, "AR:revision")
    assert roles_run(bridge, task_id) == ["SA", "SD", "AR", "SD", "AR", "SD", "AR"]

    with pytest.raises(BridgeError, match="between 1 and 5"):
        bridge.configure_review_gate("demo", max_rounds=6)
    with pytest.raises(BridgeError, match="between 1 and 5"):
        bridge.configure_review_gate("demo", max_rounds=0)


def test_rejecting_the_approach_escalates_without_another_round(full_flow):
    bridge = full_flow
    task_id = start(bridge, "AR:reject")
    drive(bridge, task_id, "AR:reject")

    assert roles_run(bridge, task_id) == ["SA", "SD", "AR"]
    pending = bridge.pending_flow_decision("demo", task_id)
    assert "fundamental flaw" in pending["reason"]
    assert pending["findings"][0]["severity"] == "blocker"


def test_pm_can_proceed_past_a_gate_it_overrides(full_flow):
    bridge = full_flow
    task_id = start(bridge, "AR:reject")
    drive(bridge, task_id, "AR:reject")

    result = bridge.resolve_flow_gate("demo", task_id, "proceed", "Accepted: known trade-off")
    assert result["next_execution"]["status"] == "QUEUED"
    drive(bridge, task_id, "AR:reject")

    assert roles_run(bridge, task_id) == ["SA", "SD", "AR", *FULL[3:]]
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"
    assert bridge.pending_flow_decision("demo", task_id) is None


def test_pm_can_retry_once_more(full_flow):
    bridge = full_flow
    task_id = start(bridge, "AR:revision")
    drive(bridge, task_id, "AR:revision")

    result = bridge.resolve_flow_gate("demo", task_id, "retry", "Try again with the cache design")
    assert result["bounced_to"] == 1
    assert queued(bridge, task_id)["role"] == "SD"


def test_pm_can_stop_the_task(full_flow):
    bridge = full_flow
    task_id = start(bridge, "AR:reject")
    drive(bridge, task_id, "AR:reject")

    bridge.resolve_flow_gate("demo", task_id, "stop", "Not worth building")
    assert bridge.task("demo", task_id)["state"] == "CANCELLED"


def test_a_decision_needs_a_pending_escalation_and_a_known_choice(full_flow):
    bridge = full_flow
    task_id = start(bridge)
    with pytest.raises(BridgeError, match="No gate decision is pending"):
        bridge.resolve_flow_gate("demo", task_id, "proceed")
    with pytest.raises(BridgeError, match="decision must be one of"):
        bridge.resolve_flow_gate("demo", task_id, "maybe")


def test_ar_claims_without_evidence_cannot_block(full_flow):
    bridge = full_flow
    task_id = start(bridge, "AR:unsupported_claim,success")
    drive(bridge, task_id, "AR:unsupported_claim,success")

    assert roles_run(bridge, task_id) == FULL
    gate = bridge.flow_gate_history("demo", task_id)[0]
    assert gate["verdict"] == "PASS"
    assert gate["detail"]["discarded_unsupported"] == 1
    assert gate["detail"]["reported_revision"] is True
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"


def test_a_non_ar_gate_trusts_its_reviewers_revision_flag(full_flow):
    bridge = full_flow
    task_id = start(bridge, "REVIEW:revision,success")
    drive(bridge, task_id, "REVIEW:revision,success")

    # CR sends work back to the nearest producer, RD, and everything after RD runs again.
    assert roles_run(bridge, task_id) == [
        "SA", "SD", "AR", "SR", "SEC", "RD", "REVIEW", "RD", "REVIEW", "SEC", "QA",
    ]


def test_scripted_scenarios_resolve_per_role_and_round():
    assert flows is not None
    from ccc_mcp.core.flow_engine import scenario_for

    assert scenario_for("success", "AR", 0) == "success"
    assert scenario_for("AR:revision,success", "AR", 0) == "revision"
    assert scenario_for("AR:revision,success", "AR", 1) == "success"
    assert scenario_for("AR:revision,success", "AR", 5) == "success"
    assert scenario_for("AR:revision;default:error", "SA", 0) == "error"
    assert scenario_for("AR:revision", "SA", 0) == "success"


def test_a_workflow_without_an_analyst_uses_the_requirement_as_the_plan(bridge, human, monkeypatch):
    monkeypatch.setitem(
        flows.BUILTIN_FLOWS,
        "lean",
        {
            "name": "Lean",
            "summary": "PM straight to design and development.",
            "legacy": False,
            "steps": [{"role": "SD"}, {"role": "RD"}, {"role": "REVIEW"}],
        },
    )
    task_id = start(bridge, workflow="lean")

    assert queued(bridge, task_id)["role"] == "SD"
    plan = bridge.approved_plan("demo", task_id)["content"]
    assert "Add a feature" in plan
    drive(bridge, task_id, "success")
    assert roles_run(bridge, task_id) == ["SD", "RD", "REVIEW"]
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"


def test_new_roles_get_their_own_report_files(full_flow):
    bridge = full_flow
    task_id = start(bridge)
    drive(bridge, task_id, "success")

    names = {
        row["path"].rsplit("/", 1)[-1]
        for row in bridge.store.all(
            "SELECT path FROM artifacts WHERE project_id='demo' AND path LIKE ?",
            (f"%/tasks/{task_id}/%",),
        )
    }
    assert {"SA-REPORT.md", "SD-REPORT.md", "AR-REPORT.md", "SR-REPORT.md", "SEC-REPORT.md"} <= names
    assert {"IMPLEMENTATION.md", "REVIEW.md", "QA-REPORT.md"} <= names


# -- the pure gate rules ----------------------------------------------------------------


def finding(severity, evidence="src/app.py:12 returns None"):
    text = f"[{severity}] Something is wrong"
    return f"{text} | evidence: {evidence}" if evidence else text


@pytest.mark.parametrize(
    "strictness,severity,verdict",
    [
        ("normal", "minor", "PASS"),
        ("normal", "major", "NEEDS_REVISION"),
        ("normal", "assumption", "PASS"),
        ("strict", "minor", "NEEDS_REVISION"),
        ("strict", "assumption", "PASS"),
        ("extreme", "assumption", "NEEDS_REVISION"),
        ("extreme", "minor", "NEEDS_REVISION"),
    ],
)
def test_strictness_sets_which_findings_can_block(strictness, severity, verdict):
    report = {"findings": [finding(severity)], "needs_revision": True}
    assert judge_adversarial(report, strictness)["verdict"] == verdict


@pytest.mark.parametrize("strictness", flows.STRICTNESS_LEVELS)
def test_a_blocker_is_a_rejection_at_every_strictness(strictness):
    report = {"findings": [finding("blocker")], "needs_revision": True}
    assert judge_adversarial(report, strictness)["verdict"] == "REJECT"


@pytest.mark.parametrize("strictness", flows.STRICTNESS_LEVELS)
def test_no_strictness_lets_unsupported_claims_block(strictness):
    report = {
        "findings": [finding("blocker", evidence=None), "no tag at all", finding("major", "x")],
        "needs_revision": True,
    }
    result = judge_adversarial(report, strictness)
    assert result["verdict"] == "PASS"
    assert result["discarded_unsupported"] == 3


def test_findings_are_parsed_into_severity_claim_and_evidence():
    parsed = parse_findings(
        ["[Major] Token never expires | evidence: auth.py:40 sets no exp", "plain text"]
    )
    assert parsed[0] == ("major", "Token never expires", "auth.py:40 sets no exp")
    assert parsed[1] == (None, "plain text", None)


def test_work_goes_back_to_the_nearest_earlier_producer():
    steps = [{"role": role} for role in FULL]
    assert flows.bounce_index(steps, 2) == 1   # AR -> SD
    assert flows.bounce_index(steps, 3) == 1   # SR -> SD
    assert flows.bounce_index(steps, 6) == 5   # CR -> RD
    assert flows.bounce_index(steps, 8) == 5   # QA -> RD
    assert flows.bounce_index([{"role": "AR"}, {"role": "SA"}], 0) is None


# -- settings ---------------------------------------------------------------------------


def test_workflow_and_review_gate_settings_round_trip(bridge):
    settings = bridge.get_project_settings("demo")
    assert settings["workflow"] == "classic"
    assert settings["review_gate"] == {"ar_strictness": "normal", "max_rounds": 2, "escalation": "ask_user"}

    bridge.configure_workflow("demo", "full")
    bridge.configure_review_gate("demo", ar_strictness="extreme", escalation="auto_pm")
    saved = bridge.get_project_settings("demo")
    assert saved["workflow"] == "full"
    assert saved["review_gate"] == {"ar_strictness": "extreme", "max_rounds": 2, "escalation": "auto_pm"}


@pytest.mark.parametrize(
    "kwargs",
    [{"ar_strictness": "brutal"}, {"escalation": "whenever"}, {"max_rounds": "2"}, {"max_rounds": True}],
)
def test_review_gate_rejects_bad_values(bridge, kwargs):
    with pytest.raises(BridgeError):
        bridge.configure_review_gate("demo", **kwargs)


def test_unknown_workflow_is_rejected(bridge):
    with pytest.raises(BridgeError, match="workflow must be one of"):
        bridge.configure_workflow("demo", "nonsense")


def test_workflow_catalog_lists_the_builtin_flows(bridge):
    bridge.configure_workflow("demo", "full")
    catalog = bridge.workflow_catalog("demo")
    assert catalog["selected"] == "full"
    assert [flow["id"] for flow in catalog["workflows"]] == ["classic", "standard", "full"]
    full = catalog["workflows"][2]
    assert [step["role"] for step in full["steps"]] == FULL


def test_workflow_and_gate_cli(bridge):
    prefix = ["--home", str(bridge.home), "--project", "demo", "config"]
    runner = CliRunner()
    assert runner.invoke(app, prefix + ["workflow", "full"]).exit_code == 0
    assert runner.invoke(
        app, prefix + ["review-gate", "--ar", "strict", "--max-rounds", "3", "--escalation", "auto-pm"]
    ).exit_code == 0
    settings = bridge.get_project_settings("demo")
    assert settings["workflow"] == "full"
    assert settings["review_gate"] == {"ar_strictness": "strict", "max_rounds": 3, "escalation": "auto_pm"}
    assert runner.invoke(app, prefix + ["review-gate", "--max-rounds", "9"]).exit_code != 0


# -- custom workflows ---------------------------------------------------------------------


def test_a_saved_workflow_is_named_workflow_2_then_3(bridge):
    first = bridge.save_workflow("demo", ["SA", "RD", "REVIEW"])
    second = bridge.save_workflow("demo", ["SD", "RD"])
    assert (first["id"], first["name"]) == ("flow-2", "工作流 2")
    assert (second["id"], second["name"]) == ("flow-3", "工作流 3")
    assert [s["role"] for s in first["steps"]] == ["SA", "RD", "REVIEW"]

    catalog = bridge.workflow_catalog("demo")
    assert [f["id"] for f in catalog["workflows"]] == ["classic", "standard", "full", "flow-2", "flow-3"]
    assert [f["builtin"] for f in catalog["workflows"]] == [True, True, True, False, False]


def test_default_names_follow_the_project_language(bridge):
    bridge.configure_language("demo", "en")
    assert bridge.save_workflow("demo", ["SA"])["name"] == "Workflow 2"


def test_a_workflow_can_be_renamed_and_its_steps_changed(bridge):
    saved = bridge.save_workflow("demo", ["SA", "RD", "REVIEW"])
    bridge.save_workflow("demo", ["SD", "AR", "RD"], name="  Quick   path ", workflow_id=saved["id"])

    flow = next(f for f in bridge.workflow_catalog("demo")["workflows"] if f["id"] == saved["id"])
    assert flow["name"] == "Quick path"
    assert [s["role"] for s in flow["steps"]] == ["SD", "AR", "RD"]
    bridge.save_workflow("demo", ["SD", "RD"], workflow_id=saved["id"])  # keeps its name
    assert next(
        f for f in bridge.workflow_catalog("demo")["workflows"] if f["id"] == saved["id"]
    )["name"] == "Quick path"


def test_a_taken_number_is_skipped_even_when_renamed(bridge):
    bridge.save_workflow("demo", ["SA"])
    bridge.save_workflow("demo", ["SA"], name="工作流 3", workflow_id="flow-2")
    assert bridge.save_workflow("demo", ["SA"])["name"] == "工作流 2"  # 2 is free again
    assert bridge.save_workflow("demo", ["SA"])["name"] == "工作流 4"  # 2 and 3 are taken


@pytest.mark.parametrize(
    "steps",
    [[], ["QA"], ["AR", "SD"], ["SA", "NOPE"], ["SA"] * 13, "SA", None],
)
def test_workflow_steps_are_validated(bridge, steps):
    with pytest.raises(BridgeError):
        bridge.save_workflow("demo", steps)


def test_workflow_names_are_validated_and_builtins_are_protected(bridge):
    with pytest.raises(BridgeError):
        bridge.save_workflow("demo", ["SA"], name="x" * 41)
    with pytest.raises(BridgeError):
        bridge.save_workflow("demo", ["SA"], name="   ")
    with pytest.raises(BridgeError, match="Built-in workflows cannot be changed"):
        bridge.save_workflow("demo", ["SA"], workflow_id="full")
    with pytest.raises(BridgeError, match="Built-in workflows cannot be deleted"):
        bridge.delete_workflow("demo", "classic")
    with pytest.raises(BridgeError, match="Unknown workflow"):
        bridge.delete_workflow("demo", "flow-9")


def test_a_project_holds_at_most_twenty_workflows(bridge):
    for _ in range(20):
        bridge.save_workflow("demo", ["SA"])
    with pytest.raises(BridgeError, match="at most 20"):
        bridge.save_workflow("demo", ["SA"])


def test_deleting_the_selected_workflow_falls_back_to_classic(bridge):
    saved = bridge.save_workflow("demo", ["SD", "RD"])
    bridge.configure_workflow("demo", saved["id"])
    assert bridge.delete_workflow("demo", saved["id"]) == {"deleted": saved["id"], "workflow": "classic"}
    assert bridge.get_project_settings("demo")["workflow"] == "classic"


def test_a_custom_workflow_without_an_analyst_runs_end_to_end(bridge, human):
    """PM straight to SD: no SA, so the requirement itself is the plan."""
    saved = bridge.save_workflow("demo", ["SD", "AR", "RD", "REVIEW"], name="PM to SD")
    task_id = start(bridge, workflow=saved["id"])

    assert queued(bridge, task_id)["role"] == "SD"
    assert "Add a feature" in bridge.approved_plan("demo", task_id)["content"]
    drive(bridge, task_id, "success")
    assert roles_run(bridge, task_id) == ["SD", "AR", "RD", "REVIEW"]
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"
    brief = bridge.get_task_status("demo", task_id)["brief"]["workflow"]
    assert (brief["id"], brief["name"], brief["selected_by"]) == (saved["id"], "PM to SD", "fixed")


def test_a_deleted_workflow_does_not_disturb_a_task_already_running_it(bridge, human):
    saved = bridge.save_workflow("demo", ["SD", "RD", "REVIEW"])
    task_id = start(bridge, workflow=saved["id"])
    bridge.delete_workflow("demo", saved["id"])

    drive(bridge, task_id, "success")
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"


def test_settings_json_cannot_smuggle_in_bad_workflows(bridge):
    from pathlib import Path

    from ccc_mcp.infra.config import metadata_name

    root = Path(bridge.project("demo")["root"])
    path = root / metadata_name(root) / "settings.json"
    bridge.configure_workflow("demo", "full")  # makes sure the file exists
    document = json.loads(path.read_text(encoding="utf-8"))
    document["workflows"] = {"flow-2": {"name": "Bad", "steps": [{"role": "AR"}]}}
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(BridgeError):
        bridge.get_project_settings("demo")


# -- who picks the workflow -----------------------------------------------------------------


def test_fixed_selection_ignores_a_requested_workflow(bridge, human):
    bridge.configure_workflow("demo", "full")
    bridge.authorize_project_local("demo")
    task = bridge.create_task("demo", "Anything", workflow="classic")
    assert bridge._task_flow("demo", task["id"])["id"] == "full"


def test_pm_selection_uses_the_requested_workflow_or_the_selected_one(bridge, human):
    bridge.authorize_project_local("demo")
    bridge.configure_workflow_selection("demo", "pm")
    bridge.configure_workflow("demo", "full")

    first = bridge.create_task("demo", "One", workflow="full")
    assert bridge._task_flow("demo", first["id"])["selected_by"] == "pm"
    bridge.cancel_task("demo", first["id"], "next")
    second = bridge.create_task("demo", "Two")
    assert bridge._task_flow("demo", second["id"])["selected_by"] == "fixed"
    bridge.cancel_task("demo", second["id"], "next")
    third = bridge.create_task("demo", "Three", workflow="classic")
    assert bridge._task_flow("demo", third["id"]) is None
    with pytest.raises(BridgeError, match="workflow must be one of"):
        bridge.create_task("demo", "Four", workflow="nope")


def test_auto_selection_adds_security_review_when_the_task_touches_auth(bridge, human):
    bridge.authorize_project_local("demo")
    bridge.configure_workflow_selection("demo", "auto")
    task = bridge.create_task("demo", "Add password reset with an emailed token")

    flow = bridge._task_flow("demo", task["id"])
    assert "SEC" in [s["role"] for s in flow["steps"]]
    assert flow["selected_by"] == "auto"
    assert "SEC" in flow["reason"]


def test_auto_selection_gives_small_changes_the_shortest_workflow(bridge, human):
    bridge.authorize_project_local("demo")
    bridge.configure_workflow_selection("demo", "auto")
    bridge.configure_workflow("demo", "full")
    task = bridge.create_task("demo", "Fix a typo in the README")
    assert bridge._task_flow("demo", task["id"]) is None  # classic is the shortest


def test_auto_selection_prefers_the_shortest_workflow_that_covers_the_need(bridge, human):
    bridge.authorize_project_local("demo")
    bridge.configure_workflow_selection("demo", "auto")
    lean = bridge.save_workflow("demo", ["SD", "AR", "RD", "REVIEW"], name="Lean")
    task = bridge.create_task("demo", "Refactor the storage layer")

    assert bridge._task_flow("demo", task["id"])["id"] == lean["id"]  # SD+AR, shorter than full


def test_auto_selection_keeps_the_project_workflow_for_ordinary_work(bridge, human):
    bridge.authorize_project_local("demo")
    bridge.configure_workflow_selection("demo", "auto")
    bridge.configure_workflow("demo", "full")
    task = bridge.create_task("demo", "Show the user's name on the dashboard header")
    assert bridge._task_flow("demo", task["id"])["id"] == "full"


@pytest.mark.parametrize("value", ["", "manual", None])
def test_selection_mode_is_validated(bridge, value):
    with pytest.raises(BridgeError):
        bridge.configure_workflow_selection("demo", value)


def test_choose_workflow_is_a_pure_function_of_text_and_catalog():
    catalog = {i: flows.flow_definition(i) for i in flows.BUILTIN_FLOWS}
    assert flows.choose_workflow("Add OAuth login", catalog, "classic")[0] == "full"
    assert flows.choose_workflow("rename a variable", catalog, "full")[0] == "classic"
    assert flows.choose_workflow("Add a chart", catalog, "classic")[0] == "classic"
    assert flows.choose_workflow("重構登入流程", catalog, "classic")[0] == "full"


def test_custom_workflow_cli_round_trip(bridge):
    prefix = ["--home", str(bridge.home), "--project", "demo", "config"]
    runner = CliRunner()
    saved = runner.invoke(app, prefix + ["workflow-save", "--steps", "SD, AR ,RD", "--name", "Quick"])
    assert saved.exit_code == 0, saved.output
    assert json.loads(saved.output)["id"] == "flow-2"
    assert runner.invoke(app, prefix + ["workflow", "flow-2"]).exit_code == 0
    assert runner.invoke(app, prefix + ["workflow-selection", "auto"]).exit_code == 0
    listing = json.loads(runner.invoke(app, prefix + ["workflows"]).output)
    assert (listing["selected"], listing["selection"]) == ("flow-2", "auto")
    assert runner.invoke(app, prefix + ["workflow-save", "--steps", "QA"]).exit_code != 0
    deleted = runner.invoke(app, prefix + ["workflow-delete", "flow-2"])
    assert json.loads(deleted.output) == {"deleted": "flow-2", "workflow": "classic"}


# -- the Standard workflow: SD and RD are separate steps -------------------------------------


def test_standard_workflow_runs_design_and_development_as_separate_steps(bridge, human):
    task_id = start(bridge, workflow="standard")
    drive(bridge, task_id, "success")

    assert roles_run(bridge, task_id) == ["SA", "SD", "RD", "REVIEW"]
    assert "SD/RD" not in roles_run(bridge, task_id)
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"
    rows = {
        r["role"]: r["worktree"]
        for r in bridge.store.all(
            "SELECT role,worktree FROM agent_executions WHERE project_id='demo' AND task_id=?",
            (task_id,),
        )
    }
    assert rows["SD"] is None and rows["RD"]


def test_review_sends_standard_work_back_to_rd_not_sd(bridge, human):
    task_id = start(bridge, "REVIEW:revision,success", workflow="standard")
    drive(bridge, task_id, "REVIEW:revision,success")
    assert roles_run(bridge, task_id) == ["SA", "SD", "RD", "REVIEW", "RD", "REVIEW"]


def test_projects_added_by_the_cli_start_on_the_standard_workflow(tmp_path, bridge):
    repo = tmp_path / "fresh-repo"
    repo.mkdir()
    (repo / "a.txt").write_text("x", encoding="utf-8")
    runner = CliRunner()
    prefix = ["--home", str(bridge.home)]
    added = runner.invoke(app, prefix + ["project", "add", str(repo), "--id", "fresh"])
    assert added.exit_code == 0, added.output
    assert bridge.get_project_settings("fresh")["workflow"] == "standard"

    bridge.configure_workflow("fresh", "full")
    runner.invoke(app, prefix + ["project", "add", str(repo), "--id", "fresh"])
    assert bridge.get_project_settings("fresh")["workflow"] == "full"  # a choice is never reset


def test_a_project_that_never_chose_keeps_classic(bridge):
    assert bridge.get_project_settings("demo")["workflow"] == "classic"


# -- the Specification Writer ---------------------------------------------------------------


def artifact_text(bridge, task_id, name):
    row = bridge.store.one(
        "SELECT content FROM artifacts WHERE project_id='demo' AND path LIKE ? "
        "AND path NOT LIKE '%/executions/%'",
        (f"%/tasks/{task_id}/{name}",),
    )
    return row["content"] if row else None


def test_the_spec_writer_is_a_read_only_producer_that_defaults_to_codex(bridge):
    assert bridge.get_project_settings("demo")["roles"]["SW"]["provider"] == "codex"
    assert "SW" in flows.PRODUCER_ROLES and "SW" not in flows.GATE_ROLES
    steps = flows.validated_steps(["SW", "AR", "RD"])  # may start a workflow
    assert steps[0] == {"role": "SW"}


def test_the_spec_writer_saves_its_document_as_spec_md_for_later_steps(bridge, human):
    saved = bridge.save_workflow("demo", ["SA", "SW", "RD", "REVIEW"], name="With spec")
    task_id = start(bridge, workflow=saved["id"])
    drive(bridge, task_id, "success")

    assert roles_run(bridge, task_id) == ["SA", "SW", "RD", "REVIEW"]
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"
    spec = artifact_text(bridge, task_id, "SPEC.md")
    assert "REQ-001" in spec
    assert artifact_text(bridge, task_id, "SW-REPORT.md")
    worktree = bridge.store.one(
        "SELECT worktree FROM agent_executions WHERE project_id='demo' AND task_id=? AND role='SW'",
        (task_id,),
    )["worktree"]
    assert worktree is None  # a writer never touches the working tree

    handoff = bridge.build_handoff("demo", "RD", task_id)
    assert "REQ-001" in handoff["content"]["reports"]["SPEC.md"]


def test_a_gate_after_the_spec_writer_sends_work_back_to_it(bridge, human):
    saved = bridge.save_workflow("demo", ["SA", "SW", "SR"], name="Spec review")
    task_id = start(bridge, "SR:revision,success", workflow=saved["id"])
    drive(bridge, task_id, "SR:revision,success")

    assert roles_run(bridge, task_id) == ["SA", "SW", "SR", "SW", "SR"]
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"


def test_the_writer_sees_what_the_reviewers_concluded(bridge, human):
    saved = bridge.save_workflow("demo", ["SA", "AR", "SW"], name="Discuss then write")
    task_id = start(bridge, workflow=saved["id"])
    drive(bridge, task_id, "success")

    reports = bridge.build_handoff("demo", "SW", task_id)["content"]["reports"]
    assert {"SA-REPORT.md", "AR-REPORT.md"} <= set(reports)


def test_a_report_without_a_document_is_still_valid():
    from ccc_mcp.adapters.agents.base import parse_report

    report = parse_report(
        '{"summary":"s","findings":[],"risks":[],"next_action":"n","tests":[],'
        '"needs_revision":false,"simulated":false,"context_updates":[]}'
    )
    assert report["document"] == ""
