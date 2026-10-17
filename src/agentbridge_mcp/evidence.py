from .git import git, tracked_snapshot, working_snapshot
from .security import comment_check, redact, reject_secrets, same_text_content


def implementation_evidence(bridge, row, cwd):
    before, after = tracked_snapshot(cwd), working_snapshot(cwd)
    check = comment_check(before, after)
    diff = git(cwd, "diff", "HEAD", "--no-ext-diff", "--no-textconv").stdout.decode(
        "utf-8", "replace"
    )
    reject_secrets(diff)
    with bridge.store.transaction() as conn:
        reference = bridge.save_artifact(
            conn, row["project_id"], row["task_id"], "CHANGES.diff", row["id"], diff
        )
    new = [p for p in after if p not in before]
    extras = {
        "comment_check": check,
        "diff_reference": reference,
        "untracked_files": new,
        "changed_files": [p for p in before if not same_text_content(before[p], after.get(p))],
        "line_ending_only_files": [
            p
            for p in before
            if before[p] != after.get(p) and same_text_content(before[p], after.get(p))
        ],
        "new_file_contents": {
            p: redact(after[p].decode("utf-8", "replace")) for p in new if len(after[p]) < 200_000
        },
    }
    state = "BLOCKED" if not check["ok"] else None
    if bridge.latest(row["project_id"])["version"] != row["context_version"]:
        state = "BLOCKED"
        extras["context_changed_during_execution"] = True
    return extras, state
