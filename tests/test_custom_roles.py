"""Custom roles: identity, immutable versions, frozen snapshots, trust and pinned rosters."""

import json
import sqlite3

import pytest

from ccc_mcp.adapters.storage import now
from ccc_mcp.infra.errors import BridgeError, PermissionDenied, VersionConflict
from ccc_mcp.infra.security import canonical, digest

PROMPT = {"mode": "sections", "sections": {"goal": "Find what the others missed."}}
FREEFORM = {"mode": "freeform", "freeform": "You are a pedantic reviewer.\n\nBe brief."}


def make_role(bridge, name="Strict Reviewer", base="REVIEW", **kwargs):
    kwargs.setdefault("prompt", PROMPT)
    return bridge.create_custom_role("demo", name, base, **kwargs)


def raw(bridge):
    connection = sqlite3.connect(bridge.store.path)
    connection.row_factory = sqlite3.Row
    return connection


def execute_ok(connection, sql, args=()):
    connection.execute(sql, args)
    connection.commit()


def insert_execution(bridge, execution_id, *, role="REVIEW", role_ref=None, snapshot=None,
                     step_id=None, task_id="TASK-0001"):
    with bridge.store.transaction() as conn:
        conn.execute(
            "INSERT INTO agent_executions(id,project_id,task_id,agent,role,context_version,"
            "instruction,adapter,options,status,package,created,role_ref,role_snapshot,step_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (execution_id, "demo", task_id, "codex", role, 1, "go", "fake", "{}", "SUCCEEDED",
             "{}", now(), role_ref, snapshot, step_id),
        )


def mark_imported(bridge, snapshot_id):
    """What a bundle import will do: bring the snapshot in as 'imported'. The column is part of
    the frozen row, so the test rebuilds the row rather than updating it."""
    row = bridge.store.one("SELECT * FROM role_snapshots WHERE snapshot_id=?", (snapshot_id,))
    connection = raw(bridge)
    try:
        connection.execute("DROP TRIGGER role_snapshots_no_delete")
        connection.execute("DELETE FROM role_snapshots WHERE snapshot_id=?", (snapshot_id,))
        connection.execute(
            "CREATE TRIGGER role_snapshots_no_delete BEFORE DELETE ON role_snapshots "
            "BEGIN SELECT RAISE(ABORT, 'Role snapshots are immutable'); END"
        )
        connection.execute(
            "INSERT INTO role_snapshots(project_id,snapshot_id,role_ref,version,base_role,"
            "content,contract_hash,origin,created) VALUES(?,?,?,?,?,?,?,?,?)",
            (row["project_id"], row["snapshot_id"], row["role_ref"], row["version"],
             row["base_role"], row["content"], row["contract_hash"], "imported", row["created"]),
        )
        connection.commit()
    finally:
        connection.close()


# -- creation and validation ---------------------------------------------------------


def test_a_new_role_starts_at_version_one_and_keeps_its_base(bridge):
    role = make_role(bridge, description="Reviews the diff", avatar="monk")
    assert role["id"].startswith("u-") and role["current_version"] == 1
    assert role["base_role"] == "REVIEW" and role["status"] == "active"
    assert role["definition"]["kind"] == "reviewer"
    assert role["definition"]["avatar"] == "monk"
    assert [event["action"] for event in bridge.custom_role_events("demo", role["id"])] == ["created"]


def test_ids_never_collide_even_for_the_same_name(bridge):
    first, second = make_role(bridge), make_role(bridge)
    assert first["id"] != second["id"]
    assert first["definition"] == second["definition"]


@pytest.mark.parametrize("name", [
    "", "   ", "<b>x</b>", "a&b", 'say "hi"', "it's", "back`tick", "a\\b", "x" * 25,
    "ab‮cd", "ab​cd", "ab\x00cd", "-dash first",
])
def test_unsafe_or_invalid_names_are_rejected(bridge, name):
    with pytest.raises(BridgeError):
        make_role(bridge, name=name)


def test_an_ordinary_name_in_any_script_is_accepted(bridge):
    assert make_role(bridge, name="審查官 Alice")["definition"]["name"] == "審查官 Alice"


@pytest.mark.parametrize("base", ["PM", "nope", "", None, "u-custom"])
def test_the_base_must_be_a_builtin_agent_role(bridge, base):
    with pytest.raises(BridgeError):
        make_role(bridge, base=base)


def test_a_role_needs_instructions_and_only_known_sections(bridge):
    with pytest.raises(BridgeError):
        make_role(bridge, prompt={"mode": "sections", "sections": {}})
    with pytest.raises(BridgeError):
        make_role(bridge, prompt={"mode": "sections", "sections": {"extra": "x"}})
    with pytest.raises(BridgeError):
        make_role(bridge, prompt={"mode": "freeform", "freeform": "  "})
    with pytest.raises(BridgeError):
        make_role(bridge, prompt={"mode": "other", "freeform": "x"})
    with pytest.raises(BridgeError):
        make_role(bridge, prompt={"mode": "sections", "sections": {"goal": "x"}, "bogus": 1})


def test_free_form_instructions_do_not_need_the_six_sections(bridge):
    role = make_role(bridge, prompt=FREEFORM)
    prompt = role["definition"]["prompt"]
    assert prompt["mode"] == "freeform" and prompt["freeform"].startswith("You are")
    assert set(prompt["sections"].values()) == {""}


def test_both_bodies_are_kept_whichever_mode_is_chosen(bridge):
    role = make_role(bridge, prompt={
        "mode": "sections", "sections": {"goal": "g"}, "freeform": "  the other text  ",
    })
    prompt = role["definition"]["prompt"]
    assert prompt["mode"] == "sections"
    assert prompt["sections"]["goal"] == "g" and prompt["freeform"] == "  the other text  "


def test_free_form_text_is_stored_exactly_apart_from_line_endings(bridge):
    text = "  indented\n\ttabbed\r\n{braces} 漢字 😀  \n\n"
    role = make_role(bridge, prompt={"mode": "freeform", "freeform": text})
    assert role["definition"]["prompt"]["freeform"] == text.replace("\r\n", "\n")
    with pytest.raises(BridgeError, match="needs instructions"):
        make_role(bridge, prompt={"mode": "freeform", "freeform": " \n\t "})


def test_switching_modes_keeps_everything_and_records_each_switch(bridge):
    role = make_role(bridge, prompt={
        "mode": "sections", "sections": {"goal": "Section goal", "duties": "Section duties"},
    })
    to_free = bridge.update_custom_role(
        "demo", role["id"],
        {"prompt": {"mode": "freeform", "freeform": "Free body\n  with indent"}},
        expected_version=1)
    assert to_free["changed"] and to_free["current_version"] == 2
    prompt = to_free["definition"]["prompt"]
    assert prompt["mode"] == "freeform" and prompt["freeform"] == "Free body\n  with indent"
    assert prompt["sections"]["goal"] == "Section goal"       # not discarded
    assert prompt["sections"]["duties"] == "Section duties"

    back = bridge.update_custom_role(
        "demo", role["id"], {"prompt": {"mode": "sections"}}, expected_version=2)
    assert back["current_version"] == 3
    assert back["definition"]["prompt"]["freeform"] == "Free body\n  with indent"
    assert back["definition"]["prompt"]["sections"]["duties"] == "Section duties"

    versions = bridge.custom_role_versions("demo", role["id"])
    assert [v["definition"]["prompt"]["mode"] for v in versions] == [
        "sections", "freeform", "sections"]
    assert len({v["sha256"] for v in versions}) == 3        # the hash tells each state apart
    assert versions[0]["definition"]["prompt"]["freeform"] == ""   # history is untouched


def test_an_edit_naming_one_section_leaves_the_others_alone(bridge):
    role = make_role(bridge, prompt={
        "mode": "sections", "sections": {"goal": "G", "duties": "D"}, "freeform": "F",
    })
    edited = bridge.update_custom_role(
        "demo", role["id"], {"prompt": {"sections": {"duties": "D2"}}}, expected_version=1)
    prompt = edited["definition"]["prompt"]
    assert prompt["sections"]["goal"] == "G" and prompt["sections"]["duties"] == "D2"
    assert prompt["freeform"] == "F" and prompt["mode"] == "sections"


def test_switching_to_a_mode_with_no_text_is_refused_and_changes_nothing(bridge):
    role = make_role(bridge)
    with pytest.raises(BridgeError, match="the sections are kept"):
        bridge.update_custom_role(
            "demo", role["id"], {"prompt": {"mode": "freeform"}}, expected_version=1)
    assert bridge.get_custom_role("demo", role["id"])["current_version"] == 1
    assert bridge.get_custom_role("demo", role["id"])["definition"]["prompt"]["mode"] == "sections"


def test_a_whitespace_change_in_free_form_text_is_a_new_version_with_a_new_hash(bridge):
    role = make_role(bridge, prompt={"mode": "freeform", "freeform": "Same words."})
    edited = bridge.update_custom_role(
        "demo", role["id"], {"prompt": {"freeform": "Same words. "}}, expected_version=1)
    assert edited["changed"] and edited["version_sha256"] != role["version_sha256"]


def test_instruction_limits_and_secrets(bridge):
    with pytest.raises(BridgeError, match="at most"):
        make_role(bridge, prompt={"mode": "sections", "sections": {"goal": "x" * 4001}})
    with pytest.raises(BridgeError, match="at most"):
        make_role(bridge, prompt={"mode": "freeform", "freeform": "x" * 16001})
    with pytest.raises(BridgeError):
        make_role(bridge, prompt={"mode": "freeform", "freeform": "password = hunter2hunter2"})
    with pytest.raises(BridgeError):
        make_role(bridge, prompt={"mode": "freeform", "freeform": "line‮evil"})
    with pytest.raises(BridgeError):
        make_role(bridge, description="has\nnewline")


def test_stats_avatar_kind_and_runtime_are_validated(bridge):
    for bad in (
        {"stats": {"research": 1}},
        {"stats": {axis: 6 for axis in ("research", "analysis", "planning", "creation",
                                         "verification", "risk")}},
        {"avatar": "not-an-avatar"},
        {"kind": "wizard"},
        {"runtime": {"provider": "unknown"}},
        {"runtime": {"provider": "chatgpt", "model": "gpt"}},
        {"runtime": {"provider": "codex", "evil": 1}},
    ):
        with pytest.raises(BridgeError):
            make_role(bridge, **bad)
    good = {axis: 3 for axis in ("research", "analysis", "planning", "creation",
                                 "verification", "risk")}
    role = make_role(bridge, stats=good, runtime={"provider": "codex", "effort": "high"})
    assert role["definition"]["stats"] == good
    assert role["definition"]["runtime"]["provider"] == "codex"


def test_a_project_has_a_role_limit(bridge, monkeypatch):
    from ccc_mcp.core import custom_roles

    monkeypatch.setattr(custom_roles, "MAX_CUSTOM_ROLES", 2)
    make_role(bridge), make_role(bridge)
    with pytest.raises(BridgeError, match="at most 2"):
        make_role(bridge)


# -- versions ------------------------------------------------------------------------


def test_every_edit_is_a_new_immutable_version_with_its_own_hash(bridge):
    role = make_role(bridge)
    edited = bridge.update_custom_role(
        "demo", role["id"], {"description": "now stricter"}, expected_version=1
    )
    assert edited["changed"] and edited["current_version"] == 2
    versions = bridge.custom_role_versions("demo", role["id"])
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["definition"]["description"] == ""
    assert versions[1]["definition"]["description"] == "now stricter"
    assert versions[0]["sha256"] != versions[1]["sha256"]
    assert bridge.get_custom_role("demo", role["id"], version=1)["definition"]["description"] == ""


def test_an_edit_that_changes_nothing_makes_no_version(bridge):
    role = make_role(bridge)
    same = bridge.update_custom_role("demo", role["id"], {"description": ""}, expected_version=1)
    assert same["changed"] is False and same["current_version"] == 1
    assert len(bridge.custom_role_versions("demo", role["id"])) == 1


def test_a_stale_editor_cannot_overwrite_a_newer_version(bridge):
    role = make_role(bridge)
    bridge.update_custom_role("demo", role["id"], {"description": "a"}, expected_version=1)
    with pytest.raises(VersionConflict):
        bridge.update_custom_role("demo", role["id"], {"description": "b"}, expected_version=1)


def test_the_base_role_cannot_be_changed(bridge):
    role = make_role(bridge)
    with pytest.raises(BridgeError, match="base role cannot change"):
        bridge.update_custom_role("demo", role["id"], {"base_role": "RD"}, expected_version=1)
    with pytest.raises(BridgeError):
        bridge.update_custom_role("demo", role["id"], {"unknown": 1}, expected_version=1)


def test_version_hashes_are_stable_and_cover_identity_and_content(bridge):
    role = make_role(bridge)
    expected = digest(canonical({
        "role_ref": role["id"], "base_role": "REVIEW", "definition": role["definition"],
    }).encode("utf-8"))
    assert role["version_sha256"] == expected


def test_clone_makes_an_independent_role(bridge):
    role = make_role(bridge, name="Original")
    clone = bridge.clone_custom_role("demo", role["id"], "Copy")
    assert clone["id"] != role["id"] and clone["cloned_from"] == role["id"]
    assert clone["definition"]["prompt"] == role["definition"]["prompt"]
    bridge.update_custom_role("demo", clone["id"], {"description": "x"}, expected_version=1)
    assert bridge.get_custom_role("demo", role["id"])["current_version"] == 1
    assert bridge.custom_role_events("demo", clone["id"])[0]["action"] == "cloned"


def test_disabling_keeps_history_and_blocks_edits_until_enabled(bridge):
    role = make_role(bridge)
    disabled = bridge.set_custom_role_status("demo", role["id"], "disabled")
    assert disabled["status"] == "disabled"
    with pytest.raises(BridgeError, match="active role"):
        bridge.update_custom_role("demo", role["id"], {"description": "x"}, expected_version=1)
    assert bridge.get_custom_role("demo", role["id"], version=1)["definition"]
    assert bridge.set_custom_role_status("demo", role["id"], "active")["status"] == "active"
    assert [e["action"] for e in bridge.custom_role_events("demo", role["id"])] == [
        "created", "disabled", "enabled",
    ]


def test_there_is_no_way_to_delete_a_role(bridge):
    assert not [name for name in dir(bridge) if "delete_custom_role" in name]
    role = make_role(bridge)
    connection = raw(bridge)
    with pytest.raises(sqlite3.DatabaseError, match="cannot be deleted"):
        connection.execute("DELETE FROM custom_roles WHERE id=?", (role["id"],))
    connection.close()


# -- database constraints --------------------------------------------------------------


@pytest.mark.parametrize("sql", [
    "UPDATE custom_role_versions SET actor='x'",
    "DELETE FROM custom_role_versions",
    "UPDATE role_snapshots SET content='{}'",
    "UPDATE role_snapshots SET origin='local'",
    "DELETE FROM role_snapshots",
    "UPDATE role_snapshot_trust_events SET action='trust'",
    "DELETE FROM role_snapshot_trust_events",
    "UPDATE custom_role_events SET actor='x'",
    "DELETE FROM custom_role_events",
    "UPDATE task_roster_pins SET roster='[]'",
    "DELETE FROM task_roster_pins",
    "UPDATE execution_prompts SET prompt_text='x'",
    "DELETE FROM execution_prompts",
    "UPDATE approval_rosters SET roster_hash='x'",
    "DELETE FROM approval_rosters",
])
def test_history_tables_refuse_updates_and_deletes(bridge, sql):
    role = make_role(bridge)
    snapshot = bridge.snapshot_custom_role("demo", role["id"])
    bridge.revoke_role_snapshot_trust("demo", snapshot["snapshot_id"], "tester")
    bridge.trust_role_snapshot("demo", snapshot["snapshot_id"], "tester")
    with bridge.store.transaction() as conn:
        bridge.pin_task_roster(conn, "demo", "TASK-0001", "flow-2",
                               [{"role": "REVIEW", "role_ref": role["id"]}])
        bridge.record_execution_prompt(conn, "demo", "EXEC-1", "codex", "adapter_stdin", "p", [])
        conn.execute(
            "INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("APP-1", "demo", "TASK-0001", "PLAN-A", 1, "h", 1, "APPROVED", "t", now(), now()),
        )
        bridge.bind_approval_to_roster(conn, "demo", "APP-1", "TASK-0001")
    connection = raw(bridge)
    with pytest.raises(sqlite3.DatabaseError):
        connection.execute(sql)
    connection.close()


def test_a_roles_identity_columns_and_version_pointer_cannot_move_backwards(bridge):
    role = make_role(bridge)
    bridge.update_custom_role("demo", role["id"], {"description": "x"}, expected_version=1)
    connection = raw(bridge)
    for sql in (
        "UPDATE custom_roles SET base_role='RD'",
        "UPDATE custom_roles SET id='u-other'",
        "UPDATE custom_roles SET origin='imported'",
        "UPDATE custom_roles SET current_version=1",
        "UPDATE custom_roles SET status='bogus'",
    ):
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(sql)
    connection.close()


# -- snapshots and trust ----------------------------------------------------------------


def test_a_snapshot_freezes_everything_that_affects_a_run(bridge):
    role = make_role(bridge, runtime={"provider": "codex", "model": "m1", "effort": "high"})
    snapshot = bridge.snapshot_custom_role("demo", role["id"])
    content = snapshot["content"]
    assert content["role_ref"] == role["id"] and content["base_role"] == "REVIEW"
    assert content["version"] == 1 and content["version_sha256"] == role["version_sha256"]
    assert content["definition"]["prompt"] == role["definition"]["prompt"]
    assert content["runtime"] == {
        "provider": "codex", "model": "m1", "effort": "high", "quota_fallback": None,
        "inherited": False,
    }
    assert content["contract"]["contract_hash"] == snapshot["contract_hash"]
    assert set(content["contract"]["components"]) >= {"operator_policy", "agent_report_schema"}
    assert snapshot["snapshot_id"] == digest(canonical(content).encode("utf-8"))
    assert snapshot["origin"] == "local" and snapshot["trusted"] is True


def test_the_same_content_is_the_same_snapshot(bridge):
    role = make_role(bridge)
    assert (
        bridge.snapshot_custom_role("demo", role["id"])["snapshot_id"]
        == bridge.snapshot_custom_role("demo", role["id"])["snapshot_id"]
    )
    assert bridge.store.one("SELECT COUNT(*) AS n FROM role_snapshots")["n"] == 1


def test_editing_a_role_never_changes_an_existing_snapshot(bridge):
    role = make_role(bridge)
    first = bridge.snapshot_custom_role("demo", role["id"])
    bridge.update_custom_role("demo", role["id"], {"description": "later"}, expected_version=1)
    second = bridge.snapshot_custom_role("demo", role["id"])
    assert second["snapshot_id"] != first["snapshot_id"]
    again = bridge.role_snapshot("demo", first["snapshot_id"])
    assert again["content"] == first["content"] and again["content"]["version"] == 1


def test_an_inherited_provider_is_pinned_as_the_value_it_had(bridge):
    role = make_role(bridge)
    before = bridge.snapshot_custom_role("demo", role["id"])
    provider = before["content"]["runtime"]["provider"]
    assert before["content"]["runtime"]["inherited"] is True
    other = "codex" if provider != "codex" else "claude"
    bridge.configure_role("demo", "REVIEW", other)
    after = bridge.snapshot_custom_role("demo", role["id"])
    assert after["content"]["runtime"]["provider"] == other
    assert after["snapshot_id"] != before["snapshot_id"]
    assert bridge.role_snapshot("demo", before["snapshot_id"])["content"]["runtime"][
        "provider"] == provider


def test_a_disabled_or_pending_role_cannot_be_snapshotted(bridge):
    role = make_role(bridge)
    bridge.set_custom_role_status("demo", role["id"], "disabled")
    with pytest.raises(BridgeError, match="disabled"):
        bridge.snapshot_custom_role("demo", role["id"])


def test_imported_snapshots_are_untrusted_until_confirmed_by_a_separate_record(bridge):
    role = make_role(bridge)
    snapshot_id = bridge.snapshot_custom_role("demo", role["id"])["snapshot_id"]
    mark_imported(bridge, snapshot_id)
    before = bridge.store.one("SELECT * FROM role_snapshots WHERE snapshot_id=?", (snapshot_id,))
    assert bridge.role_snapshot("demo", snapshot_id)["trusted"] is False
    with pytest.raises(PermissionDenied):
        bridge.require_role_snapshot_trusted("demo", snapshot_id)

    bridge.trust_role_snapshot("demo", snapshot_id, "operator", "checked the prompt")
    assert bridge.role_snapshot("demo", snapshot_id)["trusted"] is True
    assert bridge.store.one(
        "SELECT * FROM role_snapshots WHERE snapshot_id=?", (snapshot_id,)
    ) == before  # the frozen row did not change
    events = bridge.store.all("SELECT action,actor FROM role_snapshot_trust_events")
    assert events == [{"action": "trust", "actor": "operator"}]

    bridge.revoke_role_snapshot_trust("demo", snapshot_id, "operator")
    assert bridge.role_snapshot("demo", snapshot_id)["trusted"] is False


def test_trust_needs_a_named_actor(bridge):
    role = make_role(bridge)
    snapshot_id = bridge.snapshot_custom_role("demo", role["id"])["snapshot_id"]
    mark_imported(bridge, snapshot_id)
    with pytest.raises(BridgeError):
        bridge.trust_role_snapshot("demo", snapshot_id, "  ")


def test_an_untrusted_snapshot_cannot_be_dispatched_by_any_entry_point(bridge):
    """The check lives in the database, so no caller (console, MCP tool, CLI, worker or a
    resumed dispatch) can reach an execution row without it."""
    role = make_role(bridge)
    snapshot_id = bridge.snapshot_custom_role("demo", role["id"])["snapshot_id"]
    mark_imported(bridge, snapshot_id)

    with pytest.raises(sqlite3.DatabaseError, match="not trusted"):
        insert_execution(bridge, "EXEC-1", role_ref=role["id"], snapshot=snapshot_id,
                         step_id="001-s00")
    with pytest.raises(sqlite3.DatabaseError, match="not trusted"):
        with bridge.store.transaction() as conn:
            conn.execute(
                "INSERT INTO pending_dispatch(project_id,task_id,source_execution_id,role,"
                "context_version,plan_content,adapter,fake_scenario,committed_json,created,"
                "role_ref,role_snapshot,step_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("demo", "TASK-0001", "EXEC-0", "REVIEW", 1, "", "fake", "success", "{}", now(),
                 role["id"], snapshot_id, "001-s00"),
            )
    with pytest.raises(sqlite3.DatabaseError, match="not trusted"):
        with bridge.store.transaction() as conn:
            conn.execute(
                "INSERT INTO pending_dispatch(project_id,task_id,source_execution_id,role,"
                "context_version,plan_content,adapter,fake_scenario,committed_json,created) "
                "VALUES('demo','TASK-0001','EXEC-0','REVIEW',1,'','fake','success','{}',?)",
                (now(),),
            )
            conn.execute(
                "UPDATE pending_dispatch SET role_ref=?, role_snapshot=?, step_id='001-s00'",
                (role["id"], snapshot_id),
            )

    bridge.trust_role_snapshot("demo", snapshot_id, "operator")
    insert_execution(bridge, "EXEC-1", role_ref=role["id"], snapshot=snapshot_id,
                     step_id="001-s00")
    bridge.revoke_role_snapshot_trust("demo", snapshot_id, "operator")
    with pytest.raises(sqlite3.DatabaseError, match="not trusted"):
        insert_execution(bridge, "EXEC-2", role_ref=role["id"], snapshot=snapshot_id,
                         step_id="001-s00")


def test_an_unknown_snapshot_cannot_be_dispatched(bridge):
    with pytest.raises(sqlite3.DatabaseError, match="not trusted"):
        insert_execution(bridge, "EXEC-1", role_ref="u-ghost", snapshot="0" * 64,
                         step_id="001-s00")


def test_a_custom_execution_must_carry_its_role_and_its_step(bridge):
    role = make_role(bridge)
    snapshot_id = bridge.snapshot_custom_role("demo", role["id"])["snapshot_id"]
    with pytest.raises(sqlite3.DatabaseError, match="role_ref and step_id"):
        insert_execution(bridge, "EXEC-1", snapshot=snapshot_id, step_id="001-s00")
    with pytest.raises(sqlite3.DatabaseError, match="role_ref and step_id"):
        insert_execution(bridge, "EXEC-1", role_ref=role["id"], snapshot=snapshot_id)
    with pytest.raises(sqlite3.DatabaseError, match="does not belong"):
        other = make_role(bridge, name="Other")
        insert_execution(bridge, "EXEC-1", role_ref=other["id"], snapshot=snapshot_id,
                         step_id="001-s00")


def test_built_in_executions_need_none_of_it(bridge):
    insert_execution(bridge, "EXEC-1")
    row = bridge.store.one("SELECT role_ref,role_snapshot,step_id FROM agent_executions")
    assert row == {"role_ref": None, "role_snapshot": None, "step_id": None}


# -- rosters, steps and executions ---------------------------------------------------------


def pin(bridge, steps, task_id="TASK-0001", flow_id="flow-2"):
    with bridge.store.transaction() as conn:
        return bridge.pin_task_roster(conn, "demo", task_id, flow_id, steps)


def test_one_role_used_twice_is_two_steps_with_their_own_ids(bridge):
    role = make_role(bridge)
    pinned = pin(bridge, [
        {"role": "SA"},
        {"role": "REVIEW", "role_ref": role["id"]},
        {"role": "SD"},
        {"role": "REVIEW", "role_ref": role["id"]},
    ])
    roster = pinned["roster"]
    ids = [step["step_id"] for step in roster]
    assert len(set(ids)) == 4 and ids[0] == "001-s00"
    assert roster[1]["snapshot_id"] == roster[3]["snapshot_id"] is not None
    assert roster[0]["role_ref"] is None and roster[0]["snapshot_id"] is None
    assert roster[1]["role_version"] == 1


def test_executions_of_one_step_and_of_two_steps_stay_distinct(bridge):
    """The same custom role at two steps, and a retried step, never overwrite each other:
    step_id names the step, execution_id the run."""
    role = make_role(bridge)
    other = make_role(bridge, name="Other Reviewer")
    snapshot, other_snapshot = (
        bridge.snapshot_custom_role("demo", r["id"])["snapshot_id"] for r in (role, other)
    )
    insert_execution(bridge, "EXEC-1", role_ref=role["id"], snapshot=snapshot, step_id="001-s01")
    insert_execution(bridge, "EXEC-2", role_ref=role["id"], snapshot=snapshot, step_id="001-s01")
    insert_execution(bridge, "EXEC-3", role_ref=role["id"], snapshot=snapshot, step_id="001-s03")
    insert_execution(bridge, "EXEC-4", role_ref=other["id"], snapshot=other_snapshot,
                     step_id="001-s02")
    by_step = {
        row["step_id"]: row["n"]
        for row in bridge.store.all(
            "SELECT step_id, COUNT(*) AS n FROM agent_executions WHERE role='REVIEW' "
            "GROUP BY step_id"
        )
    }
    assert by_step == {"001-s01": 2, "001-s03": 1, "001-s02": 1}
    by_role = {
        row["role_ref"]: row["n"]
        for row in bridge.store.all(
            "SELECT role_ref, COUNT(*) AS n FROM agent_executions GROUP BY role_ref"
        )
    }
    assert by_role == {role["id"]: 3, other["id"]: 1}


def test_gate_results_evaluations_and_clarifications_can_name_the_step(bridge):
    for table, extra in (
        ("flow_gate_results", {"role_ref", "step_id"}),
        ("flow_escalations", {"role_ref", "step_id"}),
        ("evaluations", {"role_ref", "role_snapshot", "step_id"}),
        ("reputation_events", {"role_ref", "role_snapshot", "step_id"}),
        ("pending_dispatch", {"role_ref", "role_snapshot", "step_id"}),
        ("meeting_clarifications", {
            "asker_role_ref", "asker_step_id", "responder_role_ref", "responder_step_id",
        }),
    ):
        columns = {row["name"] for row in bridge.store.all(f"PRAGMA table_info({table})")}
        assert extra <= columns, table


def test_editing_or_disabling_a_role_leaves_a_pinned_roster_unchanged(bridge):
    role = make_role(bridge)
    first = pin(bridge, [{"role": "REVIEW", "role_ref": role["id"]}])
    bridge.update_custom_role("demo", role["id"], {"description": "changed"}, expected_version=1)
    bridge.set_custom_role_status("demo", role["id"], "disabled")

    stored = bridge.current_roster_pin("demo", "TASK-0001")
    assert stored["roster"] == first["roster"] and stored["roster_hash"] == first["roster_hash"]
    snapshot = bridge.role_snapshot("demo", first["roster"][0]["snapshot_id"])
    assert snapshot["content"]["version"] == 1
    assert snapshot["content"]["definition"]["description"] == ""
    # and the old snapshot can still be used to carry the task on
    insert_execution(bridge, "EXEC-1", role_ref=role["id"], snapshot=snapshot["snapshot_id"],
                     step_id=first["roster"][0]["step_id"])


def test_a_new_pin_is_appended_and_old_ones_remain_as_history(bridge):
    role = make_role(bridge)
    first = pin(bridge, [{"role": "REVIEW", "role_ref": role["id"]}])
    bridge.update_custom_role("demo", role["id"], {"description": "v2"}, expected_version=1)
    second = pin(bridge, [{"role": "REVIEW", "role_ref": role["id"]}])
    assert (first["seq"], second["seq"]) == (1, 2)
    assert first["roster"][0]["step_id"] != second["roster"][0]["step_id"]
    assert second["roster"][0]["role_version"] == 2
    assert first["roster_hash"] != second["roster_hash"]
    assert bridge.current_roster_pin("demo", "TASK-0001")["seq"] == 2
    assert bridge.store.one("SELECT COUNT(*) AS n FROM task_roster_pins")["n"] == 2


def approve_in(bridge, approval_id, task_id="TASK-0001"):
    with bridge.store.transaction() as conn:
        conn.execute(
            "INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (approval_id, "demo", task_id, "PLAN-A", 1, "h", 1, "APPROVED", "tester", now(), now()),
        )
        return bridge.bind_approval_to_roster(conn, "demo", approval_id, task_id)


def test_an_approval_belongs_to_exactly_one_roster(bridge):
    role = make_role(bridge)
    pin(bridge, [{"role": "REVIEW", "role_ref": role["id"]}])
    first_hash = approve_in(bridge, "APP-1")
    assert bridge.approval_roster("demo", "APP-1")["roster_hash"] == first_hash
    assert bridge.roster_matches_approval("demo", "TASK-0001", "APP-1")

    # the role changes and the task starts a new round with a new pin
    bridge.update_custom_role("demo", role["id"], {"description": "v2"}, expected_version=1)
    newer = pin(bridge, [{"role": "REVIEW", "role_ref": role["id"]}])
    assert not bridge.roster_matches_approval("demo", "TASK-0001", "APP-1")
    second_hash = approve_in(bridge, "APP-2")
    assert second_hash == newer["roster_hash"] != first_hash
    assert bridge.roster_matches_approval("demo", "TASK-0001", "APP-2")
    assert not bridge.roster_matches_approval("demo", "TASK-0001", "APP-1")


def test_approvals_made_without_a_pinned_roster_are_unaffected(bridge):
    """Every approval from before custom roles, and any task using only built-in roles."""
    assert approve_in(bridge, "APP-1") is None
    assert bridge.approval_roster("demo", "APP-1") is None
    assert bridge.roster_matches_approval("demo", "TASK-0001", "APP-1")
    role = make_role(bridge)
    pin(bridge, [{"role": "REVIEW", "role_ref": role["id"]}])
    assert not bridge.roster_matches_approval("demo", "TASK-0001", "APP-1")


def test_an_approval_can_be_bound_only_once_and_the_binding_never_changes(bridge):
    role = make_role(bridge)
    pin(bridge, [{"role": "REVIEW", "role_ref": role["id"]}])
    approve_in(bridge, "APP-1")
    with pytest.raises(sqlite3.IntegrityError):
        with bridge.store.transaction() as conn:
            bridge.bind_approval_to_roster(conn, "demo", "APP-1", "TASK-0001")
    connection = raw(bridge)
    for sql in ("UPDATE approval_rosters SET roster_hash='x'", "DELETE FROM approval_rosters"):
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            connection.execute(sql)
    connection.close()


def test_pinning_rejects_a_mismatched_disabled_or_unknown_step(bridge):
    role = make_role(bridge)
    with pytest.raises(BridgeError, match="not based on"):
        pin(bridge, [{"role": "RD", "role_ref": role["id"]}])
    with pytest.raises(BridgeError, match="Unknown role"):
        pin(bridge, [{"role": "PM"}])
    with pytest.raises(BridgeError):
        pin(bridge, [{"role": "REVIEW", "role_ref": "u-ghost"}])
    bridge.set_custom_role_status("demo", role["id"], "disabled")
    with pytest.raises(BridgeError, match="disabled"):
        pin(bridge, [{"role": "REVIEW", "role_ref": role["id"]}])
    assert bridge.store.one("SELECT COUNT(*) AS n FROM task_roster_pins")["n"] == 0


# -- what was actually sent --------------------------------------------------------------


def test_the_complete_prompt_is_kept_per_execution_and_a_preview_is_not(bridge):
    text = "POLICY\n\n" + json.dumps({"context_package": {"x": 1}, "instruction": "do it"})
    segments = [{"name": "operator_policy", "layer": "fixed", "sha256": "a" * 64}]
    with bridge.store.transaction() as conn:
        bridge.record_execution_prompt(conn, "demo", "EXEC-1", "codex", "adapter_stdin", text,
                                       segments)
    stored = bridge.execution_prompt("demo", "EXEC-1")
    assert stored["prompt_text"] == text and stored["redacted"] == 0
    assert stored["prompt_sha256"] == digest(text.encode("utf-8"))
    assert stored["segments"] == segments and stored["via"] == "adapter_stdin"
    assert bridge.execution_prompt("demo", "EXEC-404") is None  # a preview has no execution id
    with pytest.raises(sqlite3.IntegrityError):
        with bridge.store.transaction() as conn:
            bridge.record_execution_prompt(conn, "demo", "EXEC-1", "codex", "adapter_stdin", "y", [])
    with pytest.raises(BridgeError):
        with bridge.store.transaction() as conn:
            bridge.record_execution_prompt(conn, "demo", "EXEC-2", "codex", "preview", "y", [])


def test_a_credential_in_a_prompt_is_redacted_in_the_copy_but_hashed_as_sent(bridge):
    sent = "context says api_key = abcdef123456 here"
    with bridge.store.transaction() as conn:
        bridge.record_execution_prompt(conn, "demo", "EXEC-1", "claude", "adapter_stdin", sent, [])
    stored = bridge.execution_prompt("demo", "EXEC-1")
    assert "abcdef123456" not in stored["prompt_text"] and stored["redacted"] == 1
    assert stored["prompt_sha256"] == digest(sent.encode("utf-8"))


# -- cleanup paths ---------------------------------------------------------------------------


def seed_everything(bridge):
    role = make_role(bridge)
    snapshot_id = bridge.snapshot_custom_role("demo", role["id"])["snapshot_id"]
    pin(bridge, [{"role": "REVIEW", "role_ref": role["id"]}])
    with bridge.store.transaction() as conn:
        bridge.record_execution_prompt(conn, "demo", "EXEC-1", "codex", "adapter_stdin", "p", [])
    return role, snapshot_id


def count(bridge, table):
    return bridge.store.one(f"SELECT COUNT(*) AS n FROM {table}")["n"]


def test_clearing_task_history_removes_pins_and_prompts_but_keeps_the_roles(bridge, human):
    role, _ = seed_everything(bridge)
    bridge.clear_project_history_local("demo")
    assert count(bridge, "task_roster_pins") == 0 and count(bridge, "execution_prompts") == 0
    assert count(bridge, "custom_roles") == 1 and count(bridge, "role_snapshots") == 1
    assert bridge.get_custom_role("demo", role["id"])["current_version"] == 1


def test_removing_a_project_removes_its_custom_roles_too(bridge, human):
    seed_everything(bridge)
    bridge.remove_project_local("demo", delete_files=False, keep_records=False)
    for table in (
        "custom_roles", "custom_role_versions", "custom_role_events", "role_snapshots",
        "role_snapshot_trust_events", "task_roster_pins", "execution_prompts", "approval_rosters",
        "contract_drift_log",
    ):
        assert count(bridge, table) == 0, table


def test_a_backup_now_carries_role_history_and_never_the_runnable_trust(bridge):
    """P1 made export fail closed until bundles learned custom roles. They have: the backup
    carries the role history (tests/test_role_bundle.py covers the import and its trust step),
    and still never carries the provider text stored for runs."""
    import json as _json

    seed_everything(bridge)
    payload = _json.loads(bridge.export_project_bundle("demo"))["payload"]
    assert payload["version"] == 2
    for table in ("custom_roles", "custom_role_versions", "role_snapshots", "task_roster_pins"):
        assert payload["tables"][table], table
    assert "execution_prompts" not in payload["tables"]


def test_history_protections_are_back_after_clearing_and_after_removing(bridge, human):
    from ccc_mcp.adapters.storage import ROLE_NO_DELETE_TRIGGERS

    def present():
        return {
            row["name"] for row in bridge.store.all("SELECT name FROM sqlite_master WHERE type='trigger'")
        }

    seed_everything(bridge)
    bridge.clear_project_history_local("demo")
    assert set(ROLE_NO_DELETE_TRIGGERS) <= present()
    with bridge.store.transaction() as conn:
        bridge.record_execution_prompt(conn, "demo", "EXEC-9", "codex", "adapter_stdin", "p", [])
    connection = raw(bridge)
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        connection.execute("DELETE FROM execution_prompts")
    connection.close()
    bridge.remove_project_local("demo", delete_files=False, keep_records=False)
    assert set(ROLE_NO_DELETE_TRIGGERS) <= present()


def test_an_execution_keeps_the_role_identity_it_started_with(bridge):
    role = make_role(bridge)
    snapshot_id = bridge.snapshot_custom_role("demo", role["id"])["snapshot_id"]
    insert_execution(bridge, "EXEC-1", role_ref=role["id"], snapshot=snapshot_id, step_id="001-s00")
    connection = raw(bridge)
    for sql in (
        "UPDATE agent_executions SET step_id='001-s09'",
        "UPDATE agent_executions SET role_ref=NULL",
        "UPDATE agent_executions SET role_snapshot=NULL",
    ):
        with pytest.raises(sqlite3.DatabaseError, match="cannot change"):
            connection.execute(sql)
    connection.execute("UPDATE agent_executions SET status='FAILED'")  # ordinary updates are fine
    connection.close()


def test_a_pending_dispatch_cannot_move_to_another_role_and_keep_the_old_identity(bridge):
    role = make_role(bridge)
    snapshot_id = bridge.snapshot_custom_role("demo", role["id"])["snapshot_id"]
    with bridge.store.transaction() as conn:
        conn.execute(
            "INSERT INTO pending_dispatch(project_id,task_id,source_execution_id,role,"
            "context_version,plan_content,adapter,fake_scenario,committed_json,created,"
            "role_ref,role_snapshot,step_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("demo", "TASK-0001", "EXEC-0", "REVIEW", 1, "", "fake", "success", "{}", now(),
             role["id"], snapshot_id, "001-s00"),
        )
    with pytest.raises(sqlite3.DatabaseError, match="without changing its role identity"):
        with bridge.store.transaction() as conn:
            # the shape of the existing upsert, which does not know about the new columns
            conn.execute("UPDATE pending_dispatch SET role='RD', source_execution_id='EXEC-1'")
    with bridge.store.transaction() as conn:
        conn.execute(
            "UPDATE pending_dispatch SET role='RD', role_ref=NULL, role_snapshot=NULL, "
            "step_id=NULL"
        )
