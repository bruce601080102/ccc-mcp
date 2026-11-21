// One piece of work in detail (a Goal): the request, the plan, approval, the run and its steps. It lives
// under Tasks. Nothing here starts work by itself: approving a plan starts nothing, and a person (or
// ChatGPT, once the plan is approved) starts it. The page updates in place and never reloads.
// Everything that comes from the server is put into the page through esc(), or as an element's value.
window.cccGoals = function (ctx) {
  "use strict";
  const { api, esc, UI, layout, navTo, toast, showModal, parseRoute, stale, getGen, livePoll,
    morphContent, whenHtml, loadingView, errorView } = ctx;
  const T = (text) => esc(UI.translate(text));
  const phasesUi = window.cccPhases(ctx);
  const goalRunUi = window.cccGoalRun(ctx);

  const STATE = {
    DRAFT: ["neutral", "Not planned yet"],
    PLANNING: ["accent", "Planning"],
    AWAITING_APPROVAL: ["accent", "Waiting for your approval"],
    READY: ["positive", "Approved"],
    RUNNING: ["accent", "Working"],
    VERIFYING: ["accent", "Verifying the whole goal"],
    WAITING_USER: ["negative", "Waiting for you"],
    PAUSED: ["neutral", "Paused"],
    PASSED: ["positive", "Passed"],
    EXHAUSTED: ["negative", "Stopped: a limit was reached"],
    FAILED: ["negative", "Could not be completed"],
    CANCELLED: ["neutral", "Cancelled"],
  };
  const KIND = {
    host_test: "Host test", host_command: "Host command",
    reviewer_check: "Reviewer check", manual: "Manual check",
  };
  const FAILURE = {
    INVALID_OUTPUT: "The Delivery PM's answer was not a valid plan, so nothing was saved.",
    TIMEOUT: "The Delivery PM ran out of time.",
    QUOTA_EXHAUSTED: "The provider's quota is used up.",
    PROVIDER_UNAVAILABLE: "The provider could not be started.",
    CONFLICT: "The plan was changed while the Delivery PM worked, so its answer was not saved.",
    INTERRUPTED: "The planning process stopped before it finished.",
    SPAWN: "The planning process could not be started.",
    CANCELLED: "Planning was stopped.",
    ERROR: "Planning failed.",
  };
  const base = (project) => `/projects/${encodeURIComponent(project)}/goals`;

  function statePill(state) {
    const [tone, label] = STATE[state] || ["neutral", state];
    return `<span class="pill ${tone}">${T(label)}</span>`;
  }

  // ---- the plan ----------------------------------------------------------------------------

  function criterionHtml(criterion) {
    const verify = criterion.verify;
    const how = verify.command && verify.command.length
      ? `<code class="mono">${esc(verify.command.join(" "))}</code>`
      : `<span class="muted">${esc(verify.description || "")}</span>`;
    return `<li><span>${esc(criterion.statement)}</span>
      <span class="goal-verify"><span class="pill neutral">${T(KIND[verify.kind] || verify.kind)}</span> ${how}</span></li>`;
  }

  function listHtml(items) {
    return items && items.length ? `<ul>${items.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>` : `<p class="muted">—</p>`;
  }

  function planHtml(plan, goal) {
    const body = plan.plan;
    return `
      <section class="goal-plan">
        <h2>${T("The plan")} <small>${T("Version")} ${esc(plan.version)} · ${esc(body.phases.length)} ${T("phases")}</small></h2>
        <p>${esc(body.summary)}</p>
        <div class="goal-cols">
          <div><h3>${T("In scope")}</h3>${listHtml(body.in_scope)}</div>
          <div><h3>${T("Out of scope")}</h3>${listHtml(body.out_of_scope)}</div>
        </div>
        <ol class="goal-phases">${body.phases.map((phase) => `
          <li class="goal-phase">
            <div class="goal-phase-head">
              <strong>${esc(phase.title)}</strong>
              <span class="pill neutral mono">${esc(phase.id)}</span>
              <span class="pill ${phase.risk === "high" ? "negative" : phase.risk === "medium" ? "accent" : "neutral"}">${T("Risk")}: ${T(phase.risk)}</span>
              ${phase.depends_on.length ? `<span class="muted">${T("after")} ${phase.depends_on.map(esc).join(", ")}</span>` : `<span class="muted">${T("can start first")}</span>`}
            </div>
            <p>${esc(phase.scope)}</p>
            <h4>${T("Delivers")}</h4>${listHtml(phase.outputs)}
            <h4>${T("Accepted when")}</h4><ul class="goal-criteria">${phase.acceptance.map(criterionHtml).join("")}</ul>
            <div class="muted">${T("Workflow")}: <span data-no-i18n>${esc(plan.teams?.[phase.workflow]?.builtin
                ? UI.translate(plan.teams[phase.workflow].name) : plan.teams?.[phase.workflow]?.name || phase.workflow)}</span>${phase.provider_hint ? ` · ${T("Suggested provider")}: ${esc(phase.provider_hint)}` : ""}</div>
          </li>`).join("")}</ol>
        <h3>${T("The whole goal is accepted when")}</h3>
        <ul class="goal-criteria">${body.final_acceptance.map(criterionHtml).join("")}</ul>
        ${body.decisions_needed.length ? `<h3>${T("Decisions for you")}</h3><ul>${body.decisions_needed.map((d) => `<li>${esc(d.question)}${d.options.length ? ` <span class="muted">(${d.options.map(esc).join(" / ")})</span>` : ""}</li>`).join("")}</ul>` : ""}
        <div class="goal-cols">
          <div><h3>${T("Assumptions")}</h3>${listHtml(body.assumptions)}</div>
          <div><h3>${T("Risks")}</h3>${listHtml(body.risks)}</div>
        </div>
        <p class="goal-budget muted">${T("Limits when a phase runs")}: ${esc(body.budget.max_attempts_per_phase)} ${T("tries per phase")}, ${esc(body.budget.max_agent_dispatches)} ${T("agent runs")}, ${esc(body.budget.max_wall_clock_minutes)} ${T("minutes")}. ${T("The program enforces these limits; a plan cannot switch them off.")}</p>
      </section>`;
  }

  // ---- the conversation ----------------------------------------------------------------------

  function post(who, name, bodyHtml, when, tone) {
    const user = who === "user";
    return `<article class="chat-post ${user ? "is-user" : ""} ${tone === "error" ? "has-error" : ""}">
      <div class="chat-avatar goal-avatar" aria-hidden="true" data-no-i18n>${user ? T("You") : "PM"}</div>
      <div class="chat-post-body"><div class="chat-post-meta"><strong data-no-i18n>${esc(name)}</strong>${when ? `<time>${whenHtml(when)}</time>` : ""}</div>
      <div class="chat-bubble">${bodyHtml}</div></div></article>`;
  }

  function notice(text, when, negative) {
    return `<div class="chat-system-entry ${negative ? "is-negative" : ""}"><div class="chat-notice"><span class="chat-notice-dot" aria-hidden="true"></span><span>${text}</span>${when ? ` <time>${whenHtml(when)}</time>` : ""}</div></div>`;
  }

  function eventHtml(event) {
    const d = event.detail || {};
    if (event.kind === "goal_created") return post("user", UI.translate("You"), `<p>${esc(d.requirement)}</p>`, event.created);
    if (event.kind === "plan_requested") {
      return d.change_request
        ? post("user", UI.translate("You"), `<p><strong>${T("Please change the plan:")}</strong></p><p>${esc(d.change_request)}</p>`, event.created)
        : notice(T("Asked the Delivery PM to plan this goal."), event.created);
    }
    if (event.kind === "plan_version_created") {
      const manual = d.source === "manual";
      const who = manual ? UI.translate("Imported plan") : UI.translate("Delivery PM");
      return post("pm", who,
        `${d.message ? `<p>${esc(UI.translate(d.message))}</p>` : ""}<p class="muted">${T("Version")} ${esc(d.version)} · ${esc(d.phases)} ${T("phases")}${manual ? ` · ${T("written outside ccc-mcp, not by the Delivery PM")}` : ""}</p>`,
        event.created);
    }
    if (event.kind === "plan_run_failed") {
      return notice(`${T(FAILURE[d.failure_kind] || FAILURE.ERROR)} <span class="muted">${d.error ? esc(UI.error(d.error)) : ""}</span>`, event.created, true);
    }
    if (event.kind === "plan_run_cancelled") return notice(T("Planning was stopped."), event.created);
    if (event.kind === "approved") {
      return notice(`${T("You approved version")} ${esc(d.version)}.`, event.created);
    }
    if (event.kind === "goal_cancelled") return notice(T("This goal was cancelled."), event.created, true);
    return "";
  }

  // ---- the page ------------------------------------------------------------------------------

  function versionsHtml(goal) {
    if (!goal.versions.length) return "";
    return `<details class="goal-versions"><summary>${T("Versions")} (${esc(goal.versions.length)})</summary>
      <ul>${goal.versions.slice().reverse().map((v) => `
        <li>v${esc(v.version)} · ${v.source === "provider" ? T("Delivery PM") : T("Imported")} · ${esc(v.phase_count)} ${T("phases")}
          ${v.approved ? `<span class="pill positive">${T("Approved")}</span>` : ""}
          ${v.version === goal.current_version ? `<span class="pill accent">${T("Current")}</span>` : ""}
          ${whenHtml(v.created)}</li>`).join("")}</ul></details>`;
  }

  function advancedHtml(goal) {
    const rows = [["Goal ID", goal.id], ["Plan version", goal.current_version || "—"],
      ["Plan hash (sha256)", goal.plan ? goal.plan.sha256 : "—"],
      ["Approval", goal.approval ? goal.approval.id + " · " + goal.approval.identity : "—"],
      ["Planning runs used", goal.limits.planning_runs_used + " / " + goal.limits.max_planning_runs]];
    return `<details class="goal-advanced"><summary>${T("Advanced")}</summary>
      <dl>${rows.map(([k, v]) => `<dt>${T(k)}</dt><dd class="mono">${esc(v)}</dd>`).join("")}</dl></details>`;
  }

  function actionsHtml(goal) {
    if (goal.state === "CANCELLED") return "";
    const planning = Boolean(goal.live_run);
    const hasPlan = Boolean(goal.plan);
    const needsApproval = hasPlan && goal.approved_version !== goal.plan.version;
    const working = Boolean(goal.goal_run);                  // a goal run exists: it owns what happens next
    return `
      <section class="goal-actions">
        ${planning ? `
          <div class="goal-working"><span class="status-dot pending"></span> ${T("The Delivery PM is planning. This can take a few minutes.")}
            <button class="btn small" id="goal-stop-plan" type="button">${T("Stop planning")}</button></div>` : `
          ${hasPlan ? `
            ${working ? "" : `<label for="goal-change">${T("Ask for changes")}</label>
            <textarea id="goal-change" rows="3" maxlength="4000" placeholder="${esc(UI.translate("For example: split the first phase, or add a security review."))}"></textarea>`}
            <div class="goal-buttons">
              ${working ? "" : `<button class="btn" id="goal-revise" type="button">${T("Ask the Delivery PM to revise")}</button>`}
              ${needsApproval ? `<button class="btn primary" id="goal-approve" type="button">${T("Approve this plan")}</button>` : ""}
            </div>` : `
            <div class="goal-buttons"><button class="btn primary" id="goal-plan" type="button">${T("Ask the Delivery PM to plan")}</button></div>`}`}
        ${goal.state === "READY" ? `<p class="goal-ready">${T("Approved. Run the whole goal, or one phase, when you are ready; nothing starts by itself.")}</p>` : ""}
        <div class="goal-buttons end"><button class="btn danger small" id="goal-cancel" type="button">${T("Cancel goal")}</button></div>
      </section>`;
  }

  function signature(goal, runs) {
    return JSON.stringify([goal.state, goal.current_version, goal.events.length, goalRunUi.signature(goal),
      goal.live_run && goal.live_run.status, goal.approved_version,
      (goal.phase_runs || []).map((r) => [r.id, r.status]), phasesUi.signature(runs || [])]);
  }

  async function renderGoalDetail() {
    const gen = getGen();
    const route = parseRoute();
    const project = route.project;
    const goalId = route.sub;
    const path = `${base(project)}/${encodeURIComponent(goalId)}`;
    layout(loadingView());
    let goal;
    let runs = [];
    try { goal = await api(path); runs = await phasesUi.load(path, goal); }
    catch (err) { if (!stale(gen)) errorView(err); return; }
    if (stale(gen)) return;

    const pageHtml = () => `
        <div class="title-row"><div>
          <a class="muted" href="#/p/${encodeURIComponent(project)}/tasks">${T("All tasks")}</a>
          <h1><span data-no-i18n>${esc(goal.title)}</span> ${statePill(goal.state)}
            <button type="button" class="btn small quiet" id="goal-rename">${T("Rename")}</button></h1>
          <div class="sub">${T(goal.next_action)}</div></div></div>
        <section class="chat-thread goal-thread" aria-label="${esc(UI.translate("Conversation"))}">${goal.events.map(eventHtml).join("")}</section>
        ${goal.plan ? planHtml(goal.plan, goal) : ""}
        ${goal.goal_run ? goalRunUi.panelHtml(goal.goal_run, goal) : goalRunUi.startHtml(goal)}
        ${runs.map(phasesUi.panelHtml).join("")}
        ${actionsHtml(goal)}
        ${versionsHtml(goal)}
        ${advancedHtml(goal)}
      `;
    // The first paint builds the page; every later one patches it in place, so what the person is
    // typing, reading or has open stays exactly as it is.
    let painted = false;
    const draw = () => {
      if (painted) morphContent(pageHtml());
      else { layout(pageHtml()); painted = true; }
      bind();
    };

    const reload = async () => {
      try { goal = await api(path); runs = await phasesUi.load(path, goal); } catch (err) { toast(err.message, true); return; }
      if (!stale(gen)) draw();
    };
    const call = async (suffix, body) => {
      try { await api(`${path}${suffix}`, { method: "POST", body: body || {} }); }
      catch (err) { toast(err.message, true); }
      await reload();
    };

    function bind() {
      const on = (id, handler) => { const el = document.getElementById(id); if (el) el.onclick = handler; };
      const sheet = document.getElementById("sheet");
      phasesUi.bind(sheet, { goal, project, reload, runs });
      goalRunUi.bind(sheet, { goal, project, reload });
      on("goal-rename", () => showModal({
        title: "Rename this work", body: "Only the name changes. The request and the plan stay as they were.",
        fields: [{ label: UI.translate("Name"), value: goal.title }], confirmLabel: "Rename",
        onSubmit: async ([title]) => { await api(`${path}/rename`, { method: "POST", body: { title } }); await reload(); },
      }));
      on("goal-plan", () => call("/plan"));
      on("goal-revise", () => {
        const text = document.getElementById("goal-change").value.trim();
        if (!text) { toast("Describe what to change first.", true); return; }
        call("/plan", { change_request: text, base_version: goal.current_version });
      });
      on("goal-stop-plan", () => call("/plan/cancel"));
      on("goal-approve", () => {
        const challenge = "APPROVE " + goal.id + " v" + goal.plan.version;
        showModal({
          title: "Approve this plan",
          body: "You are approving exactly this version of the plan. It does not approve commit, push, release, deploy, wider access, a larger budget or an untrusted role. It starts work only if you tick the box below.",
          fields: [{ label: `${UI.translate("Type")} ${challenge} ${UI.translate("to confirm")}`, placeholder: challenge },
            { type: "checkbox", label: UI.translate("Start working right after approving"), checked: true }],
          confirmLabel: "Approve",
          onSubmit: async ([typed, startNow]) => {
            if (typed !== challenge) throw new Error(UI.translate("Confirmation text did not match."));
            await api(`${path}/approve`, { method: "POST", body: { version: goal.plan.version, sha256: goal.plan.sha256, confirm: typed } });
            toast("Plan approved.");
            if (startNow && !goal.goal_run) {
              // The person chose this here, in the same act; approving alone still starts nothing.
              try { await api(`/projects/${encodeURIComponent(project)}/goals/${encodeURIComponent(goal.id)}/run`, { method: "POST", body: {} }); toast("Started."); }
              catch (err) { toast(err.message, true); }
            }
            await reload();
          },
        });
      });
      on("goal-cancel", () => {
        showModal({
          title: "Cancel this work", body: "Cancelled work cannot be reopened. Its history is kept.",
          fields: [{ label: UI.translate("Reason (optional)"), placeholder: "" }], confirmLabel: "Cancel work", danger: true,
          onSubmit: async ([reason]) => { await api(`${path}/cancel`, { method: "POST", body: { reason } }); await reload(); },
        });
      });
    }

    draw();
    livePoll(gen, async () => {
      const fresh = await api(path);
      const freshRuns = await phasesUi.load(path, fresh);
      if (stale(gen)) return;
      if (signature(fresh, freshRuns) !== signature(goal, runs)) { goal = fresh; runs = freshRuns; draw(); }
    });
  }

  return { renderGoalDetail };
};
