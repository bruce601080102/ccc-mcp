"""One self-contained HTML page per task, with three views of the same TaskDelta.

  新手架構圖（實際）  plain-language modules: state, overlays, feature labels, dependency arrows
  規劃（Planned）     the approved plan's files and planned dependencies, compared with what exists
  工程視圖            files, symbols, sources, identity events, dependency reconciliation, exclusions

Everything is computed here, deterministically, from the graphs and the delta. No model draws a
diagram, and no source text is written into the page: only names, line ranges, markers, and verdicts.
Nothing here has been tested with novice users; the page shows correctness and completeness only.
"""

import html
import json
import os

from .gate import REASON_TEXT, approval_gate
from .views import novice_view

NODE_W, NODE_H = 200, 104
COL_GAP, ROW_GAP = 26, 118
MARGIN = 24
MAX_COLS = 5

STATE_STYLE = {
    "EXISTING": {
        "label": "既有、未變動",
        "short": "既有",
        "symbol": "·",
        "fill": "#f3f4f6",
        "stroke": "#4b5563",
        "border": "solid",
    },
    "ADDED": {
        "label": "新增",
        "short": "新增",
        "symbol": "＋",
        "fill": "#dcfce7",
        "stroke": "#15803d",
        "border": "solid",
    },
    "MODIFIED": {
        "label": "修改",
        "short": "修改",
        "symbol": "✎",
        "fill": "#fef9c3",
        "stroke": "#a16207",
        "border": "solid",
    },
    "REMOVED": {
        "label": "移除（歷史保留）",
        "short": "移除",
        "symbol": "−",
        "fill": "#fee2e2",
        "stroke": "#b91c1c",
        "border": "dotted",
    },
    "AFFECTED": {
        "label": "間接影響",
        "short": "影響",
        "symbol": "↯",
        "fill": "#dbeafe",
        "stroke": "#1d4ed8",
        "border": "dashed",
    },
    "EXCLUDED": {
        "label": "敏感，已排除（未讀內容）",
        "short": "已排除",
        "symbol": "⊘",
        "fill": "#e5e7eb",
        "stroke": "#374151",
        "border": "dotted",
    },
    "ABSENT": {
        "label": "規劃中，尚未實作",
        "short": "未實作",
        "symbol": "◌",
        "fill": "#ffffff",
        "stroke": "#9ca3af",
        "border": "dashed",
    },
}
OVERLAY_STYLE = {
    "UNCOMMITTED": {"label": "未 Commit", "color": "#c2410c"},
    "OUT_OF_PLAN": {"label": "超出計畫", "color": "#b91c1c"},
    "UNPLANNED": {"label": "未規劃的符號", "color": "#b91c1c"},
    "PENDING": {"label": "待確認（對應不確定）", "color": "#6b21a8"},
    "STAGED_DIFF": {"label": "暫存區與工作區不同", "color": "#0e7490"},
}
OVERLAY_SHORT = {
    "UNCOMMITTED": "未 Commit",
    "OUT_OF_PLAN": "超出計畫",
    "UNPLANNED": "未規劃",
    "PENDING": "待確認",
    "STAGED_DIFF": "暫存≠工作區",
}
COMMIT_LABEL = {"COMMITTED": "已 commit", "UNCOMMITTED": "尚未 commit"}
HOST_LABEL = {"PASSED": "通過", "FAILED": "失敗", "NOT_RUN": "未執行"}
STRUCTURE_LABEL = {
    "MATCHED": "與計畫相符",
    "NEEDS_REVIEW": "需人工檢視",
    "FLAGGED_OUT_OF_SCOPE": "有超出計畫",
    "BLOCKED_BY_SCOPE": "超出計畫，阻擋",
}
PLANNED_ACTION_STYLE = {
    "ADD": {"label": "計畫：新增", "fill": "#dcfce7", "stroke": "#15803d", "border": "solid"},
    "MODIFY": {"label": "計畫：修改", "fill": "#fef9c3", "stroke": "#a16207", "border": "solid"},
    "DELETE": {"label": "計畫：刪除", "fill": "#fee2e2", "stroke": "#b91c1c", "border": "dotted"},
    "AFFECTED": {
        "label": "計畫：可能受影響",
        "fill": "#dbeafe",
        "stroke": "#1d4ed8",
        "border": "dashed",
    },
}


def _present(graph, path):
    entry = graph.files.get(path)
    return bool(entry) and entry.get("blob") is not None


def _base(path):
    return os.path.basename(path)


def _layout(ids, edges):
    """Layered layout: dependencies sit lower, dependents higher. Returns (placement, columns, rows).

    Rows come from dependency depth. Within a row, nodes are ordered by the average position of
    their dependencies in the row below (barycenter), processed bottom-up, so arrows cross less.
    """
    deps = {node: [] for node in ids}
    for src, dst in edges:
        if src in deps and dst in deps and src != dst:
            deps[src].append(dst)
    depth = {}

    def visit(node, stack):
        if node in depth:
            return depth[node]
        if node in stack:
            return 0  # cycle guard: a back-edge contributes nothing
        stack.add(node)
        depth[node] = 1 + max((visit(d, stack) for d in deps[node]), default=-1)
        stack.discard(node)
        return depth[node]

    for node in sorted(ids):
        visit(node, set())
    top = max(depth.values(), default=0)
    rows = {}
    for node in ids:
        rows.setdefault(top - depth[node], []).append(node)
    position = {}
    for row in sorted(rows, reverse=True):  # bottom-most first

        def key(node):
            below = [position[d] for d in deps[node] if d in position]
            return (sum(below) / len(below) if below else float("inf"), node)

        members = sorted(rows[row], key=key)
        rows[row] = members
        for col, node in enumerate(members):
            position[node] = col
    # A layer wider than MAX_COLS wraps into sub-rows, so the whole diagram fits the page width.
    chunked = []
    for row in sorted(rows):
        members = rows[row]
        for start in range(0, len(members), MAX_COLS):
            chunked.append(members[start : start + MAX_COLS])
    placement = {}
    for row_index, members in enumerate(chunked):
        for col, node in enumerate(members):
            placement[node] = (row_index, col)
    return placement, max((len(m) for m in chunked), default=1), len(chunked)


def _canvas(placement, columns, row_count):
    width = MARGIN * 2 + columns * (NODE_W + COL_GAP) - COL_GAP
    height = MARGIN * 2 + row_count * (NODE_H + ROW_GAP) - ROW_GAP
    coords = {
        node: (MARGIN + col * (NODE_W + COL_GAP), MARGIN + row * (NODE_H + ROW_GAP))
        for node, (row, col) in placement.items()
    }
    return coords, max(width, NODE_W + MARGIN * 2), max(height, NODE_H + MARGIN * 2)


def _file_states(plan, base, current, delta):
    rows = delta["symbols"]
    changed = {r["path"] for r in delta["files"]}
    excluded = {e["path"]: e for e in delta["excluded"]}
    uncommitted = {r["path"] for r in delta["files"] if r["commit_state"] == "UNCOMMITTED"}
    staged = {r["path"] for r in delta["files"] if r.get("staged_differs_from_worktree")}
    out_of_plan = set(delta["out_of_plan_files"])
    unplanned = {r["path"] for r in rows if r["structure_state"] == "UNPLANNED"}
    pending_paths = set()
    for row in rows:
        if row["marker"] == "UNCERTAIN":
            pending_paths.add(row["path"])
            if row.get("from_key"):
                pending_paths.add(row["from_key"].split("::", 1)[0])
    affected = {r["path"] for r in rows if r["marker"] == "AFFECTED"}

    universe = set(excluded)
    universe |= {p for p, e in base.files.items() if e.get("blob") is not None}
    universe |= {p for p, e in current.files.items() if e.get("blob") is not None}
    for group in plan.files.values():
        universe |= set(group)
    for src, dst in plan.planned_dependencies or []:
        universe |= {src, dst}

    features = {}
    for feature in plan.features:
        for member in feature.get("members", []):
            features.setdefault(member.split("::", 1)[0], []).append(feature["title_plain"])
    planned_action = {}
    for name, group in plan.files.items():
        for path in group:
            planned_action[path] = {
                "add": "ADD",
                "modify": "MODIFY",
                "delete": "DELETE",
                "affected": "AFFECTED",
            }[name]

    states = {}
    for path in sorted(universe):
        now = _present(current, path)
        before = _present(base, path)
        if current.files.get(path, {}).get("excluded"):
            # Excluded by name or by content: the file is shown as excluded, never as its content.
            state = "EXCLUDED"
        elif now and not before:
            state = "ADDED"
        elif now and before and path in changed:
            state = "MODIFIED"
        elif before and not now:
            state = "REMOVED"
        elif now and path in affected:
            state = "AFFECTED"
        elif now:
            state = "EXISTING"
        else:
            state = "ABSENT"
        overlays = []
        if path in uncommitted:
            overlays.append("UNCOMMITTED")
        if path in out_of_plan:
            overlays.append("OUT_OF_PLAN")
        if path in unplanned:
            overlays.append("UNPLANNED")
        if path in pending_paths:
            overlays.append("PENDING")
        if path in staged:
            overlays.append("STAGED_DIFF")
        symbols = [
            {
                "qualname": r["key"].split("::", 1)[1],
                "marker": r["marker"],
                "structure": r["structure_state"],
                "commit": r["commit_state"],
                "nid": r.get("nid"),
                "from_key": r.get("from_key"),
                "source": r.get("source"),
            }
            for r in rows
            if r["path"] == path and r["marker"] != "REMOVE"
        ]
        removed_symbols = [
            r["key"].split("::", 1)[1]
            for r in rows
            if r["path"] == path and r["marker"] == "REMOVE"
        ]
        reasons = sorted(
            {
                reason
                for r in delta["pending_confirmations"]
                if r["to_key"].split("::", 1)[0] == path or r["from_key"].split("::", 1)[0] == path
                for reason in r.get("reasons", [])
            }
        )
        pairs = [
            {
                "from_key": r["from_key"],
                "to_key": r["to_key"],
                "reasons": [REASON_TEXT.get(code, code) for code in r.get("reasons", [])],
            }
            for r in delta["pending_confirmations"]
            if r["to_key"].split("::", 1)[0] == path or r["from_key"].split("::", 1)[0] == path
        ]
        states[path] = {
            "path": path,
            "pending_pairs": pairs,
            "title": plan.module_titles.get(path)
            or ("已排除的敏感檔案" if path in excluded else _base(path)),
            "state": state,
            "overlays": overlays,
            "features": sorted(set(features.get(path, []))),
            "planned": planned_action.get(path),
            "symbols": symbols,
            "removed_symbols": removed_symbols,
            "pending_reasons": reasons,
            "excluded_reason": excluded[path]["reason"] if path in excluded else None,
            "python": path.endswith(".py"),
        }
    return states


def _actual_edges(current, states):
    edges = set()
    for edge in current.edges:
        if edge["kind"] == "IMPORT" and edge["src"] in states and edge["dst"] in states:
            if states[edge["src"]]["state"] != "ABSENT" and states[edge["dst"]]["state"] not in (
                "ABSENT",
                "EXCLUDED",
            ):
                edges.add((edge["src"], edge["dst"]))
    return edges


def _reconcile_edges(plan, actual, states):
    if plan.planned_dependencies is None:
        return {(s, d): "NEUTRAL" for s, d in actual}, []
    planned = {tuple(pair) for pair in plan.planned_dependencies}
    status = {}
    for pair in actual:
        status[pair] = "MATCHED" if pair in planned else "UNPLANNED"
    missing = []
    for src, dst in sorted(planned):
        if src in states and dst in states and (src, dst) not in actual:
            if states[src]["state"] not in ("ABSENT", "EXCLUDED") and states[dst]["state"] not in (
                "ABSENT",
                "EXCLUDED",
            ):
                missing.append((src, dst))
                status[(src, dst)] = "MISSING"
    return status, missing


UNATTRIBUTED_ID = "UNATTRIBUTED"
NOVICE_ORDER = ["ADD", "MODIFY", "AFFECTED", "REMOVE", "UNCERTAIN", "UNCHANGED", "UNATTRIBUTED"]
# Natural-language markers for the novice view. The raw states stay in the engineer view and the JSON.
NOVICE_STYLE = {
    "ADD": {"label": "新增", "symbol": "＋", "fill": "#ecfdf5", "stroke": "#15803d", "border": "solid"},
    "MODIFY": {"label": "修改", "symbol": "✎", "fill": "#fefce8", "stroke": "#a16207", "border": "solid"},
    "AFFECTED": {"label": "間接受影響", "symbol": "↯", "fill": "#eff6ff", "stroke": "#1d4ed8", "border": "dashed"},
    "REMOVE": {"label": "已移除（歷史保留）", "symbol": "−", "fill": "#fef2f2", "stroke": "#b91c1c", "border": "dotted"},
    "UNCERTAIN": {"label": "需要確認", "symbol": "?", "fill": "#faf5ff", "stroke": "#6b21a8", "border": "double"},
    "UNCHANGED": {"label": "未變動", "symbol": "·", "fill": "#f9fafb", "stroke": "#6b7280", "border": "solid"},
    "UNATTRIBUTED": {"label": "超出計畫", "symbol": "⚠", "fill": "#fff1f2", "stroke": "#9f1239", "border": "dashed"},
}
EDGE_RANK = {"MATCHED": 1, "NEUTRAL": 2, "UNPLANNED": 3}
GATE_NATURAL = {
    "READY": "可合併（本 Task）",
    "NEEDS_COMMIT": "待 commit（本 Task）",
    "BLOCKED": "未通過（本 Task 有阻擋項）",
}


def _feature_tab(plan, delta, states, actual_status, unattributed):
    """Novice main view: one node per approved feature, with natural-language markers.

    Built from the approved plan's features, the TaskDelta (through views.novice_view) and the
    file-level reconciliation. A file-level dependency between two features becomes one feature
    arrow. One file can hold code of several features, so a feature arrow can over-state a
    dependency; the file-level evidence is listed in the detail panel.
    """
    view = novice_view(delta, plan)
    by_id = {f["id"]: f for f in view["features"]}
    feature_files, file_features = {}, {}
    for feature in plan.features:
        files = []
        for member in feature.get("members", []):
            path = member.split("::", 1)[0]
            if path not in files:
                files.append(path)
            owners = file_features.setdefault(path, [])
            if feature["id"] not in owners:
                owners.append(feature["id"])
        feature_files[feature["id"]] = files
    nodes = [f["id"] for f in plan.features]
    if unattributed:
        nodes.append(UNATTRIBUTED_ID)
        feature_files[UNATTRIBUTED_ID] = list(unattributed)
        for path in unattributed:
            file_features.setdefault(path, []).append(UNATTRIBUTED_ID)
    # Evidence rule. A file import is DIRECT evidence for a feature pair only when both files belong
    # to exactly one feature each (and neither is unattributed). Otherwise, the file belongs to several
    # features and the import only suggests a relation, so the arrow is INFERRED and drawn dashed.
    pair_evidence = {}
    for (src, dst), status in actual_status.items():
        src_owners = file_features.get(src, [])
        dst_owners = file_features.get(dst, [])
        direct = (
            len(src_owners) == 1
            and len(dst_owners) == 1
            and UNATTRIBUTED_ID not in src_owners + dst_owners
        )
        for a in src_owners:
            for b in dst_owners:
                if a == b:
                    continue
                entry = pair_evidence.setdefault((a, b), {"direct": False, "unplanned": False, "files": []})
                entry["direct"] = entry["direct"] or direct
                entry["unplanned"] = entry["unplanned"] or status == "UNPLANNED"
                entry["files"].append(f"{src} → {dst}")
    feature_edges = {}
    for pair, entry in pair_evidence.items():
        if entry["direct"]:
            status = "DIRECT_UNPLANNED" if entry["unplanned"] else "DIRECT"
        else:
            status = "INFERRED"
        feature_edges[pair] = {"status": status, "evidence": sorted(set(entry["files"]))}
    placement, columns, rows = _layout(nodes, list(feature_edges))
    coords, width, height = _canvas(placement, columns, rows)
    features = {}
    for fid in nodes:
        if fid == UNATTRIBUTED_ID:
            info = {
                "title": "未歸屬功能的檔案",
                "marker": "UNATTRIBUTED",
                "sentence": "這些檔案超出已核准計畫，且無法歸屬任何功能。不會因此隱藏，需人工判斷是否接受或撤回。",
                "renamed_from": None,
                "git": None,
                "scope": ["超出計畫，未歸屬功能"],
                "identity_pending": 0,
            }
        else:
            item = by_id[fid]
            info = {
                "title": item["title"],
                "marker": item["marker"],
                "sentence": item["sentence"],
                "renamed_from": item.get("renamed_from"),
                "git": item["git"],
                "scope": item["scope"],
                "identity_pending": item["identity_pending"],
            }
        info["files"] = [
            {
                "path": path,
                "state": states.get(path, {}).get("state", "ABSENT"),
                "symbols": [
                    {"qualname": s["qualname"], "marker": s["marker"], "structure": s["structure"]}
                    for s in states.get(path, {}).get("symbols", [])
                ],
            }
            for path in feature_files.get(fid, [])
        ]
        features[fid] = info
    return {
        "nodes": nodes,
        "coords": coords,
        "width": width,
        "height": height,
        "edges": [
            {"src": a, "dst": b, "status": e["status"], "evidence": e["evidence"]}
            for (a, b), e in sorted(feature_edges.items())
        ],
        "features": features,
        "feature_of_file": {path: list(ids) for path, ids in file_features.items()},
    }


def _planned_tab(plan, states, actual):
    ids = sorted(
        {
            p
            for p in plan.files["add"]
            + plan.files["modify"]
            + plan.files["delete"]
            + plan.files["affected"]
        }
    )
    if plan.planned_dependencies:
        ids = sorted(set(ids) | {x for pair in plan.planned_dependencies for x in pair})
    edges_known = set(actual)
    placement, columns, rows = _layout(ids, [tuple(p) for p in (plan.planned_dependencies or [])])
    coords, width, height = _canvas(placement, columns, rows)
    edges = []
    for src, dst in plan.planned_dependencies or []:
        edges.append(
            {"src": src, "dst": dst, "status": "EXISTS" if (src, dst) in edges_known else "NOT_YET"}
        )
    return {"nodes": ids, "coords": coords, "width": width, "height": height, "edges": edges}


def _planned_check(action, state):
    if action == "ADD":
        return (
            ("已依計畫建立" if state == "ADDED" else "尚未建立")
            if state != "EXISTING"
            else "已存在（計畫為新增）"
        )
    if action == "MODIFY":
        return "已依計畫修改" if state == "MODIFIED" else "尚未修改"
    if action == "DELETE":
        return "已依計畫刪除" if state == "REMOVED" else "尚未刪除"
    return "依賴影響（符合預期）" if state in ("AFFECTED", "EXISTING") else "未出現"


def build_diagram(plan, base, current, delta, prior=()):
    states = _file_states(plan, base, current, delta)
    actual = _actual_edges(current, states)
    actual_status, missing = _reconcile_edges(plan, actual, states)
    unattributed = [path for path in delta["out_of_plan_files"] if not states[path]["features"]]
    novice = _feature_tab(plan, delta, states, actual_status, unattributed)
    planned = _planned_tab(plan, states, actual)
    for path in planned["nodes"]:
        state = states.get(path, {"state": "ABSENT"})
        action = states.get(path, {}).get("planned")
        if action is None:
            for group, name in (
                ("add", "ADD"),
                ("modify", "MODIFY"),
                ("delete", "DELETE"),
                ("affected", "AFFECTED"),
            ):
                if path in plan.files[group]:
                    action = name
        states.setdefault(
            path,
            {
                "path": path,
                "title": plan.module_titles.get(path) or _base(path),
                "state": "ABSENT",
                "overlays": [],
                "features": [],
                "planned": None,
                "symbols": [],
                "removed_symbols": [],
                "pending_reasons": [],
                "excluded_reason": None,
                "python": path.endswith(".py"),
            },
        )
        states[path]["planned"] = action
        states[path]["planned_check"] = _planned_check(action, state["state"]) if action else None
    planned_symbols = {}
    for symbol in plan.symbols:
        planned_symbols.setdefault(symbol["key"].split("::", 1)[0], []).append(
            {
                "qualname": symbol["key"].split("::", 1)[1],
                "action": symbol["action"],
                "reason": symbol.get("reason", ""),
            }
        )
    for path, items in planned_symbols.items():
        if path in states:
            states[path]["planned_symbols"] = items

    prior = list(prior)
    for path, info in states.items():
        # Keep the history of every earlier Task for each file, so later Tasks never drop it.
        info["history"] = [
            {
                "task": earlier["header"]["task"],
                "state": earlier["files"].get(path, {}).get("state", "ABSENT"),
            }
            for earlier in prior
        ]
    gate = approval_gate(delta)
    pending_pairs = [
        {
            "from_key": r["from_key"],
            "to_key": r["to_key"],
            "from_file": r["from_key"].split("::", 1)[0],
            "to_file": r["to_key"].split("::", 1)[0],
            "reasons": [REASON_TEXT.get(code, code) for code in r.get("reasons", [])],
        }
        for r in delta["pending_confirmations"]
    ]
    prior_tasks = [
        {
            "task": earlier["header"]["task"],
            "commit_state": earlier["header"]["commit_state"],
            "host_test": earlier["header"]["host_test"],
            "gate": earlier["header"]["gate"]["status"],
            "out_of_plan": len(earlier["header"]["out_of_plan"]),
            "out_of_plan_files": list(earlier["header"]["out_of_plan"]),
            "pending": earlier["header"]["pending"],
        }
        for earlier in prior
    ]
    header = {
        "gate": gate,
        "task": plan.task,
        "title": plan.title,
        "commit_state": delta["commit_state"],
        "structure_state": delta["structure_state"],
        "host_test": delta["host_test"]["state"],
        "host_test_scope": delta["host_test"].get("scope"),
        "out_of_plan": delta["out_of_plan_files"],
        "pending": len(delta["pending_confirmations"]),
        "excluded": len(delta["excluded"]),
        "current_label": delta["current"]["label"],
        "base_label": delta["base"]["label"],
    }
    edges_summary = {
        "matched": sorted(f"{s} → {d}" for (s, d), st in actual_status.items() if st == "MATCHED"),
        "unplanned": sorted(
            f"{s} → {d}" for (s, d), st in actual_status.items() if st == "UNPLANNED"
        ),
        "missing": sorted(f"{s} → {d}" for s, d in missing),
        "neutral": sorted(f"{s} → {d}" for (s, d), st in actual_status.items() if st == "NEUTRAL"),
    }
    return {
        "header": header,
        "alerts": {
            "unattributed_out_of_plan": unattributed,
            "pending_pairs": pending_pairs,
            "gate": gate,
        },
        "prior_tasks": prior_tasks,
        "files": states,
        "tabs": {"novice": novice, "planned": planned},
        "edges_summary": edges_summary,
        "symbols": delta["symbols"],
        "identity_events": delta["identity_events"],
        "pending_confirmations": delta["pending_confirmations"],
        "excluded": delta["excluded"],
        "missing_planned": delta["missing_planned"],
        "legend": {
            "states": STATE_STYLE,
            "overlays": OVERLAY_STYLE,
            "planned": PLANNED_ACTION_STYLE,
        },
    }


def _style_attrs(style):
    dash = {
        "solid": "",
        "dashed": ' stroke-dasharray="7 4"',
        "dotted": ' stroke-dasharray="2 4"',
        "double": "",
    }[style["border"]]
    return style["fill"], style["stroke"], dash


def _node_svg(node_id, coords, meta, style, chip, sub_lines, subtitle=None, kind="file"):
    x, y = coords
    fill, stroke, dash = _style_attrs(style)
    width = 4 if style.get("border") == "double" else 2
    parts = [
        f'<g class="node" data-id="{html.escape(node_id)}" data-kind="{kind}" tabindex="0" role="button" aria-label="{html.escape(meta["title"])}">'
    ]
    parts.append(
        f'<rect x="{x}" y="{y}" width="{NODE_W}" height="{NODE_H}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="{width}"{dash}/>'
    )
    if style.get("border") == "double":
        parts.append(
            f'<rect x="{x + 4}" y="{y + 4}" width="{NODE_W - 8}" height="{NODE_H - 8}" rx="7" fill="none" stroke="{stroke}" stroke-width="1"/>'
        )
    if chip:
        parts.append(
            f'<text x="{x + 12}" y="{y + 18}" class="chip" fill="{stroke}">{html.escape(chip)}</text>'
        )
    parts.append(f'<text x="{x + 12}" y="{y + 38}" class="ttl">{html.escape(meta["title"])}</text>')
    parts.append(
        f'<text x="{x + 12}" y="{y + 55}" class="sub">{html.escape(node_id if subtitle is None else subtitle)}</text>'
    )
    for index, line in enumerate(sub_lines[:2]):
        parts.append(
            f'<text x="{x + 12}" y="{y + 72 + index * 15}" class="sub2">{html.escape(line)}</text>'
        )
    parts.append("</g>")
    return "".join(parts)


def _arrow(src_xy, dst_xy, status):
    sx, sy = src_xy[0] + NODE_W / 2, src_xy[1] + NODE_H
    dx, dy = dst_xy[0] + NODE_W / 2, dst_xy[1]
    colour = {
        "MATCHED": "#374151",
        "NEUTRAL": "#6b7280",
        "UNPLANNED": "#b91c1c",
        "MISSING": "#9ca3af",
        "EXISTS": "#374151",
        "NOT_YET": "#9ca3af",
        "DIRECT": "#374151",
        "DIRECT_UNPLANNED": "#b91c1c",
        "INFERRED": "#6b7280",
    }[status]
    dash = ' stroke-dasharray="6 5"' if status in ("UNPLANNED", "MISSING", "NOT_YET") else ""
    if status == "INFERRED":
        dash = ' stroke-dasharray="3 4"'
    return (
        f'<path d="M{sx},{sy} C{sx},{sy + 30} {dx},{dy - 30} {dx},{dy}" fill="none" stroke="{colour}" '
        f'stroke-width="1.8"{dash} marker-end="url(#arrow)"/>'
    )


def _chip(state_key):
    style = STATE_STYLE[state_key]
    return f"{style['symbol']} {style['short']}"


def _svg(tab, node_fn, label):
    parts = [
        f'<svg viewBox="0 0 {tab["width"]} {tab["height"]}" width="{tab["width"]}" height="{tab["height"]}" role="img" aria-label="{html.escape(label)}">',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker></defs>',
    ]
    coords = {k: tuple(v) for k, v in tab["coords"].items()}
    for edge in tab["edges"]:
        if edge["src"] in coords and edge["dst"] in coords:
            parts.append(_arrow(coords[edge["src"]], coords[edge["dst"]], edge["status"]))
    for node in tab["nodes"]:
        parts.append(node_fn(node, coords[node]))
    parts.append("</svg>")
    return "".join(parts)


def _brief(text, limit):
    return text if len(text) <= limit else text[:limit] + "…"


def _feature_node(fid, xy, info):
    style = NOVICE_STYLE[info["marker"]]
    chip = f"{style['symbol']} {style['label']}"
    lines = []
    if info.get("git"):
        lines.append(f"Git：{info['git']}")
    flags = list(info.get("scope") or [])
    if info.get("identity_pending"):
        flags.append(f"待確認對應 {info['identity_pending']} 組")
    if flags:
        lines.append(_brief("；".join(flags), 17))
    return _node_svg(
        fid,
        xy,
        {"title": info["title"]},
        style,
        chip,
        lines,
        subtitle=f"{len(info['files'])} 個檔案",
        kind="feature",
    )


def _planned_node(path, xy, files):
    info = files[path]
    action = info.get("planned")
    style = PLANNED_ACTION_STYLE.get(
        action, {"fill": "#f9fafb", "stroke": "#6b7280", "border": "solid"}
    )
    style = {**style, "border": style.get("border", "solid")}
    chip = PLANNED_ACTION_STYLE[action]["label"] if action else "計畫依賴（未列為變更）"
    lines = [info.get("planned_check") or ""]
    return _node_svg(path, xy, {"title": info["title"]}, style, chip, lines)


def _engineer_html(data):
    rows = []
    for path, info in sorted(data["files"].items()):
        state = STATE_STYLE[info["state"]]["label"]
        overlays = "、".join(OVERLAY_STYLE[o]["label"] for o in info["overlays"]) or "—"
        symbol_lines = []
        for sym in info["symbols"]:
            source = sym.get("source") or {}
            where = (
                f"{source.get('lines', ['', ''])[0]}–{source.get('lines', ['', ''])[1]}"
                if source
                else "—"
            )
            symbol_lines.append(
                f"<li><code>{html.escape(sym['qualname'])}</code> · {sym['marker']} · {sym['structure']} · "
                f"{sym['commit']} · nid {html.escape(str(sym.get('nid') or '—'))} · 行 {where}"
                + (
                    f" · 來源 <code>{html.escape(sym['from_key'])}</code>"
                    if sym.get("from_key")
                    else ""
                )
                + "</li>"
            )
        for name in info.get("removed_symbols", []):
            symbol_lines.append(f"<li><code>{html.escape(name)}</code> · REMOVE（歷史保留）</li>")
        reasons = "、".join(info.get("pending_reasons", [])) or "—"
        rows.append(
            f"<tr><td><code>{html.escape(path)}</code></td><td>{state}</td><td>{html.escape(overlays)}</td>"
            f"<td>{html.escape(info.get('planned') or '—')}</td><td><ul>{''.join(symbol_lines) or '<li>—</li>'}</ul></td>"
            f"<td>{html.escape(reasons)}</td></tr>"
        )
    identity = []
    for event in data["identity_events"]:
        if event["type"] == "NEW":
            continue
        reasons = "、".join(event.get("reasons", [])) or "—"
        identity.append(
            f"<tr><td>{html.escape(event['type'])}</td><td>{html.escape(event['status'])}</td>"
            f"<td><code>{html.escape(str(event.get('from_key') or '—'))}</code></td>"
            f"<td><code>{html.escape(str(event.get('to_key') or '—'))}</code></td>"
            f"<td>{html.escape(str(event.get('score') if event.get('score') is not None else '—'))}</td>"
            f"<td>{html.escape(reasons)}</td></tr>"
        )
    edges = data["edges_summary"]

    def edge_list(items):
        return "".join(f"<li><code>{html.escape(i)}</code></li>" for i in items) or "<li>—</li>"

    excluded = (
        "".join(
            f"<li><code>{html.escape(e['path'])}</code> · {html.escape(e['reason'])}（未讀內容、未進入圖）</li>"
            for e in data["excluded"]
        )
        or "<li>—</li>"
    )
    missing = (
        "".join(f"<li>{html.escape(str(m))}</li>" for m in data["missing_planned"]) or "<li>—</li>"
    )
    return f"""
{_raw_states_html(data)}
<h2>檔案與符號</h2>
<table><thead><tr><th>檔案</th><th>實際狀態</th><th>標記</th><th>計畫</th><th>符號（標記 · 結構 · commit · nid · 行）</th><th>待確認原因</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
<h2>依賴對帳</h2>
<div class="cols">
<div><h3>符合計畫</h3><ul>{edge_list(edges["matched"])}</ul></div>
<div><h3>未規劃的依賴</h3><ul>{edge_list(edges["unplanned"])}</ul></div>
<div><h3>規劃有、實作無</h3><ul>{edge_list(edges["missing"])}</ul></div>
<div><h3>無計畫依賴可比對</h3><ul>{edge_list(edges["neutral"])}</ul></div>
</div>
<h2>身分事件（身分對應的證據）</h2>
<table><thead><tr><th>類型</th><th>狀態</th><th>來自</th><th>到</th><th>分數</th><th>原因代碼</th></tr></thead>
<tbody>{"".join(identity) or '<tr><td colspan="6">—</td></tr>'}</tbody></table>
<h2>排除的敏感檔案</h2><ul>{excluded}</ul>
<h2>計畫中未實作的項目</h2><ul>{missing}</ul>
"""


_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, "PingFang TC", "Noto Sans TC", sans-serif; color: #111827; background: #f9fafb; }
header { background: #111827; color: #f9fafb; padding: 14px 20px; }
header h1 { font-size: 1.15rem; margin: 0 0 8px; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; font-size: .85rem; }
.chip-box { background: #374151; border-radius: 999px; padding: 3px 10px; }
.chip-box.warn { background: #9a3412; } .chip-box.ok { background: #166534; } .chip-box.bad { background: #991b1b; }
nav.tabs { display: flex; gap: 6px; padding: 10px 20px 0; border-bottom: 1px solid #d1d5db; background: #fff; position: sticky; top: 0; z-index: 2; flex-wrap: wrap; }
nav.tabs button { border: 1px solid #d1d5db; border-bottom: none; background: #f3f4f6; padding: 8px 14px; border-radius: 8px 8px 0 0; font-size: .92rem; cursor: pointer; }
nav.tabs button[aria-selected="true"] { background: #fff; font-weight: 700; }
main { display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; padding: 16px 20px; }
.tab { display: none; min-width: 0; } .tab.active { display: block; }
.canvas { overflow: auto; background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 8px; }
svg text { font-family: inherit; } svg .ttl { font-size: 14px; font-weight: 700; fill: #111827; }
svg .sub { font-size: 11px; fill: #4b5563; } svg .sub2 { font-size: 11px; fill: #374151; }
svg .chip { font-size: 11px; font-weight: 700; }
svg .node { cursor: pointer; } svg .node:focus rect, svg .node:hover rect { stroke-width: 3; }
aside#detail { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 10px 14px; font-size: .9rem; }
aside#detail .kv { display: flex; flex-wrap: wrap; gap: 4px 18px; }
aside#detail h3 { margin: 0 0 6px; font-size: 1rem; } aside#detail ul { padding-left: 18px; margin: 4px 0; }
.legend { padding: 0 20px 12px; font-size: .88rem; display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 6px 16px; }
.legend .sw { display: inline-block; width: 26px; height: 16px; border-radius: 4px; vertical-align: middle; margin-right: 6px; border: 2px solid; }
.engineer { padding: 16px 20px; background: #fff; margin: 0 20px 16px; border-radius: 10px; border: 1px solid #e5e7eb; overflow: auto; }
table { border-collapse: collapse; width: 100%; font-size: .85rem; margin-bottom: 14px; }
th, td { border: 1px solid #e5e7eb; padding: 6px 8px; vertical-align: top; text-align: left; }
th { background: #f9fafb; } td ul { margin: 0; padding-left: 16px; }
.cols { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
footer { padding: 12px 20px 22px; color: #4b5563; font-size: .82rem; }
.alerts { display: grid; gap: 10px; margin-bottom: 12px; font-size: .9rem; }
.alert, .gate, .history { background: #fff; border: 2px solid #9ca3af; border-radius: 10px; padding: 10px 14px; }
.alert.bad { border-color: #b91c1c; } .alert.pending { border-color: #6b21a8; border-style: double; border-width: 4px; }
.gate { border-color: #374151; } .alert ul, .gate ul { margin: 4px 0; padding-left: 18px; }
button.link { background: none; border: none; padding: 0; color: #1d4ed8; text-decoration: underline; cursor: pointer; font: inherit; }
.hint { color: #4b5563; font-size: .85rem; margin: 0; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; margin-top: 8px; }
.card { background: #374151; border-radius: 10px; padding: 7px 10px; min-width: 0; overflow-wrap: anywhere; color: #f9fafb; }
.card .k { font-size: .74rem; opacity: .85; } .card .v { font-size: .92rem; font-weight: 600; line-height: 1.35; }
.card.ok { background: #166534; } .card.warn { background: #9a3412; } .card.bad { background: #991b1b; } .card.neutral { background: #1f2937; }
.canvas svg { max-width: 100%; height: auto; }
.node.focus rect { stroke-width: 4; }
.novice-head { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 10px 14px; margin-bottom: 10px; }
.task-line, .summary { margin: 2px 0; } .summary { font-size: .95rem; }
.nlegends { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: .85rem; margin: 6px 0; }
.nlegend { display: inline-flex; align-items: center; gap: 4px; }
.nsw { display: inline-block; min-width: 24px; text-align: center; border: 2px solid; border-radius: 6px; padding: 0 4px; font-weight: 700; }
.status-detail { background: #fff; border: 1px solid #d1d5db; border-radius: 10px; padding: 8px 14px; margin-top: 10px; }
.status-detail summary { cursor: pointer; font-weight: 600; }
.unresolved { border: 2px solid #b45309; border-radius: 10px; padding: 8px 12px; margin: 8px 0; }
.unresolved ul { margin: 4px 0; padding-left: 18px; }
.arrowlegend { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: .84rem; margin: 6px 0 2px; }
.arrowlegend span { display: inline-flex; align-items: center; gap: 6px; }
.arrowlegend svg { flex: none; }
.novice-attention { margin: 10px 0; display: grid; gap: 8px; }
@media (max-width: 1099px) {
  main { display: flex; flex-direction: column; }
  .tab { order: 1; }
  aside#detail { order: 2; }
  #tab-novice.active { display: flex; flex-direction: column; }
  #tab-novice .novice-head { order: 0; }
  #tab-novice .canvas { order: 1; }
  #tab-novice .novice-attention { order: 2; }
  #tab-novice .status-detail { order: 3; }
}
@media (min-width: 1100px) {
  main { grid-template-columns: minmax(0, 1fr) minmax(280px, 340px); }
  aside#detail { grid-column: 2; grid-row: 1; position: sticky; top: 16px; align-self: start; max-height: calc(100vh - 32px); overflow: auto; }
  .tab { grid-column: 1; grid-row: 1; }
}
"""

_SCRIPT = """
(function () {
  var data = JSON.parse(document.getElementById('archgraph-data').textContent);
  var detail = document.getElementById('detail');
  function clearDetail(title) {
    detail.innerHTML = '';
    var h = document.createElement('h3'); h.textContent = title; detail.appendChild(h);
  }
  function line(text) { var p = document.createElement('div'); p.textContent = text; detail.appendChild(p); }
  function stateLabel(state) { return data.legend.states[state] ? data.legend.states[state].label : state; }
  function highlight(id) {
    document.querySelectorAll('svg .node.focus').forEach(function (n) { n.classList.remove('focus'); });
    var nodes = document.querySelectorAll('svg .node');
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i].getAttribute('data-id') === id) { nodes[i].classList.add('focus'); return nodes[i]; }
    }
    return null;
  }
  function activate(tabId) {
    var button = document.querySelector('nav.tabs button[data-tab="' + tabId + '"]');
    if (button) { button.click(); }
  }
  function showFile(id) {
    var f = data.files[id];
    if (!f) { return; }
    clearDetail(f.title);
    line('檔案：' + f.path);
    line('實際狀態：' + stateLabel(f.state));
    line(f.planned ? data.legend.planned[f.planned].label : '計畫：未列為變更（不在計畫的變更清單內）');
    if (f.planned_check) { line('計畫對照：' + f.planned_check); }
    if (f.overlays.length) { line('標記：' + f.overlays.map(function (o) { return data.legend.overlays[o].label; }).join('、')); }
    if (f.features.length) { line('功能：' + f.features.join('、')); }
    if (f.excluded_reason) { line('排除原因：' + f.excluded_reason + '（未讀內容）'); }
    if (f.pending_pairs && f.pending_pairs.length) {
      f.pending_pairs.forEach(function (pp) { line('待確認對應：' + pp.from_key + ' ↔ ' + pp.to_key + '（尚未人工確認' + (pp.reasons.length ? '；' + pp.reasons.join('、') : '') + '）'); });
    }
    if (f.state !== 'EXCLUDED' && f.overlays.indexOf('OUT_OF_PLAN') >= 0) {
      line(f.features.length ? '超出計畫：已歸屬功能 ' + f.features.join('、') : '超出計畫：未歸屬任何功能（列於頁首的專案層級警告）');
    }
    if (f.history && f.history.length) {
      line('歷程：' + f.history.map(function (h) { return h.task + ' ' + h.state; }).join(' → ') + '（本次 ' + stateLabel(f.state) + '）');
    }
    if (f.symbols.length) {
      var ul = document.createElement('ul');
      f.symbols.forEach(function (s) { var li = document.createElement('li'); li.textContent = s.qualname + ' · ' + s.marker + ' · ' + s.structure; ul.appendChild(li); });
      detail.appendChild(ul);
    }
    if (f.planned_symbols) {
      var pl = document.createElement('div'); pl.textContent = '計畫中的符號：'; detail.appendChild(pl);
      var pul = document.createElement('ul');
      f.planned_symbols.forEach(function (s) { var li = document.createElement('li'); li.textContent = s.qualname + ' · ' + s.action; pul.appendChild(li); });
      detail.appendChild(pul);
    }
  }
  function showFeature(id) {
    var f = data.features[id];
    if (!f) { return; }
    clearDetail(f.title);
    line(f.sentence);
    if (f.renamed_from) { line('原名：' + f.renamed_from); }
    line('Git 狀態（程式碼是否已 commit）：' + (f.git || (id === 'UNATTRIBUTED' ? '不適用' : '本功能沒有變更')));
    f.scope.forEach(function (s) { line('範圍：' + s); });
    line('待確認對應（尚未人工確認）：' + (f.identity_pending ? f.identity_pending + ' 組' : '無'));
    data.pending.forEach(function (pp) {
      var touches = f.files.some(function (x) { return x.path === pp.from_file || x.path === pp.to_file; });
      if (touches) { line('· ' + pp.from_key + ' ↔ ' + pp.to_key); }
    });
    line('驗證（主機測試）為整個 Task 的結果，見頁首。');
    line('涉及檔案：' + (f.files.length ? f.files.length + ' 個' : '無'));
    f.files.forEach(function (x) { line('· ' + x.path + '（' + stateLabel(x.state) + '）'); });
    data.edges.forEach(function (e) {
      if (e.src !== id && e.dst !== id) { return; }
      var other = data.features[e.src === id ? e.dst : e.src];
      var arrow = e.src === id ? '→ ' + other.title : '← ' + other.title;
      var kind = e.status === 'INFERRED' ? '推測性功能關係（非確認的功能依賴）'
        : (e.status === 'DIRECT_UNPLANNED' ? '直接證據，計畫外' : '直接證據');
      line('依賴 ' + arrow + '：' + kind);
      e.evidence.forEach(function (ev) { line('   檔案 import：' + ev); });
    });
    if (id === 'UNATTRIBUTED') { line('超出計畫，且無法歸屬任何功能；不會隱藏，需人工判斷是否接受或撤回。'); }
    var det = document.createElement('details');
    var sm = document.createElement('summary'); sm.textContent = '程式細節（展開）'; det.appendChild(sm);
    f.files.forEach(function (x) {
      var pe = document.createElement('div'); pe.textContent = x.path; det.appendChild(pe);
      var ul = document.createElement('ul');
      x.symbols.forEach(function (s) { var li = document.createElement('li'); li.textContent = s.qualname + ' · ' + s.marker + ' · ' + s.structure; ul.appendChild(li); });
      det.appendChild(ul);
    });
    detail.appendChild(det);
  }
  function focusFile(path) {
    var ids = data.feature_of_file[path];
    if (ids && ids.length) {
      activate('tab-novice');
      showFeature(ids[0]);
      highlight(ids[0]);
      var note = document.createElement('div');
      note.textContent = '定位：' + path + ' 屬於此功能。';
      detail.insertBefore(note, detail.children[1] || null);
    } else {
      showFile(path);
    }
  }
  document.querySelectorAll('[data-focus]').forEach(function (item) {
    item.addEventListener('click', function () { focusFile(item.getAttribute('data-focus')); });
  });
  document.querySelectorAll('.node').forEach(function (node) {
    var go = function () {
      var id = node.getAttribute('data-id');
      if (node.getAttribute('data-kind') === 'feature') { showFeature(id); } else { showFile(id); }
      highlight(id);
    };
    node.addEventListener('click', go);
    node.addEventListener('keydown', function (e) { if (e.key === 'Enter') { go(); } });
  });
  document.querySelectorAll('nav.tabs button').forEach(function (button) {
    button.addEventListener('click', function () {
      document.querySelectorAll('nav.tabs button').forEach(function (b) { b.setAttribute('aria-selected', 'false'); });
      document.querySelectorAll('.tab').forEach(function (t) { t.classList.remove('active'); });
      button.setAttribute('aria-selected', 'true');
      document.getElementById(button.getAttribute('data-tab')).classList.add('active');
    });
  });
  var hashTabs = { novice: 'tab-novice', planned: 'tab-planned', engineer: 'tab-engineer' };
  var wanted = hashTabs[location.hash.replace('#', '')];
  if (wanted) { activate(wanted); }
  if (data.features[data.default_node]) { showFeature(data.default_node); } else { showFile(data.default_node); }
})();
"""


def _short_scope(scope):
    """commit:<40-hex> is shortened for display; the full value stays in the JSON."""
    text = str(scope or "")
    if text.startswith("commit:") and len(text) > 19:
        return "commit:" + text[7:19]
    return text


def _counts_line(features):
    counts = {}
    for fid, info in features.items():
        if fid != UNATTRIBUTED_ID:
            counts[info["marker"]] = counts.get(info["marker"], 0) + 1
    parts = [
        f"{NOVICE_STYLE[m]['symbol']} {NOVICE_STYLE[m]['label']} {counts[m]}"
        for m in NOVICE_ORDER
        if counts.get(m)
    ]
    return " ｜ ".join(parts) or "沒有功能變動"


def _attention_html(data):
    """Warnings the reader must not miss: unattributed out-of-plan files and pending identity pairs."""
    alerts = data["alerts"]
    parts = []
    if alerts["unattributed_out_of_plan"]:
        items = "".join(
            f'<li><button class="link" data-focus="{html.escape(p)}">{html.escape(p)}</button></li>'
            for p in alerts["unattributed_out_of_plan"]
        )
        parts.append(
            '<div class="alert bad"><b>⚠ 超出計畫，且無法歸屬任何功能</b>'
            f"<ul>{items}</ul><p>這些檔案不會因為無法歸屬而隱藏。需要人工判斷是否接受或撤回（原型尚無接受機制）。</p></div>"
        )
    if alerts["pending_pairs"]:
        items = "".join(
            f'<li><button class="link" data-focus="{html.escape(pair["from_file"])}">{html.escape(pair["from_key"])}</button>'
            f' ↔ <button class="link" data-focus="{html.escape(pair["to_file"])}">{html.escape(pair["to_key"])}</button>'
            f'（{html.escape("、".join(pair["reasons"]) or "需人工確認")}）</li>'
            for pair in alerts["pending_pairs"]
        )
        parts.append(
            '<div class="alert pending"><b>待人工確認的新舊對應</b>'
            "<span>（尚未人工確認，未計入實際對應；點擊可定位到相關功能）</span>"
            f"<ul>{items}</ul></div>"
        )
    return "".join(parts)


def _status_details_html(data):
    """Task-level approval, prior-Task history and the unresolved items carried from earlier Tasks.

    The approval verdict is per Task and is not changed by earlier Tasks. Earlier unresolved items are
    listed beside it so a READY Task does not hide them.
    """
    gate = data["alerts"]["gate"]
    blockers = "".join(f"<li>{html.escape(b['text'])}</li>" for b in gate["blockers"]) or "<li>沒有阻擋項</li>"
    actions = "".join(f"<li>{html.escape(a['text'])}</li>" for a in gate["actions"]) or "<li>沒有待執行的動作</li>"
    unresolved = [
        f"<li>{html.escape(t['task'])}：{html.escape(path)}（超出計畫，目前原型沒有接受機制）</li>"
        for t in data["prior_tasks"]
        for path in t.get("out_of_plan_files", [])
    ]
    unresolved_html = (
        "<div class='unresolved'><b>前序未解事項</b>"
        "<p>這些事項來自前序 Task，不改變本 Task 的核准判定，但仍需人工決定。</p>"
        f"<ul>{''.join(unresolved)}</ul></div>"
        if unresolved
        else ""
    )
    rows = "".join(
        f"<tr><td>{html.escape(t['task'])}</td><td>{html.escape(t['commit_state'])}</td>"
        f"<td>{html.escape(t['host_test'])}</td><td>{html.escape(t['gate'])}</td>"
        f"<td>{t['out_of_plan']}</td><td>{t['pending']}</td></tr>"
        for t in data["prior_tasks"]
    )
    history = (
        "<div class='history'><b>前序 Task 歷程（保留，不被覆蓋）</b><table><thead><tr>"
        "<th>Task</th><th>提交</th><th>驗證</th><th>核准判定</th><th>超出計畫</th><th>待確認</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
        if data["prior_tasks"]
        else ""
    )
    verdict = GATE_NATURAL.get(gate["status"], gate["status"])
    return (
        '<details class="status-detail"><summary>本 Task 核准判定、前序歷程與定義（展開）</summary>'
        f'<div class="gate"><b>本 Task 核准判定：{html.escape(verdict)}</b>'
        f'<p>{html.escape(gate["definition"])}</p>'
        f"<div>阻擋項：<ul>{blockers}</ul></div><div>待執行動作：<ul>{actions}</ul></div></div>"
        f"{unresolved_html}{history}</details>"
    )


def _gate_reason(gate, header, prior):
    """Why this Task has its verdict, in one line. Earlier Tasks' unresolved items are named too."""
    reasons = []
    for blocker in gate["blockers"]:
        if blocker["code"] == "OUT_OF_PLAN":
            reasons.append(f"超出計畫 {len(header['out_of_plan'])} 個檔案")
        elif blocker["code"] == "PENDING_IDENTITY":
            reasons.append(f"待確認對應 {header['pending']} 組")
        elif blocker["code"] == "HOST_TEST_NOT_PASSED":
            reasons.append("主機測試未通過")
        else:
            reasons.append(blocker["code"])
    if any(t.get("out_of_plan_files") for t in prior):
        reasons.append("另有前序未解事項，見展開區")
    return "（" + "、".join(reasons) + "）" if reasons else ""


def _cards_html(header, prior):
    gate = header["gate"]
    commit_text = "尚未 commit（工作區修改）" if header["commit_state"] == "UNCOMMITTED" else "已 commit"
    host = f"{HOST_LABEL.get(header['host_test'], header['host_test'])}（範圍 {_short_scope(header['host_test_scope'])}）"
    structure_bad = header["structure_state"] in ("BLOCKED_BY_SCOPE", "FLAGGED_OUT_OF_SCOPE")
    current = header["current_label"]
    where = "工作區（尚未 commit）" if current == "worktree" else f"commit 快照：{_short_scope(current)}"
    cards = [
        ("提交（Git）", commit_text, "warn" if header["commit_state"] == "UNCOMMITTED" else "ok"),
        ("計畫比對", STRUCTURE_LABEL.get(header["structure_state"], header["structure_state"]),
         "bad" if structure_bad else "ok"),
        ("驗證（主機測試）", host, "ok" if header["host_test"] == "PASSED" else "bad"),
        ("本 Task 核准判定", GATE_NATURAL.get(gate["status"], gate["status"]) + _gate_reason(gate, header, prior),
         {"READY": "ok", "NEEDS_COMMIT": "warn"}.get(gate["status"], "bad")),
        ("需注意", f"超出計畫 {len(header['out_of_plan'])} ｜ 待確認 {header['pending']} ｜ 已排除 {header['excluded']}",
         "warn" if header["out_of_plan"] or header["pending"] else "ok"),
        ("目前狀態", where, "neutral"),
    ]
    items = "".join(
        f'<div class="card {cls}"><div class="k">{html.escape(k)}</div><div class="v">{html.escape(v)}</div></div>'
        for k, v, cls in cards
    )
    return f'<div class="cards">{items}</div>'


def _evolution_html(features):
    steps = [
        f"{info['renamed_from']} → {info['title']}"
        for fid, info in features.items()
        if fid != UNATTRIBUTED_ID and info.get("renamed_from")
    ]
    if not steps:
        return ""
    return "<p class=\"summary\"><b>演進：</b>" + html.escape("；".join(steps)) + "</p>"


def _novice_section_html(data):
    tab = data["tabs"]["novice"]
    header = data["header"]
    legend = "".join(
        f'<span class="nlegend"><b class="nsw" style="border-color:{NOVICE_STYLE[m]["stroke"]};'
        f'background:{NOVICE_STYLE[m]["fill"]}">{NOVICE_STYLE[m]["symbol"]}</b>{NOVICE_STYLE[m]["label"]}</span>'
        for m in NOVICE_ORDER
    )
    svg = _svg(tab, lambda n, xy: _feature_node(n, xy, tab["features"][n]), "功能架構圖")
    arrows = (
        '<div class="arrowlegend"><span><svg width="34" height="10"><line x1="0" y1="5" x2="34" y2="5" '
        'stroke="#374151" stroke-width="2"/></svg> 直接證據：兩端檔案各只屬於一個功能</span>'
        '<span><svg width="34" height="10"><line x1="0" y1="5" x2="34" y2="5" stroke="#6b7280" '
        'stroke-width="2" stroke-dasharray="3 4"/></svg> 推測性功能關係：至少一端檔案屬於多個功能，'
        "僅由檔案 import 推導，不代表已確認的功能依賴</span>"
        '<span><svg width="34" height="10"><line x1="0" y1="5" x2="34" y2="5" stroke="#b91c1c" '
        'stroke-width="2"/></svg> 計畫外的直接依賴</span></div>'
    )
    return (
        f'<div class="novice-head"><p class="task-line"><b>{html.escape(header["task"])}</b>：'
        f'{html.escape(header["title"])}</p>'
        f'<p class="summary"><b>功能變化：</b>{html.escape(_counts_line(tab["features"]))}</p>'
        f"{_evolution_html(tab['features'])}"
        f'<div class="nlegends">{legend}</div>{arrows}</div>'
        f'<div class="canvas">{svg}</div>'
        f'<div class="novice-attention">{_attention_html(data)}</div>'
        f"{_status_details_html(data)}"
        '<details class="status-detail"><summary>說明：這裡是實際程式狀態</summary>'
        '<p class="hint">這裡是實際程式狀態；已核准計畫與實際的對照請看「計畫（Planned）」分頁。'
        "點擊功能可看 Git 狀態、超出計畫、待確認對應、推測性與直接依賴的證據，以及程式細節。"
        "驗證（主機測試）是整個 Task 的結果，見頁首，不屬於單一功能。</p></details>"
    )


def _raw_states_html(data):
    header = data["header"]
    gate = header["gate"]
    return (
        "<h2>原始技術狀態（工程視圖專用）</h2><table><tbody>"
        f"<tr><th>commit_state</th><td>{html.escape(header['commit_state'])}</td></tr>"
        f"<tr><th>structure_state</th><td>{html.escape(header['structure_state'])}</td></tr>"
        f"<tr><th>host_test</th><td>{html.escape(header['host_test'])} · scope {html.escape(str(header['host_test_scope']))}</td></tr>"
        f"<tr><th>gate.status</th><td>{html.escape(gate['status'])}</td></tr>"
        f"<tr><th>current / base</th><td>{html.escape(header['current_label'])} / {html.escape(header['base_label'])}</td></tr>"
        "</tbody></table>"
    )


def render_html(data):
    header = data["header"]
    tab = data["tabs"]["novice"]
    legend_states = "".join(
        f'<div><span class="sw" style="background:{s["fill"]};border-color:{s["stroke"]};border-style:{"dashed" if s["border"] == "dashed" else ("dotted" if s["border"] == "dotted" else "solid")}"></span>'
        f"<b>{html.escape(s['symbol'])}</b> {html.escape(s['label'])}</div>"
        for s in STATE_STYLE.values()
    )
    legend_overlays = "".join(
        f'<div><span class="sw" style="border-color:{o["color"]};background:#fff"></span>{html.escape(o["label"])}</div>'
        for o in OVERLAY_STYLE.values()
    )
    planned_svg = _svg(
        data["tabs"]["planned"], lambda p, xy: _planned_node(p, xy, data["files"]), "規劃架構圖"
    )
    default_node = next(iter(tab["nodes"]), "")
    payload = {
        "files": data["files"],
        "legend": data["legend"],
        "default_node": default_node,
        "features": tab["features"],
        "feature_of_file": tab["feature_of_file"],
        "edges": tab["edges"],
        "pending": data["alerts"]["pending_pairs"],
    }
    payload_json = (
        json.dumps(payload, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(header["task"])} 架構圖（原型）</title>
<style>{_CSS}</style></head>
<body>
<header>
<h1>{html.escape(header["task"])}：{html.escape(header["title"])}（原型，未經新手使用者測試）</h1>
{_cards_html(header, data["prior_tasks"])}
</header>
<nav class="tabs" role="tablist">
<button data-tab="tab-novice" aria-selected="true">功能架構（實際，新手）</button>
<button data-tab="tab-planned" aria-selected="false">計畫（Planned）</button>
<button data-tab="tab-engineer" aria-selected="false">工程視圖（原始狀態與對帳）</button>
</nav>
<main>
<aside id="detail" aria-live="polite"><h3>點擊節點</h3><div>功能會分別顯示 Git 狀態、範圍、待確認對應與涉及檔案；檔案會顯示實際狀態與計畫對照。</div></aside>
<section id="tab-novice" class="tab active">{_novice_section_html(data)}</section>
<section id="tab-planned" class="tab"><p class="hint">計畫分頁是已核准計畫的檔案與依賴，與實際狀態分開顯示。</p><div class="canvas">{planned_svg}</div></section>
<section id="tab-engineer" class="tab"><div class="engineer">{_engineer_html(data)}</div></section>
</main>
<section class="legend"><div><b>工程圖例：實際狀態</b>{legend_states}</div><div><b>標記</b>{legend_overlays}</div></section>
<footer>這是單一任務的架構原型，呈現實際狀態、計畫對照與證據。圖由確定性程式從圖與差異計算，未由模型繪製；頁面不含原始碼或敏感內容。本頁只驗證畫面的正確性與完整性，不作為新手使用性的結論。</footer>
<script type="application/json" id="archgraph-data">{payload_json}</script>
<script>{_SCRIPT}</script>
</body></html>
"""
