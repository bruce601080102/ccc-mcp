"""User-defined roles: their identity, immutable versions, pinned snapshots and trust.

A custom role is a name and a body of instructions layered over one of the built-in roles
(its `base_role`). The base decides everything that is enforced by code -- whether the role may
write, how its report is judged, which sandbox flags it runs under -- so a custom role can never
gain a permission by what its instructions say. What it owns is its identity (`role_ref`), its
instructions, its declared abilities and, optionally, which provider runs it.

Four rules shape the storage and are enforced in the database as well as here:

* A role's history is append-only. Every edit is a new row in `custom_role_versions`; nothing is
  overwritten or deleted.
* A *snapshot* freezes everything that influences an execution (definition, base role, the
  resolved provider settings and the fixed-contract manifest) under a sha256 of that content. A
  snapshot is never modified. Whether a snapshot may be dispatched is a separate, append-only
  trust record, so confirming an imported role never touches the frozen content.
* A role identifies a *definition*. A workflow step is identified by its own `step_id` in a
  per-task roster, and each run of a step by an `execution_id`, so the same role used twice in
  one workflow, or retried, never overwrites itself.
* A task's roster is pinned when the task starts a work round, in the same transaction that pins
  its workflow and before anything runs or any plan can be approved. Editing or disabling a role
  afterwards cannot change what that task uses.
"""

import json
import re
import unicodedata

from ..adapters.storage import now
from ..infra.errors import BridgeError, ContractDriftBlocked, PermissionDenied, VersionConflict
from ..infra.security import canonical, digest, redact, reject_secrets
from .configuration import EFFORTS, PROVIDERS, ConfigurationHub
from .flows import GATE_ROLES, PRODUCER_ROLES
from .prompt_lint import lint_role
from .role_contract import (
    COMPATIBLE,
    IDENTICAL,
    INCOMPATIBLE,
    assess_contract,
    contract_manifest,
)
from .role_prompt import assemble_preview, custom_instruction
from .roles import (
    AGENT_ROLES,
    AVATAR_IDS,
    DEFAULT_AVATARS,
    ROLE_CATALOG,
    ROLE_STATS,
    STAT_AXES,
    STAT_MAX,
    default_provider,
    validated_role_name,
    writes_files,
)

MAX_CUSTOM_ROLES = 30
DESCRIPTION_MAX = 200
SECTION_MAX = 4000
PROMPT_TOTAL_MAX = 16000
SECTION_KEYS = ("goal", "duties", "constraints", "io_format", "collaboration", "failure_handling")
PROMPT_MODES = ("sections", "freeform")
KINDS = ("analyst", "designer", "developer", "reviewer", "writer", "researcher", "specialist")
STATUSES = ("active", "disabled", "imported_pending")
DEFINITION_KEYS = ("name", "description", "avatar", "kind", "prompt", "stats", "runtime")
ROLE_REF = re.compile(r"u-[a-z0-9][a-z0-9-]{0,28}")

_DEFAULT_KIND = {
    "SA": "analyst", "AN": "analyst", "SD": "designer", "PL": "designer",
    "RD": "developer", "SD/RD": "developer",
    "REVIEW": "reviewer", "SR": "reviewer", "AR": "reviewer", "SEC": "reviewer",
    "QA": "reviewer", "VC": "reviewer", "RV": "reviewer",
    "SW": "writer", "WR": "writer", "ED": "writer", "RS": "researcher",
}
# Characters that would let a name or instruction lie about what it says when it is shown.
_FORBIDDEN_NAME_CHARS = frozenset("<>&\"'`\\")


def default_kind(base_role):
    return _DEFAULT_KIND.get(base_role, "specialist")


def _plain_text(value, label, limit, *, allow_newlines, strip=True):
    """Text with no control, format or bidirectional-override characters. Line endings are
    normalized to \\n; with strip=False nothing else about the text is changed."""
    if not isinstance(value, str):
        raise BridgeError(f"{label} must be text")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    for char in value:
        category = unicodedata.category(char)
        if char == "\n" and allow_newlines:
            continue
        if char == "\t" and allow_newlines:
            continue
        if category in {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}:
            raise BridgeError(f"{label} contains unsupported characters")
    if strip:
        value = value.strip()
    if len(value) > limit:
        raise BridgeError(f"{label} must be at most {limit} characters")
    return value


def validated_custom_name(name):
    cleaned = validated_role_name(name)
    if not cleaned:
        raise BridgeError("Role name is required")
    if _FORBIDDEN_NAME_CHARS & set(cleaned):
        raise BridgeError("Role name must not contain < > & \" ' ` or \\")
    if unicodedata.category(cleaned[0]) not in {"Lu", "Ll", "Lt", "Lo", "Lm", "Nd"}:
        raise BridgeError("Role name must start with a letter or digit")
    return cleaned


def validated_prompt(value):
    """The role's own instructions: six guided sections, or one free-form body, and which of the
    two is in use.

    Both bodies are always kept, whichever mode is chosen, so switching modes never discards or
    rewrites what the person wrote. Only the active mode's text is ever sent to an agent.
    Free-form text is stored exactly as given, apart from line endings being normalized."""
    if not isinstance(value, dict):
        raise BridgeError("prompt must be an object")
    unknown = set(value) - {"mode", "sections", "freeform"}
    if unknown:
        raise BridgeError(f"prompt has unknown fields: {sorted(unknown)}")
    mode = value.get("mode", "sections")
    if mode not in PROMPT_MODES:
        raise BridgeError(f"prompt mode must be one of {list(PROMPT_MODES)}")
    given = value.get("sections") or {}
    if not isinstance(given, dict) or set(given) - set(SECTION_KEYS):
        raise BridgeError(f"sections must only use {list(SECTION_KEYS)}")
    sections = {
        key: _plain_text(given.get(key, ""), f"Section {key}", SECTION_MAX, allow_newlines=True)
        for key in SECTION_KEYS
    }
    freeform = _plain_text(
        value.get("freeform", ""), "Free-form instructions", PROMPT_TOTAL_MAX,
        allow_newlines=True, strip=False,
    )
    if len("".join(sections.values())) > PROMPT_TOTAL_MAX:
        raise BridgeError(f"Sections must be at most {PROMPT_TOTAL_MAX} characters in total")
    active = freeform if mode == "freeform" else "".join(sections.values())
    if not active.strip():
        raise BridgeError(
            "A role needs instructions in the mode it uses"
            + (" (switching to free-form needs its text; the sections are kept)"
               if mode == "freeform" else " (the free-form text is kept)")
        )
    return {"mode": mode, "sections": sections, "freeform": freeform}


def validated_stats(value, base_role):
    """Declared abilities, 0 to 5 on every axis. They describe how the role is set up, never
    how well it performs; a role with none declared shows its base role's."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != set(STAT_AXES):
        raise BridgeError(f"stats must give every ability: {list(STAT_AXES)}")
    stats = {}
    for axis in STAT_AXES:
        score = value[axis]
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= STAT_MAX:
            raise BridgeError(f"stats.{axis} must be a whole number from 0 to {STAT_MAX}")
        stats[axis] = score
    return stats


def declared_stats(definition, base_role):
    stats = definition.get("stats")
    return stats if stats else dict(zip(STAT_AXES, ROLE_STATS[base_role], strict=True))


def roster_hash(roster):
    """One hash over a pinned roster, so an approval can say exactly which roles and prompt
    versions it was given for."""
    return digest(canonical(roster).encode("utf-8"))


def make_step_id(pin_seq, index):
    return f"{pin_seq:03d}-s{index:02d}"


class CustomRoleHub:
    # -- definitions -----------------------------------------------------------------

    def _validated_definition(self, base_role, raw):
        if not isinstance(raw, dict):
            raise BridgeError("A role definition must be an object")
        unknown = set(raw) - set(DEFINITION_KEYS)
        if unknown:
            raise BridgeError(f"Unknown role fields: {sorted(unknown)}")
        avatar = raw.get("avatar")
        if avatar in ("", None):
            avatar = None
        elif avatar not in AVATAR_IDS:
            raise BridgeError(f"Unknown avatar; choose one of {list(AVATAR_IDS)}")
        kind = raw.get("kind") or default_kind(base_role)
        if kind not in KINDS:
            raise BridgeError(f"kind must be one of {list(KINDS)}")
        runtime = raw.get("runtime")
        if runtime is not None:
            if not isinstance(runtime, dict) or set(runtime) - {
                "provider", "model", "effort", "quota_fallback"
            }:
                raise BridgeError("runtime may only set provider, model, effort, quota_fallback")
            runtime = ConfigurationHub._validated_role(
                base_role,
                runtime.get("provider"),
                runtime.get("model"),
                runtime.get("effort"),
                runtime.get("quota_fallback"),
            )
        definition = {
            "name": validated_custom_name(raw.get("name")),
            "description": _plain_text(
                raw.get("description") or "", "Description", DESCRIPTION_MAX, allow_newlines=False
            ),
            "avatar": avatar,
            "kind": kind,
            "prompt": validated_prompt(raw.get("prompt") or {}),
            "stats": validated_stats(raw.get("stats"), base_role),
            "runtime": runtime,
        }
        reject_secrets(definition)
        return definition

    @staticmethod
    def _new_role_ref(name, taken):
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:16].strip("-") or "role"
        number = 1
        while True:
            suffix = "" if number == 1 else f"-{number}"
            candidate = f"u-{slug}{suffix}"
            if candidate not in taken:
                return candidate
            number += 1

    @staticmethod
    def _role_ref(role_ref):
        if not isinstance(role_ref, str) or not ROLE_REF.fullmatch(role_ref):
            raise BridgeError("Invalid custom role id")
        return role_ref

    def _role_row(self, project_id, role_ref, conn=None):
        query = "SELECT * FROM custom_roles WHERE project_id=? AND id=?"
        args = (project_id, self._role_ref(role_ref))
        row = conn.execute(query, args).fetchone() if conn else self.store.one(query, args)
        if not row:
            raise BridgeError("Custom role not found")
        return dict(row)

    def _version_row(self, project_id, role_ref, version, conn=None):
        query = (
            "SELECT * FROM custom_role_versions WHERE project_id=? AND role_ref=? AND version=?"
        )
        args = (project_id, role_ref, version)
        row = conn.execute(query, args).fetchone() if conn else self.store.one(query, args)
        if not row:
            raise BridgeError("Custom role version not found")
        row = dict(row)
        row["definition"] = json.loads(row["definition"])
        return row

    @staticmethod
    def _append_role_event(conn, project_id, role_ref, action, version, actor, detail=None):
        conn.execute(
            "INSERT INTO custom_role_events(project_id,role_ref,action,version,actor,detail,"
            "created) VALUES(?,?,?,?,?,?,?)",
            (project_id, role_ref, action, version, actor, canonical(detail or {}), now()),
        )

    @staticmethod
    def _insert_version(conn, project_id, role_ref, base_role, version, definition, actor):
        content_hash = digest(
            canonical({"role_ref": role_ref, "base_role": base_role, "definition": definition})
            .encode("utf-8")
        )
        conn.execute(
            "INSERT INTO custom_role_versions(project_id,role_ref,version,definition,sha256,"
            "actor,created) VALUES(?,?,?,?,?,?,?)",
            (project_id, role_ref, version, canonical(definition), content_hash, actor, now()),
        )
        return content_hash

    def create_custom_role(
        self, project_id, name, base_role, *, description="", avatar=None, kind=None,
        prompt=None, stats=None, runtime=None, actor="console", cloned_from=None,
    ):
        """A new custom role at version 1, based on one of the built-in roles."""
        self.project(project_id)
        if base_role not in AGENT_ROLES:
            raise BridgeError(f"base_role must be one of {list(AGENT_ROLES)}")
        definition = self._validated_definition(
            base_role,
            {
                "name": name, "description": description, "avatar": avatar, "kind": kind,
                "prompt": prompt, "stats": stats, "runtime": runtime,
            },
        )
        with self.lock(project_id), self.store.transaction() as conn:
            taken = {
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM custom_roles WHERE project_id=?", (project_id,)
                )
            }
            if len(taken) >= MAX_CUSTOM_ROLES:
                raise BridgeError(f"A project can have at most {MAX_CUSTOM_ROLES} custom roles")
            role_ref = self._new_role_ref(definition["name"], taken)
            stamp = now()
            conn.execute(
                "INSERT INTO custom_roles(project_id,id,base_role,status,current_version,origin,"
                "cloned_from,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                (project_id, role_ref, base_role, "active", 1, "local", cloned_from, stamp, stamp),
            )
            sha = self._insert_version(conn, project_id, role_ref, base_role, 1, definition, actor)
            self._append_role_event(
                conn, project_id, role_ref, "cloned" if cloned_from else "created", 1, actor,
                {"sha256": sha, **({"cloned_from": cloned_from} if cloned_from else {})},
            )
        return self.get_custom_role(project_id, role_ref)

    def update_custom_role(self, project_id, role_ref, changes, *, expected_version, actor="console"):
        """Save an edit as a new version. `expected_version` must be the version the editor
        started from, so two editors cannot silently overwrite one another. An edit that
        changes nothing creates no version."""
        if not isinstance(changes, dict):
            raise BridgeError("changes must be an object")
        if "base_role" in changes:
            raise BridgeError("A role's base role cannot change; clone the role to use another")
        with self.lock(project_id), self.store.transaction() as conn:
            role = self._role_row(project_id, role_ref, conn)
            if role["status"] != "active":
                raise BridgeError("Only an active role can be edited")
            if role["current_version"] != expected_version:
                raise VersionConflict(
                    f"Role is at version {role['current_version']}, not {expected_version}"
                )
            current = self._version_row(project_id, role_ref, role["current_version"], conn)
            merged = {**current["definition"], **changes}
            if "prompt" in changes:
                merged["prompt"] = self._merged_prompt(current["definition"]["prompt"], changes["prompt"])
            definition = self._validated_definition(role["base_role"], merged)
            if canonical(definition) == canonical(current["definition"]):
                return {**self.get_custom_role(project_id, role_ref), "changed": False}
            version = role["current_version"] + 1
            sha = self._insert_version(
                conn, project_id, role_ref, role["base_role"], version, definition, actor
            )
            conn.execute(
                "UPDATE custom_roles SET current_version=?, updated=? WHERE project_id=? AND id=?",
                (version, now(), project_id, role_ref),
            )
            self._append_role_event(
                conn, project_id, role_ref, "edited", version, actor, {"sha256": sha}
            )
        return {**self.get_custom_role(project_id, role_ref), "changed": True}

    @staticmethod
    def _merged_prompt(current, given):
        """An edit that names only part of the prompt changes only that part. Sections not
        mentioned, and the text of the mode not in use, stay exactly as they were."""
        if not isinstance(given, dict):
            raise BridgeError("prompt must be an object")
        sections = given.get("sections")
        if sections is not None and not isinstance(sections, dict):
            raise BridgeError("sections must be an object")
        return {
            **{key: value for key, value in given.items() if key not in {"sections"}},
            "mode": given.get("mode", current["mode"]),
            "sections": {**current["sections"], **(sections or {})},
            "freeform": given.get("freeform", current["freeform"]),
        }

    def clone_custom_role(self, project_id, role_ref, name=None, *, actor="console"):
        """A new role, at version 1, with this role's current definition."""
        source = self.get_custom_role(project_id, role_ref)
        definition = source["definition"]
        label = name or f"{definition['name']} 2"
        return self.create_custom_role(
            project_id, label, source["base_role"],
            description=definition["description"], avatar=definition["avatar"],
            kind=definition["kind"], prompt=definition["prompt"], stats=definition["stats"],
            runtime=definition["runtime"], actor=actor, cloned_from=role_ref,
        )

    def set_custom_role_status(self, project_id, role_ref, status, *, actor="console"):
        """Disable or re-enable a role. Disabling keeps every version and snapshot; tasks that
        already pinned the role are unaffected."""
        if status not in {"active", "disabled"}:
            raise BridgeError("status must be active or disabled")
        with self.lock(project_id), self.store.transaction() as conn:
            role = self._role_row(project_id, role_ref, conn)
            if role["status"] == "imported_pending":
                raise PermissionDenied("An imported role must be confirmed before it can be used")
            if role["status"] != status:
                conn.execute(
                    "UPDATE custom_roles SET status=?, updated=? WHERE project_id=? AND id=?",
                    (status, now(), project_id, role_ref),
                )
                self._append_role_event(
                    conn, project_id, role_ref, "enabled" if status == "active" else "disabled",
                    role["current_version"], actor,
                )
        return self.get_custom_role(project_id, role_ref)

    def get_custom_role(self, project_id, role_ref, version=None):
        self.project(project_id)
        role = self._role_row(project_id, role_ref)
        row = self._version_row(project_id, role["id"], version or role["current_version"])
        return {
            "id": role["id"], "base_role": role["base_role"], "status": role["status"],
            "origin": role["origin"], "cloned_from": role["cloned_from"],
            "current_version": role["current_version"], "version": row["version"],
            "version_sha256": row["sha256"], "definition": row["definition"],
            "created": role["created"], "updated": role["updated"],
        }

    def list_custom_roles(self, project_id):
        self.project(project_id)
        rows = self.store.all(
            "SELECT id FROM custom_roles WHERE project_id=? ORDER BY created, id", (project_id,)
        )
        return [self.get_custom_role(project_id, row["id"]) for row in rows]

    def custom_role_versions(self, project_id, role_ref):
        self.project(project_id)
        self._role_row(project_id, role_ref)
        rows = self.store.all(
            "SELECT version,sha256,actor,created,definition FROM custom_role_versions "
            "WHERE project_id=? AND role_ref=? ORDER BY version", (project_id, role_ref),
        )
        return [{**row, "definition": json.loads(row["definition"])} for row in rows]

    def custom_role_bases(self, project_id, statuses=("active",)):
        """{role id: base role} for the project's custom roles in the given states. Workflows
        may only be given steps that use active roles; roles that are disabled are still read
        back in workflows that already name them."""
        marks = ",".join("?" for _ in statuses)
        rows = self.store.all(
            f"SELECT id, base_role FROM custom_roles WHERE project_id=? AND status IN ({marks})",
            (project_id, *statuses),
        )
        return {row["id"]: row["base_role"] for row in rows}

    def custom_role_events(self, project_id, role_ref):
        self.project(project_id)
        rows = self.store.all(
            "SELECT seq,action,version,actor,detail,created FROM custom_role_events "
            "WHERE project_id=? AND role_ref=? ORDER BY seq", (project_id, role_ref),
        )
        return [{**row, "detail": json.loads(row["detail"])} for row in rows]

    # -- snapshots and trust ----------------------------------------------------------

    def _resolved_runtime(self, project_id, base_role, definition):
        """The provider settings a snapshot pins. A role that names none inherits the project's
        current setting for its base role, and the snapshot records the values it got."""
        if definition["runtime"]:
            return {**definition["runtime"], "inherited": False}
        configured = self.get_project_settings(project_id)["roles"][base_role]
        return {
            "provider": configured["provider"], "model": configured["model"],
            "effort": configured["effort"], "quota_fallback": configured["quota_fallback"],
            "inherited": True,
        }

    def _snapshot_in(self, conn, project_id, role_ref, version=None):
        role = self._role_row(project_id, role_ref, conn)
        if role["status"] == "imported_pending":
            raise PermissionDenied(
                f"Role {role_ref} was imported and has not been confirmed on this device; "
                "review and confirm it in the console's Role Studio first"
            )
        if role["status"] != "active":
            raise BridgeError(f"Role {role_ref} is {role['status']} and cannot be pinned")
        row = self._version_row(project_id, role_ref, version or role["current_version"], conn)
        language = self.get_project_settings(project_id)["language"]
        manifest = contract_manifest(language, role["base_role"])
        content = {
            "schema": 1,
            "role_ref": role_ref,
            "version": row["version"],
            "version_sha256": row["sha256"],
            "base_role": role["base_role"],
            "definition": row["definition"],
            "runtime": self._resolved_runtime(project_id, role["base_role"], row["definition"]),
            "contract": manifest,
        }
        snapshot_id = digest(canonical(content).encode("utf-8"))
        conn.execute(
            "INSERT OR IGNORE INTO role_snapshots(project_id,snapshot_id,role_ref,version,"
            "base_role,content,contract_hash,origin,created) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                project_id, snapshot_id, role_ref, row["version"], role["base_role"],
                canonical(content), manifest["contract_hash"], "local", now(),
            ),
        )
        return snapshot_id

    def snapshot_custom_role(self, project_id, role_ref, version=None):
        """Freeze a version of an active role. The same content always gives the same id."""
        with self.lock(project_id), self.store.transaction() as conn:
            snapshot_id = self._snapshot_in(conn, project_id, role_ref, version)
        return self.role_snapshot(project_id, snapshot_id)

    def role_snapshot(self, project_id, snapshot_id):
        row = self.store.one(
            "SELECT s.*, v.trusted FROM role_snapshots s JOIN role_snapshot_status v "
            "ON v.project_id=s.project_id AND v.snapshot_id=s.snapshot_id "
            "WHERE s.project_id=? AND s.snapshot_id=?", (project_id, snapshot_id),
        )
        if not row:
            raise BridgeError("Role snapshot not found")
        return {**row, "content": json.loads(row["content"]), "trusted": bool(row["trusted"])}

    def role_snapshot_trusted(self, project_id, snapshot_id):
        return self.role_snapshot(project_id, snapshot_id)["trusted"]

    def require_role_snapshot_trusted(self, project_id, snapshot_id):
        """The same rule the database enforces on every dispatch insert, as a readable error."""
        if not self.role_snapshot_trusted(project_id, snapshot_id):
            raise PermissionDenied(
                "This role snapshot came from an imported file and has not been confirmed on "
                "this device"
            )

    def _record_trust(self, project_id, snapshot_id, action, actor, note):
        if not isinstance(actor, str) or not actor.strip():
            raise BridgeError("An actor is required")
        snapshot = self.role_snapshot(project_id, snapshot_id)
        if action == "trust" and snapshot["trusted"]:
            return snapshot
        with self.lock(project_id), self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO role_snapshot_trust_events(project_id,snapshot_id,action,actor,"
                "note,created) VALUES(?,?,?,?,?,?)",
                (project_id, snapshot_id, action, actor, note or "", now()),
            )
            self._append_role_event(
                conn, project_id, snapshot["role_ref"],
                "snapshot_trusted" if action == "trust" else "snapshot_revoked",
                snapshot["version"], actor, {"snapshot_id": snapshot_id},
            )
        return self.role_snapshot(project_id, snapshot_id)

    def trust_role_snapshot(self, project_id, snapshot_id, actor, note=""):
        """Confirm that this exact frozen content may be dispatched. Appends a record; the
        snapshot itself is untouched."""
        return self._record_trust(project_id, snapshot_id, "trust", actor, note)

    def revoke_role_snapshot_trust(self, project_id, snapshot_id, actor, note=""):
        return self._record_trust(project_id, snapshot_id, "revoke", actor, note)

    # -- running a pinned role ----------------------------------------------------------------

    def snapshot_agent_settings(self, snapshot, agent="auto", model=None, effort=None):
        """The provider settings for one dispatch of a pinned role: exactly what its snapshot
        froze, with a caller's explicit provider, model or effort (a quota fallback names
        another provider) laid over them the same way as for a built-in role."""
        runtime = snapshot["content"]["runtime"]
        configured = {key: runtime[key] for key in ("provider", "model", "effort", "quota_fallback")}
        return self._resolve_settings(configured, agent, model, effort)

    def compose_pinned_instruction(self, project_id, snapshot_id, requirement, feedback=None):
        """The instruction a pinned custom role is dispatched with: its frozen operator text and
        base-role contract, then the same strictness, gate, feedback and requirement parts a
        built-in role gets. The only thing read from the snapshot is the snapshot."""
        content = self.role_snapshot(project_id, snapshot_id)["content"]
        gate = self.get_project_settings(project_id)["review_gate"]
        return custom_instruction(
            (content["definition"]["name"], content["role_ref"], content["version"],
             content["base_role"]),
            content["definition"]["prompt"], requirement, strictness=gate["ar_strictness"],
            max_rounds=gate["max_rounds"], feedback=feedback,
        )

    def check_snapshot_contract(self, project_id, task_id, snapshot, step_id):
        """Compare the fixed contract a snapshot was pinned under with today's, before it runs.

        identical     nothing recorded; the role runs.
        compatible    same major versions, only wording differs: the role runs under the current
                      text, and the difference is recorded for that task and step.
        incompatible  the meaning of the fixed contract changed: nothing is dispatched until a
                      person accepts that exact change for this task, and every attempt is
                      recorded. Raises ContractDriftBlocked."""
        language = self.get_project_settings(project_id)["language"]
        current = contract_manifest(language, snapshot["base_role"])
        result = assess_contract(snapshot["content"]["contract"], current)
        result = {
            **result, "pinned_contract_hash": snapshot["contract_hash"],
            "current_contract_hash": current["contract_hash"],
        }
        if result["status"] == IDENTICAL:
            return result
        with self.store.transaction() as conn:
            accepted = conn.execute(
                "SELECT actor FROM contract_drift_log WHERE project_id=? AND task_id=? "
                "AND snapshot_id=? AND current_contract_hash=? AND decision='accepted' "
                "ORDER BY seq DESC LIMIT 1",
                (project_id, task_id, snapshot["snapshot_id"], current["contract_hash"]),
            ).fetchone()
            decision = "compatible" if result["status"] == COMPATIBLE else (
                "accepted" if accepted else "blocked"
            )
            if not (decision == "accepted"):
                last = conn.execute(
                    "SELECT decision, current_contract_hash FROM contract_drift_log WHERE "
                    "project_id=? AND task_id=? AND snapshot_id=? AND step_id IS ? "
                    "ORDER BY seq DESC LIMIT 1",
                    (project_id, task_id, snapshot["snapshot_id"], step_id),
                ).fetchone()
                if not last or (last["decision"], last["current_contract_hash"]) != (
                    decision, current["contract_hash"]
                ):
                    self._log_drift(conn, project_id, task_id, step_id, snapshot, current,
                                    decision, result["changes"], None)
        if decision == "blocked":
            raise ContractDriftBlocked(
                "The fixed contract this role was pinned under has changed in a way that may "
                f"change its meaning ({', '.join(c['component'] or '?' for c in result['changes'])}). "
                "Nothing was dispatched. Accept the change for this task, or stop the task."
            )
        result["decision"] = decision
        if accepted:
            result["accepted_by"] = accepted["actor"]
        return result

    @staticmethod
    def _log_drift(conn, project_id, task_id, step_id, snapshot, current, decision, changes, actor):
        conn.execute(
            "INSERT INTO contract_drift_log(project_id,task_id,step_id,snapshot_id,"
            "pinned_contract_hash,current_contract_hash,decision,changes,actor,created) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                project_id, task_id, step_id, snapshot["snapshot_id"], snapshot["contract_hash"],
                current["contract_hash"], decision, canonical(changes), actor, now(),
            ),
        )

    def accept_contract_drift(
        self, project_id, task_id, step_id, actor, *, snapshot_id=None, contract_hash=None
    ):
        """A person accepts the current fixed contract for one step of one task whose pinned
        role was frozen under an incompatible one. Recorded with who and which exact change;
        it covers only that task and snapshot and only the contract as it is now.

        A caller that showed the person a particular change passes its `snapshot_id` and
        `contract_hash`; if the contract has changed again since, nothing is accepted."""
        if not isinstance(actor, str) or not actor.strip():
            raise BridgeError("An actor is required")
        flow = self._task_flow(project_id, task_id)
        step = next((s for s in (flow or {}).get("steps", []) if s.get("step_id") == step_id), None)
        if not step or not step.get("snapshot_id"):
            raise BridgeError("That step does not use a pinned custom role")
        snapshot = self.role_snapshot(project_id, step["snapshot_id"])
        current = contract_manifest(self.get_project_settings(project_id)["language"],
                                    snapshot["base_role"])
        assessment = assess_contract(snapshot["content"]["contract"], current)
        if assessment["status"] != INCOMPATIBLE:
            raise BridgeError("There is no incompatible contract change to accept for this step")
        if (snapshot_id is not None and snapshot_id != snapshot["snapshot_id"]) or (
            contract_hash is not None and contract_hash != current["contract_hash"]
        ):
            raise VersionConflict(
                "The fixed contract or the pinned role changed since you reviewed it; review "
                "the change again before accepting"
            )
        with self.lock(project_id), self.store.transaction() as conn:
            self._log_drift(conn, project_id, task_id, step_id, snapshot, current, "accepted",
                            assessment["changes"], actor)
        return {"accepted": True, "changes": assessment["changes"]}

    def contract_drift_history(self, project_id, task_id):
        rows = self.store.all(
            "SELECT * FROM contract_drift_log WHERE project_id=? AND task_id=? ORDER BY seq",
            (project_id, task_id),
        )
        return [{**row, "changes": json.loads(row["changes"])} for row in rows]

    @staticmethod
    def role_studio_catalog():
        """What the role studio needs to know that never changes between projects: the base
        roles a custom role can stand on and what each means for it, plus the limits."""
        bases = []
        for role in AGENT_ROLES:
            abbreviation, name, description = ROLE_CATALOG[role]
            bases.append({
                "id": role, "abbreviation": abbreviation, "name": name,
                "description": description, "may_write": writes_files(role),
                "is_gate": role in GATE_ROLES, "is_producer": role in PRODUCER_ROLES,
                "default_provider": default_provider(role),
                "default_avatar": DEFAULT_AVATARS.get(role),
                "stats": dict(zip(STAT_AXES, ROLE_STATS[role], strict=True)),
            })
        return {
            "bases": bases, "avatars": list(AVATAR_IDS), "kinds": list(KINDS),
            "stat_axes": list(STAT_AXES), "stat_max": STAT_MAX,
            "sections": list(SECTION_KEYS), "prompt_modes": list(PROMPT_MODES),
            "providers": {p: sorted(EFFORTS.get(p, set())) for p in sorted(PROVIDERS)},
            "limits": {
                "roles": MAX_CUSTOM_ROLES, "description": DESCRIPTION_MAX,
                "section": SECTION_MAX, "prompt_total": PROMPT_TOTAL_MAX,
            },
        }

    # -- what the console shows ---------------------------------------------------------------

    def custom_role_directory(self, project_id):
        """A light summary of every custom role, for lists, pickers and chat labels."""
        out = []
        for role in self.list_custom_roles(project_id):
            definition = role["definition"]
            out.append({
                "id": role["id"], "name": definition["name"], "base_role": role["base_role"],
                "status": role["status"], "origin": role["origin"], "version": role["version"],
                "avatar": definition["avatar"], "kind": definition["kind"],
                "description": definition["description"],
                "stats": declared_stats(definition, role["base_role"]),
                "stats_declared": definition["stats"] is not None,
            })
        return out

    def role_labels(self, project_id, snapshot_ids):
        """{snapshot id: name, avatar, version, base role} as each role was when it was pinned."""
        labels = {}
        for snapshot_id in {item for item in snapshot_ids if item}:
            row = self.store.one(
                "SELECT content FROM role_snapshots WHERE project_id=? AND snapshot_id=?",
                (project_id, snapshot_id),
            )
            if row:
                content = json.loads(row["content"])
                labels[snapshot_id] = {
                    "role_ref": content["role_ref"], "name": content["definition"]["name"],
                    "avatar": content["definition"]["avatar"], "version": content["version"],
                    "base_role": content["base_role"],
                }
        return labels

    def custom_role_usage(self, project_id, role_ref):
        """Where a role is used: workflows that name it, and tasks pinned to it."""
        self._role_row(project_id, role_ref)
        workflows = [
            {"id": flow_id, "name": flow["name"]}
            for flow_id, flow in self.get_project_settings(project_id)["workflows"].items()
            if any(step.get("role_ref") == role_ref for step in flow["steps"])
        ]
        pins = self.store.all(
            "SELECT DISTINCT task_id FROM task_roster_pins WHERE project_id=? AND roster LIKE ?",
            (project_id, f'%"role_ref":"{role_ref}"%'),
        )
        return {"workflows": workflows, "tasks": sorted(row["task_id"] for row in pins)}

    def custom_role_diff(self, project_id, role_ref, from_version, to_version):
        """What changed between two versions, as plain data a page can show: which fields
        differ and, for the instructions, a line-by-line comparison."""
        import difflib

        before = self.get_custom_role(project_id, role_ref, from_version)["definition"]
        after = self.get_custom_role(project_id, role_ref, to_version)["definition"]
        fields = []
        for key in ("name", "description", "avatar", "kind", "stats", "runtime"):
            if before[key] != after[key]:
                fields.append({"field": key, "before": before[key], "after": after[key]})
        texts = {
            "mode": (before["prompt"]["mode"], after["prompt"]["mode"]),
            **{
                f"section:{key}": (before["prompt"]["sections"][key], after["prompt"]["sections"][key])
                for key in SECTION_KEYS
            },
            "freeform": (before["prompt"]["freeform"], after["prompt"]["freeform"]),
        }
        prompt = []
        for name, (old, new) in texts.items():
            if old == new:
                continue
            lines = [
                {"op": line[0], "text": line[2:]}
                for line in difflib.ndiff(old.splitlines(), new.splitlines())
                if line[0] in "+- "
            ]
            prompt.append({"part": name, "lines": lines})
        return {"from": from_version, "to": to_version, "fields": fields, "prompt": prompt}

    def task_contract_status(self, project_id, task_id):
        """For each pinned custom-role step of a task, whether the fixed contract it was frozen
        under still matches today's, and what to confirm to go on if it does not."""
        flow = self._task_flow(project_id, task_id)
        language = self.get_project_settings(project_id)["language"]
        steps = []
        for step in (flow or {}).get("steps", []):
            if not step.get("snapshot_id"):
                continue
            snapshot = self.role_snapshot(project_id, step["snapshot_id"])
            current = contract_manifest(language, snapshot["base_role"])
            assessment = assess_contract(snapshot["content"]["contract"], current)
            accepted = self.store.one(
                "SELECT actor, created FROM contract_drift_log WHERE project_id=? AND task_id=? "
                "AND snapshot_id=? AND current_contract_hash=? AND decision='accepted' "
                "ORDER BY seq DESC LIMIT 1",
                (project_id, task_id, snapshot["snapshot_id"], current["contract_hash"]),
            )
            item = {
                "step_id": step["step_id"], "role_ref": step["role_ref"],
                "name": snapshot["content"]["definition"]["name"],
                "base_role": snapshot["base_role"], "status": assessment["status"],
                "changes": assessment["changes"], "snapshot_id": snapshot["snapshot_id"],
                "trusted": snapshot["trusted"],
                "pinned_contract_hash": snapshot["contract_hash"],
                "current_contract_hash": current["contract_hash"],
                "accepted": dict(accepted) if accepted else None,
            }
            if assessment["status"] == INCOMPATIBLE and not accepted:
                item["confirmation_phrase"] = self.drift_confirmation(
                    task_id, step["step_id"], current["contract_hash"]
                )
            steps.append(item)
        return {
            "task_id": task_id, "steps": steps,
            "blocked": [s["step_id"] for s in steps
                        if s["status"] == INCOMPATIBLE and not s["accepted"]],
            "history": self.contract_drift_history(project_id, task_id),
        }

    def flow_step_details(self, project_id, flow):
        """Each step of a task's pinned workflow: its base role and, for a custom role, who it is
        as it was when pinned."""
        labels = self.role_labels(project_id, [s.get("snapshot_id") for s in flow["steps"]])
        details = []
        for index, step in enumerate(flow["steps"]):
            label = labels.get(step.get("snapshot_id")) or {}
            details.append({
                "index": index, "role": step["role"], "role_ref": step.get("role_ref"),
                "step_id": step.get("step_id"), "name": label.get("name"),
                "avatar": label.get("avatar"), "version": label.get("version"),
            })
        return details

    def flow_declared_stats(self, project_id, flow):
        """{role id: six scores} for the custom roles of a pinned workflow, as pinned."""
        extra = {}
        for step in flow["steps"]:
            if step.get("snapshot_id"):
                content = self.role_snapshot(project_id, step["snapshot_id"])["content"]
                extra[step["role_ref"]] = tuple(
                    declared_stats(content["definition"], content["base_role"]).values()
                )
        return extra

    @staticmethod
    def drift_confirmation(task_id, step_id, contract_hash):
        """The exact words a person types to accept one specific change."""
        return f"ACCEPT {task_id} {step_id} {contract_hash[:8]}"

    def accept_contract_drift_web(
        self, project_id, task_id, step_id, snapshot_id, contract_hash, typed_confirmation
    ):
        """The local console's way to accept a change. Beyond holding the console's session
        token, the person must type a phrase naming this task, step and contract, and the
        change they reviewed must still be the current one. The record names the console as
        the actor. Nothing reachable from MCP or from a project's write authorization calls it."""
        if typed_confirmation != self.drift_confirmation(task_id, step_id, contract_hash or ""):
            raise PermissionDenied("The confirmation text does not match this change")
        return self.accept_contract_drift(
            project_id, task_id, step_id, "local-console",
            snapshot_id=snapshot_id, contract_hash=contract_hash,
        )

    # -- rosters ----------------------------------------------------------------------

    def pin_task_roster(self, conn, project_id, task_id, flow_id, steps):
        """Pin which role definition runs at each step of a task, inside the caller's
        transaction (the one that pins the task's workflow).

        `steps` is the workflow's list of {"role": built-in role, "role_ref": optional custom
        role}. Every step gets its own `step_id`, so one role used twice stays two steps. A new
        pin is appended each time a task starts a work round; earlier pins are history."""
        roster = []
        seq_row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM task_roster_pins WHERE project_id=? AND task_id=?",
            (project_id, task_id),
        ).fetchone()
        seq = seq_row[0]
        for index, step in enumerate(steps):
            role, role_ref = step.get("role"), step.get("role_ref")
            if role not in AGENT_ROLES:
                raise BridgeError(f"Unknown role in workflow: {role!r}")
            entry = {
                "step_id": make_step_id(seq, index), "index": index, "role": role,
                "role_ref": None, "snapshot_id": None, "role_version": None,
            }
            if role_ref is not None:
                snapshot_id = self._snapshot_in(conn, project_id, role_ref)
                stored = self._role_row(project_id, role_ref, conn)
                if stored["base_role"] != role:
                    raise BridgeError(f"{role_ref} is not based on {role}")
                entry.update(
                    role_ref=role_ref, snapshot_id=snapshot_id,
                    role_version=self._snapshot_version(conn, project_id, snapshot_id),
                )
            roster.append(entry)
        digest_value = roster_hash(
            [
                {key: item[key] for key in ("step_id", "role", "role_ref", "snapshot_id")}
                for item in roster
            ]
        )
        pin_id = f"PIN-{task_id}-{seq:03d}"
        conn.execute(
            "INSERT INTO task_roster_pins(id,project_id,task_id,seq,flow_id,roster,roster_hash,"
            "created) VALUES(?,?,?,?,?,?,?,?)",
            (pin_id, project_id, task_id, seq, flow_id, canonical(roster), digest_value, now()),
        )
        return {
            "id": pin_id, "seq": seq, "flow_id": flow_id, "roster": roster,
            "roster_hash": digest_value,
        }

    @staticmethod
    def _snapshot_version(conn, project_id, snapshot_id):
        return conn.execute(
            "SELECT version FROM role_snapshots WHERE project_id=? AND snapshot_id=?",
            (project_id, snapshot_id),
        ).fetchone()[0]

    def current_roster_pin(self, project_id, task_id):
        row = self.store.one(
            "SELECT * FROM task_roster_pins WHERE project_id=? AND task_id=? "
            "ORDER BY seq DESC LIMIT 1", (project_id, task_id),
        )
        return {**row, "roster": json.loads(row["roster"])} if row else None

    def bind_approval_to_roster(self, conn, project_id, approval_id, task_id):
        """Record which roster the task is running as a plan is approved, inside the approval's
        own transaction. Binding is append-only and happens once per approval."""
        pin = conn.execute(
            "SELECT id, roster_hash FROM task_roster_pins WHERE project_id=? AND task_id=? "
            "ORDER BY seq DESC LIMIT 1", (project_id, task_id),
        ).fetchone()
        if not pin:
            return None
        conn.execute(
            "INSERT INTO approval_rosters(project_id,approval_id,pin_id,roster_hash,created) "
            "VALUES(?,?,?,?,?)", (project_id, approval_id, pin["id"], pin["roster_hash"], now()),
        )
        return pin["roster_hash"]

    def approval_roster(self, project_id, approval_id):
        return self.store.one(
            "SELECT pin_id, roster_hash FROM approval_rosters WHERE project_id=? AND approval_id=?",
            (project_id, approval_id),
        )

    def roster_matches_approval(self, project_id, task_id, approval_id):
        """Whether the roster the task is running now is the one this approval was given for.
        An approval with no binding (every approval made before custom roles existed, or for a
        task with only built-in roles) matches only a task that has no pinned roster."""
        bound = self.approval_roster(project_id, approval_id)
        pin = self.current_roster_pin(project_id, task_id)
        if bound is None:
            return pin is None
        return pin is not None and pin["roster_hash"] == bound["roster_hash"]

    # -- previews and checks (read-only) ------------------------------------------------------

    def _role_for_review(self, project_id, role_ref, draft, base_role, version):
        """A stored role, or an unsaved draft, as (role_ref, version, base_role, definition).
        Reads only. A draft is validated like a real save would be, but never stored."""
        if draft is not None:
            if role_ref is not None:
                stored = self._role_row(project_id, role_ref)
                base_role = stored["base_role"]
                version = stored["current_version"]
            if base_role not in AGENT_ROLES:
                raise BridgeError(f"base_role must be one of {list(AGENT_ROLES)}")
            return role_ref, version, base_role, self._validated_definition(base_role, draft)
        if role_ref is None:
            raise BridgeError("Give a role id or a draft")
        role = self.get_custom_role(project_id, role_ref, version)
        return role["id"], role["version"], role["base_role"], role["definition"]

    def preview_custom_role_prompt(
        self, project_id, role_ref=None, *, draft=None, base_role=None, version=None,
        provider=None, requirement=None, strictness=None, max_rounds=None, feedback=None,
        package=None,
    ):
        """What the role would be sent, labelled by who may change each part. Not a dispatch:
        no execution is created and nothing is written."""
        self.project(project_id)
        ref, number, base, definition = self._role_for_review(
            project_id, role_ref, draft, base_role, version
        )
        settings = self.get_project_settings(project_id)
        gate = settings["review_gate"]
        chosen = provider or (definition["runtime"] or {}).get("provider") or (
            settings["roles"][base]["provider"]
        )
        return assemble_preview(
            (definition["name"], ref or "(unsaved draft)", number or 0, base),
            definition["prompt"],
            language=settings["language"], provider=chosen, requirement=requirement,
            strictness=strictness or gate["ar_strictness"],
            max_rounds=max_rounds if max_rounds is not None else gate["max_rounds"],
            feedback=feedback, package=package,
        )

    def lint_custom_role(self, project_id, role_ref=None, *, draft=None, base_role=None,
                         version=None):
        """The free local check of a role's instructions. No model call, no score."""
        self.project(project_id)
        if draft is not None:
            # A draft is checked as typed, including problems saving would reject.
            if role_ref is not None:
                base_role = self._role_row(project_id, role_ref)["base_role"]
            if base_role not in AGENT_ROLES:
                raise BridgeError(f"base_role must be one of {list(AGENT_ROLES)}")
            return lint_role(base_role, draft)
        _, _, base, definition = self._role_for_review(project_id, role_ref, None, None, version)
        return lint_role(base, definition)

    # -- what was actually sent ------------------------------------------------------

    @staticmethod
    def record_execution_prompt(conn, project_id, execution_id, provider, via, prompt, segments):
        """Keep the complete text handed to a provider for one execution. A preview is never
        stored here: only a real dispatch has an execution_id.

        The hash is of what was really sent. If the text held something that looks like a
        credential, the stored copy has it redacted and `redacted` is set, so the record never
        becomes a place secrets are kept and never pretends to be a verbatim copy."""
        if via not in {"adapter_stdin", "chatgpt_handoff"}:
            raise BridgeError("Unknown prompt channel")
        stored = redact(prompt)
        conn.execute(
            "INSERT INTO execution_prompts(execution_id,project_id,provider,via,prompt_sha256,"
            "prompt_text,redacted,segments,created) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                execution_id, project_id, provider, via, digest(prompt.encode("utf-8")), stored,
                int(stored != prompt), canonical(segments), now(),
            ),
        )

    def execution_prompt(self, project_id, execution_id):
        row = self.store.one(
            "SELECT * FROM execution_prompts WHERE project_id=? AND execution_id=?",
            (project_id, execution_id),
        )
        return {**row, "segments": json.loads(row["segments"])} if row else None
