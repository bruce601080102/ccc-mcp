"""Work: the one place where "what is being worked on" is read and started.

A person (in ChatGPT or in the console) never has to know the Goal -> Phase -> Task layers. This
module groups them: a *work group* is a Goal with the Tasks its phases run, and a *single task* is a
Task that belongs to no Goal. The ChatGPT MCP tools and the console's Tasks page both call the
functions here, so both always show and change the same SQLite rows under the same permission
checks. Nothing here has its own storage.

What this module deliberately does not do:

* It never approves anything. A Goal plan is approved by a person at this machine
  (`GoalHub.approve_goal_local` / `approve_goal_web`); `goal_approval_request` only reads what the person
  must approve and where.
* It never guesses a project. With several registered projects and none named, it says so.
* It never starts a second copy of work that already exists: the same requirement sent again, or
  with the same `request_key`, returns the Goal that is already there.
"""

import hashlib
import json
import re

from ..infra.errors import BridgeError
from .context import identifier

# A new request that looks this much like an unfinished Goal is put to the person as a question
# ("continue that one?") instead of silently starting a second Goal.
SIMILAR_GOAL = 0.45
OPEN_GOAL_STATES = frozenset({
    "DRAFT", "PLANNING", "AWAITING_APPROVAL", "READY", "RUNNING", "WAITING_USER", "PAUSED",
    "VERIFYING",
})
_FILLER = re.compile(
    r"^(?:please\s+|can you\s+|could you\s+|help me\s+|i (?:want|need) (?:you )?to\s+|i would like to\s+"
    r"|let'?s\s+|幫我|請你|請|麻煩你|麻煩|我想要|我想|我要|可以幫我|能不能幫我)+",
    re.IGNORECASE,
)

STATE_TEXT = {
    "DRAFT": "Waiting for a plan.",
    "PLANNING": "The planner is drafting the plan.",
    "AWAITING_APPROVAL": "The plan is ready and needs your approval.",
    "READY": "Approved and ready to start.",
    "RUNNING": "Working.",
    "WAITING_USER": "Stopped and needs you.",
    "PAUSED": "Paused.",
    "VERIFYING": "Checking the whole project.",
    "PASSED": "Done. The final checks passed.",
    "EXHAUSTED": "A limit was reached, so nothing more will run.",
    "FAILED": "It could not be completed as planned.",
    "CANCELLED": "Cancelled.",
}
# States in which the person has something to do. Everything else runs, or is over, by itself.
NEEDS_PERSON = frozenset({"AWAITING_APPROVAL", "WAITING_USER"})
FINISHED_GOAL = frozenset({"PASSED", "EXHAUSTED", "FAILED", "CANCELLED"})
TASK_NEEDS_PERSON = frozenset({
    "AWAITING_USER_APPROVAL", "AWAITING_PM_DECISION", "AWAITING_PM_REVIEW",
    "AWAITING_SA_CLARIFICATION",
})
# Tasks a goal creates for itself (a phase's task, the whole-goal verification holder) are named after it.
GOAL_TASK = re.compile(r"GOAL-\d+-.+")
GOAL_ACTIONS = ("start", "pause", "resume", "cancel", "rename")


# Words every development request shares ("build a thing, test it and review it") say nothing about
# *which* work it is, so they never count towards two requests being about the same thing.
_COMMON = frozenset("""
a an the and or but to of in on at for from with without into onto by as is are be been it its this that
these those then than also just only can could should would will shall may might must do does did have has
had not no yes please help me my we our you your i want need like would make build create write add fix
update change use using new small simple basic tool tools app code program script feature features project
python java javascript typescript test tests testing tested review reviews reviewed check checks checking
run runs running implement develop development work works working task tasks thing things some any all
more less one two first second next last after before again still when where which what who how why
""".split())
_COMMON_CJK = frozenset(
    "幫我 請你 我想 我要 一個 一些 這個 那個 開發 建立 建置 製作 完成 測試 檢查 審查 功能 系統 工具 程式 專案 需求 的話 然後 之後 以及 並且 可以 需要".split()
)


def _tokens(text):
    text = (text or "").lower()
    found = {w for w in re.findall(r"[a-z0-9_]{3,}", text) if w not in _COMMON}
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        found.update(b for b in (run[i:i + 2] for i in range(len(run) - 1)) if b not in _COMMON_CJK)
    return found


def similarity(a, b):
    """How much two requests are about the same thing: the shared subject words, as a cosine over
    the words left after the ones every request uses are removed."""
    left, right = _tokens(a), _tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / (len(left) * len(right)) ** 0.5


def normalized(text):
    return " ".join((text or "").split()).lower()


def suggest_title(requirement, limit=48):
    """A short title when the caller did not give one. ChatGPT is asked to write a better one; this
    is only the deterministic fallback: the first line without leading filler words."""
    first = (requirement or "").strip().splitlines()[0] if (requirement or "").strip() else ""
    first = _FILLER.sub("", first.strip()).strip(" ,.:;，。：；！!？?")
    first = first or (requirement or "").strip().splitlines()[0][:limit]
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def _first_line(text, limit=90):
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


class WorkHub:
    # ---- choosing the project ---------------------------------------------------------------

    def resolve_work_project(self, project_id=None):
        """The project a request belongs to. Returns (project_id, None), or (None, question) when it
        cannot be told without guessing."""
        projects = [row["id"] for row in self.list_projects()]
        if project_id:
            if project_id not in projects:
                return None, {
                    "status": "needs_choice", "reason": "unknown_project",
                    "question": f"There is no project named {project_id!r}. Which one did you mean?",
                    "choices": projects,
                }
            return project_id, None
        if len(projects) == 1:
            return projects[0], None
        if not projects:
            return None, {
                "status": "needs_choice", "reason": "no_project",
                "question": "No project is registered yet. Add one in the console or with "
                            "`ccc-mcp project add` first.",
                "choices": [],
            }
        return None, {
            "status": "needs_choice", "reason": "ambiguous_project",
            "question": "Which project is this for?", "choices": projects,
        }

    # ---- reading ----------------------------------------------------------------------------

    def _task_workers(self, project_id):
        """Who is running right now, per task, in one query."""
        workers = {}
        for row in self.store.all(
            "SELECT task_id,role,agent,status FROM agent_executions WHERE project_id=? AND "
            "status IN ('QUEUED','RUNNING') ORDER BY created", (project_id,),
        ):
            workers[row["task_id"]] = {"role": row["role"], "agent": row["agent"],
                                       "status": row["status"]}
        return workers

    def _goal_tasks(self, project_id):
        """Which task belongs to which goal, and which tasks belong to none."""
        owner = {}
        for row in self.store.all(
            "SELECT goal_id,phase_id,task_id,status FROM goal_phase_runs WHERE project_id=? "
            "ORDER BY created", (project_id,),
        ):
            owner[row["task_id"]] = row
        return owner

    @staticmethod
    def _plan_phases(goal):
        run = goal.get("goal_run")
        if run:
            return [
                {"id": p["id"], "title": p["title"], "state": p["state"], "task_id": p["task_id"],
                 "working": p.get("working"), "reason_text": p.get("reason_text"),
                 "kind": p.get("kind", "PLAN")}
                for p in run["phases"]
            ]
        plan = goal.get("plan")
        if not plan:
            return []
        by_phase = {r["phase_id"]: r for r in goal.get("phase_runs") or []}
        phases = []
        for item in plan["plan"]["phases"]:
            ran = by_phase.get(item["id"])
            phases.append({
                "id": item["id"], "title": item["title"],
                "state": ran["status"] if ran else "NOT_STARTED",
                "task_id": ran["task_id"] if ran else None, "working": None, "reason_text": None,
                "kind": "PLAN",
            })
        return phases

    def _goal_entry(self, project_id, summary, workers):
        goal = self.get_goal(project_id, summary["id"])
        phases = self._plan_phases(goal)
        for phase in phases:
            if phase["task_id"] and not phase.get("working"):
                phase["working"] = workers.get(phase["task_id"])
        total = len(phases)
        done = sum(1 for p in phases if p["state"] == "PASSED")
        state = goal["state"]
        run = goal.get("goal_run")
        working = None
        if run and run.get("current") and run["current"].get("working"):
            working = run["current"]["working"]
        elif state == "PLANNING" and goal.get("live_run"):
            working = {"role": "PM", "agent": goal["live_run"]["provider"], "status": "RUNNING"}
        headline = STATE_TEXT.get(state, state)
        if state == "RUNNING":
            current = next((p for p in phases if p.get("working")), None)
            if current:
                headline = f"Working on “{current['title']}”."
            elif done == total and total:
                headline = "All steps passed; starting the final check."
        elif state in ("WAITING_USER", "PAUSED") and run and run.get("reason_text"):
            headline = run["reason_text"]
        elif state == "AWAITING_APPROVAL":
            headline = f"The plan has {total} step(s) and needs your approval."
        needs = None
        if state == "AWAITING_APPROVAL":
            needs = "approve_plan"
        elif state == "WAITING_USER":
            needs = (run or {}).get("reason") or "decision"
        return {
            "kind": "goal", "id": goal["id"], "project_id": project_id, "title": goal["title"],
            "state": state,
            "headline": headline, "needs_user": needs, "working": working,
            "progress": {"done": done, "total": total},
            "plan_version": goal["current_version"], "approved_version": goal["approved_version"],
            "goal_run_id": goal["goal_run_id"], "created": goal["created"],
            "updated": goal["updated"], "finished": state in FINISHED_GOAL,
            "phases": phases, "actions": (run or {}).get("actions", []),
        }

    def _cached_goal_entry(self, project_id, summary, workers):
        """A finished goal only changes when it is renamed (which moves `updated`), so polling does not
        recompute it every time; running goals are always read fresh."""
        if summary["state"] not in FINISHED_GOAL:
            return self._goal_entry(project_id, summary, workers)
        cache = self.__dict__.setdefault("_finished_goal_entries", {})
        key = (project_id, summary["id"], summary["updated"], summary["state"])
        if key not in cache:
            cache[key] = self._goal_entry(project_id, summary, workers)
        return cache[key]

    def _task_entry(self, task, workers, owner=None):
        state = task["state"]
        entry = {
            "kind": "task", "id": task["id"], "title": _first_line(task.get("requirement")),
            "state": state, "created": task["created"], "working": workers.get(task["id"]),
            "needs_user": "decision" if state in TASK_NEEDS_PERSON else None,
        }
        if owner:
            entry["goal_id"], entry["phase_id"] = owner["goal_id"], owner["phase_id"]
        return entry

    def work_overview(self, project_id):
        """Everything the Tasks page and ChatGPT list: work groups (a Goal and its steps) and single
        tasks. `revision` changes exactly when anything shown changes, so a poller can ask "has it
        changed?" cheaply and a page can patch only what moved."""
        self.project(project_id)
        workers = self._task_workers(project_id)
        owner = self._goal_tasks(project_id)
        groups = [self._cached_goal_entry(project_id, g, workers) for g in self.list_goals(project_id)]
        singles = [
            self._task_entry(t, workers) for t in self.list_tasks(project_id)
            if t["id"] not in owner and not GOAL_TASK.fullmatch(t["id"])
        ]
        body = {"project_id": project_id, "groups": groups, "singles": singles}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        return {**body, "revision": digest[:20],
                "needs_you": sum(1 for g in groups if g["needs_user"])
                + sum(1 for t in singles if t["needs_user"])}

    def goal_view(self, project_id, goal_id):
        """One work group for ChatGPT or the console: the entry plus plan, results and what a person
        can still do. Evidence stays where it is stored; each step names its task so the existing
        result and review reads work on it."""
        self.project(project_id)
        goal_id = identifier(goal_id)
        goal = self.get_goal(project_id, goal_id)
        workers = self._task_workers(project_id)
        entry = self._goal_entry(project_id, {"id": goal_id}, workers)
        run = goal.get("goal_run")
        view = {
            **entry, "requirement": goal["requirement"], "next_action": goal["next_action"],
            "plan": goal["plan"], "approval": goal["approval"],
            "budget": (run or {}).get("budget"), "acceptance": (run or {}).get("acceptance"),
            "verifications": (run or {}).get("verifications", []),
            "evidence_reads": [
                {"step": p["id"], "task_id": p["task_id"], "tool": "get_task_result"}
                for p in entry["phases"] if p["task_id"]
            ],
        }
        if run and goal["state"] == "WAITING_USER" and run.get("reason") in (
            "FINAL_ACCEPTANCE_NEEDED", "VERIFICATION_NEEDS_PERSON"
        ):
            try:
                view["acceptance_request"] = self.accept_goal_description(project_id, run["id"])
            except BridgeError as exc:
                view["acceptance_request"] = {"unavailable": str(exc)}
        return view

    # ---- starting ---------------------------------------------------------------------------

    def start_goal_work(
        self, project_id, requirement, title=None, request_key=None, confirm_new=False, plan=None,
        auto_plan=False,
    ):
        """Begin a work group for a request that needs more than one step, once.

        * The same `request_key` (or, without one, the same requirement while that Goal is still
          open) returns the Goal that exists; a retried tool call never makes a second one.
        * A request that looks like an unfinished Goal is returned as a question, not created, unless
          `confirm_new` says the person really wants a separate one.
        * `plan`, when given, is stored as the first plan version (it still needs approval).
        * `auto_plan` (the console's Start button) asks the configured planner for a plan at once, so a
          person who typed a request does not also have to find a "plan it" button. It never approves
          or starts anything; when no planner can run, `planning` says why and the Goal stays a draft.
        """
        project_id, question = self.resolve_work_project(project_id)
        if question:
            return question
        if not isinstance(requirement, str) or not requirement.strip():
            raise BridgeError("requirement is required")
        title = (title or "").strip() or suggest_title(requirement)
        if request_key:
            known = self.store.one(
                "SELECT id,requirement FROM goals WHERE project_id=? AND request_key=?",
                (project_id, request_key),
            )
            if known and normalized(known["requirement"]) == normalized(requirement):
                return {**self._started(project_id, known["id"], plan), "created_now": False,
                        "status": "exists"}
        open_goals = [g for g in self.list_goals(project_id) if g["state"] in OPEN_GOAL_STATES]
        rows = {r["id"]: r for r in self.store.all(
            "SELECT id,requirement FROM goals WHERE project_id=?", (project_id,))}
        same = next((g for g in open_goals if normalized(rows[g["id"]]["requirement"]) ==
                     normalized(requirement)), None)
        if same:
            return {**self._started(project_id, same["id"], plan), "created_now": False,
                    "status": "exists"}
        if not confirm_new:
            candidates = []
            for g in open_goals:
                score = max(similarity(requirement, rows[g["id"]]["requirement"]),
                            similarity(requirement, g["title"]))
                if score >= SIMILAR_GOAL:
                    candidates.append({"goal_id": g["id"], "title": g["title"], "state": g["state"],
                                       "match": round(score, 2)})
            if candidates:
                candidates.sort(key=lambda c: -c["match"])
                return {
                    "status": "needs_choice", "reason": "similar_goal",
                    "question": "This looks like work that is already in progress. Continue one "
                                "of these (use plan_goal with change_request), or confirm it is "
                                "a separate piece of work?",
                    "choices": candidates,
                    "to_create_anyway": "call start_goal again with confirm_new=true",
                }
        created = self.create_goal(project_id, requirement, title, request_key)
        planning = None
        if auto_plan and plan is None and created["created_now"]:
            try:
                self.request_goal_plan(project_id, created["id"])
                planning = "started"
            except BridgeError as exc:
                planning = f"not started: {exc}"
        result = self._started(project_id, created["id"], plan)
        return {**result, "created_now": created["created_now"], "status": "created",
                **({"planning": planning} if planning else {})}

    def _started(self, project_id, goal_id, plan):
        if plan is not None:
            current = self.get_goal(project_id, goal_id)
            if not current["plan"]:
                self.import_goal_plan(project_id, goal_id, plan, 0, "Plan written in the chat")
        view = self.goal_view(project_id, goal_id)
        view["next"] = self._next_step(view)
        return view

    @staticmethod
    def _next_step(view):
        state = view["state"]
        return {
            "DRAFT": "Call plan_goal: pass `plan` if you can write one, or leave it empty to ask "
                     "the planner.",
            "PLANNING": "Planning is running. Call get_goal again shortly.",
            "AWAITING_APPROVAL": "Call request_goal_approval and give the person the result. Only "
                                 "they can approve.",
            "READY": "Call control_goal with action=start.",
            "RUNNING": "Running. Report get_goal's headline; do not call start again.",
            "WAITING_USER": "Tell the person what it needs (headline / acceptance_request).",
        }.get(state, STATE_TEXT.get(state, ""))

    # ---- planning, approval, control ----------------------------------------------------------

    def plan_goal_work(
        self, project_id, goal_id, plan=None, change_request=None, base_version=None,
        wait_seconds=0,
    ):
        """Store a plan written in the chat, or ask the planner for one (or for a change to the
        current one). While a run is stopped for a person, a change becomes a drafted revision."""
        self.project(project_id)
        goal = self.get_goal(project_id, goal_id)
        if plan is not None:
            version = goal["current_version"] if base_version is None else base_version
            self.import_goal_plan(project_id, goal["id"], plan, version, change_request or "")
        else:
            run = goal.get("goal_run")
            if run and run["status"] == "WAITING_USER":
                self.draft_goal_revision(project_id, run["id"], change_request)
            else:
                self.request_goal_plan(
                    project_id, goal["id"], change_request, base_version, None)
            if wait_seconds:
                try:
                    self.wait_for_planning(project_id, goal["id"], timeout=wait_seconds)
                except BridgeError:
                    pass
        view = self.goal_view(project_id, goal["id"])
        view["next"] = self._next_step(view)
        return view

    def goal_approval_request(self, project_id, goal_id):
        """What the person must approve, and how. Approval itself is theirs alone: it needs a person
        at this machine to type the phrase, so no tool here can grant it."""
        self.project(project_id)
        view = self.goal_view(project_id, goal_id)
        if view["approved_version"] and view["approved_version"] == view["plan_version"]:
            return {"status": "already_approved", "goal_id": view["id"],
                    "version": view["plan_version"], "goal": view}
        preview = self.approval_preview(project_id, goal_id)
        return {
            "status": "approval_needed", "goal_id": view["id"], "version": preview["version"],
            "sha256": preview["sha256"], "phrase_to_type": preview["challenge"],
            "what_you_approve": preview["description"],
            "where": [
                f"Console: Tasks -> {view['title']} -> Approve (ccc-mcp ui)",
                f"Terminal: ccc-mcp --project {project_id} goal approve {view['id']}",
            ],
            "note": "ChatGPT cannot approve. Show the person this text and wait for them to "
                    "approve, then call get_goal.",
        }

    def control_goal(self, project_id, goal_id, action, reason="", note=""):
        """start / pause / resume / cancel / rename a work group. Starting needs a plan approved by a person
        for exactly this version, a read-write project and a configured test command: the same
        checks the console makes."""
        if action not in GOAL_ACTIONS:
            raise BridgeError(f"action must be one of {', '.join(GOAL_ACTIONS)}")
        self.project(project_id)
        goal = self.get_goal(project_id, goal_id)
        run = goal.get("goal_run")
        if action == "rename":
            self.rename_goal(project_id, goal["id"], note)
            return {"action": action, "goal": self.goal_view(project_id, goal["id"])}
        if action == "start":
            started = self.start_goal_run(project_id, goal["id"])
            return {"action": action, "already_started": bool(started.get("already_started")),
                    "goal": self.goal_view(project_id, goal["id"])}
        if action == "cancel" and not run:
            self.cancel_goal(project_id, goal["id"], reason)
        else:
            if not run:
                raise BridgeError("This goal has not been started")
            if action == "pause":
                self.pause_goal_run(project_id, run["id"], reason)
            elif action == "resume":
                self.resume_goal_run(project_id, run["id"], note)
            else:
                self.cancel_goal_run(project_id, run["id"], reason)
        return {"action": action, "goal": self.goal_view(project_id, goal["id"])}
