// Tasks: the one place work is listed. A piece of work that has several steps is shown as a group
// (a Goal and the tasks its steps run); a one-off task stands alone. ChatGPT and this page read and
// change the same rows (GET /work, POST /work/goals), so what ChatGPT started is here, and what is
// started here is visible to ChatGPT.
//
// Nothing on this page reloads. The list asks the server whether its revision changed, and morphs
// only what moved, so a person typing a request, reading a group or scrolling keeps their place.
// Everything from the server goes through esc() before it is put into the page.
window.cccTasks = function (ctx) {
  "use strict";
  const { api, esc, UI, layout, navTo, toast, showModal, parseRoute, stale, getGen, livePoll, whenHtml,
    loadingView, errorView, rememberedProject, taskStateTone, confirmDeleteTask, removableStates } = ctx;
  const T = (text) => esc(UI.translate(text));

  // Day-to-day names for the roles; the code names stay in the data.
  const WORKER = {
    PM: "Project manager", SA: "Analyst", SD: "Designer", RD: "Developer", "SD/RD": "Developer",
    REVIEW: "Code reviewer", SR: "Security reviewer", AR: "Architecture reviewer", SEC: "Security reviewer",
    QA: "Tester", SW: "Writer",
  };
  const AGENT = { codex: "Codex", claude: "Claude", chatgpt: "ChatGPT" };
  const GOAL_STATE = {
    DRAFT: ["neutral", "Not planned yet"], PLANNING: ["accent", "Planning"],
    AWAITING_APPROVAL: ["warning", "Needs your approval"], READY: ["positive", "Approved"],
    RUNNING: ["accent", "Working"], VERIFYING: ["accent", "Final check"],
    WAITING_USER: ["warning", "Needs you"], PAUSED: ["neutral", "Paused"], PASSED: ["positive", "Done"],
    EXHAUSTED: ["negative", "Stopped at a limit"], FAILED: ["negative", "Could not finish"],
    CANCELLED: ["neutral", "Cancelled"],
  };
  const STEP_STATE = {
    PASSED: ["positive", "Done"], RUNNING: ["accent", "In progress"], STARTING: ["accent", "Starting"],
    PAUSED: ["neutral", "Paused"], NEEDS_HUMAN: ["warning", "Needs you"],
    AWAITING_ACCEPTANCE: ["warning", "Needs you"], EXHAUSTED: ["negative", "Stopped"],
    CANCELLED: ["neutral", "Cancelled"], READY: ["neutral", "Next"], BLOCKED: ["neutral", "Waiting"],
    NOT_STARTED: ["neutral", "Waiting"],
  };

  // The server writes a headline in English. The two that carry a name or a number are translated by shape.
  function headlineText(headline) {
    if (UI.getLocale() === "zh-TW") {
      let m = /^Working on “(.*)”\.$/.exec(headline);
      if (m) return `正在處理「${m[1]}」。`;
      m = /^The plan has (\d+) step\(s\) and needs your approval\.$/.exec(headline);
      if (m) return `計畫共 ${m[1]} 個步驟，等你核准。`;
    }
    return UI.translate(headline);
  }

  function workerText(working) {
    if (!working) return "";
    const role = UI.translate(WORKER[working.role] || working.role);
    const agent = AGENT[working.agent] || working.agent || "";
    return `${role}${agent ? ` (${agent})` : ""}`;
  }

  // ---- which groups are open: the person's choice wins, otherwise unfinished work is open ----------
  const openChoice = new Map();
  try {
    const saved = JSON.parse(sessionStorage.getItem("ccc_open_groups") || "{}");
    Object.keys(saved).forEach((key) => openChoice.set(key, Boolean(saved[key])));
  } catch (e) { /* the page works without it */ }
  const saveChoices = () => {
    try { sessionStorage.setItem("ccc_open_groups", JSON.stringify(Object.fromEntries(openChoice))); } catch (e) { /* ignore */ }
  };
  const isOpen = (group) => (openChoice.has(group.id) ? openChoice.get(group.id) : !group.finished);

  const newKey = () => {
    try { return "ui-" + crypto.randomUUID().replace(/-/g, ""); }
    catch (e) { return "ui-" + Math.random().toString(16).slice(2) + Date.now().toString(16); }
  };

  // ---- markup -----------------------------------------------------------------------------------

  function groupHtml(project, group) {
    const [tone, label] = GOAL_STATE[group.state] || ["neutral", group.state];
    const link = `#/p/${encodeURIComponent(project)}/tasks/${encodeURIComponent(group.id)}`;
    const { done, total } = group.progress;
    const percent = total ? Math.round((done / total) * 100) : 0;
    const open = isOpen(group);
    const worker = group.working ? `<span class="work-worker"><span class="status-dot pending" aria-hidden="true"></span> ${esc(workerText(group.working))}</span>` : "";
    const steps = group.phases.map((phase) => {
      const [stepTone, stepLabel] = STEP_STATE[phase.state] || ["neutral", phase.state];
      const inner = `<span class="pill ${stepTone}">${T(stepLabel)}</span>
        <span class="work-step-title">${esc(phase.title)}</span>
        ${phase.working ? `<span class="muted">${esc(workerText(phase.working))}</span>` : ""}`;
      const href = phase.task_id ? `#/p/${encodeURIComponent(project)}/tasks/${encodeURIComponent(phase.task_id)}` : "";
      return `<li data-key="step:${esc(phase.id)}">${href ? `<a class="work-step" href="${href}">${inner}</a>` : `<span class="work-step">${inner}</span>`}</li>`;
    }).join("");
    return `<section class="work-group${group.needs_user ? " needs-you" : ""}${group.finished ? " is-finished" : ""}" data-key="goal:${esc(group.id)}">
      <header class="work-group-head">
        <button type="button" class="work-toggle" data-toggle="${esc(group.id)}" aria-expanded="${open}" aria-label="${esc(UI.translate("Show or hide the steps"))}"><span aria-hidden="true">${open ? "▾" : "▸"}</span></button>
        <div class="work-group-main">
          <div class="work-group-titleline">
            <a class="work-group-title" href="${link}" data-no-i18n>${esc(group.title)}</a>
            <button type="button" class="btn small quiet" data-rename="${esc(group.id)}" data-title="${esc(group.title)}">${T("Rename")}</button>
          </div>
          <div class="work-group-line" data-no-i18n>${esc(headlineText(group.headline))}</div>
          ${worker}
        </div>
        <div class="work-group-side">
          ${group.needs_user ? `<span class="pill warning">${T("Needs you")}</span>` : ""}
          <span class="pill ${tone}">${T(label)}</span>
          ${total ? `<span class="work-progress" role="img" aria-label="${done}/${total}"><span style="width:${percent}%"></span></span><span class="muted num">${done}/${total}</span>` : ""}
        </div>
      </header>
      <ol class="work-steps"${open ? "" : " hidden"}>${steps || `<li class="muted work-nosteps">${T("Steps appear once there is a plan.")}</li>`}</ol>
    </section>`;
  }

  function singleHtml(project, task) {
    const link = `#/p/${encodeURIComponent(project)}/tasks/${encodeURIComponent(task.id)}`;
    const worker = task.working ? `<span class="work-worker"><span class="status-dot pending" aria-hidden="true"></span> ${esc(workerText(task.working))}</span>` : "";
    return `<div class="work-single" data-key="task:${esc(task.id)}">
      <div class="work-group-main">
        <a class="work-group-title" href="${link}" data-no-i18n>${esc(task.title || task.id)}</a>
        <div class="muted"><span class="mono">${esc(task.id)}</span> · ${whenHtml(task.created)}</div>
        ${worker}
      </div>
      <div class="work-group-side">
        ${task.needs_user ? `<span class="pill warning">${T("Needs you")}</span>` : ""}
        <span class="pill ${taskStateTone(task.state)}">${esc(UI.translate(task.state))}</span>
        ${removableStates.has(task.state) ? `<button type="button" class="btn small danger quiet" data-delete-task="${esc(task.id)}">${T("Delete")}</button>` : ""}
      </div>
    </div>`;
  }

  function listHtml(data) {
    const { project_id: project, groups, singles, needs_you: needs } = data;
    if (!groups.length && !singles.length) {
      return `<div class="empty" data-key="empty">${T("Nothing here yet. Tell ChatGPT what you want built, or describe it above.")}</div>`;
    }
    const ordered = [...groups].sort((a, b) => Number(Boolean(b.needs_user)) - Number(Boolean(a.needs_user)) || Number(a.finished) - Number(b.finished) || String(b.updated).localeCompare(String(a.updated)));
    return `${needs ? `<div class="work-needs" data-key="needs"><span class="pill warning">${needs}</span> ${T("waiting for you")}</div>` : ""}
      <div class="work-groups" data-key="groups">${ordered.map((g) => groupHtml(project, g)).join("")}</div>
      ${singles.length ? `<h2 class="work-subhead" data-key="singles-head">${T("Single tasks")}</h2>
        <div class="work-singles" data-key="singles">${[...singles].reverse().map((t) => singleHtml(project, t)).join("")}</div>` : ""}`;
  }

  function paintBadge(needs) {
    const badge = document.getElementById("nav-needs-badge");
    if (!badge) return;
    badge.hidden = !needs;
    badge.textContent = needs ? String(needs) : "";
  }

  // ---- entry: "Tasks" in the sidebar ---------------------------------------------------------------

  async function renderHome() {
    const gen = getGen();
    layout(loadingView());
    let projects;
    try { projects = await api("/projects"); } catch (err) { if (!stale(gen)) errorView(err); return; }
    if (stale(gen)) return;
    const ids = projects.map((p) => p.id);
    const remembered = rememberedProject();
    const target = ids.includes(remembered) ? remembered : ids.length === 1 ? ids[0] : "";
    if (target) { location.replace(`#/p/${encodeURIComponent(target)}/tasks`); return; }
    location.replace("#/");
  }

  // ---- the page -----------------------------------------------------------------------------------

  async function renderList() {
    const gen = getGen();
    const project = parseRoute().project;
    const base = `/projects/${encodeURIComponent(project)}`;
    layout(loadingView());
    let data, projects;
    try { [data, projects] = await Promise.all([api(`${base}/work`), api("/projects")]); }
    catch (err) { if (!stale(gen)) errorView(err); return; }
    if (stale(gen)) return;
    const picker = projects.length > 1
      ? `<select id="project-picker" aria-label="${esc(UI.translate("Project"))}">${projects.map((p) => `<option value="${esc(p.id)}"${p.id === project ? " selected" : ""} data-no-i18n>${esc(p.id)}</option>`).join("")}</select>`
      : `<span data-no-i18n>${esc(project)}</span>`;
    layout(`
      <div class="title-row"><div><h1>${T("Tasks")}</h1><div class="sub">${picker}</div></div></div>
      <section class="work-new">
        <label for="work-text">${T("What do you want done?")}</label>
        <textarea id="work-text" rows="3" maxlength="20000" placeholder="${esc(UI.translate("Describe it the way you would to a colleague. ChatGPT can start work too, and it shows up here."))}"></textarea>
        <div class="work-new-actions">
          <button class="btn primary" id="work-start" type="button">${T("Start")}</button>
          <span class="hint">${T("Starting only writes the request down. You approve the plan before anything changes your files.")}</span>
        </div>
        <div id="work-choice" data-morph-skip></div>
      </section>
      <div id="work-list" aria-live="polite">${listHtml(data)}</div>
    `);
    const list = document.getElementById("work-list");
    UI.localize(list);
    paintBadge(data.needs_you);

    let revision = data.revision;
    const paint = (fresh) => {
      data = fresh;
      revision = fresh.revision;
      window.cccMorph.morph(list, listHtml(fresh), UI.localize);
      paintBadge(fresh.needs_you);
    };
    const refresh = async () => {
      const fresh = await api(`${base}/work`);
      if (!stale(gen)) paint(fresh);
    };
    const poll = livePoll(gen, async () => {
      const fresh = await api(`${base}/work?rev=${encodeURIComponent(revision)}`);
      if (stale(gen) || fresh.unchanged) return;
      paint(fresh);
    });

    // One handler for the whole list: nodes come and go as the list updates, the handler stays.
    list.onclick = async (event) => {
      const toggle = event.target.closest("[data-toggle]");
      if (toggle) {
        const id = toggle.dataset.toggle;
        const section = toggle.closest(".work-group");
        const steps = section.querySelector(".work-steps");
        const open = steps.hidden;
        steps.hidden = !open;
        toggle.setAttribute("aria-expanded", String(open));
        toggle.firstElementChild.textContent = open ? "▾" : "▸";
        openChoice.set(id, open);
        saveChoices();
        return;
      }
      const rename = event.target.closest("[data-rename]");
      if (rename) {
        showModal({
          title: "Rename this work",
          body: "Only the name changes. The request and the plan stay as they were.",
          fields: [{ label: UI.translate("Name"), value: rename.dataset.title }],
          confirmLabel: "Rename",
          onSubmit: async ([title]) => {
            await api(`${base}/goals/${encodeURIComponent(rename.dataset.rename)}/rename`, { method: "POST", body: { title } });
            await refresh();
          },
        });
        return;
      }
      const remove = event.target.closest("[data-delete-task]");
      if (remove) confirmDeleteTask(project, remove.dataset.deleteTask, refresh);
    };

    const picked = document.getElementById("project-picker");
    if (picked) picked.onchange = () => navTo(`/p/${encodeURIComponent(picked.value)}/tasks`);

    // ---- a new request: the same call ChatGPT's start_goal makes ----------------------------------
    let requestKey = newKey();
    const choice = document.getElementById("work-choice");
    const startButton = document.getElementById("work-start");
    const open = (goal) => navTo(`/p/${encodeURIComponent(project)}/tasks/${encodeURIComponent(goal.id)}`);
    const start = async (confirmNew) => {
      const text = document.getElementById("work-text").value.trim();
      if (!text) { toast("Describe what you want first.", true); return; }
      startButton.disabled = true;
      try {
        const result = await api(`${base}/work/goals`, { method: "POST", body: { requirement: text, request_key: requestKey, confirm_new: confirmNew || undefined } });
        if (result.status === "needs_choice") { showChoice(result); return; }
        requestKey = newKey();
        document.getElementById("work-text").value = "";
        choice.innerHTML = "";
        open(result);
      } catch (err) { toast(err.message, true); }
      finally { startButton.disabled = false; }
    };
    const showChoice = (result) => {
      choice.innerHTML = `<div class="work-choice">
        <p><strong>${esc(UI.translate(result.reason === "similar_goal" ? "This looks like work you already have. Continue one of these?" : result.question))}</strong></p>
        ${result.choices.map((c) => c.goal_id
          ? `<button type="button" class="btn small" data-continue="${esc(c.goal_id)}" data-no-i18n>${esc(c.title)}</button>`
          : `<span class="pill neutral" data-no-i18n>${esc(c)}</span>`).join(" ")}
        ${result.reason === "similar_goal" ? `<button type="button" class="btn small primary" data-new-anyway>${T("No, this is separate work")}</button>` : ""}
      </div>`;
      UI.localize(choice);
    };
    startButton.onclick = () => start(false);
    choice.onclick = (event) => {
      const cont = event.target.closest("[data-continue]");
      if (cont) { toast("Describe the change on the next page and ask for a plan revision."); open({ id: cont.dataset.continue }); return; }
      if (event.target.closest("[data-new-anyway]")) start(true);
    };
    return poll;
  }

  return { renderHome, renderList };
};
