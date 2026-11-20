// Goal runs (Stage C), shown on a goal's page: the whole approved goal, phase by phase. The story is
// first ("phase 1 failed its test, the Delivery PM found the cause, the developer fixed it, phase 1
// passed, phase 2 started with what phase 1 delivered, the whole project was tested, the goal passed");
// ids, hashes and raw records are in the expandable evidence under it. Every limit shown is the
// whole goal's, not a phase's. Everything that comes from the server is put into the page through
// esc(), never as raw markup.
window.cccGoalRun = function (ctx) {
  "use strict";
  const { api, esc, UI, toast, showModal, whenHtml } = ctx;
  const T = (text) => esc(UI.translate(text));

  const RUN_STATE = {
    RUNNING: ["accent", "Working"],
    VERIFYING: ["accent", "Verifying the whole goal"],
    WAITING_USER: ["negative", "Waiting for you"],
    PAUSED: ["neutral", "Paused"],
    PASSED: ["positive", "Passed"],
    EXHAUSTED: ["negative", "Stopped: a limit was reached"],
    FAILED: ["negative", "Could not be completed"],
    CANCELLED: ["neutral", "Stopped by you"],
  };
  const PHASE_STATE = {
    READY: ["neutral", "Ready"],
    BLOCKED: ["neutral", "Waits for an earlier phase"],
    STARTING: ["accent", "Starting"],
    RUNNING: ["accent", "Working"],
    PAUSED: ["neutral", "Paused"],
    NEEDS_HUMAN: ["negative", "Waiting for you"],
    AWAITING_ACCEPTANCE: ["accent", "Waiting for your acceptance"],
    PASSED: ["positive", "Passed"],
    EXHAUSTED: ["negative", "Stopped: a limit was reached"],
    CANCELLED: ["neutral", "Stopped by you"],
  };
  const LIVE = ["RUNNING", "VERIFYING", "WAITING_USER", "PAUSED"];
  const ACTIVE = ["RUNNING", "VERIFYING"];
  const PROBLEM = {
    TREE_CHANGED: "The files changed after the last phase passed, outside any recorded run.",
    UNREPORTED_FILES: "Some changed files were not reported by any phase.",
    PROTECTED_PATHS: "Protected files differ from the last commit.",
    TESTS_REMOVED: "Test files that a phase reported are gone.",
    OPEN_REVIEW_FINDINGS: "There are unresolved review findings.",
    DEVIATION_ACKNOWLEDGED: "A phase was continued after protected files were changed.",
  };

  const agentName = (agent) => (agent === "codex" ? "Codex" : agent === "claude" ? "Claude Code" : agent || "");
  const pill = (tone, label) => `<span class="pill ${tone}">${T(label)}</span>`;
  const statePill = (map, status) => { const [tone, label] = map[status] || ["neutral", status]; return pill(tone, label); };

  function notice(html, when, negative) {
    return `<div class="chat-system-entry ${negative ? "is-negative" : ""}"><div class="chat-notice"><span class="chat-notice-dot" aria-hidden="true"></span><span>${html}</span>${when ? ` <time>${whenHtml(when)}</time>` : ""}</div></div>`;
  }

  function codeBlock(text) {
    return `<pre class="phase-code mono">${esc(text)}</pre>`;
  }

  // ---- the story ---------------------------------------------------------------------------------

  function eventHtml(event, run) {
    const d = event.detail || {};
    const id = `<span class="mono">${esc(d.phase_id || "")}</span>`;
    switch (event.kind) {
      case "goal_run_started":
        return notice(`${T("You started the whole goal.")} ${esc((d.phases || []).length)} ${T("phases run one at a time, in the order their dependencies allow.")}`, event.created);
      case "phase_run_started":
        return notice(d.handoff_sha256
          ? `${T("Phase")} ${id} ${T("was started by the goal run, with what the earlier phases delivered.")}`
          : `${T("Phase")} ${id} ${T("was started by the goal run.")}`, event.created);
      case "goal_phase_recorded":
        return notice(`${T("Phase")} ${id} ${T("passed. Who did the work, the test result and the files were saved.")}`, event.created);
      case "goal_run_verifying":
        return notice(T("Every phase passed. The whole project is being tested again."), event.created);
      case "goal_verification_finished": {
        const text = {
          PASSED: "The whole project passed the host test.",
          FAILED: "The whole project failed the host test although every phase had passed.",
          NEEDS_PERSON: "The host test passed, but something needs your decision.",
          INTERRUPTED: "The final host test did not run to a result.",
        }[d.status];
        return notice(T(text || "The final verification finished."), event.created, d.status === "FAILED" || d.status === "INTERRUPTED");
      }
      case "goal_integration_requested":
        return notice(`${T("One integration fix was started inside the approved plan:")} <span class="mono">${esc(d.phase_id)}</span>`, event.created, true);
      case "goal_run_paused": return notice(T("The goal run was paused."), event.created);
      case "goal_run_resumed": return notice(T("The goal run was resumed."), event.created);
      case "goal_run_plan_adopted":
        return notice(`${T("The goal run continues under plan version")} ${esc(d.to_version)}, ${T("which you approved.")}`, event.created);
      case "goal_run_revision_requested":
        return notice(T("The Delivery PM was asked to draft a revised plan. Nothing changes until you approve it."), event.created);
      case "goal_run_recovered":
        return notice(T("The goal run was picked up again from its saved state."), event.created);
      case "goal_run_waiting_user":
      case "goal_run_exhausted":
      case "goal_run_failed":
        return notice(`${T((d.detail && d.detail.message) || "The goal run stopped.")}`, event.created, true);
      case "goal_run_cancelled": return notice(T("The goal run was stopped."), event.created, true);
      case "goal_run_passed": return notice(T("The goal passed its final verification."), event.created);
      default: return "";
    }
  }

  function headline(run) {
    const detail = run.detail || {};
    if (ACTIVE.includes(run.status)) {
      const current = run.current;
      if (run.status === "VERIFYING") return T("Every phase passed. The whole project is being verified.");
      if (current && current.working) {
        return `${esc(agentName(current.working.agent))} · <span class="mono">${esc(current.working.role)}</span> ${T("is working on phase")} <span class="mono">${esc(current.phase_id)}</span>.`;
      }
      if (current) return `${T("Phase")} <span class="mono">${esc(current.phase_id)}</span> ${T("is between two steps.")}`;
      return T("Choosing the next phase.");
    }
    if (run.status === "PAUSED") return T("Paused. Nothing new will start until you resume.");
    if (run.status === "PASSED") return T("The goal passed its final verification.");
    if (run.status === "CANCELLED") return T("You stopped this goal.");
    const why = run.reason_text || detail.message || "The goal run stopped.";
    const phase = detail.phase_id ? ` (${T("phase")} <span class="mono">${esc(detail.phase_id)}</span>)` : "";
    return `${T(why)}${phase}`;
  }

  // ---- the phases --------------------------------------------------------------------------------

  function phasesHtml(run) {
    return `<ol class="goal-phases goalrun-phases">${run.phases.map((phase) => `
      <li class="goal-phase" data-grun-phase="${esc(phase.id)}">
        <div class="goal-phase-head">
          <strong>${esc(phase.title)}</strong>
          <span class="pill neutral mono">${esc(phase.id)}</span>
          ${phase.kind === "INTEGRATION" ? pill("accent", "Integration fix") : ""}
          ${statePill(PHASE_STATE, phase.state)}
          ${phase.attempts ? `<span class="muted">${T("Attempt")} ${esc(Math.max(phase.attempts.used, phase.state === "READY" ? 0 : 1))} / ${esc(phase.attempts.max)}</span>` : ""}
          ${phase.recorded ? `<span class="muted">${T("evidence saved")}</span>` : ""}
          ${phase.working ? `<span class="muted">${esc(agentName(phase.working.agent))} · ${esc(phase.working.role)}</span>` : ""}
        </div>
        ${phase.state === "BLOCKED" && phase.waiting_for.length ? `<div class="muted">${T("Waits for")} ${phase.waiting_for.map(esc).join(", ")}</div>` : ""}
        ${phase.reason_text && ["NEEDS_HUMAN", "EXHAUSTED", "PAUSED"].includes(phase.state) ? `<div class="muted">${T(phase.reason_text)}</div>` : ""}
        ${phase.phase_run_id ? `<button class="btn small" data-phase-show="${esc(phase.phase_run_id)}" type="button">${T("Show what happened")}</button>` : ""}
      </li>`).join("")}</ol>`;
  }

  // ---- the budget of the whole goal ------------------------------------------------------------------

  function meter(label, used, max, unit) {
    const pct = max ? Math.min(100, Math.round((used / max) * 100)) : 0;
    return `<div class="phase-meter"><span>${T(label)}</span><span class="mono">${esc(used)} / ${esc(max)}${unit ? " " + T(unit) : ""}</span>
      <div class="phase-bar" aria-hidden="true"><div style="width:${pct}%"></div></div></div>`;
  }

  function budgetHtml(run) {
    const b = run.budget;
    const l = run.limits;
    return `<div class="phase-budget">
      ${meter("Agent runs (whole goal)", b.dispatches.used, b.dispatches.max)}
      ${meter("Minutes (whole goal)", b.minutes.used, b.minutes.max)}
      <div class="muted">${T("Tries per phase")}: ${esc(l.max_attempts_per_phase)} · ${T("Left")}: ${esc(b.dispatches.left)} ${T("agent runs")}, ${esc(b.minutes.left)} ${T("minutes")}</div>
      <p class="muted">${T("These limits belong to the whole goal. Starting a new phase never adds to them, and resuming never adds to them.")}</p>
      <p class="muted">${T("Time counts while a phase works or the final verification runs. Waiting for you and a pause do not count; a computer that sleeps while the goal works does.")}</p>
      <p class="muted">${T("Tokens and cost: unknown. The providers' command-line tools do not report them.")}</p></div>`;
  }

  // ---- the final verification -------------------------------------------------------------------------

  function verificationHtml(run) {
    if (!run.verifications.length) return "";
    return `<h3>${T("Final verification")}</h3>` + run.verifications.map((v) => {
      const e = v.evidence || {};
      const host = e.host_test || {};
      const tone = v.status === "PASSED" ? "positive" : v.status === "RUNNING" ? "accent" : "negative";
      const problems = (e.problems || []).map((p) => `<li>${T(PROBLEM[p.code] || p.code)} <span class="muted">${esc(p.detail)}</span></li>`).join("");
      const criteria = (e.criteria || []).map((c) => `<li>${esc(c.statement)} ${pill(c.coverage === "host_test" ? "positive" : "neutral", c.coverage === "host_test" ? "Checked by the host test" : "Needs you")}</li>`).join("");
      return `<div class="goalrun-verification">
        <p>${T("Check")} ${esc(v.seq)}: ${pill(tone, v.status === "PASSED" ? "Passed" : v.status === "FAILED" ? "Failed" : v.status === "NEEDS_PERSON" ? "Needs you" : v.status === "RUNNING" ? "Working" : "No result")}
        <span class="muted">${v.outcome ? T(v.outcome) : ""}</span></p>
        ${host.command ? `<p class="mono">${esc((host.command || []).join(" "))} · ${host.passed ? T("passed") : T("did not pass")}</p>` : ""}
        ${problems ? `<ul>${problems}</ul>` : ""}
        ${criteria ? `<h4>${T("Final criteria")}</h4><ul class="goal-criteria">${criteria}</ul>` : ""}
        ${host.stdout || host.stderr ? `<details><summary>${T("Host test output")}</summary>${codeBlock((host.stdout || "") + (host.stderr ? "\n" + host.stderr : ""))}</details>` : ""}
      </div>`;
    }).join("");
  }

  function evidenceHtml(run) {
    const rows = [["Goal run", run.id], ["Plan version", run.plan_version], ["Plan hash (sha256)", run.plan_sha256],
      ["Approval", run.approval_id], ["Test command", (run.test_command || []).join(" ")],
      ["Starting point (git)", (run.base && run.base.head) || "—"], ["Supervisor process", run.supervisor_alive ? "running" : "none right now"]];
    const ledger = run.ledger.map((r) => `<li class="mono">${esc(r.phase_id)} · ${esc(r.task_id)} · ${T("evidence hash")} ${esc(r.evidence_sha256)}
      <details><summary>${T("What was saved")}</summary>${codeBlock(JSON.stringify(r.evidence, null, 2))}</details>
      ${r.handoff ? `<details><summary>${T("Context given to the next phase")}</summary>${codeBlock(r.handoff)}</details>` : ""}</li>`).join("");
    return `<details class="goal-advanced phase-evidence"><summary>${T("Technical evidence")}</summary>
      <dl>${rows.map(([k, v]) => `<dt>${T(k)}</dt><dd class="mono">${esc(v)}</dd>`).join("")}</dl>
      ${ledger ? `<h4>${T("Passed phases")}</h4><ul>${ledger}</ul>` : ""}
      <h4>${T("Record")}</h4>${codeBlock(run.events.map((e) => [e.created, e.kind, e.actor].join("  ")).join("\n"))}</details>`;
  }

  // ---- controls ------------------------------------------------------------------------------------

  function controlsHtml(run, goal) {
    if (!LIVE.includes(run.status)) return "";
    const buttons = [];
    const has = (name) => run.actions.includes(name);
    const revised = goal.plan && goal.plan.version !== run.plan_version;
    if (has("pause")) buttons.push(`<button class="btn" data-grun-act="pause" type="button">${T("Pause")}</button>`);
    if (has("resume") || (revised && run.status === "WAITING_USER")) buttons.push(`<button class="btn primary" data-grun-act="resume" type="button">${T("Resume")}</button>`);
    if (has("accept")) buttons.push(`<button class="btn primary" data-grun-act="accept" type="button">${T("Review and accept the result")}</button>`);
    if (has("revise") && !revised) buttons.push(`<button class="btn" data-grun-act="revise" type="button">${T("Ask the Delivery PM for a revised plan")}</button>`);
    if (run.stalled) buttons.push(`<button class="btn" data-grun-act="recover" type="button">${T("Pick this up again")}</button>`);
    buttons.push(`<button class="btn danger small" data-grun-act="cancel" type="button">${T("Stop the whole goal")}</button>`);
    const waitingRevised = revised ? `<p class="muted">${T("A revised plan (version")} ${esc(goal.plan.version)}) ${goal.approved_version === goal.plan.version
      ? T("is approved. Resume to continue under it.")
      : T("is waiting for your approval below. Nothing continues until you approve exactly that version.")}</p>` : "";
    return `<div class="goal-buttons">${buttons.join("")}</div>${waitingRevised}
      ${run.stalled ? `<p class="muted">${T("Nothing has moved for a while and no process is working on it.")}</p>` : ""}`;
  }

  function panelHtml(run, goal) {
    return `<section class="goal-plan goal-run" data-grun="${esc(run.id)}">
      <h2>${T("Running the whole goal")} ${statePill(RUN_STATE, run.status)}</h2>
      <p class="goalrun-headline">${headline(run)}</p>
      ${phasesHtml(run)}
      <section class="chat-thread phase-thread">${run.events.map((e) => eventHtml(e, run)).join("")}</section>
      ${controlsHtml(run, goal)}
      ${budgetHtml(run)}
      ${verificationHtml(run)}
      <p class="muted">${T("A phase starts by itself when the ones it depends on have passed. Nothing is committed, pushed or released, and no limit is raised by the goal run.")}</p>
      <p class="muted">${T("The goal keeps going without this page. If every process dies, it waits until the console starts again or you pick it up.")}</p>
      ${evidenceHtml(run)}</section>`;
  }

  // ---- what the plan list needs -------------------------------------------------------------------------

  function startHtml(goal) {
    if (goal.state !== "READY" || !goal.plan) return "";
    return `<section class="goal-actions goalrun-start">
      <p>${T("Run the whole approved plan: each phase starts by itself when the phases it depends on have passed, and the whole project is tested at the end. One set of limits covers the whole goal.")}</p>
      <div class="goal-buttons"><button class="btn primary" id="goalrun-start" type="button">${T("Run the whole goal")}</button></div>
    </section>`;
  }

  function signature(goal) {
    const run = goal.goal_run;
    if (!run) return "";
    return JSON.stringify([run.status, run.reason, run.plan_version, run.events.length, run.stalled,
      run.phases.map((p) => [p.id, p.state, p.attempts && p.attempts.used, p.working && p.working.role]),
      run.budget.dispatches.used, run.budget.minutes.used, run.verifications.map((v) => v.status)]);
  }

  // ---- buttons ------------------------------------------------------------------------------------------

  function bind(root, env) {
    const { goal, project, reload } = env;
    const base = `/projects/${encodeURIComponent(project)}`;
    const start = document.getElementById("goalrun-start");
    if (start) {
      start.addEventListener("click", () => showModal({
        title: "Run the whole goal",
        body: "The developer roles will change the project's files and the project's own test command will run on your computer, phase after phase, until the goal is verified or something needs you. One set of limits (agent runs and minutes) covers the whole goal. Nothing is committed, pushed or released.",
        confirmLabel: "Run the whole goal",
        onSubmit: async () => {
          await api(`${base}/goals/${encodeURIComponent(goal.id)}/run`, { method: "POST", body: {} });
          toast("The goal is running.");
          await reload();
        },
      }));
    }
    const run = goal.goal_run;
    if (!run) return;
    const url = (action) => `${base}/goal-runs/${encodeURIComponent(run.id)}/${action}`;
    const send = async (target, body) => {
      try { await api(target, { method: "POST", body: body || {} }); }
      catch (err) { toast(err.message, true); }
      await reload();
    };
    root.querySelectorAll("[data-grun-act]").forEach((button) => {
      button.addEventListener("click", () => {
        const act = button.dataset.grunAct;
        if (act === "pause") send(url("pause"));
        else if (act === "recover") send(`${base}/goal-runs/recover`);
        else if (act === "resume") {
          showModal({
            title: "Resume the goal",
            body: "It carries on from where it stopped. Nothing that already ran is run again, and no limit is raised.",
            fields: [{ label: UI.translate("A note for the Delivery PM (optional)"), placeholder: "" }],
            confirmLabel: "Resume",
            onSubmit: async ([note]) => { await api(url("resume"), { method: "POST", body: { note: note || "" } }); await reload(); },
          });
        } else if (act === "revise") {
          showModal({
            title: "Ask the Delivery PM for a revised plan",
            body: "The Delivery PM drafts a new version of the plan. It is only a draft: nothing continues until you read it and approve exactly that version. The request counts as one agent run of this goal.",
            fields: [{ label: UI.translate("What should change? (optional)"), placeholder: "" }],
            confirmLabel: "Ask",
            onSubmit: async ([text]) => { await api(url("revise"), { method: "POST", body: { change_request: text || undefined } }); toast("Asked the Delivery PM."); await reload(); },
          });
        } else if (act === "cancel") {
          showModal({
            title: "Stop the whole goal", body: "The goal run ends, the open phase and its task are stopped, and nothing more is started. What was already written to the files stays.",
            fields: [{ label: UI.translate("Reason (optional)"), placeholder: "" }], confirmLabel: "Stop the whole goal", danger: true,
            onSubmit: async ([reason]) => { await api(url("cancel"), { method: "POST", body: { reason } }); await reload(); },
          });
        } else if (act === "accept") {
          api(url("accept-preview")).then((info) => {
            const challenge = info.challenge;
            const list = (info.criteria || []).map((c) => c.id + " " + c.statement)
              .concat((info.problems || []).map((p) => p.code + ": " + p.detail)).join("; ");
            showModal({
              title: "Accept the final result",
              body: UI.translate("Every phase passed and the host test passed on the whole project. By accepting you say that you decided on the following yourself: ") + list,
              fields: [{ label: `${UI.translate("Type")} ${challenge} ${UI.translate("to confirm")}`, placeholder: challenge }],
              confirmLabel: "Accept",
              onSubmit: async ([typed]) => {
                if (typed !== challenge) throw new Error(UI.translate("Confirmation text did not match."));
                await api(url("accept"), { method: "POST", body: { confirm: typed } });
                toast("The goal was accepted.");
                await reload();
              },
            });
          }).catch((err) => toast(err.message, true));
        }
      });
    });
  }

  return { panelHtml, startHtml, signature, bind };
};
