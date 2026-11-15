"""Write the participant question sheet for each case from its answer key.

The participant sheet (questions.md) contains questions and blank answer fields only.
Answer keys stay in keys.json and are for raters, never shown to participants.

Question set (identical structure for every case; only the surface names differ):
  Q01 current features             Q02 implementation file of search
  Q03-Q08 added / modified per Task (TASK-0001..0003)
  Q09 yes/no grid for TASK-0003 features (uncommitted, host test, pending mapping)
  Q10 can TASK-0003 be merged as done?   Q11 out-of-plan files
  Q12 files that depend on the search implementation
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
MATERIALS = HERE / "materials"
CASES = ["case1_notes", "case2_bookmarks", "case3_todos"]

GRID_AXES = [
    ("a_uncommitted_change", "此功能有尚未 commit 的變更嗎？"),
    ("b_host_test_passed", "TASK-0003 的主機測試是否通過？"),
    ("d_pending_mapping", "此功能與一組待人工確認的新舊對應有關嗎？"),
]


def render(case):
    keys = json.loads((MATERIALS / case / "keys.json").read_text(encoding="utf-8"))
    lines = [
        f"# 問卷（{case}）",
        "",
        "請只根據你拿到的材料作答。每題先作答，才能進入下一題；不確定可寫「不確定」。",
        "",
        "## Q01　目前（TASK-0003 之後）工具有哪些功能？",
        "回答：",
        "",
        "## Q02　搜尋功能的主要實作在哪個檔案？",
        "回答：",
        "",
    ]
    for number, (label, task, what) in enumerate(
        [
            ("Q03", "TASK-0001", "新增了哪些功能？"),
            ("Q04", "TASK-0001", "修改了哪些既有功能？（沒有就寫「無」）"),
            ("Q05", "TASK-0002", "新增了哪些功能？"),
            ("Q06", "TASK-0002", "修改了哪些既有功能？（沒有就寫「無」）"),
            ("Q07", "TASK-0003", "新增了哪些功能？"),
            ("Q08", "TASK-0003", "修改了哪些既有功能？（沒有就寫「無」）"),
        ],
        start=0,
    ):
        lines += [f"## {label}　{task} {what}", "回答：", ""]
    lines += [
        "## Q09　TASK-0003 各功能的判斷（每格填「是」或「否」）",
        "| 功能 | " + " | ".join(text for _, text in GRID_AXES) + " |",
        "|---|" + "---|" * len(GRID_AXES),
    ]
    for title in keys["S_grid_T3"]:
        lines.append(f"| {title} |" + " |" * len(GRID_AXES))
    lines += [
        "",
        "## Q10　TASK-0003 能否直接視為已完成、可合併？",
        "回答：　□ 能　□ 不能",
        "阻擋合併的原因（可複選；只勾會阻擋合併的項目）：",
        "　□ 超出計畫的檔案　□ 待確認的新舊對應　□ 主機測試未通過　□ 尚未 commit",
        "待執行的動作（可複選）：",
        "　□ 需先 commit　□ 需要重跑測試　□ 不需要任何動作",
        "",
        "## Q11　TASK-0003 有哪些檔案超出已核准計畫？",
        "回答：",
        "",
        "## Q12　若修改搜尋的實作檔，哪些檔案會依賴它？",
        "回答：",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    for case in CASES:
        (MATERIALS / case / "questions.md").write_text(render(case), encoding="utf-8")
        print("wrote", case)
