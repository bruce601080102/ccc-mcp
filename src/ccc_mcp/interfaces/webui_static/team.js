// "AI team and workflow": one card on the project page.
//
// What a person sees: the workflow they picked, its steps in the order they run, and for each step the
// role's name and which AI tool plays it. Model, effort and quota fallback are one click away in a
// step's "More", review gates are folded under "More options", and there is one Save button for the
// whole card. Your own workflows (a name and an ordered list of roles) and your own roles (a name, a
// base role and instructions) are made in two small dialogs. Nothing here can widen what a role may
// do: that is decided by the role's base role in ccc-mcp's own code.
//
// Everything from the server is put into the page through esc().
window.cccTeam = function (ctx) {
  "use strict";
  const { api, esc, UI, toast, mountModal, closeModal } = ctx;
  const T = (text) => esc(UI.translate(text));

  const ROLE_LABEL = {
    SA: "Analyst", SD: "Designer", RD: "Developer", "SD/RD": "Designer and developer in one step",
    REVIEW: "Code reviewer", SR: "Specification reviewer", AR: "Adversarial reviewer",
    SEC: "Security reviewer", QA: "Tester", SW: "Specification writer", PA: "Personal assistant",
    RS: "Researcher", AN: "Data analyst", PL: "Planner", CS: "Consultant", WR: "Writer", ED: "Editor",
    VC: "Fact checker", RV: "Reviewer",
  };
  const PROVIDER_LABEL = { chatgpt: "ChatGPT", codex: "Codex", claude: "Claude Code" };
  const SOFTWARE = new Set(["classic", "standard", "full"]);
  // Built-in workflow names come from the server in English.
  const flowName = (flow) => (flow.builtin ? UI.translate(flow.name) : flow.name);
  const roleLabel = (role) => UI.translate(ROLE_LABEL[role] || role);

  // ---- the draft: what is on screen, compared with what is saved ------------------------------------

  function snapshot(overview) {
    const roles = {};
    for (const [role, r] of Object.entries(overview.editable.roles || {})) {
      roles[role] = { provider: r.provider, model: r.model || "", effort: r.effort || "", quota_fallback: r.quota_fallback || "" };
    }
    const custom = {};
    for (const role of overview.custom_roles || []) {
      const r = role.runtime || {};
      custom[role.id] = { provider: r.provider || "", model: r.model || "", effort: r.effort || "", quota_fallback: r.quota_fallback || "" };
    }
    const gate = overview.editable.review_gate || {};
    return {
      workflow: overview.editable.workflow, selection: overview.editable.workflow_selection, roles, custom,
      gate: { ar_strictness: gate.ar_strictness || "normal", max_rounds: gate.max_rounds ?? 2, escalation: gate.escalation || "ask_user" },
    };
  }

  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  // ---- markup --------------------------------------------------------------------------------------

  function providerSelect(value, field, allowDefault, defaultLabel) {
    return `<select data-field="${field}" aria-label="${esc(UI.translate("AI tool"))}">
      ${allowDefault ? `<option value="">${esc(UI.translate(defaultLabel))}</option>` : ""}
      ${Object.entries(PROVIDER_LABEL).map(([id, label]) => `<option value="${id}"${id === value ? " selected" : ""}>${esc(label)}</option>`).join("")}
    </select>`;
  }

  function stepHtml(step, index, overview, draft, firstSeen) {
    const custom = step.role_ref ? (overview.custom_roles || []).find((r) => r.id === step.role_ref) : null;
    const key = step.role_ref || step.role;
    const entry = step.role_ref ? draft.custom[key] : draft.roles[key];
    if (!entry) return "";
    const inherited = custom && draft.roles[custom.base_role]?.provider;
    const tool = PROVIDER_LABEL[entry.provider || inherited] || T("Unavailable");
    if (firstSeen.has(key)) {
      // A role that appears twice is one setting: it is edited on its first step only.
      return `<li class="team-step is-repeat" data-step="${index}" data-key="${esc(key)}:${index}">
        <span class="team-step-no" aria-hidden="true">${index + 1}</span>
        <span class="team-step-name"><strong data-no-i18n>${esc(custom ? custom.name : roleLabel(step.role))}</strong></span>
        <span class="team-step-tool">${tool}</span>
        <span class="team-step-actions"><button type="button" class="btn small quiet" data-repeat="${firstSeen.get(key)}">${T("Advanced")}</button></span></li>`;
    }
    firstSeen.set(key, index);
    const efforts = overview.available_providers[entry.provider] || [];
    const label = custom ? custom.name : roleLabel(step.role);
    return `<li class="team-step" data-step="${index}" data-key="${esc(key)}:${index}">
      <span class="team-step-no" aria-hidden="true">${index + 1}</span>
      <span class="team-step-name"><strong data-no-i18n>${esc(label)}</strong></span>
      <span class="team-step-tool">${custom ? tool : providerSelect(entry.provider, "provider", false)}</span>
      <span class="team-step-actions">
        ${custom ? `<button type="button" class="btn small quiet" data-edit-role="${esc(custom.id)}">${T("Advanced")}</button>`
          : `<button type="button" class="btn small quiet" data-more="${index}" aria-controls="team-role-options-${index}" aria-expanded="false">${T("Advanced")}</button>`}
      </span>
      ${custom ? "" : `<div class="team-step-more" id="team-role-options-${index}" data-more-panel="${index}" hidden>
        <label>${T("Model (optional)")}<input type="text" data-field="model" value="${esc(entry.model)}" placeholder="${T("Provider default")}" ${entry.provider === "chatgpt" ? "disabled" : ""} /></label>
        <label>${T("Effort")}<select data-field="effort"><option value="">${T("Provider default")}</option>${efforts.map((e) => `<option value="${esc(e)}"${e === entry.effort ? " selected" : ""}>${T(e)}</option>`).join("")}</select></label>
        <label>${T("If quota runs out")}<select data-field="quota_fallback"><option value="">${esc(UI.translate("No fallback"))}</option>${Object.entries(PROVIDER_LABEL).map(([id, l]) => `<option value="${id}"${id === entry.quota_fallback ? " selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
        <small><a href="#/settings">${T("CLI installation and sign-in")}</a></small>
      </div>`}
    </li>`;
  }

  function html(overview, draftIn) {
    const draft = draftIn || snapshot(overview);
    const flows = overview.workflow_choices || [];
    const chosen = flows.find((f) => f.id === draft.workflow) || flows.find((f) => !f.legacy) || flows[0];
    if (!chosen) return "";
    const group = (label, list) => list.length
      ? `<optgroup label="${esc(UI.translate(label))}">${list.map((f) => `<option value="${esc(f.id)}"${f.id === chosen.id ? " selected" : ""}>${esc(flowName(f))}</option>`).join("")}</optgroup>` : "";
    const flowSelect = group("Software teams", flows.filter((f) => SOFTWARE.has(f.id)))
      + group("Everyday teams", flows.filter((f) => f.builtin && !SOFTWARE.has(f.id)))
      + group("Your workflows", flows.filter((f) => !f.builtin));
    const mine = !chosen.builtin;
    const firstSeen = new Map();
    const steps = chosen.steps.map((s, i) => stepHtml(s, i, overview, draft, firstSeen)).join("");
    const roles = (overview.custom_roles || []).filter((r) => r.status !== "imported_pending");
    const gate = draft.gate;
    const opt = (value, label, current) => `<option value="${value}"${value === current ? " selected" : ""}>${esc(UI.translate(label))}</option>`;
    return `<section class="settings-card team-card" id="team-card" data-key="team-card">
      <header class="settings-card-head"><div><h2>${T("AI team and workflow")}</h2>
        <p>${T("Choose the team for new work. Roles run in the order shown.")}</p></div></header>
      <div class="settings-card-body">
        <div class="team-flow-row">
          <label class="team-flow-label" for="team-workflow">${T("Workflow")}</label>
          <select id="team-workflow">${flowSelect}</select>
          <span class="team-flow-actions">
            <button type="button" class="btn small" id="team-new-workflow">${T("New workflow")}</button>
            ${mine ? `<button type="button" class="btn small" id="team-edit-workflow">${T("Edit")}</button><button type="button" class="btn small danger" id="team-delete-workflow">${T("Delete")}</button>` : ""}
          </span>
        </div>
        ${chosen.goal_ready === false ? `<p class="hint" id="team-goal-hint">${T("This team can run individual tasks. Automated goal phases require a developer step.")}</p>` : ""}
        <ol class="team-steps" aria-label="${esc(UI.translate("Steps, in the order they run"))}">${steps}</ol>
        <div class="team-roles-row">
          <button type="button" class="btn small" id="team-new-role">${T("New role")}</button>
          <details><summary>${T("Manage custom roles")}</summary>
            ${roles.length ? roles.map((r) => `<div class="team-library-row"><strong data-no-i18n>${esc(r.name)}</strong>
              <span class="muted">${T(r.status === "active" ? "Active" : "Disabled")}</span>
              <button type="button" class="btn small quiet" data-edit-role="${esc(r.id)}">${T("Edit")}</button>
              <button type="button" class="btn small quiet" data-role-status="${esc(r.id)}">${T(r.status === "active" ? "Disable" : "Enable")}</button></div>`).join("") : `<p class="muted">${T("None yet")}</p>`}
          </details>
        </div>
        <details class="settings-more team-more-options"><summary>${T("Advanced team settings")}</summary>
          <div class="team-presets">
            <label for="team-selection">${T("Who chooses the workflow")}</label>
            <select id="team-selection">${opt("fixed", "Use the selected workflow", draft.selection)}${opt("pm", "Let the task coordinator choose", draft.selection)}${opt("auto", "Choose by task description", draft.selection)}</select>
            ${Object.entries(overview.profile_presets || {}).filter(([, value]) => value).map(([id]) => `<button type="button" class="btn small" data-team-preset="${id}">${T(id === "chatgpt-only" ? "Use ChatGPT for all built-in roles" : "Use installed CLI providers")}</button>`).join("")}
          </div>
          <h3>${T("Review gates")}</h3>
          <div class="settings-grid">
            <div class="field"><label for="gate-ar">${T("Adversarial review strictness")}</label><select id="gate-ar">${opt("normal", "Normal: only clear faults", gate.ar_strictness)}${opt("strict", "Strict: minor issues too", gate.ar_strictness)}${opt("extreme", "Extreme: includes assumptions", gate.ar_strictness)}</select></div>
            <div class="field"><label for="gate-rounds">${T("Max rounds per gate (1-5)")}</label><input type="text" inputmode="numeric" id="gate-rounds" value="${esc(gate.max_rounds)}" /></div>
            <div class="field"><label for="gate-escalation">${T("When a gate gives up")}</label><select id="gate-escalation">${opt("ask_user", "Ask me", gate.escalation)}${opt("auto_pm", "ChatGPT decides", gate.escalation)}</select></div>
          </div>
        </details>
      </div>
      <footer class="settings-card-foot team-foot">
        <span class="team-dirty" id="team-dirty" role="status" aria-live="polite"></span>
        <button type="button" class="btn small" id="team-discard" disabled>${T("Discard changes")}</button>
        <button type="button" class="btn small primary" id="team-save" disabled>${T("Save")}</button>
      </footer>
    </section>`;
  }

  // ---- behaviour ------------------------------------------------------------------------------------

  function bind(projectId, overview, carried, notice = "") {
    const base = `/projects/${encodeURIComponent(projectId)}`;
    const saved = snapshot(overview);
    const flows = overview.workflow_choices || [];
    const card = document.getElementById("team-card");
    if (!card) return;
    const working = carried || JSON.parse(JSON.stringify(saved));

    const stepsOf = (id) => (flows.find((f) => f.id === id) || { steps: [] }).steps;
    let busy = false;
    let conflict = false;
    const dirty = () => !same(working, saved);
    const report = (err) => {
      conflict = err.status === 409;
      toast(err.message, true);
      paintState();
    };
    const repaint = (nextOverview, draft, focus, message) => {
      if (!card.isConnected) return;
      const scroll = document.getElementById("sheet");
      const top = scroll.scrollTop;
      const details = [...card.querySelectorAll("details")].map((item) => item.open);
      const expanded = new Set([...card.querySelectorAll("[data-more][aria-expanded=true]")]
        .map((button) => button.closest(".team-step").dataset.key));
      card.outerHTML = html(nextOverview, draft);
      const next = document.getElementById("team-card");
      next.querySelectorAll("details").forEach((item, i) => { item.open = Boolean(details[i]); });
      UI.localize(next);
      bind(projectId, nextOverview, draft, message);
      next.querySelectorAll("[data-more]").forEach((button) => {
        if (expanded.has(button.closest(".team-step").dataset.key)) button.click();
      });
      if (focus) next.querySelector(focus)?.focus({ preventScroll: true });
      scroll.scrollTop = top;
    };
    async function refresh(focus, message) {
      const next = await api(`${base}/settings`);
      repaint(next, null, focus, message);
    }
    async function refreshAfterSave(focus, message) {
      toast(message);
      try { await refresh(focus, message); }
      catch (err) {
        conflict = true;
        toast("Saved. Reload the saved settings before making more changes.", true);
        paintState();
      }
    }

    function readFields() {
      card.querySelectorAll(".team-step").forEach((li) => {
        const step = stepsOf(working.workflow)[Number(li.dataset.step)];
        if (!step) return;
        const entry = step.role_ref ? working.custom[step.role_ref] : working.roles[step.role];
        li.querySelectorAll("[data-field]").forEach((el) => { entry[el.dataset.field] = el.value.trim(); });
      });
      working.selection = card.querySelector("#team-selection").value;
      working.gate = {
        ar_strictness: document.getElementById("gate-ar").value,
        max_rounds: Number(document.getElementById("gate-rounds").value) || 0,
        escalation: document.getElementById("gate-escalation").value,
      };
    }

    function paintState() {
      const isDirty = dirty();
      card.querySelector("#team-save").disabled = busy || conflict || !isDirty;
      card.querySelector("#team-discard").disabled = busy || (!conflict && !isDirty);
      card.querySelector("#team-dirty").textContent = UI.translate(conflict
        ? "Settings changed in another session. Your draft is still here. Discard it to load the saved settings."
        : busy ? "Saving…" : isDirty ? "You have unsaved changes." : notice);
      card.querySelectorAll("#team-new-workflow, #team-edit-workflow, #team-delete-workflow, #team-new-role, [data-edit-role], [data-role-status]").forEach((el) => {
        el.disabled = busy || isDirty || conflict;
        el.title = isDirty ? UI.translate("Save or discard your changes first.") : "";
      });
    }

    card.refreshRoles = async () => {
      try {
        const next = await api(`${base}/settings`);
        if (!dirty()) repaint(next, null);
        else toast("Finish editing before reloading the role list.");
      } catch (err) { report(err); }
    };
    card.oninput = (event) => {
      if (event.target.matches("#team-workflow, [data-field=provider]")) return;
      readFields();
      paintState();
    };
    card.onchange = (event) => {
      const target = event.target;
      if (target.id === "team-workflow") {
        readFields();
        working.workflow = target.value;
        working.selection = "fixed";
        repaint(overview, working, "#team-workflow");
      } else if (target.matches("[data-field=provider]")) {
        const row = target.closest(".team-step");
        const step = stepsOf(working.workflow)[Number(row.dataset.step)];
        const role = working.roles[step.role];
        const efforts = overview.available_providers[target.value] || [];
        const effort = row.querySelector("[data-field=effort]");
        effort.replaceChildren(new Option(UI.translate("Provider default"), ""), ...efforts.map((e) => new Option(UI.translate(e), e)));
        if (efforts.includes(role.effort)) effort.value = role.effort;
        const model = row.querySelector("[data-field=model]");
        if (target.value !== role.provider) model.value = "";
        model.disabled = target.value === "chatgpt";
        readFields();
        paintState();
      }
    };
    card.onclick = (event) => {
      const repeat = event.target.closest("[data-repeat]");
      if (repeat) {
        const first = card.querySelector(`[data-step="${repeat.dataset.repeat}"] .team-step-actions button`);
        first?.click();
        first?.focus();
      }
      const more = event.target.closest("[data-more]");
      if (more) {
        const panel = card.querySelector(`[data-more-panel="${more.dataset.more}"]`);
        panel.hidden = !panel.hidden;
        more.setAttribute("aria-expanded", String(!panel.hidden));
      }
      const preset = event.target.closest("[data-team-preset]");
      if (preset) {
        for (const [role, provider] of Object.entries(overview.profile_presets[preset.dataset.teamPreset])) {
          working.roles[role] = { provider, model: "", effort: "", quota_fallback: "" };
        }
        repaint(overview, working, '[data-team-preset="' + preset.dataset.teamPreset + '"]');
      }
    };
    card.querySelector("#team-discard").onclick = async () => {
      try { await refresh("#team-workflow", ""); } catch (err) { report(err); }
    };
    card.querySelector("#team-save").onclick = async () => {
      if (busy || conflict || !dirty()) return;
      const roles = {};
      for (const [role, value] of Object.entries(working.roles)) {
        if (!same(value, saved.roles[role])) roles[role] = runtime(value);
      }
      busy = true;
      paintState();
      const content = card.querySelector(".settings-card-body");
      content.inert = true;
      try {
        await api(`${base}/config/team`, { method: "POST", body: {
          revision: overview.team_revision, changes: { workflow: working.workflow,
            workflow_selection: working.selection, roles, review_gate: working.gate },
        } });
        await refreshAfterSave("#team-workflow", "Team settings saved.");
      } catch (err) { report(err); }
      finally { busy = false; content.inert = false; paintState(); }
    };

    const flow = flows.find((f) => f.id === working.workflow);
    card.querySelector("#team-new-workflow").onclick = () => workflowDialog(base, overview, null, flow, refreshAfterSave, report);
    const editFlow = card.querySelector("#team-edit-workflow");
    if (editFlow) editFlow.onclick = () => workflowDialog(base, overview, flow, flow, refreshAfterSave, report);
    const deleteFlow = card.querySelector("#team-delete-workflow");
    if (deleteFlow) deleteFlow.onclick = () => confirmDelete(base, overview, flow, refreshAfterSave, report);
    card.querySelector("#team-new-role").onclick = () => roleDialog(base, overview, null, refreshAfterSave);
    let loadingRole = false;
    card.querySelectorAll("[data-edit-role]").forEach((el) => {
      el.onclick = async () => {
        if (loadingRole) return;
        loadingRole = true;
        try { await roleDialog(base, overview, el.dataset.editRole, refreshAfterSave, card); }
        finally { loadingRole = false; }
      };
    });
    card.querySelectorAll("[data-role-status]").forEach((el) => {
      el.onclick = () => {
        const role = overview.custom_roles.find((item) => item.id === el.dataset.roleStatus);
        const disable = role.status === "active";
        dialog(disable ? "Disable this role?" : "Enable this role?",
          `<p data-no-i18n>${esc(role.name)}</p>${disable ? `<p>${T("Pinned tasks keep using this role. Remove it from workflows before starting new work with those workflows.")}</p>` : ""}`,
          async () => {
            await api(`${base}/roles/${encodeURIComponent(role.id)}/status`, { method: "POST", body: { status: disable ? "disabled" : "active" } });
            await refreshAfterSave("#team-new-role", disable ? "Role disabled." : "Role enabled.");
          }, disable ? "Disable" : "Enable");
      };
    });
    paintState();
  }

  const runtime = (entry) => ({
    provider: entry.provider, model: entry.model || null, effort: entry.effort || null,
    quota_fallback: entry.quota_fallback || null,
  });

  // ---- dialogs ----------------------------------------------------------------------------------------

  function dialog(title, bodyHtml, onSave, saveLabel, onConflict) {
    closeModal();
    const overlay = document.createElement("div");
    overlay.id = "modal-overlay";
    overlay.className = "modal-overlay";
    overlay.innerHTML = `<div class="modal team-dialog" role="dialog" aria-modal="true" aria-labelledby="team-dialog-title">
      <h2 id="team-dialog-title">${T(title)}</h2><div class="team-dialog-body">${bodyHtml}</div>
      <p class="team-dialog-error" role="alert" hidden></p>
      <div class="actions"><button class="btn" id="modal-cancel" type="button">${T("Cancel")}</button>
        <button class="btn primary" id="modal-confirm" type="button">${T(saveLabel || "Save")}</button></div></div>`;
    mountModal(overlay);
    UI.localize(overlay);
    const confirm = overlay.querySelector("#modal-confirm");
    const cancel = overlay.querySelector("#modal-cancel");
    const body = overlay.querySelector(".team-dialog-body");
    const error = overlay.querySelector(".team-dialog-error");
    overlay.onclick = (event) => { if (event.target === overlay && !confirm.disabled) closeModal(); };
    cancel.onclick = closeModal;
    confirm.onclick = async () => {
      confirm.disabled = cancel.disabled = body.inert = true;
      error.hidden = true;
      try { await onSave(overlay); if (overlay.isConnected) closeModal(); }
      catch (err) {
        if (err.status === 409) onConflict?.(err);
        error.textContent = err.status === 409
          ? UI.translate(onConflict ? "This workflow changed elsewhere. Copy your edits, close this editor and discard the draft to load the latest settings."
            : "This item changed in another session. Copy your edits before closing, then reopen the editor.")
          : UI.error(err.message, err.status);
        error.hidden = false;
      } finally { confirm.disabled = cancel.disabled = body.inert = false; }
    };
    overlay.querySelector("input:not([disabled])")?.focus({ preventScroll: true });
    return overlay;
  }

  function roleOptions(overview, selected) {
    const builtin = Object.keys(ROLE_LABEL).filter((r) => (overview.editable.roles || {})[r]);
    const own = (overview.custom_roles || []).filter((r) => r.status === "active");
    return `<optgroup label="${esc(UI.translate("Built-in roles"))}">${builtin.map((r) => `<option value="${esc(r)}"${selected === r ? " selected" : ""}>${esc(roleLabel(r))} (${esc(r)})</option>`).join("")}</optgroup>`
      + (own.length ? `<optgroup label="${esc(UI.translate("Your roles"))}">${own.map((r) => `<option value="${esc(r.id)}"${selected === r.id ? " selected" : ""} data-no-i18n>${esc(r.name)}</option>`).join("")}</optgroup>` : "");
  }

  function workflowDialog(base, overview, existing, current, refresh, onConflict) {
    const own = (overview.custom_roles || []).filter((r) => r.status === "active");
    let steps = ((existing || current || { steps: [{ role: "SA" }, { role: "RD" }, { role: "REVIEW" }] }).steps).map((s) => s.role_ref || s.role);
    const name = existing ? existing.name : "";
    const overlay = dialog(existing ? "Edit workflow" : "New workflow", `
      <label class="field">${T("Name")}<input type="text" id="wf-name" maxlength="40" value="${esc(name)}" placeholder="${esc(UI.translate("For example: Careful release"))}" /></label>
      <p class="hint">${T("Steps run from top to bottom. The first step must make something (analyst, designer or developer); reviewers check what came before.")}</p>
      <ol class="team-wf-steps" id="wf-steps"></ol>
      <button type="button" class="btn small" id="wf-add">${T("Add a step")}</button>`, async () => {
      const typed = document.getElementById("wf-name").value.trim();
      const payload = steps.map((v) => (own.some((r) => r.id === v) ? { role_ref: v } : { role: v }));
      await api(`${base}/workflows`, { method: "POST", body: { steps: payload, name: typed || undefined,
        id: existing?.id, revision: overview.team_revision, select: true } });
      await refresh("#team-workflow", "Workflow saved.");
    }, undefined, onConflict);
    const list = overlay.querySelector("#wf-steps");
    const add = overlay.querySelector("#wf-add");
    const paint = () => {
      add.disabled = steps.length >= 12;
      list.innerHTML = steps.map((value, i) => `<li data-i="${i}">
        <span class="team-step-no">${i + 1}</span>
        <select data-pick aria-label="${T("Step role")}">${roleOptions(overview, value)}</select>
        <button type="button" class="btn small quiet" data-up ${i === 0 ? "disabled" : ""} aria-label="${esc(UI.translate("Move up"))}">↑</button>
        <button type="button" class="btn small quiet" data-down ${i === steps.length - 1 ? "disabled" : ""} aria-label="${esc(UI.translate("Move down"))}">↓</button>
        <button type="button" class="btn small quiet" data-remove ${steps.length === 1 ? "disabled" : ""} aria-label="${esc(UI.translate("Remove step"))}">✕</button></li>`).join("");
      UI.localize(list);
    };
    list.onclick = (e) => {
      const li = e.target.closest("li");
      if (!li) return;
      const i = Number(li.dataset.i);
      if (e.target.closest("[data-up]") && i > 0) [steps[i - 1], steps[i]] = [steps[i], steps[i - 1]];
      else if (e.target.closest("[data-down]") && i < steps.length - 1) [steps[i + 1], steps[i]] = [steps[i], steps[i + 1]];
      else if (e.target.closest("[data-remove]") && steps.length > 1) steps.splice(i, 1);
      else return;
      paint();
      const at = Math.min(i + (e.target.closest("[data-up]") ? -1 : e.target.closest("[data-down]") ? 1 : 0), steps.length - 1);
      list.querySelector(`[data-i="${at}"] [data-pick]`).focus({ preventScroll: true });
    };
    list.onchange = (e) => { const li = e.target.closest("li"); if (li && e.target.matches("[data-pick]")) steps[Number(li.dataset.i)] = e.target.value; };
    add.onclick = () => {
      if (steps.length >= 12) return;
      steps.push("REVIEW");
      paint();
      list.querySelector("li:last-child select").focus({ preventScroll: true });
    };
    paint();
  }

  function confirmDelete(base, overview, flow, refresh, onConflict) {
    dialog("Delete this workflow?", `<p data-no-i18n>${esc(flow.name)}</p><p class="hint">${T("Tasks already running it keep it. New tasks go back to the Classic workflow.")}</p>`, async () => {
      await api(`${base}/workflows/${encodeURIComponent(flow.id)}/delete`, { method: "POST", body: { revision: overview.team_revision } });
      await refresh("#team-workflow", "Workflow deleted.");
    }, "Delete", onConflict);
  }

  async function roleDialog(base, overview, roleRef, refresh, card) {
    let existing = null;
    if (roleRef) {
      try { existing = (await api(`${base}/roles/${encodeURIComponent(roleRef)}`)).role; }
      catch (err) { toast(err.message, true); return; }
      if (!card.isConnected) return;
    }
    const def = existing ? existing.definition : { name: "", prompt: { mode: "freeform", freeform: "", sections: {} } };
    const prompt = def.prompt || {};
    const text = prompt.mode === "freeform" ? prompt.freeform : Object.values(prompt.sections || {}).filter(Boolean).join("\n\n");
    const bases = Object.keys(ROLE_LABEL);
    const initial = def.runtime || {};
    const overlay = dialog(existing ? "Edit role" : "New role", `
      <label class="field">${T("Name")}<input type="text" id="role-name" maxlength="40" value="${esc(def.name)}" placeholder="${esc(UI.translate("For example: Payments reviewer"))}" /></label>
      <label class="field">${T("It works like")}<select id="role-base" ${existing ? "disabled" : ""}>${bases.map((b) => `<option value="${b}"${(existing ? existing.base_role : "REVIEW") === b ? " selected" : ""}>${esc(roleLabel(b))} (${b})</option>`).join("")}</select></label>
      <p class="hint">${T("What a role may do (for example whether it can change files) comes from the role it works like, in ccc-mcp's own code. Nothing written below can widen it.")}</p>
      <label class="field">${T("Instructions")}<textarea id="role-text" rows="7" maxlength="16000" placeholder="${T("Tell this role what to focus on, what to avoid, and what its findings should contain.")}">${esc(text)}</textarea></label>
      <label class="field">${T("AI tool")}${providerSelect(initial.provider, "role-provider", true, "Same as its base role")}</label>
      <details class="settings-more"><summary>${T("Advanced")}</summary>
        <label class="field">${T("Model (optional)")}<input id="custom-model" value="${esc(initial.model)}"></label>
        <label class="field">${T("Effort")}<select id="custom-effort"></select></label>
        <label class="field">${T("If quota runs out")}<select id="custom-fallback"><option value="">${T("No fallback")}</option>
          ${Object.entries(PROVIDER_LABEL).map(([id, label]) => `<option value="${id}"${id === initial.quota_fallback ? " selected" : ""}>${label}</option>`).join("")}
        </select></label>
        <a href="#/settings">${T("CLI installation and sign-in")}</a>
      </details>`, async (modal) => {
      const name = modal.querySelector("#role-name").value.trim();
      const instructions = modal.querySelector("#role-text").value;
      const values = runtime({ provider: modal.querySelector('[data-field="role-provider"]').value,
        model: modal.querySelector("#custom-model").value.trim(),
        effort: modal.querySelector("#custom-effort").value, quota_fallback: modal.querySelector("#custom-fallback").value });
      if (!name) throw new Error("Enter a role name.");
      if (existing) {
        await api(`${base}/roles/${encodeURIComponent(roleRef)}/update`, { method: "POST", body: {
          expected_version: existing.current_version, name,
          ...(instructions === text ? {} : { instructions }), runtime: values.provider ? values : null,
        } });
      } else {
        await api(`${base}/roles`, { method: "POST", body: {
          name, base_role: modal.querySelector("#role-base").value, instructions, runtime: values.provider ? values : null,
        } });
      }
      await refresh("#team-new-role", "Role saved.");
    });
    const provider = overlay.querySelector('[data-field="role-provider"]');
    const effort = overlay.querySelector("#custom-effort");
    const model = overlay.querySelector("#custom-model");
    const fallback = overlay.querySelector("#custom-fallback");
    function updateProvider(selected = "") {
      const efforts = overview.available_providers[provider.value] || [];
      effort.replaceChildren(new Option(UI.translate("Provider default"), ""), ...efforts.map((e) => new Option(UI.translate(e), e)));
      effort.value = efforts.includes(selected) ? selected : "";
      model.disabled = !provider.value || provider.value === "chatgpt";
      if (provider.value === "chatgpt") model.value = "";
      effort.disabled = !efforts.length;
      fallback.disabled = !provider.value;
    }
    provider.onchange = () => updateProvider(effort.value);
    updateProvider(initial.effort);

  }

  return { html, bind, refreshRoles: () => document.getElementById("team-card")?.refreshRoles() };
};
