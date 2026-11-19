"""Custom roles inside a project bundle, and the confirmation an imported role needs.

A bundle is a file that arrives from somewhere else, so everything in it is untrusted data:

* Nothing in it can switch a role on. Whatever status, origin or trust a bundle claims, an
  imported role arrives as ``imported_pending`` and its snapshots as ``imported``; the trust
  records of the device it came from are kept as read-only history in the role's ``imported``
  event and are never copied into the table that decides what may be dispatched.
* Every definition, snapshot, roster and reference is checked again here, with the same
  validators a role created in the console goes through, before anything is written. The hashes
  inside a bundle only show it was not damaged; they prove nothing about who wrote it.
* Switching an imported role on is a separate act of the person at the local console
  (``confirm_imported_role_web``): they read the role, name the exact version, and type a phrase
  that contains it. No MCP tool, CLI command or settings file reaches it.
"""

import json

from ..adapters.storage import AGENT_EXECUTION_IDENTITY_TRIGGER, now
from ..infra.errors import BridgeError, PermissionDenied, VersionConflict
from ..infra.security import canonical, digest, reject_secrets
from .configuration import LANGUAGES, ConfigurationHub
from .custom_roles import MAX_CUSTOM_ROLES, ROLE_REF, roster_hash
from .flows import GATE_ROLES, PRODUCER_ROLES
from .roles import AGENT_ROLES, READ_ONLY_ROLES, writes_files

# Parent rows first. Every one of these carries a project_id; none has a foreign key.
ROLE_TABLES = (
    "custom_roles",
    "custom_role_versions",
    "custom_role_events",
    "role_snapshots",
    "role_snapshot_trust_events",
    "task_roster_pins",
    "approval_rosters",
    "contract_drift_log",
)
# Project-scoped tables a bundle deliberately leaves out, with the reason. The exporter counts
# what it skipped so the bundle can say so instead of silently shrinking.
OMITTED_TABLES = {
    # The complete text handed to a provider for each run: it includes the project's Context and
    # task conversation, so it is neither portable nor something to hand to another person.
    "execution_prompts": "the full text sent to a provider for each run",
}
EVENT_ACTIONS = (
    "created", "cloned", "edited", "disabled", "enabled", "imported", "activated",
    "snapshot_trusted", "snapshot_revoked",
)
DRIFT_DECISIONS = ("compatible", "blocked", "accepted")
TEXT_LIMIT = 100_000
SOURCE_TRUST_LIMIT = 50

# column -> kind: s text, n text or null, i integer. "seq" is allowed but never imported.
SHAPES = {
    "custom_roles": {
        "project_id": "s", "id": "s", "base_role": "s", "status": "s", "current_version": "i",
        "origin": "s", "cloned_from": "n", "created": "s", "updated": "s",
    },
    "custom_role_versions": {
        "project_id": "s", "role_ref": "s", "version": "i", "definition": "s", "sha256": "s",
        "actor": "s", "created": "s",
    },
    "custom_role_events": {
        "project_id": "s", "role_ref": "s", "action": "s", "version": "ni", "actor": "s",
        "detail": "s", "created": "s",
    },
    "role_snapshots": {
        "project_id": "s", "snapshot_id": "s", "role_ref": "s", "version": "i", "base_role": "s",
        "content": "s", "contract_hash": "s", "origin": "s", "created": "s",
    },
    "role_snapshot_trust_events": {
        "project_id": "s", "snapshot_id": "s", "action": "s", "actor": "s", "note": "s",
        "created": "s",
    },
    "task_roster_pins": {
        "id": "s", "project_id": "s", "task_id": "s", "seq": "i", "flow_id": "s", "roster": "s",
        "roster_hash": "s", "created": "s",
    },
    "approval_rosters": {
        "project_id": "s", "approval_id": "s", "pin_id": "s", "roster_hash": "s", "created": "s",
    },
    "contract_drift_log": {
        "project_id": "s", "task_id": "s", "step_id": "n", "snapshot_id": "s",
        "pinned_contract_hash": "s", "current_contract_hash": "s", "decision": "s",
        "changes": "s", "actor": "n", "created": "s",
    },
}
# Tables whose rows may name a custom role or a snapshot (the columns added in P1).
REFERENCING_TABLES = {
    "agent_executions": ("role_ref", "role_snapshot"),
    "evaluations": ("role_ref", "role_snapshot"),
    "reputation_events": ("role_ref", "role_snapshot"),
    "flow_gate_results": ("role_ref", None),
    "flow_escalations": ("role_ref", None),
}
SNAPSHOT_RUNTIME_KEYS = {"provider", "model", "effort", "quota_fallback", "inherited"}
SNAPSHOT_KEYS = {
    "schema", "role_ref", "version", "version_sha256", "base_role", "definition", "runtime",
    "contract",
}


def _invalid(table, why):
    return BridgeError(f"Project bundle has an invalid {table} record: {why}")


def _checked(table, row, project_id):
    shape = SHAPES[table]
    # An autoincrement `seq` may be present on the history tables; it is never imported.
    if not isinstance(row, dict) or set(row) - ({"seq"} - set(shape)) != set(shape):
        raise _invalid(table, "unexpected columns")
    for column, kind in shape.items():
        value = row[column]
        if kind == "s" and not isinstance(value, str):
            raise _invalid(table, f"{column} must be text")
        if kind == "n" and value is not None and not isinstance(value, str):
            raise _invalid(table, f"{column} must be text or empty")
        if kind == "i" and (not isinstance(value, int) or isinstance(value, bool)):
            raise _invalid(table, f"{column} must be a whole number")
        if kind == "ni" and value is not None and (
            not isinstance(value, int) or isinstance(value, bool)
        ):
            raise _invalid(table, f"{column} must be a whole number or empty")
        if isinstance(value, str) and len(value) > TEXT_LIMIT:
            raise _invalid(table, f"{column} is too long")
    if row["project_id"] != project_id:
        raise BridgeError(f"Foreign project record in {table}")
    return row


def _json(table, text, kind):
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise _invalid(table, "not valid JSON") from exc
    if not isinstance(value, kind):
        raise _invalid(table, "unexpected JSON shape")
    return value


# -- export ---------------------------------------------------------------------------------


def export_role_tables(conn, project_id):
    """{table: rows} for every role table this device has, and {table: count} of what was
    deliberately left out. Raises if a role holds something that looks like a credential: it was
    checked on every save, so this only fires for a database changed by hand."""
    present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    tables = {}
    for table in ROLE_TABLES:
        if table not in present:
            tables[table] = []
            continue
        tables[table] = [
            dict(row)
            for row in conn.execute(
                f'SELECT * FROM "{table}" WHERE project_id=? ORDER BY rowid', (project_id,)
            )
        ]
    for row in tables["custom_role_versions"]:
        try:
            reject_secrets(row["definition"])
        except BridgeError as exc:
            raise BridgeError(
                f"Custom role {row['role_ref']} version {row['version']} looks like it contains "
                "a credential; remove it before exporting"
            ) from exc
    omitted = {}
    for table in OMITTED_TABLES:
        if table in present:
            count = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE project_id=?', (project_id,)
            ).fetchone()[0]
            if count:
                omitted[table] = count
    return tables, omitted


# -- validation of an incoming bundle ----------------------------------------------------------


class ImportedRoles:
    """What validation learned, for the insert step to use."""

    def __init__(self):
        self.roles = {}  # role id -> row
        self.versions = {}  # (role id, version) -> {"row", "definition"}
        self.snapshots = {}  # snapshot id -> {"row", "content"}
        self.pins = {}  # pin id -> row
        self.trust_by_snapshot = {}  # snapshot id -> [summary of the source device's records]


def validate_role_records(hub, project_id, version, tables):
    """Check every role record of an incoming bundle against the rest of it. Raises
    ``BridgeError`` on the first problem; nothing has been written yet."""
    present = any(tables[table] for table in ROLE_TABLES)
    parsed = ImportedRoles()
    if not present:
        _check_workflow_references(tables, set(), {})
        return parsed
    if version < 2:
        raise BridgeError("A version 1 project bundle cannot carry custom role records")
    rows = {
        table: [_checked(table, row, project_id) for row in tables[table]] for table in ROLE_TABLES
    }

    # -- the roles
    if len(rows["custom_roles"]) > MAX_CUSTOM_ROLES:
        raise BridgeError(f"Project bundle has more than {MAX_CUSTOM_ROLES} custom roles")
    for row in rows["custom_roles"]:
        role_id = row["id"]
        if not ROLE_REF.fullmatch(role_id):
            raise _invalid("custom_roles", "role id has an unexpected form")
        if role_id in parsed.roles:
            raise BridgeError(f"Project bundle repeats the custom role id {role_id}")
        if row["base_role"] not in AGENT_ROLES:
            raise _invalid("custom_roles", f"{role_id} has an unknown base role")
        if row["current_version"] < 1:
            raise _invalid("custom_roles", f"{role_id} has no version")
        if row["cloned_from"] is not None and not ROLE_REF.fullmatch(row["cloned_from"]):
            raise _invalid("custom_roles", "cloned_from has an unexpected form")
        parsed.roles[role_id] = row

    # -- their versions: re-validated exactly as a save in the console is
    for row in rows["custom_role_versions"]:
        role = parsed.roles.get(row["role_ref"])
        key = (row["role_ref"], row["version"])
        if role is None:
            raise _invalid("custom_role_versions", "version of a role the bundle does not carry")
        if key in parsed.versions:
            raise BridgeError(f"Project bundle repeats version {key[1]} of {key[0]}")
        definition = _json("custom_role_versions", row["definition"], dict)
        checked = hub._validated_definition(role["base_role"], definition)
        if canonical(checked) != canonical(definition):
            raise _invalid("custom_role_versions", f"{key[0]} v{key[1]} is not a valid definition")
        expected = digest(
            canonical(
                {"role_ref": key[0], "base_role": role["base_role"], "definition": checked}
            ).encode("utf-8")
        )
        if expected != row["sha256"]:
            raise BridgeError(f"Role version hash mismatch for {key[0]} v{key[1]}")
        parsed.versions[key] = {"row": row, "definition": checked}
    for role_id, role in parsed.roles.items():
        wanted = {(role_id, number) for number in range(1, role["current_version"] + 1)}
        have = {key for key in parsed.versions if key[0] == role_id}
        if have != wanted:
            raise BridgeError(f"Project bundle is missing versions of {role_id}")

    # -- the frozen snapshots
    for row in rows["role_snapshots"]:
        snapshot_id = row["snapshot_id"]
        role = parsed.roles.get(row["role_ref"])
        if role is None or role["base_role"] != row["base_role"]:
            raise _invalid("role_snapshots", "snapshot of a role the bundle does not carry")
        if snapshot_id in parsed.snapshots:
            raise BridgeError("Project bundle repeats a role snapshot")
        content = _json("role_snapshots", row["content"], dict)
        if set(content) != SNAPSHOT_KEYS or content["schema"] != 1:
            raise _invalid("role_snapshots", "unexpected snapshot content")
        if digest(canonical(content).encode("utf-8")) != snapshot_id:
            raise BridgeError("Role snapshot hash mismatch")
        version_row = parsed.versions.get((row["role_ref"], row["version"]))
        if (
            version_row is None
            or content["role_ref"] != row["role_ref"]
            or content["version"] != row["version"]
            or content["base_role"] != row["base_role"]
            or content["version_sha256"] != version_row["row"]["sha256"]
            or canonical(content["definition"]) != canonical(version_row["definition"])
        ):
            raise BridgeError("Role snapshot does not match its role version")
        _check_snapshot_runtime(row["base_role"], content["runtime"])
        contract = content["contract"]
        components = contract.get("components") if isinstance(contract, dict) else None
        if (
            not isinstance(components, dict)
            or contract.get("contract_hash") != row["contract_hash"]
            or contract.get("base_role") != row["base_role"]
            or contract.get("language") not in LANGUAGES
            or digest(canonical(components).encode("utf-8")) != row["contract_hash"]
        ):
            raise BridgeError("Role snapshot contract hash mismatch")
        parsed.snapshots[snapshot_id] = {"row": row, "content": content}

    # -- the history. Trust records of the source device are summarised, never replayed.
    for row in rows["custom_role_events"]:
        if row["role_ref"] not in parsed.roles or row["action"] not in EVENT_ACTIONS:
            raise _invalid("custom_role_events", "unknown role or action")
        _json("custom_role_events", row["detail"], dict)
    for row in rows["role_snapshot_trust_events"]:
        if row["snapshot_id"] not in parsed.snapshots or row["action"] not in ("trust", "revoke"):
            raise _invalid("role_snapshot_trust_events", "unknown snapshot or action")
        parsed.trust_by_snapshot.setdefault(row["snapshot_id"], []).append(
            {
                "action": row["action"], "actor": row["actor"][:100],
                "note": row["note"][:500], "created": row["created"][:40],
            }
        )

    # -- tasks pinned to roles
    task_ids = {task["id"] for task in tables["tasks"] if isinstance(task, dict)}
    approval_ids = {a["id"] for a in tables["approvals"] if isinstance(a, dict)}
    seen_sequence = set()
    for row in rows["task_roster_pins"]:
        if row["task_id"] not in task_ids:
            raise _invalid("task_roster_pins", "pin of a task the bundle does not carry")
        if row["id"] in parsed.pins or (row["task_id"], row["seq"]) in seen_sequence:
            raise BridgeError("Project bundle repeats a roster pin")
        seen_sequence.add((row["task_id"], row["seq"]))
        roster = _json("task_roster_pins", row["roster"], list)
        for entry in roster:
            _check_roster_entry(parsed, entry)
        recomputed = roster_hash(
            [
                {key: entry[key] for key in ("step_id", "role", "role_ref", "snapshot_id")}
                for entry in roster
            ]
        )
        if recomputed != row["roster_hash"]:
            raise BridgeError("Roster pin hash mismatch")
        parsed.pins[row["id"]] = row
    for row in rows["approval_rosters"]:
        pin = parsed.pins.get(row["pin_id"])
        if row["approval_id"] not in approval_ids or pin is None:
            raise _invalid("approval_rosters", "binding to a pin or approval that is not carried")
        if pin["roster_hash"] != row["roster_hash"]:
            raise BridgeError("Approval roster hash mismatch")
    for row in rows["contract_drift_log"]:
        if (
            row["task_id"] not in task_ids
            or row["snapshot_id"] not in parsed.snapshots
            or row["decision"] not in DRIFT_DECISIONS
        ):
            raise _invalid("contract_drift_log", "unknown task, snapshot or decision")
        _json("contract_drift_log", row["changes"], (dict, list))

    # -- every other table that names a role
    _check_references(parsed, tables)
    _check_workflow_references(tables, set(parsed.roles), parsed.roles)
    return parsed


def _check_snapshot_runtime(base_role, runtime):
    if not isinstance(runtime, dict) or set(runtime) != SNAPSHOT_RUNTIME_KEYS:
        raise BridgeError("Role snapshot has an unexpected runtime")
    if not isinstance(runtime["inherited"], bool):
        raise BridgeError("Role snapshot has an unexpected runtime")
    ConfigurationHub._validated_role(
        base_role, runtime["provider"], runtime["model"], runtime["effort"],
        runtime["quota_fallback"],
    )


def _check_roster_entry(parsed, entry):
    keys = {"step_id", "index", "role", "role_ref", "snapshot_id", "role_version"}
    if not isinstance(entry, dict) or set(entry) != keys:
        raise _invalid("task_roster_pins", "unexpected roster entry")
    if entry["role"] not in AGENT_ROLES or not isinstance(entry["step_id"], str):
        raise _invalid("task_roster_pins", "unknown role in a roster")
    for key in ("index", "role_version"):
        if entry[key] is not None and (
            not isinstance(entry[key], int) or isinstance(entry[key], bool)
        ):
            raise _invalid("task_roster_pins", f"{key} must be a whole number")
    role_ref, snapshot_id = entry["role_ref"], entry["snapshot_id"]
    if role_ref is None:
        if snapshot_id is not None:
            raise _invalid("task_roster_pins", "a built-in step cannot carry a snapshot")
        return
    role = parsed.roles.get(role_ref)
    snapshot = parsed.snapshots.get(snapshot_id)
    if role is None or role["base_role"] != entry["role"]:
        raise BridgeError("A roster names a custom role the bundle does not carry")
    if snapshot is None or snapshot["row"]["role_ref"] != role_ref:
        raise BridgeError("A roster names a snapshot the bundle does not carry")


def _check_references(parsed, tables):
    """A history row may name a role or a snapshot only if the bundle carries it, and a
    snapshot only together with the role it belongs to."""
    for table, (role_column, snapshot_column) in REFERENCING_TABLES.items():
        for row in tables[table]:
            if not isinstance(row, dict):
                continue
            role_ref = row.get(role_column)
            snapshot_id = row.get(snapshot_column) if snapshot_column else None
            if role_ref is not None and role_ref not in parsed.roles:
                raise BridgeError(f"{table} names a custom role the bundle does not carry")
            if snapshot_id is not None:
                snapshot = parsed.snapshots.get(snapshot_id)
                if snapshot is None or snapshot["row"]["role_ref"] != role_ref:
                    raise BridgeError(f"{table} names a snapshot the bundle does not carry")
    for row in tables["meeting_clarifications"]:
        if not isinstance(row, dict):
            continue
        for column in ("asker_role_ref", "responder_role_ref"):
            if row.get(column) is not None and row[column] not in parsed.roles:
                raise BridgeError("meeting_clarifications names a custom role the bundle lacks")


def _check_workflow_references(tables, role_ids, roles):
    """A saved workflow may name only roles the bundle carries, with the base role it says."""
    for row in tables["project_settings"]:
        try:
            settings = json.loads(row["content"])
            flows = settings.get("workflows") or {}
        except (KeyError, TypeError, ValueError, AttributeError):
            continue  # a malformed settings row is rejected by the importer itself
        if not isinstance(flows, dict):
            continue
        for flow in flows.values():
            steps = flow.get("steps") if isinstance(flow, dict) else None
            for step in steps if isinstance(steps, list) else []:
                role_ref = step.get("role_ref") if isinstance(step, dict) else None
                if role_ref is None:
                    continue
                if role_ref not in role_ids:
                    raise BridgeError(
                        f"A workflow names the custom role {role_ref!r}, which the bundle "
                        "does not carry"
                    )
                if step.get("role") != roles[role_ref]["base_role"]:
                    raise BridgeError(f"A workflow step does not match the base role of {role_ref}")


# -- writing ------------------------------------------------------------------------------------


def insert_role_records(conn, project_id, tables, parsed, provenance):
    """Write the validated records, with the trust the importing device gives them: none."""
    for role_id, role in parsed.roles.items():
        conn.execute(
            "INSERT INTO custom_roles(project_id,id,base_role,status,current_version,origin,"
            "cloned_from,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                project_id, role_id, role["base_role"], "imported_pending", role["current_version"],
                "imported", role["cloned_from"], role["created"], role["updated"],
            ),
        )
    for (role_id, number), item in sorted(parsed.versions.items()):
        row = item["row"]
        conn.execute(
            "INSERT INTO custom_role_versions(project_id,role_ref,version,definition,sha256,"
            "actor,created) VALUES(?,?,?,?,?,?,?)",
            (
                project_id, role_id, number, canonical(item["definition"]), row["sha256"],
                row["actor"], row["created"],
            ),
        )
    for row in tables["custom_role_events"]:
        conn.execute(
            "INSERT INTO custom_role_events(project_id,role_ref,action,version,actor,detail,"
            "created) VALUES(?,?,?,?,?,?,?)",
            (
                project_id, row["role_ref"], row["action"], row["version"], row["actor"],
                row["detail"], row["created"],
            ),
        )
    for snapshot_id, item in parsed.snapshots.items():
        row = item["row"]
        conn.execute(
            "INSERT INTO role_snapshots(project_id,snapshot_id,role_ref,version,base_role,"
            "content,contract_hash,origin,created) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                project_id, snapshot_id, row["role_ref"], row["version"], row["base_role"],
                canonical(item["content"]), row["contract_hash"], "imported", row["created"],
            ),
        )
    for row in tables["task_roster_pins"]:
        conn.execute(
            "INSERT INTO task_roster_pins(id,project_id,task_id,seq,flow_id,roster,roster_hash,"
            "created) VALUES(?,?,?,?,?,?,?,?)",
            (
                row["id"], project_id, row["task_id"], row["seq"], row["flow_id"], row["roster"],
                row["roster_hash"], row["created"],
            ),
        )
    for row in tables["approval_rosters"]:
        conn.execute(
            "INSERT INTO approval_rosters(project_id,approval_id,pin_id,roster_hash,created) "
            "VALUES(?,?,?,?,?)",
            (project_id, row["approval_id"], row["pin_id"], row["roster_hash"], row["created"]),
        )
    for row in tables["contract_drift_log"]:
        conn.execute(
            "INSERT INTO contract_drift_log(project_id,task_id,step_id,snapshot_id,"
            "pinned_contract_hash,current_contract_hash,decision,changes,actor,created) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                project_id, row["task_id"], row["step_id"], row["snapshot_id"],
                row["pinned_contract_hash"], row["current_contract_hash"], row["decision"],
                row["changes"], row["actor"], row["created"],
            ),
        )
    for role_id, role in parsed.roles.items():
        own = [
            snapshot_id
            for snapshot_id, item in parsed.snapshots.items()
            if item["row"]["role_ref"] == role_id
        ]
        trust = {
            snapshot_id: parsed.trust_by_snapshot[snapshot_id][:SOURCE_TRUST_LIMIT]
            for snapshot_id in own
            if snapshot_id in parsed.trust_by_snapshot
        }
        conn.execute(
            "INSERT INTO custom_role_events(project_id,role_ref,action,version,actor,detail,"
            "created) VALUES(?,?,?,?,?,?,?)",
            (
                project_id, role_id, "imported", role["current_version"], "import",
                canonical(
                    {
                        "bundle_sha256": provenance["bundle_sha256"],
                        "exported_at": provenance["exported_at"],
                        "source_project_id": provenance["source_project_id"],
                        "source_status": role["status"],
                        "source_origin": role["origin"],
                        "snapshots": own,
                        "source_trust": trust,
                        "note": "Imported roles start unconfirmed on this device.",
                    }
                ),
                now(),
            ),
        )


def history_names_snapshots(tables):
    """Finished runs that name a snapshot are history, not a dispatch, and the snapshots they
    name arrive untrusted. Only then does the import pause the dispatch-time trigger."""
    return any(row.get("role_snapshot") for row in tables["agent_executions"])


def restore_identity_check(conn):
    conn.execute(AGENT_EXECUTION_IDENTITY_TRIGGER)
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='agent_executions_role_identity'"
    ).fetchone():
        raise BridgeError("Import could not restore the role identity check")


# -- confirming an imported role (the local console only) ------------------------------------------


class RoleBundleHub:
    """Reviewing and confirming imported roles. Nothing here is reachable from MCP or the CLI."""

    @staticmethod
    def import_confirmation(role_ref, version, version_sha256):
        """The exact words a person types to switch on one imported role at one version."""
        return f"ACTIVATE {role_ref} v{version} {version_sha256[:8]}"

    def pending_imported_roles(self, project_id):
        return [
            row["id"]
            for row in self.store.all(
                "SELECT id FROM custom_roles WHERE project_id=? AND status='imported_pending' "
                "ORDER BY id", (project_id,),
            )
        ]

    def imported_role_review(self, project_id, role_ref):
        """Everything the console shows before a person decides: where the role came from, its
        instructions and versions, what its base role lets it do, which frozen snapshots exist,
        and the phrase that confirms it. Read only."""
        role = self.get_custom_role(project_id, role_ref)
        if role["status"] != "imported_pending":
            raise BridgeError("This role is not waiting for confirmation")
        events = self.custom_role_events(project_id, role_ref)
        imported = [event for event in events if event["action"] == "imported"]
        source = imported[-1]["detail"] if imported else {}
        snapshots = []
        for row in self.store.all(
            "SELECT snapshot_id FROM role_snapshots WHERE project_id=? AND role_ref=? "
            "ORDER BY version, snapshot_id", (project_id, role_ref),
        ):
            snapshot = self.role_snapshot(project_id, row["snapshot_id"])
            content = snapshot["content"]
            snapshots.append(
                {
                    "snapshot_id": snapshot["snapshot_id"], "version": snapshot["version"],
                    "origin": snapshot["origin"], "trusted": snapshot["trusted"],
                    "contract_hash": snapshot["contract_hash"], "created": snapshot["created"],
                    "runtime": content["runtime"],
                    "is_current_version": snapshot["version"] == role["current_version"],
                    # Snapshots of the current version are confirmed together with the role;
                    # older ones only if the person ticks them.
                    "confirmed_with_role": snapshot["version"] == role["current_version"],
                    "source_trust": (source.get("source_trust") or {}).get(
                        snapshot["snapshot_id"], []
                    ),
                }
            )
        base = role["base_role"]
        return {
            "role": role,
            "source": {
                "bundle_sha256": source.get("bundle_sha256"),
                "exported_at": source.get("exported_at"),
                "source_project_id": source.get("source_project_id"),
                "source_status": source.get("source_status"),
                "imported_at": imported[-1]["created"] if imported else None,
            },
            "versions": self.custom_role_versions(project_id, role_ref),
            "permissions": {
                "base_role": base,
                "may_write_files": bool(writes_files(base)),
                "read_only": base in READ_ONLY_ROLES,
                "is_gate": base in GATE_ROLES,
                "is_producer": base in PRODUCER_ROLES,
                "note": (
                    "What a role may do comes from its base role in ccc-mcp's own code. "
                    "Nothing written in the role or its bundle can widen it."
                ),
            },
            "runtime": role["definition"]["runtime"],
            "usage": self.custom_role_usage(project_id, role_ref),
            "snapshots": snapshots,
            "confirmation": {
                "version": role["current_version"],
                "version_sha256": role["version_sha256"],
                "phrase": self.import_confirmation(
                    role_ref, role["current_version"], role["version_sha256"]
                ),
            },
        }

    def confirm_imported_role_web(
        self, project_id, role_ref, version, version_sha256, snapshot_ids, typed_confirmation,
        note="",
    ):
        """Switch on an imported role: the local console's own act. Beyond holding the console's
        session token, the person must type a phrase naming this role, version and fingerprint,
        and the version they read must still be the current one. The record names the console as
        the actor. The frozen snapshots of that version are confirmed with it, and older ones only
        when named in `snapshot_ids`, one by one; the snapshots themselves are never modified. Nothing reachable from MCP, from the CLI, or from a
        project's write authorization calls this."""
        if not isinstance(version_sha256, str) or not isinstance(version, int):
            raise BridgeError("Name the role version you reviewed")
        if typed_confirmation != self.import_confirmation(role_ref, version, version_sha256):
            raise PermissionDenied("The confirmation text does not match this role")
        if not isinstance(snapshot_ids, list) or not all(
            isinstance(item, str) for item in snapshot_ids
        ):
            raise BridgeError("snapshot_ids must be a list")
        if not isinstance(note, str) or len(note) > 1000:
            raise BridgeError("The note is too long")
        with self.lock(project_id), self.store.transaction() as conn:
            role = self._role_row(project_id, role_ref, conn)
            if role["status"] != "imported_pending" or role["origin"] != "imported":
                raise BridgeError("This role is not waiting for confirmation")
            current = self._version_row(project_id, role_ref, role["current_version"], conn)
            if role["current_version"] != version or current["sha256"] != version_sha256:
                raise VersionConflict("The role changed since it was reviewed; review it again")
            expected = digest(
                canonical(
                    {
                        "role_ref": role_ref, "base_role": role["base_role"],
                        "definition": current["definition"],
                    }
                ).encode("utf-8")
            )
            if expected != current["sha256"]:
                raise BridgeError("The stored role does not match its fingerprint; not confirmed")
            # Snapshots of the version being confirmed are part of what was reviewed and are
            # confirmed with it: the same content on this device would otherwise stay blocked
            # even though the person just approved it. Snapshots of older versions (kept by tasks
            # that were pinned to them) are confirmed only when they are named.
            current_snapshots = [
                row["snapshot_id"]
                for row in conn.execute(
                    "SELECT snapshot_id FROM role_snapshots WHERE project_id=? AND role_ref=? "
                    "AND version=? AND origin='imported' ORDER BY snapshot_id",
                    (project_id, role_ref, version),
                )
            ]
            chosen = list(dict.fromkeys([*current_snapshots, *snapshot_ids]))
            for snapshot_id in chosen:
                row = conn.execute(
                    "SELECT * FROM role_snapshots WHERE project_id=? AND snapshot_id=?",
                    (project_id, snapshot_id),
                ).fetchone()
                if not row or row["role_ref"] != role_ref or row["origin"] != "imported":
                    raise BridgeError("That snapshot is not an imported snapshot of this role")
                if digest(row["content"].encode("utf-8")) != snapshot_id:
                    raise BridgeError("A stored snapshot does not match its hash; not confirmed")
            for snapshot_id in chosen:
                conn.execute(
                    "INSERT INTO role_snapshot_trust_events(project_id,snapshot_id,action,actor,"
                    "note,created) VALUES(?,?,?,?,?,?)",
                    (project_id, snapshot_id, "trust", "local-console", note, now()),
                )
                snapshot_version = conn.execute(
                    "SELECT version FROM role_snapshots WHERE project_id=? AND snapshot_id=?",
                    (project_id, snapshot_id),
                ).fetchone()[0]
                self._append_role_event(
                    conn, project_id, role_ref, "snapshot_trusted", snapshot_version,
                    "local-console", {"snapshot_id": snapshot_id},
                )
            conn.execute(
                "UPDATE custom_roles SET status='active', updated=? WHERE project_id=? AND id=?",
                (now(), project_id, role_ref),
            )
            self._append_role_event(
                conn, project_id, role_ref, "activated", version, "local-console",
                {"version_sha256": version_sha256, "snapshots": chosen, "note": note},
            )
        return self.get_custom_role(project_id, role_ref)

    # -- snapshots that came with an already confirmed role

    @staticmethod
    def imported_snapshot_confirmation(role_ref, version, snapshot_id):
        """The exact words a person types to confirm one imported snapshot of a role they have
        already switched on."""
        return f"TRUST {role_ref} v{version} {snapshot_id[:8]}"

    def unconfirmed_imported_snapshots(self, project_id, role_ref):
        """Imported snapshots of a role that is on but that no one has confirmed yet: for
        example the one an imported, paused task was pinned to. Empty for a local role."""
        role = self._role_row(project_id, role_ref)
        if role["origin"] != "imported" or role["status"] == "imported_pending":
            return []
        out = []
        for row in self.store.all(
            "SELECT snapshot_id FROM role_snapshots WHERE project_id=? AND role_ref=? "
            "AND origin='imported' ORDER BY version, snapshot_id", (project_id, role_ref),
        ):
            snapshot = self.role_snapshot(project_id, row["snapshot_id"])
            if not snapshot["trusted"]:
                out.append(
                    {
                        "snapshot_id": snapshot["snapshot_id"], "version": snapshot["version"],
                        "runtime": snapshot["content"]["runtime"],
                        "contract_hash": snapshot["contract_hash"],
                        "phrase": self.imported_snapshot_confirmation(
                            role_ref, snapshot["version"], snapshot["snapshot_id"]
                        ),
                    }
                )
        return out

    def confirm_imported_snapshot_web(
        self, project_id, role_ref, snapshot_id, typed_confirmation, note=""
    ):
        """Confirm one more imported snapshot of a role that is already on, so a paused task
        pinned to it can continue. The console's own act, like `confirm_imported_role_web`."""
        if not isinstance(snapshot_id, str) or not isinstance(note, str) or len(note) > 1000:
            raise BridgeError("Name the snapshot you reviewed")
        with self.lock(project_id), self.store.transaction() as conn:
            role = self._role_row(project_id, role_ref, conn)
            if role["origin"] != "imported":
                raise BridgeError("This role was not imported")
            if role["status"] == "imported_pending":
                raise PermissionDenied("Confirm the role itself before its snapshots")
            row = conn.execute(
                "SELECT * FROM role_snapshots WHERE project_id=? AND snapshot_id=?",
                (project_id, snapshot_id),
            ).fetchone()
            if not row or row["role_ref"] != role_ref or row["origin"] != "imported":
                raise BridgeError("That snapshot is not an imported snapshot of this role")
            if typed_confirmation != self.imported_snapshot_confirmation(
                role_ref, row["version"], snapshot_id
            ):
                raise PermissionDenied("The confirmation text does not match this snapshot")
            if digest(row["content"].encode("utf-8")) != snapshot_id:
                raise BridgeError("A stored snapshot does not match its hash; not confirmed")
            trusted = conn.execute(
                "SELECT trusted FROM role_snapshot_status WHERE project_id=? AND snapshot_id=?",
                (project_id, snapshot_id),
            ).fetchone()[0]
            if trusted:
                raise BridgeError("That snapshot is already confirmed")
            conn.execute(
                "INSERT INTO role_snapshot_trust_events(project_id,snapshot_id,action,actor,"
                "note,created) VALUES(?,?,?,?,?,?)",
                (project_id, snapshot_id, "trust", "local-console", note, now()),
            )
            self._append_role_event(
                conn, project_id, role_ref, "snapshot_trusted", row["version"], "local-console",
                {"snapshot_id": snapshot_id},
            )
        return self.role_snapshot(project_id, snapshot_id)
