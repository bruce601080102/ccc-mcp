"""TaskDelta: reconcile the PlannedGraph, the base and current ActualGraph, identity events and host
test evidence. The three verdicts are kept separate on purpose:

  commit_state      COMMITTED | UNCOMMITTED           is the change recorded in a commit?
  structure_state   MATCHED | NEEDS_REVIEW | FLAGGED_OUT_OF_SCOPE | BLOCKED_BY_SCOPE
                                                     does the code match the approved plan?
  host_test_state   PASSED | FAILED | NOT_RUN         what did the host observe when it ran tests?
"""


def _changed_files(base, current):
    """A file is changed when the committed blob differs from the worktree OR from the index.

    Comparing the worktree alone lost staged-only changes (a file staged with a change, then the
    worktree restored to HEAD). The index blob is checked as well, so nothing uncommitted drops out.
    """
    changed = []
    for path in sorted(set(base.files) | set(current.files)):
        before = base.files.get(path, {}).get("blob")
        state = current.files.get(path, {})
        after = state.get("blob")
        staged = state.get("index_blob", after) if current.mode == "worktree" else after
        if before != after or staged != before:
            changed.append(path)
    return changed


def _scope(path, plan_files):
    if path in plan_files["add"] or path in plan_files["modify"] or path in plan_files["delete"]:
        return "PLANNED"
    if path in plan_files["affected"]:
        return "PLANNED_AFFECTED"
    return "NOT_PLANNED"


def _file_is_uncommitted(current, path):
    if current.mode != "worktree":
        return False
    change = current.files.get(path, {}).get("change", {})
    return change.get("index", "none") != "none" or change.get("worktree", "none") != "none"


def reconcile(plan, base, current, tests=None):
    base_map = base.node_map()
    current_map = current.node_map()
    changed = _changed_files(base, current)
    changed_set = set(changed)
    plan_symbols = {symbol["key"]: symbol for symbol in plan.symbols}

    file_rows = []
    for path in changed:
        state = current.files.get(path, {})
        file_rows.append(
            {
                "path": path,
                "scope": _scope(path, plan.files),
                "excluded": state.get("excluded"),
                "change": state.get("change", {"index": "deleted", "worktree": "none"}),
                "commit_state": "UNCOMMITTED"
                if _file_is_uncommitted(current, path)
                else "COMMITTED",
                # Symbol rows describe the worktree; a staged blob that differs from it is recorded here.
                "staged_differs_from_worktree": current.mode == "worktree"
                and state.get("change", {}).get("index", "none") != "none"
                and state.get("index_blob") != state.get("blob"),
            }
        )

    importers = {}
    for edge in current.edges:
        if edge["kind"] == "IMPORT":
            importers.setdefault(edge["src"], set()).add(edge["dst"])
    importing_changed = {src for src, dsts in importers.items() if dsts & changed_set}

    move_events = {
        event["to_key"]: event
        for event in current.events
        if event["type"] in ("MOVE", "RENAME", "MOVE_RENAME")
        and event["status"] in ("AUTO", "CONFIRMED_BY_USER")
    }
    moved_from = {event["from_key"] for event in move_events.values()}
    pending_events = [event for event in current.events if event["type"] == "PENDING_CONFIRMATION"]
    pending_targets = {event["to_key"] for event in pending_events}

    symbol_rows = []

    def add_row(marker, key, node, old, path, extra=None):
        planned_key = (
            key
            if key in plan_symbols
            else (old.key if old is not None and old.key in plan_symbols else None)
        )
        if marker == "AFFECTED":
            structure = "INFERRED"
        elif marker == "UNCERTAIN":
            structure = "PENDING_CONFIRMATION"
        elif planned_key is not None:
            structure = "MATCHED"
        else:
            structure = "UNPLANNED"
        row = {
            "key": key,
            "marker": marker,
            "nid": (node.nid if node else (old.nid if old else None)),
            "path": path,
            "qualname": (
                node.qualname if node else (old.qualname if old else key.split("::", 1)[-1])
            ),
            "structure_state": structure,
            "planned_action": plan_symbols[planned_key]["action"] if planned_key else None,
            "planned_reason": plan_symbols[planned_key].get("reason") if planned_key else None,
            "commit_state": "UNCOMMITTED" if _file_is_uncommitted(current, path) else "COMMITTED",
            "source": ({"path": node.path, "lines": [node.start, node.end]} if node else None),
            "from_key": old.key if old else None,
        }
        if extra:
            row.update(extra)
        symbol_rows.append(row)

    for key in sorted(current_map):
        node = current_map[key]
        if key in move_events:
            event = move_events[key]
            old = base_map.get(event["from_key"])
            add_row(
                "MOVED",
                key,
                node,
                old,
                node.path,
                {
                    "move_type": event["type"],
                    "score": event["score"],
                    "identity_status": event["status"],
                },
            )
            continue
        old = base_map.get(key)
        if old is None:
            # A pending new symbol is reported once, as UNCERTAIN below, never also as ADD.
            if node.path in changed_set and key not in pending_targets:
                add_row("ADD", key, node, None, node.path)
            continue
        if old.body_hash != node.body_hash or old.signature_hash != node.signature_hash:
            add_row("MODIFY", key, node, old, node.path)
        elif node.path in importing_changed:
            add_row("AFFECTED", key, node, old, node.path)

    for key in sorted(base_map):
        if key in current_map or key in moved_from:
            continue
        old = base_map[key]
        add_row("REMOVE", key, None, old, old.path)

    for event in pending_events:
        node = current_map.get(event["to_key"])
        add_row(
            "UNCERTAIN",
            event["to_key"],
            node,
            base_map.get(event["from_key"]),
            node.path if node else event["to_key"].split("::", 1)[0],
            {
                "candidate_key": event["from_key"],
                "score": event["score"],
                "identity_status": "PENDING",
            },
        )

    missing = []
    for symbol in plan.symbols:
        key, action = symbol["key"], symbol["action"]
        if action in ("ADD", "MODIFY") and key not in current_map and key not in move_events:
            missing.append(
                {"key": key, "action": action, "reason": "planned symbol is not present"}
            )
        if action == "REMOVE" and key in current_map:
            missing.append(
                {"key": key, "action": action, "reason": "planned removal is still present"}
            )
    for path in plan.files["add"] + plan.files["modify"]:
        if path not in changed_set:
            missing.append({"path": path, "reason": "planned file did not change"})
    for path in plan.files["delete"]:
        if current.files.get(path, {}).get("blob") is not None:
            missing.append({"path": path, "reason": "planned deletion is not present"})

    out_of_plan = [
        row["path"] for row in file_rows if row["scope"] == "NOT_PLANNED" and not row["excluded"]
    ]
    unplanned = [row["key"] for row in symbol_rows if row["structure_state"] == "UNPLANNED"]
    pending = [row["key"] for row in symbol_rows if row["marker"] == "UNCERTAIN"]

    if out_of_plan:
        structure_state = (
            "BLOCKED_BY_SCOPE" if current.mode == "worktree" else "FLAGGED_OUT_OF_SCOPE"
        )
    elif missing or unplanned or pending:
        structure_state = "NEEDS_REVIEW"
    else:
        structure_state = "MATCHED"

    any_uncommitted = any(row["commit_state"] == "UNCOMMITTED" for row in file_rows)
    excluded = [
        {"path": path, "reason": state["excluded"], "change": state.get("change")}
        for path, state in sorted(current.files.items())
        if state.get("excluded")
    ]
    host_test = tests if tests else {"state": "NOT_RUN"}
    return {
        "task": plan.task,
        "title": plan.title,
        "plan_gv": plan.gv_id,
        "base": {"gv_id": base.gv_id, "label": base.label, "mode": base.mode, "head": base.head},
        "current": {
            "gv_id": current.gv_id,
            "label": current.label,
            "mode": current.mode,
            "head": current.head,
            "provisional": current.provisional,
        },
        "commit_state": "UNCOMMITTED"
        if (current.mode == "worktree" and any_uncommitted)
        else "COMMITTED",
        "structure_state": structure_state,
        "host_test_state": host_test["state"],
        "host_test": host_test,
        "files": file_rows,
        "symbols": symbol_rows,
        "missing_planned": missing,
        "out_of_plan_files": out_of_plan,
        "unplanned_symbols": unplanned,
        "pending_confirmations": [
            {
                "from_key": e["from_key"],
                "to_key": e["to_key"],
                "score": e["score"],
                "reasons": e.get("reasons", []),
            }
            for e in pending_events
        ],
        "identity_events": current.events,
        "excluded": excluded,
    }
