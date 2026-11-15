"""Approval gate: what stops a Task from being merged, kept apart from commit state.

Definition (fixed here, shown on the page, and used by the study's answer keys):

  READY         no blocker AND committed.
  NEEDS_COMMIT  no blocker, but the reviewed change is not committed yet. This is a pending
                action, not a blocker: committing it does not need any further review.
  BLOCKED       at least one blocker. Blockers are facts that a commit would not fix:
                host tests not PASSED, out-of-plan files (no acceptance mechanism exists in
                this prototype, so any out-of-plan file blocks), unconfirmed identity pairs,
                or planned work that is not present.

"Not committed" and "cannot be merged" are therefore different statements. A Task can be
BLOCKED and committed, or NEEDS_COMMIT and fully verified.
"""

DEFINITION = (
    "可合併 = 主機測試通過，且沒有阻擋項，且已 commit。"
    "未 commit 只是待執行的動作，不單獨構成阻擋。"
)

REASON_TEXT = {
    "PARENT_IDENTITY_UNRESOLVED": "上層類別的對應無法唯一確定",
    "LOW_SCORE": "相似度不足以自動判定",
    "AMBIGUOUS_CANDIDATES": "有多個候選對應，無法自動判定",
}


def approval_gate(delta):
    blockers = []
    host_state = delta["host_test_state"]
    if host_state != "PASSED":
        blockers.append({"code": "HOST_TEST_NOT_PASSED", "text": f"主機測試未通過（{host_state}）"})
    if delta["out_of_plan_files"]:
        blockers.append(
            {
                "code": "OUT_OF_PLAN",
                "text": "超出已核准計畫的檔案需人工接受（原型尚無接受機制）："
                + "、".join(delta["out_of_plan_files"]),
            }
        )
    if delta["pending_confirmations"]:
        blockers.append(
            {
                "code": "PENDING_IDENTITY",
                "text": f"{len(delta['pending_confirmations'])} 組新舊對應待人工確認",
            }
        )
    if delta["missing_planned"]:
        blockers.append(
            {
                "code": "PLANNED_NOT_BUILT",
                "text": f"{len(delta['missing_planned'])} 項已規劃的內容尚未實作",
            }
        )
    actions = []
    if delta["commit_state"] == "UNCOMMITTED":
        actions.append({"code": "COMMIT_REQUIRED", "text": "需先 commit（待執行的動作，不是阻擋）"})
    if blockers:
        status = "BLOCKED"
    elif actions:
        status = "NEEDS_COMMIT"
    else:
        status = "READY"
    return {"status": status, "blockers": blockers, "actions": actions, "definition": DEFINITION}
