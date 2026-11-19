-- Frozen copy of the ccc-mcp 0.4.0 database structure, used to prove that an existing
-- bridge.sqlite3 opens with newer code unchanged. Generated from the unmodified Store; do not edit.

CREATE TABLE agent_executions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
 agent TEXT NOT NULL, role TEXT NOT NULL, context_version INTEGER NOT NULL,
 instruction TEXT NOT NULL, adapter TEXT NOT NULL, options TEXT NOT NULL,
 status TEXT NOT NULL, package TEXT NOT NULL, result TEXT, error TEXT,
 worktree TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
 created TEXT NOT NULL, finished TEXT);
CREATE TABLE approvals (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
 plan_id TEXT NOT NULL, plan_version INTEGER NOT NULL, plan_hash TEXT NOT NULL,
 context_version INTEGER NOT NULL, status TEXT NOT NULL,
 identity TEXT, created TEXT NOT NULL, approved_at TEXT);
CREATE TABLE artifacts (
 project_id TEXT NOT NULL, path TEXT NOT NULL, content TEXT NOT NULL,
 sha256 TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(project_id,path));
CREATE TABLE context_patches (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 payload TEXT NOT NULL, committed_version INTEGER);
CREATE TABLE context_versions (
 project_id TEXT NOT NULL REFERENCES projects(id), version INTEGER NOT NULL,
 snapshot TEXT NOT NULL, metadata TEXT NOT NULL, created TEXT NOT NULL,
 PRIMARY KEY(project_id, version));
CREATE TABLE decisions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
 content TEXT NOT NULL, reason TEXT NOT NULL, approver TEXT NOT NULL,
 status TEXT NOT NULL, context_version INTEGER NOT NULL, created TEXT NOT NULL);
CREATE TABLE evaluations (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 task_id TEXT NOT NULL, role TEXT NOT NULL, agent TEXT NOT NULL, model TEXT,
 task_type TEXT NOT NULL, source_execution_id TEXT NOT NULL REFERENCES agent_executions(id),
 version INTEGER NOT NULL, trigger TEXT NOT NULL,
 quality_score REAL, evidence_coverage REAL NOT NULL, task_status TEXT NOT NULL,
 scoring_details TEXT NOT NULL, evidence TEXT NOT NULL, disagreements TEXT NOT NULL,
 schema_version TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE flow_escalations (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 task_id TEXT NOT NULL, step_index INTEGER NOT NULL, role TEXT NOT NULL,
 reason TEXT NOT NULL, detail TEXT NOT NULL, adapter TEXT NOT NULL,
 fake_scenario TEXT NOT NULL, escalation TEXT NOT NULL,
 status TEXT NOT NULL, decision TEXT, decision_note TEXT, decided TEXT, created TEXT NOT NULL);
CREATE TABLE flow_gate_results (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 task_id TEXT NOT NULL, step_index INTEGER NOT NULL, role TEXT NOT NULL,
 source_execution_id TEXT NOT NULL UNIQUE, verdict TEXT NOT NULL,
 strictness TEXT, detail TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE meeting_clarifications (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 task_id TEXT NOT NULL, round INTEGER NOT NULL,
 question TEXT NOT NULL, question_execution_id TEXT NOT NULL,
 response_type TEXT, response_content TEXT, response_execution_id TEXT,
 pm_decision_content TEXT, pm_decision_at TEXT,
 status TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE operator_settings (
 key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE pending_dispatch (
 project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT NOT NULL,
 source_execution_id TEXT NOT NULL, role TEXT NOT NULL,
 context_version INTEGER NOT NULL, plan_content TEXT,
 adapter TEXT NOT NULL, fake_scenario TEXT NOT NULL,
 committed_json TEXT NOT NULL, created TEXT NOT NULL, meeting_round_action TEXT,
 PRIMARY KEY(project_id, task_id));
CREATE TABLE plans (
 project_id TEXT NOT NULL, task_id TEXT NOT NULL, id TEXT NOT NULL,
 version INTEGER NOT NULL, context_version INTEGER NOT NULL, content TEXT NOT NULL,
 hash TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(project_id,task_id,id,version));
CREATE TABLE project_access_events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
 project_id TEXT NOT NULL REFERENCES projects(id), root TEXT NOT NULL,
 access TEXT NOT NULL, identity TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE project_deletions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 identity TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE project_settings (
 project_id TEXT PRIMARY KEY REFERENCES projects(id), content TEXT NOT NULL);
CREATE TABLE project_write_receipts (
 edit_id TEXT PRIMARY KEY REFERENCES workspace_edits(id),
 authorization_id TEXT NOT NULL REFERENCES project_access_events(id));
CREATE TABLE projects (
 id TEXT PRIMARY KEY, root TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
 created TEXT NOT NULL);
CREATE TABLE reputation_events (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 task_id TEXT NOT NULL, role TEXT NOT NULL, agent TEXT NOT NULL, model TEXT,
 task_type TEXT NOT NULL, source_execution_id TEXT NOT NULL UNIQUE REFERENCES agent_executions(id),
 outcome TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE task_controls (
 project_id TEXT NOT NULL, task_id TEXT NOT NULL, paused INTEGER NOT NULL DEFAULT 0,
 stop_requested INTEGER NOT NULL DEFAULT 0, stop_reason TEXT, stop_requested_at TEXT,
 host_test_running INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(project_id,task_id), FOREIGN KEY(project_id,task_id) REFERENCES tasks(project_id,id));
CREATE TABLE task_deletions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 task_id TEXT NOT NULL, identity TEXT NOT NULL, created TEXT NOT NULL,
 UNIQUE(project_id, task_id));
CREATE TABLE task_events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
 old_state TEXT, new_state TEXT NOT NULL, reason TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE task_flows (
 project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT NOT NULL,
 flow_id TEXT NOT NULL, snapshot TEXT NOT NULL, created TEXT NOT NULL,
 PRIMARY KEY(project_id, task_id));
CREATE TABLE task_interventions (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
 action TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', context_version INTEGER NOT NULL,
 execution_id TEXT, delivered_execution_id TEXT, created TEXT NOT NULL,
 FOREIGN KEY(project_id,task_id) REFERENCES tasks(project_id,id));
CREATE TABLE tasks (
 id TEXT NOT NULL, project_id TEXT NOT NULL REFERENCES projects(id),
 requirement TEXT NOT NULL, acceptance TEXT NOT NULL, state TEXT NOT NULL,
 created TEXT NOT NULL, PRIMARY KEY(project_id,id));
CREATE TABLE workspace_batches (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), edits TEXT NOT NULL,
 status TEXT NOT NULL, identity TEXT, created TEXT NOT NULL, approved_at TEXT, applied_at TEXT);
CREATE TABLE workspace_edits (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 path TEXT NOT NULL, before_content TEXT, before_sha256 TEXT,
 content TEXT NOT NULL, sha256 TEXT NOT NULL, diff TEXT NOT NULL,
 comment_check TEXT NOT NULL, status TEXT NOT NULL,
 identity TEXT, created TEXT NOT NULL, approved_at TEXT, applied_at TEXT);
CREATE INDEX idx_agent_executions_project_created
 ON agent_executions(project_id, created);
CREATE INDEX idx_agent_executions_project_task_created
 ON agent_executions(project_id, task_id, created);
CREATE INDEX idx_approvals_project_task_created
 ON approvals(project_id, task_id, created);
CREATE INDEX idx_decisions_project_created
 ON decisions(project_id, created);
CREATE INDEX idx_evaluations_execution ON evaluations(source_execution_id, version);
CREATE INDEX idx_evaluations_project_task ON evaluations(project_id, task_id, role);
CREATE INDEX idx_flow_escalations_task
 ON flow_escalations(project_id, task_id, status);
CREATE INDEX idx_flow_gate_results_task
 ON flow_gate_results(project_id, task_id, step_index);
CREATE INDEX idx_meeting_clarifications_project_task
 ON meeting_clarifications(project_id, task_id, round);
CREATE INDEX idx_project_access_events_project_seq
 ON project_access_events(project_id, seq);
CREATE INDEX idx_project_deletions_project ON project_deletions(project_id);
CREATE INDEX idx_reputation_events_lookup ON reputation_events(agent, role, task_type);
CREATE INDEX idx_task_events_project_task_seq
 ON task_events(project_id, task_id, seq);
CREATE INDEX idx_task_interventions_task
 ON task_interventions(project_id,task_id,seq);
CREATE INDEX idx_workspace_batches_project
 ON workspace_batches(project_id);
CREATE INDEX idx_workspace_edits_project_path_created
 ON workspace_edits(project_id, path, created);
CREATE UNIQUE INDEX one_live_execution
 ON agent_executions(project_id,task_id) WHERE status IN ('QUEUED','RUNNING');
CREATE TRIGGER context_no_delete BEFORE DELETE ON context_versions
 BEGIN SELECT RAISE(ABORT, 'Context history is immutable'); END;
CREATE TRIGGER context_no_update BEFORE UPDATE ON context_versions
 BEGIN SELECT RAISE(ABORT, 'Context history is immutable'); END;
CREATE TRIGGER decisions_no_delete BEFORE DELETE ON decisions
 BEGIN SELECT RAISE(ABORT, 'Decisions are append-only'); END;
CREATE TRIGGER decisions_no_update BEFORE UPDATE ON decisions
 BEGIN SELECT RAISE(ABORT, 'Decisions are append-only'); END;
CREATE TRIGGER evaluations_no_delete BEFORE DELETE ON evaluations
 BEGIN SELECT RAISE(ABORT, 'Evaluation history is immutable'); END;
CREATE TRIGGER evaluations_no_update BEFORE UPDATE ON evaluations
 BEGIN SELECT RAISE(ABORT, 'Evaluation history is immutable; re-evaluate to add a new version'); END;
CREATE TRIGGER flow_escalations_no_delete BEFORE DELETE ON flow_escalations
 BEGIN SELECT RAISE(ABORT, 'Gate escalation history is immutable'); END;
CREATE TRIGGER flow_gate_results_no_delete BEFORE DELETE ON flow_gate_results
 BEGIN SELECT RAISE(ABORT, 'Gate verdict history is immutable'); END;
CREATE TRIGGER flow_gate_results_no_update BEFORE UPDATE ON flow_gate_results
 BEGIN SELECT RAISE(ABORT, 'Gate verdict history is immutable'); END;
CREATE TRIGGER meeting_clarifications_no_delete BEFORE DELETE ON meeting_clarifications
 BEGIN SELECT RAISE(ABORT, 'Meeting Mode clarification history is immutable'); END;
CREATE TRIGGER project_access_no_delete BEFORE DELETE ON project_access_events
 BEGIN SELECT RAISE(ABORT, 'Project access history is immutable'); END;
CREATE TRIGGER project_access_no_update BEFORE UPDATE ON project_access_events
 BEGIN SELECT RAISE(ABORT, 'Project access history is immutable'); END;
CREATE TRIGGER project_deletions_no_delete BEFORE DELETE ON project_deletions
 BEGIN SELECT RAISE(ABORT, 'Project deletion history is immutable'); END;
CREATE TRIGGER project_deletions_no_update BEFORE UPDATE ON project_deletions
 BEGIN SELECT RAISE(ABORT, 'Project deletion history is immutable'); END;
CREATE TRIGGER project_receipt_no_delete BEFORE DELETE ON project_write_receipts
 BEGIN SELECT RAISE(ABORT, 'Project write receipts are immutable'); END;
CREATE TRIGGER project_receipt_no_update BEFORE UPDATE ON project_write_receipts
 BEGIN SELECT RAISE(ABORT, 'Project write receipts are immutable'); END;
CREATE TRIGGER reputation_events_no_delete BEFORE DELETE ON reputation_events
 BEGIN SELECT RAISE(ABORT, 'Reputation ledger is immutable'); END;
CREATE TRIGGER reputation_events_no_update BEFORE UPDATE ON reputation_events
 BEGIN SELECT RAISE(ABORT, 'Reputation ledger is immutable'); END;
CREATE TRIGGER task_deletions_no_delete BEFORE DELETE ON task_deletions
 BEGIN SELECT RAISE(ABORT, 'Task deletion history is immutable'); END;
CREATE TRIGGER task_deletions_no_update BEFORE UPDATE ON task_deletions
 BEGIN SELECT RAISE(ABORT, 'Task deletion history is immutable'); END;
CREATE TRIGGER workspace_batch_members_immutable
 BEFORE UPDATE OF project_id,edits ON workspace_batches
 BEGIN SELECT RAISE(ABORT, 'Workspace batch members are immutable'); END;
CREATE TRIGGER workspace_batches_no_delete BEFORE DELETE ON workspace_batches
 BEGIN SELECT RAISE(ABORT, 'Workspace batch history is immutable'); END;
CREATE TRIGGER workspace_edit_content_immutable
 BEFORE UPDATE OF project_id,path,before_content,before_sha256,content,sha256,diff,comment_check
 ON workspace_edits
 BEGIN SELECT RAISE(ABORT, 'Workspace edit proposals are immutable'); END;
CREATE TRIGGER workspace_edits_no_delete BEFORE DELETE ON workspace_edits
 BEGIN SELECT RAISE(ABORT, 'Workspace edit history is immutable'); END;
