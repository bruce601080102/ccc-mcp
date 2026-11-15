"""Derive P0B study materials from the pipeline outputs of build_cases.py.

Reads <scratch>/<case>/TASK-000N/{novice,architecture}.json, <scratch>/<case>/plans/*.json
and <scratch>/<case>/results.json, then writes, per case, into materials/<case>/:

  facts.json      raw fact sheet shared by every condition (no ADD/MODIFY labels)
  keys.json       answer key and scoring metadata, derived from the same outputs
  A/TASK-000N.html  condition A: the ccc-mcp view (copied from the pipeline output)
  C/TASK-000N.html  condition C: a plain static architecture diagram of that Task's state
                    (no change markers, no evidence labels)

Condition B (the text summary) is written by hand and is NOT produced here. See
parity_check.py, which verifies every key fact is present in every condition.

Usage: python build_materials.py <scratch-dir>
"""

import html
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MATERIALS = HERE / "materials"
CASES = ["case1_notes", "case2_bookmarks", "case3_todos"]
TASKS = ["TASK-0001", "TASK-0002", "TASK-0003"]
UNCOMMITTED_TEXT = "尚未 commit"
UNPLANNED_TEXT = "有未規劃的程式變更"
SKIP_STATES = {"EXCLUDED", "REMOVED"}
# Shown to testers as a plain row. The distractor status lives only in the keys (all-NO row) and in
# these scripts, never in the participant-facing wording.
DISTRACTOR = "匯出 CSV"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def plan_features(plan):
    return {f["id"]: f for f in plan["features"]}


def task_facts(case_dir, task, result):
    arch = load(case_dir / task / "architecture.json")
    novice = load(case_dir / task / "novice.json")
    plan = load(case_dir / "plans" / f"plan{task[-1]}.json")
    files = [
        {"path": path, "git_state": meta["state"]}
        for path, meta in sorted(arch["files"].items())
        # Git-level changes only: added, modified or removed. EXISTING and AFFECTED are not
        # changes; EXCLUDED files are listed separately and never read.
        if meta["state"] in {"ADDED", "MODIFIED", "REMOVED"}
    ]
    return {
        "title": novice["title"],
        "commit_state": result["commit_state"],
        "structure_state": result["structure_state"],
        "host_test": {"state": result["host_test_state"], "scope": arch["header"]["host_test_scope"]},
        "changed_files": files,
        "plan_features": [
            {"id": f["id"], "title": f["title_plain"], "members": sorted({m.split("::")[0] for m in f["members"]})}
            for f in plan["features"]
        ],
        "out_of_plan_files": result["out_of_plan_files"],
        "pending_confirmations": [
            {"from": p["from_key"], "to": p["to_key"]} for p in result["pending_confirmations"]
        ],
        "excluded": [{"path": e["path"], "reason": e["reason"]} for e in result["excluded"]],
        "_novice": novice,
        "_arch": arch,
        "_plan": plan,
    }


def score_features_t3(facts_by_task):
    """Axes for every feature visible at TASK-0003."""
    t3 = facts_by_task["TASK-0003"]
    novice = t3["_novice"]
    plan = t3["_plan"]
    members_by_id = {f["id"]: f["members"] for f in plan["features"]}
    symbols = {s["key"]: s for s in t3["_arch"]["symbols"]}
    # Pending mapping is matched at FILE level: a feature is linked to a pending
    # old/new correspondence when one of its endpoints lives in a file the feature owns.
    pending_files = {k.split("::")[0] for p in t3["pending_confirmations"] for k in (p["from"], p["to"])}
    rows = []
    for feature in novice["features"]:
        if feature["marker"] == "REMOVE":
            continue
        members = members_by_id.get(feature["id"], [])
        member_files = {m.split("::")[0] for m in members}
        member_symbols = [symbols[m] for m in members if m in symbols]
        axes = {
            "a_uncommitted_change": feature["git"] == UNCOMMITTED_TEXT
            or any(s["commit_state"] != "COMMITTED" for s in member_symbols),
            "b_host_test_passed": t3["host_test"]["state"] == "PASSED",
            "d_pending_mapping": bool(member_files & pending_files),
        }
        rows.append({"id": feature["id"], "title": feature["title"], "marker": feature["marker"], "axes": axes})
    return rows


def dependents_of(arch, target_file):
    """Files that depend on target_file, from the evidence edges the pipeline recorded."""
    edges = []
    for bucket in ("matched", "unplanned", "missing", "neutral"):
        edges.extend(arch["edges_summary"].get(bucket, []))
    result = []
    for edge in edges:
        source, _, target = (part.strip() for part in edge.partition("→"))
        if target == target_file and source != target_file:
            result.append(source)
    return sorted(set(result))


def search_file(plan3):
    for feature in plan3["features"]:
        if feature["title_plain"].startswith("搜尋"):
            files = sorted({m.split("::")[0] for m in feature["members"]})
            impl = [f for f in files if not f.endswith("cli.py")]
            return impl[0]
    raise ValueError("no search feature in plan")


def build_keys(facts_by_task):
    t3 = facts_by_task["TASK-0003"]
    feats = score_features_t3(facts_by_task)
    impl = search_file(t3["_plan"])
    keys = {
        "G1_current_features": sorted(f["title"] for f in feats),
        "G2_search_implementation_file": impl,
        "T1_added": sorted(f["title"] for f in facts_by_task["TASK-0001"]["_novice"]["features"] if f["marker"] == "ADD"),
        "T1_modified": sorted(f["title"] for f in facts_by_task["TASK-0001"]["_novice"]["features"] if f["marker"] == "MODIFY"),
        "T2_added": sorted(f["title"] for f in facts_by_task["TASK-0002"]["_novice"]["features"] if f["marker"] == "ADD"),
        "T2_modified": sorted(f["title"] for f in facts_by_task["TASK-0002"]["_novice"]["features"] if f["marker"] == "MODIFY"),
        "T3_added": sorted(f["title"] for f in t3["_novice"]["features"] if f["marker"] == "ADD"),
        "T3_modified": sorted(f["title"] for f in t3["_novice"]["features"] if f["marker"] == "MODIFY"),
        # Feature-level attribution of out-of-plan files is not evidenced for every file
        # (schema.sql is referenced by no code), so out-of-plan is asked at FILE level.
        "O1_out_of_plan_files": sorted(t3["out_of_plan_files"]),
        "S_grid_T3": {f["title"]: f["axes"] for f in feats},
        # Approval gate (archgraph/gate.py): blockers and pending actions are separate.
        # "Not committed" is a pending action, never a blocker on its own.
        "E1_ready_to_merge": {
            "answer": "NO",
            "blocking_reasons": sorted(
                reason
                for reason, present in (
                    ("超出計畫", bool(t3["out_of_plan_files"])),
                    ("待確認", bool(t3["pending_confirmations"])),
                )
                if present
            ),
            "pending_actions": ["需先 commit"] if t3["commit_state"] == "UNCOMMITTED" else [],
            "not_a_reason": [
                "主機測試通過（它只代表工作區副本通過，不代表已 commit 或已驗收）",
                "尚未 commit（它是待執行的動作，不是阻擋項）",
            ],
        },
        "I1_dependents_of_search": dependents_of(t3["_arch"], impl),
    }
    # A distractor that exists in no Task. Its correct answer is "no" on every axis, which
    # stops "answer yes to everything" from scoring well on the grid.
    keys["S_grid_T3"][DISTRACTOR] = {
        "a_uncommitted_change": False,
        "b_host_test_passed": False,
        "d_pending_mapping": False,
    }
    return keys


def fact_section(facts_task):
    """Readable fact table for one Task: the same facts every condition is given. Raw facts only:
    no ADD/MODIFY interpretation and no merge verdict."""
    def esc(value):
        return html.escape(str(value))

    commit = facts_task["commit_state"]
    rows = [
        ("提交狀態", f"{'已 commit' if commit == 'COMMITTED' else '尚未 commit'}（{esc(commit)}）"),
        ("主機測試", f"{esc(facts_task['host_test']['state'])}，範圍 {esc(facts_task['host_test']['scope'])}"),
        ("計畫比對結構", esc(facts_task["structure_state"])),
        (
            "變更檔案（Git 層級）",
            "；".join(f"{esc(c['path'])}（{esc(c['git_state'])}）" for c in facts_task["changed_files"]) or "無",
        ),
        (
            "不在已核准計畫內的變更檔案",
            "、".join(esc(p) for p in facts_task["out_of_plan_files"]) or "無",
        ),
        (
            "待確認的新舊對應",
            "；".join(f"{esc(p['from'])} ↔ {esc(p['to'])}" for p in facts_task["pending_confirmations"]) or "無",
        ),
        (
            "排除的檔案（未讀內容）",
            "；".join(f"{esc(e['path'])}（{esc(e['reason'])}）" for e in facts_task["excluded"]) or "無",
        ),
    ]
    body = "".join(f"<tr><th>{esc(k)}</th><td>{v}</td></tr>" for k, v in rows)
    plan = "".join(
        f"<li>{esc(f['id'])} {esc(f['title'])}：{esc('、'.join(f['members']))}</li>"
        for f in facts_task["plan_features"]
    )
    return (
        "<section class='facts'><h2>事實表（本 Task）</h2>"
        f"<table><tbody>{body}</tbody></table>"
        f"<h3>已核准計畫的功能與檔案</h3><ul>{plan}</ul></section>"
    )


def static_html(title, arch, task, facts_task):
    files = [
        (path, meta) for path, meta in sorted(arch["files"].items()) if meta["state"] not in SKIP_STATES
    ]
    folders = {}
    for path, _ in files:
        folder = path.split("/")[0] if "/" in path else "(根目錄)"
        folders.setdefault(folder, []).append(path)
    columns = []
    for folder, paths in sorted(folders.items()):
        boxes = "".join(f'<div class="box">{html.escape(p)}</div>' for p in paths)
        columns.append(f'<section class="col"><h3>{html.escape(folder)}</h3>{boxes}</section>')
    edges = []
    for bucket in ("matched", "unplanned", "missing", "neutral"):
        edges.extend(arch["edges_summary"].get(bucket, []))
    edge_items = "".join(f"<li>{html.escape(e)}</li>" for e in sorted(set(edges)))
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(task)} 靜態架構圖</title>
<style>
body{{font-family:-apple-system,"PingFang TC",sans-serif;margin:16px;color:#111;background:#fff}}
.cols{{display:flex;gap:16px;flex-wrap:wrap}}
.col{{min-width:200px;border:1px solid #999;padding:8px;border-radius:6px}}
.col h3{{margin:0 0 6px;font-size:15px}}
.box{{border:1px solid #555;border-radius:4px;padding:4px 6px;margin:4px 0;font-family:ui-monospace,monospace;font-size:13px;background:#f7f7f7}}
ul{{font-family:ui-monospace,monospace;font-size:13px}}
</style></head><body>
<h1>{html.escape(task)} 靜態架構圖</h1>
<p>此圖顯示此 Task 結束時的檔案與依賴，不標示變更、提交或測試狀態。</p>
<div class="cols">{''.join(columns)}</div>
<h2>依賴（來源 → 目標）</h2><ul>{edge_items}</ul>
{fact_section(facts_task)}
</body></html>"""


def public_facts_task(facts_task):
    return {k: v for k, v in facts_task.items() if not k.startswith("_")}


def export_case(case, scratch, out_root):
    case_dir = scratch / case
    results = load(case_dir / "results.json")
    facts_by_task = {t: task_facts(case_dir, t, results[t]) for t in TASKS}
    keys = build_keys(facts_by_task)

    dest = out_root / case
    (dest / "A").mkdir(parents=True, exist_ok=True)
    (dest / "C").mkdir(parents=True, exist_ok=True)
    for task in TASKS:
        shutil.copy(case_dir / task / "architecture.html", dest / "A" / f"{task}.html")
        arch = facts_by_task[task]["_arch"]
        (dest / "C" / f"{task}.html").write_text(
            static_html(case, arch, task, public_facts_task(facts_by_task[task])), encoding="utf-8"
        )

    public_facts = {
        "case": case,
        "說明": "共同事實表：三組條件都以這份事實為準。不含 ADD/MODIFY 等解讀。",
        "tasks": {
            task: {k: v for k, v in facts_by_task[task].items() if not k.startswith("_")}
            for task in TASKS
        },
    }
    (dest / "facts.json").write_text(json.dumps(public_facts, ensure_ascii=False, indent=2), encoding="utf-8")
    (dest / "keys.json").write_text(json.dumps(keys, ensure_ascii=False, indent=2), encoding="utf-8")
    return keys


if __name__ == "__main__":
    scratch = Path(sys.argv[1]).resolve()
    MATERIALS.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        keys = export_case(case, scratch, MATERIALS)
        print(case, json.dumps(keys, ensure_ascii=False)[:300])
