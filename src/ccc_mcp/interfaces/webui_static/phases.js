// Phase runs (Stage B), shown on a goal's page: one phase at a time, told as a conversation. The
// story is first ("the test failed, the Delivery PM found the cause, the developer fixed it, the
// test passed"); ids, hashes and raw records are in the expandable evidence under it. Nothing here
// says the next phase will start by itself, because it does not. Everything that comes from the
// server is put into the page through esc(), never as raw markup.
window.cccPhases = function (ctx) {
  "use strict";
  // Assigning onclick replaces the previous handler, so binding again after an in-place update never stacks them.
  const click = (element, handler) => { element.onclick = handler; };
  const { api, esc, UI, toast, showModal, whenHtml } = ctx;
  const T = (text) => esc(UI.translate(text));

  const RUN_STATE = {
    STARTING: ["accent", "Starting"],
    RUNNING: ["accent", "Working"],
    PAUSED: ["neutral", "Paused"],
    NEEDS_HUMAN: ["negative", "Waiting for you"],
    AWAITING_ACCEPTANCE: ["accent", "Waiting for your acceptance"],
    PASSED: ["positive", "Passed"],
    EXHAUSTED: ["negative", "Stopped: a limit was reached"],
    CANCELLED: ["neutral", "Stopped by you"],
  };
  const OPEN = ["STARTING", "RUNNING", "PAUSED", "NEEDS_HUMAN", "AWAITING_ACCEPTANCE"];
  const ACTIVE = ["STARTING", "RUNNING"];
  const GATE = { PASS: "positive", NEEDS_REVISION: "negative", REJECT: "negative" };

  const agentName = (agent) => (agent === "codex" ? "Codex" : agent === "claude" ? "Claude Code" : agent || "");
  const pill = (tone, label) => `<span class="pill ${tone}">${T(label)}</span>`;
  const runPill = (status) => { const [tone, label] = RUN_STATE[status] || ["neutral", status]; return pill(tone, label); };

  function post(name, avatar, bodyHtml, when, tone) {
    return `<article class="chat-post ${tone === "error" ? "has-error" : ""}">
      <div class="chat-avatar goal-avatar" aria-hidden="true" data-no-i18n>${esc(avatar)}</div>
      <div class="chat-post-body"><div class="chat-post-meta"><strong data-no-i18n>${esc(name)}</strong>${when ? `<time>${whenHtml(when)}</time>` : ""}</div>
      <div class="chat-bubble">${bodyHtml}</div></div></article>`;
  }

  function notice(html, when, negative) {
    return `<div class="chat-system-entry ${negative ? "is-negative" : ""}"><div class="chat-notice"><span class="chat-notice-dot" aria-hidden="true"></span><span>${html}</span>${when ? ` <time>${whenHtml(when)}</time>` : ""}</div></div>`;
  }

  function codeBlock(text) {
    return `<pre class="phase-code mono">${esc(text)}</pre>`;
  }

  // ---- what to say about one attempt -----------------------------------------------------------

  function pmPost(repair, run) {
    const spec = repair.spec || {};
    if (repair.status === "FAILED" || repair.status === "INTERRUPTED") {
      return post(UI.translate("Delivery PM"), "PM",
        `<p>${T("I could not produce a usable repair.")}</p><p class="muted">${esc(repair.error || "")}</p>`, repair.finished, "error");
    }
    const causes = (spec.root_causes || []).map((cause) => `
      <li>${pill(cause.confidence === "confirmed" ? "positive" : "neutral", cause.confidence === "confirmed" ? "Confirmed" : "Hypothesis")}
        ${esc(cause.statement)}${(cause.evidence || []).map((item) => `<div class="phase-quote mono">${esc(item.quote)}${item.verified ? "" : ` <span class="muted">(${T("not found in the evidence")})</span>`}</div>`).join("")}</li>`).join("");
    const scope = (spec.change_scope || []).length ? `<p>${T("Where to change")}: <code class="mono">${(spec.change_scope || []).map(esc).join(", ")}</code></p>` : "";
    const decision = spec.decision === "needs_human"
      ? `<p><strong>${T("I need a person to look at this.")}</strong> ${esc(spec.needs_human_reason || "")}</p>`
      : `<p>${T("Sent back to the developer. The host will run the same tests again.")}${spec.needs_reanalysis ? ` ${T("The analysis and design are redone first.")}` : ""}</p>`;
    return post(UI.translate("Delivery PM"), "PM",
      `${spec.message ? `<p>${esc(spec.message)}</p>` : ""}<p>${esc(spec.diagnosis || "")}</p>${causes ? `<ul class="phase-causes">${causes}</ul>` : ""}${scope}${decision}`,
      repair.finished);
  }

  function storyHtml(run) {
    const max = run.budget.attempts.max;
    const builders = run.executions.filter((e) => (e.role === "RD" || e.role === "SD/RD") && !e.fallback_from);
    const parts = [notice(`${T("Phase")} <span class="mono">${esc(run.phase_id)}</span> ${run.goal_run_id ? T("was started by the goal run.") : T("was started by you.")}`, run.created)];
    run.attempts.forEach((att) => {
      const dev = builders[att.number - 1];
      const evidence = att.evidence || {};
      const files = evidence.changed_files || [];
      const who = dev ? agentName(dev.agent) + " · " + dev.role : UI.translate("Developer");
      parts.push(post(who, "DEV",
        `<p>${T("Attempt")} ${esc(att.number)} / ${esc(max)}${att.kind === "REPAIR" ? ` · ${T("a repair")}` : ""}</p>${files.length
          ? `<p>${T("Changed files")}: <code class="mono">${files.map(esc).join(", ")}</code></p>`
          : (att.status === "RUNNING" ? "" : `<p class="muted">${T("No changed files were recorded.")}</p>`)}`, att.started));
      if (att.status === "PASSED") {
        parts.push(notice(T("The host test passed."), att.finished));
      } else if (evidence.kind === "host_test_failed" || evidence.kind === "no_tests_collected") {
        parts.push(notice(`${T("The host test failed.")}${(evidence.failing_lines || evidence.signature_lines || []).length ? codeBlock((evidence.failing_lines || evidence.signature_lines).slice(0, 5).join("\n")) : ""}`, att.finished, true));
      } else if (evidence.kind === "review_not_passed") {
        parts.push(notice(T("The host test passed, but the review did not."), att.finished, true));
      } else if (att.status === "FAILED" && att.outcome) {
        parts.push(notice(T("This attempt ended without a result"), att.finished, true));
      }
      run.repairs.filter((r) => r.after_attempt === att.number).forEach((repair) => parts.push(pmPost(repair, run)));
    });
    if (run.gates && run.gates.length) {
      parts.push(notice(run.gates.map((g) => `${esc(g.role)}: ${pill(GATE[g.verdict] || "neutral", g.verdict === "PASS" ? "Passed" : "Sent back")}`).join(" · "), null));
    }
    if (run.active_execution) {
      parts.push(notice(`${esc(agentName(run.active_execution.agent))} · ${esc(run.active_execution.role)} ${T("is working now.")}`, run.active_execution.created));
    }
    const closing = {
      PASSED: "The phase passed: the host test passed, the review passed and nothing protected was touched.",
      AWAITING_ACCEPTANCE: "The tests passed. The remaining criteria can only be judged by you.",
    }[run.status];
    if (closing) parts.push(notice(T(closing), run.finished || run.updated));
    else if (run.status === "EXHAUSTED") parts.push(notice(`${T("Stopped.")} ${T(run.reason_text || "A limit was reached.")}`, run.finished, true));
    else if (run.status === "NEEDS_HUMAN") parts.push(notice(`${T(run.reason_text || "A person is needed.")}`, run.updated, true));
    else if (run.status === "PAUSED") parts.push(notice(T("Paused."), run.updated));
    else if (run.status === "CANCELLED") parts.push(notice(T("You stopped this phase."), run.finished));
    return `<section class="chat-thread phase-thread">${parts.join("")}</section>`;
  }

  // ---- the panel ------------------------------------------------------------------------------

  function meter(label, used, max, unit) {
    const pct = max ? Math.min(100, Math.round((used / max) * 100)) : 0;
    return `<div class="phase-meter"><span>${T(label)}</span><span class="mono">${esc(used)} / ${esc(max)}${unit ? " " + T(unit) : ""}</span>
      <div class="phase-bar" aria-hidden="true"><div style="width:${pct}%"></div></div></div>`;
  }

  function budgetHtml(run) {
    const b = run.budget;
    return `<div class="phase-budget">
      ${meter("Attempts", b.attempts.used, b.attempts.max)}
      ${meter("Agent runs", b.dispatches.used, b.dispatches.max)}
      ${meter("Minutes", b.minutes.used, b.minutes.max)}
      <p class="muted">${T("These limits are enforced by the program. Resuming never adds to them.")}</p></div>`;
  }

  function evidenceHtml(run) {
    const host = run.host_test;
    const rows = [["Run", run.id], ["Task", run.task_id], ["Plan version", run.plan_version],
      ["Plan hash (sha256)", run.plan_sha256], ["Approval", run.approval_id],
      ["Workflow", run.workflow_id], ["Test command", (run.test_command || []).join(" ")]];
    const executions = run.executions.map((e) => `<tr><td class="mono">${esc(e.id)}</td><td>${esc(e.role)}</td><td>${esc(e.agent)}</td><td>${esc(e.status)}</td></tr>`).join("");
    const repairs = run.repairs.map((r) => `<li class="mono">${esc(r.id)} · ${T(r.status)} · ${T("evidence hash")} ${esc(r.input_sha256)}${r.message_to_developer ? `<details><summary>${T("What the developer was told")}</summary>${codeBlock(r.message_to_developer)}</details>` : ""}</li>`).join("");
    return `<details class="goal-advanced phase-evidence"><summary>${T("Technical evidence")}</summary>
      <dl>${rows.map(([k, v]) => `<dt>${T(k)}</dt><dd class="mono">${esc(v)}</dd>`).join("")}</dl>
      ${host ? `<h4>${T("Latest host test")}</h4><p class="mono">${esc((host.command || []).join(" "))} · ${host.passed ? T("passed") : T("did not pass")}</p>${codeBlock((host.stdout || "") + (host.stderr ? "\n" + host.stderr : ""))}` : ""}
      ${executions ? `<h4>${T("Agent runs")}</h4><table class="task-table"><tbody>${executions}</tbody></table>` : ""}
      ${repairs ? `<h4>${T("Delivery PM analyses")}</h4><ul>${repairs}</ul>` : ""}
      <h4>${T("Record")}</h4>${codeBlock(run.events.map((e) => [e.created, e.kind, e.actor].join("  ")).join("\n"))}</details>`;
  }

  function controlsHtml(run) {
    const open = OPEN.includes(run.status);
    if (!open) return "";
    const buttons = [];
    if (ACTIVE.includes(run.status)) buttons.push(`<button class="btn" data-phase-act="pause" type="button">${T("Pause")}</button>`);
    if (run.status === "PAUSED" || (run.status === "NEEDS_HUMAN" && run.resumable)) {
      buttons.push(`<button class="btn primary" data-phase-act="resume" type="button">${T("Resume")}</button>`);
    }
    if (run.status === "AWAITING_ACCEPTANCE") buttons.push(`<button class="btn primary" data-phase-act="accept" type="button">${T("Review and accept")}</button>`);
    if (run.stalled) buttons.push(`<button class="btn" data-phase-act="recover" type="button">${T("Pick this up again")}</button>`);
    buttons.push(`<button class="btn danger small" data-phase-act="cancel" type="button">${T("Stop this phase")}</button>`);
    return `<div class="goal-buttons">${buttons.join("")}</div>
      ${run.stalled ? `<p class="muted">${T("Nothing has moved for a while and no process is working on it.")}</p>` : ""}`;
  }

  function panelHtml(run) {
    return `<section class="goal-plan phase-run" data-run="${esc(run.id)}">
      <h2>${T("Phase")} <span class="mono">${esc(run.phase_id)}</span> ${runPill(run.status)}
        <small>${T("Attempt")} ${esc(Math.max(run.attempts.length, 1))} / ${esc(run.budget.attempts.max)}</small></h2>
      ${storyHtml(run)}
      ${controlsHtml(run)}
      ${budgetHtml(run)}
      <p class="muted">${run.goal_run_id
        ? T("This phase belongs to a goal run. The next phase starts by itself when this one passes.")
        : T("Only this phase runs. The next phase is never started automatically.")}</p>
      ${evidenceHtml(run)}</section>`;
  }

  // ---- what the plan list needs ------------------------------------------------------------------

  function runsOf(goal) { return goal.phase_runs || []; }

  async function load(path, goal) {
    const runs = await Promise.all(runsOf(goal).map((r) =>
      api(`/projects/${encodeURIComponent(goal.project_id)}/phase-runs/${encodeURIComponent(r.id)}`)));
    return runs;
  }

  function signature(runs) {
    return JSON.stringify(runs.map((r) => [r.id, r.status, r.reason, r.attempts.length,
      r.repairs.map((x) => x.status + (x.restart_execution_id || "")).join(), r.executions.map((e) => e.status).join(),
      r.budget.dispatches.used, r.stalled, r.events.length]));
  }

  function anyActive(runs) { return runs.some((r) => ACTIVE.includes(r.status)); }

  // ---- buttons ------------------------------------------------------------------------------------

  function bind(root, env) {
    const { goal, project, reload } = env;
    const base = `/projects/${encodeURIComponent(project)}`;
    const send = async (url, body) => {
      try { await api(url, { method: "POST", body: body || {} }); }
      catch (err) { toast(err.message, true); }
      await reload();
    };
    root.querySelectorAll("[data-phase-show]").forEach((button) => {
      click(button, () => {
        const panel = root.querySelector(`[data-run="${CSS.escape(button.dataset.phaseShow)}"]`);
        if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
    root.querySelectorAll("[data-run]").forEach((panel) => {
      const id = panel.dataset.run;
      const url = (action) => `${base}/phase-runs/${encodeURIComponent(id)}/${action}`;
      const run = (env.runs || []).find((r) => r.id === id) || {};
      panel.querySelectorAll("[data-phase-act]").forEach((button) => {
        click(button, () => {
          const act = button.dataset.phaseAct;
          if (act === "pause") send(url("pause"));
          else if (act === "recover") send(`${base}/phase-runs/recover`);
          else if (act === "resume") {
            const protectedPaths = run.reason === "PROTECTED_PATH";
            showModal({
              title: "Resume this phase",
              body: "It carries on from where it stopped. Nothing that already ran is run again, and no limit is raised.",
              fields: [{ label: UI.translate("A note for the Delivery PM (optional)"), placeholder: "" },
                ...(protectedPaths ? [{ label: UI.translate("Protected files were changed. Type protected-paths to continue anyway"), placeholder: "protected-paths" }] : [])],
              confirmLabel: "Resume",
              onSubmit: async ([note, ack]) => {
                await api(url("resume"), { method: "POST", body: { note: note || "", acknowledge: protectedPaths ? ack : undefined } });
                await reload();
              },
            });
          } else if (act === "cancel") {
            showModal({
              title: "Stop this phase", body: "The phase ends and its task is stopped. What was already written to the files stays.",
              fields: [{ label: UI.translate("Reason (optional)"), placeholder: "" }], confirmLabel: "Stop this phase", danger: true,
              onSubmit: async ([reason]) => { await api(url("cancel"), { method: "POST", body: { reason } }); await reload(); },
            });
          } else if (act === "accept") {
            api(`${url("accept-preview")}`).then((info) => {
              const challenge = info.challenge;
              showModal({
                title: "Accept the remaining criteria",
                body: "The tests and the review passed. By accepting you say that you checked the criteria below yourself: " + (info.criteria || []).map((c) => c.id + " " + c.statement).join("; "),
                fields: [{ label: `${UI.translate("Type")} ${challenge} ${UI.translate("to confirm")}`, placeholder: challenge }],
                confirmLabel: "Accept",
                onSubmit: async ([typed]) => {
                  if (typed !== challenge) throw new Error(UI.translate("Confirmation text did not match."));
                  await api(url("accept"), { method: "POST", body: { confirm: typed } });
                  toast("Phase accepted.");
                  await reload();
                },
              });
            }).catch((err) => toast(err.message, true));
          }
        });
      });
    });
  }

  return { panelHtml, load, signature, anyActive, bind, runsOf };
};
