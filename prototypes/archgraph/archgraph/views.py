"""Two projections of one TaskDelta: a novice feature view and an engineer view.

Both are derived from the same delta. Neither re-draws anything with a model, and neither
contains source text. Colour is never the only signal: every marker has a symbol, a word,
and its own border style.
"""

import html

LEGEND = [
    {"marker": "ADD", "symbol": "＋", "label": "新增", "color": "#15803d", "border": "solid"},
    {"marker": "MODIFY", "symbol": "✎", "label": "修改", "color": "#a16207", "border": "solid"},
    {
        "marker": "AFFECTED",
        "symbol": "↯",
        "label": "間接受影響",
        "color": "#1d4ed8",
        "border": "dashed",
    },
    {
        "marker": "REMOVE",
        "symbol": "−",
        "label": "已移除（歷史保留）",
        "color": "#b91c1c",
        "border": "dotted",
    },
    {
        "marker": "UNCERTAIN",
        "symbol": "?",
        "label": "未確認",
        "color": "#6b21a8",
        "border": "double",
    },
    {
        "marker": "UNCHANGED",
        "symbol": "·",
        "label": "既有、未變動",
        "color": "#4b5563",
        "border": "solid",
    },
]
_LEGEND_BY_MARKER = {item["marker"]: item for item in LEGEND}


def _feature_marker(touched):
    markers = {row["marker"] for row in touched}
    if not markers:
        return "UNCHANGED"
    if "UNCERTAIN" in markers:
        return "UNCERTAIN"
    if markers <= {"REMOVE"}:
        return "REMOVE"
    if markers <= {"AFFECTED"}:
        return "AFFECTED"
    if markers <= {"ADD", "AFFECTED"} and "ADD" in markers:
        return "ADD"
    return "MODIFY"


_SENTENCES = {
    "ADD": "新增：{title}",
    "MODIFY": "修改：{title}",
    "AFFECTED": "間接受影響：{title}（它依賴的程式有改動，本身未改）",
    "REMOVE": "已移除（歷史保留）：{title}",
    "UNCERTAIN": "需要確認：{title}（新舊程式的對應不確定，等待人工判斷）",
    "UNCHANGED": "未變動：{title}",
}


def novice_view(delta, plan):
    rows = delta["symbols"]
    features = []
    for feature in plan.features:
        members = set(feature.get("members", []))
        touched = [row for row in rows if row["key"] in members or row.get("from_key") in members]
        marker = _feature_marker(touched)
        if feature.get("new") and marker in ("MODIFY", "ADD"):
            # The approved plan declares this feature as new; a moved or edited function
            # inside it does not make the feature itself a modification.
            marker = "ADD"
        title = feature["title_plain"]
        sentence = _SENTENCES[marker].format(title=title)
        if feature.get("renamed_from"):
            sentence += f"（原名：{feature['renamed_from']}）"
        # Four kinds of fact, kept apart so none can be mistaken for another:
        #   git      the commit state of this feature's code (not a verification result)
        #   scope    out-of-plan files and unplanned code changes inside the feature
        #   identity pending old/new mappings that touch this feature (not yet confirmed by a person)
        # The host test is a property of the whole Task, so it stays in the header, not per feature.
        members_files = {member.split("::", 1)[0] for member in feature.get("members", [])}
        git = None
        if touched:
            git = "尚未 commit" if any(row["commit_state"] == "UNCOMMITTED" for row in touched) else "已 commit"
        scope = []
        if members_files & set(delta["out_of_plan_files"]):
            count = len(members_files & set(delta["out_of_plan_files"]))
            scope.append(f"超出計畫 {count} 個檔案")
        if any(row["structure_state"] == "UNPLANNED" for row in touched):
            scope.append("有未規劃的程式變更")
        identity_pending = sum(
            1
            for pending in delta["pending_confirmations"]
            if pending["from_key"].split("::", 1)[0] in members_files
            or pending["to_key"].split("::", 1)[0] in members_files
        )
        sources = [row["source"] for row in touched if row.get("source")]
        features.append(
            {
                "id": feature["id"],
                "title": title,
                "marker": marker,
                "sentence": sentence,
                "git": git,
                "scope": scope,
                "identity_pending": identity_pending,
                "sources": sources,
                "renamed_from": feature.get("renamed_from"),
            }
        )
    notices = []
    if delta["out_of_plan_files"]:
        notices.append(
            {
                "kind": "OUT_OF_PLAN",
                "text": "有修改超出已核准計畫的檔案：" + "、".join(delta["out_of_plan_files"]),
            }
        )
    if delta["commit_state"] == "UNCOMMITTED":
        notices.append({"kind": "UNCOMMITTED", "text": "這些修改尚未 commit，是目前工作區的狀態。"})
    if delta["pending_confirmations"]:
        notices.append(
            {
                "kind": "PENDING",
                "text": f"{len(delta['pending_confirmations'])} 組新舊程式對應待人工確認。",
            }
        )
    if delta["excluded"]:
        notices.append(
            {
                "kind": "EXCLUDED",
                "text": f"{len(delta['excluded'])} 個檔案因敏感性被排除，未讀取內容，也未放入架構圖。",
            }
        )
    return {
        "mode": "novice",
        "task": delta["task"],
        "title": delta["title"],
        "provisional": delta["current"]["provisional"],
        "states": {
            "commit": delta["commit_state"],
            "structure": delta["structure_state"],
            "host_test": delta["host_test_state"],
        },
        "legend": LEGEND,
        "features": features,
        "notices": notices,
    }


def engineer_view(delta, current):
    return {
        "mode": "engineer",
        "task": delta["task"],
        "states": {
            "commit": delta["commit_state"],
            "structure": delta["structure_state"],
            "host_test": delta["host_test"],
        },
        "base": delta["base"],
        "current": delta["current"],
        "files": delta["files"],
        "symbols": delta["symbols"],
        "missing_planned": delta["missing_planned"],
        "out_of_plan_files": delta["out_of_plan_files"],
        "unplanned_symbols": delta["unplanned_symbols"],
        "identity_events": delta["identity_events"],
        "excluded": delta["excluded"],
        "current_nodes": [
            {
                "nid": n.nid,
                "key": n.key,
                "kind": n.kind,
                "source": {"path": n.path, "lines": [n.start, n.end]},
                "body_hash": n.body_hash,
                "provisional": n.provisional,
                "origin": n.origin,
            }
            for n in current.nodes
        ],
        "removed_nodes": [
            {"nid": n.nid, "key": n.key, "status": n.status} for n in current.removed
        ],
    }


def render_novice_html(view):
    cards = []
    for feature in view["features"]:
        item = _LEGEND_BY_MARKER[feature["marker"]]
        sources = (
            "".join(
                f"<li><code>{html.escape(s['path'])}</code> 第 {s['lines'][0]}–{s['lines'][1]} 行</li>"
                for s in feature["sources"]
            )
            or "<li>（沒有對應的程式位置）</li>"
        )
        flags = []
        if feature["git"]:
            flags.append(f"Git：{feature['git']}")
        flags += feature["scope"]
        if feature["identity_pending"]:
            flags.append(f"待確認對應 {feature['identity_pending']} 組")
        notes = "".join(f"<li>{html.escape(n)}</li>" for n in flags)
        notes_block = f"<ul class='flags'>{notes}</ul>" if notes else ""
        cards.append(
            f"<article class='card' data-marker='{feature['marker']}' style='border-color:{item['color']}'>"
            f"<div class='badge' style='background:{item['color']}'>{item['symbol']} {item['label']}</div>"
            f"<h3>{html.escape(feature['title'])}</h3>"
            f"<p>{html.escape(feature['sentence'])}</p>"
            f"{notes_block}"
            f"<details><summary>程式位置</summary><ul>{sources}</ul></details>"
            f"</article>"
        )
    legend = "".join(
        f"<li><span class='swatch' style='background:{item['color']}'>{item['symbol']}</span> "
        f"{item['label']}（{item['border']} 框線）</li>"
        for item in view["legend"]
    )
    notices = "".join(f"<li>{html.escape(n['text'])}</li>" for n in view["notices"])
    states = view["states"]
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(view["task"])} 功能變更</title>
<style>
body {{ font-family: -apple-system, "PingFang TC", sans-serif; margin: 16px; color: #111827; background: #ffffff; }}
h1 {{ font-size: 1.3rem; margin: 0 0 4px; }}
.states {{ font-size: .9rem; color: #374151; margin-bottom: 12px; }}
.grid {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); }}
.card {{ border: 2px solid #9ca3af; border-radius: 10px; padding: 10px 12px; background: #fff; }}
.card[data-marker='AFFECTED'] {{ border-style: dashed; }}
.card[data-marker='REMOVE'] {{ border-style: dotted; }}
.card[data-marker='UNCERTAIN'] {{ border-style: double; border-width: 4px; }}
.badge {{ display: inline-block; color: #fff; font-weight: 700; padding: 2px 8px; border-radius: 6px; font-size: .85rem; }}
.card h3 {{ font-size: 1rem; margin: 8px 0 4px; }}
.card p {{ margin: 4px 0; font-size: .92rem; }}
.flags {{ color: #92400e; font-size: .85rem; padding-left: 18px; }}
.legend, .notices {{ margin-top: 16px; font-size: .9rem; }}
.legend ul, .notices ul {{ list-style: none; padding-left: 0; }}
.swatch {{ display: inline-block; color: #fff; width: 1.6em; text-align: center; border-radius: 4px; margin-right: 6px; }}
code {{ font-size: .85rem; }}
@media (prefers-color-scheme: dark) {{
  body {{ background: #111827; color: #f9fafb; }} .card {{ background: #1f2937; }} .states {{ color: #d1d5db; }}
}}
</style></head>
<body>
<h1>{html.escape(view["task"])}：{html.escape(view["title"])}</h1>
<div class="states">commit：{states["commit"]} ｜ 結構：{states["structure"]} ｜ 主機測試：{states["host_test"]}</div>
<section class="grid">{"".join(cards)}</section>
<section class="legend"><strong>圖例</strong><ul>{legend}</ul></section>
<section class="notices"><strong>未確認與注意事項</strong><ul>{notices or "<li>沒有</li>"}</ul></section>
</body></html>
"""
