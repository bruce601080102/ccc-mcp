import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime

from ..infra.security import canonical


def now() -> str:
    return datetime.now(UTC).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS project_settings (
 project_id TEXT PRIMARY KEY REFERENCES projects(id), content TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS operator_settings (
 key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS project_access_events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
 project_id TEXT NOT NULL REFERENCES projects(id), root TEXT NOT NULL,
 access TEXT NOT NULL, identity TEXT NOT NULL, created TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS project_access_no_update BEFORE UPDATE ON project_access_events
 BEGIN SELECT RAISE(ABORT, 'Project access history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS project_access_no_delete BEFORE DELETE ON project_access_events
 BEGIN SELECT RAISE(ABORT, 'Project access history is immutable'); END;
CREATE TABLE IF NOT EXISTS project_write_receipts (
 edit_id TEXT PRIMARY KEY REFERENCES workspace_edits(id),
 authorization_id TEXT NOT NULL REFERENCES project_access_events(id));
CREATE TRIGGER IF NOT EXISTS project_receipt_no_update BEFORE UPDATE ON project_write_receipts
 BEGIN SELECT RAISE(ABORT, 'Project write receipts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS project_receipt_no_delete BEFORE DELETE ON project_write_receipts
 BEGIN SELECT RAISE(ABORT, 'Project write receipts are immutable'); END;
CREATE TABLE IF NOT EXISTS workspace_edits (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 path TEXT NOT NULL, before_content TEXT, before_sha256 TEXT,
 content TEXT NOT NULL, sha256 TEXT NOT NULL, diff TEXT NOT NULL,
 comment_check TEXT NOT NULL, status TEXT NOT NULL,
 identity TEXT, created TEXT NOT NULL, approved_at TEXT, applied_at TEXT);
CREATE TRIGGER IF NOT EXISTS workspace_edit_content_immutable
 BEFORE UPDATE OF project_id,path,before_content,before_sha256,content,sha256,diff,comment_check
 ON workspace_edits
 BEGIN SELECT RAISE(ABORT, 'Workspace edit proposals are immutable'); END;
CREATE TRIGGER IF NOT EXISTS workspace_edits_no_delete BEFORE DELETE ON workspace_edits
 BEGIN SELECT RAISE(ABORT, 'Workspace edit history is immutable'); END;
CREATE TABLE IF NOT EXISTS projects (
 id TEXT PRIMARY KEY, root TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
 created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workspace_batches (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), edits TEXT NOT NULL,
 status TEXT NOT NULL, identity TEXT, created TEXT NOT NULL, approved_at TEXT, applied_at TEXT);
CREATE TRIGGER IF NOT EXISTS workspace_batch_members_immutable
 BEFORE UPDATE OF project_id,edits ON workspace_batches
 BEGIN SELECT RAISE(ABORT, 'Workspace batch members are immutable'); END;
CREATE TRIGGER IF NOT EXISTS workspace_batches_no_delete BEFORE DELETE ON workspace_batches
 BEGIN SELECT RAISE(ABORT, 'Workspace batch history is immutable'); END;
CREATE TABLE IF NOT EXISTS context_versions (
 project_id TEXT NOT NULL REFERENCES projects(id), version INTEGER NOT NULL,
 snapshot TEXT NOT NULL, metadata TEXT NOT NULL, created TEXT NOT NULL,
 PRIMARY KEY(project_id, version));
CREATE TABLE IF NOT EXISTS context_patches (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 payload TEXT NOT NULL, committed_version INTEGER);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT NOT NULL, project_id TEXT NOT NULL REFERENCES projects(id),
 requirement TEXT NOT NULL, acceptance TEXT NOT NULL, state TEXT NOT NULL,
 created TEXT NOT NULL, PRIMARY KEY(project_id,id));
CREATE TABLE IF NOT EXISTS task_events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
 old_state TEXT, new_state TEXT NOT NULL, reason TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_controls (
 project_id TEXT NOT NULL, task_id TEXT NOT NULL, paused INTEGER NOT NULL DEFAULT 0,
 stop_requested INTEGER NOT NULL DEFAULT 0, stop_reason TEXT, stop_requested_at TEXT,
 host_test_running INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(project_id,task_id), FOREIGN KEY(project_id,task_id) REFERENCES tasks(project_id,id));
CREATE TABLE IF NOT EXISTS task_interventions (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
 action TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', context_version INTEGER NOT NULL,
 execution_id TEXT, delivered_execution_id TEXT, created TEXT NOT NULL,
 FOREIGN KEY(project_id,task_id) REFERENCES tasks(project_id,id));
CREATE INDEX IF NOT EXISTS idx_task_interventions_task
 ON task_interventions(project_id,task_id,seq);
CREATE TABLE IF NOT EXISTS plans (
 project_id TEXT NOT NULL, task_id TEXT NOT NULL, id TEXT NOT NULL,
 version INTEGER NOT NULL, context_version INTEGER NOT NULL, content TEXT NOT NULL,
 hash TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(project_id,task_id,id,version));
CREATE TABLE IF NOT EXISTS approvals (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
 plan_id TEXT NOT NULL, plan_version INTEGER NOT NULL, plan_hash TEXT NOT NULL,
 context_version INTEGER NOT NULL, status TEXT NOT NULL,
 identity TEXT, created TEXT NOT NULL, approved_at TEXT);
CREATE TABLE IF NOT EXISTS decisions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
 content TEXT NOT NULL, reason TEXT NOT NULL, approver TEXT NOT NULL,
 status TEXT NOT NULL, context_version INTEGER NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_executions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
 agent TEXT NOT NULL, role TEXT NOT NULL, context_version INTEGER NOT NULL,
 instruction TEXT NOT NULL, adapter TEXT NOT NULL, options TEXT NOT NULL,
 status TEXT NOT NULL, package TEXT NOT NULL, result TEXT, error TEXT,
 worktree TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
 created TEXT NOT NULL, finished TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_live_execution
 ON agent_executions(project_id,task_id) WHERE status IN ('QUEUED','RUNNING');
CREATE TABLE IF NOT EXISTS artifacts (
 project_id TEXT NOT NULL, path TEXT NOT NULL, content TEXT NOT NULL,
 sha256 TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(project_id,path));
CREATE TRIGGER IF NOT EXISTS decisions_no_update BEFORE UPDATE ON decisions
 BEGIN SELECT RAISE(ABORT, 'Decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS decisions_no_delete BEFORE DELETE ON decisions
 BEGIN SELECT RAISE(ABORT, 'Decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS context_no_update BEFORE UPDATE ON context_versions
 BEGIN SELECT RAISE(ABORT, 'Context history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS context_no_delete BEFORE DELETE ON context_versions
 BEGIN SELECT RAISE(ABORT, 'Context history is immutable'); END;
CREATE TABLE IF NOT EXISTS pending_dispatch (
 project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT NOT NULL,
 source_execution_id TEXT NOT NULL, role TEXT NOT NULL,
 context_version INTEGER NOT NULL, plan_content TEXT,
 adapter TEXT NOT NULL, fake_scenario TEXT NOT NULL,
 committed_json TEXT NOT NULL, created TEXT NOT NULL,
 PRIMARY KEY(project_id, task_id));
-- A project ID is retired the moment it has one row here; it then disappears from
-- `project list` and every other command refuses it (see ContextHub.project). Append-only
-- and irreversible by design, like every other audit table in this schema: deregistering
-- never deletes the project's Context/task/decision/evidence history, only hides and
-- blocks further use of the ID.
CREATE TABLE IF NOT EXISTS project_deletions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 identity TEXT NOT NULL, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_project_deletions_project ON project_deletions(project_id);
CREATE TRIGGER IF NOT EXISTS project_deletions_no_update BEFORE UPDATE ON project_deletions
 BEGIN SELECT RAISE(ABORT, 'Project deletion history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS project_deletions_no_delete BEFORE DELETE ON project_deletions
 BEGIN SELECT RAISE(ABORT, 'Project deletion history is immutable'); END;
-- A task the operator removed from the console. Like project_deletions this only hides
-- the task (lists, dashboard, shared Context); its executions, evaluations and events
-- stay for audit, and its ID is never handed out again.
CREATE TABLE IF NOT EXISTS task_deletions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 task_id TEXT NOT NULL, identity TEXT NOT NULL, created TEXT NOT NULL,
 UNIQUE(project_id, task_id));
CREATE TRIGGER IF NOT EXISTS task_deletions_no_update BEFORE UPDATE ON task_deletions
 BEGIN SELECT RAISE(ABORT, 'Task deletion history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS task_deletions_no_delete BEFORE DELETE ON task_deletions
 BEGIN SELECT RAISE(ABORT, 'Task deletion history is immutable'); END;
-- Workflow engine (core/flow_engine.py). task_flows pins the workflow a task runs, as the
-- steps it had when the task started, so editing a workflow never changes work in flight.
-- Tasks with no row run the Classic workflow on the original state machine.
CREATE TABLE IF NOT EXISTS task_flows (
 project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT NOT NULL,
 flow_id TEXT NOT NULL, snapshot TEXT NOT NULL, created TEXT NOT NULL,
 PRIMARY KEY(project_id, task_id));
-- One verdict per gate execution, append-only. A gate's failed rounds are counted from
-- these rows, so the retry limit survives a crash or a resumed dispatch.
CREATE TABLE IF NOT EXISTS flow_gate_results (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 task_id TEXT NOT NULL, step_index INTEGER NOT NULL, role TEXT NOT NULL,
 source_execution_id TEXT NOT NULL UNIQUE, verdict TEXT NOT NULL,
 strictness TEXT, detail TEXT NOT NULL, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_flow_gate_results_task
 ON flow_gate_results(project_id, task_id, step_index);
CREATE TRIGGER IF NOT EXISTS flow_gate_results_no_update BEFORE UPDATE ON flow_gate_results
 BEGIN SELECT RAISE(ABORT, 'Gate verdict history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS flow_gate_results_no_delete BEFORE DELETE ON flow_gate_results
 BEGIN SELECT RAISE(ABORT, 'Gate verdict history is immutable'); END;
-- A gate that ran out of rounds, or rejected the approach outright, waits here for the PM.
CREATE TABLE IF NOT EXISTS flow_escalations (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 task_id TEXT NOT NULL, step_index INTEGER NOT NULL, role TEXT NOT NULL,
 reason TEXT NOT NULL, detail TEXT NOT NULL, adapter TEXT NOT NULL,
 fake_scenario TEXT NOT NULL, escalation TEXT NOT NULL,
 status TEXT NOT NULL, decision TEXT, decision_note TEXT, decided TEXT, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_flow_escalations_task
 ON flow_escalations(project_id, task_id, status);
CREATE TRIGGER IF NOT EXISTS flow_escalations_no_delete BEFORE DELETE ON flow_escalations
 BEGIN SELECT RAISE(ABORT, 'Gate escalation history is immutable'); END;
-- Read-path indexes only: every query below already filters by project_id (often also
-- task_id) and these columns have no other supporting index, so lookups on these tables
-- were full scans. Purely additive (IF NOT EXISTS), so they cannot change any query's
-- result, only its cost, and apply lazily to existing bridge.sqlite3 files on next start.
CREATE INDEX IF NOT EXISTS idx_agent_executions_project_task_created
 ON agent_executions(project_id, task_id, created);
CREATE INDEX IF NOT EXISTS idx_agent_executions_project_created
 ON agent_executions(project_id, created);
CREATE INDEX IF NOT EXISTS idx_decisions_project_created
 ON decisions(project_id, created);
CREATE INDEX IF NOT EXISTS idx_task_events_project_task_seq
 ON task_events(project_id, task_id, seq);
CREATE INDEX IF NOT EXISTS idx_approvals_project_task_created
 ON approvals(project_id, task_id, created);
CREATE INDEX IF NOT EXISTS idx_workspace_edits_project_path_created
 ON workspace_edits(project_id, path, created);
CREATE INDEX IF NOT EXISTS idx_workspace_batches_project
 ON workspace_batches(project_id);
CREATE INDEX IF NOT EXISTS idx_project_access_events_project_seq
 ON project_access_events(project_id, seq);
-- Meeting Mode's bounded SD/RD<->SA clarification loop. One row per round; rounds_used
-- is always derived as COUNT(*) WHERE status='RESOLVED' (never a separately stored
-- counter), so it is retry/resume-safe by construction and cannot be reset. Append +
-- status update only, like workspace_edits' lifecycle; never deleted.
CREATE TABLE IF NOT EXISTS meeting_clarifications (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 task_id TEXT NOT NULL, round INTEGER NOT NULL,
 question TEXT NOT NULL, question_execution_id TEXT NOT NULL,
 response_type TEXT, response_content TEXT, response_execution_id TEXT,
 pm_decision_content TEXT, pm_decision_at TEXT,
 status TEXT NOT NULL, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_meeting_clarifications_project_task
 ON meeting_clarifications(project_id, task_id, round);
CREATE TRIGGER IF NOT EXISTS meeting_clarifications_no_delete BEFORE DELETE ON meeting_clarifications
 BEGIN SELECT RAISE(ABORT, 'Meeting Mode clarification history is immutable'); END;
-- ARCHIVE: written by the evaluation engine of earlier releases, which no longer exists. Append-only and
-- kept so existing history is never destroyed; nothing but project export/import reads it.
CREATE TABLE IF NOT EXISTS evaluations (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 task_id TEXT NOT NULL, role TEXT NOT NULL, agent TEXT NOT NULL, model TEXT,
 task_type TEXT NOT NULL, source_execution_id TEXT NOT NULL REFERENCES agent_executions(id),
 version INTEGER NOT NULL, trigger TEXT NOT NULL,
 quality_score REAL, evidence_coverage REAL NOT NULL, task_status TEXT NOT NULL,
 scoring_details TEXT NOT NULL, evidence TEXT NOT NULL, disagreements TEXT NOT NULL,
 schema_version TEXT NOT NULL, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_evaluations_project_task ON evaluations(project_id, task_id, role);
CREATE INDEX IF NOT EXISTS idx_evaluations_execution ON evaluations(source_execution_id, version);
CREATE TRIGGER IF NOT EXISTS evaluations_no_update BEFORE UPDATE ON evaluations
 BEGIN SELECT RAISE(ABORT, 'Evaluation history is immutable; re-evaluate to add a new version'); END;
CREATE TRIGGER IF NOT EXISTS evaluations_no_delete BEFORE DELETE ON evaluations
 BEGIN SELECT RAISE(ABORT, 'Evaluation history is immutable'); END;
-- ARCHIVE: the agent reliability ledger of earlier releases. Append-only; nothing but export/import reads it.
CREATE TABLE IF NOT EXISTS reputation_events (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 task_id TEXT NOT NULL, role TEXT NOT NULL, agent TEXT NOT NULL, model TEXT,
 task_type TEXT NOT NULL, source_execution_id TEXT NOT NULL UNIQUE REFERENCES agent_executions(id),
 outcome TEXT NOT NULL, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_reputation_events_lookup ON reputation_events(agent, role, task_type);
CREATE TRIGGER IF NOT EXISTS reputation_events_no_update BEFORE UPDATE ON reputation_events
 BEGIN SELECT RAISE(ABORT, 'Reputation ledger is immutable'); END;
CREATE TRIGGER IF NOT EXISTS reputation_events_no_delete BEFORE DELETE ON reputation_events
 BEGIN SELECT RAISE(ABORT, 'Reputation ledger is immutable'); END;

-- Custom roles (core/custom_roles.py). Every table here is new, so an existing database only
-- gains structure. No table has a foreign key: project removal and history clearing manage
-- their own deletes (see ROLE_NO_DELETE_TRIGGERS).
--
-- The mutable head of a role. Everything about its content lives in the append-only version
-- rows; here only the pointer to the newest version and the on/off state can change.
CREATE TABLE IF NOT EXISTS custom_roles (
 project_id TEXT NOT NULL, id TEXT NOT NULL, base_role TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('active','disabled','imported_pending')),
 current_version INTEGER NOT NULL,
 origin TEXT NOT NULL CHECK(origin IN ('local','imported')),
 cloned_from TEXT, created TEXT NOT NULL, updated TEXT NOT NULL,
 PRIMARY KEY(project_id, id));
CREATE TRIGGER IF NOT EXISTS custom_roles_no_delete BEFORE DELETE ON custom_roles
 BEGIN SELECT RAISE(ABORT, 'Custom roles cannot be deleted; disable the role instead'); END;
CREATE TRIGGER IF NOT EXISTS custom_roles_identity_fixed
 BEFORE UPDATE OF project_id, id, base_role, origin, cloned_from, created ON custom_roles
 BEGIN SELECT RAISE(ABORT, 'A custom role''s identity and base role cannot change'); END;
CREATE TRIGGER IF NOT EXISTS custom_roles_version_forward_only
 BEFORE UPDATE OF current_version ON custom_roles
 WHEN NEW.current_version < OLD.current_version
 BEGIN SELECT RAISE(ABORT, 'A custom role''s version cannot go backwards'); END;
-- One immutable row per edit.
CREATE TABLE IF NOT EXISTS custom_role_versions (
 project_id TEXT NOT NULL, role_ref TEXT NOT NULL, version INTEGER NOT NULL,
 definition TEXT NOT NULL, sha256 TEXT NOT NULL, actor TEXT NOT NULL, created TEXT NOT NULL,
 PRIMARY KEY(project_id, role_ref, version));
CREATE TRIGGER IF NOT EXISTS custom_role_versions_no_update BEFORE UPDATE ON custom_role_versions
 BEGIN SELECT RAISE(ABORT, 'Custom role versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS custom_role_versions_no_delete BEFORE DELETE ON custom_role_versions
 BEGIN SELECT RAISE(ABORT, 'Custom role versions are immutable'); END;
CREATE TABLE IF NOT EXISTS custom_role_events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, role_ref TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('created','cloned','edited','disabled','enabled',
  'imported','activated','snapshot_trusted','snapshot_revoked')),
 version INTEGER, actor TEXT NOT NULL, detail TEXT NOT NULL, created TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS custom_role_events_no_update BEFORE UPDATE ON custom_role_events
 BEGIN SELECT RAISE(ABORT, 'Custom role history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS custom_role_events_no_delete BEFORE DELETE ON custom_role_events
 BEGIN SELECT RAISE(ABORT, 'Custom role history is immutable'); END;
-- A frozen, content-addressed copy of everything that influences an execution of a role.
-- Never modified. Whether it may be dispatched is recorded separately, below.
CREATE TABLE IF NOT EXISTS role_snapshots (
 project_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, role_ref TEXT NOT NULL,
 version INTEGER NOT NULL, base_role TEXT NOT NULL, content TEXT NOT NULL,
 contract_hash TEXT NOT NULL, origin TEXT NOT NULL CHECK(origin IN ('local','imported')),
 created TEXT NOT NULL, PRIMARY KEY(project_id, snapshot_id));
CREATE TRIGGER IF NOT EXISTS role_snapshots_no_update BEFORE UPDATE ON role_snapshots
 BEGIN SELECT RAISE(ABORT, 'Role snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS role_snapshots_no_delete BEFORE DELETE ON role_snapshots
 BEGIN SELECT RAISE(ABORT, 'Role snapshots are immutable'); END;
-- Confirmations (and revocations) of a snapshot, appended as they happen. The newest one wins;
-- a snapshot with none is trusted only if it was created on this device.
CREATE TABLE IF NOT EXISTS role_snapshot_trust_events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, snapshot_id TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('trust','revoke')),
 actor TEXT NOT NULL, note TEXT NOT NULL, created TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS role_snapshot_trust_events_no_update
 BEFORE UPDATE ON role_snapshot_trust_events
 BEGIN SELECT RAISE(ABORT, 'Role trust history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS role_snapshot_trust_events_no_delete
 BEFORE DELETE ON role_snapshot_trust_events
 BEGIN SELECT RAISE(ABORT, 'Role trust history is immutable'); END;
CREATE VIEW IF NOT EXISTS role_snapshot_status AS
 SELECT s.project_id, s.snapshot_id, s.origin,
  COALESCE(
   (SELECT t.action FROM role_snapshot_trust_events t
     WHERE t.project_id=s.project_id AND t.snapshot_id=s.snapshot_id
     ORDER BY t.seq DESC LIMIT 1),
   CASE s.origin WHEN 'local' THEN 'trust' ELSE 'none' END) = 'trust' AS trusted
 FROM role_snapshots s;
-- Which role definition runs at which step of a task, pinned when the task starts a work
-- round (in the same transaction that pins its workflow). Append-only: a new round adds a pin.
CREATE TABLE IF NOT EXISTS task_roster_pins (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL, seq INTEGER NOT NULL,
 flow_id TEXT NOT NULL, roster TEXT NOT NULL, roster_hash TEXT NOT NULL, created TEXT NOT NULL,
 UNIQUE(project_id, task_id, seq));
CREATE TRIGGER IF NOT EXISTS task_roster_pins_no_update BEFORE UPDATE ON task_roster_pins
 BEGIN SELECT RAISE(ABORT, 'Roster pins are immutable'); END;
CREATE TRIGGER IF NOT EXISTS task_roster_pins_no_delete BEFORE DELETE ON task_roster_pins
 BEGIN SELECT RAISE(ABORT, 'Roster pins are immutable'); END;
-- Which roster a plan approval was given for. A separate append-only table, because the
-- approvals table is written with positional INSERTs and is updated as approvals change state.
CREATE TABLE IF NOT EXISTS approval_rosters (
 project_id TEXT NOT NULL, approval_id TEXT NOT NULL, pin_id TEXT NOT NULL,
 roster_hash TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(project_id, approval_id));
CREATE TRIGGER IF NOT EXISTS approval_rosters_no_update BEFORE UPDATE ON approval_rosters
 BEGIN SELECT RAISE(ABORT, 'Approval roster bindings are immutable'); END;
CREATE TRIGGER IF NOT EXISTS approval_rosters_no_delete BEFORE DELETE ON approval_rosters
 BEGIN SELECT RAISE(ABORT, 'Approval roster bindings are immutable'); END;
-- Every time a pinned role snapshot was checked against the current fixed contract and found
-- different: what changed, whether the dispatch was allowed, and who accepted a blocking change.
-- 'blocked' = incompatible and not accepted; 'accepted' = a person accepted that exact change for
-- this task; 'compatible' = same major versions, run with the current wording.
CREATE TABLE IF NOT EXISTS contract_drift_log (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
 step_id TEXT, snapshot_id TEXT NOT NULL, pinned_contract_hash TEXT NOT NULL,
 current_contract_hash TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('compatible','blocked','accepted')),
 changes TEXT NOT NULL, actor TEXT, created TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS contract_drift_log_no_update BEFORE UPDATE ON contract_drift_log
 BEGIN SELECT RAISE(ABORT, 'Contract drift history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS contract_drift_log_no_delete BEFORE DELETE ON contract_drift_log
 BEGIN SELECT RAISE(ABORT, 'Contract drift history is immutable'); END;
-- The complete text handed to a provider for one real execution. A preview is never stored.
CREATE TABLE IF NOT EXISTS execution_prompts (
 execution_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, provider TEXT NOT NULL,
 via TEXT NOT NULL CHECK(via IN ('adapter_stdin','chatgpt_handoff')),
 prompt_sha256 TEXT NOT NULL, prompt_text TEXT NOT NULL, redacted INTEGER NOT NULL DEFAULT 0,
 segments TEXT NOT NULL, created TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS execution_prompts_no_update BEFORE UPDATE ON execution_prompts
 BEGIN SELECT RAISE(ABORT, 'Execution prompts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS execution_prompts_no_delete BEFORE DELETE ON execution_prompts
 BEGIN SELECT RAISE(ABORT, 'Execution prompts are immutable'); END;

-- Host Test runs (core/host_test_run.py, used by TaskHub.run_configured_test_command). One row per
-- run: who started it, which child process it spawned last, and how it ended. While it is RUNNING
-- the runner's OS file lease is what proves it is alive, never this row or task_controls alone.
-- FINISHED and INTERRUPTED are final. An INTERRUPTED row means the runner was found dead.
CREATE TABLE IF NOT EXISTS host_test_runs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
 owner TEXT NOT NULL, child TEXT,
 status TEXT NOT NULL CHECK(status IN ('RUNNING','FINISHED','INTERRUPTED')),
 started TEXT NOT NULL, finished TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS idx_host_test_runs_task ON host_test_runs(project_id, task_id, started);
CREATE TRIGGER IF NOT EXISTS host_test_runs_no_delete BEFORE DELETE ON host_test_runs
 BEGIN SELECT RAISE(ABORT, 'Host test run history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS host_test_runs_identity_fixed
 BEFORE UPDATE OF id, project_id, task_id, owner, started ON host_test_runs
 BEGIN SELECT RAISE(ABORT, 'Host test run history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS host_test_runs_final_status
 BEFORE UPDATE ON host_test_runs WHEN OLD.status != 'RUNNING'
 BEGIN SELECT RAISE(ABORT, 'Host test run history is immutable'); END;

-- Goals (core/goals.py, Stage A: planning and approval only; nothing here starts any work).
-- SQLite is the only authority for a Goal. There is no Markdown projection and nothing is written to
-- the shared Context, so there is no second copy that could disagree.
--
-- The head of a goal. Only the pointer to the newest plan version and the cancel mark can change; its
-- identity and the requirement as the user wrote it never do. A goal's state (planning, awaiting
-- approval, ready) is derived from the rows below and is never stored, so it cannot contradict them.
CREATE TABLE IF NOT EXISTS goals (
 project_id TEXT NOT NULL, id TEXT NOT NULL, title TEXT NOT NULL, requirement TEXT NOT NULL,
 request_key TEXT, creator TEXT NOT NULL, current_version INTEGER NOT NULL DEFAULT 0,
 cancelled TEXT, cancel_reason TEXT, created TEXT NOT NULL, updated TEXT NOT NULL,
 PRIMARY KEY(project_id, id));
CREATE UNIQUE INDEX IF NOT EXISTS idx_goals_request_key
 ON goals(project_id, request_key) WHERE request_key IS NOT NULL;
CREATE TRIGGER IF NOT EXISTS goals_no_delete BEFORE DELETE ON goals
 BEGIN SELECT RAISE(ABORT, 'Goals cannot be deleted; cancel the goal instead'); END;
CREATE TRIGGER IF NOT EXISTS goals_identity_fixed
 BEFORE UPDATE OF project_id, id, title, requirement, request_key, creator, created ON goals
 BEGIN SELECT RAISE(ABORT, 'A goal''s identity and requirement cannot change'); END;
CREATE TRIGGER IF NOT EXISTS goals_version_forward_only
 BEFORE UPDATE OF current_version ON goals WHEN NEW.current_version < OLD.current_version
 BEGIN SELECT RAISE(ABORT, 'A goal''s plan version cannot go backwards'); END;
CREATE TRIGGER IF NOT EXISTS goals_cancel_is_final
 BEFORE UPDATE OF cancelled, cancel_reason ON goals WHEN OLD.cancelled IS NOT NULL
 BEGIN SELECT RAISE(ABORT, 'A cancelled goal stays cancelled'); END;
-- One immutable row per plan version. The hash is over the canonical plan, and it is what an approval binds to.
CREATE TABLE IF NOT EXISTS goal_plan_versions (
 project_id TEXT NOT NULL, goal_id TEXT NOT NULL, version INTEGER NOT NULL,
 content TEXT NOT NULL, sha256 TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source IN ('provider','manual')), author TEXT NOT NULL,
 based_on INTEGER NOT NULL, change_request TEXT, message TEXT, run_id TEXT, created TEXT NOT NULL,
 PRIMARY KEY(project_id, goal_id, version));
CREATE TRIGGER IF NOT EXISTS goal_plan_versions_no_update BEFORE UPDATE ON goal_plan_versions
 BEGIN SELECT RAISE(ABORT, 'Goal plan versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS goal_plan_versions_no_delete BEFORE DELETE ON goal_plan_versions
 BEGIN SELECT RAISE(ABORT, 'Goal plan versions are immutable'); END;
-- The phases of one version, one row each, written in the same transaction as the version.
CREATE TABLE IF NOT EXISTS goal_plan_phases (
 project_id TEXT NOT NULL, goal_id TEXT NOT NULL, version INTEGER NOT NULL,
 phase_id TEXT NOT NULL, ordinal INTEGER NOT NULL, title TEXT NOT NULL,
 depends_on TEXT NOT NULL, content TEXT NOT NULL,
 PRIMARY KEY(project_id, goal_id, version, phase_id));
CREATE TRIGGER IF NOT EXISTS goal_plan_phases_no_update BEFORE UPDATE ON goal_plan_phases
 BEGIN SELECT RAISE(ABORT, 'Goal plan phases are immutable'); END;
CREATE TRIGGER IF NOT EXISTS goal_plan_phases_no_delete BEFORE DELETE ON goal_plan_phases
 BEGIN SELECT RAISE(ABORT, 'Goal plan phases are immutable'); END;
-- A person's approval of one exact version and hash. A new version is never covered by an old row.
CREATE TABLE IF NOT EXISTS goal_approvals (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, goal_id TEXT NOT NULL,
 plan_version INTEGER NOT NULL, plan_sha256 TEXT NOT NULL, identity TEXT NOT NULL,
 project_access TEXT NOT NULL, scope TEXT NOT NULL, created TEXT NOT NULL,
 UNIQUE(project_id, goal_id, plan_version));
CREATE TRIGGER IF NOT EXISTS goal_approvals_no_update BEFORE UPDATE ON goal_approvals
 BEGIN SELECT RAISE(ABORT, 'Goal approvals are immutable'); END;
CREATE TRIGGER IF NOT EXISTS goal_approvals_no_delete BEFORE DELETE ON goal_approvals
 BEGIN SELECT RAISE(ABORT, 'Goal approvals are immutable'); END;
CREATE TABLE IF NOT EXISTS goal_events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, goal_id TEXT NOT NULL,
 kind TEXT NOT NULL, actor TEXT NOT NULL, detail TEXT NOT NULL, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_goal_events_goal ON goal_events(project_id, goal_id, seq);
CREATE TRIGGER IF NOT EXISTS goal_events_no_update BEFORE UPDATE ON goal_events
 BEGIN SELECT RAISE(ABORT, 'Goal events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS goal_events_no_delete BEFORE DELETE ON goal_events
 BEGIN SELECT RAISE(ABORT, 'Goal events are immutable'); END;
-- One user-triggered Delivery PM call. A goal has at most one live run; a finished run never changes.
CREATE TABLE IF NOT EXISTS goal_plan_runs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, goal_id TEXT NOT NULL, seq INTEGER NOT NULL,
 base_version INTEGER NOT NULL, request TEXT, provider TEXT NOT NULL, adapter TEXT NOT NULL,
 settings TEXT NOT NULL, timeout INTEGER NOT NULL, fake_scenario TEXT,
 status TEXT NOT NULL CHECK(status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')),
 failure_kind TEXT, error TEXT, result TEXT, result_version INTEGER, invocation TEXT, prompt_sha256 TEXT,
 cancel_requested INTEGER NOT NULL DEFAULT 0, spawned_at TEXT, created TEXT NOT NULL,
 started TEXT, finished TEXT, UNIQUE(project_id, goal_id, seq));
CREATE UNIQUE INDEX IF NOT EXISTS one_live_goal_plan_run
 ON goal_plan_runs(project_id, goal_id) WHERE status IN ('QUEUED','RUNNING');
CREATE TRIGGER IF NOT EXISTS goal_plan_runs_no_delete BEFORE DELETE ON goal_plan_runs
 BEGIN SELECT RAISE(ABORT, 'Goal planning history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS goal_plan_runs_identity_fixed
 BEFORE UPDATE OF id, project_id, goal_id, seq, base_version, request, provider, adapter, created
 ON goal_plan_runs
 BEGIN SELECT RAISE(ABORT, 'Goal planning history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS goal_plan_runs_final_status
 BEFORE UPDATE ON goal_plan_runs WHEN OLD.status IN ('SUCCEEDED','FAILED','CANCELLED')
 BEGIN SELECT RAISE(ABORT, 'Goal planning history is immutable'); END;

-- Phase runs (core/phase_runner.py, Stage B): one approved phase of one goal, run through the existing
-- Task engine as one Task with a fixed ID. A phase has at most one run, so starting it twice can never
-- create a second Task. Only the status columns change; what was approved and what was started
-- (plan version and hash, the criteria, the Task, the budget) never does.
CREATE TABLE IF NOT EXISTS goal_phase_runs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, goal_id TEXT NOT NULL, phase_id TEXT NOT NULL,
 plan_version INTEGER NOT NULL, plan_sha256 TEXT NOT NULL, approval_id TEXT NOT NULL,
 task_id TEXT NOT NULL, workflow_id TEXT NOT NULL, requirement_sha256 TEXT NOT NULL,
 adapter TEXT NOT NULL CHECK(adapter IN ('real','fake')), fake_scenario TEXT,
 pm_settings TEXT NOT NULL, pm_timeout INTEGER NOT NULL, budget TEXT NOT NULL,
 criteria TEXT NOT NULL, test_command TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN
  ('STARTING','RUNNING','PAUSED','NEEDS_HUMAN','AWAITING_ACCEPTANCE','PASSED','EXHAUSTED','CANCELLED')),
 reason TEXT, detail TEXT, resume_to TEXT,
 wake_seq INTEGER NOT NULL DEFAULT 0, handled_seq INTEGER NOT NULL DEFAULT 0,
 active_seconds REAL NOT NULL DEFAULT 0, active_since TEXT,
 creator TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, finished TEXT,
 kind TEXT, synthetic TEXT, handoff TEXT, goal_run_id TEXT,
 UNIQUE(project_id, goal_id, phase_id), UNIQUE(project_id, task_id));
CREATE TRIGGER IF NOT EXISTS goal_phase_runs_no_delete BEFORE DELETE ON goal_phase_runs
 BEGIN SELECT RAISE(ABORT, 'Phase run history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS goal_phase_runs_identity_fixed
 BEFORE UPDATE OF id, project_id, goal_id, phase_id, plan_version, plan_sha256, approval_id, task_id,
  workflow_id, requirement_sha256, adapter, fake_scenario, pm_settings, pm_timeout, budget, criteria,
  test_command, creator, created ON goal_phase_runs
 BEGIN SELECT RAISE(ABORT, 'What a phase run was started with cannot change'); END;
CREATE TRIGGER IF NOT EXISTS goal_phase_runs_final_status
 BEFORE UPDATE ON goal_phase_runs WHEN OLD.status IN ('PASSED','EXHAUSTED','CANCELLED')
 BEGIN SELECT RAISE(ABORT, 'A finished phase run stays finished'); END;
-- One builder pass over the phase's Task. Attempt 1 is the first; each later one is a repair the
-- Delivery PM asked for. Rows are opened and then closed once with what was observed.
CREATE TABLE IF NOT EXISTS goal_phase_attempts (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, project_id TEXT NOT NULL, goal_id TEXT NOT NULL,
 phase_id TEXT NOT NULL, number INTEGER NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('INITIAL','REPAIR')),
 repair_id TEXT, status TEXT NOT NULL CHECK(status IN ('RUNNING','PASSED','FAILED','INTERRUPTED')),
 outcome TEXT, evidence TEXT, started TEXT NOT NULL, finished TEXT,
 UNIQUE(run_id, number));
CREATE TRIGGER IF NOT EXISTS goal_phase_attempts_no_delete BEFORE DELETE ON goal_phase_attempts
 BEGIN SELECT RAISE(ABORT, 'Phase attempt history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS goal_phase_attempts_identity_fixed
 BEFORE UPDATE OF id, run_id, project_id, goal_id, phase_id, number, kind, repair_id, started
 ON goal_phase_attempts
 BEGIN SELECT RAISE(ABORT, 'Phase attempt history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS goal_phase_attempts_final_status
 BEFORE UPDATE ON goal_phase_attempts WHEN OLD.status != 'RUNNING'
 BEGIN SELECT RAISE(ABORT, 'Phase attempt history is immutable'); END;
-- One Delivery PM analysis of a failed attempt, and what became of it. At most one is live per run.
CREATE TABLE IF NOT EXISTS goal_phase_repairs (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, project_id TEXT NOT NULL, goal_id TEXT NOT NULL,
 phase_id TEXT NOT NULL, seq INTEGER NOT NULL, after_attempt INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('RUNNING','SUCCEEDED','FAILED','INTERRUPTED')),
 failure_kind TEXT, error TEXT, input_sha256 TEXT NOT NULL, input_summary TEXT NOT NULL,
 prompt_sha256 TEXT, spec TEXT, decision TEXT, message TEXT, restart_step INTEGER,
 restart_execution_id TEXT, invocation TEXT, created TEXT NOT NULL, finished TEXT,
 UNIQUE(run_id, seq));
CREATE UNIQUE INDEX IF NOT EXISTS one_live_phase_repair
 ON goal_phase_repairs(run_id) WHERE status = 'RUNNING';
CREATE TRIGGER IF NOT EXISTS goal_phase_repairs_no_delete BEFORE DELETE ON goal_phase_repairs
 BEGIN SELECT RAISE(ABORT, 'Phase repair history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS goal_phase_repairs_identity_fixed
 BEFORE UPDATE OF id, run_id, project_id, goal_id, phase_id, seq, after_attempt, input_sha256,
  input_summary, created ON goal_phase_repairs
 BEGIN SELECT RAISE(ABORT, 'Phase repair history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS goal_phase_repairs_final_status
 BEFORE UPDATE ON goal_phase_repairs WHEN OLD.status != 'RUNNING' AND NOT (
  OLD.status = 'SUCCEEDED' AND OLD.restart_execution_id IS NULL AND NEW.restart_execution_id IS NOT NULL
  AND NEW.status = 'SUCCEEDED' AND NEW.spec IS OLD.spec AND NEW.decision IS OLD.decision)
 BEGIN SELECT RAISE(ABORT, 'Phase repair history is immutable'); END;

-- Goal runs (core/goal_orchestrator.py, Stage C): the supervisor's durable record for one goal. A goal
-- has at most one run, ever; the run starts phases one at a time, in dependency order, through the
-- Stage B phase runner, then verifies the whole goal. What it was started with never changes. The only
-- thing that may be re-pointed is the plan version, and only by a person (`goal_run_plan_adopted`).
CREATE TABLE IF NOT EXISTS goal_runs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, goal_id TEXT NOT NULL,
 plan_version INTEGER NOT NULL, plan_sha256 TEXT NOT NULL, approval_id TEXT NOT NULL,
 adapter TEXT NOT NULL CHECK(adapter IN ('real','fake')), fake_scenario TEXT,
 pm_settings TEXT NOT NULL, pm_timeout INTEGER NOT NULL, test_command TEXT NOT NULL,
 budget TEXT NOT NULL, base TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN
  ('RUNNING','WAITING_USER','PAUSED','VERIFYING','PASSED','EXHAUSTED','FAILED','CANCELLED')),
 reason TEXT, detail TEXT, resume_to TEXT,
 wake_seq INTEGER NOT NULL DEFAULT 0, handled_seq INTEGER NOT NULL DEFAULT 0,
 active_seconds REAL NOT NULL DEFAULT 0, active_since TEXT,
 creator TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, finished TEXT,
 UNIQUE(project_id, goal_id));
CREATE TRIGGER IF NOT EXISTS goal_runs_no_delete BEFORE DELETE ON goal_runs
 BEGIN SELECT RAISE(ABORT, 'Goal run history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS goal_runs_identity_fixed
 BEFORE UPDATE OF id, project_id, goal_id, adapter, fake_scenario, pm_settings, pm_timeout,
  test_command, budget, base, creator, created ON goal_runs
 BEGIN SELECT RAISE(ABORT, 'What a goal run was started with cannot change'); END;
CREATE TRIGGER IF NOT EXISTS goal_runs_final_status
 BEFORE UPDATE ON goal_runs WHEN OLD.status IN ('PASSED','EXHAUSTED','FAILED','CANCELLED')
 BEGIN SELECT RAISE(ABORT, 'A finished goal run stays finished'); END;
-- What the supervisor recorded when a phase run passed: the Task, roles, providers, prompts, git state and
-- the context handed to the phases that depend on it. Written once; never changed or deleted.
CREATE TABLE IF NOT EXISTS goal_run_phases (
 id TEXT PRIMARY KEY, goal_run_id TEXT NOT NULL, project_id TEXT NOT NULL, goal_id TEXT NOT NULL,
 phase_id TEXT NOT NULL, phase_run_id TEXT NOT NULL, task_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('PLAN','INTEGRATION')),
 evidence TEXT NOT NULL, evidence_sha256 TEXT NOT NULL, handoff TEXT NOT NULL,
 handoff_sha256 TEXT NOT NULL, created TEXT NOT NULL,
 UNIQUE(goal_run_id, phase_id));
CREATE TRIGGER IF NOT EXISTS goal_run_phases_no_update BEFORE UPDATE ON goal_run_phases
 BEGIN SELECT RAISE(ABORT, 'Goal run phase records are immutable'); END;
CREATE TRIGGER IF NOT EXISTS goal_run_phases_no_delete BEFORE DELETE ON goal_run_phases
 BEGIN SELECT RAISE(ABORT, 'Goal run phase records are immutable'); END;
-- One final verification of the whole goal. At most one is live; a finished one never changes.
CREATE TABLE IF NOT EXISTS goal_verifications (
 id TEXT PRIMARY KEY, goal_run_id TEXT NOT NULL, project_id TEXT NOT NULL, goal_id TEXT NOT NULL,
 seq INTEGER NOT NULL, anchor_task_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('RUNNING','PASSED','FAILED','NEEDS_PERSON','INTERRUPTED')),
 outcome TEXT, evidence TEXT, started TEXT NOT NULL, finished TEXT,
 UNIQUE(goal_run_id, seq));
CREATE UNIQUE INDEX IF NOT EXISTS one_live_goal_verification
 ON goal_verifications(goal_run_id) WHERE status = 'RUNNING';
CREATE TRIGGER IF NOT EXISTS goal_verifications_no_delete BEFORE DELETE ON goal_verifications
 BEGIN SELECT RAISE(ABORT, 'Goal verification history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS goal_verifications_identity_fixed
 BEFORE UPDATE OF id, goal_run_id, project_id, goal_id, seq, anchor_task_id, started
 ON goal_verifications
 BEGIN SELECT RAISE(ABORT, 'Goal verification history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS goal_verifications_final_status
 BEFORE UPDATE ON goal_verifications WHEN OLD.status != 'RUNNING'
 BEGIN SELECT RAISE(ABORT, 'Goal verification history is immutable'); END;
"""

# Like the role tables, project removal and history clearing manage this table's deletes.
HOST_TEST_TABLES = ("host_test_runs",)
HOST_TEST_NO_DELETE_TRIGGERS = ("host_test_runs_no_delete",)
# Goals belong to a project, not to a task: removing the project removes them, clearing task history
# does not touch them.
GOAL_TABLES = (
    "goal_verifications", "goal_run_phases", "goal_runs",
    "goal_phase_repairs", "goal_phase_attempts", "goal_phase_runs",
    "goal_plan_runs", "goal_events", "goal_approvals", "goal_plan_phases", "goal_plan_versions",
    "goals",
)
GOAL_NO_DELETE_TRIGGERS = (
    "goals_no_delete", "goal_plan_versions_no_delete", "goal_plan_phases_no_delete",
    "goal_approvals_no_delete", "goal_events_no_delete", "goal_plan_runs_no_delete",
    "goal_phase_runs_no_delete", "goal_phase_attempts_no_delete", "goal_phase_repairs_no_delete",
    "goal_runs_no_delete", "goal_run_phases_no_delete", "goal_verifications_no_delete",
)

# Rows a step workflow (Standard, Full, ...) leaves behind, which reference the project and so used to
# make removing such a project fail on a foreign key. Project removal archives the whole database first
# and now deletes these as well; clearing task history is not changed.
STEP_WORKFLOW_TABLES = (
    "evaluations", "reputation_events", "flow_gate_results", "flow_escalations", "task_flows",
    "task_deletions",
)
STEP_WORKFLOW_NO_DELETE_TRIGGERS = (
    "evaluations_no_delete", "reputation_events_no_delete", "flow_gate_results_no_delete",
    "flow_escalations_no_delete", "task_deletions_no_delete",
)

# Immutable-history triggers that project removal / history clearing must drop while they
# delete (and restore afterwards), as they already do for the older history tables.
ROLE_NO_DELETE_TRIGGERS = (
    "custom_roles_no_delete", "custom_role_versions_no_delete", "custom_role_events_no_delete",
    "role_snapshots_no_delete", "role_snapshot_trust_events_no_delete",
    "task_roster_pins_no_delete", "execution_prompts_no_delete", "approval_rosters_no_delete",
    "contract_drift_log_no_delete",
)
# Removing a project removes its roles; clearing task history only removes what belongs to tasks.
ROLE_PROJECT_TABLES = (
    "execution_prompts", "approval_rosters", "contract_drift_log", "task_roster_pins",
    "role_snapshot_trust_events", "role_snapshots",
    "custom_role_events", "custom_role_versions", "custom_roles",
)
ROLE_TASK_TABLES = (
    "execution_prompts", "approval_rosters", "contract_drift_log", "task_roster_pins",
)
ROLE_TASK_NO_DELETE_TRIGGERS = (
    "execution_prompts_no_delete", "approval_rosters_no_delete", "contract_drift_log_no_delete",
    "task_roster_pins_no_delete",
)

# Columns added to existing tables. All are nullable: NULL means "a built-in role, no step
# identity", which is exactly what every row written before they existed is.
ADDED_COLUMNS = {
    "agent_executions": ("role_ref", "role_snapshot", "step_id"),
    "pending_dispatch": ("role_ref", "role_snapshot", "step_id"),
    "flow_gate_results": ("role_ref", "step_id"),
    "flow_escalations": ("role_ref", "step_id"),
    "evaluations": ("role_ref", "role_snapshot", "step_id"),
    "reputation_events": ("role_ref", "role_snapshot", "step_id"),
    "meeting_clarifications": (
        "asker_role_ref", "asker_step_id", "responder_role_ref", "responder_step_id",
    ),
    # Stage C: how a phase run came about (a plan phase or an integration fix), the context it was
    # handed, and the goal run that started it. NULL for runs started by hand in Stage B.
    "goal_phase_runs": ("kind", "synthetic", "handoff", "goal_run_id"),
    # The name a person gave a goal in the console or in chat. `title` (the name it was created
    # with) never changes; the page shows display_title when there is one.
    "goals": ("display_title",),
    "goal_plan_versions": ("team_snapshots",),
}

# Objects that refer to the added columns, so they can only be created once the columns exist.
# A custom execution must name its role and its step and may only use a snapshot of that very
# role that is trusted. The database enforces it on every insert, so no code path (console,
# MCP tool, CLI, worker or a resumed dispatch) can start an untrusted or anonymous role.
_IDENTITY_CHECKS = """
 SELECT CASE WHEN NEW.role_snapshot IS NOT NULL
   AND (NEW.role_ref IS NULL OR NEW.step_id IS NULL)
  THEN RAISE(ABORT, 'A custom role execution needs role_ref and step_id') END;
 SELECT CASE WHEN NEW.role_snapshot IS NOT NULL AND NOT EXISTS (
   SELECT 1 FROM role_snapshot_status v WHERE v.project_id=NEW.project_id
    AND v.snapshot_id=NEW.role_snapshot AND v.trusted)
  THEN RAISE(ABORT, 'Role snapshot is not trusted on this device') END;
 SELECT CASE WHEN NEW.role_snapshot IS NOT NULL AND NOT EXISTS (
   SELECT 1 FROM role_snapshots s WHERE s.project_id=NEW.project_id
    AND s.snapshot_id=NEW.role_snapshot AND s.role_ref=NEW.role_ref AND s.base_role=NEW.role)
  THEN RAISE(ABORT, 'Role snapshot does not belong to this role') END;
"""
# Also used by a project import, which drops this one trigger for the length of its own
# transaction (finished runs are history, not dispatches) and recreates it from this text.
AGENT_EXECUTION_IDENTITY_TRIGGER = (
    "CREATE TRIGGER IF NOT EXISTS agent_executions_role_identity BEFORE INSERT ON "
    f"agent_executions BEGIN {_IDENTITY_CHECKS} END"
)
COLUMN_SCHEMA = f"""
CREATE TRIGGER IF NOT EXISTS goal_phase_runs_binding_fixed
 BEFORE UPDATE OF kind, synthetic, handoff, goal_run_id ON goal_phase_runs
 BEGIN SELECT RAISE(ABORT, 'What a phase run was started with cannot change'); END;
{AGENT_EXECUTION_IDENTITY_TRIGGER};
CREATE TRIGGER IF NOT EXISTS agent_executions_role_identity_fixed
 BEFORE UPDATE OF role_ref, role_snapshot, step_id ON agent_executions
 WHEN OLD.role_ref IS NOT NEW.role_ref OR OLD.role_snapshot IS NOT NEW.role_snapshot
  OR OLD.step_id IS NOT NEW.step_id
 BEGIN SELECT RAISE(ABORT, 'An execution''s role identity cannot change'); END;
CREATE TRIGGER IF NOT EXISTS pending_dispatch_role_identity BEFORE INSERT ON pending_dispatch
 BEGIN {_IDENTITY_CHECKS} END;
CREATE TRIGGER IF NOT EXISTS pending_dispatch_role_identity_update
 BEFORE UPDATE OF role_ref, role_snapshot, step_id ON pending_dispatch
 BEGIN {_IDENTITY_CHECKS} END;
-- A pending dispatch that moves to another role must say who that is; it can never keep the
-- identity of the role it replaces.
CREATE TRIGGER IF NOT EXISTS pending_dispatch_role_moved BEFORE UPDATE OF role ON pending_dispatch
 WHEN NEW.role IS NOT OLD.role AND OLD.role_ref IS NOT NULL AND NEW.role_ref IS OLD.role_ref
 BEGIN SELECT RAISE(ABORT, 'A pending dispatch changed role without changing its role identity');
 END;
CREATE INDEX IF NOT EXISTS idx_agent_executions_role_ref
 ON agent_executions(project_id, role_ref, step_id);
CREATE INDEX IF NOT EXISTS idx_evaluations_role_ref ON evaluations(project_id, role_ref);
CREATE INDEX IF NOT EXISTS idx_reputation_events_role_ref ON reputation_events(role_ref);
"""


class Store:
    def __init__(self, home):
        self.path = home / "bridge.sqlite3"
        self._transaction_state = threading.local()
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            # Additive column migration: lets a Meeting Mode PM decision reuse the
            # existing pending_dispatch/resume_pending_dispatch atomicity-and-resume
            # machinery instead of a parallel one. NULL for every pre-existing row.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(pending_dispatch)")}
            if "meeting_round_action" not in columns:
                conn.execute("ALTER TABLE pending_dispatch ADD COLUMN meeting_round_action TEXT")
            control_columns = {row[1] for row in conn.execute("PRAGMA table_info(task_controls)")}
            if "host_test_running" not in control_columns:
                conn.execute("ALTER TABLE task_controls ADD COLUMN host_test_running INTEGER NOT NULL DEFAULT 0")
            for table, names in ADDED_COLUMNS.items():
                present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                for name in names:
                    if name not in present:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} TEXT")
            conn.executescript(COLUMN_SCHEMA)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._transaction_state.active = True
            try:
                yield conn
            finally:
                self._transaction_state.active = False

    @property
    def in_transaction(self):
        return getattr(self._transaction_state, "active", False)

    def all(self, query, args=()):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(query, args).fetchall()]

    def one(self, query, args=()):
        rows = self.all(query, args)
        return rows[0] if rows else None

    def execute(self, query, args=()):
        with self.connect() as conn:
            conn.execute(query, args)


def decode(row, *fields):
    if row:
        row = dict(row)
        for field in fields:
            if row.get(field) is not None:
                row[field] = json.loads(row[field])
    return row


def event(conn, project, task, old, new, reason):
    conn.execute(
        "INSERT INTO task_events(project_id,task_id,old_state,new_state,reason,created) "
        "VALUES(?,?,?,?,?,?)",
        (project, task, old, new, reason, now()),
    )


def artifact(conn, project, path, content, ai_output=False):
    from ..infra.security import digest, reject_credentials, reject_secrets

    (reject_credentials if ai_output else reject_secrets)(content)
    conn.execute(
        "INSERT INTO artifacts VALUES(?,?,?,?,?)",
        (project, path, content, digest(content.encode()), now()),
    )


def dump(value):
    return canonical(value)
