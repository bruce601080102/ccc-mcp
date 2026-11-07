(() => {
  "use strict";
  const UI = window.cccI18n;
  let appVersion = "";

  let TOKEN = "";
  function captureTokenFromHash() {
    const fragment = location.hash.slice(1);
    const match = fragment.match(/(?:^|[?&])token=([^&]+)/);
    if (!match) return;
    TOKEN = decodeURIComponent(match[1]);
    try { sessionStorage.setItem("ccc_ui_token", TOKEN); } catch (e) {}
    const route = fragment.startsWith("/") ? fragment.split("?")[0] : "";
    history.replaceState(null, "", location.pathname + location.search + (route ? `#${route}` : ""));
  }
  captureTokenFromHash();
  if (!TOKEN) {
    try { TOKEN = sessionStorage.getItem("ccc_ui_token") || ""; } catch (e) {}
  }

  async function api(path, opts) {
    opts = opts || {};
    const headers = { "X-CCC-UI-Token": TOKEN };
    let body = opts.body;
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }
    const res = await fetch("/api" + path, { method: opts.method || "GET", headers, body });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = { error: text }; }
    if (!res.ok) {
      const err = new Error((data && data.error) || ("Request failed: " + res.status));
      err.status = res.status;
      err.path = "/api" + path;
      throw err;
    }
    return data;
  }

  function esc(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // Timestamps read as "5 minutes ago" with the exact local time on hover, instead of raw ISO text.
  function whenHtml(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return esc(iso);
    const locale = UI.getLocale();
    const seconds = Math.round((date.getTime() - Date.now()) / 1000);
    const abs = Math.abs(seconds);
    const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
    let text;
    if (abs < 60) text = rtf.format(0, "second");
    else if (abs < 3600) text = rtf.format(Math.round(seconds / 60), "minute");
    else if (abs < 86400) text = rtf.format(Math.round(seconds / 3600), "hour");
    else if (abs < 7 * 86400) text = rtf.format(Math.round(seconds / 86400), "day");
    else text = date.toLocaleDateString(locale, { year: "numeric", month: "short", day: "numeric" });
    return `<time datetime="${esc(iso)}" title="${esc(date.toLocaleString(locale))}" data-no-i18n>${esc(text)}</time>`;
  }

  // A project's icon in lists: a pixel character or a short text/emoji in a color, else its first letter.
  const ICON_TONES = ["green", "blue", "violet", "rose", "orange", "amber", "teal", "slate"];
  function projectIconHtml(id, icon, size = "small") {
    const tone = icon && icon.tone ? ` tone-${icon.tone}` : "";
    if (icon && icon.avatar) return `<span class="project-avatar project-sprite ${size}${tone}" aria-hidden="true">${spriteFor(icon.avatar)}</span>`;
    const text = icon && icon.text ? icon.text : id.slice(0, 1).toUpperCase();
    return `<span class="project-avatar ${size}${tone}${icon && icon.text ? " is-text" : ""}" aria-hidden="true" data-no-i18n>${esc(text)}</span>`;
  }

  // A page-shaped placeholder: the layout does not jump when real content arrives.
  const loadingView = () => `<div class="loading-skeleton" role="status" aria-live="polite"><span class="sr-only">${esc(UI.translate("Loading…"))}</span><div class="sk sk-title"></div><div class="sk sk-line"></div><div class="sk sk-card"></div><div class="sk sk-card short"></div></div>`;

  function taskStateTone(state) {
    if (["FAILED", "BLOCKED", "REJECTED"].includes(state)) return "negative";
    if (state === "COMPLETED") return "positive";
    if (state === "CANCELLED") return "neutral";
    if (String(state).startsWith("AWAITING")) return "warning";
    return "accent";
  }

  let toastTimer = null;
  function toast(message, isError) {
    let el = document.getElementById("toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "toast";
      document.body.appendChild(el);
    }
    el.className = "toast" + (isError ? " error" : "");
    el.textContent = UI.translate(message);
    el.style.display = "block";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.style.display = "none"; }, 4200);
  }

  let modalReturnFocus = null;
  function mountModal(overlay) {
    modalReturnFocus = document.activeElement;
    document.body.appendChild(overlay);
    const dialog = overlay.querySelector('[role="dialog"]');
    if (dialog) {
      dialog.tabIndex = -1;
      dialog.focus();
    }
  }
  function closeModal() {
    const overlay = document.getElementById("modal-overlay");
    if (!overlay) return;
    overlay.remove();
    const returnTo = modalReturnFocus;
    modalReturnFocus = null;
    if (returnTo && returnTo.isConnected) returnTo.focus();
  }
  document.addEventListener("keydown", (event) => {
    const overlay = document.getElementById("modal-overlay");
    if (!overlay) return;
    if (event.key === "Escape") { closeModal(); return; }
    if (event.key !== "Tab") return;
    const focusable = [...overlay.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])')]
      .filter((element) => element.getClientRects().length);
    if (!focusable.length) { event.preventDefault(); return; }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === overlay.querySelector('[role="dialog"]') || !overlay.contains(document.activeElement))) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !overlay.contains(document.activeElement))) {
      event.preventDefault(); first.focus();
    }
  });

  function showModal({ title, body, fields, confirmLabel, cancelLabel, danger, onSubmit }) {
    closeModal();
    const overlay = document.createElement("div");
    overlay.id = "modal-overlay";
    overlay.className = "modal-overlay";
    const fieldsHtml = (fields || []).map((f, i) => f.type === "checkbox" ? `
      <div class="field">
        <div class="checkbox-row"><input type="checkbox" id="mf-${i}" ${f.checked ? "checked" : ""} /><label for="mf-${i}">${esc(f.label)}</label></div>
        ${f.hint ? `<div class="hint">${esc(f.hint)}</div>` : ""}
      </div>
    ` : `
      <div class="field">
        <label for="mf-${i}">${esc(f.label)}</label>
        <input type="text" id="mf-${i}" placeholder="${esc(f.placeholder || "")}" value="${esc(f.value || "")}" autocomplete="off" />
        ${f.hint ? `<div class="hint">${esc(f.hint)}</div>` : ""}
      </div>
    `).join("");
    overlay.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true">
        <h2>${esc(title)}</h2>
        <div class="body">${esc(body)}</div>
        <div class="fields">${fieldsHtml}</div>
        <div class="actions">
          <button class="btn" id="modal-cancel" type="button">${esc(cancelLabel || "Cancel")}</button>
          <button class="btn ${danger ? "danger" : "primary"}" id="modal-confirm" type="button">${esc(confirmLabel || "Confirm")}</button>
        </div>
      </div>
    `;
    mountModal(overlay);
    UI.localize(overlay);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) closeModal(); });
    document.getElementById("modal-cancel").addEventListener("click", closeModal);
    document.getElementById("modal-confirm").addEventListener("click", async () => {
      const confirm = document.getElementById("modal-confirm");
      if (confirm.disabled) return;
      const values = (fields || []).map((f, i) => f.type === "checkbox" ? document.getElementById("mf-" + i).checked : document.getElementById("mf-" + i).value);
      confirm.disabled = true;
      confirm.setAttribute("aria-busy", "true");
      try {
        await onSubmit(values);
        closeModal();
      } catch (err) {
        toast(err.message, true);
      } finally {
        confirm.disabled = false;
        confirm.removeAttribute("aria-busy");
      }
    });
    const first = overlay.querySelector("input");
    if (first) first.focus();
    return overlay;
  }

  function confirmTextModal({ title, description, challenge, confirmLabel, onConfirm, checkbox }) {
    const overlay = showModal({
      title,
      body: description,
      fields: [
        { label: `${UI.translate("Type")} ${challenge} ${UI.translate("to confirm")}`, placeholder: challenge },
        ...(checkbox ? [{ type: "checkbox", label: checkbox.label, hint: checkbox.hint }] : []),
      ],
      confirmLabel: confirmLabel || "Confirm",
      danger: true,
      onSubmit: async ([typed, checked]) => {
        if (typed !== challenge) throw new Error(UI.translate("Confirmation text did not match."));
        await onConfirm(typed, Boolean(checked));
      },
    });
    if (checkbox?.descriptionWhenChecked) overlay.querySelector("#mf-1").addEventListener("change", (event) => {
      overlay.querySelector(".body").textContent = event.target.checked ? checkbox.descriptionWhenChecked : description;
    });
  }

  const root = document.getElementById("root");
  let renderGen = 0;
  function stale(gen) { return gen !== renderGen; }

  function parseRoute() {
    const hash = location.hash.replace(/^#/, "") || "/";
    const [path, query] = hash.split("?", 2);
    const parts = path.split("/").filter(Boolean);
    if (parts[0] === "docs") return { view: "docs", sub: parts[1] || "README", anchor: new URLSearchParams(query || "").get("anchor") };
    if (parts[0] === "p" && parts[1]) {
      return { project: decodeURIComponent(parts[1]), view: parts[2] || "overview", sub: parts[3] ? decodeURIComponent(parts[3]) : undefined };
    }
    if (parts[0] === "connect") return { view: "connect" };
    if (parts[0] === "global-skills") return { view: "global-skills" };
    if (parts[0] === "dashboard") return { view: "dashboard" };
    return { view: "projects" };
  }

  function navTo(path) { location.hash = path; }

  const ICON = {
    projects: `<svg class="icon" viewBox="0 0 16 16"><path d="M1.5 4.5a1 1 0 0 1 1-1h3l1.2 1.5H13.5a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-7.5Z"/></svg>`,
    dashboard: `<svg class="icon" viewBox="0 0 16 16"><path d="M2 13.5h12M3.5 11V8M7.5 11V4M11.5 11V6"/><path d="M3.5 8h2v3h-2zM7.5 4h2v7h-2zM11.5 6h2v5h-2z"/></svg>`,
    overview: `<svg class="icon" viewBox="0 0 16 16"><path d="M2 8.5 8 3l6 5.5"/><path d="M3.5 7.5V13a.5.5 0 0 0 .5.5h3v-4h2v4h3a.5.5 0 0 0 .5-.5V7.5"/></svg>`,
    tasks: `<svg class="icon" viewBox="0 0 16 16"><path d="M3 4.5h10M3 8h10M3 11.5h6"/></svg>`,
    settings: `<svg class="icon" viewBox="0 0 16 16"><circle cx="8" cy="8" r="2.1"/><path d="M8 2v1.6M8 12.4V14M13.3 5.3l-1.3.8M4 9.9l-1.3.8M14 8h-1.6M3.6 8H2M13.3 10.7l-1.3-.8M4 6.1l-1.3-.8"/></svg>`,
    context: `<svg class="icon" viewBox="0 0 16 16"><path d="M4 1.8h5.5L12.3 4.5V14a.5.5 0 0 1-.5.5h-7.8a.5.5 0 0 1-.5-.5V2.3a.5.5 0 0 1 .5-.5Z"/><path d="M6 8h4M6 10.3h4"/></svg>`,
    skills: `<svg class="icon" viewBox="0 0 16 16"><path d="M5.7 2.3a1.3 1.3 0 0 1 2.6 0c0 .5-.3.8-.3 1.3 0 .4.3.7.7.7h1.6a.7.7 0 0 1 .7.7v1.6c0 .4.3.7.7.7.5 0 .8-.3 1.3-.3a1.3 1.3 0 0 1 0 2.6c-.5 0-.8-.3-1.3-.3-.4 0-.7.3-.7.7v1.6a.7.7 0 0 1-.7.7H8.7c-.4 0-.7-.3-.7-.7 0-.5.3-.8.3-1.3a1.3 1.3 0 0 0-2.6 0c0 .5.3.8.3 1.3 0 .4-.3.7-.7.7H3.7a.7.7 0 0 1-.7-.7v-1.6c0-.4-.3-.7-.7-.7-.5 0-.8.3-1.3.3a1.3 1.3 0 0 1 0-2.6c.5 0 .8.3 1.3.3.4 0 .7-.3.7-.7V5c0-.4.3-.7.7-.7h1.6c.4 0 .7-.3.7-.7 0-.5-.3-.8-.3-1.3Z"/></svg>`,
    docs: `<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.5c-2-1-4-1-6-.5v9.5c2-.5 4-.5 6 .5 2-1 4-1 6-.5V3c-2-.5-4-.5-6 .5Z"/><path d="M8 3.5V13"/></svg>`,
    connect: `<svg class="icon" viewBox="0 0 16 16"><path d="M6.3 9.7 3.9 12a2 2 0 1 1-2.8-2.8l2.3-2.4M9.7 6.3 12 3.9a2 2 0 1 1 2.8 2.8l-2.3 2.4M5.5 10.5l5-5"/></svg>`,
    up: `<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 13V3M4.5 6.5 8 3l3.5 3.5"/></svg>`,
    down: `<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10m-3.5-3.5L8 13l3.5-3.5"/></svg>`,
    back: `<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M13 8H3m3.5-3.5L3 8l3.5 3.5"/></svg>`,
    sun: `<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="3"/><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.5 3.5l1.4 1.4M11.1 11.1l1.4 1.4M3.5 12.5l1.4-1.4M11.1 4.9l1.4-1.4"/></svg>`,
    moon: `<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M13.5 9.5A5.8 5.8 0 0 1 6.5 2.5 5.8 5.8 0 1 0 13.5 9.5Z"/></svg>`,
  };

  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }
  function setTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("ccc_ui_theme", theme); } catch (e) {}
  }

  let lastConnectStatus = null;
  let activeTimers = [];
  function stopPolling() {
    activeTimers.forEach(clearInterval);
    activeTimers = [];
  }
  function trackTimer(id) {
    activeTimers.push(id);
    return id;
  }

  /** Quietly re-fetches `fetchFn` every `intervalMs` and, the first time the result
   * differs from what the page last showed, surfaces a small "new data" banner
   * instead of silently replacing content under the user (which could wipe an
   * open dropdown, an in-progress drag, or text they are mid-typing). Clicking
   * the banner runs `onReload`, normally the same render* function that built
   * the current page. This is the "auto-detect environment changes and offer to
   * reload" behavior — detection is automatic, applying the change is not. */
  function watchForChanges(gen, fetchFn, onReload, intervalMs = 8000) {
    let baseline = null;
    trackTimer(setInterval(async () => {
      if (stale(gen)) return;
      let fresh;
      try { fresh = await fetchFn(); } catch (e) { return; }
      const serialized = JSON.stringify(fresh);
      if (baseline === null) { baseline = serialized; return; }
      if (serialized !== baseline && !document.getElementById("reload-banner")) {
        showReloadBanner(onReload);
      }
    }, intervalMs));
  }

  function showReloadBanner(onReload) {
    if (document.getElementById("reload-banner")) return;
    const content = document.querySelector(".content");
    if (!content) return;
    const banner = document.createElement("div");
    banner.id = "reload-banner";
    banner.className = "reload-banner";
    banner.innerHTML = `<span><span class="status-dot pending"></span> ${esc(UI.translate("New data is available."))}</span><button class="btn small primary" id="reload-banner-btn">${esc(UI.translate("Refresh"))}</button>`;
    content.prepend(banner);
    document.getElementById("reload-banner-btn").addEventListener("click", onReload);
  }

  async function refreshConnectDot() {
    try { lastConnectStatus = await api("/connect/status"); }
    catch (e) { lastConnectStatus = null; }
    const dot = document.getElementById("nav-connect-dot");
    if (!dot || !lastConnectStatus) return;
    dot.className = "dot" + (lastConnectStatus.state === "connected" ? " on" : lastConnectStatus.state === "error" ? " err" : "");
  }

  function layout(contentHtml, opts) {
    const narrow = opts && opts.narrow;
    stopPolling();
    const currentRoute = parseRoute();
    const currentProjectId = currentRoute.project || null;
    const p = currentProjectId ? encodeURIComponent(currentProjectId) : "";
    const projectNav = currentProjectId ? `
      <div class="nav-group-label">${esc(currentProjectId)}</div>
      <a class="nav-item ${currentRoute.view === "overview" ? "active" : ""}" href="#/p/${p}/overview">${ICON.overview}Overview</a>
      <a class="nav-item ${currentRoute.view === "tasks" ? "active" : ""}" href="#/p/${p}/tasks">${ICON.tasks}Tasks</a>
      <a class="nav-item ${currentRoute.view === "settings" ? "active" : ""}" href="#/p/${p}/settings">${ICON.settings}Settings</a>
      <a class="nav-item ${currentRoute.view === "context" ? "active" : ""}" href="#/p/${p}/context">${ICON.context}Context</a>
      <a class="nav-item ${currentRoute.view === "skills" ? "active" : ""}" href="#/p/${p}/skills">${ICON.skills}Skills &amp; MCP</a>
    ` : "";
    const dotClass = "dot" + (lastConnectStatus && lastConnectStatus.state === "connected" ? " on" : lastConnectStatus && lastConnectStatus.state === "error" ? " err" : "");
    root.innerHTML = `
      <button type="button" class="skip-link" id="skip-link">Skip to content</button>
      <nav class="sidebar" aria-label="Main">
        <div class="brand"><span class="mark">C</span><span>ccc-mcp<small>CONTROL CENTER <span id="app-version" data-no-i18n>${appVersion ? `· v${esc(appVersion)}` : ""}</span></small></span></div>
        <a class="nav-item ${currentRoute.view === "projects" ? "active" : ""}" href="#/">${ICON.projects}All projects</a>
        <a class="nav-item ${currentRoute.view === "dashboard" ? "active" : ""}" href="#/dashboard">${ICON.dashboard}Command center</a>
        <a class="nav-item ${currentRoute.view === "global-skills" ? "active" : ""}" href="#/global-skills">${ICON.skills}Global Skills</a>
        ${projectNav}
        <div class="nav-group-label">&nbsp;</div>
        <a class="nav-item ${currentRoute.view === "connect" ? "active" : ""}" href="#/connect">${ICON.connect}Connect<span id="nav-connect-dot" class="${dotClass}"></span></a>
        <a class="nav-item ${currentRoute.view === "docs" ? "active" : ""}" href="#/docs">${ICON.docs}Documentation</a>
        <div class="sidebar-spacer"></div>
        <button type="button" id="theme-toggle" class="nav-item" aria-pressed="${currentTheme() === "dark"}" aria-label="Toggle dark mode">${currentTheme() === "dark" ? ICON.moon : ICON.sun}<span>Dark mode</span><span class="dot${currentTheme() === "dark" ? " on" : ""}"></span></button>
        <div class="sidebar-locale"><span>Interface language</span><select id="ui-locale" aria-label="Interface language"><option value="zh-TW">繁體中文</option><option value="en">English</option></select></div>
        <div class="sidebar-foot">LOCAL CONSOLE <span class="status-dot on"></span></div>
      </nav>
      <div class="frame"><main class="sheet" id="sheet" tabindex="-1"><div class="content${narrow ? " narrow" : ""}${opts && opts.contentClass ? " " + opts.contentClass : ""}">${contentHtml}</div></main></div>
    `;
    document.getElementById("skip-link").addEventListener("click", () => document.getElementById("sheet").focus());
    // On a phone the sidebar is a horizontal strip; keep the current page visible in it.
    const activeNav = root.querySelector(".sidebar .nav-item.active");
    if (activeNav && activeNav.scrollIntoView) activeNav.scrollIntoView({ block: "nearest", inline: "center" });
    const localeSelect = document.getElementById("ui-locale");
    localeSelect.value = UI.getLocale();
    localeSelect.addEventListener("change", () => {
      UI.setLocale(localeSelect.value);
      closeModal();
      route();
    });
    const themeToggle = document.getElementById("theme-toggle");
    themeToggle.addEventListener("click", () => {
      const next = currentTheme() === "dark" ? "light" : "dark";
      setTheme(next);
      themeToggle.setAttribute("aria-pressed", String(next === "dark"));
      themeToggle.innerHTML = `${next === "dark" ? ICON.moon : ICON.sun}<span>${UI.translate("Dark mode")}</span><span class="dot${next === "dark" ? " on" : ""}"></span>`;
    });
    UI.localize(root);
  }

  function errorView(err) {
    layout(`<div class="empty">${esc(err.message)}</div>`);
  }

  function accessPill(access) {
    if (access.mode === "read-write") return `<span class="pill positive">${UI.translate("read-write")}</span>`;
    return `<span class="pill neutral">${UI.translate("read-only")}</span>`;
  }

  const TERMINAL_STATES = new Set(["COMPLETED", "CANCELLED", "REJECTED"]);
  const REMOVABLE_STATES = new Set([...TERMINAL_STATES, "FAILED"]);
  function confirmDeleteTask(projectId, taskId, onDone) {
    showModal({
      title: "Delete this task?",
      body: `${taskId}\n${UI.translate("The task disappears from this console. Its history is kept for audit and the ID is not reused. Files already changed in the project are not touched.")}`,
      confirmLabel: "Delete task", danger: true,
      onSubmit: async () => {
        await api(`/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}/delete`, { method: "POST" });
        toast("Task deleted.");
        onDone();
      },
    });
  }
  const WAITING_LABELS = {
    DISPATCH_RETRY_NEEDED: "Dispatch needs retry",
    PM_CLARIFICATION_DECISION: "Clarification decision needed",
    PM_FLOW_DECISION: "Gate decision needed",
    CHATGPT_HANDOFF_PENDING: "ChatGPT handoff pending",
    PM_REFINEMENT: "Context review needed",
    USER_APPROVAL: "Approval needed",
    PM_DECISION: "Decision needed",
    BLOCKED: "Worker blocked",
    FAILED: "Execution failed",
    AGENT_RUNNING: "Agent running",
  };

  function dashboardTestPill(tests, finished) {
    if (!tests?.executed_by_host) return finished ? `<span class="dashboard-evidence muted">${esc(UI.translate("No host test evidence"))}</span>` : "";
    if (tests.no_tests_collected) return `<span class="pill warning">${esc(UI.translate("No tests collected"))}</span>`;
    return tests.passed
      ? `<span class="pill positive">${esc(UI.translate("Host tests passed"))}</span>`
      : `<span class="pill negative">${esc(UI.translate("Host tests failed"))}</span>`;
  }

  function dashboardTaskRow(task) {
    const brief = task.brief || {};
    const waiting = brief.waiting_on || task.state;
    const label = WAITING_LABELS[waiting] || task.state;
    const finished = TERMINAL_STATES.has(task.state);
    const tone = waiting === "AGENT_RUNNING" ? "accent"
      : waiting === "COMPLETED" ? "positive"
      : waiting === "CANCELLED" || waiting === "REJECTED" ? "neutral"
      : waiting === "FAILED" || waiting === "BLOCKED" ? "negative" : "warning";
    const detail = brief.error || (waiting === "AGENT_RUNNING"
      ? [brief.agent, brief.role].filter(Boolean).join(" · ")
      : finished ? brief.summary || "" : brief.next_action || "");
    const link = `#/p/${encodeURIComponent(task.project_id)}/tasks/${encodeURIComponent(task.task_id)}`;
    return `<a class="dashboard-task" href="${link}">
      <span class="dashboard-task-main">
        <span class="dashboard-task-meta"><strong data-no-i18n>${esc(task.project_id)}</strong><span class="mono">${esc(task.task_id)}</span></span>
        <span class="dashboard-task-title" data-no-i18n>${esc(task.requirement)}</span>
        ${detail ? `<span class="dashboard-task-detail" data-no-i18n>${esc(detail)}</span>` : ""}
      </span>
      <span class="dashboard-task-status"><span class="pill ${tone}">${esc(UI.translate(label))}</span>${dashboardTestPill(brief.tests, finished)}</span>
    </a>`;
  }

  function dashboardActivityChart(activity) {
    const points = activity || [];
    const highest = Math.max(1, ...points.map((day) => Math.max(day.created, day.completed)));
    const top = Math.max(3, Math.ceil(highest / 3) * 3);
    const base = 218;
    const chartHeight = 170;
    const step = 600 / Math.max(points.length, 1);
    const grid = [0, 1, 2, 3].map((index) => {
      const y = base - index * chartHeight / 3;
      return `<line x1="32" y1="${y}" x2="640" y2="${y}" class="dashboard-chart-grid" />
        <text x="24" y="${y + 4}" text-anchor="end" class="dashboard-chart-label">${Math.round(top * index / 3)}</text>`;
    }).join("");
    const bars = points.map((day, index) => {
      const x = 36 + index * step;
      const createdHeight = day.created ? Math.max(4, day.created / top * chartHeight) : 0;
      const completedHeight = day.completed ? Math.max(4, day.completed / top * chartHeight) : 0;
      const date = new Date(`${day.date}T00:00:00Z`).toLocaleDateString(UI.getLocale(), { month: "numeric", day: "numeric", timeZone: "UTC" });
      return `<rect x="${x}" y="${base - createdHeight}" width="11" height="${createdHeight}" rx="2" class="dashboard-bar-created"><title>${esc(`${date}: ${day.created} ${UI.translate("Started")}`)}</title></rect>
        <rect x="${x + 14}" y="${base - completedHeight}" width="11" height="${completedHeight}" rx="2" class="dashboard-bar-completed"><title>${esc(`${date}: ${day.completed} ${UI.translate("Completed")}`)}</title></rect>
        ${index % 3 === 0 ? `<text x="${x + 13}" y="244" text-anchor="middle" class="dashboard-chart-label">${esc(date)}</text>` : ""}`;
    }).join("");
    const empty = points.every((day) => !day.created && !day.completed)
      ? `<text x="335" y="126" text-anchor="middle" class="dashboard-chart-empty-label">${esc(UI.translate("No task activity in this period."))}</text>` : "";
    return `<svg class="dashboard-activity-chart" viewBox="0 0 650 260" role="img" aria-label="${esc(UI.translate("Tasks started and completed over the last 14 days"))}">${grid}${bars}${empty}</svg>`;
  }

  function dashboardStatusChart(attention, running) {
    const blocked = attention.filter((task) => ["BLOCKED", "FAILED"].includes(task.brief.waiting_on)).length;
    const waiting = attention.length - blocked;
    const total = attention.length + running.length;
    const blockedAngle = total ? blocked / total * 360 : 0;
    const waitingAngle = total ? (blocked + waiting) / total * 360 : 0;
    return `<div class="dashboard-status-chart">
      <div class="dashboard-donut${total ? "" : " empty"}" style="--blocked-angle:${blockedAngle}deg;--waiting-angle:${waitingAngle}deg" role="img" aria-label="${esc(`${total} ${UI.translate("Open tasks")}: ${blocked} ${UI.translate("Blocked or failed")}, ${waiting} ${UI.translate("Waiting for action")}, ${running.length} ${UI.translate("Agents running")}`)}">
        <div class="dashboard-donut-hole"><strong class="num">${total}</strong><span>${esc(UI.translate("Open tasks"))}</span></div>
      </div>
      <div class="dashboard-status-legend">
        <div><i class="blocked"></i><span>${esc(UI.translate("Blocked or failed"))}</span><strong class="num">${blocked}</strong></div>
        <div><i class="waiting"></i><span>${esc(UI.translate("Waiting for action"))}</span><strong class="num">${waiting}</strong></div>
        <div><i class="running"></i><span>${esc(UI.translate("Agents running"))}</span><strong class="num">${running.length}</strong></div>
      </div>
    </div>`;
  }

  function dashboardProjectLoad(projects, openTasks) {
    const rows = projects.map((project) => ({
      id: project.id,
      count: openTasks.filter((task) => task.project_id === project.id).length,
    })).sort((a, b) => b.count - a.count || a.id.localeCompare(b.id)).slice(0, 6);
    const max = Math.max(1, ...rows.map((row) => row.count));
    return rows.length ? `<div class="dashboard-project-load">${rows.map((row) => `
      <a href="#/p/${encodeURIComponent(row.id)}/tasks" class="dashboard-load-row">
        <span class="dashboard-load-name" data-no-i18n>${esc(row.id)}</span><span class="dashboard-load-track"><span style="width:${row.count / max * 100}%"></span></span><strong class="num">${row.count}</strong>
      </a>`).join("")}</div>` : `<div class="dashboard-empty">${esc(UI.translate("No projects yet. Add an existing folder or create a new one."))}</div>`;
  }

  const CLAUDE_PROBE_REASONS = {
    claude_unavailable: "Claude Code CLI was not found on this computer.",
    not_signed_in: "Claude Code is not signed in. Sign in once with a Claude subscription, then check again.",
    no_rate_limit_data: "Claude answered but reported no plan limits. Limits exist for subscription sign-ins, not API keys.",
    timeout: "Claude Code did not respond in time. Click Check now to retry.",
    session_ended: "Claude Code closed unexpectedly. Click Check now to retry.",
    busy: "A check is already running.",
    unsupported: "Automatic checks are not supported on this system yet; open Claude Code in a terminal once instead.",
    sync_disabled: "Claude quota sync is off.",
  };
  const USAGE_WINDOWS = ["5m", "15m", "1h", "1d", "1w", "1mo"];
  let dashboardUsageWindow = (() => {
    try { return USAGE_WINDOWS.includes(localStorage.getItem("ccc_usage_window")) ? localStorage.getItem("ccc_usage_window") : "1d"; }
    catch (e) { return "1d"; }
  })();
  let accountUsageCache = null, accountUsageAt = 0, accountUsagePending = null;
  const CLAUDE_AUTO_CHOICES = [0, 15, 30, 60];
  let claudeAutoAttemptAt = 0;
  function claudeAutoMinutes() {
    try {
      const value = Number(localStorage.getItem("ccc_claude_auto_check"));
      return CLAUDE_AUTO_CHOICES.includes(value) ? value : 0;
    } catch (e) { return 0; }
  }

  function codexReason(data) {
    if (data?.reason === "codex_unavailable") return "Codex CLI was not found on this computer.";
    if (data?.available === false) return "Couldn't read Codex limits just now. Click Check now to retry.";
    return "Codex returned no plan limits. Limits only exist for ChatGPT-plan sign-ins, not API keys.";
  }

  function dashboardAccountUsage(data, providers, claudeUsage) {
    const number = new Intl.NumberFormat(UI.getLocale());
    const codex = providers?.codex || {};
    const claude = providers?.claude || {};
    const status = (item) => !item.installed ? "CLI not installed" : item.logged_in ? "Signed in" : "Sign in required";
    const renderWindows = (rows) => (rows || []).map((row) => {
      const remaining = Math.max(0, 100 - row.used_percent);
      const reset = row.resets_at ? new Date(row.resets_at * 1000).toLocaleString(UI.getLocale()) : "—";
      return `<div class="quota-window"><div><strong>${esc(UI.translate(row.name))}</strong><span>${row.duration_mins ? esc(`${row.duration_mins} ${UI.translate("minutes")}`) : ""}</span><b class="num">${number.format(remaining)}% ${esc(UI.translate("remaining"))}</b></div><div class="quota-track" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${remaining}" aria-label="${esc(UI.translate(row.name))} ${esc(UI.translate("remaining"))}"><span style="width:${remaining}%"></span></div><small>${esc(UI.translate("Resets"))}: ${esc(reset)}</small></div>`;
    }).join("");
    const windows = renderWindows(data?.rate_limits);
    const claudeWindows = renderWindows(claudeUsage?.windows);
    const claudeUpdated = claudeUsage?.captured_at ? new Date(claudeUsage.captured_at * 1000).toLocaleString(UI.getLocale()) : "";
    const claudeState = claudeUsage?.backend_outdated
      ? `<div class="quota-unavailable"><strong>${esc(UI.translate("Console restart required"))}</strong><span>${esc(UI.translate("This page has newer Claude controls than the running ccc-mcp server. Restart ccc-mcp ui and open its new URL."))}</span></div>`
      : claudeUsage?.enabled
      ? `${claudeWindows || `<div class="quota-unavailable"><strong>${esc(UI.translate("Waiting for Claude usage"))}</strong><span>${esc(UI.translate("Click Check now. ccc-mcp quietly opens a short Claude Code session in the background (about 10 seconds, one tiny Haiku message) and reads your plan limits. No terminal needed."))}</span></div>`}
         ${claudeUpdated ? `<p class="quota-source ${claudeUsage.stale ? "stale" : ""}">${esc(UI.translate(claudeUsage.stale ? "Last observed · may be stale" : "Observed from Claude Code"))}: ${esc(claudeUpdated)}</p>` : ""}
         <label class="quota-auto"><span>${esc(UI.translate("Auto-check while this page is open"))}</span><select data-claude-auto-check>${CLAUDE_AUTO_CHOICES.map((m) => `<option value="${m}" ${claudeAutoMinutes() === m ? "selected" : ""}>${esc(m ? UI.translate("Every {n} min").replace("{n}", m) : UI.translate("Off"))}</option>`).join("")}</select></label>
         <p class="quota-source">${esc(UI.translate("Each check uses one tiny Haiku message from your Claude plan."))}</p>
         <div class="quota-actions"><button class="btn small ghost" data-claude-usage-action="refresh">${esc(UI.translate("Check now"))}</button><button class="btn small ghost" data-claude-usage-action="disable">${esc(UI.translate("Disable Claude quota sync"))}</button></div>`
      : `<div class="quota-unavailable"><strong>${esc(UI.translate("Claude quota sync is off"))}</strong><span>${esc(UI.translate("Read official 5-hour and 7-day plan limits from Claude Code's status line. No credentials or prompts are collected."))}</span></div>
         <button class="btn small quota-action" data-claude-usage-action="enable">${esc(UI.translate("Enable Claude quota sync"))}</button>`;
    const total = data?.tokens_available ? data.summary?.lifetimeTokens : null;
    const days = dashboardUsageWindow === "1mo" ? 30 : dashboardUsageWindow === "1w" ? 7 : dashboardUsageWindow === "1d" ? 1 : 0;
    const daily = days ? (data?.daily || []).slice(-days) : [];
    const max = Math.max(1, ...daily.map((row) => row.tokens));
    const chart = daily.length ? `<div class="dashboard-account-chart" role="img" aria-label="${esc(UI.translate("Daily account token activity"))}">${daily.map((row) => `<div class="dashboard-account-day" title="${esc(row.date)} · ${number.format(row.tokens)}"><span style="height:${Math.max(3, row.tokens / max * 100)}%"></span></div>`).join("")}</div>` : `<p class="dashboard-account-no-chart">${esc(UI.translate(days ? "Daily account history is unavailable." : "Account history has daily resolution; choose one day or longer."))}</p>`;
    return `<div class="quota-grid">
      <div class="quota-provider"><div class="quota-provider-head"><strong>Codex</strong><span>${esc(UI.translate(status(codex)))}</span></div><p>${esc(UI.translate("ChatGPT account plan window · all Codex activity"))}</p>${windows || `<div class="quota-unavailable"><strong>${esc(UI.translate(codexReason(data)))}</strong></div><div class="quota-actions"><button class="btn small ghost" data-claude-usage-action="refresh-codex">${esc(UI.translate("Check now"))}</button></div>`}</div>
      <div class="quota-provider"><div class="quota-provider-head"><strong>Claude Code</strong><span>${esc(UI.translate(status(claude)))}</span></div><p>${esc(UI.translate("Claude plan allowance"))}</p>${claudeState}</div>
    </div><details class="account-history"><summary>${esc(UI.translate("Account-wide token history"))} · ${total === null ? "—" : number.format(total)} ${esc(UI.translate("lifetime tokens"))}</summary><p>${esc(UI.translate("This is the signed-in ChatGPT account across apps and all time. It cannot be attributed to this workspace, and is not a remaining allowance."))}</p>${chart}</details>`;
  }

  function loadDashboardAccountUsage(gen, force=false) {
    const target = document.getElementById("dashboard-account-usage");
    if (!target) return;
    if (force || !accountUsageCache || Date.now() - accountUsageAt > 300000) {
      if (!accountUsagePending) accountUsagePending = Promise.all([
        api("/dashboard/account-usage").catch(() => ({ available: false })),
        api("/connect/providers").catch(() => ({ providers: {} })),
        api("/dashboard/claude-usage").catch((err) => ({
          enabled: false, windows: [], backend_outdated: err.status === 404 || err.status === 405,
        })),
      ]).then(([data, providerData, claudeUsage]) => {
        accountUsageCache = { data, providers: providerData.providers || {}, claudeUsage }; accountUsageAt = Date.now(); accountUsagePending = null; return accountUsageCache;
      }).catch((err) => { accountUsagePending = null; throw err; });
    }
    const pending = accountUsagePending || Promise.resolve(accountUsageCache);
    pending.then((value) => {
      if (stale(gen) || !target.isConnected) return;
      target.innerHTML = dashboardAccountUsage(value.data, value.providers, value.claudeUsage);
      target.querySelector("[data-claude-auto-check]")?.addEventListener("change", (event) => {
        try { localStorage.setItem("ccc_claude_auto_check", event.target.value); } catch (e) {}
        claudeAutoAttemptAt = Date.now();
      });
      target.querySelectorAll("[data-claude-usage-action]").forEach((button) => button.addEventListener("click", async (event) => {
        const button = event.currentTarget;
        const action = button.dataset.claudeUsageAction;
        const label = button.textContent;
        button.disabled = true;
        try {
          if (action === "refresh" || action === "enable" || action === "disable") {
            if (action === "enable" || action === "disable")
              await api(`/dashboard/claude-usage/${action}`, { method: "POST" });
            if (action !== "disable") {
              button.textContent = UI.translate("Checking Claude…");
              const outcome = (await api("/dashboard/claude-usage/probe", { method: "POST" })).probe;
              if (outcome && !outcome.ok) toast(UI.translate(CLAUDE_PROBE_REASONS[outcome.reason] || CLAUDE_PROBE_REASONS.timeout), true);
            }
          }
          accountUsageCache = null;
          loadDashboardAccountUsage(gen, true);
        } catch (err) {
          toast(UI.translate(err.status === 404 || err.status === 405
            ? "This console is running an older backend. Restart ccc-mcp ui and open the new URL."
            : err.message), true);
          button.disabled = false;
          button.textContent = label;
        }
      }));
    }).catch((err) => toast(err.message, true));
  }

  function dashboardUsagePanel(usage) {
    const data = usage || {};
    const providers = data.providers || [];
    const number = new Intl.NumberFormat(UI.getLocale());
    const field = (provider, name) => {
      const observed = provider.fields?.[name];
      return observed?.executions ? number.format(observed.total) : "—";
    };
    const card = (name, label, extras) => {
      const provider = providers.find((item) => item.provider === name) || { executions: 0, fields: {} };
      return `<div class="dashboard-usage-provider">
        <div class="dashboard-usage-provider-head"><strong>${esc(label)}</strong><span>${number.format(provider.executions)} ${esc(UI.translate("measured runs"))}</span></div>
        <div class="dashboard-token-pair">
          <div><strong class="num">${field(provider, "input_tokens")}</strong><span>${esc(UI.translate("Input tokens"))}</span></div>
          <div><strong class="num">${field(provider, "output_tokens")}</strong><span>${esc(UI.translate("Output tokens"))}</span></div>
        </div>
        <div class="dashboard-token-extras">${extras.map(([key, title]) => `<span>${esc(UI.translate(title))} <strong class="num">${field(provider, key)}</strong></span>`).join("")}</div>
      </div>`;
    };
    return `<p class="usage-scope">${esc(UI.translate("Only completed ccc-mcp CLI executions in the selected period. This excludes Codex or Claude work outside this workspace."))}</p><div class="dashboard-usage-grid">
      ${card("codex", "Codex CLI", [["cached_input_tokens", "Cached input"], ["reasoning_output_tokens", "Reasoning output"]])}
      ${card("claude", "Claude Code CLI", [["cache_read_input_tokens", "Cache read"], ["cache_creation_input_tokens", "Cache creation"]])}
    </div>
    <div class="dashboard-usage-coverage"><span>${number.format(data.unmeasured_native_executions || 0)} ${esc(UI.translate("native runs without token data"))}</span><span>${number.format(data.chatgpt_executions || 0)} ${esc(UI.translate("ChatGPT runs without per-run token data"))}</span></div>
    <p class="dashboard-usage-note">${esc(UI.translate("Cache fields are shown separately and may already be included in the provider's input total. Do not add them to the account history or each other."))}</p>`;
  }

  function dashboardReliability(rows) {
    if (!rows?.length) return `<div class="dashboard-empty">${esc(UI.translate("No evaluated executions yet. Enable evaluation in project settings, then evaluate a task."))}</div>`;
    return `<div class="reliability-list">${rows.slice(0, 8).map((row) => `<div class="reliability-row"><div><strong>${esc(row.agent)} · ${esc(row.role)}</strong><small>${esc(row.model || "")} · ${esc(row.task_type)}</small></div><strong class="num">${Math.round(row.reliability_score * 100)}%</strong><span>${esc(row.sample_count)} ${esc(UI.translate("evaluated outcomes"))}${row.low_sample ? ` · ${esc(UI.translate("small sample"))}` : ""}</span></div>`).join("")}</div><p class="dashboard-usage-note">${esc(UI.translate("Rule-based outcome history, not an independent benchmark or calibrated confidence score."))}</p>`;
  }

  async function loadDashboardReliability(gen) {
    const target = document.getElementById("dashboard-reliability");
    if (!target) return;
    try {
      const rows = await api("/dashboard/reliability");
      if (!stale(gen) && target.isConnected) target.innerHTML = dashboardReliability(rows);
    } catch (err) {
      if (!stale(gen) && target.isConnected) target.textContent = err.message;
    }
  }

  function renderDashboard() { return renderWorkspace(true); }
  function renderProjects() { return renderWorkspace(false); }

  async function renderWorkspace(isDashboard) {
    const gen = renderGen;
    layout(loadingView());
    let projects = [], organization = { groups: [], assignments: {} }, snapshot = { tasks: [], open_count: 0 };
    try {
      if (isDashboard) [projects, snapshot] = await Promise.all([api("/projects"), api(`/dashboard?usage_window=${dashboardUsageWindow}`)]);
      else [projects, organization] = await Promise.all([api("/projects"), api("/project-groups")]);
    } catch (err) {
      if (!stale(gen)) errorView(err);
      return;
    }
    if (stale(gen)) return;
    const writable = projects.filter((p) => p.access.mode === "read-write").length;
    const attention = snapshot.tasks.filter((t) => !TERMINAL_STATES.has(t.state) && t.brief.waiting_on !== "AGENT_RUNNING");
    const running = snapshot.tasks.filter((t) => t.brief.waiting_on === "AGENT_RUNNING");
    const recent = snapshot.tasks.filter((t) => TERMINAL_STATES.has(t.state)).slice(0, 8);
    const openTasks = [...attention, ...running];
    const queue = [...attention].sort((a, b) => Number(["BLOCKED", "FAILED"].includes(b.brief.waiting_on)) - Number(["BLOCKED", "FAILED"].includes(a.brief.waiting_on))).concat(running);
    const activity = snapshot.activity || [];
    const completedWeek = activity.slice(-7).reduce((total, day) => total + day.completed, 0);
    const startedWeek = activity.slice(-7).reduce((total, day) => total + day.created, 0);
    const groups = organization.groups || [];
    const assignments = organization.assignments || {};
    const icons = organization.icons || {};
    const groupName = (group) => group[UI.getLocale() === "zh-TW" ? "name_zh" : "name_en"] || group.name;
    const groupFields = (group) => {
      const legacyName = group && group.name_zh === undefined && group.name_en === undefined ? group.name : "";
      return [
        { label: "Chinese name", value: group?.name_zh || (/\p{Script=Han}/u.test(legacyName) ? legacyName : ""), hint: "Shown when the interface is in Traditional Chinese." },
        { label: "English name", value: group?.name_en || (legacyName && !/\p{Script=Han}/u.test(legacyName) ? legacyName : ""), hint: "Shown when the interface is in English." },
      ];
    };
    const saveGroupNames = ([name_zh, name_en]) => {
      if (!name_zh.trim() && !name_en.trim()) throw new Error(UI.translate("Enter a Chinese or English group name"));
      return { name_zh, name_en };
    };
    const groupOptions = (selected) => `<option value="" ${!selected ? "selected" : ""}>${esc(UI.translate("Ungrouped"))}</option>` + groups.map((g) => `<option data-no-i18n value="${esc(g.id)}" ${selected === g.id ? "selected" : ""}>${esc(groupName(g))}</option>`).join("");
    const row = (p) => `
      <div class="project-row" draggable="true" data-drag-project="${esc(p.id)}">
        <button type="button" class="project-icon-button" data-project-icon="${esc(p.id)}" aria-label="${esc(UI.translate("Change project icon"))}: ${esc(p.id)}" title="${esc(UI.translate("Change project icon"))}">${projectIconHtml(p.id, icons[p.id])}</button>
        <a class="project-row-link" href="#/p/${encodeURIComponent(p.id)}/overview">
          <span class="project-row-text">
            <span class="project-row-name" data-no-i18n>${esc(p.id)}</span>
            <span class="project-row-path mono">${esc(p.root)}</span>
          </span>
        </a>
        <span class="project-row-meta">
          ${accessPill(p.access)}
          <label class="project-move-label" for="move-${esc(p.id)}">${esc(UI.translate("Group"))}</label>
          <select id="move-${esc(p.id)}" class="project-move" data-project-id="${esc(p.id)}" aria-label="${esc(UI.translate("Move project to group"))}">${groupOptions(assignments[p.id])}</select>
        </span>
      </div>`;
    const section = (group, index) => {
      const groupId = group ? group.id : "";
      const members = projects.filter((p) => (assignments[p.id] || "") === groupId);
      const title = group ? `<span data-no-i18n>${esc(groupName(group))}</span>` : esc(UI.translate("Ungrouped"));
      const actions = group ? `<div class="group-actions">${index > 0 ? `<button class="btn small ghost icon-button" data-group-up="${esc(group.id)}" aria-label="${esc(UI.translate("Move group up"))}" title="${esc(UI.translate("Move group up"))}">${ICON.up}</button>` : ""}${index < groups.length - 1 ? `<button class="btn small ghost icon-button" data-group-down="${esc(group.id)}" aria-label="${esc(UI.translate("Move group down"))}" title="${esc(UI.translate("Move group down"))}">${ICON.down}</button>` : ""}<button class="btn small" data-group-rename="${esc(group.id)}">${esc(UI.translate("Rename"))}</button><button class="btn small danger" data-group-delete="${esc(group.id)}">${esc(UI.translate("Delete group"))}</button></div>` : "";
      return pageCard(group ? "folder" : "inbox", title, `${members.length} ${esc(UI.translate("registered projects"))}`,
        `<div class="project-list" data-drop-group="${esc(groupId)}">${members.length ? members.map(row).join("") : `<div class="group-empty">${esc(UI.translate("Drag projects here, or use Move to on a card."))}</div>`}</div>`,
        "", { actions, flush: true });
    };
    const dashboardHtml = isDashboard ? `
      <header class="dashboard-command">
        <div class="dashboard-command-copy">
          <div class="dashboard-eyebrow"><span class="status-dot on"></span>${esc(UI.translate("LOCAL WORKSPACE"))}<span class="dashboard-eyebrow-separator">/</span>${projects.length} ${esc(UI.translate("Projects"))}</div>
          <h1>${esc(UI.translate("Command center"))}</h1>
          <p>${esc(UI.translate("A live view of delivery, blockers, and verified outcomes across your projects."))}</p>
        </div>
        <div class="dashboard-command-signal"><span>${esc(UI.translate("REQUIRES ACTION"))}</span><strong class="num">${attention.length}</strong><small>${esc(UI.translate("tasks waiting on a decision or recovery"))}</small></div>
      </header>
      <div class="dashboard-metrics" aria-label="${esc(UI.translate("Workflow summary"))}">
        <div class="dashboard-metric critical"><span>${esc(UI.translate("Needs attention"))}</span><strong class="num">${attention.length}</strong><small>${esc(UI.translate("Decisions and blockers"))}</small></div>
        <div class="dashboard-metric live"><span>${esc(UI.translate("Agents running"))}</span><strong class="num">${running.length}</strong><small>${esc(UI.translate("Active executions"))}</small></div>
        <div class="dashboard-metric"><span>${esc(UI.translate("Completed in 7 days"))}</span><strong class="num">${completedWeek}</strong><small>${startedWeek} ${esc(UI.translate("started in the same period"))}</small></div>
        <div class="dashboard-metric"><span>${esc(UI.translate("Open tasks"))}</span><strong class="num">${snapshot.open_count}</strong><small>${projects.length} ${esc(UI.translate("Projects"))} · ${writable} ${esc(UI.translate("read-write"))}</small></div>
      </div>
      <div class="dashboard-grid dashboard-operations">
        <section class="dashboard-panel dashboard-panel-wide">
          <div class="dashboard-panel-head"><div><span class="dashboard-panel-kicker">01 / ${esc(UI.translate("OPERATIONS"))}</span><h2>${esc(UI.translate("Action queue"))}</h2><p>${esc(UI.translate("Blocked work first, then decisions and active agents."))}</p></div><strong class="dashboard-panel-count num">${queue.length}</strong></div>
          ${queue.length ? `<div class="dashboard-task-list dashboard-queue">${queue.map(dashboardTaskRow).join("")}</div>` : `<div class="dashboard-empty">${esc(UI.translate("Nothing needs attention right now."))}</div>`}
        </section>
        <section class="dashboard-panel">
          <div class="dashboard-panel-head"><div><span class="dashboard-panel-kicker">02 / ${esc(UI.translate("PROJECT LOAD"))}</span><h2>${esc(UI.translate("Work by project"))}</h2><p>${esc(UI.translate("Open tasks in the six busiest projects."))}</p></div></div>
          ${dashboardProjectLoad(projects, openTasks)}
        </section>
      </div>
      <div class="dashboard-grid">
        <section class="dashboard-panel dashboard-panel-wide">
          <div class="dashboard-panel-head"><div><span class="dashboard-panel-kicker">03 / ${esc(UI.translate("THROUGHPUT"))}</span><h2>${esc(UI.translate("14-day activity"))}</h2><p>${esc(UI.translate("Tasks started and completed each day (UTC)."))}</p></div><div class="dashboard-chart-key"><span><i class="created"></i>${esc(UI.translate("Started"))}</span><span><i class="completed"></i>${esc(UI.translate("Completed"))}</span></div></div>
          ${dashboardActivityChart(activity)}
        </section>
        <section class="dashboard-panel">
          <div class="dashboard-panel-head"><div><span class="dashboard-panel-kicker">04 / ${esc(UI.translate("CURRENT STATE"))}</span><h2>${esc(UI.translate("Open task mix"))}</h2><p>${esc(UI.translate("Where active work is waiting now."))}</p></div></div>
          ${dashboardStatusChart(attention, running)}
        </section>
      </div>
      <section class="dashboard-panel dashboard-usage">
        <div class="dashboard-panel-head"><div><h2>${esc(UI.translate("Usage and remaining allowance"))}</h2><p>${esc(UI.translate("Plan windows come from the account; the time filter applies to workspace CLI records and available daily history."))}</p></div><div class="usage-window-picker" role="group" aria-label="${esc(UI.translate("Workspace CLI period"))}">${USAGE_WINDOWS.map((window) => `<button class="usage-window-button ${dashboardUsageWindow === window ? "active" : ""}" data-usage-window="${window}" aria-pressed="${dashboardUsageWindow === window}">${esc(UI.translate(window))}</button>`).join("")}</div></div>
        <p class="quota-refresh-note">${esc(UI.translate("This page re-reads Codex every five minutes. Claude limits refresh when you click Check now."))}</p>
        <div id="dashboard-account-usage" class="dashboard-account-usage">${esc(UI.translate("Loading account usage…"))}</div>
        <div class="dashboard-usage-subhead">${esc(UI.translate("This workspace · CLI runs"))} · ${esc(UI.translate(dashboardUsageWindow))}</div>
        <div id="dashboard-cli-usage">${dashboardUsagePanel(snapshot.usage)}</div>
      </section>
      <section class="dashboard-panel dashboard-outcomes">
        <div class="dashboard-panel-head"><div><h2>${esc(UI.translate("Evidence and agent reliability"))}</h2><p>${esc(UI.translate("Verified task outcomes grouped by agent and role. Open a task to inspect the evidence behind a score."))}</p></div></div>
        <div id="dashboard-reliability">${esc(UI.translate("Loading…"))}</div>
      </section>
      <section class="dashboard-panel dashboard-outcomes">
        <div class="dashboard-panel-head"><div><span class="dashboard-panel-kicker">05 / ${esc(UI.translate("VERIFICATION"))}</span><h2>${esc(UI.translate("Recent outcomes"))}</h2><p>${esc(UI.translate("Finished work and host test evidence, shown separately."))}</p></div></div>
        ${recent.length ? `<div class="dashboard-task-list">${recent.map(dashboardTaskRow).join("")}</div>` : `<div class="dashboard-empty">${esc(UI.translate("No finished tasks yet."))}</div>`}
      </section>
    ` : "";
    const projectsHtml = isDashboard ? "" : `
      <div class="title-row">
        <div><h1>${esc(UI.translate("Projects"))}</h1><div class="sub">${esc(UI.translate("Every project registered with this ccc-mcp home."))}</div></div>
        <div class="title-actions"><button class="btn" id="btn-group">${esc(UI.translate("New group"))}</button><button class="btn" id="btn-import">${esc(UI.translate("Import backup…"))}</button><button class="btn" id="btn-add">${ICON.projects}${esc(UI.translate("Add existing folder"))}</button><button class="btn primary" id="btn-create">${esc(UI.translate("Create new project"))}</button></div>
      </div>
      ${projects.length ? `<div class="kpi-strip">
        <div class="kpi"><div class="v num">${projects.length}</div><div class="l">${esc(UI.translate("Projects"))}</div></div>
        <div class="kpi"><div class="v num">${writable}</div><div class="l">${esc(UI.translate("Read-write"))}</div></div>
        <div class="kpi"><div class="v num">${projects.length - writable}</div><div class="l">${esc(UI.translate("Read-only"))}</div></div>
      </div>` : ""}
      ${projects.length || groups.length ? `<div class="settings-stack">${groups.map(section).join("") + section(null, groups.length)}</div>` : `<div class="empty">No projects yet. Add an existing folder or create a new one.</div>`}
    `;
    layout(isDashboard ? dashboardHtml : projectsHtml);
    if (isDashboard) {
      loadDashboardAccountUsage(gen);
      loadDashboardReliability(gen);
      document.querySelectorAll("[data-usage-window]").forEach((button) => button.addEventListener("click", () => {
        dashboardUsageWindow = button.dataset.usageWindow;
        try { localStorage.setItem("ccc_usage_window", dashboardUsageWindow); } catch (e) {}
        renderDashboard();
      }));
      trackTimer(setInterval(async () => {
        if (stale(gen)) return;
        const minutes = claudeAutoMinutes();
        const claude = accountUsageCache?.claudeUsage;
        const observed = (claude?.captured_at || 0) * 1000;
        const due = Math.max(observed, claudeAutoAttemptAt) + minutes * 60000;
        if (minutes && claude?.enabled && Date.now() >= due) {
          claudeAutoAttemptAt = Date.now();
          try { await api("/dashboard/claude-usage/probe", { method: "POST" }); } catch (e) {}
          accountUsageCache = null;
        }
        loadDashboardAccountUsage(gen, true);
        loadDashboardReliability(gen);
        try {
          const fresh = await api(`/dashboard?usage_window=${dashboardUsageWindow}`);
          const target = document.getElementById("dashboard-cli-usage");
          if (!stale(gen) && target) target.innerHTML = dashboardUsagePanel(fresh.usage);
        } catch (e) {}
      }, 300000));
      watchForChanges(gen, () => Promise.all([api("/projects"), api(`/dashboard?usage_window=${dashboardUsageWindow}`)]), renderDashboard);
      return;
    }
    document.getElementById("btn-add").addEventListener("click", openAddProjectModal);
    document.getElementById("btn-create").addEventListener("click", openCreateProjectModal);
    document.getElementById("btn-import").addEventListener("click", openImportProjectModal);
    document.getElementById("btn-group").addEventListener("click", () => showModal({
      title: "New group", body: "Group projects on this device without changing their folders or settings.",
      fields: groupFields(null), confirmLabel: "Create", onSubmit: async (values) => {
        await api("/project-groups", { method: "POST", body: { action: "create", ...saveGroupNames(values) } });
        renderProjects();
      },
    }));
    const update = async (body) => {
      try { await api("/project-groups", { method: "POST", body }); renderProjects(); }
      catch (err) { toast(err.message, true); }
    };
    document.querySelectorAll("[data-project-icon]").forEach((button) => button.addEventListener("click", () => {
      const projectId = button.dataset.projectIcon;
      const chosen = { ...(icons[projectId] || {}) };
      const overlay = showModal({ title: "Project icon", body: "Pick a pixel character or type a short text or emoji, then a color.", fields: [], confirmLabel: "Save", onSubmit: async () => {
        await api("/project-groups", { method: "POST", body: { action: "icon", project_id: projectId, avatar: chosen.avatar || undefined, text: chosen.avatar ? undefined : chosen.text || undefined, tone: chosen.tone || undefined } });
        renderProjects();
      } });
      const area = overlay.querySelector(".fields");
      area.innerHTML = `<div class="icon-picker">
        <div class="icon-preview" id="icon-preview"></div>
        <div class="field"><label>${esc(UI.translate("Pixel character"))}</label><div class="avatar-picker" id="icon-avatars" role="radiogroup" aria-label="${esc(UI.translate("Pixel character"))}">${AVATAR_ORDER.map((avatar) => `<button type="button" class="avatar-option" role="radio" data-avatar="${avatar}" title="${esc(UI.translate(avatar))}"><span class="chat-avatar av-pm" aria-hidden="true">${spriteFor(avatar)}</span></button>`).join("")}</div></div>
        <div class="field"><label for="icon-text">${esc(UI.translate("Text or emoji"))}</label><input type="text" id="icon-text" maxlength="4" autocomplete="off" value="${esc(chosen.text || "")}" placeholder="${esc(projectId.slice(0, 1).toUpperCase())}" data-no-i18n /><div class="hint">${esc(UI.translate("Up to 4 characters. Leave empty to use the first letter."))}</div></div>
        <div class="field"><label>${esc(UI.translate("Color"))}</label><div class="tone-picker" id="icon-tones" role="radiogroup" aria-label="${esc(UI.translate("Color"))}">${ICON_TONES.map((tone) => `<button type="button" class="tone-swatch tone-${tone}" role="radio" data-tone="${tone}" aria-label="${esc(tone)}"></button>`).join("")}</div></div>
        <button type="button" class="btn small ghost" id="icon-reset">${esc(UI.translate("Use the first letter"))}</button>
      </div>`;
      UI.localize(area);
      const paint = () => {
        document.getElementById("icon-preview").innerHTML = projectIconHtml(projectId, chosen, "big");
        area.querySelectorAll("[data-avatar]").forEach((el) => { const on = el.dataset.avatar === chosen.avatar; el.classList.toggle("selected", on); el.setAttribute("aria-checked", String(on)); });
        area.querySelectorAll("[data-tone]").forEach((el) => { const on = el.dataset.tone === chosen.tone; el.classList.toggle("selected", on); el.setAttribute("aria-checked", String(on)); });
      };
      area.querySelectorAll("[data-avatar]").forEach((el) => el.addEventListener("click", () => { chosen.avatar = el.dataset.avatar; delete chosen.text; document.getElementById("icon-text").value = ""; paint(); }));
      area.querySelectorAll("[data-tone]").forEach((el) => el.addEventListener("click", () => { chosen.tone = el.dataset.tone; paint(); }));
      document.getElementById("icon-text").addEventListener("input", (event) => {
        const value = event.target.value.trim();
        if (value) { chosen.text = value; delete chosen.avatar; } else delete chosen.text;
        paint();
      });
      document.getElementById("icon-reset").addEventListener("click", () => { for (const key of Object.keys(chosen)) delete chosen[key]; document.getElementById("icon-text").value = ""; paint(); });
      paint();
    }));
    document.querySelectorAll(".project-move").forEach((select) => select.addEventListener("change", () => update({ action: "move", project_id: select.dataset.projectId, group_id: select.value })));
    document.querySelectorAll("[data-group-rename]").forEach((btn) => btn.addEventListener("click", () => {
      const group = groups.find((g) => g.id === btn.dataset.groupRename);
      showModal({ title: "Rename group", body: "Set the names shown for each interface language.", fields: groupFields(group), confirmLabel: "Save", onSubmit: async (values) => {
        await api("/project-groups", { method: "POST", body: { action: "rename", group_id: group.id, ...saveGroupNames(values) } });
        renderProjects();
      } });
    }));
    document.querySelectorAll("[data-group-delete]").forEach((btn) => btn.addEventListener("click", () => {
      const group = groups.find((g) => g.id === btn.dataset.groupDelete);
      showModal({ title: "Delete group", body: "Projects in this group will become ungrouped. No project files are deleted.", confirmLabel: "Delete group", danger: true, onSubmit: async () => {
        await api("/project-groups", { method: "POST", body: { action: "delete", group_id: group.id } });
        renderProjects();
      } });
    }));
    for (const direction of ["up", "down"]) document.querySelectorAll(`[data-group-${direction}]`).forEach((btn) => btn.addEventListener("click", () => {
      const ids = groups.map((g) => g.id);
      const index = ids.indexOf(btn.dataset[direction === "up" ? "groupUp" : "groupDown"]);
      const other = index + (direction === "up" ? -1 : 1);
      [ids[index], ids[other]] = [ids[other], ids[index]];
      update({ action: "reorder", group_ids: ids });
    }));
    document.querySelectorAll("[data-drag-project]").forEach((tile) => {
      tile.addEventListener("dragstart", (event) => { event.dataTransfer.setData("text/plain", tile.dataset.dragProject); event.dataTransfer.effectAllowed = "move"; tile.classList.add("dragging"); });
      tile.addEventListener("dragend", () => tile.classList.remove("dragging"));
    });
    document.querySelectorAll("[data-drop-group]").forEach((target) => {
      target.addEventListener("dragover", (event) => { event.preventDefault(); target.classList.add("drop-ready"); });
      target.addEventListener("dragleave", (event) => { if (!target.contains(event.relatedTarget)) target.classList.remove("drop-ready"); });
      target.addEventListener("drop", (event) => {
        event.preventDefault(); target.classList.remove("drop-ready");
        const projectId = event.dataTransfer.getData("text/plain");
        if (projects.some((p) => p.id === projectId)) update({ action: "move", project_id: projectId, group_id: target.dataset.dropGroup });
      });
    });
    watchForChanges(gen, () => Promise.all([api("/projects"), api("/project-groups")]), renderProjects);
  }

  async function browseFolder(path) {
    let data;
    try {
      data = await api("/browse?path=" + encodeURIComponent(path || ""));
    } catch (err) {
      toast(err.message, true);
      return;
    }
    const list = document.getElementById("folder-list");
    const pathInput = document.getElementById("folder-path");
    if (pathInput) pathInput.value = data.path;
    if (!list) return;
    const rows = data.directories.map((name) => `<button class="folder-row" type="button" data-dir="${esc(name)}">${esc(name)}/</button>`).join("");
    list.innerHTML = (data.parent ? `<button class="folder-row" type="button" data-dir="..">${esc(UI.translate(".. (parent)"))}</button>` : "") + rows;
    list.querySelectorAll(".folder-row").forEach((row) => {
      row.addEventListener("click", () => {
        const dir = row.dataset.dir;
        const next = dir === ".." ? data.parent : (data.path.replace(/\/$/, "") + "/" + dir);
        browseFolder(next);
      });
    });
  }

  function openAddProjectModal() {
    closeModal();
    const overlay = document.createElement("div");
    overlay.id = "modal-overlay";
    overlay.className = "modal-overlay";
    overlay.innerHTML = `
      <div class="modal" style="width:560px" role="dialog" aria-modal="true">
        <h2>Add an existing folder</h2>
        <div class="body">Register a folder that already exists as a ccc-mcp project. This never creates or modifies any file in it unless you also enable write below.</div>
        <div class="field">
          <label for="folder-path">Folder</label>
          <input type="text" id="folder-path" />
        </div>
        <div class="folder-list" id="folder-list"></div>
        <div class="field" style="margin-top:16px">
          <label for="add-id">Project ID (optional)</label>
          <input type="text" id="add-id" placeholder="derived from folder name" />
        </div>
        <div class="field">
          <div class="checkbox-row">
            <input type="checkbox" id="add-write" />
            <label for="add-write" style="margin:0">Also enable continuous write authorization now</label>
          </div>
          <div class="hint" id="add-write-hint" style="display:none">
            ccc-mcp will be able to write files inside this folder and run its configured test
            command automatically before each review round. Dependency installation can run
            package scripts, which are inside this same boundary.
          </div>
        </div>
        <div class="actions">
          <button class="btn" id="modal-cancel" type="button">Cancel</button>
          <button class="btn primary" id="modal-confirm" type="button">Add project</button>
        </div>
      </div>
    `;
    mountModal(overlay);
    UI.localize(overlay);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) closeModal(); });
    document.getElementById("modal-cancel").addEventListener("click", closeModal);
    document.getElementById("add-write").addEventListener("change", (e) => {
      document.getElementById("add-write-hint").style.display = e.target.checked ? "block" : "none";
    });
    document.getElementById("folder-path").addEventListener("change", (e) => browseFolder(e.target.value));
    browseFolder("");
    document.getElementById("modal-confirm").addEventListener("click", async () => {
      const confirm = document.getElementById("modal-confirm");
      if (confirm.disabled) return;
      const path = document.getElementById("folder-path").value;
      const id = document.getElementById("add-id").value || undefined;
      const wantWrite = document.getElementById("add-write").checked;
      confirm.disabled = true;
      confirm.setAttribute("aria-busy", "true");
      try {
        const added = await api("/projects/add", { method: "POST", body: { path, id } });
        closeModal();
        if (!wantWrite) {
          toast(`Added ${added.id}`);
          navTo(`/p/${encodeURIComponent(added.id)}/overview`);
          return;
        }
        promptAuthorize(added.id, () => navTo(`/p/${encodeURIComponent(added.id)}/overview`));
      } catch (err) { toast(err.message, true); }
      finally {
        confirm.disabled = false;
        confirm.removeAttribute("aria-busy");
      }
    });
  }

  function openCreateProjectModal() {
    showModal({
      title: "Create a new project",
      body: "Creates a new directory with a Git baseline. It never overwrites an existing folder.",
      fields: [
        { label: "Project ID", placeholder: "hello-demo" },
        { label: "Path (optional)", placeholder: "leave empty to use the default projects directory" },
      ],
      confirmLabel: "Create",
      onSubmit: async ([id, path]) => {
        if (!id) throw new Error("Project ID is required.");
        const created = await api("/projects/create", { method: "POST", body: { id, path: path || undefined } });
        toast(`Created ${created.id}`);
        navTo(`/p/${encodeURIComponent(created.id)}/overview`);
      },
    });
  }

  function openImportProjectModal() {
    closeModal();
    const overlay = document.createElement("div");
    overlay.id = "modal-overlay";
    overlay.className = "modal-overlay";
    overlay.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true">
        <h2>Import project backup</h2>
        <div class="body">Restore settings and saved task records into an existing, unregistered project folder. Source files are not included. The imported project starts read-only.</div>
        <div class="field"><label for="import-file">Backup file</label><input id="import-file" type="file" accept=".json,application/json" /></div>
        <div class="field"><label for="import-path">Project folder on this computer</label><input id="import-path" type="text" placeholder="/path/to/existing/project" autocomplete="off" /></div>
        <div class="actions"><button class="btn" id="modal-cancel" type="button">Cancel</button><button class="btn primary" id="modal-confirm" type="button">Import project</button></div>
      </div>`;
    mountModal(overlay);
    UI.localize(overlay);
    overlay.addEventListener("click", (event) => { if (event.target === overlay) closeModal(); });
    document.getElementById("modal-cancel").addEventListener("click", closeModal);
    document.getElementById("modal-confirm").addEventListener("click", async () => {
      const button = document.getElementById("modal-confirm");
      const file = document.getElementById("import-file").files[0];
      const path = document.getElementById("import-path").value.trim();
      if (!file || !path) { toast("Choose a backup file and project folder.", true); return; }
      button.disabled = true;
      try {
        const response = await fetch(`/api/projects/import?path=${encodeURIComponent(path)}`, {
          method: "POST", headers: { "X-CCC-UI-Token": TOKEN, "Content-Type": "application/json" },
          body: file,
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Import failed");
        closeModal();
        toast("Project backup imported. Write access is off.");
        navTo(`/p/${encodeURIComponent(result.project_id)}/overview`);
      } catch (error) { toast(error.message, true); }
      finally { button.disabled = false; }
    });
  }

  async function downloadProjectBackup(id) {
    const response = await fetch(`/api/projects/${encodeURIComponent(id)}/export`, {
      headers: { "X-CCC-UI-Token": TOKEN },
    });
    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.error || "Export failed");
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = `ccc-mcp-${id}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function promptAuthorize(projectId, onDone) {
    api(`/projects/${encodeURIComponent(projectId)}/authorize-preview`).then((preview) => {
      confirmTextModal({
        title: "Enable continuous write",
        description: preview.description,
        challenge: preview.challenge,
        confirmLabel: "Enable write",
        onConfirm: async (typed) => {
          await api(`/projects/${encodeURIComponent(projectId)}/authorize`, { method: "POST", body: { confirm: typed } });
          toast("Write authorization enabled.");
          if (onDone) onDone();
        },
      });
    }).catch((err) => toast(err.message, true));
  }

  // Who is speaking: every role has an avatar (icon + colour) and a display name that
  // defaults to its abbreviation and can be renamed in Settings.
  const ROLE_AVATARS = {
    PM: ["pm", '<path d="M5 21V4"/><path d="M5 4h11l-2 4 2 4H5"/>'],
    SA: ["sa", '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>'],
    SD: ["sd", '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>'],
    RD: ["rd", '<path d="m8 7-5 5 5 5"/><path d="m16 7 5 5-5 5"/><path d="m14 4-4 16"/>'],
    "SD/RD": ["rd", '<path d="m8 7-5 5 5 5"/><path d="m16 7 5 5-5 5"/><path d="m14 4-4 16"/>'],
    REVIEW: ["cr", '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'],
    SR: ["sr", '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="m9 14 2 2 4-4"/>'],
    AR: ["ar", '<circle cx="12" cy="12" r="9"/><path d="M12 3v4M12 17v4M3 12h4M17 12h4"/>'],
    SEC: ["sec", '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>'],
    SW: ["sw", '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>'],
    PA: ["pa", ""], RS: ["rs", ""], AN: ["an", ""], PL: ["pl", ""], CS: ["cs", ""], WR: ["wr", ""], ED: ["ed", ""], VC: ["vc", ""], RV: ["rv", ""],
    QA: ["qa", '<path d="m9 11 3 3 8-8"/><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h9"/>'],
    USER: ["user", '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>'],
  };
  const ROLE_ABBREVIATIONS = { REVIEW: "CR" };
  // Everyday roles are shown by what they do, not by an abbreviation.
  const ROLE_FRIENDLY = { PA: "Personal assistant", RS: "Researcher", AN: "Analyst", PL: "Planner", CS: "Consultant", WR: "Writer", ED: "Editor", VC: "Fact checker", RV: "Reviewer", AR: "Strict reviewer" };
  const EVERYDAY_ROLES = ["PA", "RS", "AN", "PL", "CS", "WR", "ED", "VC", "RV"];
  let activeRoleNames = {};
  const roleName = (role) => activeRoleNames[role] || (ROLE_FRIENDLY[role] ? UI.translate(ROLE_FRIENDLY[role]) : ROLE_ABBREVIATIONS[role] || role);
  const roleAvatarTone = (role) => (ROLE_AVATARS[role] || ROLE_AVATARS.SA)[0];
  // Ability charts. Every role scores 0-5 on six abilities (the backend owns the numbers, see
  // core/roles.py); a workflow's chart combines its roles the same way team_stats() does there:
  // the strongest member sets an ability and each other strong member adds a little.
  const STAT_ORDER = ["research", "analysis", "planning", "creation", "verification", "risk"];
  const STAT_NAMES = { research: "Research", analysis: "Reasoning", planning: "Planning", creation: "Creation", verification: "Verification", risk: "Risk sense" };
  const STAT_MAX = 5, STAT_STRONG = 3, STAT_BACKUP = 0.5, STAT_GAP = 1;
  let roleStatsMap = {};
  const round1 = (value) => Math.round(value * 10) / 10;
  function teamStats(roles, map = roleStatsMap) {
    const distinct = [...new Set(roles)].filter((role) => map[role]);
    const team = {};
    for (const axis of STAT_ORDER) {
      const scores = distinct.map((role) => map[role][axis]).sort((a, b) => b - a);
      const backups = scores.slice(1).filter((score) => score >= STAT_STRONG).length;
      team[axis] = Math.min(STAT_MAX, round1((scores[0] || 0) + STAT_BACKUP * backups));
    }
    return { team, power: round1(STAT_ORDER.reduce((sum, axis) => sum + team[axis], 0)), gaps: STAT_ORDER.filter((axis) => team[axis] <= STAT_GAP) };
  }
  const statSummary = (values) => STAT_ORDER.map((axis) => `${UI.translate(STAT_NAMES[axis])} ${values[axis]}`).join(", ");
  // A hexagon radar. `ghost` is a second, dashed outline (the whole team while only part of it
  // has finished); `labels` adds the ability names and scores around it.
  function radarSvg(values, { size = 120, labels = false, ghost = null } = {}) {
    const pad = labels === true ? 58 : labels === "short" ? (UI.getLocale() === "zh-TW" ? 20 : 28) : 6;
    const centre = size / 2, radius = size / 2 - pad;
    const point = (index, level) => {
      const angle = (-90 + index * 60) * Math.PI / 180;
      const length = radius * Math.max(0, Math.min(level, STAT_MAX)) / STAT_MAX;
      return [centre + length * Math.cos(angle), centre + length * Math.sin(angle)];
    };
    const ring = (level) => STAT_ORDER.map((_, index) => point(index, level).map((n) => n.toFixed(1)).join(",")).join(" ");
    const shape = (data) => STAT_ORDER.map((axis, index) => point(index, data[axis]).map((n) => n.toFixed(1)).join(",")).join(" ");
    const rings = (labels === true ? [1, 2, 3, 4, 5] : [2.5, 5]).map((level) => `<polygon class="radar-ring${level === STAT_MAX ? " outer" : ""}" points="${ring(level)}"/>`).join("");
    const spokes = STAT_ORDER.map((_, index) => { const [x, y] = point(index, STAT_MAX); return `<line class="radar-spoke" x1="${centre}" y1="${centre}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}"/>`; }).join("");
    const shortNames = labels === "short" ? STAT_ORDER.map((axis, index) => {
      const [x, y] = point(index, STAT_MAX * (1 + 8 / radius));
      const name = UI.translate(STAT_NAMES[axis]);
      const anchor = Math.abs(x - centre) < 4 ? "middle" : x > centre ? "start" : "end";
      return `<text class="radar-label short" x="${x.toFixed(1)}" y="${(y + (index === 0 ? -1 : index === 3 ? 9 : 4)).toFixed(1)}" text-anchor="${anchor}">${esc(UI.getLocale() === "zh-TW" ? name.slice(0, 1) : name.slice(0, 3))}</text>`;
    }).join("") : "";
    const names = labels === true ? STAT_ORDER.map((axis, index) => {
      const [x, y] = point(index, STAT_MAX + 1.5);
      const anchor = Math.abs(x - centre) < 4 ? "middle" : x > centre ? "start" : "end";
      return `<text class="radar-label" x="${x.toFixed(1)}" y="${(y + (index === 0 ? 0 : index === 3 ? 10 : 4)).toFixed(1)}" text-anchor="${anchor}">${esc(UI.translate(STAT_NAMES[axis]))} <tspan class="radar-score">${values[axis]}</tspan></text>`;
    }).join("") : "";
    const dots = labels === true ? STAT_ORDER.map((axis, index) => { const [x, y] = point(index, values[axis]); return `<circle class="radar-dot" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3"/>`; }).join("") : "";
    return `<svg class="radar" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" role="img" aria-label="${esc(statSummary(values))}" data-no-i18n>${rings}${spokes}${ghost ? `<polygon class="radar-ghost" points="${shape(ghost)}"/>` : ""}<polygon class="radar-fill" points="${shape(values)}"/>${dots}${names}${shortNames}</svg>`;
  }
  const STAT_HELP = {
    research: "Looks things up and collects information",
    analysis: "Breaks down needs, compares options and reasons it through",
    planning: "Sets steps, order and structure",
    creation: "Writes the code, text or document",
    verification: "Checks facts, runs tests and confirms results",
    risk: "Spots flaws, security holes and blind spots",
  };
  const statLegend = (values) => `<dl class="stat-legend">${STAT_ORDER.map((axis) => `<div><dt>${esc(UI.translate(STAT_NAMES[axis]))}${values ? ` <span class="num">${values[axis]}</span>` : ""}</dt><dd>${esc(UI.translate(STAT_HELP[axis]))}</dd></div>`).join("")}</dl>`;
  const statExplainer = () => `<details class="stat-explainer"><summary>${esc(UI.translate("What do the six abilities mean?"))}</summary>${statLegend()}</details>`;
  // "Strong at" / "Weaker at" in words, so nobody has to read the chart to know what a role or team is for.
  const strengthLine = (values, { strong = 4, weak = 1 } = {}) => {
    const named = (test) => STAT_ORDER.filter((axis) => test(values[axis])).sort((a, b) => values[b] - values[a]).map((axis) => UI.translate(STAT_NAMES[axis]));
    const good = named((v) => v >= strong), poor = named((v) => v <= weak);
    return `${good.length ? `<span class="strength-good">${esc(UI.translate("Strong at"))}: ${esc(good.join(" · "))}</span>` : ""}${poor.length ? `<span class="strength-weak">${esc(UI.translate("Weaker at"))}: ${esc(poor.join(" · "))}</span>` : ""}`;
  };
  const gapChips = (gaps) => gaps.length
    ? `<div class="team-gaps"><span>${esc(UI.translate("Nobody here is good at"))}</span>${gaps.map((axis) => `<span class="pill warning">${esc(UI.translate(STAT_NAMES[axis]))}</span>`).join("")}</div>`
    : `<div class="team-gaps ok">${esc(UI.translate("No weak spots: every ability has someone strong."))}</div>`;
  const teamPanel = (stats, ghost) => `<div class="team-panel"><div class="team-chart">${radarSvg(stats.team, { size: 290, labels: true, ghost })}</div>
      <div class="team-info"><div class="team-power"><span>${esc(UI.translate("Team power"))}</span><strong class="num">${stats.power}</strong><small>/ ${STAT_MAX * STAT_ORDER.length}</small></div>${gapChips(stats.gaps)}${statLegend(stats.team)}
      <p class="muted team-rule">${esc(UI.translate("Each role scores 0 to 5 on six abilities. The strongest member sets the team's score, and every other strong member (3 or more) adds 0.5, up to 5."))}</p></div></div>`;
  // Every role is a small pixel character: a chubby chibi base with its own hair, outfit and
  // accessory (PM crown, SA detective hat, SD beret, RD headphones, CR angel, SR monk,
  // AR devil, SEC knight, QA tester). "." keeps the base pixel, anything else replaces it.
  const SPRITE_BASE = [
    "..............",
    "..............",
    "...oooooooo...",
    "..ohhhhhhhho..",
    ".ohhhhhhhhhho.",
    ".ohhsssssshho.",
    ".osseWsseWsso.",
    ".osseesseesso.",
    ".oscssmmsscso.",
    "..osssssssso..",
    "...oooooooo...",
    "..obbbbbbbbo..",
    "..obbbbbbbbo..",
    "...oooooooo...",
  ];
  const SPRITE_COLORS = { o: "var(--sprite-outline, #2d2a32)", s: "#ffd9b8", e: "#2a2233", W: "#ffffff", c: "#ff9aa8", m: "#c0504d", y: "#ffcf3a", a: "#bfe3ff", r: "#e5484d", x: "#9aa7b5", g: "#4ca85a", k: "#2b3042", p: "#d36bb8", b: "var(--av)" };
  const BLOB_BODY = [[10, ".obbbbbWbbbbo."], [11, ".obbWbWbbbbbo."], [12, ".obbbWbbbbbbo."], [13, "..oooooooooo.."]];
  const SPRITES = {
    person: { hair: "#5a3b1e", edits: [] },
    boss: { hair: "#4a3a2a", edits: [[0, "....y.yy.y...."], [1, "....yyyyyy...."], [11, "......rr......"], [12, "......rr......"]] },
    detective: { hair: "#6b4423", edits: [[1, "....kkkkkk...."], [2, "...kkrrrrkk..."], [3, ".kkkkkkkkkkkk."], [10, "...........ooo"], [11, "...........oWo"], [12, "...........ooo"], [13, ".............k"]] },
    designer: { hair: "#d36bb8", edits: [[0, ".......p......"], [1, "....pppppp...."], [2, "...pppppppp..."]] },
    dev: { hair: "#2f3b52", edits: [[2, "...kkkkkkkk..."], [3, "..k........k.."], [4, ".k..........k."], [5, "kk..........kk"], [6, "kk..........kk"], [7, "kk..........kk"]] },
    angel: { hair: "#ffe08a", body: "#ffffff", edits: [[0, "....yyyyyy...."], [1, "...y......y..."], [8, "a............a"], [9, "aa..........aa"], [10, "aaa........aaa"], [11, "aa..........aa"]] },
    monk: { hair: "#2b2b3a", body: "#f0b429", edits: [[0, "......kk......"], [1, ".....kkkk....."], [5, "......rr......"], [6, "..ssssssssss.."], [7, "s............s"], [8, "s............s"], [9, "s............s"]] },
    devil: { hair: "#5b2a86", edits: [[0, ".rr........rr."], [1, "..r........r.."], [5, "....kk..kk...."], [9, "......WW......"], [11, ".............r"], [12, "............rr"]] },
    knight: { hair: "#9aa7b5", edits: [[0, "......rr......"], [1, ".....rrrr....."], [2, "...xxxxxxxx..."], [3, "..xxxxxxxxxx.."], [4, ".xxxxxxxxxxxx."], [5, "..xx......xx.."], [10, "...........xxx"], [11, "...........xyx"], [12, "...........xxx"]] },
    tester: { hair: "#3f8f4a", edits: BLOB_BODY },
    cat: { hair: "#f0a04b", skin: "#f7c27a", edits: [[1, "..hh......hh.."], [2, "..hhh....hhh.."], [7, "k............k"], [9, "k............k"], [8, "kk..........kk"]] },
    robot: { hair: "#6b7a8a", skin: "#c9d3dc", edits: [[0, ".......r......"], [1, ".......k......"], [6, "x............x"], [7, "x............x"]] },
    bear: { hair: "#8b5a2b", skin: "#c8955c", edits: [[1, "..hh......hh.."], [2, "..hh......hh.."], [8, ".....WmmW....."]] },
    frog: { hair: "#5fb85a", skin: "#8ee07d", edits: [[1, "..WW......WW.."], [2, "..WeW....WeW.."], [8, "....mmmmmm...."]] },
    assistant: { hair: "#3b2f2f", edits: [[11, ".....rWWr....."]] },
    researcher: { hair: "#8a4b2a", body: "#f4f4f4", edits: [[10, "...........aaa"], [11, "...........aga"], [12, "...........aaa"]] },
    analyst: { hair: "#3a3a4a", edits: [[5, "...kkkk.kkkk.."], [10, ".............y"], [11, "............yy"], [12, "...........yyy"]] },
    planner: { hair: "#a0522d", edits: [[10, "...........WWW"], [11, "...........WkW"], [12, "...........WWW"]] },
    consultant: { hair: "#9aa0a6", body: "#3b4252", edits: [[11, "......WW......"], [12, "......rr......"]] },
    author: { hair: "#7a4a21", edits: [[7, "h............h"], [8, "h............h"], [9, "h............h"], [10, ".............W"], [11, "............W."], [12, "...........y.."]] },
    editor: { hair: "#444444", edits: [[4, ".gggggggggggg."], [10, ".............r"], [11, "............r."]] },
    checker: { hair: "#3a2a1a", edits: [[1, "....kkkkkk...."], [2, "...kkkyykkk..."], [3, ".kkkkkkkkkkkk."], [10, "...........ggg"], [11, "...........gWg"], [12, "...........ggg"], [13, ".............k"]] },
    reviewer: { hair: "#e8e8e8", edits: [[1, "....WWWWWW...."], [2, "..WWWWWWWWWW.."], [5, "W............W"], [6, "W............W"], [7, "W............W"], [10, "...........rrr"], [11, "...........rWr"], [12, "...........rrr"]] },
    writer: { hair: "#4a3a2a", edits: [[0, ".kkkkkkkkkkkk."], [1, "...kkkkkkkk.y."], [2, "............y."], [10, ".............y"], [11, "............y."], [12, "...........y.."]] },
  };
  const DEFAULT_AVATAR = { USER: "person", PM: "boss", SA: "detective", SD: "designer", RD: "dev", "SD/RD": "dev", REVIEW: "angel", SR: "monk", AR: "devil", SEC: "knight", QA: "tester", SW: "writer", PA: "assistant", RS: "researcher", AN: "analyst", PL: "planner", CS: "consultant", WR: "author", ED: "editor", VC: "checker", RV: "reviewer" };
  const AVATAR_ORDER = ["boss", "detective", "designer", "dev", "angel", "monk", "devil", "knight", "tester", "person", "cat", "robot", "bear", "frog", "writer", "assistant", "researcher", "analyst", "planner", "consultant", "author", "editor", "checker", "reviewer"];
  let activeRoleAvatars = {};
  let activeTouchesCode = true;
  const spriteCache = {};
  const roleAvatarIcon = (role) => spriteFor(activeRoleAvatars[role] || DEFAULT_AVATAR[role] || "person");
  const spriteFor = (id) => {
    if (spriteCache[id]) return spriteCache[id];
    const sprite = SPRITES[id] || SPRITES.person;
    const grid = SPRITE_BASE.map((row) => row.split(""));
    for (const [y, text] of sprite.edits) [...text].forEach((char, x) => { if (char !== ".") grid[y][x] = char; });
    const fill = (char) => (char === "h" ? sprite.hair : char === "s" && sprite.skin ? sprite.skin : char === "b" && sprite.body ? sprite.body : SPRITE_COLORS[char]);
    const rects = [];
    grid.forEach((row, y) => {
      let x = 0;
      while (x < row.length) {
        const char = row[x];
        if (char === ".") { x += 1; continue; }
        let end = x;
        while (end + 1 < row.length && row[end + 1] === char) end += 1;
        rects.push(`<rect x="${x}" y="${y}" width="${end - x + 1}" height="1" fill="${fill(char)}"/>`);
        x = end + 1;
      }
    });
    return (spriteCache[id] = `<svg class="role-sprite" viewBox="0 0 14 14" shape-rendering="crispEdges" aria-hidden="true">${rects.join("")}</svg>`);
  };

  const chatParticipants = (roles) => [...(roles || ["SA", "SD/RD", "REVIEW"]), "PM"].map((role) => `<span class="chat-avatar av-${roleAvatarTone(role)}" title="${esc(roleName(role))}">${roleAvatarIcon(role).replace('width="18" height="18"', 'width="15" height="15"')}</span>`).join("");
  const PAGE_ICONS = {
    profile: '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>',
    roles: '<circle cx="9" cy="7" r="4"/><path d="M2 21v-2a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v2"/><path d="M16 3.1a4 4 0 0 1 0 7.8"/><path d="M22 21v-2a4 4 0 0 0-3-3.9"/>',
    language: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    test: '<path d="m4 17 6-6-6-6"/><path d="M12 19h8"/>',
    runtime: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2"/>',
    meeting: '<path d="M14 9a2 2 0 0 1-2 2H6l-4 4V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2z"/><path d="M18 9h2a2 2 0 0 1 2 2v11l-4-4h-6a2 2 0 0 1-2-2v-1"/>',
    evaluation: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    workflow: '<circle cx="6" cy="6" r="3"/><circle cx="18" cy="18" r="3"/><path d="M9 6h6a3 3 0 0 1 3 3v6"/>',
    gate: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M12 8v4"/><path d="M12 16h.01"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
    danger: '<path d="m21.7 18-8-14a2 2 0 0 0-3.4 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    doc: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/>',
    decision: '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    question: '<circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
    package: '<path d="M21 8a2 2 0 0 0-1-1.7l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.7l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
    server: '<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><path d="M6 6h.01M6 18h.01"/>',
    search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    code: '<path d="m16 18 6-6-6-6"/><path d="m8 6-6 6 6 6"/>',
    folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9L9.6 3.9A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2z"/>',
    inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.7 4H7.3a2 2 0 0 0-1.8 1.1z"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
    key: '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6"/><path d="m15.5 7.5 3 3L22 7l-3-3"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
  };
  const pageIcon = (name) => `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PAGE_ICONS[name]}</svg>`;
  // One titled card: icon + heading + description, a body, and an optional footer / header actions.
  // `title` and `description` are plain English strings; the DOM localizer translates them.
  const pageCard = (icon, title, description, body, footer, opts = {}) => `<section class="settings-card${opts.tone ? " is-" + opts.tone : ""}">
      <header class="settings-card-head"><span class="settings-card-icon">${pageIcon(icon)}</span><div><h2>${title}</h2>${description ? `<p>${description}</p>` : ""}</div>${opts.actions ? `<div class="settings-card-actions">${opts.actions}</div>` : ""}</header>
      <div class="settings-card-body${opts.flush ? " flush" : ""}">${body}</div>${footer ? `<footer class="settings-card-foot">${footer}</footer>` : ""}
    </section>`;

  async function renderProjectOverview() {
    const gen = renderGen;
    const route = parseRoute();
    const id = route.project;
    layout(loadingView());
    let project;
    try {
      const projects = await api("/projects");
      project = projects.find((p) => p.id === id);
      if (!project) throw new Error("Project not found");
    } catch (err) {
      if (!stale(gen)) errorView(err);
      return;
    }
    if (stale(gen)) return;
    const access = project.access;
    let tasks = [];
    try { tasks = await api(`/projects/${encodeURIComponent(id)}/tasks`); } catch (err) { tasks = []; }
    if (stale(gen)) return;
    const finished = tasks.filter((t) => t.state === "COMPLETED").length;
    const running = tasks.filter((t) => !["COMPLETED", "CANCELLED"].includes(t.state)).length;
    const latest = [...tasks].sort((a, b) => String(a.created || "").localeCompare(String(b.created || ""))).at(-1);
    const dangerRow = (title, description, actions, hint) => `<div class="danger-row"><div><strong>${title}</strong><p>${description}</p>${hint || ""}</div><div class="danger-row-actions">${actions}</div></div>`;
    layout(`
      <div class="title-row">
        <div><h1>${esc(id)}</h1><div class="sub path-line"><span class="mono" data-no-i18n>${esc(project.root)}</span><button type="button" class="btn small ghost" id="btn-copy-path">Copy path</button></div></div>
        <div class="title-actions">
          <a class="btn" href="#/p/${encodeURIComponent(id)}/tasks">View tasks</a>
          <a class="btn" href="#/p/${encodeURIComponent(id)}/settings">Settings</a>
          <button class="btn" id="btn-export-project">Export backup</button>
        </div>
      </div>
      <div class="kpi-strip">
        <div class="kpi"><div class="v num">${tasks.length}</div><div class="l">Tasks</div></div>
        <div class="kpi"><div class="v num">${finished}</div><div class="l">Completed</div></div>
        <div class="kpi"><div class="v num">${running}</div><div class="l">In progress</div></div>
        <div class="kpi"><div class="v">${accessPill(access)}</div><div class="l">Access</div></div>
      </div>
      <div class="settings-stack">
        ${latest ? pageCard("clock", "Latest task", "The most recent work request in this project.",
          `<a class="latest-task" href="#/p/${encodeURIComponent(id)}/tasks/${encodeURIComponent(latest.id)}">
            <span class="latest-task-id mono">${esc(latest.id)}</span>
            <span class="latest-task-text">${esc((latest.requirement || "").slice(0, 160))}</span>
            <span class="pill neutral">${esc(UI.translate(latest.state))}</span>
          </a>`) : ""}
        ${pageCard("shield", "Access", "Whether ccc-mcp can write files in this project and run its test command.",
          `<div class="kv-row"><span class="k">Mode</span><span class="v">${accessPill(access)}</span></div>
          ${access.mode === "read-write" ? `
            <div class="kv-row"><span class="k">Authorized at</span><span class="v">${whenHtml(access.authorized_at)}</span></div>
            <div class="kv-row"><span class="k">Identity</span><span class="v mono">${esc(access.identity || "")}</span></div>
          ` : ""}`,
          access.mode === "read-write"
            ? `<button class="btn small" id="btn-revoke">Revoke write (back to read-only)</button>`
            : `<button class="btn small primary" id="btn-enable-write">Enable write</button>`)}
        ${pageCard("danger", "Destructive actions", "These change or remove this project's ccc-mcp records. Read each one before you confirm.",
          `<div class="danger-list">
            ${dangerRow("Clear workflow history", "Archives the database and resets tasks/context for this project. Source files, settings and authorization are preserved.",
              `<button class="btn" id="btn-clear-history">Clear history…</button>`)}
            ${dangerRow("Remove from ccc-mcp", "Removes this project and clears its ccc-mcp records by default. Choose whether to keep the folder; select Keep records in the confirmation if you need an archive.",
              `<div class="project-remove-actions"><button class="btn primary" id="btn-remove">Remove, keep folder…</button><button class="btn danger" id="btn-remove-files">Remove and delete folder…</button></div>`)}
            ${dangerRow("Permanently retire project ID", "Permanently retires this project ID. Never deletes files on disk or historical records. Cannot be undone.",
              `<button class="btn danger" id="btn-delete" ${access.mode === "read-write" ? "disabled" : ""}>Retire ID…</button>`,
              access.mode === "read-write" ? `<div class="hint">Revoke write access first.</div>` : "")}
          </div>`, "", { tone: "danger" })}
      </div>
    `, { narrow: true, contentClass: "settings-wide" });
    document.getElementById("btn-copy-path").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(project.root); toast("Path copied."); }
      catch (_) { toast("Could not copy. Select the path and copy it manually.", true); }
    });
    document.getElementById("btn-export-project").addEventListener("click", async () => {
      try { await downloadProjectBackup(id); }
      catch (error) { toast(error.message, true); }
    });
    const enableBtn = document.getElementById("btn-enable-write");
    if (enableBtn) enableBtn.addEventListener("click", () => promptAuthorize(id, renderProjectOverview));
    const revokeBtn = document.getElementById("btn-revoke");
    if (revokeBtn) revokeBtn.addEventListener("click", () => {
      showModal({
        title: "Revoke write access",
        body: "ccc-mcp will no longer be able to write files in this project or run its test command automatically.",
        confirmLabel: "Revoke",
        danger: true,
        onSubmit: async () => {
          await api(`/projects/${encodeURIComponent(id)}/revoke`, { method: "POST" });
          toast("Reverted to read-only.");
          renderProjectOverview();
        },
      });
    });
    document.getElementById("btn-clear-history").addEventListener("click", async () => {
      let preview;
      try { preview = await api(`/projects/${encodeURIComponent(id)}/clear-history-preview`); }
      catch (err) { toast(err.message, true); return; }
      confirmTextModal({
        title: "Clear workflow history",
        description: preview.description,
        challenge: preview.challenge,
        confirmLabel: "Clear history",
        onConfirm: async (typed) => {
          await api(`/projects/${encodeURIComponent(id)}/clear-history`, { method: "POST", body: { confirm: typed } });
          toast("History cleared.");
          renderProjectOverview();
        },
      });
    });
    for (const [buttonId, deleteFiles] of [["btn-remove", false], ["btn-remove-files", true]]) {
      document.getElementById(buttonId).addEventListener("click", async () => {
        let preview;
        try {
          preview = await api(`/projects/${encodeURIComponent(id)}/remove-preview?delete_files=${deleteFiles}`);
        } catch (err) { toast(err.message, true); return; }
        confirmTextModal({
          title: deleteFiles ? "Remove and delete folder" : "Remove, keep folder",
          description: UI.getLocale() === "en" ? preview.description_en : preview.description,
          challenge: preview.challenge,
          confirmLabel: deleteFiles ? "Delete folder and remove" : "Remove from list",
          checkbox: {
            label: "Keep ccc-mcp records in an archive",
            hint: "Off by default. The archive includes the full ccc-mcp database; re-registering this ID still starts with empty records.",
            descriptionWhenChecked: UI.getLocale() === "en" ? preview.description_en_keep_records : preview.description_keep_records,
          },
          onConfirm: async (typed, keepRecords) => {
            const result = await api(`/projects/${encodeURIComponent(id)}/remove`, {
              method: "POST", body: { confirm: typed, delete_files: deleteFiles, keep_records: keepRecords },
            });
            if (result.cleanup_error) {
              const paths = result.remaining_paths || [result.remaining_path];
              toast(`${UI.translate("Project removed, but cleanup failed. Remaining path:")} ${paths.join(", ")}`, true);
            } else if (result.directory_was_missing) {
              toast("Project removed; the folder was already missing.");
            } else {
              toast(result.files_deleted ? "Project and folder removed." : "Project removed; folder kept.");
            }
            navTo("/");
          },
        });
      });
    }
    const deleteBtn = document.getElementById("btn-delete");
    if (!deleteBtn.disabled) deleteBtn.addEventListener("click", async () => {
      let preview;
      try { preview = await api(`/projects/${encodeURIComponent(id)}/delete-preview`); }
      catch (err) { toast(err.message, true); return; }
      confirmTextModal({
        title: "Permanently retire project ID",
        description: preview.description,
        challenge: preview.challenge,
        confirmLabel: "Retire ID",
        onConfirm: async (typed) => {
          await api(`/projects/${encodeURIComponent(id)}/delete`, { method: "POST", body: { confirm: typed } });
          toast("Project deregistered.");
          navTo("/");
        },
      });
    });
    watchForChanges(gen, () => api("/projects"), renderProjectOverview);
  }

  const STAGES = [
    "CREATED", "ANALYZING", "AWAITING_PM_DECISION", "AWAITING_USER_APPROVAL", "APPROVED",
    "IMPLEMENTING", "AWAITING_SA_CLARIFICATION", "REVIEWING", "AWAITING_PM_REVIEW", "COMPLETED",
  ];
  const TERMINAL_FAIL = new Set(["FAILED", "CANCELLED", "REJECTED", "BLOCKED"]);

  function stageStrip(state) {
    const failed = TERMINAL_FAIL.has(state);
    const idx = STAGES.indexOf(state);
    const segs = STAGES.map((stage, i) => {
      let cls = "seg";
      if (failed) cls += i === 0 ? " failed" : "";
      else if (idx !== -1 && i < idx) cls += " done";
      else if (idx !== -1 && i === idx) cls += state === "COMPLETED" ? " done" : " now";
      return `<div class="${cls}"></div>`;
    }).join("");
    return `<div class="stage-strip">${segs}</div>`;
  }

  const taskRowHtml = (t) => `
      <tr class="clickable" data-task="${esc(t.id)}" tabindex="0" role="link" aria-label="${esc(t.id)}">
        <td class="mono">${esc(t.id)}</td>
        <td><span class="pill ${taskStateTone(t.state)}">${esc(UI.translate(t.state))}</span></td>
        <td class="task-req" title="${esc(t.requirement || "")}">${esc((t.requirement || "").slice(0, 80))}</td>
        <td class="task-when">${whenHtml(t.created)}</td>
        <td class="table-actions">${REMOVABLE_STATES.has(t.state) ? `<button class="btn small danger quiet" data-delete-task="${esc(t.id)}">${esc(UI.translate("Delete"))}</button>` : ""}</td>
      </tr>
    `;

  // Update the task table in place: new tasks slide in, changed rows are replaced, removed
  // rows disappear, and rows that did not change are never touched.
  function patchTaskRows(tbody, tasks) {
    const template = document.createElement("template");
    template.innerHTML = tasks.map(taskRowHtml).join("");
    const incoming = [...template.content.children];
    incoming.forEach((el) => UI.localize(el));
    const live = new Map([...tbody.children].map((el) => [el.dataset.task, el]));
    const strip = (el) => el.outerHTML.replace(/ is-entering/g, "");
    const finalOrder = incoming.map((node) => {
      const existing = live.get(node.dataset.task);
      if (existing) return strip(existing) === strip(node) ? existing : node;
      enter(node, "is-entering");
      return node;
    });
    applyOrder(tbody, finalOrder);
  }

  async function renderTasksList() {
    const gen = renderGen;
    const id = parseRoute().project;
    const tasksPath = `/projects/${encodeURIComponent(id)}/tasks`;
    layout(loadingView());
    let tasks;
    try { tasks = await api(tasksPath); }
    catch (err) { if (!stale(gen)) errorView(err); return; }
    if (stale(gen)) return;
    layout(`
      <div class="title-row"><div><h1>Tasks</h1><div class="sub">${esc(id)}</div></div></div>
      ${tasks.length ? `
        <table class="task-table">
          <thead><tr><th>Task</th><th>State</th><th>Requirement</th><th>Created</th><th></th></tr></thead>
          <tbody>${tasks.map(taskRowHtml).join("")}</tbody>
        </table>
      ` : `<div class="empty">${esc(UI.translate("Tasks created through ChatGPT or the CLI will appear here."))}</div>`}
    `);
    const signature = (list) => JSON.stringify(list.map((t) => [t.id, t.state, t.requirement, t.created]));
    let baseline = signature(tasks);
    // One refresh path for the poll and for the delete button: a table that appears or
    // empties out is a full redraw (rare), anything else is patched row by row.
    const refresh = async () => {
      let fresh;
      try { fresh = await api(tasksPath); } catch (_) { return; }
      if (stale(gen)) return;
      const next = signature(fresh);
      if (next === baseline) return;
      baseline = next;
      const tbody = document.querySelector("table tbody");
      if (!fresh.length || !tbody) { renderTasksList(); return; }
      patchTaskRows(tbody, fresh);
    };
    const table = document.querySelector("table");
    if (table) {
      const open = (taskId) => navTo(`/p/${encodeURIComponent(id)}/tasks/${encodeURIComponent(taskId)}`);
      table.addEventListener("click", (event) => {
        const remove = event.target.closest("[data-delete-task]");
        if (remove) { confirmDeleteTask(id, remove.dataset.deleteTask, refresh); return; }
        const row = event.target.closest("tr[data-task]");
        if (row) open(row.dataset.task);
      });
      table.addEventListener("keydown", (event) => {
        const row = event.target.closest("tr[data-task]");
        if (row && event.target === row && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); open(row.dataset.task); }
      });
    }
    let polling = false;
    trackTimer(setInterval(async () => {
      if (stale(gen) || polling) return;
      polling = true;
      try { await refresh(); } finally { polling = false; }
    }, 2500));
  }

  function verificationLine(tests) {
    if (!tests) return "";
    if (!tests.executed_by_host) return `<span class="pill warning">${esc(UI.translate("host tests not run"))}</span>`;
    if (tests.no_tests_collected) return `<span class="pill warning">${esc(UI.translate("no tests collected"))}</span>`;
    return tests.passed ? `<span class="pill positive">${esc(UI.translate("host tests passed"))}</span>` : `<span class="pill negative">${esc(UI.translate("host tests failed"))}</span>`;
  }

  function testScopeLine(tests) {
    const selection = tests && tests.selection;
    if (!selection) return "";
    if (selection.mode === "changed_test_files") {
      return `${esc(UI.translate("Automatically selected changed test files"))}: <span class="mono">${esc((selection.paths || []).join(", "))}</span>`;
    }
    const label = selection.mode === "full_suite" ? "Project default test scope" :
      selection.mode === "project_pytest_config" ? "Scope from changed pytest settings" : "Project test command";
    return esc(UI.translate(label));
  }

  function dependencyLine(tests) {
    const deps = tests && tests.dependencies;
    if (!deps || !deps.detected) return "";
    const label = deps.installed ? "Installed before testing" : "Dependency installation failed";
    return `${esc(UI.translate(label))} · <span class="mono">${esc(deps.manifest || "")}</span>${deps.reason ? ` · ${esc(deps.reason)}` : ""}`;
  }

  function evaluationReport(row) {
    const score = row.quality_score === null ? "—" : `${row.quality_score}/100`;
    const coverage = Math.round((row.evidence_coverage || 0) * 100);
    const sub = row.scoring_details?.sub_scores || {};
    const dims = Object.entries(row.scoring_details?.weights || {}).map(([name]) => `<span>${esc(UI.translate(name))}: <strong>${sub[name] === null || sub[name] === undefined ? "—" : Math.round(sub[name])}</strong></span>`).join("");
    return `<article class="evaluation-report"><div class="evaluation-report-top"><div><strong>${esc(row.role)} · ${esc(row.agent)}</strong><small>${esc(row.evaluation_timestamp)} · v${esc(row.version)} · ${esc(row.trigger)}</small></div><span class="pill ${row.task_status === "PASSED" ? "positive" : row.task_status === "FAILED" ? "negative" : "warning"}">${esc(UI.translate(row.task_status))}</span></div><div class="evaluation-measures"><span><strong class="num">${esc(score)}</strong>${esc(UI.translate("Quality from available signals"))}</span><span><strong class="num">${coverage}%</strong>${esc(UI.translate("Evidence coverage"))}</span></div><div class="evaluation-dimensions">${dims}</div><details><summary>${esc(UI.translate("Evidence and disagreements"))} · ${esc((row.evidence || []).length)} / ${esc((row.disagreements || []).length)}</summary><pre class="mono">${esc(JSON.stringify({ evidence: row.evidence, disagreements: row.disagreements, features: row.scoring_details?.features }, null, 2))}</pre></details><button class="btn small" data-reevaluate="${esc(row.source_execution_id)}">${esc(UI.translate("Re-evaluate execution"))}</button></article>`;
  }

  let taskDetailPane = "conversation";
  let taskDetailDraft = "";
  let taskDetailRoute = "";

  function renderTaskVersionMismatch(status, projectId, taskId, missingPaths, legacyExecutions) {
    const transitions = (status.events || []).filter((event) => ["CREATED", "COMPLETED", "CANCELLED"].includes(event.new_state)).map((event) => ({
      event_id: `legacy-state-${event.seq}`, event_type: event.new_state === "CREATED" ? "task_created" : event.new_state === "COMPLETED" ? "task_completed" : "task_cancelled",
      actor: event.new_state === "CREATED" ? "user" : "system", role: null, execution_id: null,
      timestamp: event.created, summary: event.new_state === "CREATED" ? status.requirement : event.reason,
      detail: null, evidence_refs: [], context_version: null, legacy: true,
    }));
    const executions = (legacyExecutions || []).filter((execution) => execution.finished).map((execution) => ({
      event_id: `legacy-agent-${execution.id}`, event_type: execution.status === "SUCCEEDED" ? "agent_completed" : "agent_failed",
      actor: execution.agent, role: execution.role, execution_id: execution.id, timestamp: execution.finished,
      summary: execution.result?.report?.summary || execution.error || "",
      detail: execution.result, evidence_refs: [], context_version: execution.context_version,
    }));
    const events = [...transitions, ...executions].sort((a, b) => (a.timestamp || "").localeCompare(b.timestamp || "") || a.event_id.localeCompare(b.event_id));
    layout(`<div class="task-workspace">
      <header class="task-workspace-head"><div><a class="task-back" href="#/p/${encodeURIComponent(projectId)}/tasks">← ${esc(UI.translate("Back to tasks"))}</a><h1>${esc(UI.translate("Task discussion"))}</h1><div class="task-identifiers"><span>${esc(projectId)}</span><span class="mono">${esc(taskId)}</span><span class="pill warning">${esc(UI.translate(status.state || "UNKNOWN"))}</span></div></div></header>
      <div class="task-conversation-room"><div class="chat-room-bar"><div class="chat-room-title"><span class="chat-room-mark" aria-hidden="true">#</span><div><strong>${esc(UI.translate("Task meeting room"))}</strong><small>${esc(taskId)} · ${esc(UI.translate("Read-only conversation"))}</small></div></div><div class="chat-participants" aria-label="${esc(UI.translate("Meeting participants"))}">${chatParticipants()}</div></div>
        <div class="chat-sync-warning" role="alert"><strong>${esc(UI.translate("Restart the local UI server"))}</strong><span>${esc(UI.translate("The browser has the new task page, but the running ccc-mcp UI server does not provide its required API. Stop that server, run ccc-mcp ui again, then open its newly printed URL."))}</span><small class="mono">${esc(missingPaths.join(", "))}</small></div>
        <div class="conversation-feed" role="log" aria-label="${esc(UI.translate("Work conversation"))}">${taskConversation(events)}</div>
        <div class="task-composer"><label>${esc(UI.translate("Correction or instruction"))}</label><textarea rows="2" disabled placeholder="${esc(UI.translate("Restart the UI server to send a correction."))}"></textarea></div>
      </div>
    </div>`);
    const feed = document.querySelector(".conversation-feed");
    if (feed) feed.scrollTop = feed.scrollHeight;
    bindChatDetailButtons(events);
  }

  const TASK_ROLE_LABELS = { SA: "Analysis", "SD/RD": "Development", SD: "Design", RD: "Development", REVIEW: "Review", SR: "Specification review", AR: "Adversarial review", SEC: "Security review", QA: "Quality assurance", SW: "Specification writing", PA: "Coordination", RS: "Research", AN: "Data analysis", PL: "Planning", CS: "Advice", WR: "Writing", ED: "Editing", VC: "Fact checking", RV: "Review", PM: "Summary" };

  function statusPill(kind, text) {
    return `<span class="pill ${kind}">${esc(UI.translate(text))}</span>`;
  }

  // Short, readable facts pulled from a host-test result; everything else stays in the raw data.
  function hostTestFacts(detail) {
    if (!detail) return [];
    const facts = [];
    if (!detail.executed_by_host) facts.push(["Result", statusPill("warning", "Not verified"), "status"]);
    else if (detail.no_tests_collected) facts.push(["Result", statusPill("warning", "No tests collected"), "status"]);
    else facts.push(["Result", detail.passed ? statusPill("positive", "Host tests passed") : statusPill("negative", "Host tests failed"), "status"]);
    if (Array.isArray(detail.command) && detail.command.length) {
      const [program, ...args] = detail.command;
      facts.push(["Test command", [String(program).split(/[\\/]/).pop(), ...args].join(" "), "code"]);
    }
    const selection = detail.selection;
    if (selection) {
      const scope = selection.full_suite ? UI.translate("Full test suite") : selection.mode === "changed_test_files" ? UI.translate("Only test files changed in this round") : UI.translate("Selected tests");
      facts.push(["Test scope", Array.isArray(selection.paths) && selection.paths.length ? `${scope} · ${selection.paths.join(", ")}` : scope]);
    }
    if (detail.git_revision) facts.push(["Revision", String(detail.git_revision).slice(0, 8), "code"]);
    if (detail.identity) facts.push(["Run as", detail.identity, "code"]);
    if (!detail.executed_by_host && detail.reason) facts.push(["Reason", detail.reason]);
    return facts;
  }

  function reportFacts(detail) {
    const report = detail && detail.report;
    if (!report || typeof report !== "object") return [];
    const facts = [];
    if (typeof report.summary === "string" && report.summary) facts.push(["Summary", report.summary]);
    if (typeof report.next_action === "string" && report.next_action) facts.push(["Next action", report.next_action]);
    return facts;
  }

  function showTaskEventDetail(event) {
    closeModal();
    const overlay = document.createElement("div");
    overlay.id = "modal-overlay";
    overlay.className = "modal-overlay";
    const isHostTest = event.event_type === "host_test_completed";
    const contextLabel = TASK_ROLE_LABELS[event.role] || (isHostTest ? "Host tests" : "System update");
    const payload = {
      execution_id: event.execution_id,
      detail: event.detail,
      evidence_refs: event.evidence_refs,
      context_version: event.context_version,
      delivered_execution_id: event.delivered_execution_id,
    };
    const facts = isHostTest ? hostTestFacts(event.detail) : reportFacts(event.detail);
    const factsHtml = facts.length ? `<dl class="task-detail-facts">${facts.map(([name, value, kind]) => `<div><dt>${esc(UI.translate(name))}</dt><dd${kind === "code" ? ' class="mono"' : ""}>${kind === "status" ? value : esc(value)}</dd></div>`).join("")}</dl>` : "";
    const output = isHostTest && typeof event.detail?.stdout === "string" && event.detail.stdout.trim() ? `<section class="task-detail-section"><h3>${esc(UI.translate("Test output"))}</h3><pre class="mono task-detail-output" tabindex="0">${esc(event.detail.stdout.trim())}</pre></section>` : "";
    overlay.innerHTML = `<div class="modal task-detail-dialog" role="dialog" aria-modal="true" aria-labelledby="task-detail-title">
      <div class="task-detail-dialog-head"><div><small>${esc(UI.translate("View technical details"))}</small><h2 id="task-detail-title">${esc(UI.translate(contextLabel))}</h2></div><button class="btn small" id="task-detail-close" type="button">${esc(UI.translate("Close"))}</button></div>
      <div class="task-detail-body">${factsHtml}${output}
        <details class="task-detail-raw"><summary>${esc(UI.translate("Raw data"))}</summary><pre class="mono" tabindex="0">${esc(JSON.stringify(payload, null, 2))}</pre></details>
      </div>
    </div>`;
    mountModal(overlay);
    UI.localize(overlay);
    overlay.addEventListener("click", (click) => { if (click.target === overlay) closeModal(); });
    overlay.querySelector("#task-detail-close").addEventListener("click", closeModal);
  }

  function bindChatDetailButtons(events) {
    const byId = new Map(events.map((event) => [event.event_id, event]));
    document.querySelectorAll("[data-chat-detail]").forEach((button) => { button.onclick = () => {
      const event = byId.get(button.dataset.chatDetail);
      if (event) showTaskEventDetail(event);
    }; });
  }

  function taskChatItem(event, latestCreate, typingIds) {
    const type = event.event_type;
    const isUser = type === "task_created" || type === "user_message";
    const isAgent = ["agent_started", "agent_completed", "agent_failed", "role_returned"].includes(type);
    const isPm = type === "task_completed";
    const role = isUser ? "You" : isPm ? "PM" : event.role || "ccc-mcp";
    const label = isUser ? (type === "task_created" ? "Your request" : "Your correction") :
      isAgent || isPm ? TASK_ROLE_LABELS[role] || "Work complete" :
      type === "host_test_completed" ? "Host tests" :
      type === "pause_requested" ? "Pause requested" :
      type === "task_paused" ? "Task paused" :
      type === "resume_requested" ? "Work resumed" :
      type === "cancel_requested" ? "Stop requested" :
      type === "task_cancelled" ? "Task stopped" : "System update";
    let summary = event.summary || "";
    if (type === "agent_started") summary = "Execution dispatched; waiting for the result.";
    if (type === "task_completed") summary = event.legacy ?
      "This work round completed. Restart the UI server to view the saved test evidence." :
      (activeTouchesCode ? "This work round completed. Review the host test evidence below before accepting the result." : "This work round completed. Open the Results tab to read what each step produced.");
    if (type === "task_cancelled") summary = "No further work will be dispatched. Existing file changes were not rolled back.";
    if (type === "pause_requested") summary = "Further agent dispatch is paused. An active operation may finish at a safe point.";
    if (type === "task_paused") summary = "No task operation is running. Work will stay paused until you continue it.";
    if (type === "resume_requested") summary = "Dispatch is allowed again. Continue from the saved handoff in ChatGPT if it needs a PM decision.";
    if (type === "cancel_requested") summary = "Stop request saved. An active operation may still be settling; no next agent will start." +
      (event.summary ? `\n${UI.translate("Reason:")} ${event.summary}` : "");
    if (type === "task_created" && event.event_id !== latestCreate) summary = "A work round was started.";
    if (type === "host_test_completed") {
      summary = event.detail?.executed_by_host ?
        (event.detail.no_tests_collected ? "The host ran the test command but collected no tests, so nothing was verified." :
          event.detail.passed ? "The host ran the test command and it passed." : "The host ran the test command and it failed.") :
        `${UI.translate("Host verification is not complete:")} ${event.detail?.reason || UI.translate("no test was run")}`;
    }
    if (type === "agent_failed" && !summary) summary = "This execution ended without a verified result.";
    const details = type !== "role_returned" && (event.detail || event.evidence_refs?.length || event.context_version) ? `<button class="chat-detail-trigger" type="button" data-chat-detail="${esc(event.event_id)}">${esc(UI.translate("View technical details"))} <span aria-hidden="true">↗</span></button>` : "";
    const date = event.timestamp ? new Date(event.timestamp).toLocaleString(UI.getLocale()) : "";
    if (type === "host_test_completed") {
      const state = !event.detail?.executed_by_host || event.detail.no_tests_collected ? "pending" : event.detail.passed ? "pass" : "fail";
      const icons = { pass: "M20 6 9 17l-5-5", fail: "M18 6 6 18M6 6l12 12", pending: "M5 12h14" };
      return `<div class="chat-evidence is-${state}" data-event-id="${esc(event.event_id)}">
        <span class="chat-evidence-icon" aria-hidden="true"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="${icons[state]}"/></svg></span>
        <div class="chat-evidence-text"><strong>${esc(UI.translate(label))}</strong><span>${esc(UI.translate(summary))}</span></div>
        <time datetime="${esc(event.timestamp || "")}">${esc(date)}</time>
        <button class="chat-evidence-open" type="button" data-chat-detail="${esc(event.event_id)}" aria-label="${esc(UI.translate("View technical details"))}">${esc(UI.translate("Details"))}</button>
      </div>`;
    }
    if (!isUser && !isAgent && !isPm) {
      return `<div class="chat-system-entry ${type === "task_cancelled" || event.detail?.passed === false ? "is-negative" : ""}" data-event-id="${esc(event.event_id)}"><div class="chat-notice"><span class="chat-notice-dot" aria-hidden="true"></span><span><strong>${esc(UI.translate(label))}</strong> · ${esc(UI.translate(summary || "System update"))}</span><time datetime="${esc(event.timestamp || "")}">${esc(date)}</time></div>${details}</div>`;
    }
    const speaker = isUser ? "USER" : role;
    const avatar = `<div class="chat-avatar av-${roleAvatarTone(speaker)}" aria-hidden="true">${roleAvatarIcon(speaker)}</div>`;
    const speakerName = isUser ? UI.translate("You") : roleName(role);
    if (type === "agent_started" && typingIds && typingIds.has(event.execution_id)) {
      return `<article class="chat-post is-typing" data-event-id="${esc(event.event_id)}">
        ${avatar}
        <div class="chat-post-body"><div class="chat-post-meta"><strong data-no-i18n>${esc(speakerName)}</strong><span>${esc(UI.translate(label))}</span><span class="typing-label">${esc(UI.translate("is typing…"))}</span></div>
        <div class="chat-bubble typing-bubble" role="status" aria-label="${esc(speakerName)} ${esc(UI.translate("is typing…"))}"><span class="typing-dots" aria-hidden="true"><i></i><i></i><i></i></span></div></div>
      </article>`;
    }
    return `<article class="chat-post ${isUser ? "is-user" : ""} ${isPm ? "is-pm" : ""} ${type === "agent_failed" ? "has-error" : ""}" data-event-id="${esc(event.event_id)}">
      ${avatar}
      <div class="chat-post-body"><div class="chat-post-meta"><strong data-no-i18n>${esc(speakerName)}</strong><span>${esc(UI.translate(label))}</span><time datetime="${esc(event.timestamp || "")}">${esc(date)}</time></div>
      <div class="chat-bubble"><p>${esc(UI.translate(summary || "No summary was saved for this execution."))}</p>${details}</div></div>
    </article>`;
  }

  function taskConversation(events, typingIds) {
    const latestCreate = [...events].reverse().find((event) => event.event_type === "task_created")?.event_id;
    const ended = new Set(events.filter((event) => ["agent_completed", "agent_failed"].includes(event.event_type)).map((event) => event.execution_id));
    const rounds = [];
    for (const event of events) {
      if (event.event_type === "agent_started" && ended.has(event.execution_id)) continue;
      if (event.event_type === "task_created" || !rounds.length) rounds.push([]);
      rounds.at(-1).push(event);
    }
    if (!rounds.length) return `<div class="empty">${esc(UI.translate("No task events yet."))}</div>`;
    const older = rounds.slice(0, -1);
    const history = older.length ? `<details class="chat-history" data-key="history"><summary>${esc(UI.translate("Earlier work rounds"))} <span>${older.length}</span></summary>${older.map((round, index) => `<div class="chat-round-divider">${esc(UI.translate("Work round"))} ${index + 1}</div>${round.map((event) => taskChatItem(event, latestCreate, typingIds)).join("")}`).join("")}</details>` : "";
    return `${history}<div class="chat-round-divider" data-key="latest-round">${esc(UI.translate("Latest work round"))}</div>${rounds.at(-1).map((event) => taskChatItem(event, latestCreate, typingIds)).join("")}`;
  }

  async function fetchTaskView(id, taskId, taskBase) {
    const responses = await Promise.allSettled([
      api(taskBase), api(`${taskBase}/timeline`), api(`${taskBase}/evaluations`),
      api(`${taskBase}/artifacts`), api(`/projects/${encodeURIComponent(id)}/settings`), api(`${taskBase}/handoff`),
    ]);
    if (responses[0].status === "rejected") return { error: responses[0].reason };
    const missing = responses.slice(1).filter((result) => result.status === "rejected" && result.reason.status === 404);
    if (missing.length) {
      let legacyExecutions = [];
      try { legacyExecutions = await api(`${taskBase}/result`); } catch (_) { /* Keep the task visible even when the old result endpoint is unavailable. */ }
      return { mismatch: { status: responses[0].value, paths: missing.map((result) => result.reason.path), legacyExecutions } };
    }
    const failed = responses.find((result) => result.status === "rejected");
    if (failed) return { error: new Error(`${failed.reason.path || "Task request"}: ${failed.reason.message}`) };
    const [status, timeline, evaluations, artifacts, settings, handoff] = responses.map((result) => result.value);
    return { data: { status, timeline, evaluations, artifacts, settings, handoff } };
  }

  // What counts as "something changed" for the live conversation. Fields that tick every
  // second while an agent runs (elapsed time, the waiting note) are left out on purpose, or
  // the page would rebuild itself on every poll.
  function taskSignature(status, timeline) {
    return JSON.stringify({
      state: status.state,
      control: status.brief?.control || null,
      executions: (status.executions || []).map((e) => [e.id, e.status]),
      events: (timeline.events || []).map((e) => e.event_id),
    });
  }

  // Everything the task page shows, as HTML strings, so a first paint and a later in-place
  // update share one source of truth.
  function buildTaskParts(id, taskId, data) {
    const { status, timeline, evaluations, artifacts, settings, handoff } = data;
    activeRoleNames = settings?.role_names || settings?.editable?.role_names || {};
    activeRoleAvatars = settings?.role_avatars || settings?.editable?.role_avatars || {};
    const brief = status.brief || {};
    const control = brief.control || { status: "ACTIVE" };
    const terminal = ["COMPLETED", "CANCELLED", "REJECTED"].includes(status.state);
    const displayState = control.status !== "ACTIVE" ? control.status : status.state;
    const stateTone = ["FAILED", "BLOCKED", "CANCELLED", "STOPPING"].includes(displayState) ? "negative" :
      ["COMPLETED"].includes(displayState) ? "positive" : "warning";
    const events = timeline.events || [];
    const typingIds = new Set((status.executions || []).filter((e) => ["RUNNING", "QUEUED"].includes(e.status)).map((e) => e.id));
    const doneRoles = new Set(events.filter((e) => e.event_type === "agent_completed").map((e) => e.role));
    const workflow = brief.workflow || {};
    const flowSteps = Array.isArray(workflow.steps) ? workflow.steps : null;
    const flowDone = (index) => status.state === "COMPLETED" || (workflow.current_step != null &&
      (index < workflow.current_step || (index === workflow.current_step && workflow.current_step_done)));
    const steps = flowSteps ? flowSteps.map((role, index) => [roleName(role), flowDone(index)]) : [
      ["Analysis", doneRoles.has("SA")],
      ["Development", ["SD", "RD", "SD/RD"].some((r) => doneRoles.has(r))],
      ["Host tests", events.some((e) => e.event_type === "host_test_completed" && e.detail?.executed_by_host)],
      ["Review", doneRoles.has("REVIEW")],
    ];
    const gateDecision = workflow.pending_decision && !terminal ? workflow.pending_decision : null;
    const gateCard = gateDecision ? `<div class="task-next gate-decision"><strong>${esc(UI.translate("Gate decision needed"))}</strong>
      <p>${esc(roleName(gateDecision.role))} · ${esc(gateDecision.reason)}</p>
      ${gateDecision.findings.length ? `<ul class="gate-findings">${gateDecision.findings.map((f) => `<li><span class="pill ${f.severity === "blocker" ? "negative" : "warning"}">${esc(f.severity)}</span> ${esc(f.claim)}${f.evidence ? ` <small>${esc(f.evidence)}</small>` : ""}</li>`).join("")}</ul>` : ""}
      <div class="gate-decision-actions">
        <button class="btn small primary" type="button" data-gate-decision="proceed">${esc(UI.translate("Accept and continue"))}</button>
        <button class="btn small" type="button" data-gate-decision="retry">${esc(UI.translate("Send back once more"))}</button>
        <button class="btn small danger" type="button" data-gate-decision="stop">${esc(UI.translate("Stop task"))}</button>
      </div></div>` : "";
    const canMessage = !terminal && !control.stop_requested;
    const pauseButton = canMessage ?
      `<button class="btn small" id="task-pause-resume">${esc(UI.translate(control.paused ? "Continue work" : "Pause work"))}</button>` : "";
    const canCompose = flowSteps ? !["CANCELLED", "REJECTED"].includes(status.state) && !control.stop_requested : canMessage;
    const restartDefault = workflow.current_step != null ? workflow.current_step : (flowSteps ? flowSteps.length - 1 : 0);
    const stopButton = canMessage ? `<button class="btn small danger" id="task-stop">${esc(UI.translate("Stop task"))}</button>` : "";
    const deleteButton = REMOVABLE_STATES.has(status.state) ? `<button class="btn small danger" id="task-delete">${esc(UI.translate("Delete task"))}</button>` : "";
    const diffRows = artifacts.filter((a) => a.path.endsWith("/CHANGES.diff"));
    const testRows = artifacts.filter((a) => /\/executions\/TEST-[^/]+\/TEST-RESULT\.md$/.test(a.path));
    const reportRows = artifacts.filter((a) => /\/(SA-REPORT|SD-REPORT|SW-REPORT|SPEC|SR-REPORT|AR-REPORT|SEC-REPORT|QA-REPORT|IMPLEMENTATION|REVIEW|CONTEXT-UPDATES)\.md$/.test(a.path));
    // Documents the steps handed over (the specification, or an everyday team's research, plan,
    // draft...), newest first; the code cards below only matter when something changes code.
    const docRole = (path) => { const m = path.match(/\/(SPEC|([A-Z]{2})-DOCUMENT)\.md$/); return m ? (m[1] === "SPEC" ? "SW" : m[2]) : null; };
    const deliverables = artifacts.filter((a) => docRole(a.path)).sort((a, b) => String(b.created).localeCompare(String(a.created)));
    const touchesCode = !flowSteps || flowSteps.some((role) => role === "RD" || role === "SD/RD");
    activeTouchesCode = touchesCode;
    const deliverableCard = deliverables.length ? `<section class="task-result-card spec-card"><h2>${esc(UI.translate(touchesCode ? "Documents from each step" : "Results"))}</h2><p class="muted">${esc(UI.translate("Newest first. Each step hands its document to the next."))}</p>${deliverables.map((a, index) => `<details ${index === 0 ? "open" : ""}><summary><strong data-no-i18n>${esc(roleName(docRole(a.path)))}</strong> · ${esc(UI.translate(TASK_ROLE_LABELS[docRole(a.path)] || "Result"))}</summary><pre class="mono task-spec">${esc(a.content || "")}</pre></details>`).join("")}</section>` : "";
    const teamCard = (() => {
      if (!workflow.stats) return "";
      const map = workflow.stats.roles || {};
      const full = teamStats(flowSteps || ["SA", "SD/RD", "REVIEW"], map);
      const finished = flowSteps ? flowSteps.filter((_, index) => flowDone(index)) : ["SA", "SD/RD", "REVIEW"].filter((role) => (role === "SD/RD" ? ["SD", "RD", "SD/RD"].some((r) => doneRoles.has(r)) : doneRoles.has(role)));
      const now = teamStats(finished, map);
      const complete = finished.length >= (flowSteps ? flowSteps.length : 3);
      return `<section class="task-result-card team-card"><h2>${esc(UI.translate("Team abilities"))}</h2><p class="muted">${esc(UI.translate(complete ? "Every step is done: this is what the whole team brought." : "Grows as each step finishes. The dashed outline is the whole team."))}</p>${teamPanel(complete ? full : now, complete ? null : full.team)}</section>`;
    })();
    const latestDiff = diffRows.at(-1);
    const latestTest = testRows.at(-1);
    return {
      events, control, canMessage: canCompose, restartable: Boolean(flowSteps), terminal, typing: typingIds.size > 0,
      signature: taskSignature(status, timeline),
      pill: { text: UI.translate(displayState), tone: stateTone },
      actions: `${pauseButton}${stopButton}${deleteButton}`,
      steps: steps.map(([, done]) => done),
      nextText: handoff.next_action && !terminal ? handoff.next_action + (gateDecision ? `|${gateDecision.escalation_id}` : "") : "",
      hasGateDecision: Boolean(gateDecision),
      workflowRoles: flowSteps ? [...new Set(flowSteps)] : null,
      head: `<div><a class="task-back" href="#/p/${encodeURIComponent(id)}/tasks">← ${esc(UI.translate("Back to tasks"))}</a><h1>${esc(UI.translate("Task discussion"))}</h1><div class="task-identifiers"><span>${esc(id)}</span><span class="mono">${esc(taskId)}</span><span class="pill ${stateTone}">${esc(UI.translate(displayState))}</span></div></div><div class="task-head-actions">${pauseButton}${stopButton}${deleteButton}</div>`,
      progress: steps.map(([name, done]) => `<span class="conversation-step ${done ? "done" : ""}"><i aria-hidden="true"></i>${esc(UI.translate(name))}</span>`).join(""),
      feed: taskConversation(events, typingIds),
      paused: Boolean(control.paused && canMessage),
      restartPicker: flowSteps ? `<fieldset class="restart-picker" ${canCompose ? "" : "disabled"}><legend>${esc(UI.translate("Who should redo it?"))}</legend><div class="restart-chips">${flowSteps.map((role, index) => `<label class="restart-chip"><input type="radio" name="restart-step" value="${index}" data-role="${esc(role)}" ${index === restartDefault ? "checked" : ""}><span class="restart-chip-body"><span class="chat-avatar av-${roleAvatarTone(role)}" aria-hidden="true">${roleAvatarIcon(role).replace('width="18" height="18"', 'width="16" height="16"')}</span><span class="restart-chip-name"><i>${index + 1}</i><span data-no-i18n>${esc(roleName(role))}</span></span></span></label>`).join("")}</div></fieldset>` : "",
      next: gateCard || (handoff.next_action && !terminal ? `<div class="task-next ${control.paused ? "paused" : ""}"><strong>${esc(UI.translate(control.paused ? "Paused: waiting for you" : "Next step"))}</strong><p>${esc(handoff.next_action)}</p>${control.paused && canMessage ? `<button class="btn small primary" type="button" id="task-resume-inline">${esc(UI.translate("Continue work"))}</button>` : ""}</div>` : ""),
      results: `${teamCard}${deliverableCard}${touchesCode ? `<section class="task-result-grid">
      <div class="task-result-card"><h2>${esc(UI.translate("Changed files and diff"))}</h2>${latestDiff ? `<pre class="mono task-diff">${esc(latestDiff.content || "")}</pre>` : `<p class="muted">${esc(UI.translate("No saved diff yet."))}</p>`}</div>
      <div class="task-result-card"><h2>${esc(UI.translate("Host test evidence"))}</h2>${verificationLine(brief.tests)}${latestTest ? `<details><summary>${esc(UI.translate("View saved test result"))}</summary><pre class="mono">${esc(latestTest.content)}</pre></details>` : `<p class="muted">${esc(UI.translate("No host test evidence yet."))}</p>`}</div>
    </section><section class="task-result-card evaluation-section"><div class="evaluation-section-head"><div><h2>${esc(UI.translate("Evaluation and evidence"))}</h2><p>${esc(UI.translate("Scores are separate from task completion."))}</p></div>${settings.editable?.evaluation_engine?.enabled ? `<button class="btn small" id="task-evaluate">${esc(UI.translate("Evaluate latest executions"))}</button>` : `<button class="btn small" id="task-enable-evaluation">${esc(UI.translate("Enable evaluation"))}</button>`}</div>${evaluations.length ? `<div class="evaluation-list">${evaluations.slice().reverse().map(evaluationReport).join("")}</div>` : `<p class="muted">${esc(UI.translate("No evaluations for this task yet."))}</p>`}</section>` : ""}`,
      technical: `<div class="task-technical"><section class="task-result-card"><h2>${esc(UI.translate("Execution history"))}</h2><div class="task-table-scroll"><table><thead><tr><th>Execution</th><th>Role</th><th>Status</th><th>Error</th></tr></thead><tbody>${(status.executions || []).map((e) => `<tr><td class="mono">${esc(e.id)}</td><td>${esc(e.role)}</td><td>${esc(UI.translate(e.status))}</td><td>${esc(e.error || "")}</td></tr>`).join("")}</tbody></table></div></section>
      <section class="task-result-card"><h2>${esc(UI.translate("Original reports"))}</h2>${reportRows.length ? reportRows.map((a) => `<details><summary>${esc(a.path.split("/").at(-1))} · ${esc(a.created)}</summary><pre class="mono">${esc(a.content)}</pre></details>`).join("") : `<p class="muted">${esc(UI.translate("No original reports yet."))}</p>`}</section>
      <section class="task-result-card"><h2>${esc(UI.translate("Context and handoff"))}</h2><details><summary>${esc(UI.translate("View current handoff"))}</summary><pre class="mono">${esc(JSON.stringify(handoff, null, 2))}</pre></details><details><summary>${esc(UI.translate("View task status and events"))}</summary><pre class="mono">${esc(JSON.stringify(status, null, 2))}</pre></details><button class="btn small" id="task-load-context">${esc(UI.translate("Load project context"))}</button><div id="task-context-output"></div></section>
      <section class="task-result-card"><h2>${esc(UI.translate("Usage and raw executions"))}</h2><button class="btn small" id="task-load-raw">${esc(UI.translate("Load original records"))}</button><div id="task-raw-output"></div></section></div>`,
    };
  }

  // Bring the conversation up to date without rebuilding it. Existing messages stay put,
  // new ones are inserted where they belong, and a finished "typing" bubble hands its exact
  // slot to the message that replaces it (like a chat app), so nothing blinks or jumps.
  function patchFeed(feed, html) {
    const template = document.createElement("template");
    template.innerHTML = html;
    const incoming = [...template.content.children];
    incoming.forEach((el) => UI.localize(el));
    const keyOf = (el) => el.dataset.key || el.dataset.eventId || "";
    const live = [...feed.children];
    if (incoming.some((el) => !keyOf(el)) || live.some((el) => !keyOf(el))) {
      feed.innerHTML = html;
      UI.localize(feed);
      return true;
    }
    const liveByKey = new Map(live.map((el) => [keyOf(el), el]));
    const incomingKeys = new Set(incoming.map(keyOf));
    const strip = (el) => el.outerHTML.replace(/ is-(entering|settling)/g, "");
    let slotTaken = false;
    const finalOrder = incoming.map((node) => {
      const existing = liveByKey.get(keyOf(node));
      if (existing) return strip(existing) === strip(node) ? existing : node;
      const taken = !slotTaken && node.classList.contains("chat-post") && !node.classList.contains("is-typing")
        && live.some((el) => el.classList.contains("is-typing") && !incomingKeys.has(keyOf(el)));
      if (taken) slotTaken = true;
      enter(node, taken ? "is-settling" : "is-entering");
      return node;
    });
    return applyOrder(feed, finalOrder);
  }

  // Make `container`'s children exactly `finalOrder`, touching only what differs: nodes that
  // are already in place are left alone, the rest are inserted or removed.
  function applyOrder(container, finalOrder) {
    let changed = false;
    const keep = new Set(finalOrder);
    // Drop what disappeared first, otherwise it would sit in the way and make every node
    // after it look "moved" (and re-played its entrance animation).
    [...container.children].forEach((el) => { if (!keep.has(el)) { el.remove(); changed = true; } });
    let ref = container.firstElementChild;
    for (const el of finalOrder) {
      if (el === ref) { ref = ref.nextElementSibling; continue; }
      container.insertBefore(el, ref);
      changed = true;
    }
    return changed;
  }

  // Play an entrance animation once; the class is removed afterwards so a later move of the
  // same node cannot replay it.
  function enter(node, className) {
    node.classList.add(className);
    node.addEventListener("animationend", () => node.classList.remove(className), { once: true });
  }

  async function renderTaskDetail() {
    const gen = renderGen;
    const route = parseRoute();
    const id = route.project, taskId = route.sub;
    if (taskDetailRoute !== `${id}/${taskId}`) {
      taskDetailRoute = `${id}/${taskId}`;
      taskDetailPane = "conversation";
      taskDetailDraft = "";
    }
    const oldInput = document.getElementById("task-message");
    const oldFeed = document.querySelector(".conversation-feed");
    const oldFeedScroll = oldFeed?.scrollTop || 0;
    const wasAtBottom = !oldFeed || oldFeed.scrollHeight - oldFeed.scrollTop - oldFeed.clientHeight < 120;
    if (oldInput) taskDetailDraft = oldInput.value;
    const taskBase = `/projects/${encodeURIComponent(id)}/tasks/${encodeURIComponent(taskId)}`;
    layout(loadingView());
    const loaded = await fetchTaskView(id, taskId, taskBase);
    if (stale(gen)) return;
    if (loaded.error) { errorView(loaded.error); return; }
    if (loaded.mismatch) {
      renderTaskVersionMismatch(loaded.mismatch.status, id, taskId, loaded.mismatch.paths, loaded.mismatch.legacyExecutions);
      return;
    }
    let data = loaded.data;
    let parts = buildTaskParts(id, taskId, data);
    const conversationActive = taskDetailPane === "conversation";
    layout(`<div class="task-workspace">
      <header class="task-workspace-head">${parts.head}</header>
      <div class="conversation-progress" aria-label="${esc(UI.translate("Task progress"))}">${parts.progress}</div>
      <nav class="tabs task-main-tabs" role="tablist" aria-label="${esc(UI.translate("Task views"))}">
        <button class="tab ${taskDetailPane === "conversation" ? "active" : ""}" data-task-pane="conversation" role="tab" aria-selected="${taskDetailPane === "conversation"}">${esc(UI.translate("Work conversation"))}</button>
        <button class="tab ${taskDetailPane === "results" ? "active" : ""}" data-task-pane="results" role="tab" aria-selected="${taskDetailPane === "results"}">${esc(UI.translate("Results"))}</button>
        <button class="tab ${taskDetailPane === "technical" ? "active" : ""}" data-task-pane="technical" role="tab" aria-selected="${taskDetailPane === "technical"}">${esc(UI.translate("Technical record"))}</button>
      </nav>
      <div class="task-pane" data-pane="conversation" ${conversationActive ? "" : "hidden"}><div class="task-conversation-room">
        <div class="chat-room-bar"><div class="chat-room-title"><span class="chat-room-mark" aria-hidden="true">#</span><div><strong>${esc(UI.translate("Task meeting room"))}</strong><small>${esc(taskId)} · ${esc(UI.translate("Saved work conversation"))}</small></div></div><div class="chat-participants" aria-label="${esc(UI.translate("Meeting participants"))}">${chatParticipants(parts.workflowRoles)}</div></div>
        <div class="conversation-feed" role="log" aria-live="polite" aria-label="${esc(UI.translate("Work conversation"))}">${parts.feed}</div>
        <div class="task-next-slot">${parts.next}</div>
        <form class="task-composer" id="task-message-form"><label for="task-message">${esc(UI.translate("Correction or instruction"))}</label><textarea id="task-message" rows="2" maxlength="8000" placeholder="${esc(UI.translate(parts.restartable ? "Tell that role what should change…" : "Tell the PM what should change…"))}" ${parts.canMessage ? "" : "disabled"}></textarea>${parts.restartable ? parts.restartPicker : ""}<div class="task-composer-foot">${parts.restartable ? "" : `<label class="task-check"><input type="checkbox" id="task-message-pause" checked ${parts.canMessage ? "" : "disabled"}> ${esc(UI.translate("Pause before the next agent"))}</label>`}<button class="btn primary" type="submit" id="task-message-send" ${parts.canMessage ? "" : "disabled"}>${parts.restartable ? "" : esc(UI.translate("Send correction"))}</button></div><p class="muted">${esc(UI.translate(parts.restartable ? "That role redoes its work with your note, and every step after it runs again." : "This records guidance for the workflow. It does not start a separate AI chat."))}</p></form>
      </div>
      </div>
      <div class="task-pane" data-pane="results" ${taskDetailPane !== "results" ? "hidden" : ""}>${parts.results}</div>
      <div class="task-pane" data-pane="technical" ${taskDetailPane !== "technical" ? "hidden" : ""}>${parts.technical}</div>
    </div>`);
    const messageInput = document.getElementById("task-message");
    if (messageInput) messageInput.value = taskDetailDraft;
    const feed = document.querySelector(".conversation-feed");
    if (feed) feed.scrollTop = wasAtBottom ? feed.scrollHeight : oldFeedScroll;

    // Handlers on parts that are rebuilt in place use `onclick`, so binding again replaces
    // the old handler instead of stacking a second one.
    const bindDynamic = () => {
      const { control } = parts;
      bindChatDetailButtons(parts.events);
      const pauseResumeClick = async (event) => {
        event.currentTarget.disabled = true;
        try { const result = await api(`${taskBase}/${control.paused ? "resume" : "pause"}`, { method: "POST" });
          if (control.paused && result.next_action) toast("Work can continue from the saved handoff.");
          else toast(control.paused ? "Work resumed." : "Pause requested.");
          refresh();
        } catch (err) { toast(err.message, true); event.currentTarget.disabled = false; }
      };
      ["task-pause-resume", "task-resume-inline"].forEach((buttonId) => {
        const button = document.getElementById(buttonId);
        if (button) button.onclick = pauseResumeClick;
      });
      const stopTask = document.getElementById("task-stop");
      if (stopTask) stopTask.onclick = () => showModal({
        title: "Stop this task?", body: "Stopping prevents further agent work. Changes already made to files will not be rolled back.",
        fields: [{ label: "Reason (optional)", placeholder: "Why are you stopping this task?" }], danger: true,
        confirmLabel: "Stop task", onSubmit: async ([reason]) => { await api(`${taskBase}/stop`, { method: "POST", body: { reason } }); toast("Stop requested."); refresh(); },
      });
      document.querySelectorAll("[data-gate-decision]").forEach((button) => { button.onclick = () => {
        const decision = button.dataset.gateDecision;
        const copy = {
          proceed: ["Accept this work and continue?", "The reviewer's findings are overridden and the next step runs."],
          retry: ["Send this back once more?", "The step that made this work redoes it using the findings, then the gate runs again."],
          stop: ["Stop this task?", "Stopping prevents further agent work. Changes already made to files will not be rolled back."],
        }[decision];
        showModal({
          title: copy[0], body: copy[1], danger: decision === "stop",
          fields: [{ label: "Note (optional)", placeholder: "Anything the next step should know" }],
          confirmLabel: decision === "proceed" ? "Accept and continue" : decision === "retry" ? "Send back once more" : "Stop task",
          onSubmit: async ([note]) => { await api(`${taskBase}/gate-decision`, { method: "POST", body: { decision, note } }); toast("Decision sent."); refresh(); },
        });
      }; });
      const deleteTask = document.getElementById("task-delete");
      if (deleteTask) deleteTask.onclick = () => confirmDeleteTask(id, taskId, () => navTo(`/p/${encodeURIComponent(id)}/tasks`));
      const enableEvaluation = document.getElementById("task-enable-evaluation");
      if (enableEvaluation) enableEvaluation.onclick = async () => {
        try { await api(`/projects/${encodeURIComponent(id)}/config/evaluation-engine`, { method: "POST", body: { enabled: true, auto_evaluate: data.settings.editable?.evaluation_engine?.auto_evaluate || false } }); toast("Evaluation enabled."); refresh(); }
        catch (err) { toast(err.message, true); }
      };
      const evaluate = document.getElementById("task-evaluate");
      if (evaluate) evaluate.onclick = async () => {
        try { await api(`${taskBase}/evaluate`, { method: "POST", body: {} }); toast("Evaluations saved."); refresh(); }
        catch (err) { toast(err.message, true); }
      };
      document.querySelectorAll("[data-reevaluate]").forEach((button) => { button.onclick = async () => {
        try { await api(`${taskBase}/reevaluate`, { method: "POST", body: { execution_id: button.dataset.reevaluate } }); toast("New evaluation version saved."); refresh(); }
        catch (err) { toast(err.message, true); }
      }; });
      const loadContext = document.getElementById("task-load-context");
      if (loadContext) loadContext.onclick = async () => {
        try { const result = await api(`/projects/${encodeURIComponent(id)}/context`); document.getElementById("task-context-output").innerHTML = `<pre class="mono">${esc(JSON.stringify(result, null, 2))}</pre>`; }
        catch (err) { toast(err.message, true); }
      };
      const loadRaw = document.getElementById("task-load-raw");
      if (loadRaw) loadRaw.onclick = async () => {
        try { const [usage, result] = await Promise.all([api(`${taskBase}/usage`), api(`${taskBase}/result`)]); document.getElementById("task-raw-output").innerHTML = `<pre class="mono">${esc(JSON.stringify({ usage, result }, null, 2))}</pre>`; }
        catch (err) { toast(err.message, true); }
      };
    };

    // Update the page in place: only what actually changed is touched.
    const patch = (next) => {
      const root = document.querySelector(".task-workspace");
      const room = root?.querySelector(".conversation-feed");
      if (!root || !room) return;
      const stick = room.scrollHeight - room.scrollTop - room.clientHeight < 120;
      const pill = root.querySelector(".task-identifiers .pill");
      if (pill && (pill.textContent !== next.pill.text || !pill.classList.contains(next.pill.tone))) {
        pill.className = `pill ${next.pill.tone}`;
        pill.textContent = next.pill.text;
      }
      if (parts.actions !== next.actions) {
        const actions = root.querySelector(".task-head-actions");
        if (actions) { actions.innerHTML = next.actions; UI.localize(actions); }
      }
      root.querySelectorAll(".conversation-step").forEach((el, index) => el.classList.toggle("done", Boolean(next.steps[index])));
      const slot = root.querySelector(".task-next-slot");
      const nextBody = slot?.querySelector(".task-next p");
      if (slot && (parts.nextText !== next.nextText || parts.paused !== next.paused)) {
        if (nextBody && next.nextText && !next.hasGateDecision && !parts.hasGateDecision && parts.paused === next.paused) nextBody.textContent = next.nextText;
        else { slot.innerHTML = next.next; UI.localize(slot); }
      }
      [['[data-pane="results"]', "results"], ['[data-pane="technical"]', "technical"]].forEach(([selector, key]) => {
        if (parts[key] === next[key]) return;
        const el = root.querySelector(selector);
        if (el) { el.innerHTML = next[key]; UI.localize(el); }
      });
      const feedChanged = parts.feed !== next.feed && patchFeed(room, next.feed);
      root.querySelector("#task-message-form")?.querySelectorAll("textarea, input, button[type=submit]").forEach((el) => { el.disabled = !next.canMessage; });
      parts = next;
      bindDynamic();
      if (feedChanged && stick) room.scrollTo({ top: room.scrollHeight, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    };
    async function refresh() {
      const result = await fetchTaskView(id, taskId, taskBase);
      if (stale(gen) || !result.data) return;
      data = result.data;
      patch(buildTaskParts(id, taskId, data));
    }

    bindDynamic();
    document.querySelectorAll("[data-task-pane]").forEach((button) => button.addEventListener("click", () => {
      taskDetailPane = button.dataset.taskPane;
      document.querySelectorAll("[data-task-pane]").forEach((tab) => { tab.classList.toggle("active", tab === button); tab.setAttribute("aria-selected", String(tab === button)); });
      document.querySelectorAll("[data-pane]").forEach((pane) => { pane.hidden = pane.dataset.pane !== taskDetailPane; });
    }));
    const sendLabel = () => {
      const button = document.getElementById("task-message-send");
      const picked = document.querySelector('input[name="restart-step"]:checked');
      if (!button || !picked) return;
      button.innerHTML = `<span>${esc(UI.translate("Ask"))}</span><strong data-no-i18n>${esc(roleName(picked.dataset.role))}</strong><span>${esc(UI.translate("to redo it"))}</span>`;
    };
    sendLabel();
    document.querySelectorAll('input[name="restart-step"]').forEach((radio) => radio.addEventListener("change", sendLabel));
    document.getElementById("task-message-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const content = messageInput.value.trim();
      if (!content) { messageInput.focus(); return; }
      const button = event.currentTarget.querySelector('button[type="submit"]'); button.disabled = true;
      try {
        if (parts.restartable) {
          const step = Number(document.querySelector('input[name="restart-step"]:checked').value);
          const result = await api(`${taskBase}/restart`, { method: "POST", body: { step, content } });
          taskDetailDraft = ""; messageInput.value = "";
          toast(`${roleName(result.role)}: ${result.greeting}`);
        } else {
          await api(`${taskBase}/messages`, { method: "POST", body: { content, pause: document.getElementById("task-message-pause").checked } });
          taskDetailDraft = ""; messageInput.value = "";
          toast(document.getElementById("task-message-pause").checked ? "Correction saved. The task is paused: press Continue work to send it to the next agent." : "Correction saved for the workflow.");
        }
        button.disabled = false; refresh();
      } catch (err) { toast(err.message, true); button.disabled = false; }
    });
    let baseline = parts.signature;
    let polling = false;
    trackTimer(setInterval(async () => {
      if (stale(gen) || polling) return;
      polling = true;
      try {
        const [freshStatus, freshTimeline] = await Promise.all([api(taskBase), api(`${taskBase}/timeline`)]);
        const serialized = taskSignature(freshStatus, freshTimeline);
        if (!stale(gen) && serialized !== baseline) { baseline = serialized; await refresh(); }
      } catch (_) { /* The next poll retries; saved events remain available after reconnect. */ }
      finally { polling = false; }
    }, 2500));
  }

  async function renderSettings() {
    const gen = renderGen;
    const id = parseRoute().project;
    layout(loadingView());
    let overview, catalog;
    try {
      [overview, catalog] = await Promise.all([
        api(`/projects/${encodeURIComponent(id)}/settings`), api(`/projects/${encodeURIComponent(id)}/workflows`),
      ]);
    }
    catch (err) { if (!stale(gen)) errorView(err); return; }
    if (stale(gen)) return;
    const roles = overview.editable.roles || {};
    const providers = Object.keys(overview.available_providers || {});
    const effortOptions = (provider, current) => {
      const list = overview.available_providers?.[provider] || [];
      return `<option value="">${esc(UI.translate("CLI default"))}</option>` +
        list.map((e) => `<option value="${esc(e)}" ${current === e ? "selected" : ""}>${esc(e)}</option>`).join("");
    };
    const ROLE_HELP = { SA: "Analysis", SD: "Design", RD: "Implementation", "SD/RD": "Design and implementation in one step (Classic workflow only)", REVIEW: "Code review (CR)", SR: "Specification review", AR: "Adversarial review", SEC: "Security review", QA: "Quality assurance", SW: "Specification writing", PA: "Understands the request and hands over the result", RS: "Looks things up and collects information", AN: "Compares options and weighs pros and cons", PL: "Turns findings into a plan with steps and costs", CS: "Gives expert advice and judges what is workable", WR: "Writes the article, proposal or report", ED: "Polishes wording and structure", VC: "Checks claims, sources and figures", RV: "Checks the result for problems and gaps" };
    activeRoleNames = overview.editable.role_names || {};
    activeRoleAvatars = overview.editable.role_avatars || {};
    const nameField = (role) => `<div class="field role-name-field"><label for="role-name-${esc(role.replace("/", "-"))}">${esc(UI.translate("Chat name"))}</label><input type="text" id="role-name-${esc(role.replace("/", "-"))}" class="role-name-input" maxlength="24" value="${esc(activeRoleNames[role] || "")}" placeholder="${esc(ROLE_FRIENDLY[role] ? UI.translate(ROLE_FRIENDLY[role]) : ROLE_ABBREVIATIONS[role] || role)}" data-no-i18n /></div>`;
    const avatarPicker = (role) => `<details class="field avatar-field avatar-details"><summary><span class="avatar-summary-label">${esc(UI.translate("Avatar"))}</span><span class="role-avatar chat-avatar av-${roleAvatarTone(role)}" aria-hidden="true">${roleAvatarIcon(role)}</span><span class="avatar-summary-action">${esc(UI.translate("Change avatar"))}</span></summary><div class="avatar-picker" role="radiogroup" aria-label="${esc(UI.translate("Avatar"))}" data-avatar-role="${esc(role)}">${AVATAR_ORDER.map((avatar) => {
      const current = activeRoleAvatars[role] || DEFAULT_AVATAR[role] || "person";
      return `<button type="button" class="avatar-option ${current === avatar ? "selected" : ""}" role="radio" aria-checked="${current === avatar}" data-avatar="${avatar}" title="${esc(UI.translate(avatar))}"><span class="chat-avatar av-${roleAvatarTone(role)}" aria-hidden="true">${spriteFor(avatar)}</span></button>`;
    }).join("")}</div></details>`;
    roleStatsMap = catalog.role_stats || {};
    const roleHead = (role, help, pill) => `<div class="role-config-head">
          <span class="role-avatar chat-avatar av-${roleAvatarTone(role)}" aria-hidden="true">${roleAvatarIcon(role)}</span>
          <div class="role-config-title"><strong data-no-i18n>${esc(ROLE_FRIENDLY[role] ? UI.translate(ROLE_FRIENDLY[role]) : ROLE_ABBREVIATIONS[role] || role)}${ROLE_FRIENDLY[role] ? ` <span class="role-abbr">${esc(ROLE_ABBREVIATIONS[role] || role)}</span>` : ""}</strong><small>${esc(UI.translate(help))}</small>${roleStatsMap[role] ? `<small class="strength-line">${strengthLine(roleStatsMap[role], { strong: 4, weak: 0 })}</small>` : ""}</div>
          ${roleStatsMap[role] ? `<span class="role-radar" title="${esc(statSummary(roleStatsMap[role]))}">${radarSvg(roleStatsMap[role], { size: 124, labels: "short" })}</span>` : ""}
          ${pill}
        </div>`;
    const pmRow = `<div class="role-config-row" data-role="PM" data-name-only>
        ${roleHead("PM", "Project manager: the ChatGPT conversation that drives the workflow", "")}
        <div class="role-config-fields">${nameField("PM")}${avatarPicker("PM")}</div>
      </div>`;
    // The combined SD/RD role only exists for the Classic workflow; it is listed last and
    // hidden whenever no workflow in use needs it.
    const ROLE_LIST_ORDER = ["SA", "SW", "SD", "RD", "REVIEW", "SR", "AR", "SEC", "QA", "SD/RD"];
    const needsCombinedRole = () => catalog.workflows.some((flow) => (flow.id === catalog.selected || !flow.builtin) && flow.steps.some((step) => step.role === "SD/RD"));
    const roleRow = (role) => {
      const cfg = roles[role] || {};
      const isChatgpt = cfg.provider === "chatgpt";
      return `<div class="role-config-row" data-role="${esc(role)}" ${role === "SD/RD" ? `data-legacy-role ${needsCombinedRole() ? "" : "hidden"}` : ""}>
        ${roleHead(role, ROLE_HELP[role] || "Workflow role", `<span class="pill role-provider-pill" data-no-i18n>${esc(cfg.provider || "")}</span>`)}
        <div class="role-config-fields">
          ${nameField(role)}
          ${avatarPicker(role)}
          <div class="field"><label>${esc(UI.translate("Provider"))}</label><select class="role-field" data-field="provider">${providers.map((p) => `<option value="${esc(p)}" ${cfg.provider === p ? "selected" : ""}>${esc(p)}</option>`).join("")}</select></div>
          <div class="field"><label>${esc(UI.translate("Model"))}</label><input type="text" class="role-field" data-field="model" value="${esc(cfg.model || "")}" placeholder="${esc(UI.translate("CLI default"))}" ${isChatgpt ? "disabled" : ""} /></div>
          <div class="field"><label>${esc(UI.translate("Effort"))}</label><select class="role-field" data-field="effort" ${isChatgpt ? "disabled" : ""}>${effortOptions(cfg.provider, cfg.effort)}</select></div>
          <div class="field"><label>${esc(UI.translate("Quota fallback"))}</label><select class="role-field" data-field="quota_fallback"><option value="" ${!cfg.quota_fallback ? "selected" : ""}>${esc(UI.translate("None"))}</option>${providers.map((p) => `<option value="${esc(p)}" ${cfg.quota_fallback === p ? "selected" : ""}>${esc(p)}</option>`).join("")}</select></div>
        </div>
      </div>`;
    };
    // Roles are grouped by who they are for; a group opens when the selected workflow, or one of
    // your own, uses a role from it, so someone who only plans trips sees just their team.
    const EVERYDAY_LIST = ["PA", "RS", "AN", "PL", "CS", "WR", "ED", "VC", "RV"];
    const usedRoles = new Set(catalog.workflows.filter((flow) => flow.id === catalog.selected || !flow.builtin).flatMap((flow) => flow.steps.map((step) => step.role)));
    const roleGroup = (title, list, always) => `<details class="role-group" ${always || list.some((role) => usedRoles.has(role)) ? "open" : ""}><summary>${esc(UI.translate(title))}</summary><div class="role-config-list">${list.filter((role) => roles[role]).map(roleRow).join("")}</div></details>`;
    const roleRows = pmRow + roleGroup("Everyday team roles", EVERYDAY_LIST, false) + roleGroup("Software team roles", ROLE_LIST_ORDER, false);
    const selectedWorkflow = overview.editable.workflow || "classic";
    const reviewGate = overview.editable.review_gate || {};
    const gateSelect = (name, value, options) => `<select id="${name}">${options.map(([key, label]) => `<option value="${key}" ${value === key ? "selected" : ""}>${esc(UI.translate(label))}</option>`).join("")}</select>`;
    const mm = overview.editable.meeting_mode || {};
    const ee = overview.editable.evaluation_engine || {};
    const scoringRoles = ["SA", "SD/RD", "REVIEW"];
    const DIMENSION_HELP = {
      test: "Whether the host actually ran the tests, and whether they passed.",
      requirement: "How closely the code changes match the plan SA approved.",
      code_quality: "Issues found by static analysis (ruff) in the changed code.",
      security: "Security issues found in the changed code.",
      review: "Findings and disagreements from cross-role review.",
    };
    const scoreDimensionLegend = `<div class="score-dimension-legend"><div class="t">${esc(UI.translate("What each dimension means"))}</div>${Object.entries(DIMENSION_HELP).map(([dimension, help]) => `<div class="score-dimension-legend-row"><span class="score-weight-dot" style="background:var(--dim-${dimension})"></span><strong>${esc(UI.translate(dimension))}</strong><span>${esc(UI.translate(help))}</span></div>`).join("")}</div>`;
    const SCORE_PRESETS = {
      "SA": {
        default: { requirement: 0.70, review: 0.30 },
        review: { requirement: 0.40, review: 0.60 },
      },
      "SD/RD": {
        default: { test: 0.40, requirement: 0.25, code_quality: 0.15, security: 0.10, review: 0.10 },
        test: { test: 0.60, requirement: 0.15, code_quality: 0.10, security: 0.05, review: 0.10 },
        security: { test: 0.30, requirement: 0.15, code_quality: 0.15, security: 0.35, review: 0.05 },
      },
      "REVIEW": {
        default: { test: 0.40, requirement: 0.30, review: 0.30 },
        test: { test: 0.60, requirement: 0.20, review: 0.20 },
        review: { test: 0.25, requirement: 0.25, review: 0.50 },
      },
    };
    const SCORE_PRESET_LABELS = { default: "Default", test: "Test-focused", security: "Security-focused", review: "Review-focused" };
    const scoreWeightRows = scoringRoles.map((role) => {
      const entries = Object.entries(overview.evaluation_weights?.[role] || {});
      const pct = (weight) => Math.round(weight * 100);
      const bar = entries.map(([dimension, weight]) => `<span class="seg" style="width:${pct(weight)}%;background:var(--dim-${dimension})" title="${esc(UI.translate(dimension))} ${pct(weight)}%"></span>`).join("");
      const rows = entries.map(([dimension, weight]) => `<div class="score-weight-row"><span class="score-weight-dot" style="background:var(--dim-${dimension})"></span><span class="score-weight-label">${esc(UI.translate(dimension))}</span><input type="range" min="0" max="100" step="1" class="score-weight-slider" style="accent-color:var(--dim-${dimension})" data-score-role="${role}" data-score-dimension="${dimension}" value="${pct(weight)}" aria-label="${esc(UI.translate(dimension))} ${esc(role)}" /><output class="score-weight-value">${pct(weight)}%</output></div>`).join("");
      const presets = Object.keys(SCORE_PRESETS[role] || {}).map((presetId) => `<button type="button" class="btn small ghost" data-score-preset-role="${role}" data-score-preset="${presetId}">${esc(UI.translate(SCORE_PRESET_LABELS[presetId]))}</button>`).join("");
      return `<div class="score-weight-role"><div class="score-weight-role-head"><div class="score-weight-role-title" data-no-i18n>${esc(role)}</div><div class="score-weight-presets">${presets}</div></div><div class="score-weight-bar" data-role="${esc(role)}">${bar}</div><div class="score-weight-sliders">${rows}</div></div>`;
    }).join("");
    const runtime = overview.editable.runtime || {};
    const switchRow = (id, checked, label) => `<label class="settings-switch" for="${id}"><input type="checkbox" role="switch" id="${id}" ${checked ? "checked" : ""} /><span>${label}</span></label>`;
    const profileOption = (name, description) => `<button class="profile-option ${overview.profile === name ? "selected" : ""}" data-profile="${name}" aria-pressed="${overview.profile === name}">
        <span class="profile-option-name" data-no-i18n>${name}</span><span class="profile-option-desc">${description}</span>
        <span class="profile-option-check" aria-hidden="true">${pageIcon("check")}</span>
      </button>`;
    layout(`
      <div class="title-row"><div><h1>Settings</h1><div class="sub">${esc(id)} · ${UI.translate("profile")}: ${esc(overview.profile)}</div></div></div>
      <div class="settings-stack">
        ${pageCard("profile", "Profile", "Apply a bundled provider combination to every role at once.",
          `<div class="profile-options">
            ${profileOption("chatgpt-only", "Every role runs on ChatGPT through the connector.")}
            ${profileOption("best-model", "Codex for SA and REVIEW, Claude for implementation, when both are installed.")}
          </div>`)}
        ${pageCard("roles", "Roles", "Provider, model, effort and quota fallback for each workflow role.", `${statExplainer()}<div class="role-config-list">${roleRows}</div>`)}
        ${pageCard("workflow", "Workflow", "The steps a new task goes through. A task keeps the workflow it started with.",
          `${statExplainer()}<div id="workflow-panel"></div>`)}
        ${pageCard("gate", "Review gates", "How hard reviewers push back, and how many times work may be sent back before you decide.",
          `<div class="settings-grid">
            <div class="field"><label for="gate-ar">${esc(UI.translate("Adversarial review strictness"))}</label>${gateSelect("gate-ar", reviewGate.ar_strictness, [["normal", "Normal: only clear faults"], ["strict", "Strict: minor problems too"], ["extreme", "Extreme: challenge every assumption"]])}</div>
            <div class="field"><label for="gate-rounds">${esc(UI.translate("Max rounds per gate (1-5)"))}</label><input type="text" inputmode="numeric" id="gate-rounds" value="${esc(reviewGate.max_rounds ?? 2)}" /></div>
            <div class="field"><label for="gate-escalation">${esc(UI.translate("When a gate gives up"))}</label>${gateSelect("gate-escalation", reviewGate.escalation, [["ask_user", "Ask me"], ["auto_pm", "ChatGPT decides"]])}</div>
          </div>
          <div class="hint">${esc(UI.translate("Every adversarial finding must cite evidence, or it does not count. A gate that still finds blocking problems after its last round, or reports a fundamental flaw, waits for a decision instead of looping."))}</div>`,
          `<button class="btn small primary" id="save-review-gate">Save</button>`)}
        ${pageCard("language", "Language", "What Codex/Claude/ChatGPT receive in reports.",
          `<div class="field narrow-field"><select id="language-select" aria-label="${esc(UI.translate("Language"))}">
            ${["zh-TW", "ch", "en"].map((l) => `<option value="${l}" ${overview.editable.language === l ? "selected" : ""}>${l}</option>`).join("")}
          </select></div>`)}
        ${pageCard("test", "Test command", "After each implementation, ccc-mcp installs declared dependencies and runs tests automatically before REVIEW.",
          `<div class="field">
            <input type="text" id="test-command" class="mono" value="${esc((overview.editable.test_command || []).join(" "))}" placeholder="e.g. python -m pytest -q" aria-label="${esc(UI.translate("Test command"))}" />
            <div class="hint">Leave blank to test changed Python test files automatically. If none changed, or pytest settings changed, pytest uses the project's configured scope. Enter a command only to override this.</div>
          </div>`, `<button class="btn small primary" id="save-test-command">Save</button>`)}
        ${pageCard("runtime", "Project runtime", "Choose the language and installed runtime used for this project's code and tests.",
          `<div class="settings-grid">
            <div class="field">
              <label for="runtime-language">Programming language</label>
              <select id="runtime-language">
                <option value="" ${!runtime.language ? "selected" : ""}>Default environment</option>
                ${Object.entries(overview.available_runtimes || {}).map(([key, label]) => `<option value="${esc(key)}" ${runtime.language === key ? "selected" : ""}>${esc(label)}</option>`).join("")}
              </select>
            </div>
            <div class="field">
              <label for="runtime-version">Runtime version</label>
              <input type="text" id="runtime-version" value="${esc(runtime.version)}" placeholder="e.g. 3.9 or 22.14.0" autocomplete="off" />
              <div class="hint">3.9 accepts any 3.9.x patch. JavaScript and TypeScript use the Node.js version.</div>
            </div>
          </div>
          <div class="field">
            <label for="runtime-executable">Local executable (optional)</label>
            <input type="text" id="runtime-executable" class="mono" value="${esc(runtime.executable)}" placeholder="e.g. C:/Python39/python.exe" autocomplete="off" />
            <div class="hint">Absolute executable path or command name, without arguments. Leave empty to find an installed runtime.</div>
          </div>
          <div class="hint">Python uses a separate virtual environment per selection. Set a test command for other languages. Install the required runtime locally first.</div>
          <div id="runtime-status" class="hint settings-status" role="status" aria-live="polite"></div>`,
          `<button class="btn small" id="check-runtime">Check local version</button><button class="btn small primary" id="save-runtime">Save runtime</button>`)}
        ${pageCard("meeting", "Meeting Mode", "Bounded SD/RD↔SA clarification rounds before implementation.",
          `${switchRow("mm-enabled", mm.enabled, "Enabled")}
          <div class="field narrow-field" style="margin-top:16px">
            <label for="mm-max">Max rounds (0-5)</label>
            <input type="text" id="mm-max" value="${esc(mm.max_rounds)}" inputmode="numeric" />
          </div>`, `<button class="btn small primary" id="save-meeting-mode">Save</button>`)}
        ${pageCard("evaluation", "Evaluation Engine", "Score saved execution evidence without another model call. Open a task to evaluate, inspect, or re-evaluate it.",
          `${switchRow("ee-enabled", ee.enabled, "Enabled")}
          ${switchRow("ee-auto", ee.auto_evaluate, "Auto-evaluate after every successful role execution")}
          <details class="score-settings"><summary>${esc(UI.translate("Advanced scoring settings"))}</summary><p>${esc(UI.translate("Not sure? Pick a preset below each role. Want to fine-tune instead? Drag a slider - the others adjust automatically so each role always totals 100%. Changes only affect new evaluation versions."))}</p>${scoreDimensionLegend}${scoreWeightRows}</details>`,
          `<button class="btn small primary" id="save-evaluation-engine">Save</button>`)}
      </div>
    `, { narrow: true, contentClass: "settings-wide" });
    document.querySelectorAll(".role-config-row").forEach((row) => {
      const role = row.dataset.role;
      row.querySelectorAll(".avatar-option").forEach((button) => button.addEventListener("click", async () => {
        try {
          await api(`/projects/${encodeURIComponent(id)}/config/role-avatar`, { method: "POST", body: { role, avatar: button.dataset.avatar } });
          const next = { ...activeRoleAvatars };
          if (button.dataset.avatar === (DEFAULT_AVATAR[role] || "person")) delete next[role]; else next[role] = button.dataset.avatar;
          activeRoleAvatars = next;
          row.querySelectorAll(".avatar-option").forEach((other) => { const on = other === button; other.classList.toggle("selected", on); other.setAttribute("aria-checked", String(on)); });
          const head = row.querySelector(".role-config-head .chat-avatar");
          if (head) head.innerHTML = roleAvatarIcon(role);
          const chosen = row.querySelector(".avatar-details > summary .chat-avatar");
          if (chosen) chosen.innerHTML = roleAvatarIcon(role);
          toast("Avatar saved.");
        } catch (err) { toast(err.message, true); }
      }));
      row.querySelector(".role-name-input")?.addEventListener("change", async (event) => {
        try {
          await api(`/projects/${encodeURIComponent(id)}/config/role-name`, { method: "POST", body: { role, name: event.target.value.trim() } });
          toast("Name saved.");
        } catch (err) { toast(err.message, true); }
      });
      if (row.hasAttribute("data-name-only")) return;
      const providerSel = row.querySelector('[data-field="provider"]');
      const modelInput = row.querySelector('[data-field="model"]');
      const effortSel = row.querySelector('[data-field="effort"]');
      const fallbackSel = row.querySelector('[data-field="quota_fallback"]');
      const save = async () => {
        try {
          await api(`/projects/${encodeURIComponent(id)}/config/role`, {
            method: "POST",
            body: {
              role,
              provider: providerSel.value,
              model: modelInput.value.trim() || null,
              effort: effortSel.value || null,
              quota_fallback: fallbackSel.value || null,
            },
          });
          toast("Role updated.");
        } catch (err) { toast(err.message, true); }
      };
      providerSel.addEventListener("change", () => {
        const isChatgpt = providerSel.value === "chatgpt";
        row.querySelector(".role-provider-pill").textContent = providerSel.value;
        modelInput.disabled = isChatgpt;
        effortSel.disabled = isChatgpt;
        if (isChatgpt) modelInput.value = "";
        const list = overview.available_providers?.[providerSel.value] || [];
        const keep = list.includes(effortSel.value) ? effortSel.value : "";
        effortSel.innerHTML = `<option value="">${esc(UI.translate("CLI default"))}</option>` +
          list.map((e) => `<option value="${esc(e)}" ${keep === e ? "selected" : ""}>${esc(e)}</option>`).join("");
        save();
      });
      [modelInput, effortSel, fallbackSel].forEach((el) => el.addEventListener("change", save));
    });
    document.querySelectorAll("[data-profile]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          await api(`/projects/${encodeURIComponent(id)}/config/profile`, { method: "POST", body: { profile: btn.dataset.profile } });
          toast("Profile applied.");
          renderSettings();
        } catch (err) { toast(err.message, true); }
      });
    });
    document.getElementById("language-select").addEventListener("change", async (e) => {
      try {
        await api(`/projects/${encodeURIComponent(id)}/config/language`, { method: "POST", body: { language: e.target.value } });
        toast("Language updated.");
      } catch (err) { toast(err.message, true); }
    });
    document.getElementById("save-test-command").addEventListener("click", async () => {
      const typed = document.getElementById("test-command").value.trim();
      const command = typed ? typed.split(/\s+/) : null;
      try {
        await api(`/projects/${encodeURIComponent(id)}/config/test-command`, { method: "POST", body: { command } });
        toast("Test command saved.");
      } catch (err) { toast(err.message, true); }
    });
    function runtimeSelection() {
      const language = document.getElementById("runtime-language").value;
      return language ? {
        language,
        version: document.getElementById("runtime-version").value.trim() || null,
        executable: document.getElementById("runtime-executable").value.trim() || null,
      } : null;
    }
    const runtimeStatus = document.getElementById("runtime-status");
    const runtimeLanguage = document.getElementById("runtime-language");
    function runtimeFieldsChanged() {
      const disabled = !runtimeLanguage.value;
      document.getElementById("runtime-version").disabled = disabled;
      document.getElementById("runtime-executable").disabled = disabled;
      runtimeStatus.textContent = "";
    }
    runtimeFieldsChanged();
    runtimeLanguage.addEventListener("change", runtimeFieldsChanged);
    ["runtime-version", "runtime-executable"].forEach((name) => {
      document.getElementById(name).addEventListener("input", () => { runtimeStatus.textContent = ""; });
    });
    document.getElementById("save-runtime").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await api(`/projects/${encodeURIComponent(id)}/config/runtime`, { method: "POST", body: { runtime: runtimeSelection() } });
        if (!stale(gen)) runtimeStatus.textContent = UI.translate("Runtime saved. Check the local version to verify availability.");
        toast("Runtime saved.");
      } catch (err) { toast(err.message, true); }
      finally { button.disabled = false; }
    });
    document.getElementById("check-runtime").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      runtimeStatus.textContent = UI.translate("Checking…");
      try {
        const selection = runtimeSelection();
        if (!selection) {
          runtimeStatus.textContent = UI.translate("Default environment; no runtime version selected.");
          return;
        }
        const result = await api(`/projects/${encodeURIComponent(id)}/config/runtime/check`, { method: "POST", body: { runtime: selection } });
        if (stale(gen)) return;
        runtimeStatus.textContent = result.matches && result.available
          ? `${UI.translate("Runtime available")}: ${result.actual_version} · ${result.executable}`
          : `${UI.translate("Runtime unavailable or version mismatch")}: ${result.reason}`;
      } catch (err) { if (!stale(gen)) runtimeStatus.textContent = err.message; }
      finally { button.disabled = false; }
    });
    const AGENT_ROLE_ORDER = ["SA", "SW", "SD", "RD", "SD/RD", "REVIEW", "SR", "AR", "SEC", "QA"];
    const PRODUCER_ROLES = ["SA", "SW", "SD", "RD", "SD/RD", "PA", "RS", "AN", "PL", "CS", "WR", "ED"];
    let editing = null;
    const refreshCatalog = async () => {
      catalog = await api(`/projects/${encodeURIComponent(id)}/workflows`);
      const legacy = document.querySelector("[data-legacy-role]");
      if (legacy) legacy.hidden = !needsCombinedRole();
    };
    const stepChips = (flow) => flow.steps.map((step, index) => `${index ? '<i class="flow-arrow" aria-hidden="true">›</i>' : ""}<span class="flow-step"><span class="chat-avatar av-${roleAvatarTone(step.role)}" aria-hidden="true">${roleAvatarIcon(step.role).replace('width="18" height="18"', 'width="12" height="12"')}</span><span data-no-i18n>${esc(roleName(step.role))}</span></span>`).join("");
    const workflowItem = (flow) => `<div class="workflow-item">
        <button class="profile-option workflow-option ${catalog.selected === flow.id ? "selected" : ""}" type="button" data-workflow="${esc(flow.id)}" aria-pressed="${catalog.selected === flow.id}">
          <span class="profile-option-name" data-no-i18n>${esc(flow.builtin ? UI.translate(flow.name) : flow.name)}</span>
          ${flow.summary ? `<span class="profile-option-desc">${esc(UI.translate(flow.summary))}</span>` : ""}
          ${flow.stats ? `<span class="strength-line">${strengthLine(flow.stats.team, { strong: 5, weak: 2 })}</span>` : ""}
          <span class="flow-steps" aria-label="${esc(UI.translate("Steps"))}">${stepChips(flow)}</span>
          ${flow.stats ? `<span class="flow-radar" title="${esc(statSummary(flow.stats.team))}">${radarSvg(flow.stats.team, { size: 144, labels: "short" })}<span class="flow-power">${esc(UI.translate("Team power"))} <strong class="num">${flow.stats.power}</strong></span></span>` : ""}
          <span class="profile-option-check" aria-hidden="true">${pageIcon("check")}</span>
        </button>
        <div class="workflow-item-actions">
          <button class="btn small" type="button" data-wf-edit="${esc(flow.id)}">${esc(UI.translate(flow.builtin ? "Save a copy to edit" : "Edit"))}</button>
          ${flow.builtin ? "" : `<button class="btn small danger" type="button" data-wf-delete="${esc(flow.id)}">${esc(UI.translate("Delete"))}</button>`}
        </div>
      </div>`;
    const SELECTION_LABELS = [["fixed", "Always the selected workflow"], ["pm", "ChatGPT chooses for each task"], ["auto", "ccc-mcp chooses from the task"]];
    const paintWorkflow = () => {
      const panel = document.getElementById("workflow-panel");
      if (!panel) return;
      if (editing) {
        const roleOption = (current) => (role) => `<option value="${esc(role)}" ${role === current ? "selected" : ""}>${esc(roleName(role))} (${esc(UI.translate(ROLE_HELP[role] || role))})</option>`;
        const roleOptions = (index, current) => {
          const allowed = (role) => index > 0 || PRODUCER_ROLES.includes(role);
          return `<optgroup label="${esc(UI.translate("Everyday team roles"))}">${EVERYDAY_ROLES.filter(allowed).map(roleOption(current)).join("")}</optgroup><optgroup label="${esc(UI.translate("Software team roles"))}">${AGENT_ROLE_ORDER.filter(allowed).map(roleOption(current)).join("")}</optgroup>`;
        };
        panel.innerHTML = `<div class="wf-editor">
          <div class="field narrow-field"><label for="wf-name">${esc(UI.translate("Workflow name"))}</label><input type="text" id="wf-name" maxlength="40" value="${esc(editing.name)}" placeholder="${esc(UI.translate("Named automatically if left empty"))}" /></div>
          <div class="wf-team"><h3 class="workflow-group-title">${esc(UI.translate("Team abilities"))}</h3>${teamPanel(teamStats(editing.steps))}</div>
          <ol class="wf-steps">${editing.steps.map((role, index) => `<li class="wf-step">
            <span class="wf-index" aria-hidden="true">${index + 1}</span>
            <span class="chat-avatar av-${roleAvatarTone(role)}" aria-hidden="true">${roleAvatarIcon(role)}</span>
            <select class="wf-role" data-index="${index}" aria-label="${esc(UI.translate("Step"))} ${index + 1}">${roleOptions(index, role)}</select>
            <button class="btn small ghost" type="button" data-wf-move="${index}:-1" aria-label="${esc(UI.translate("Move up"))}" ${index === 0 ? "disabled" : ""}>↑</button>
            <button class="btn small ghost" type="button" data-wf-move="${index}:1" aria-label="${esc(UI.translate("Move down"))}" ${index === editing.steps.length - 1 ? "disabled" : ""}>↓</button>
            <button class="btn small ghost" type="button" data-wf-remove="${index}" aria-label="${esc(UI.translate("Remove step"))}" ${editing.steps.length === 1 ? "disabled" : ""}>×</button>
          </li>`).join("")}</ol>
          <div class="wf-add-row"><button class="btn small" type="button" id="wf-add" ${editing.steps.length >= 12 ? "disabled" : ""}>${esc(UI.translate("Add step"))}</button></div>
          <div class="hint">${esc(UI.translate("The first step must make something (SA, SD, RD or SD/RD). A reviewer that finds a problem sends work back to the nearest step before it that makes something; everything after that step then runs again. Up to 12 steps."))}</div>
          <div class="wf-actions"><button class="btn small" type="button" id="wf-cancel">${esc(UI.translate("Cancel"))}</button><button class="btn small primary" type="button" id="wf-save">${esc(UI.translate("Save workflow"))}</button></div>
        </div>`;
        UI.localize(panel);
        const keepName = () => { editing.name = document.getElementById("wf-name").value; };
        panel.querySelectorAll(".wf-role").forEach((select) => select.addEventListener("change", () => { keepName(); editing.steps[Number(select.dataset.index)] = select.value; paintWorkflow(); }));
        panel.querySelectorAll("[data-wf-move]").forEach((button) => button.addEventListener("click", () => {
          keepName();
          const [from, delta] = button.dataset.wfMove.split(":").map(Number);
          const to = from + delta;
          if (to < 0 || to >= editing.steps.length) return;
          [editing.steps[from], editing.steps[to]] = [editing.steps[to], editing.steps[from]];
          // The first step has to be a producer; keep that true when something else moved up.
          if (!PRODUCER_ROLES.includes(editing.steps[0])) { [editing.steps[from], editing.steps[to]] = [editing.steps[to], editing.steps[from]]; toast("The first step must be SA, SD, RD or SD/RD.", true); }
          paintWorkflow();
        }));
        panel.querySelectorAll("[data-wf-remove]").forEach((button) => button.addEventListener("click", () => {
          keepName();
          const next = editing.steps.filter((_, index) => index !== Number(button.dataset.wfRemove));
          if (!PRODUCER_ROLES.includes(next[0])) { toast("The first step must be SA, SD, RD or SD/RD.", true); return; }
          editing.steps = next;
          paintWorkflow();
        }));
        document.getElementById("wf-add").addEventListener("click", () => { keepName(); editing.steps.push("REVIEW"); paintWorkflow(); });
        document.getElementById("wf-cancel").addEventListener("click", () => { editing = null; paintWorkflow(); });
        document.getElementById("wf-save").addEventListener("click", async () => {
          keepName();
          try {
            const saved = await api(`/projects/${encodeURIComponent(id)}/workflows`, { method: "POST", body: { id: editing.id || undefined, name: editing.name.trim() || undefined, steps: editing.steps } });
            toast("Workflow saved.");
            editing = null;
            await refreshCatalog();
            paintWorkflow();
            return saved;
          } catch (err) { toast(err.message, true); }
        });
        return;
      }
      const groups = [["general", "Everyday teams"], ["software", "Software workflows"], ["custom", "Your own workflows"]]
        .map(([audience, title]) => ({ title, flows: catalog.workflows.filter((flow) => flow.audience === audience) }))
        .filter((group) => group.flows.length);
      // The group holding the selected workflow comes first.
      groups.sort((a, b) => Number(b.flows.some((flow) => flow.id === catalog.selected)) - Number(a.flows.some((flow) => flow.id === catalog.selected)));
      panel.innerHTML = `${groups.map((group) => `<h3 class="workflow-group-title">${esc(UI.translate(group.title))}</h3><div class="workflow-list">${group.flows.map(workflowItem).join("")}</div>`).join("")}
        <div class="wf-add-row"><button class="btn small primary" type="button" id="wf-new">${esc(UI.translate("New workflow"))}</button></div>
        <div class="field narrow-field wf-selection"><label for="wf-selection">${esc(UI.translate("Who chooses the workflow for a new task"))}</label>${gateSelect("wf-selection", catalog.selection, SELECTION_LABELS)}
          <div class="hint">${esc(UI.translate("With ChatGPT choosing, it sees every workflow and picks the best fit for each task. With ccc-mcp choosing, security work gets SEC, large refactors get SD and AR, and small edits get the shortest workflow."))}</div></div>`;
      UI.localize(panel);
      panel.querySelectorAll("[data-workflow]").forEach((button) => button.addEventListener("click", async () => {
        try {
          await api(`/projects/${encodeURIComponent(id)}/config/workflow`, { method: "POST", body: { workflow: button.dataset.workflow } });
          toast("Workflow saved.");
          await refreshCatalog();
          paintWorkflow();
        } catch (err) { toast(err.message, true); }
      }));
      panel.querySelectorAll("[data-wf-edit]").forEach((button) => button.addEventListener("click", () => {
        const flow = catalog.workflows.find((item) => item.id === button.dataset.wfEdit);
        editing = { id: flow.builtin ? null : flow.id, name: flow.builtin ? "" : flow.name, steps: flow.steps.map((step) => step.role) };
        paintWorkflow();
      }));
      panel.querySelectorAll("[data-wf-delete]").forEach((button) => button.addEventListener("click", () => {
        const flow = catalog.workflows.find((item) => item.id === button.dataset.wfDelete);
        showModal({
          title: "Delete this workflow?", body: `${flow.name}\n${UI.translate("Tasks already running it keep going. If it is the selected workflow, new tasks use Classic.")}`,
          confirmLabel: "Delete", danger: true,
          onSubmit: async () => { await api(`/projects/${encodeURIComponent(id)}/workflows/${encodeURIComponent(flow.id)}/delete`, { method: "POST" }); toast("Workflow deleted."); await refreshCatalog(); paintWorkflow(); },
        });
      }));
      document.getElementById("wf-new").addEventListener("click", () => { editing = { id: null, name: "", steps: ["SA", "RD", "REVIEW"] }; paintWorkflow(); });
      document.getElementById("wf-selection").addEventListener("change", async (event) => {
        try {
          await api(`/projects/${encodeURIComponent(id)}/config/workflow-selection`, { method: "POST", body: { selection: event.target.value } });
          toast("Workflow saved.");
          await refreshCatalog();
        } catch (err) { toast(err.message, true); }
      });
    };
    paintWorkflow();
    document.getElementById("save-review-gate").addEventListener("click", async () => {
      const rounds = parseInt(document.getElementById("gate-rounds").value, 10);
      try {
        await api(`/projects/${encodeURIComponent(id)}/config/review-gate`, {
          method: "POST",
          body: {
            ar_strictness: document.getElementById("gate-ar").value,
            max_rounds: isNaN(rounds) ? undefined : rounds,
            escalation: document.getElementById("gate-escalation").value,
          },
        });
        toast("Review gates saved.");
      } catch (err) { toast(err.message, true); }
    });
    document.getElementById("save-meeting-mode").addEventListener("click", async () => {
      const enabled = document.getElementById("mm-enabled").checked;
      const maxRounds = parseInt(document.getElementById("mm-max").value, 10);
      try {
        await api(`/projects/${encodeURIComponent(id)}/config/meeting-mode`, {
          method: "POST", body: { enabled, max_rounds: isNaN(maxRounds) ? undefined : maxRounds },
        });
        toast("Meeting Mode saved.");
      } catch (err) { toast(err.message, true); }
    });
    scoringRoles.forEach((role) => {
      const sliders = Array.from(document.querySelectorAll(`.score-weight-slider[data-score-role="${role}"]`));
      const bar = document.querySelector(`.score-weight-bar[data-role="${role}"]`);
      const syncVisuals = () => {
        sliders.forEach((slider) => { slider.nextElementSibling.textContent = `${slider.value}%`; });
        if (bar) bar.querySelectorAll(".seg").forEach((seg, i) => { seg.style.width = `${sliders[i].value}%`; });
      };
      sliders.forEach((slider) => slider.addEventListener("input", () => {
        const newValue = Math.max(0, Math.min(100, Number(slider.value)));
        const others = sliders.filter((s) => s !== slider);
        const oldOthersTotal = others.reduce((sum, s) => sum + Number(s.value), 0);
        const remaining = 100 - newValue;
        if (others.length) {
          const shares = oldOthersTotal > 0
            ? others.map((s) => Number(s.value) * remaining / oldOthersTotal)
            : others.map(() => remaining / others.length);
          const rounded = shares.map((v) => Math.round(v));
          rounded[rounded.length - 1] += remaining - rounded.reduce((a, b) => a + b, 0);
          others.forEach((s, i) => { s.value = Math.max(0, Math.min(100, rounded[i])); });
        }
        syncVisuals();
      }));
      document.querySelectorAll(`[data-score-preset-role="${role}"]`).forEach((button) => button.addEventListener("click", () => {
        const weights = (SCORE_PRESETS[role] || {})[button.dataset.scorePreset];
        if (!weights) return;
        sliders.forEach((slider) => { slider.value = Math.round((weights[slider.dataset.scoreDimension] || 0) * 100); });
        syncVisuals();
      }));
    });
    document.getElementById("save-evaluation-engine").addEventListener("click", async () => {
      const enabled = document.getElementById("ee-enabled").checked;
      const autoEvaluate = document.getElementById("ee-auto").checked;
      try {
        const weights = {};
        for (const role of scoringRoles) {
          weights[role] = {};
          document.querySelectorAll(`[data-score-role="${role}"]`).forEach((field) => {
            weights[role][field.dataset.scoreDimension] = Number(field.value) / 100;
          });
          const total = Object.values(weights[role]).reduce((sum, value) => sum + value, 0);
          if (Object.values(weights[role]).some((value) => !Number.isFinite(value) || value < 0) || Math.abs(total - 1) > 0.01) throw new Error(`${role}: ${UI.translate("weights must total 100%")}`);
        }
        weights.SD = weights["SD/RD"];
        weights.RD = weights["SD/RD"];
        await api(`/projects/${encodeURIComponent(id)}/config/evaluation-engine`, {
          method: "POST", body: { enabled, auto_evaluate: autoEvaluate, weights },
        });
        toast("Evaluation Engine settings saved.");
      } catch (err) { toast(err.message, true); }
    });
    watchForChanges(gen, () => api(`/projects/${encodeURIComponent(id)}/settings`), renderSettings);
  }

  async function renderContext() {
    const gen = renderGen;
    const id = parseRoute().project;
    layout(loadingView());
    let ctx;
    try { ctx = await api(`/projects/${encodeURIComponent(id)}/context`); }
    catch (err) { if (!stale(gen)) errorView(err); return; }
    if (stale(gen)) return;
    const content = ctx.content || {};
    const shared = content.shared_context || {};
    const list = (value) => (Array.isArray(value) ? value : []);
    const field = (label, value) => value ? `<div class="ctx-field"><dt>${esc(UI.translate(label))}</dt><dd>${esc(value)}</dd></div>` : "";
    const stateKind = (state) => state === "COMPLETED" ? "positive" : state === "CANCELLED" ? "negative" : "neutral";
    const decisions = list(content.decisions);
    const invariants = list(content.invariants);
    const questions = list(content.unresolved_questions);
    const progress = list(content.progress);
    const skillsInContext = list(content.available_skills);
    const summaryFields = [
      field("Purpose", content.purpose || shared.purpose), field("Current status", shared.status),
      field("Summary", content.summary || shared.summary), field("Next action", content.next_action || shared.next_action),
    ].join("");
    const budget = ctx.token_budget ? ` / ${Number(ctx.token_budget).toLocaleString()}` : "";
    layout(`
      <div class="title-row"><div><h1>Context</h1><div class="sub">${esc(id)} · ${UI.translate("read-only PM view")}</div></div></div>
      <div class="kpi-strip">
        <div class="kpi"><div class="v num">v${esc(ctx.context_version ?? "–")}</div><div class="l">Context version</div></div>
        <div class="kpi"><div class="v num">${ctx.estimated_tokens != null ? Number(ctx.estimated_tokens).toLocaleString() : "–"}<small class="kpi-unit">${esc(budget)}</small></div><div class="l">Estimated tokens</div></div>
        <div class="kpi"><div class="v mono">${esc(String(ctx.git_revision || "–").slice(0, 8))}</div><div class="l">Git revision</div></div>
        <div class="kpi"><div class="v">${esc(ctx.role || "–")}<small class="kpi-unit"> · ${esc(ctx.target_agent || "")}</small></div><div class="l">Viewing as</div></div>
      </div>
      <div class="settings-stack">
        ${pageCard("doc", "Project summary", "What the PM currently knows about this project.",
          summaryFields ? `<dl class="ctx-fields">${summaryFields}</dl>` : `<div class="empty">${esc(UI.translate("Nothing recorded yet."))}</div>`)}
        ${pageCard("decision", "Decisions", "Choices the PM has recorded for future rounds.",
          decisions.length ? `<ul class="ctx-list">${decisions.map((d) => `<li><div class="ctx-list-main"><strong>${esc(d.content || "")}</strong>${d.reason ? `<p>${esc(d.reason)}</p>` : ""}</div><div class="ctx-list-side">${d.status ? `<span class="pill neutral">${esc(UI.translate(d.status))}</span>` : ""}${d.created ? `<time>${esc(String(d.created).slice(0, 10))}</time>` : ""}</div></li>`).join("")}</ul>` : `<div class="empty">${esc(UI.translate("No decisions recorded."))}</div>`)}
        ${pageCard("lock", "Invariants", "Rules every agent must follow in this project.",
          invariants.length ? `<ul class="ctx-list">${invariants.map((rule) => `<li><div class="ctx-list-main"><strong>${esc(rule.text || "")}</strong></div><div class="ctx-list-side"><span class="pill accent">${esc(UI.translate(rule.kind || "rule"))}</span></div></li>`).join("")}</ul>` : `<div class="empty">${esc(UI.translate("No invariants recorded."))}</div>`)}
        ${pageCard("question", "Open questions", "Things the PM still needs answered.",
          questions.length ? `<ul class="ctx-list">${questions.map((q) => `<li><div class="ctx-list-main"><strong>${esc(typeof q === "string" ? q : JSON.stringify(q))}</strong></div></li>`).join("")}</ul>` : `<div class="empty">${esc(UI.translate("Nothing is waiting on an answer."))}</div>`)}
        ${progress.length ? pageCard("list", "Progress", "Work requests tracked in this context.",
          `<ul class="ctx-list">${progress.map((t) => `<li><div class="ctx-list-main"><strong>${esc((t.requirement || "").slice(0, 160))}</strong><p class="mono">${esc(t.id || "")}</p></div><div class="ctx-list-side"><span class="pill ${stateKind(t.state)}">${esc(UI.translate(t.state || ""))}</span></div></li>`).join("")}</ul>`) : ""}
        ${skillsInContext.length ? pageCard("package", "Skills in this context", "Skills the agents receive with each handoff.",
          `<div class="chip-row">${skillsInContext.map((skill) => `<span class="chip" title="${esc(skill.description || "")}">${esc(skill.name || skill.id)}</span>`).join("")}</div>`) : ""}
        ${ctx.policy ? pageCard("shield", "Policy", "Rules applied to every agent run.", `<p class="ctx-policy">${esc(ctx.policy)}</p>`) : ""}
        <details class="ctx-raw"><summary>${esc(UI.translate("Raw data"))}</summary><pre class="mono">${esc(JSON.stringify(ctx, null, 2))}</pre></details>
      </div>
    `, { narrow: true, contentClass: "settings-wide" });
  }

  async function renderDocs() {
    const gen = renderGen;
    const docRoute = parseRoute();
    const slug = docRoute.sub || "README";
    const language = UI.getLocale() === "zh-TW" ? "ch" : "en";
    layout(loadingView());
    let page;
    try { page = await api(`/docs/${language}/${encodeURIComponent(slug)}`); }
    catch (err) { if (!stale(gen)) errorView(err); return; }
    if (stale(gen)) return;
    layout(`
      ${slug === "README" ? "" : `<div class="docs-toolbar"><a class="docs-back" href="#/docs">${ICON.back}${esc(UI.translate("All guides"))}</a></div>`}
      <div class="docs-layout no-toc${slug === "README" ? " docs-index" : ""}" id="docs-layout">
        <article class="docs-prose" id="docs-content" data-no-i18n>${page.html}</article>
      </div>
    `);
    const content = document.getElementById("docs-content");
    const usedIds = new Set();
    content.querySelectorAll("h1, h2, h3, h4, h5, h6").forEach((heading) => {
      const base = heading.textContent.toLowerCase().normalize("NFKD")
        .replace(/[^\p{L}\p{N}\- ]/gu, "").trim().replace(/\s+/g, "-") || "section";
      let id = base;
      for (let index = 2; usedIds.has(id); index++) id = `${base}-${index}`;
      heading.id = id;
      usedIds.add(id);
    });
    const sections = [...content.querySelectorAll("h2")];
    if (sections.length > 1) {
      const toc = document.createElement("nav");
      toc.className = "docs-toc";
      toc.setAttribute("aria-label", UI.translate("On this page"));
      const title = document.createElement("p");
      title.className = "docs-toc-title";
      title.textContent = UI.translate("On this page");
      toc.appendChild(title);
      const tocButtons = new Map();
      sections.forEach((heading) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "docs-toc-item";
        button.textContent = heading.textContent;
        button.addEventListener("click", () => heading.scrollIntoView({ block: "start" }));
        toc.appendChild(button);
        tocButtons.set(heading, button);
      });
      document.getElementById("docs-layout").classList.remove("no-toc");
      document.getElementById("docs-layout").appendChild(toc);
      // Highlight the section being read, so the outline doubles as a "you are here" marker.
      if ("IntersectionObserver" in window) {
        const visible = new Set();
        const mark = () => {
          const current = sections.find((heading) => visible.has(heading)) || [...sections].reverse().find((heading) => heading.getBoundingClientRect().top < 0);
          tocButtons.forEach((button, heading) => {
            const on = heading === current;
            button.classList.toggle("current", on);
            if (on) button.setAttribute("aria-current", "true"); else button.removeAttribute("aria-current");
          });
        };
        const spy = new IntersectionObserver((entries) => {
          entries.forEach((entry) => { if (entry.isIntersecting) visible.add(entry.target); else visible.delete(entry.target); });
          mark();
        }, { rootMargin: "0px 0px -65% 0px" });
        sections.forEach((heading) => spy.observe(heading));
      }
    }
    if (docRoute.anchor) content.querySelector(`#${CSS.escape(docRoute.anchor)}`)?.scrollIntoView();
    content.querySelectorAll("a[href]").forEach((link) => {
      const href = link.getAttribute("href");
      if (href.startsWith("#")) {
        link.addEventListener("click", (event) => {
          event.preventDefault();
          content.querySelector(`#${CSS.escape(decodeURIComponent(href.slice(1)))}`)?.scrollIntoView();
        });
        return;
      }
      const resolved = new URL(href, `https://docs.local/docs/${language}/${slug}.md`);
      if (resolved.pathname === "/README.md" || resolved.pathname === "/README_ZH.md") {
        link.href = `https://github.com/bruce601080102/ccc-mcp/blob/main/${resolved.pathname.slice(1)}`;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        return;
      }
      const match = resolved.pathname.match(/^\/docs\/(en|ch)\/(README|[0-9]+-[a-z0-9-]+)\.md$/);
      if (!match) return;
      const targetLocale = match[1] === "ch" ? "zh-TW" : "en";
      const target = `#/docs/${match[2]}${resolved.hash ? `?anchor=${encodeURIComponent(decodeURIComponent(resolved.hash.slice(1)))}` : ""}`;
      link.href = target;
      link.addEventListener("click", (event) => {
        if (targetLocale === UI.getLocale()) return;
        event.preventDefault();
        UI.setLocale(targetLocale);
        if (location.hash === target) route();
        else location.hash = target;
      });
    });
  }

  async function renderGlobalSkills() {
    const gen = renderGen;
    layout(loadingView());
    let installed, discovered;
    try {
      [installed, discovered] = await Promise.all([api("/skills"), api("/skills/discover")]);
    } catch (err) { if (!stale(gen)) errorView(err); return; }
    if (stale(gen)) return;
    const available = discovered.map((skill, index) => ({ skill, index }))
      .filter(({ skill }) => skill.usable && !skill.already_installed);
    const firstById = new Map();
    for (const entry of available) if (!firstById.has(entry.skill.id)) firstById.set(entry.skill.id, entry.index);
    const installedRows = installed.map((skill) => `
      <div class="skill-card has-action">
        <div class="skill-card-head"><div><strong>${esc(skill.name || skill.id)}</strong><span class="skill-id mono">${esc(skill.id)}</span></div><button class="btn small danger" data-remove-global-skill="${esc(skill.id)}">Remove</button></div>
        <p>${esc(skill.description || "")}</p>
      </div>
    `).join("");
    const discoveredRows = discovered.map((skill, index) => `
      <li class="pick-row ${skill.usable && !skill.already_installed ? "" : "is-muted"}">
        <input type="checkbox" data-global-skill-choice="${index}" aria-label="${esc(`${UI.translate("Install for all projects")}: ${skill.name}`)}" ${skill.usable && !skill.already_installed ? "" : "disabled"} />
        <div class="pick-main"><strong>${esc(skill.name)}</strong><span class="server-client">${esc(UI.translate(skill.provider))}</span><p>${esc(skill.description || "")}</p></div>
        ${skill.already_installed
          ? `<span class="pill accent">${esc(UI.translate("Already installed"))}</span>`
          : skill.usable
            ? `<span class="pill positive">${esc(UI.translate("Can install"))}</span>`
            : `<span class="pill negative" title="${esc(skill.reason || "")}">${esc(UI.translate("Not usable"))}</span>`}
      </li>
    `).join("");
    layout(`
      <div class="title-row"><div><h1>Global Skills</h1><div class="sub">${esc(UI.translate("Installed Skills are shared with every project and agent role."))}</div></div><div class="title-actions"><button class="btn" id="refresh-global-skills">Refresh</button></div></div>
      <div class="kpi-strip">
        <div class="kpi"><div class="v num">${installed.length}</div><div class="l">${esc(UI.translate("Installed for all projects"))}</div></div>
        <div class="kpi"><div class="v num">${discovered.length}</div><div class="l">${esc(UI.translate("Found on this computer"))}</div></div>
        <div class="kpi"><div class="v num">${firstById.size}</div><div class="l">${esc(UI.translate("Can install"))}</div></div>
      </div>
      <div class="settings-stack">
        ${pageCard("package", "Installed for all projects", esc(UI.translate("Removing a Skill here removes it from every project's future handoffs.")),
          installed.length ? `<div class="skill-grid wide">${installedRows}</div>` : `<div class="empty">${esc(UI.translate("No global Skills are installed yet."))}</div>`)}
        ${pageCard("search", "Discover local Skills", esc(UI.translate("Choose Skills from Claude Code or Codex and install them for every project.")),
          discovered.length ? `
            <div class="skill-bulk-actions">
              <label class="checkbox-row"><input type="checkbox" id="select-all-global-skills" ${available.length ? "" : "disabled"} /><span>${esc(UI.translate("Select all available"))}</span></label>
              <button class="btn small primary" id="install-selected-skills" disabled>${esc(UI.translate("Install selected"))} (0)</button>
            </div>
            <ul class="pick-list">${discoveredRows}</ul>
          ` : `<div class="empty">${esc(UI.translate("No local Skills were found in ~/.claude/skills or ~/.codex/skills."))}</div>`)}
      </div>
    `, { narrow: true, contentClass: "settings-wide" });
    document.getElementById("refresh-global-skills").addEventListener("click", renderGlobalSkills);
    document.querySelectorAll("[data-remove-global-skill]").forEach((button) => {
      button.addEventListener("click", () => {
        const skillId = button.dataset.removeGlobalSkill;
        confirmTextModal({
          title: "Remove global Skill",
          description: `${skillId}\n\n${UI.translate("This removes the Skill from every project's future handoffs. Source files on your computer remain unchanged.")}`,
          challenge: `REMOVE SKILL ${skillId}`,
          confirmLabel: "Remove",
          onConfirm: async (typed) => {
            await api(`/skills/${encodeURIComponent(skillId)}/remove`, { method: "POST", body: { confirm: typed } });
            toast("Global Skill removed.");
            await renderGlobalSkills();
          },
        });
      });
    });
    if (!discovered.length) return;
    const selected = new Set();
    const selectAll = document.getElementById("select-all-global-skills");
    const installButton = document.getElementById("install-selected-skills");
    const choices = [...document.querySelectorAll("[data-global-skill-choice]")];
    const updateSelection = () => {
      choices.forEach((input) => { input.checked = selected.has(Number(input.dataset.globalSkillChoice)); });
      selectAll.checked = selected.size > 0 && selected.size === firstById.size;
      selectAll.indeterminate = selected.size > 0 && selected.size < firstById.size;
      installButton.disabled = selected.size === 0;
      installButton.textContent = `${UI.translate("Install selected")} (${selected.size})`;
    };
    choices.forEach((input) => input.addEventListener("change", () => {
      const index = Number(input.dataset.globalSkillChoice);
      if (input.checked) {
        for (const other of [...selected]) if (discovered[other].id === discovered[index].id) selected.delete(other);
        selected.add(index);
      } else selected.delete(index);
      updateSelection();
    }));
    selectAll.addEventListener("change", () => {
      selected.clear();
      if (selectAll.checked) for (const index of firstById.values()) selected.add(index);
      updateSelection();
    });
    installButton.addEventListener("click", async () => {
      installButton.disabled = true;
      installButton.setAttribute("aria-busy", "true");
      let installedCount = 0;
      try {
        for (const index of selected) {
          const skill = discovered[index];
          await api("/skills/install-discovered", { method: "POST", body: { id: skill.id, path: skill.path } });
          installedCount += 1;
        }
        toast("Selected Skills installed for all projects.");
      } catch (err) {
        toast(installedCount ? `${UI.translate("Some selected Skills were installed before an error.")} ${err.message}` : err.message, true);
      }
      await renderGlobalSkills();
    });
  }

  async function renderSkills() {
    const gen = renderGen;
    const id = parseRoute().project;
    layout(loadingView());
    let skills = [], mcp = { discovered: [], installed: {} }, calls = [];
    try {
      [skills, mcp, calls] = await Promise.all([
        api("/skills"),
        api(`/projects/${encodeURIComponent(id)}/mcp`),
        api("/mcp/calls"),
      ]);
    } catch (err) { if (!stale(gen)) errorView(err); return; }
    if (stale(gen)) return;
    const skillRows = skills.map((s) => `<div class="skill-card"><strong>${esc(s.name || s.id)}</strong><p>${esc(s.description || "")}</p></div>`).join("");
    const installed = Object.entries(mcp.installed || {});
    const mcpRows = installed.map(([name, spec]) => `
      <tr>
        <td><strong>${esc(name)}</strong><div class="table-sub mono">${esc(spec.command || "")}</div></td>
        <td><span class="pill ${spec.scope === "global" ? "neutral" : "accent"}">${esc(UI.translate(spec.scope === "global" ? "Global" : "Project only"))}</span></td>
        <td class="num">${esc((spec.allowed_tools || []).length)}</td>
        <td>${spec.command_exists ? `<span class="pill positive">${esc(UI.translate("found"))}</span>` : `<span class="pill negative">${esc(UI.translate("missing"))}</span>`}</td>
        <td class="table-actions"><button class="btn small" data-tools="${esc(name)}" ${spec.command_exists ? "" : "disabled"}>Tools</button>${spec.scope === "project" ? `<button class="btn small danger" data-remove="${esc(name)}">Remove</button>` : `<span class="muted" title="${esc(UI.translate("This server is installed globally. Remove it with the CLI if you intend to affect every project."))}"> · ${esc(UI.translate("Global"))}</span>`}</td>
      </tr>
    `).join("");
    const discovered = (mcp.discovered || []).filter((item) => item.transport === "stdio");
    const discoveredRows = discovered.map((item, index) => `
      <li class="server-row">
        <span class="server-avatar" aria-hidden="true">${esc((item.name || "?").slice(0, 1).toUpperCase())}</span>
        <div class="server-main"><strong>${esc(item.name)}</strong><span class="server-client">${esc(UI.translate(item.client))}</span><code class="server-command" title="${esc(item.command || "")}">${esc(item.command || "")}</code></div>
        ${item.has_env ? `<span class="pill warning">env</span>` : `<span class="pill neutral">stdio</span>`}
        <button class="btn small" data-install="${index}">Install</button>
      </li>
    `).join("");
    const recent = calls.filter((entry) => entry.project === id).slice(-8).reverse();
    const callRows = recent.map((entry) => `
      <tr>
        <td class="mono">${esc(entry.ts || "")}</td>
        <td>${esc(entry.server || "")}</td>
        <td class="mono">${esc(entry.tool || "")}</td>
        <td>${entry.error || entry.is_error ? `<span class="pill negative">${esc(UI.translate("Error"))}</span>` : `<span class="pill positive">${esc(UI.translate("Succeeded"))}</span>`}</td>
      </tr>
    `).join("");
    layout(`
      <div class="title-row"><div><h1>Skills &amp; MCP</h1><div class="sub">${esc(id)} · ${UI.translate("Manage local tools available to this project's agents.")}</div></div><div class="title-actions"><button class="btn" id="refresh-mcp">Refresh</button></div></div>
      <div class="kpi-strip">
        <div class="kpi"><div class="v num">${installed.length}</div><div class="l">Installed for this project</div></div>
        <div class="kpi"><div class="v num">${discovered.length}</div><div class="l">Available on this computer</div></div>
        <div class="kpi"><div class="v num">${recent.length}</div><div class="l">Recent MCP calls</div></div>
      </div>
      <div class="settings-stack">
        ${pageCard("server", "MCP servers", "Installed for this project — click Tools on a row below to actually call it",
          installed.length ? `<div class="table-wrap"><table><thead><tr><th>Name</th><th>Scope</th><th class="num">Tools</th><th>Status</th><th></th></tr></thead><tbody>${mcpRows}</tbody></table></div>` : `<div class="empty">Nothing installed yet — calling a tool needs one install step first. Pick a server below, click Install, choose which tools to allow, then come back here and click Tools to call one.</div>`,
          "", { flush: installed.length > 0 })}
        ${pageCard("search", "Discover local servers", esc(UI.translate("Candidates from MCP client settings and local executables. Select one to verify its tools before installing.")),
          discovered.length ? `<ul class="server-list">${discoveredRows}</ul>` : `<div class="empty">No local MCP servers were discovered.</div>`,
          `<span class="table-sub">${esc(UI.translate("Not listed? Use Install by name to enter a command or an exact executable path."))}</span>`,
          { actions: `<button class="btn small" id="mcp-install-by-name">${esc(UI.translate("Install by name…"))}</button>` })}
        ${pageCard("clock", "Recent MCP calls", esc(id),
          recent.length ? `<div class="table-wrap"><table><thead><tr><th>Time</th><th>Server</th><th>Tool</th><th>Status</th></tr></thead><tbody>${callRows}</tbody></table></div>` : `<div class="empty">No MCP calls for this project yet.</div>`,
          "", { flush: recent.length > 0 })}
        ${pageCard("package", "Global Skills", esc(UI.translate("These Skills are shared with every project and agent role.")),
          skills.length ? `<div class="skill-grid">${skillRows}</div>` : `<div class="empty">No skills installed.</div>`,
          "", { actions: `<a class="btn small" href="#/global-skills">${esc(UI.translate("Manage global Skills"))}</a>` })}
      </div>
    `);
    document.getElementById("refresh-mcp").addEventListener("click", renderSkills);
    document.querySelectorAll("[data-install]").forEach((button) => {
      button.addEventListener("click", () => openMcpInstall(id, discovered[Number(button.dataset.install)]));
    });
    document.getElementById("mcp-install-by-name").addEventListener("click", () => openMcpInstallByName(id));
    document.querySelectorAll("[data-tools]").forEach((button) => {
      button.addEventListener("click", () => openMcpTools(id, button.dataset.tools));
    });
    document.querySelectorAll("[data-remove]").forEach((button) => {
      button.addEventListener("click", () => {
        const name = button.dataset.remove;
        showModal({
          title: "Remove from project",
          body: `${name}\n${UI.translate("This removes this project's MCP access. The MCP program itself stays installed on your computer.")}`,
          confirmLabel: "Remove",
          danger: true,
          onSubmit: async () => {
            await api(`/projects/${encodeURIComponent(id)}/mcp/${encodeURIComponent(name)}/remove`, { method: "POST" });
            toast("MCP server removed.");
            renderSkills();
          },
        });
      });
    });
  }

  async function openMcpInstall(projectId, server) {
    if (!server) return;
    const overlay = document.createElement("div");
    overlay.id = "modal-overlay";
    overlay.className = "modal-overlay";
    overlay.innerHTML = `<div class="modal modal-wide" role="dialog" aria-modal="true"><h2>${esc(UI.translate("Select tools to enable"))}</h2><div class="body">${esc(UI.translate("Checking server and tools…"))}</div></div>`;
    closeModal();
    mountModal(overlay);
    const selected = { name: server.name, client: server.client, source_file: server.source_file };
    let preview;
    try {
      preview = await api(`/projects/${encodeURIComponent(projectId)}/mcp/probe`, { method: "POST", body: selected });
    } catch (err) { closeModal(); toast(err.message, true); return; }
    if (!overlay.isConnected) return;
    const tools = preview.tools || [];
    overlay.innerHTML = `
      <div class="modal modal-wide" role="dialog" aria-modal="true">
        <h2>${esc(server.name)}</h2>
        <div class="body">${esc(UI.translate("The MCP process will be started to verify its tools. Only selected tools will be available to this project's agents."))}</div>
        <div class="mcp-command mono">${esc(preview.command)}</div>
        ${server.has_env ? `<div class="notice">${esc(UI.translate("The server defines environment variables in another client. They are not copied into ccc-mcp."))}</div>` : ""}
        <div class="field"><label>${esc(UI.translate("Select tools to enable"))} <span class="muted">(${tools.length})</span></label></div>
        <div class="tool-list">${tools.length ? tools.map((tool, index) => `<label class="tool-choice"><input type="checkbox" data-tool="${index}" /><span><strong>${esc(tool.name)} <em class="tool-risk ${tool.read_only ? "read" : "write"}">${esc(UI.translate(tool.read_only ? "Read only" : "Can change data"))}</em></strong><small>${esc(tool.description || "")}</small></span></label>`).join("") : `<div class="empty">${esc(UI.translate("No tools were exposed by this server."))}</div>`}</div>
        <div class="actions"><button class="btn" id="mcp-cancel">${esc(UI.translate("Cancel"))}</button><button class="btn primary" id="mcp-confirm" ${tools.length ? "" : "disabled"}>${esc(UI.translate("Install to project"))}</button></div>
      </div>`;
    overlay.addEventListener("click", (event) => { if (event.target === overlay) closeModal(); });
    document.getElementById("mcp-cancel").addEventListener("click", closeModal);
    const firstTool = overlay.querySelector("[data-tool]");
    if (firstTool) firstTool.focus();
    document.getElementById("mcp-confirm").addEventListener("click", async (event) => {
      const allowed = [...overlay.querySelectorAll("[data-tool]:checked")].map((input) => tools[Number(input.dataset.tool)].name);
      if (!allowed.length) { toast("No tools are selected. Choose at least one.", true); return; }
      event.currentTarget.disabled = true;
      try {
        await api(`/projects/${encodeURIComponent(projectId)}/mcp/install`, { method: "POST", body: { ...selected, tools: allowed } });
        closeModal();
        toast("MCP server installed. Opening its tools now…");
        await renderSkills();
        // Installing only grants access; it does not call anything. Open the Tools
        // dialog immediately so "install, then separately find Tools" is one
        // continuous action instead of two — this is what was confusing before.
        openMcpTools(projectId, selected.name);
      } catch (err) { toast(err.message, true); event.currentTarget.disabled = false; }
    });
  }

  // Manual fallback for an executable whose name does not match the discovery pattern,
  // or one outside PATH, the running Python environment, and the project's venv.
  function openMcpInstallByName(projectId) {
    closeModal();
    const overlay = document.createElement("div");
    overlay.id = "modal-overlay";
    overlay.className = "modal-overlay";
    overlay.innerHTML = `
      <div class="modal modal-wide" role="dialog" aria-modal="true">
        <h2>${esc(UI.translate("Install by name…"))}</h2>
        <div class="body">${esc(UI.translate("Resolves the same way as `skill install mcp <name>`: a client's config, or a PATH command named <name>, <name>-mcp or <name>-server."))}</div>
        <div class="field"><label for="mcp-name-input">${esc(UI.translate("Name"))}</label><input type="text" id="mcp-name-input" placeholder="codebase-memory" autocomplete="off" /></div>
        <div class="field"><label for="mcp-command-input">${esc(UI.translate("Command path (optional)"))}</label><input type="text" id="mcp-command-input" placeholder="${esc(UI.translate("leave empty to resolve from PATH or a client's config"))}" autocomplete="off" /></div>
        <div class="actions"><button class="btn" id="mcp-name-cancel">${esc(UI.translate("Cancel"))}</button><button class="btn primary" id="mcp-name-check">${esc(UI.translate("Check"))}</button></div>
      </div>`;
    mountModal(overlay);
    overlay.addEventListener("click", (event) => { if (event.target === overlay) closeModal(); });
    document.getElementById("mcp-name-cancel").addEventListener("click", closeModal);
    const nameInput = document.getElementById("mcp-name-input");
    nameInput.focus();
    const check = async () => {
      const name = nameInput.value.trim();
      const command = document.getElementById("mcp-command-input").value.trim();
      if (!name) { toast(UI.translate("Enter an MCP server name"), true); return; }
      const button = document.getElementById("mcp-name-check");
      button.disabled = true;
      let preview;
      try {
        preview = await api(`/projects/${encodeURIComponent(projectId)}/mcp/probe-by-name`, { method: "POST", body: { name, command } });
      } catch (err) { toast(err.message, true); button.disabled = false; return; }
      if (!overlay.isConnected) return;
      const tools = preview.tools || [];
      overlay.innerHTML = `
        <div class="modal modal-wide" role="dialog" aria-modal="true">
          <h2>${esc(preview.name)}</h2>
          <div class="body">${esc(UI.translate("The MCP process will be started to verify its tools. Only selected tools will be available to this project's agents."))}</div>
          <div class="mcp-command mono">${esc(preview.command)}</div>
          <div class="table-sub">${esc(UI.translate("Source"))}: ${esc(preview.source || "")}</div>
          ${(preview.warnings || []).map((w) => `<div class="notice">${esc(w)}</div>`).join("")}
          <div class="field"><label>${esc(UI.translate("Select tools to enable"))} <span class="muted">(${tools.length})</span></label></div>
          <div class="tool-list">${tools.length ? tools.map((tool, index) => `<label class="tool-choice"><input type="checkbox" data-tool="${index}" /><span><strong>${esc(tool.name)} <em class="tool-risk ${tool.read_only ? "read" : "write"}">${esc(UI.translate(tool.read_only ? "Read only" : "Can change data"))}</em></strong><small>${esc(tool.description || "")}</small></span></label>`).join("") : `<div class="empty">${esc(UI.translate("No tools were exposed by this server."))}</div>`}</div>
          <div class="actions"><button class="btn" id="mcp-name-cancel2">${esc(UI.translate("Cancel"))}</button><button class="btn primary" id="mcp-name-confirm" ${tools.length ? "" : "disabled"}>${esc(UI.translate("Install to project"))}</button></div>
        </div>`;
      overlay.querySelector('[role="dialog"]').focus();
      document.getElementById("mcp-name-cancel2").addEventListener("click", closeModal);
      document.getElementById("mcp-name-confirm").addEventListener("click", async (event) => {
        const allowed = [...overlay.querySelectorAll("[data-tool]:checked")].map((input) => tools[Number(input.dataset.tool)].name);
        if (!allowed.length) { toast("No tools are selected. Choose at least one.", true); return; }
        event.currentTarget.disabled = true;
        try {
          await api(`/projects/${encodeURIComponent(projectId)}/mcp/install-by-name`, { method: "POST", body: { name: preview.name, command, tools: allowed } });
          closeModal();
          toast("MCP server installed. Opening its tools now…");
          await renderSkills();
          openMcpTools(projectId, preview.name);
        } catch (err) { toast(err.message, true); event.currentTarget.disabled = false; }
      });
    };
    document.getElementById("mcp-name-check").addEventListener("click", check);
    nameInput.addEventListener("keydown", (event) => { if (event.key === "Enter") check(); });
  }

  async function openMcpTools(projectId, serverName) {
    closeModal();
    const overlay = document.createElement("div");
    overlay.id = "modal-overlay";
    overlay.className = "modal-overlay";
    overlay.innerHTML = `<div class="modal modal-wide" role="dialog" aria-modal="true"><h2>${esc(serverName)}</h2><div class="body">${esc(UI.translate("Checking server and tools…"))}</div></div>`;
    mountModal(overlay);
    let tools, projectRoot = "";
    try {
      const [availableTools, projects] = await Promise.all([
        api(`/projects/${encodeURIComponent(projectId)}/mcp/${encodeURIComponent(serverName)}/tools`),
        api("/projects"),
      ]);
      tools = availableTools;
      projectRoot = projects.find((project) => project.id === projectId)?.root || "";
    } catch (err) { closeModal(); toast(err.message, true); return; }
    if (!overlay.isConnected) return;
    overlay.innerHTML = `
      <div class="modal modal-wide" role="dialog" aria-modal="true">
        <h2>${esc(serverName)}</h2>
        <div class="body">${esc(UI.translate("Choose a tool and fill in its required fields. The result appears here and is not saved by this page."))}</div>
        ${tools.length ? `
          <div class="field"><label for="mcp-tool-select">${esc(UI.translate("Tool"))}</label><select id="mcp-tool-select">${tools.map((tool, index) => `<option value="${index}">${esc(tool.name)}</option>`).join("")}</select></div>
          <div id="mcp-tool-fields"></div>
          <div id="mcp-tool-validation" class="mcp-tool-validation" role="alert"></div>
          <details class="schema-details"><summary>${esc(UI.translate("About this tool"))}</summary><div id="mcp-tool-description" class="mcp-tool-description"></div></details>
          <details class="schema-details"><summary>${esc(UI.translate("Optional parameters (JSON)"))}</summary>
            <div class="hint">${esc(UI.translate("Leave {} to use the tool's defaults. Add optional fields as a JSON object."))}</div>
            <label for="mcp-tool-arguments">${esc(UI.translate("Optional arguments"))}</label><textarea id="mcp-tool-arguments" spellcheck="false">{}</textarea>
          </details>
          <details class="schema-details"><summary>${esc(UI.translate("Input schema"))}</summary><pre id="mcp-tool-schema" class="panel mono"></pre></details>
          <div id="mcp-tool-result" aria-live="polite"></div>
        ` : `<div class="empty">${esc(UI.translate("No enabled tools are available."))}</div>`}
        <div class="actions"><button class="btn" id="mcp-tool-close">${esc(UI.translate("Close"))}</button>${tools.length ? `<button class="btn primary" id="mcp-tool-run">${esc(UI.translate("Run tool"))}</button>` : ""}</div>
      </div>`;
    overlay.addEventListener("click", (event) => { if (event.target === overlay) closeModal(); });
    document.getElementById("mcp-tool-close").addEventListener("click", closeModal);
    if (!tools.length) return;
    const select = document.getElementById("mcp-tool-select");
    const requiredFields = (tool) => {
      const schema = tool.input_schema || {};
      const required = Array.isArray(schema.required) ? schema.required : [];
      const properties = schema.properties || {};
      if (!required.length) return `<p class="mcp-tool-empty">${esc(UI.translate("This tool has no required parameters. Open Optional parameters to add any settings."))}</p>`;
      return `<div class="mcp-tool-fields-title">${esc(UI.translate("Required parameters"))}</div>` + required.map((name, index) => {
        const spec = properties[name] || {};
        const kind = Array.isArray(spec.type) ? spec.type.find((type) => type !== "null") : spec.type;
        const value = name === "repo_path" && projectRoot ? projectRoot : spec.default;
        const label = name === "repo_path" ? UI.translate("Repository path (repo_path)") : (spec.title ? `${spec.title} (${name})` : name);
        const fieldId = `mcp-required-${index}`;
        let control;
        if (Array.isArray(spec.enum)) {
          control = `<select id="${fieldId}" data-required-index="${index}"><option value="">${esc(UI.translate("Choose a value"))}</option>${spec.enum.map((option) => `<option value="${esc(option)}" ${option === value ? "selected" : ""}>${esc(option)}</option>`).join("")}</select>`;
        } else if (kind === "boolean") {
          control = `<select id="${fieldId}" data-required-index="${index}"><option value="">${esc(UI.translate("Choose a value"))}</option><option value="true" ${value === true ? "selected" : ""}>true</option><option value="false" ${value === false ? "selected" : ""}>false</option></select>`;
        } else if (kind === "object" || kind === "array") {
          control = `<textarea id="${fieldId}" data-required-index="${index}" spellcheck="false">${esc(value === undefined ? "" : JSON.stringify(value, null, 2))}</textarea>`;
        } else {
          control = `<input id="${fieldId}" data-required-index="${index}" type="${kind === "number" || kind === "integer" ? "number" : "text"}" ${kind === "number" ? 'step="any"' : ""} value="${esc(value === undefined ? "" : value)}" />`;
        }
        const hint = name === "repo_path" && projectRoot
          ? UI.translate("Current project folder is filled in. Change it to use another repository.")
          : spec.description || "";
        return `<div class="field mcp-required-field"><label for="${fieldId}">${esc(label)} <span class="required-mark">${esc(UI.translate("Required"))}</span></label>${control}${hint ? `<div class="hint">${esc(hint)}</div>` : ""}</div>`;
      }).join("");
    };
    const refreshTool = () => {
      const tool = tools[Number(select.value)];
      document.getElementById("mcp-tool-description").textContent = tool.description || "";
      document.getElementById("mcp-tool-schema").textContent = JSON.stringify(tool.input_schema || {}, null, 2);
      document.getElementById("mcp-tool-fields").innerHTML = requiredFields(tool);
      document.getElementById("mcp-tool-arguments").value = "{}";
      document.getElementById("mcp-tool-validation").textContent = "";
      document.getElementById("mcp-tool-result").replaceChildren();
    };
    select.addEventListener("change", refreshTool);
    refreshTool();
    select.focus();
    document.getElementById("mcp-tool-run").addEventListener("click", async (event) => {
      const tool = tools[Number(select.value)];
      const schema = tool.input_schema || {};
      const required = Array.isArray(schema.required) ? schema.required : [];
      const properties = schema.properties || {};
      const validation = document.getElementById("mcp-tool-validation");
      validation.textContent = "";
      let argumentsValue, invalidField = null;
      try {
        argumentsValue = JSON.parse(document.getElementById("mcp-tool-arguments").value || "{}");
        if (!argumentsValue || Array.isArray(argumentsValue) || typeof argumentsValue !== "object") throw new Error();
      } catch (_) {
        validation.textContent = UI.translate("Optional parameters must be a JSON object, such as {}.");
        const optionalInput = document.getElementById("mcp-tool-arguments");
        optionalInput.closest("details").open = true;
        optionalInput.focus();
        return;
      }
      for (const [index, name] of required.entries()) {
        const input = document.getElementById(`mcp-required-${index}`);
        const raw = input.value.trim();
        input.removeAttribute("aria-invalid");
        if (!raw) {
          validation.textContent = `${name}: ${UI.translate("This field is required.")}`;
          invalidField = input;
          break;
        }
        const spec = properties[name] || {};
        const kind = Array.isArray(spec.type) ? spec.type.find((type) => type !== "null") : spec.type;
        if (kind === "integer" || kind === "number") {
          const number = Number(raw);
          if (!Number.isFinite(number) || kind === "integer" && !Number.isInteger(number)) {
            validation.textContent = `${name}: ${UI.translate(kind === "integer" ? "Enter a whole number." : "Enter a number.")}`;
            invalidField = input;
            break;
          }
          argumentsValue[name] = number;
        } else if (kind === "boolean") {
          argumentsValue[name] = raw === "true";
        } else if (kind === "object" || kind === "array") {
          try {
            const value = JSON.parse(raw);
            if (kind === "array" ? !Array.isArray(value) : !value || Array.isArray(value) || typeof value !== "object") throw new Error();
            argumentsValue[name] = value;
          } catch (_) {
            validation.textContent = `${name}: ${UI.translate(kind === "array" ? "Enter a JSON array." : "Enter a JSON object.")}`;
            invalidField = input;
            break;
          }
        } else argumentsValue[name] = raw;
      }
      if (invalidField) {
        invalidField.setAttribute("aria-invalid", "true");
        invalidField.focus();
        return;
      }
      const button = event.currentTarget;
      button.disabled = true;
      const result = document.getElementById("mcp-tool-result");
      result.textContent = UI.translate("Running tool…");
      try {
        const response = await api(`/projects/${encodeURIComponent(projectId)}/mcp/${encodeURIComponent(serverName)}/tools/${encodeURIComponent(tool.name)}/call`, { method: "POST", body: { arguments: argumentsValue } });
        result.innerHTML = `<div class="result-label ${response.is_error ? "error-text" : ""}">${esc(UI.translate(response.is_error ? "Tool returned an error" : "Tool result"))}${response.truncated ? ` · ${esc(UI.translate("truncated"))}` : ""}</div><pre class="log-panel" id="mcp-result-text"></pre>`;
        document.getElementById("mcp-result-text").textContent = response.text || "";
      } catch (err) { result.innerHTML = `<div class="notice">${esc(err.message)}</div>`; }
      button.disabled = false;
    });
  }

  function providerRow(name, info) {
    if (!info) return "";
    const state = !info.installed
      ? `<span class="pill neutral">${esc(UI.translate("not installed"))}</span>`
      : info.logged_in
        ? `<span class="pill positive">${esc(UI.translate("signed in"))}</span>`
        : `<span class="pill warning">${esc(UI.translate("not signed in"))}</span>`;
    return `
      <div class="kv-row">
        <span class="k">${esc(name)}</span>
        <span class="v">${state}${info.hint ? ` <span class="muted">· ${esc(info.hint)}</span>` : ""}</span>
      </div>
    `;
  }

  async function renderConnect() {
    const gen = renderGen;
    layout(`<div class="empty"><span class="spinner"></span> Loading…</div>`);
    let status;
    try { status = await api("/connect/status"); }
    catch (err) { if (!stale(gen)) errorView(err); return; }
    if (stale(gen)) return;
    lastConnectStatus = status;
    const live = status.state === "connected" || status.state === "starting" || status.state === "stopping";
    const dotCls = status.state === "connected" ? "on" : status.state === "error" ? "err" : status.state === "starting" || status.state === "stopping" ? "pending" : "";
    const savedConnections = status.saved_connections || [];
    layout(`
      <div class="title-row">
        <div><h1>Connect</h1><div class="sub">Link this ccc-mcp home to ChatGPT over an OpenAI Platform Tunnel.</div></div>
      </div>
      <div class="settings-stack">
        ${pageCard("link", "Connection status", "", `
          <div class="status-row connect-state is-${dotCls || "off"}"><span class="status-dot ${dotCls}"></span><strong>${esc(UI.translate(status.state))}</strong>${status.tunnel_id ? ` <span class="mono muted">${esc(status.tunnel_id)}</span>` : ""}</div>
          ${status.error ? `<div class="kv-row"><span class="k">Error</span><span class="v" style="color:var(--negative)">${esc(status.error)}</span></div>` : ""}
          ${!status.tunnel_client_installed ? `<div class="hint">tunnel-client is not installed yet — it downloads and verifies automatically on first connect.</div>` : ""}
          <div id="connect-log-wrap" ${live ? 'style="margin-top:16px"' : ""}></div>`,
          live ? `<button class="btn small danger" id="btn-disconnect">Disconnect</button>` : "")}
        ${!live ? pageCard("key", "Connection profile", "Save several named connections, each with its own Tunnel ID and runtime key. One connection runs at a time.", `
            <input type="hidden" id="connect-profile-id" />
            <div class="settings-grid">
              <div class="field">
                <label for="connect-name">Connection name</label>
                <input type="text" id="connect-name" placeholder="e.g. Work / Personal" maxlength="60" />
              </div>
              <div class="field">
                <label for="connect-tunnel-id">Tunnel ID</label>
                <input type="text" id="connect-tunnel-id" class="mono" placeholder="tunnel_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" />
              </div>
            </div>
            <div class="field">
              <label for="connect-api-key">Runtime API key</label>
              <input type="password" id="connect-api-key" placeholder="leave empty to use a remembered key" autocomplete="off" />
              <div class="hint">${esc(UI.translate("Saved keys are never shown again. Enter a new key here to replace one."))}</div>
            </div>
            <label class="settings-switch" for="connect-remember"><input type="checkbox" role="switch" id="connect-remember" /><span>Remember this key on this device</span></label>
            <div class="hint" id="connect-progress" role="status" aria-live="polite"></div>`,
          `<button class="btn small ghost" id="btn-clear-connection">New profile</button><button class="btn small" id="btn-save-connection">Save profile</button><button class="btn small primary" id="btn-connect">Connect</button>`) : ""}
        ${!live && savedConnections.length ? pageCard("download", "Saved connections", "Saved to ~/.ccc-mcp/tunnel/credentials.json, readable only by your OS account. Keys are never displayed here.",
          savedConnections.map((profile) => `
            <div class="saved-connection">
              <span class="server-avatar" aria-hidden="true">${esc((profile.name || "?").slice(0, 1).toUpperCase())}</span>
              <div><strong>${esc(profile.name)}</strong><div class="mono muted">${esc(profile.tunnel_id)}</div></div>
              <span class="connect-row-actions">
                <button class="btn small" data-use="${esc(profile.id)}">Use</button>
                <button class="btn small danger" data-forget="${esc(profile.id)}">Forget</button>
              </span>
            </div>
          `).join("")) : ""}
        ${pageCard("test", "Native CLI login", "Codex and Claude Code sign in independently of the Tunnel; ccc-mcp never stores their credentials.",
          `<div id="provider-status"><div class="kv-row"><span class="k muted"><span class="spinner small"></span> Checking…</span></div></div>`)}
      </div>
    `, { narrow: true, contentClass: "settings-wide" });
    api("/connect/providers").then((r) => {
      if (stale(gen)) return;
      const el = document.getElementById("provider-status");
      if (el) el.innerHTML = providerRow("codex", r.providers.codex) + providerRow("claude", r.providers.claude);
    }).catch(() => {
      const el = document.getElementById("provider-status");
      if (el) el.innerHTML = `<div class="kv-row"><span class="k muted">${esc(UI.translate("Could not check native CLI login status."))}</span></div>`;
    });
    if (live) {
      const renderLog = (lines) => {
        const wrap = document.getElementById("connect-log-wrap");
        if (!wrap) return;
        wrap.innerHTML = lines.length
          ? `<div class="log-panel">${lines.map((l) => esc(l)).join("\n")}</div>`
          : `<div class="log-panel"><span class="muted-line">${esc(UI.translate("Waiting for tunnel-client output…"))}</span></div>`;
        wrap.scrollTop = wrap.scrollHeight;
      };
      api("/connect/logs").then((r) => renderLog(r.lines)).catch(() => {});
      trackTimer(setInterval(async () => {
        try {
          const [logs, st] = await Promise.all([api("/connect/logs"), api("/connect/status")]);
          renderLog(logs.lines);
          if (st.state !== status.state) renderConnect();
        } catch (e) {}
      }, 2000));
      const disconnectBtn = document.getElementById("btn-disconnect");
      if (disconnectBtn) disconnectBtn.addEventListener("click", async () => {
        disconnectBtn.disabled = true;
        try { await api("/connect/stop", { method: "POST" }); toast("Disconnected."); renderConnect(); }
        catch (err) { toast(err.message, true); disconnectBtn.disabled = false; }
      });
      return;
    }
    const connectBtn = document.getElementById("btn-connect");
    if (connectBtn) connectBtn.addEventListener("click", async () => {
      const tunnelId = document.getElementById("connect-tunnel-id").value.trim();
      const name = document.getElementById("connect-name").value.trim();
      const profileId = document.getElementById("connect-profile-id").value || undefined;
      const apiKey = document.getElementById("connect-api-key").value;
      const remember = document.getElementById("connect-remember").checked;
      if (!tunnelId) { toast("Tunnel ID is required.", true); return; }
      connectBtn.disabled = true;
      connectBtn.innerHTML = `<span class="spinner"></span> ${esc(UI.translate("Connecting…"))}`;
      const progress = document.getElementById("connect-progress");
      const progressTimer = setTimeout(() => {
        if (progress) progress.textContent = UI.translate("Still checking — verifying the runtime key and tunnel access can take up to a minute…");
      }, 4000);
      try {
        await api("/connect/start", {
          method: "POST",
          body: { tunnel_id: tunnelId, name: name || undefined, profile_id: profileId, api_key: apiKey || undefined, remember },
        });
        toast("Connected.");
        renderConnect();
      } catch (err) {
        clearTimeout(progressTimer);
        toast(err.message, true);
        connectBtn.disabled = false;
        connectBtn.textContent = UI.translate("Connect");
      }
    });
    document.getElementById("btn-save-connection").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await api("/connect/save", { method: "POST", body: {
          profile_id: document.getElementById("connect-profile-id").value || undefined,
          name: document.getElementById("connect-name").value.trim(),
          tunnel_id: document.getElementById("connect-tunnel-id").value.trim(),
          api_key: document.getElementById("connect-api-key").value || undefined,
        } });
        document.getElementById("connect-api-key").value = "";
        toast("Connection saved.");
        renderConnect();
      } catch (err) { toast(err.message, true); button.disabled = false; }
    });
    document.getElementById("btn-clear-connection").addEventListener("click", () => {
      for (const id of ["connect-profile-id", "connect-name", "connect-tunnel-id", "connect-api-key"]) document.getElementById(id).value = "";
      document.getElementById("connect-remember").checked = false;
      document.getElementById("connect-name").focus();
    });
    document.querySelectorAll("[data-use]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const profile = savedConnections.find((row) => row.id === btn.dataset.use);
        document.getElementById("connect-profile-id").value = profile.id;
        document.getElementById("connect-name").value = profile.name;
        document.getElementById("connect-tunnel-id").value = profile.tunnel_id;
        document.getElementById("connect-api-key").value = "";
        document.getElementById("connect-name").focus();
      });
    });
    document.querySelectorAll("[data-forget]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const profile = savedConnections.find((row) => row.id === btn.dataset.forget);
        showModal({ title: "Forget connection", body: `${profile.name}\n\n${UI.translate("This removes the saved key from this device. It does not revoke the key at OpenAI.")}`,
          danger: true, confirmLabel: "Forget", onSubmit: async () => {
            await api("/connect/forget", { method: "POST", body: { profile_id: profile.id } });
            toast("Forgotten.");
            renderConnect();
          } });
      });
    });
    watchForChanges(gen, () => api("/connect/status"), renderConnect);
  }

  function route() {
    renderGen += 1;
    captureTokenFromHash();
    const r = parseRoute();
    refreshConnectDot();
    if (r.view === "docs") return renderDocs();
    if (r.view === "connect") return renderConnect();
    if (r.view === "global-skills") return renderGlobalSkills();
    if (r.view === "dashboard") return renderDashboard();
    if (!r.project) return renderProjects();
    if (r.view === "tasks" && r.sub) return renderTaskDetail();
    if (r.view === "tasks") return renderTasksList();
    if (r.view === "settings") return renderSettings();
    if (r.view === "context") return renderContext();
    if (r.view === "skills") return renderSkills();
    return renderProjectOverview();
  }

  window.addEventListener("hashchange", route);
  route();
  api("/session").then((session) => {
    appVersion = String(session.version || "");
    const label = document.getElementById("app-version");
    if (label && appVersion) label.textContent = `· v${appVersion}`;
  }).catch(() => {});
})();
