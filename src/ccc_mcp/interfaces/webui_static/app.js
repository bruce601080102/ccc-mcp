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
    const method = opts.method || "GET";
    // A stalled read must release the background poll. Mutating requests are never retried here.
    const signal = method === "GET" ? AbortSignal.timeout(8000) : undefined;
    const res = await fetch("/api" + path, { method, headers, body, signal });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = { error: text }; }
    if (!res.ok) {
      const err = new Error(UI.error(data?.error || ("Request failed: " + res.status), res.status));
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
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
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
    el.textContent = isError ? UI.error(message) : UI.translate(message);
    el.style.display = "block";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.style.display = "none"; }, 4200);
  }

  let modalReturnFocus = null;
  const hostTestKickoff = {}; // task -> when the person started its tests here, until a result shows up
  let focusRuntime = false; // set by the failed-install help link: scroll to the runtime card after navigating
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
    if (event.key === "Escape") { if (!overlay.querySelector("#modal-confirm:disabled")) closeModal(); return; }
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

  const GOAL_ID = /^GOAL-\d+$/;
  const LAST_PROJECT_KEY = "ccc_last_project";
  const rememberProject = (id) => { try { localStorage.setItem(LAST_PROJECT_KEY, id); } catch (e) {} };
  const rememberedProject = () => { try { return localStorage.getItem(LAST_PROJECT_KEY) || ""; } catch (e) { return ""; } };

  // The console has four places: Tasks (every piece of work, grouped), Projects (what ccc-mcp may
  // touch), Skills & MCP (extra guidance and local tools for the AI team) and Settings (the
  // connection to ChatGPT and the AI tools). Old addresses still resolve,
  // to the page that now holds that content; they never open a second way of doing the same thing.
  function parseRoute() {
    const hash = location.hash.replace(/^#/, "") || "/";
    const parts = hash.split("?", 2)[0].split("/").filter(Boolean);
    if (parts[0] === "p" && parts[1]) {
      const project = decodeURIComponent(parts[1]);
      const view = parts[2] || "overview";
      const sub = parts[3] ? decodeURIComponent(parts[3]) : undefined;
      if (view === "tasks" || view === "goals") {
        if (sub && GOAL_ID.test(sub)) return { project, view: "goal", sub };
        return { project, view: sub ? "task" : "tasks", sub };
      }
      return { project, view: "overview" };
    }
    if (parts[0] === "tasks") return { view: "tasks-home" };
    if (parts[0] === "skills") return { view: "skills" };
    if (parts[0] === "settings" || parts[0] === "connect") return { view: "settings" };
    return { view: "projects" };
  }
  const routeKey = (r) => [r.view, r.project || "", r.sub || ""].join("|");
  function navTo(path) { location.hash = path; }

  const ICON = {
    projects: `<svg class="icon" viewBox="0 0 16 16"><path d="M1.5 4.5a1 1 0 0 1 1-1h3l1.2 1.5H13.5a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-7.5Z"/></svg>`,
    tasks: `<svg class="icon" viewBox="0 0 16 16"><path d="M3 4.5h10M3 8h10M3 11.5h6"/></svg>`,
    skills: `<svg class="icon" viewBox="0 0 16 16"><path d="M5.7 2.3a1.3 1.3 0 0 1 2.6 0c0 .5-.3.8-.3 1.3 0 .4.3.7.7.7h1.6a.7.7 0 0 1 .7.7v1.6c0 .4.3.7.7.7.5 0 .8-.3 1.3-.3a1.3 1.3 0 0 1 0 2.6c-.5 0-.8-.3-1.3-.3-.4 0-.7.3-.7.7v1.6a.7.7 0 0 1-.7.7H8.7c-.4 0-.7-.3-.7-.7 0-.5.3-.8.3-1.3a1.3 1.3 0 0 0-2.6 0c0 .5.3.8.3 1.3 0 .4-.3.7-.7.7H3.7a.7.7 0 0 1-.7-.7v-1.6c0-.4-.3-.7-.7-.7-.5 0-.8.3-1.3.3a1.3 1.3 0 0 1 0-2.6c.5 0 .8.3 1.3.3.4 0 .7-.3.7-.7V5c0-.4.3-.7.7-.7h1.6c.4 0 .7-.3.7-.7 0-.5-.3-.8-.3-1.3Z"/></svg>`,
    settings: `<svg class="icon" viewBox="0 0 16 16"><circle cx="8" cy="8" r="2.1"/><path d="M8 2v1.6M8 12.4V14M13.3 5.3l-1.3.8M4 9.9l-1.3.8M14 8h-1.6M3.6 8H2M13.3 10.7l-1.3-.8M4 6.1l-1.3-.8"/></svg>`,
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
  let providerInfoCache = {};
  let activeTimers = [];
  function stopPolling() {
    activeTimers.forEach((timer) => (typeof timer === "object" ? timer.stop() : clearInterval(timer)));
    activeTimers = [];
  }
  function trackTimer(id) {
    activeTimers.push(id);
    return id;
  }

  // ---- background updates ----------------------------------------------------------------
  // A page never reloads to show new information. It asks the server cheaply whether anything
  // changed (`tick`) and patches only what moved. A failed request keeps the screen exactly as it
  // is, retries with a growing delay, and shows a small "reconnecting" note until one succeeds.
  function setOffline(offline) {
    let note = document.getElementById("live-offline");
    if (!offline) { if (note) note.remove(); return; }
    if (note) return;
    note = document.createElement("div");
    note.id = "live-offline";
    note.className = "live-offline";
    note.setAttribute("role", "status");
    note.textContent = UI.translate("Reconnecting… the page below is the last saved state.");
    document.body.appendChild(note);
  }

  function livePoll(gen, tick, interval) {
    interval = interval || 2500;
    let delay = interval, failures = 0, timer = null, running = false, stopped = false;
    const schedule = () => {
      if (stopped || stale(gen)) return;
      timer = setTimeout(run, document.hidden ? Math.max(delay, 10000) : delay);
    };
    const run = async () => {
      if (stopped || stale(gen) || running) return;
      running = true;
      try { await tick(); failures = 0; delay = interval; setOffline(false); }
      catch (err) { failures += 1; delay = Math.min(30000, interval * 2 ** failures); if (failures >= 2) setOffline(true); }
      finally { running = false; schedule(); }
    };
    const onVisible = () => { if (!document.hidden && !running) { clearTimeout(timer); run(); } };
    document.addEventListener("visibilitychange", onVisible);
    trackTimer({ stop: () => { stopped = true; clearTimeout(timer); document.removeEventListener("visibilitychange", onVisible); setOffline(false); } });
    schedule();
    return { now: run };
  }

  // Update the page that is on screen without touching what the person is doing in it.
  function morphContent(html) {
    const content = document.querySelector("#sheet .content");
    if (content) window.cccMorph.morph(content, html, UI.localize);
  }

  async function refreshConnectDot() {
    try { lastConnectStatus = await api("/connect/status"); }
    catch (e) { lastConnectStatus = null; }
    const dot = document.getElementById("nav-connect-dot");
    if (!dot || !lastConnectStatus) return;
    dot.className = "dot" + (lastConnectStatus.state === "connected" ? " on" : lastConnectStatus.state === "error" ? " err" : "");
  }

  // The sidebar is built once and only its highlight changes when you move between pages, so
  // navigating never rebuilds the frame around the page.
  let shellLocale = null;
  function ensureShell() {
    const r = parseRoute();
    if (!document.getElementById("sheet") || shellLocale !== UI.getLocale()) {
      shellLocale = UI.getLocale();
      const dotClass = "dot" + (lastConnectStatus && lastConnectStatus.state === "connected" ? " on" : lastConnectStatus && lastConnectStatus.state === "error" ? " err" : "");
      root.innerHTML = `
        <button type="button" class="skip-link" id="skip-link">Skip to content</button>
        <nav class="sidebar" aria-label="Main">
          <div class="brand"><span class="mark">C</span><span>ccc-mcp<small>CONTROL CENTER <span id="app-version" data-no-i18n>${appVersion ? `· v${esc(appVersion)}` : ""}</span></small></span></div>
          <a class="nav-item" data-nav="tasks" href="#/tasks">${ICON.tasks}Tasks<span id="nav-needs-badge" class="nav-badge" hidden></span></a>
          <a class="nav-item" data-nav="projects" href="#/">${ICON.projects}Projects</a>
          <a class="nav-item" data-nav="skills" href="#/skills">${ICON.skills}Skills &amp; MCP</a>
          <a class="nav-item" data-nav="settings" href="#/settings">${ICON.settings}Settings<span id="nav-connect-dot" class="${dotClass}"></span></a>
          <div class="sidebar-spacer"></div>
          <button type="button" id="theme-toggle" class="nav-item" aria-pressed="${currentTheme() === "dark"}" aria-label="Toggle dark mode">${currentTheme() === "dark" ? ICON.moon : ICON.sun}<span>Dark mode</span><span class="dot${currentTheme() === "dark" ? " on" : ""}"></span></button>
          <div class="sidebar-locale"><span>Interface language</span><select id="ui-locale" aria-label="Interface language"><option value="zh-TW">繁體中文</option><option value="en">English</option></select></div>
          <div class="sidebar-foot">LOCAL CONSOLE <span class="status-dot on"></span></div>
        </nav>
        <div class="frame"><main class="sheet" id="sheet" tabindex="-1"><div class="content"></div></main></div>
      `;
      document.getElementById("skip-link").addEventListener("click", () => document.getElementById("sheet").focus());
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
    const active = r.view === "settings" ? "settings" : r.view === "skills" ? "skills" : ["projects", "overview"].includes(r.view) ? "projects" : "tasks";
    root.querySelectorAll(".sidebar .nav-item[data-nav]").forEach((item) => item.classList.toggle("active", item.dataset.nav === active));
    const activeNav = root.querySelector(".sidebar .nav-item.active");
    if (activeNav && activeNav.scrollIntoView) activeNav.scrollIntoView({ block: "nearest", inline: "center" });
  }

  let lastRouteKey = "";
  const isLoading = (html) => html.includes("loading-skeleton");
  function layout(contentHtml, opts) {
    opts = opts || {};
    const r = parseRoute();
    if (r.project) rememberProject(r.project);
    const key = routeKey(r);
    stopPolling();
    ensureShell();
    const sheet = document.getElementById("sheet");
    const content = sheet.querySelector(".content");
    const showingThisPage = content.dataset.route === key && !content.querySelector(".loading-skeleton");
    // A page that is already on screen is not blanked out while it fetches again.
    if (isLoading(contentHtml) && showingThisPage) return;
    const keep = key === lastRouteKey ? sheet.scrollTop : 0;
    content.className = "content" + (opts.narrow ? " narrow" : "") + (opts.contentClass ? " " + opts.contentClass : "");
    content.dataset.route = key;
    content.innerHTML = contentHtml;
    UI.localize(content);
    sheet.scrollTop = keep;
    lastRouteKey = key;
  }

  function errorView(err) {
    layout(`<div class="empty">${esc(UI.error(err.message, err.status))}</div>`);
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
      body: "Creates a new directory with a Git baseline. It never overwrites an existing folder. Starts on the chatgpt-only profile — no Codex or Claude Code install needed; change this later in Settings if you want.",
      fields: [
        { label: "Project ID", placeholder: "hello-demo" },
        { label: "Path (optional)", placeholder: "leave empty to use the default projects directory" },
      ],
      confirmLabel: "Create",
      onSubmit: async ([id, path]) => {
        if (!id) throw new Error("Project ID is required.");
        const created = await api("/projects/create", { method: "POST", body: { id, path: path || undefined } });
        // A new project starts on the chatgpt-only path by default (the documented
        // quickstart route): no separate trip to Settings, no Codex/Claude required.
        try { await api(`/projects/${encodeURIComponent(created.id)}/config/profile`, { method: "POST", body: { profile: "chatgpt-only" } }); }
        catch (_) { /* best-effort; the project still exists and can be configured from Settings */ }
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
        if (result.custom_roles_pending) {
          toast("Project backup imported. Write access is off. Imported roles wait for your review in Role Studio.");
          navTo(`/p/${encodeURIComponent(result.project_id)}/roles`);
        } else {
          toast("Project backup imported. Write access is off.");
          navTo(`/p/${encodeURIComponent(result.project_id)}/overview`);
        }
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
  // The names the team card uses, so a step on the task page is called what the person set up.
  const ROLE_TITLES = {
    SA: "Analyst", SD: "Designer", RD: "Developer", "SD/RD": "Designer and developer in one step",
    REVIEW: "Code reviewer", SR: "Specification reviewer", AR: "Adversarial reviewer",
    SEC: "Security reviewer", QA: "Tester", SW: "Specification writer", PA: "Personal assistant",
    RS: "Researcher", AN: "Data analyst", PL: "Planner", CS: "Consultant", WR: "Writer", ED: "Editor",
    VC: "Fact checker", RV: "Reviewer",
  };
  // Everyday roles are shown by what they do, not by an abbreviation.
  const ROLE_FRIENDLY = { PA: "Personal assistant", RS: "Researcher", AN: "Analyst", PL: "Planner", CS: "Consultant", WR: "Writer", ED: "Editor", VC: "Fact checker", RV: "Reviewer", AR: "Strict reviewer" };
  const EVERYDAY_ROLES = ["PA", "RS", "AN", "PL", "CS", "WR", "ED", "VC", "RV"];
  let activeRoleNames = {};
  // The project's own roles, by id (u-…). A step of a workflow, a chat speaker or an evaluation is
  // named by this id when it is one of them; everything below falls back to the built-in role.
  let customRoleMap = {};
  const isCustomRole = (role) => typeof role === "string" && /^u-[a-z0-9]/.test(role);
  const baseOfRole = (role) => (customRoleMap[role] ? customRoleMap[role].base_role : role);
  const roleTitle = (role) => customRoleMap[role]?.name || activeRoleNames[role] || (ROLE_TITLES[role] ? UI.translate(ROLE_TITLES[role]) : roleName(role));
  const roleName = (role) => customRoleMap[role]?.name || activeRoleNames[role] || (ROLE_FRIENDLY[role] ? UI.translate(ROLE_FRIENDLY[role]) : ROLE_ABBREVIATIONS[role] || role);
  const roleAvatarTone = (role) => (ROLE_AVATARS[baseOfRole(role)] || ROLE_AVATARS.SA)[0];
  // Replace the registry of the project's custom roles; their declared abilities join the chart maths.
  function setCustomRoles(list) {
    customRoleMap = {};
    for (const role of list || []) customRoleMap[role.id] = role;
  }
  // A history entry carries the role's name as it was then; remember it if the role is gone.
  function rememberEventRole(event) {
    if (event.role_ref && !customRoleMap[event.role_ref]) {
      customRoleMap[event.role_ref] = { id: event.role_ref, name: event.role_name || event.role_ref, base_role: event.role, avatar: event.role_avatar || null, status: "unknown" };
    }
  }
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
  const roleAvatarIcon = (role) => spriteFor(customRoleMap[role]?.avatar || (isCustomRole(role) ? DEFAULT_AVATAR[baseOfRole(role)] : activeRoleAvatars[role] || DEFAULT_AVATAR[role]) || "person");
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

  const chatParticipants = (roles) => [...(roles || ["SA", "SD/RD", "REVIEW"]), "PM"].map((role) => `<span class="chat-avatar av-${roleAvatarTone(role)}" title="${esc(roleTitle(role))}">${roleAvatarIcon(role).replace('width="18" height="18"', 'width="15" height="15"')}</span>`).join("");
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
    xcircle: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/>',
    neutral: '<circle cx="12" cy="12" r="10"/><path d="M8 12h8"/>',
    flag: '<path d="M4 22V4"/><path d="M4 4h14l-3 4 3 4H4"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
  };
  const pageIcon = (name) => `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PAGE_ICONS[name]}</svg>`;
  // One titled card: icon + heading + description, a body, and an optional footer / header actions.
  // `title` and `description` are plain English strings; the DOM localizer translates them.
  const pageCard = (icon, title, description, body, footer, opts = {}) => `<section class="settings-card${opts.tone ? " is-" + opts.tone : ""}">
      <header class="settings-card-head"><span class="settings-card-icon">${pageIcon(icon)}</span><div><h2>${title}</h2>${description ? `<p>${description}</p>` : ""}</div>${opts.actions ? `<div class="settings-card-actions">${opts.actions}</div>` : ""}</header>
      <div class="settings-card-body${opts.flush ? " flush" : ""}">${body}</div>${footer ? `<footer class="settings-card-foot">${footer}</footer>` : ""}
    </section>`;

  function projectSettingsHtml(overview) {
    const runtime = overview.editable.runtime || {};
    return `
        ${teamUi.html(overview)}
        ${pageCard("test", "Test command", "After each change, ccc-mcp runs the tests on this computer. The result comes from the test run, never from an AI's word.",
          `<div class="field">
            <input type="text" id="test-command" class="mono" value="${esc((overview.editable.test_command || []).join(" "))}" placeholder="e.g. python -m pytest -q" aria-label="${esc(UI.translate("Test command"))}" />
            <div class="hint">Leave blank to test changed Python test files automatically. Goals that run on their own need a command here.</div>
          </div>`, `<button class="btn small primary" id="save-test-command">Save</button>`)}
        <div id="project-runtime">
        ${pageCard("runtime", "Language and version for tests", "Choose which programming language and version the host uses to install packages and run this project's tests. Leave the language on the default to use ccc-mcp's own Python.",
          `<div class="settings-grid">
            <div class="field">
              <label for="runtime-language">Programming language</label>
              <select id="runtime-language">
                <option value="" ${!runtime.language ? "selected" : ""}>Default environment</option>
                ${Object.entries(overview.available_runtimes || {}).map(([key, label]) => `<option value="${esc(key)}" ${runtime.language === key ? "selected" : ""}>${esc(label)}</option>`).join("")}
              </select>
            </div>
            <div class="field">
              <label for="runtime-found">Installed on this computer</label>
              <select id="runtime-found" disabled><option value="">${esc(UI.translate("Pick a language first"))}</option></select>
              <div class="hint" id="runtime-scan-note" role="status" aria-live="polite"></div>
            </div>
          </div>
          <div class="settings-grid">
            <div class="field">
              <label for="runtime-version">Runtime version</label>
              <input type="text" id="runtime-version" value="${esc(runtime.version)}" placeholder="e.g. 3.9 or 22.14.0" autocomplete="off" />
              <div class="hint">3.9 accepts any 3.9.x patch. JavaScript and TypeScript use the Node.js version.</div>
            </div>
            <div class="field">
              <label for="runtime-executable">Local executable (optional)</label>
              <input type="text" id="runtime-executable" class="mono" value="${esc(runtime.executable)}" placeholder="e.g. C:/Python39/python.exe" autocomplete="off" />
              <div class="hint">Absolute executable path or command name, without arguments. Leave empty to find an installed runtime.</div>
            </div>
          </div>
          <div class="field">
            <label class="check-row"><input type="checkbox" id="runtime-install" ${runtime.install_packages === false ? "" : "checked"} /> ${esc(UI.translate("Install the project's packages before testing"))}</label>
            <div class="hint">${esc(UI.translate("Turn this off to use the packages already installed in the chosen environment, for example a conda environment you set up yourself. Nothing is installed or built."))}</div>
          </div>
          <div id="runtime-status" class="hint settings-status" role="status" aria-live="polite"></div>`,
          `<button class="btn small" id="rescan-runtime">Scan this computer</button><button class="btn small" id="check-runtime">Check local version</button><button class="btn small primary" id="save-runtime">Save runtime</button>`)}
        </div>
        ${pageCard("language", "Language", "The language the AI team writes its reports in.",
          `<div class="field narrow-field"><select id="language-select" aria-label="${esc(UI.translate("Language"))}">
            ${[["zh-TW", "繁體中文"], ["ch", "简体中文"], ["en", "English"]].map(([l, name]) => `<option value="${l}" ${overview.editable.language === l ? "selected" : ""} data-no-i18n>${name}</option>`).join("")}
          </select></div>`, `<button class="btn small primary" id="save-language">Save</button>`)}`;
  }

  // A role that came in with an imported backup stays off until a person trusts it, here, by typing the
  // phrase for that exact version. This is the only place that can; roles are no longer created here.
  function pendingRolesHtml(roles) {
    return pageCard("shield", "Imported roles waiting for you", "These roles came with a backup and do nothing until you trust them. What a role may do is decided by ccc-mcp's own code for its base role, never by its text.",
      roles.map((role) => `<div class="kv-row" data-key="role:${esc(role.id)}">
        <span class="k"><strong data-no-i18n>${esc(role.name)}</strong> <span class="pill neutral" data-no-i18n>${esc(role.permissions.base_role)}</span>
          <span class="pill ${role.permissions.may_write_files ? "warning" : "positive"}">${esc(UI.translate(role.permissions.may_write_files ? "can change files" : "read-only"))}</span></span>
        <span class="v"><button class="btn small primary" data-review-role="${esc(role.id)}">${esc(UI.translate("Review and trust"))}</button></span>
      </div>`).join(""));
  }

  function bindPendingRoles(id, roles, done) {
    document.querySelectorAll("[data-review-role]").forEach((button) => {
      button.onclick = () => {
        const role = roles.find((r) => r.id === button.dataset.reviewRole);
        if (!role) return;
        showModal({
          title: "Trust this imported role",
          body: `${role.name} (${role.permissions.base_role}, ${role.permissions.may_write_files ? UI.translate("can change files") : UI.translate("read-only")})\n\n${UI.translate("From")} · ${role.source.source_project_id || "?"} · ${role.source.exported_at || ""}\n\n${String(typeof role.instruction === "string" ? role.instruction : JSON.stringify(role.instruction || ""))}`,
          fields: [{ label: `${UI.translate("Type")} ${role.phrase} ${UI.translate("to confirm")}`, placeholder: role.phrase }],
          confirmLabel: "Trust this role",
          onSubmit: async ([typed]) => {
            await api(`/projects/${encodeURIComponent(id)}/roles/${encodeURIComponent(role.id)}/confirm-import`, { method: "POST", body: { version: role.version, version_sha256: role.version_sha256, confirmation: typed } });
            toast("Role trusted.");
            button.closest("[data-key]")?.remove();
            teamUi.refreshRoles?.(id);
            done();
          },
        });
      };
    });
  }

  function bindProjectSettings(id, gen, overview) {
    teamUi.bind(id, overview);
    const base = `/projects/${encodeURIComponent(id)}/config`;
    document.getElementById("save-language").onclick = async () => {
      try { await api(`${base}/language`, { method: "POST", body: { language: document.getElementById("language-select").value } }); toast("Saved."); }
      catch (err) { toast(err.message, true); }
    };
    document.getElementById("save-test-command").onclick = async () => {
      const typed = document.getElementById("test-command").value.trim();
      try { await api(`${base}/test-command`, { method: "POST", body: { command: typed ? typed.split(/\s+/) : null } }); toast("Saved."); }
      catch (err) { toast(err.message, true); }
    };
    const status = document.getElementById("runtime-status");
    const language = document.getElementById("runtime-language");
    const selection = () => (language.value ? {
      language: language.value,
      version: document.getElementById("runtime-version").value.trim() || null,
      executable: document.getElementById("runtime-executable").value.trim() || null,
      ...(document.getElementById("runtime-install").checked ? {} : { install_packages: false }),
    } : null);
    const fieldsChanged = () => {
      document.getElementById("runtime-version").disabled = !language.value;
      document.getElementById("runtime-executable").disabled = !language.value;
      document.getElementById("runtime-install").disabled = !language.value;
      status.textContent = "";
    };
    fieldsChanged();
    // Looking at what is installed is read-only; typing a version and path by hand always still works.
    const versionInput = document.getElementById("runtime-version");
    const executableInput = document.getElementById("runtime-executable");
    const found = document.getElementById("runtime-found");
    const scanNote = document.getElementById("runtime-scan-note");
    let scanned = [];
    const syncFound = () => {
      const index = scanned.findIndex((r) => r.executable === executableInput.value.trim());
      found.value = index >= 0 ? String(index) : "";
    };
    const renderFound = () => {
      found.innerHTML = `<option value="">${esc(UI.translate("Enter manually"))}</option>` +
        scanned.map((r, i) => `<option value="${i}" title="${esc(r.executable)}" data-no-i18n>${esc(r.version)} · ${esc(r.label)}</option>`).join("");
      syncFound();
    };
    const scan = async () => {
      if (!language.value) {
        scanned = [];
        found.innerHTML = `<option value="">${esc(UI.translate("Pick a language first"))}</option>`;
        found.disabled = true;
        scanNote.textContent = "";
        return;
      }
      scanNote.textContent = UI.translate("Scanning this computer…");
      try {
        const data = await api(`/runtimes/discover?language=${encodeURIComponent(language.value)}`);
        if (stale(gen)) return;
        scanned = data.runtimes || [];
        renderFound();
        found.disabled = false;
        scanNote.textContent = scanned.length
          ? `${UI.translate("Found on this computer")}: ${scanned.length}`
          : UI.translate("Nothing found. Type the version and path yourself.");
      } catch (err) { if (!stale(gen)) scanNote.textContent = UI.error(err.message, err.status); }
    };
    found.onchange = () => {
      const picked = scanned[Number(found.value)];
      if (found.value === "" || !picked) return;
      versionInput.value = picked.short_version;
      executableInput.value = picked.executable;
      status.textContent = "";
    };
    executableInput.addEventListener("input", syncFound);
    document.getElementById("rescan-runtime").onclick = scan;
    language.onchange = () => { versionInput.value = ""; executableInput.value = ""; fieldsChanged(); scan(); };
    scan();
    document.getElementById("save-runtime").onclick = async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await api(`${base}/runtime`, { method: "POST", body: { runtime: selection() } });
        if (!stale(gen)) status.textContent = UI.translate("Runtime saved. Check the local version to verify availability.");
        toast("Saved.");
      } catch (err) { toast(err.message, true); } finally { button.disabled = false; }
    };
    document.getElementById("check-runtime").onclick = async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      status.textContent = UI.translate("Checking…");
      try {
        const chosen = selection();
        if (!chosen) { status.textContent = UI.translate("Default environment; no runtime version selected."); return; }
        const result = await api(`${base}/runtime/check`, { method: "POST", body: { runtime: chosen } });
        if (stale(gen)) return;
        status.textContent = result.matches && result.available
          ? `${UI.translate("Runtime available")}: ${result.actual_version} · ${result.executable}`
          : `${UI.translate("Runtime unavailable or version mismatch")}: ${UI.error(result.reason)}`;
      } catch (err) { if (!stale(gen)) status.textContent = UI.error(err.message, err.status); } finally { button.disabled = false; }
    };
  }

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
    let settings = null;
    try { settings = await api(`/projects/${encodeURIComponent(id)}/settings`); } catch (err) { settings = null; }
    let pendingRoles = [];
    try { pendingRoles = (await api(`/projects/${encodeURIComponent(id)}/roles/pending`)).pending; } catch (err) { pendingRoles = []; }
    if (stale(gen)) return;
    const finished = tasks.filter((t) => t.state === "COMPLETED").length;
    const running = tasks.filter((t) => !["COMPLETED", "CANCELLED"].includes(t.state)).length;
    const latest = [...tasks].sort((a, b) => String(a.created || "").localeCompare(String(b.created || ""))).at(-1);
    const dangerRow = (title, description, actions, hint) => `<div class="danger-row"><div><strong>${title}</strong><p>${description}</p>${hint || ""}</div><div class="danger-row-actions">${actions}</div></div>`;
    layout(`
      <div class="title-row">
        <div><h1>${esc(id)}</h1><div class="sub path-line"><span class="mono" data-no-i18n>${esc(project.root)}</span><button type="button" class="btn small ghost" id="btn-copy-path">Copy path</button></div></div>
        <div class="title-actions">
          <a class="btn primary" href="#/p/${encodeURIComponent(id)}/tasks">View tasks</a>
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
        ${settings ? projectSettingsHtml(settings) : ""}
        ${pendingRoles.length ? `<div id="pending-team-roles">${pendingRolesHtml(pendingRoles)}</div>` : ""}
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
    if (settings) bindProjectSettings(id, gen, settings);
    if (focusRuntime) {
      focusRuntime = false;
      document.getElementById("project-runtime")?.scrollIntoView({ block: "start" });
    }
    bindPendingRoles(id, pendingRoles, () => {
      const section = document.getElementById("pending-team-roles");
      if (!section?.querySelector("[data-review-role]")) section?.remove();
    });
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
    return `${esc(UI.translate(label))} · <span class="mono">${esc(deps.manifest || "")}</span>${deps.reason ? ` · ${esc(UI.error(deps.reason))}` : ""}`;
  }

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
  // The host could not install the project's packages, so no test ever ran.
  function setupFailed(detail) {
    const deps = detail && detail.dependencies;
    return Boolean(deps && deps.detected && deps.installed === false);
  }

  function setupErrorTail(detail) {
    const lines = String((detail.dependencies || {}).stderr || "").split("\n").map((line) => line.trim()).filter(Boolean);
    return lines.slice(-6).join("\n").slice(-600);
  }

  function hostTestFacts(detail) {
    if (!detail) return [];
    const facts = [];
    if (setupFailed(detail)) {
      facts.push(["Result", statusPill("negative", "Test environment setup failed"), "status"]);
      const projectId = (location.hash.match(/#\/p\/([^/]+)\//) || [])[1];
      if (projectId) facts.push(["Fix", `<a class="btn small primary" href="#/p/${projectId}/overview" data-focus-runtime>${esc(UI.translate("Open the language and version setting"))}</a>`, "status"]);
      facts.push(["Reason", UI.translate("The host could not install this project's packages, so no test was run. This is usually a package that does not support the Python version in use. Set the project's runtime (Project settings, Runtime for tests) to the Python version the project needs, then run the tests again.")]);
      const tail = setupErrorTail(detail);
      if (tail) facts.push(["Last installer message", tail, "code"]);
      return facts;
    }
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
    overlay.querySelector("[data-focus-runtime]")?.addEventListener("click", () => { focusRuntime = true; closeModal(); });
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
    const closedByUserEvent = type === "task_completed" && /^Closed by the user/.test(event.summary || "");
    const isUser = type === "task_created" || type === "user_message" || closedByUserEvent;
    const isAgent = ["agent_started", "agent_completed", "agent_failed", "role_returned"].includes(type);
    const isPm = type === "task_completed" && !closedByUserEvent;
    const role = isUser ? "You" : isPm ? "PM" : event.role || "ccc-mcp";
    const label = isUser ? (type === "task_created" ? "Your request" : closedByUserEvent ? "Your decision" : "Your correction") :
      isAgent || isPm ? TASK_ROLE_LABELS[role] || "Work complete" :
      type === "host_test_completed" ? "Host tests" :
      type === "pause_requested" ? "Pause requested" :
      type === "task_paused" ? "Task paused" :
      type === "resume_requested" ? "Work resumed" :
      type === "cancel_requested" ? "Stop requested" :
      type === "task_cancelled" ? "Task stopped" : "System update";
    let summary = event.summary || "";
    if (type === "agent_started") summary = "Execution dispatched; waiting for the result.";
    const closedNote = closedByUserEvent ? (event.summary || "").replace(/^Closed by the user:?\s*/, "") : "";
    if (closedByUserEvent) summary = UI.translate("You closed this task. It was not verified by host tests.") + (closedNote ? `\n${UI.translate("Your note:")} ${closedNote}` : "");
    else if (type === "task_completed") summary = event.legacy ?
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
          event.detail.passed ? "The host ran the test command and it passed." :
            setupFailed(event.detail) ? "The test environment could not be set up, so no test was run." : "The host ran the test command and it failed.") :
        `${UI.translate("Host verification is not complete:")} ${event.detail?.reason || UI.translate("no test was run")}`;
    }
    if (type === "agent_failed" && !summary) summary = "This execution ended without a verified result.";
    // Known failure reasons are explained in plain words instead of the raw English error.
    if (summary && /possible secret detected|execution timed out|stopped by user|execution cancelled|executable unavailable/i.test(summary)) summary = UI.error(summary);
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
    if (!isUser && !isPm) rememberEventRole(event);
    const speaker = isUser ? "USER" : (!isPm && event.role_ref) || role;
    const avatar = `<div class="chat-avatar av-${roleAvatarTone(speaker)}" aria-hidden="true">${roleAvatarIcon(speaker)}</div>`;
    const speakerName = isUser ? UI.translate("You") : (!isPm && event.role_name && (event.role_ref ? event.role_name : UI.translate(event.role_name))) || roleTitle(speaker);
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
      api(taskBase), api(`${taskBase}/timeline`),
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
    const [status, timeline, artifacts, settings, handoff] = responses.map((result) => result.value);
    // Only tasks that run pinned custom roles have anything to report; an older server has no route.
    let contract = null;
    try { contract = await api(`${taskBase}/contract`); } catch (_) { contract = null; }
    return { data: { status, timeline, artifacts, settings, handoff, contract } };
  }

  // What counts as "something changed" for the live conversation. Fields that tick every
  // second while an agent runs (elapsed time, the waiting note) are left out on purpose, or
  // the page would rebuild itself on every poll.
  // Provider Switching visibility (minimal, read-only addition): one card per recorded
  // quota-exhaustion failure, built only from already-fetched timeline events and
  // status.executions/brief -- the same `failure_kind`/`fallback`/`agent`/`execution_id`/
  // `waiting_on` fields get_task_result and get_task_status already return. Never reads
  // the role's *current* configured provider for this: "from"/"to" always come from the
  // historical execution rows themselves, so a later manual `config role` change can never
  // be mistaken for what actually ran. A field this cannot confirm renders as "Unconfirmed"
  // rather than a guess.
  function providerSwitchCard(events, status) {
    const quotaEvents = (events || []).filter(
      (e) => e.event_type === "agent_failed" && e.detail?.failure_kind === "QUOTA_EXHAUSTED"
    );
    if (!quotaEvents.length) return "";
    const executionsById = new Map((status.executions || []).map((e) => [e.id, e]));
    const latestExecutionId = (status.executions || []).at(-1)?.id;
    const brief = status.brief || {};
    const unconfirmed = () => UI.translate("Unconfirmed");
    const cards = quotaEvents.map((event) => {
      const from = event.actor || unconfirmed();
      const reasonLabel = esc(UI.translate("Quota exhausted"));
      const fallback = event.detail.fallback;
      const head = `<span class="pill warning">${reasonLabel}</span>`;
      if (!fallback) {
        return `<div class="provider-switch-card">${head}<p><strong data-no-i18n>${esc(from)}</strong> — ${esc(UI.translate("no fallback provider is configured for this role"))}</p></div>`;
      }
      const to = fallback.target || unconfirmed();
      const arrow = `<strong data-no-i18n>${esc(from)}</strong> → <strong data-no-i18n>${esc(to)}</strong>`;
      if (!fallback.attempted) {
        return `<div class="provider-switch-card">${head}<p>${arrow}: ${esc(UI.translate("not switched"))} — ${esc(UI.error(fallback.reason || unconfirmed()))}</p></div>`;
      }
      if (!fallback.dispatched) {
        return `<div class="provider-switch-card"><span class="pill negative">${reasonLabel}</span><p>${arrow}: ${esc(UI.translate("fallback dispatch failed"))} — ${esc(UI.error(fallback.failed_reason || unconfirmed()))}</p></div>`;
      }
      const fallbackExec = fallback.fallback_execution_id ? executionsById.get(fallback.fallback_execution_id) : null;
      let stateLabel = unconfirmed();
      let stateTone = "warning";
      if (fallbackExec) {
        if (fallbackExec.status === "RUNNING" && brief.waiting_on === "CHATGPT_HANDOFF_PENDING" && brief.agent === to) {
          stateLabel = UI.translate("Waiting for a human to hand off");
        } else if (["RUNNING", "QUEUED"].includes(fallbackExec.status)) {
          stateLabel = UI.translate("In progress");
        } else if (fallbackExec.status === "SUCCEEDED") {
          stateLabel = UI.translate("Completed");
          stateTone = "positive";
        } else if (fallbackExec.status === "FAILED") {
          stateLabel = UI.translate("Fallback also failed");
          stateTone = "negative";
        } else if (fallbackExec.status === "CANCELLED") {
          stateLabel = UI.translate("Cancelled");
        }
      }
      const verification = fallback.fallback_execution_id === latestExecutionId && brief.verification?.message
        ? `<p class="muted">${esc(brief.verification.message)}</p>` : "";
      return `<div class="provider-switch-card">${head}<p>${arrow}: ${esc(UI.translate("automatically dispatched"))}</p><p class="muted">${esc(UI.translate("Current status"))}: <span class="pill ${stateTone}">${esc(stateLabel)}</span>${fallback.fallback_execution_id ? ` <span class="mono">${esc(fallback.fallback_execution_id)}</span>` : ""}</p>${verification}</div>`;
    }).join("");
    return `<section class="task-result-card provider-switch-section"><h2>${esc(UI.translate("Provider switching"))}</h2>${cards}</section>`;
  }

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
  // ---- Handoff panel (任務 → 技術紀錄 → 交給另一個 AI) ----
  // The chosen AI and its prepared handoff survive re-renders of the technical pane. They
  // are keyed by task, so another task never shows this content. The copy button copies
  // the full handoff for the new AI; the page only shows the few lines a person needs.
  let taskDetailPane = "conversation";
  let taskDetailDraft = "";
  let taskDetailRoute = "";
  let handoffView = null;
  const HANDOFF_TARGETS = [
    ["claude", "Hand off to Claude Code"],
    ["codex", "Hand off to Codex CLI"],
    ["chatgpt", "Hand off to ChatGPT"],
  ];
  const HANDOFF_TAG = /^\[([^\]]*)\]\s*/;
  const HANDOFF_FILE_ROW = /^(\?\?|[ MADRCU?]{1,2})\s+(\S.*)$/;

  // Reads the handoff text (plain markdown-like lines) into sections of blocks.
  function parseHandoffText(text) {
    const sections = [];
    let section = null;
    let fence = null;
    for (const line of String(text || "").replace(/\r/g, "").split("\n")) {
      if (fence) {
        if (line.startsWith("```")) {
          section?.blocks.push({ type: "fence", lines: fence });
          fence = null;
        } else {
          fence.push(line);
        }
      } else if (line.startsWith("```")) {
        fence = [];
      } else if (line.startsWith("## ")) {
        section = { title: line.slice(3).trim(), blocks: [] };
        sections.push(section);
      } else if (section && line.trim()) {
        const item = line.match(/^\s*-\s+(.*)$/);
        section.blocks.push({ type: item ? "item" : "text", text: (item ? item[1] : line).trim() });
      }
    }
    return sections;
  }

  // Short, readable lines from one section. Source tags such as "[主機證據]" are kept
  // only where they change the meaning, as a "待確認" prefix.
  function handoffSectionLines(section) {
    if (!section) return [];
    return section.blocks
      .filter((block) => block.type !== "fence")
      .map((block) => {
        const tag = block.text.match(HANDOFF_TAG);
        const body = block.text.replace(HANDOFF_TAG, "");
        return tag && /待確認|to confirm/.test(tag[1]) ? `${UI.translate("To confirm")}: ${body}` : body;
      });
  }

  function handoffChangedFiles(section) {
    const rows = (section?.blocks.find((block) => block.type === "fence")?.lines || []).map((row) => row.trim());
    return rows.filter((row) => HANDOFF_FILE_ROW.test(row)).map((row) => row.match(HANDOFF_FILE_ROW)[2]);
  }

  function handoffReadyHtml(view, signature) {
    const sections = parseHandoffText(view.text);
    const find = (pattern) => sections.find((section) => pattern.test(section.title));
    const next = handoffSectionLines(find(/下一個具體動作|Next concrete action/))[0];
    const remaining = handoffSectionLines(find(/剩餘工作|Remaining work/)).slice(0, 4);
    const test = handoffSectionLines(find(/主機驗證狀態|Host verification/))[0];
    const files = handoffChangedFiles(find(/不可破壞|Existing changes/));
    const list = (items, empty) => items.length
      ? `<ul class="handoff-list">${items.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`
      : `<span class="muted">${esc(empty)}</span>`;
    const stale = view.signature !== signature
      ? `<p class="handoff-stale" role="status">${esc(UI.translate("The task has new progress since this was prepared. Choose an AI again to refresh it."))}</p>`
      : "";
    return `<div class="handoff-ready">`
      + `<div class="handoff-ready-head"><p class="handoff-ready-title">${esc(UI.translate("Ready to paste into the new AI session."))} `
      + `<span class="muted">${esc(view.generatedAt)}</span></p>`
      + `<button class="btn small primary" type="button" id="task-handoff-copy">${esc(UI.translate("Copy for the new session"))}</button></div>`
      + `${stale}`
      + `<dl class="handoff-summary">`
      + `<div><dt>${esc(UI.translate("Next step"))}</dt><dd>${esc(next || UI.translate("Nothing recorded"))}</dd></div>`
      + `<div><dt>${esc(UI.translate("Still to do"))}</dt><dd>${list(remaining, UI.translate("Nothing recorded"))}</dd></div>`
      + `<div><dt>${esc(UI.translate("Test result"))}</dt><dd>${esc(test || UI.translate("Nothing recorded"))}</dd></div>`
      + `<div><dt>${esc(UI.translate("Uncommitted changes"))}</dt><dd>${list(files, UI.translate("No uncommitted changes"))}</dd></div>`
      + `</dl></div>`;
  }

  function handoffPanelHtml(key, signature) {
    const view = handoffView && handoffView.key === key ? handoffView : null;
    const choices = HANDOFF_TARGETS.map(([targetId, label]) => {
      const selected = view?.target === targetId;
      return `<button class="btn small handoff-choice${selected ? " is-selected" : ""}" type="button" `
        + `data-handoff-target="${targetId}" aria-pressed="${selected}">${esc(UI.translate(label))}</button>`;
    }).join("");
    let result = "";
    if (view?.status === "loading") {
      result = `<p class="handoff-status" role="status" aria-busy="true">${esc(UI.translate("Preparing the handoff from the saved task state…"))}</p>`;
    } else if (view?.status === "error") {
      result = `<p class="handoff-status is-error" role="alert">${esc(UI.translate("Could not prepare the handoff."))} ${esc(UI.error(view.error))}</p>`;
    } else if (view?.status === "ready") {
      result = handoffReadyHtml(view, signature);
    }
    return `<div class="continuation-panel" id="task-continuation-panel">`
      + `<div class="continuation-buttons" role="group" aria-label="${esc(UI.translate("Hand off to another AI"))}">${choices}</div>`
      + `<div id="task-continuation-result" aria-live="polite">${result}</div></div>`;
  }

  function buildTaskParts(id, taskId, data) {
    const { status, timeline, artifacts, settings, handoff } = data;
    activeRoleNames = settings?.role_names || settings?.editable?.role_names || {};
    activeRoleAvatars = settings?.role_avatars || settings?.editable?.role_avatars || {};
    setCustomRoles(settings?.custom_roles || []);
    for (const detail of status.brief?.workflow?.steps_detail || []) {
      if (detail.role_ref) rememberEventRole({ role_ref: detail.role_ref, role_name: detail.name, role: detail.role, role_avatar: detail.avatar });
    }
    const brief = status.brief || {};
    const control = brief.control || { status: "ACTIVE" };
    const terminal = ["COMPLETED", "CANCELLED", "REJECTED"].includes(status.state);
    // BLOCKED because ccc-mcp could not confirm some changed files' comments is a request for a look,
    // not a failure; say so instead of the alarming "Blocked".
    const review = status.brief?.comment_review;
    const commentReview = review && (review.violations?.length || review.needs_human_review?.length) ? review : null;
    const closedByUser = status.state === "COMPLETED" && (timeline.events || []).some((e) => e.event_type === "task_completed" && /^Closed by the user/.test(e.summary || ""));
    const displayState = control.status !== "ACTIVE" ? control.status : closedByUser ? "CLOSED_BY_USER" : commentReview && status.state === "BLOCKED" ? "NEEDS_CONFIRMATION" : status.state;
    const stateTone = ["FAILED", "BLOCKED", "CANCELLED", "STOPPING"].includes(displayState) ? "negative" : displayState === "NEEDS_CONFIRMATION" ? "warning" :
      ["COMPLETED"].includes(displayState) ? "positive" : "warning";
    const events = timeline.events || [];
    const typingIds = new Set((status.executions || []).filter((e) => ["RUNNING", "QUEUED"].includes(e.status)).map((e) => e.id));
    const doneRoles = new Set(events.filter((e) => e.event_type === "agent_completed").map((e) => e.role));
    const workflow = brief.workflow || {};
    const flowSteps = Array.isArray(workflow.steps) ? workflow.steps : null;
    // Step rows exist for tasks on the step engine; older tasks only have their executions to go by.
    const flowDone = (index) => status.state === "COMPLETED" || (workflow.current_step != null
      ? (index < workflow.current_step || (index === workflow.current_step && workflow.current_step_done))
      : Boolean(flowSteps) && doneRoles.has(flowSteps[index]));
    const steps = flowSteps ? flowSteps.map((role, index) => [roleTitle(role), flowDone(index)]) : [
      ["Analysis", doneRoles.has("SA")],
      ["Development", ["SD", "RD", "SD/RD"].some((r) => doneRoles.has(r))],
      ["Host tests", events.some((e) => e.event_type === "host_test_completed" && e.detail?.executed_by_host)],
      ["Review", doneRoles.has("REVIEW")],
    ];
    // Who has the ball. After a step finishes nothing runs until ChatGPT (the PM) says go on; saying that
    // plainly is what keeps "SD has not started" from looking like a hang.
    const runningExecution = (status.executions || []).find((e) => ["RUNNING", "QUEUED"].includes(e.status));
    // A role that is running right now outranks an old "the next step was never started" record, so the
    // typing indicator and the card below it can never disagree.
    const waitingOn = handoff.waiting_on === "DISPATCH_RETRY_NEEDED" && runningExecution ? "AGENT_RUNNING" : handoff.waiting_on;
    const runningIndex = flowSteps && runningExecution ? flowSteps.indexOf(runningExecution.role_ref || runningExecution.role) : -1;
    const upcomingIndex = waitingOn === "AGENT_RUNNING" && runningIndex >= 0 ? runningIndex
      : flowSteps ? flowSteps.findIndex((_, index) => !flowDone(index)) : -1;
    const upcomingRole = upcomingIndex >= 0 ? roleTitle(flowSteps[upcomingIndex]) : "";
    const finishedRole = upcomingIndex > 0 ? roleTitle(flowSteps[upcomingIndex - 1]) : "";
    const waitingKind = terminal || control.paused ? "" : ({
      PM_DECISION: "pm", PM_REFINEMENT: "pm", PM_CLARIFICATION_DECISION: "question",
      CHATGPT_HANDOFF_PENDING: "handoff", DISPATCH_RETRY_NEEDED: "retry", AGENT_RUNNING: "running",
    })[waitingOn] || "";
    const fill = (text, values) => Object.entries(values).reduce((out, [key, value]) => out.replaceAll(`{${key}}`, value), UI.translate(text));
    const waitingNote = !waitingKind ? null : {
      pm: [finishedRole && upcomingRole ? fill("Waiting for ChatGPT (the PM) to start {next}", { next: upcomingRole }) : "Waiting for ChatGPT (the PM)",
        `${finishedRole ? `${fill("{done} has finished.", { done: finishedRole })} ` : ""}${UI.translate("Nothing is running right now. ccc-mcp does not start the next step by itself: ChatGPT reviews the result and tells it to go on. Open your ChatGPT conversation and ask it to continue.")}`],
      question: ["ChatGPT needs to answer a question first", "A role asked something only the PM can answer. Ask ChatGPT to answer it so the work can go on."],
      handoff: [upcomingRole ? fill("ChatGPT has to do {role} itself", { role: upcomingRole }) : "ChatGPT has to do the next step itself", "Open your ChatGPT conversation: it writes the result and reports it back here."],
      retry: [upcomingRole ? fill("{role} did not start", { role: upcomingRole }) : "The next step did not start", "The hand-over to the next step was interrupted, so nothing is running. You can start it again here."],
      running: [upcomingRole ? fill("{role} is working now", { role: upcomingRole }) : "A role is working now", "It runs in the background. This page updates by itself."],
    }[waitingKind];
    const driftSteps = (data.contract?.steps || []).filter((step) => step.status === "incompatible" && !step.accepted);
    const removedFiles = commentReview?.violations || [];
    const unverifiedFiles = commentReview?.needs_human_review || [];
    const fileList = (files) => `<p class="mono" data-no-i18n>${files.map(esc).join("<br>")}</p>`;
    const reviewCard = commentReview ? `<div class="task-next waiting handoff"><strong>${esc(UI.translate(removedFiles.length ? "Some existing comments were removed" : "Some changed files need your look"))}</strong>${removedFiles.length ? `<p>${esc(UI.translate("A Python comment that was there before is gone from:"))}</p>${fileList(removedFiles)}<p>${esc(UI.translate("Ask the developer to redo it and keep the original comments: pick Developer below and write which file."))}</p>` : ""}${unverifiedFiles.length ? `<p>${esc(UI.translate(removedFiles.length ? "ccc-mcp cannot check these changed files, so a person has to look:" : "ccc-mcp can only check that Python comments were kept. It cannot check these changed files, so it paused for a person to look:"))}</p>${fileList(unverifiedFiles)}` : ""}${removedFiles.length ? "" : `<p>${esc(UI.translate("Open the Results tab to see what changed. If it is fine, ask ChatGPT to carry on with the next step."))}</p>`}</div>` : "";
    // Nothing else runs the host tests when the PM skipped that step, so the page can start them: no
    // terminal, no copying a script. Offered once a developer step is done and until a run passes.
    const hostTestEvents = events.filter((e) => e.event_type === "host_test_completed");
    const lastHostTest = hostTestEvents[hostTestEvents.length - 1];
    const testsPassed = Boolean(lastHostTest?.detail?.executed_by_host && lastHostTest.detail.passed && !lastHostTest.detail.no_tests_collected);
    const finalState = ["COMPLETED", "CANCELLED", "FAILED", "REJECTED"].includes(status.state);
    const developerDone = ["SD", "RD", "SD/RD"].some((role) => doneRoles.has(role));
    const kickedAt = hostTestKickoff[`${id}/${taskId}`];
    // The server says whether tests are running (so a reload or a second tab agrees); the click time only
    // covers the moment before the server has recorded it.
    const testsRunning = Boolean(control.host_test_running || (kickedAt && Date.now() - kickedAt < 20000 && (!lastHostTest || Date.parse(lastHostTest.timestamp || 0) < kickedAt)));
    const testStage = control.host_test_stage ? { installing: "Installing the packages the project needs. The first run takes longer, often a few minutes.", testing: "Running the tests now.", starting: "Getting ready." }[control.host_test_stage] : "";
    const testStarted = control.host_test_started ? new Date(control.host_test_started).toLocaleTimeString(UI.getLocale()) : "";
    const canRunTests = !control.stop_requested && !control.paused;
    // What still stands between this task and "done", in plain words. "PM" is ChatGPT, not the person
    // reading this, so their own OK, or a test they ran by hand, is not something ccc-mcp can count.
    const completions = events.filter((e) => e.event_type === "agent_completed");
    const lastCompletion = (roles) => { for (let i = completions.length - 1; i >= 0; i--) if (roles.includes(completions[i].role)) return i; return -1; };
    const lastDeveloper = lastCompletion(["SD", "RD", "SD/RD"]);
    const lastReview = lastCompletion(["REVIEW"]);
    const reviewedLatest = lastDeveloper >= 0 && lastReview > lastDeveloper;
    const reviewWantsFix = reviewedLatest && Boolean(completions[lastReview].detail?.report?.needs_revision);
    const reviewPassed = reviewedLatest && !reviewWantsFix;
    const testCardState = developerDone && !finalState ? `${testsPassed ? 1 : 0}${reviewPassed ? 1 : 0}${reviewWantsFix ? "f" : ""}${testsRunning ? "r" : ""}${lastHostTest ? "t" : ""}` : "";
    const showRunButton = !testsPassed && !testsRunning && canRunTests && !runningExecution;
    const checkItems = [
      [testsPassed, testsPassed ? "The host tests passed" : testsRunning ? "The host tests are running" : lastHostTest ? "The last host test run did not pass" : "The host tests have not been run yet. A test you ran yourself in a terminal does not count."],
      [reviewPassed, reviewPassed ? "The reviewer passed the latest changes" : reviewWantsFix ? "The reviewer asked for more changes" : "The reviewer has not looked at the latest changes yet"],
      [false, "ChatGPT (the PM) records the result in the project"],
    ];
    const testCard = testCardState ? `<div class="task-next waiting ${testsRunning ? "running" : "handoff"}"><strong>${esc(UI.translate("What is still missing before this task can finish"))}</strong><ul class="done-checks">${checkItems.map(([ok, text]) => `<li class="${ok ? "ok" : "todo"}"><span aria-hidden="true">${ok ? "✓" : "○"}</span><span>${esc(UI.translate(text))}</span></li>`).join("")}</ul>${testsRunning ? `<p>${esc(UI.translate(testStage || "Getting ready."))}${testStarted ? ` <small>${esc(UI.translate("Started at"))} ${esc(testStarted)}</small>` : ""}</p>` : ""}${showRunButton ? `<button class="btn small primary" type="button" id="task-run-tests">${esc(UI.translate("Run host tests"))}</button> ` : ""}<button class="btn small" type="button" data-close-task>${esc(UI.translate("Close the task anyway"))}</button><p class="note-muted">${esc(UI.translate("\"PM\" means ChatGPT, not you. Your own OK does not finish the task: ccc-mcp needs the test and review results above."))}</p></div>` : "";
    const driftCard = driftSteps.length && !terminal ? `<div class="task-next drift-card" role="alert"><strong>${esc(UI.translate("A fixed rule changed for this task"))}</strong>
      <p>${esc(UI.translate("Work stopped before the next step because ccc-mcp's own fixed rules changed in a way that may change what a custom role means. Nothing was run. Review the change, then accept it for this task or stop the task."))}</p>
      <ul class="drift-steps">${driftSteps.map((step) => `<li><span class="pill warning" data-no-i18n>${esc(step.name)}</span> <small class="mono">${esc(step.step_id)}</small> <button class="btn small primary" type="button" data-drift-review="${esc(step.step_id)}">${esc(UI.translate("Review the change"))}</button></li>`).join("")}</ul></div>` : "";
    const gateDecision = workflow.pending_decision && !terminal ? workflow.pending_decision : null;
    const gateCard = gateDecision ? `<div class="task-next gate-decision"><strong>${esc(UI.translate("Gate decision needed"))}</strong>
      <p>${esc(roleName(gateDecision.role_ref || gateDecision.role))} · ${esc(UI.system(gateDecision.reason, "Gate decision needed"))}</p>
      ${gateDecision.findings.length ? `<ul class="gate-findings">${gateDecision.findings.map((f) => `<li><span class="pill ${f.severity === "blocker" ? "negative" : "warning"}">${esc(f.severity)}</span> ${esc(f.claim)}${f.evidence ? ` <small>${esc(f.evidence)}</small>` : ""}</li>`).join("")}</ul>` : ""}
      <div class="gate-decision-actions">
        <button class="btn small primary" type="button" data-gate-decision="proceed">${esc(UI.translate("Accept and continue"))}</button>
        <button class="btn small" type="button" data-gate-decision="retry">${esc(UI.translate("Send back once more"))}</button>
        <button class="btn small danger" type="button" data-gate-decision="stop">${esc(UI.translate("Stop task"))}</button>
      </div></div>` : "";
    const selectionNote = {
      fixed: "The project's chosen workflow", pm: "Picked by ChatGPT (the PM) for this task",
      auto: "Picked by ccc-mcp from the task text", goal_plan: "Set by the goal's plan",
    }[workflow.selected_by];
    const workflowLine = workflow.name ? `<p class="task-workflow"${workflow.reason ? ` title="${esc(workflow.reason)}"` : ""}><strong>${esc(UI.translate("Workflow"))}</strong> <span data-no-i18n>${esc(UI.translate(workflow.name))}</span>${selectionNote ? ` <small>· ${esc(UI.translate(selectionNote))}</small>` : ""}</p>` : "";
    const canMessage = !terminal && !control.stop_requested;
    const pauseButton = canMessage ?
      `<button class="btn small" id="task-pause-resume">${esc(UI.translate(control.paused ? "Continue work" : "Pause work"))}</button>` : "";
    const canCompose = flowSteps ? !["CANCELLED", "REJECTED"].includes(status.state) && !control.stop_requested : canMessage;
    const restartDefault = workflow.current_step != null ? workflow.current_step : (flowSteps ? flowSteps.length - 1 : 0);
    const closeButton = !["COMPLETED", "CANCELLED", "REJECTED"].includes(status.state) && !control.stop_requested ? `<button class="btn small" id="task-close" type="button" title="${esc(UI.translate("End this task as finished, by your own decision"))}">${esc(UI.translate("Close task"))}</button>` : "";
    const stopButton = canMessage ? `<button class="btn small danger" id="task-stop">${esc(UI.translate("Stop task"))}</button>` : "";
    const deleteButton = REMOVABLE_STATES.has(status.state) ? `<button class="btn small danger" id="task-delete">${esc(UI.translate("Delete task"))}</button>` : "";
    const diffRows = artifacts.filter((a) => a.path.endsWith("/CHANGES.diff"));
    const testRows = artifacts.filter((a) => /\/executions\/TEST-[^/]+\/TEST-RESULT\.md$/.test(a.path));
    // Documents the steps handed over (the specification, or an everyday team's research, plan,
    // draft...), newest first; the code cards below only matter when something changes code.
    const docRole = (path) => { const m = path.match(/\/(SPEC|([A-Z]{2})-DOCUMENT)\.md$/); return m ? (m[1] === "SPEC" ? "SW" : m[2]) : null; };
    const deliverables = artifacts.filter((a) => docRole(a.path)).sort((a, b) => String(b.created).localeCompare(String(a.created)));
    const touchesCode = !flowSteps || flowSteps.some((role) => ["RD", "SD/RD"].includes(baseOfRole(role)));
    activeTouchesCode = touchesCode;
    const deliverableCard = deliverables.length ? `<section class="task-result-card spec-card"><h2>${esc(UI.translate(touchesCode ? "Documents from each step" : "Results"))}</h2><p class="muted">${esc(UI.translate("Newest first. Each step hands its document to the next."))}</p>${deliverables.map((a, index) => `<details ${index === 0 ? "open" : ""}><summary><strong data-no-i18n>${esc(roleName(docRole(a.path)))}</strong> · ${esc(UI.translate(TASK_ROLE_LABELS[docRole(a.path)] || "Result"))}</summary><pre class="mono task-spec">${esc(a.content || "")}</pre></details>`).join("")}</section>` : "";
    const latestDiff = diffRows.at(-1);
    const latestTest = testRows.at(-1);
    return {
      events, control, canMessage: canCompose, restartable: Boolean(flowSteps), terminal, typing: typingIds.size > 0,
      signature: taskSignature(status, timeline),
      pill: { text: UI.translate(displayState), tone: stateTone },
      actions: `${pauseButton}${closeButton}${stopButton}${deleteButton}`,
      steps: steps.map(([, done]) => done),
      nextText: (testCard ? `tests:${testCardState}:${control.host_test_stage || ""}|` : "") + (waitingNote ? `waiting:${waitingKind}:${upcomingIndex}|` : "") + (handoff.next_action && !terminal ? handoff.next_action + (gateDecision ? `|${gateDecision.escalation_id}` : "") + (driftSteps.length ? `|drift:${driftSteps.map((step) => step.step_id).join(",")}` : "") : ""),
      hasGateDecision: Boolean(gateDecision) || driftSteps.length > 0 || Boolean(waitingNote) || Boolean(testCard) || Boolean(reviewCard),
      workflowRoles: flowSteps ? [...new Set(flowSteps)] : null,
      head: `<div><a class="task-back" href="#/p/${encodeURIComponent(id)}/tasks">← ${esc(UI.translate("Back to tasks"))}</a><h1>${esc(UI.translate("Task discussion"))}</h1><div class="task-identifiers"><span>${esc(id)}</span><span class="mono">${esc(taskId)}</span><span class="pill ${stateTone}">${esc(UI.translate(displayState))}</span></div>${workflowLine}</div><div class="task-head-actions">${pauseButton}${closeButton}${stopButton}${deleteButton}</div>`,
      current: waitingKind && upcomingIndex >= 0 ? upcomingIndex : -1, currentKind: waitingKind === "running" ? "running" : "waiting",
      progress: steps.map(([name, done], index) => `<span class="conversation-step ${done ? "done" : ""}${waitingKind && index === upcomingIndex ? (waitingKind === "running" ? " current running" : " current waiting") : ""}"${waitingKind && index === upcomingIndex && waitingNote ? ` title="${esc(UI.translate(waitingNote[0]))}"` : ""}><i aria-hidden="true"></i>${esc(UI.translate(name))}</span>`).join(""),
      feed: taskConversation(events, typingIds),
      paused: Boolean(control.paused && canMessage),
      restartPicker: flowSteps ? `<fieldset class="restart-picker" ${canCompose ? "" : "disabled"}><legend>${esc(UI.translate("Who should redo it?"))}</legend><div class="restart-chips">${flowSteps.map((role, index) => `<label class="restart-chip"><input type="radio" name="restart-step" value="${index}" data-role="${esc(role)}" ${index === restartDefault ? "checked" : ""}><span class="restart-chip-body"><span class="chat-avatar av-${roleAvatarTone(role)}" aria-hidden="true">${roleAvatarIcon(role).replace('width="18" height="18"', 'width="16" height="16"')}</span><span class="restart-chip-name"><i>${index + 1}</i><span data-no-i18n>${esc(roleTitle(role))}</span></span></span></label>`).join("")}</div></fieldset>` : "",
      next: driftCard + reviewCard + testCard + (gateCard || (waitingNote ? `<div class="task-next waiting ${waitingKind}"><strong>${esc(UI.translate(waitingNote[0]))}</strong><p>${esc(UI.translate(waitingNote[1]))}</p>${waitingKind === "retry" && canMessage ? `<button class="btn small primary" type="button" id="task-resume-dispatch">${esc(UI.translate("Start it again"))}</button>` : ""}</div>` : handoff.next_action && !terminal ? `<div class="task-next ${control.paused ? "paused" : ""}"><strong>${esc(UI.translate(control.paused ? "Paused: waiting for you" : "Next step"))}</strong><p>${esc(UI.system(handoff.next_action, "Details are available in the technical record."))}</p>${control.paused && canMessage ? `<button class="btn small primary" type="button" id="task-resume-inline">${esc(UI.translate("Continue work"))}</button>` : ""}</div>` : "")),
      results: `${deliverableCard}${touchesCode ? `<section class="task-result-grid">
      <div class="task-result-card"><h2>${esc(UI.translate("Changed files and diff"))}</h2>${latestDiff ? `<pre class="mono task-diff">${esc(latestDiff.content || "")}</pre>` : `<p class="muted">${esc(UI.translate("No saved diff yet."))}</p>`}</div>
      <div class="task-result-card"><h2>${esc(UI.translate("Host test evidence"))}</h2><p class="muted">${esc(UI.translate("Run by the host on this machine — never taken from the agent's own word."))}</p>${verificationLine(brief.tests)}${latestTest ? `<details><summary>${esc(UI.translate("View saved test result"))}</summary><pre class="mono">${esc(latestTest.content)}</pre></details>` : `<p class="muted">${esc(UI.translate("No host test evidence yet."))}</p>`}</div>
    </section>` : ""}`,
      technical: `<div class="task-technical">${providerSwitchCard(events, status)}<section class="task-result-card"><h2>${esc(UI.translate("Execution history"))}</h2>${(status.executions || []).length ? `<ul class="run-list">${(status.executions || []).map((e) => `<li><span>${esc(e.role_name || roleName(e.role_ref || e.role))}</span><span class="run-status">${esc(UI.translate(e.status))}</span>${e.error ? `<span class="run-error">${esc(UI.error(e.error))}</span>` : ""}</li>`).join("")}</ul>` : `<p class="muted">${esc(UI.translate("No executions yet."))}</p>`}</section><section class="task-result-card"><h2>${esc(UI.translate("Hand off to another AI"))}</h2>${handoffPanelHtml(`${id}/${taskId}`, taskSignature(data.status, data.timeline))}</section></div>`,
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

  // "New messages" button for a chat feed the person has scrolled away from the end of.
  function showNewMessagesHint(feed) {
    const room = feed.closest(".task-conversation-room");
    if (!room) return;
    let hint = room.querySelector(".feed-new-hint");
    if (!hint) {
      hint = document.createElement("button");
      hint.type = "button";
      hint.className = "feed-new-hint btn small primary";
      hint.textContent = UI.translate("New messages");
      hint.onclick = () => { feed.scrollTo({ top: feed.scrollHeight }); hint.remove(); };
      feed.insertAdjacentElement("afterend", hint);
      feed.addEventListener("scroll", () => {
        if (feed.scrollHeight - feed.scrollTop - feed.clientHeight < 40) room.querySelector(".feed-new-hint")?.remove();
      });
    }
  }

  async function renderTaskDetail() {
    const gen = renderGen;
    const route = parseRoute();
    const id = route.project, taskId = route.sub;
    const isNewTaskRoute = taskDetailRoute !== `${id}/${taskId}`;
    if (isNewTaskRoute) {
      taskDetailRoute = `${id}/${taskId}`;
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
    // Opening a finished task fresh: land on Results (the host test evidence) first,
    // not the conversation transcript — that's what "did it work" is answered by.
    if (isNewTaskRoute) taskDetailPane = data.status.state === "COMPLETED" ? "results" : "conversation";
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
    // Handoff panel state for this task (see handoffPanelHtml above).
    const handoffKey = `${id}/${taskId}`;
    const handoffSignature = () => taskSignature(data.status, data.timeline);
    const bindHandoffPanel = () => {
      const panel = document.getElementById("task-continuation-panel");
      if (!panel) return;
      panel.onclick = (event) => {
        const choice = event.target.closest("[data-handoff-target]");
        if (choice) {
          requestHandoff(choice.dataset.handoffTarget);
          return;
        }
        if (event.target.closest("#task-handoff-copy") && handoffView?.text) {
          navigator.clipboard.writeText(handoffView.text).then(
            () => toast("Prompt copied."),
            () => toast("Could not copy. Select the text and copy it manually.", true),
          );
        }
      };
    };
    const paintHandoffPanel = (target) => {
      const current = document.getElementById("task-continuation-panel");
      if (!current) return;
      const hadFocus = current.contains(document.activeElement);
      current.outerHTML = handoffPanelHtml(handoffKey, handoffSignature());
      bindHandoffPanel();
      if (hadFocus && target) {
        document.querySelector(`#task-continuation-panel [data-handoff-target="${target}"]`)?.focus({ preventScroll: true });
      }
    };
    const requestHandoff = async (target) => {
      const started = { key: handoffKey, target, status: "loading", signature: handoffSignature(), text: "", error: "", generatedAt: "" };
      handoffView = started;
      paintHandoffPanel(target);
      try {
        const result = await api(`${taskBase}/continuation?target=${encodeURIComponent(target)}`);
        if (stale(gen) || handoffView !== started) return;
        handoffView = {
          ...started,
          status: "ready",
          text: result.text,
          generatedAt: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        };
      } catch (err) {
        if (stale(gen) || handoffView !== started) return;
        handoffView = { ...started, status: "error", error: err.message };
      }
      paintHandoffPanel(target);
    };
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
      const resumeDispatch = document.getElementById("task-resume-dispatch");
      if (resumeDispatch) resumeDispatch.onclick = () => showModal({
        title: "Start the next step again?", body: "The hand-over to the next step was interrupted. This starts that step now, with the context already saved. Nothing else is re-sent.",
        confirmLabel: "Start it again",
        onSubmit: async () => { await api(`${taskBase}/resume-dispatch`, { method: "POST", body: {} }); toast("The next step is starting."); refresh(); },
      });
      const runTests = document.getElementById("task-run-tests");
      if (runTests) runTests.onclick = () => showModal({
        title: "Run the host tests now?", body: "ccc-mcp runs the project's tests on this computer, in this task's working copy, with the language and version set for the project. Tests run the project's own code, so this uses the project's write permission.",
        confirmLabel: "Run host tests",
        onSubmit: async () => { await api(`${taskBase}/run-tests`, { method: "POST", body: {} }); hostTestKickoff[`${id}/${taskId}`] = Date.now(); toast("The host tests are running."); await refresh(); setTimeout(refresh, 21000); },
      });
      document.querySelectorAll("#task-close, [data-close-task]").forEach((button) => { button.onclick = () => showModal({
        title: "Close this task?", body: "This marks the task finished, by your decision, and no more roles will be started. ccc-mcp records that you closed it and keeps showing the real test result: it will not count as passed host tests. Changes already made to files stay as they are.",
        fields: [{ label: "Note (optional)", placeholder: "For example: I ran the tests myself and they passed" }],
        confirmLabel: "Close task",
        onSubmit: async ([note]) => { await api(`${taskBase}/close`, { method: "POST", body: { note } }); toast("The task is closed."); await refresh(); },
      }); });
      const stopTask = document.getElementById("task-stop");
      if (stopTask) stopTask.onclick = () => showModal({
        title: "Stop this task?", body: "Stopping prevents further agent work. Changes already made to files will not be rolled back.",
        fields: [{ label: "Reason (optional)", placeholder: "Why are you stopping this task?" }], danger: true,
        confirmLabel: "Stop task", onSubmit: async ([reason]) => { await api(`${taskBase}/stop`, { method: "POST", body: { reason } }); toast("Stop requested."); refresh(); },
      });
      document.querySelectorAll("[data-drift-review]").forEach((button) => { button.onclick = () => {
        const step = (data.contract?.steps || []).find((item) => item.step_id === button.dataset.driftReview);
        if (step) reviewContractDrift(taskBase, step, refresh);
      }; });
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
      bindHandoffPanel();
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
      root.querySelectorAll(".conversation-step").forEach((el, index) => {
        el.classList.toggle("done", Boolean(next.steps[index]));
        el.classList.toggle("current", index === next.current);
        el.classList.toggle("running", index === next.current && next.currentKind === "running");
        el.classList.toggle("waiting", index === next.current && next.currentKind === "waiting");
      });
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
      // Someone reading older messages is never pulled away: they get a button instead.
      else if (feedChanged) showNewMessagesHint(room);
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
      button.innerHTML = `<span>${esc(UI.translate("Ask"))}</span><strong data-no-i18n>${esc(roleTitle(picked.dataset.role))}</strong><span>${esc(UI.translate("to redo it"))}</span>`;
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
    // Failed polls keep the page as it is and retry; a changed task is patched in place by refresh().
    livePoll(gen, async () => {
      const [freshStatus, freshTimeline] = await Promise.all([api(taskBase), api(`${taskBase}/timeline`)]);
      const serialized = taskSignature(freshStatus, freshTimeline);
      if (!stale(gen) && serialized !== baseline) { baseline = serialized; await refresh(); }
    });
  }

  function linkify(lines) {
    return lines
      .map((line) => esc(line).replace(/(https?:\/\/[^\s]+)/g, (url) => `<a href="${url}" target="_blank" rel="noopener">${url}</a>`))
      .join("\n");
  }

  function providerCardHtml(provider, label, info, login) {
    const running = login.state === "running";
    const installed = !!info.installed;
    const loggedIn = !!info.logged_in;
    const pill = running
      ? `<span class="pill accent">${esc(UI.translate("Signing in…"))}</span>`
      : !installed
        ? `<span class="pill neutral">${esc(UI.translate("Not installed"))}</span>`
        : loggedIn
          ? `<span class="pill positive">${esc(UI.translate("Signed in"))}</span>`
          : `<span class="pill warning">${esc(UI.translate("Not signed in"))}</span>`;
    const actions = running
      ? `<button class="btn small ghost" data-provider-action="cancel">${esc(UI.translate("Cancel sign-in"))}</button>`
      : !installed
        ? `<button class="btn small primary" data-provider-action="install">${esc(UI.translate("Install"))}</button>`
        : `${loggedIn ? "" : `<button class="btn small primary" data-provider-action="login">${esc(UI.translate("Sign in"))}</button>`}<button class="btn small danger" data-provider-action="uninstall">${esc(UI.translate("Uninstall"))}</button>`;
    const hint = login.error
      ? `<div class="hint" style="color:var(--negative)">${esc(UI.error(login.error))}</div>`
      : info.hint ? `<div class="hint">${esc(info.hint)}</div>` : "";
    const log = running
      ? `<div class="provider-login-log log-panel">${login.log && login.log.length ? linkify(login.log) : `<span class="muted-line">${esc(UI.translate("Waiting for output…"))}</span>`}</div>`
      : "";
    const inputRow = running
      ? `<div class="provider-login-input">
           <input type="text" class="mono" data-provider-input placeholder="${esc(UI.translate("If sign-in asked you to paste something back, type it here and press Enter"))}" autocomplete="off" />
           <button class="btn small" data-provider-action="send">${esc(UI.translate("Send"))}</button>
         </div>`
      : "";
    return `
      <div class="provider-login" data-provider="${esc(provider)}">
        <div class="provider-login-head"><strong>${esc(label)}</strong>${pill}</div>
        ${hint}
        <div class="provider-login-actions">${actions}</div>
        ${log}
        ${inputRow}
      </div>
    `;
  }

  function bindProviderCard(gen, provider, label, el) {
    el.querySelector('[data-provider-action="install"]')?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.innerHTML = `<span class="spinner small"></span> ${esc(UI.translate("Installing…"))}`;
      try {
        await api(`/connect/providers/${provider}/install`, { method: "POST" });
        const fresh = await api("/connect/providers");
        if (stale(gen)) return;
        providerInfoCache = fresh.providers || {};
        toast(`${label}: ${UI.translate("installed")}`);
        renderProviderLogin(gen, provider, label);
      } catch (err) { toast(err.message, true); button.disabled = false; }
    });
    el.querySelector('[data-provider-action="login"]')?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await api(`/connect/providers/${provider}/login`, { method: "POST" });
        if (stale(gen)) return;
        renderProviderLogin(gen, provider, label);
      } catch (err) { toast(err.message, true); button.disabled = false; }
    });
    el.querySelector('[data-provider-action="cancel"]')?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await api(`/connect/providers/${provider}/login/cancel`, { method: "POST" });
        if (stale(gen)) return;
        renderProviderLogin(gen, provider, label);
      } catch (err) { toast(err.message, true); button.disabled = false; }
    });
    el.querySelector('[data-provider-action="uninstall"]')?.addEventListener("click", () => {
      showModal({
        title: `${UI.translate("Uninstall")} ${label}`,
        body: UI.translate("This runs npm uninstall -g for the CLI ccc-mcp installed. A copy installed another way (Homebrew, the official installer, ...) is left alone and this will say so."),
        danger: true,
        confirmLabel: UI.translate("Uninstall"),
        onSubmit: async () => {
          const result = await api(`/connect/providers/${provider}/uninstall`, { method: "POST" });
          const fresh = await api("/connect/providers");
          if (stale(gen)) return;
          providerInfoCache = fresh.providers || {};
          toast(result.still_present ? result.hint : `${label}: ${UI.translate("uninstalled")}`, !!result.still_present);
          renderProviderLogin(gen, provider, label);
        },
      });
    });
    const sendInput = async () => {
      const input = el.querySelector("[data-provider-input]");
      if (!input || !input.value) return;
      const value = input.value;
      input.value = "";
      try { await api(`/connect/providers/${provider}/login/input`, { method: "POST", body: { text: value } }); }
      catch (err) { toast(err.message, true); }
    };
    el.querySelector('[data-provider-action="send"]')?.addEventListener("click", sendInput);
    el.querySelector("[data-provider-input]")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); sendInput(); }
    });
  }

  function renderProviderLogin(gen, provider, label) {
    const el = document.getElementById(`provider-login-${provider}`);
    if (!el) return;
    api(`/connect/providers/${provider}/login`).then((login) => {
      if (stale(gen)) return;
      el.innerHTML = providerCardHtml(provider, label, providerInfoCache[provider] || {}, login);
      bindProviderCard(gen, provider, label, el);
      if (login.state !== "running") return;
      const intervalId = setInterval(async () => {
        if (stale(gen)) { clearInterval(intervalId); return; }
        let fresh;
        try { fresh = await api(`/connect/providers/${provider}/login`); } catch (e) { return; }
        if (stale(gen)) { clearInterval(intervalId); return; }
        const logPanel = el.querySelector(".provider-login-log");
        if (logPanel) {
          // Follow the output only while the person is reading its end.
          const atEnd = logPanel.scrollHeight - logPanel.scrollTop - logPanel.clientHeight < 24;
          const html = fresh.log && fresh.log.length ? linkify(fresh.log) : `<span class="muted-line">${esc(UI.translate("Waiting for output…"))}</span>`;
          if (logPanel.dataset.shown !== html) {
            const before = logPanel.scrollTop;
            logPanel.innerHTML = html;
            logPanel.dataset.shown = html;
            logPanel.scrollTop = atEnd ? logPanel.scrollHeight : before;
          }
        }
        if (fresh.state === "running") return;
        clearInterval(intervalId);
        try {
          const freshProviders = await api("/connect/providers");
          if (!stale(gen)) providerInfoCache = freshProviders.providers || {};
        } catch (e) {}
        if (stale(gen)) return;
        el.innerHTML = providerCardHtml(provider, label, providerInfoCache[provider] || {}, fresh);
        bindProviderCard(gen, provider, label, el);
      }, 1500);
      trackTimer(intervalId);
    }).catch(() => {
      el.innerHTML = providerCardHtml(provider, label, providerInfoCache[provider] || {}, { state: "idle", log: [], error: null });
      bindProviderCard(gen, provider, label, el);
    });
  }

  async function renderConnect() {
    const gen = renderGen;
    layout(loadingView());
    let status;
    try { status = await api("/connect/status"); }
    catch (err) { if (!stale(gen)) errorView(err); return; }
    if (stale(gen)) return;
    lastConnectStatus = status;
    const pageHtml = () => {
    const live = status.state === "connected" || status.state === "starting" || status.state === "stopping";
    const dotCls = status.state === "connected" ? "on" : status.state === "error" ? "err" : status.state === "starting" || status.state === "stopping" ? "pending" : "";
    const savedConnections = status.saved_connections || [];
      return `
      <div class="title-row">
        <div><h1>Connect</h1><div class="sub">Link this ccc-mcp home to ChatGPT over an OpenAI Platform Tunnel.</div></div>
      </div>
      <div class="settings-stack">
        ${pageCard("link", "Connection status", "", `
          <div class="status-row connect-state is-${dotCls || "off"}"><span class="status-dot ${dotCls}"></span><strong>${esc(UI.translate(status.state))}</strong>${status.tunnel_id ? ` <span class="mono muted">${esc(status.tunnel_id)}</span>` : ""}</div>
          ${status.error ? `<div class="kv-row"><span class="k">Error</span><span class="v" style="color:var(--negative)">${esc(UI.error(status.error))}</span></div>` : ""}
          ${!status.tunnel_client_installed ? `<div class="hint">tunnel-client is not installed yet — it downloads and verifies automatically on first connect.</div>` : ""}
          <div id="connect-log-wrap" data-morph-skip ${live ? 'style="margin-top:16px"' : ""}></div>`,
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
        ${pageCard("test", "Native CLI login", "Install, sign in to, and uninstall Codex and Claude Code from here; ccc-mcp never stores their credentials. If this computer has no browser of its own, open any link that shows up below on another device and finish signing in there.",
          `<div id="provider-login-codex" data-morph-skip><span class="spinner small"></span> ${esc(UI.translate("Checking…"))}</div><div id="provider-login-claude" data-morph-skip><span class="spinner small"></span> ${esc(UI.translate("Checking…"))}</div>`)}
      </div>
    `;
    };
    layout(pageHtml(), { narrow: true, contentClass: "settings-wide" });
    // Everything below can run again after an in-place repaint: handlers are assigned (onclick), never
    // stacked, and the cards that fill themselves (the log, the Codex/Claude sign-in) are left alone.
    let paintedOnce = false;
    const refresh = async () => {
      try { status = await api("/connect/status"); lastConnectStatus = status; } catch (e) { return; }
      if (!stale(gen)) paint();
    };
    const renderLog = (lines) => {
      const wrap = document.getElementById("connect-log-wrap");
      if (!wrap) return;
      const panel = wrap.querySelector(".log-panel");
      // Follow the output only if the person is already at the end of it; never pull them down.
      const atEnd = !panel || panel.scrollHeight - panel.scrollTop - panel.clientHeight < 24;
      const html = lines.length
        ? `<div class="log-panel">${lines.map((l) => esc(l)).join("\n")}</div>`
        : `<div class="log-panel"><span class="muted-line">${esc(UI.translate("Waiting for tunnel-client output…"))}</span></div>`;
      if (wrap.dataset.shown === html) return;
      const before = panel ? panel.scrollTop : 0;
      wrap.innerHTML = html;
      wrap.dataset.shown = html;
      const next = wrap.querySelector(".log-panel");
      if (next) next.scrollTop = atEnd ? next.scrollHeight : before;
    };
    const wire = () => {
      const live = status.state === "connected" || status.state === "starting" || status.state === "stopping";
      const savedConnections = status.saved_connections || [];
      const disconnectBtn = document.getElementById("btn-disconnect");
      if (disconnectBtn) disconnectBtn.onclick = async () => {
        disconnectBtn.disabled = true;
        try { await api("/connect/stop", { method: "POST" }); toast("Disconnected."); await refresh(); }
        catch (err) { toast(err.message, true); disconnectBtn.disabled = false; }
      };
      if (live) return;
      const connectBtn = document.getElementById("btn-connect");
      if (connectBtn) connectBtn.onclick = async () => {
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
        await refresh();
      } catch (err) {
        clearTimeout(progressTimer);
        toast(err.message, true);
        connectBtn.disabled = false;
        connectBtn.textContent = UI.translate("Connect");
      }
      };
      document.getElementById("btn-save-connection").onclick = async (event) => {
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
          await refresh();
        } catch (err) { toast(err.message, true); } finally { button.disabled = false; }
      };
      document.getElementById("btn-clear-connection").onclick = () => {
        for (const id of ["connect-profile-id", "connect-name", "connect-tunnel-id", "connect-api-key"]) document.getElementById(id).value = "";
        document.getElementById("connect-remember").checked = false;
        document.getElementById("connect-name").focus();
      };
      document.querySelectorAll("[data-use]").forEach((btn) => {
        btn.onclick = () => {
          const profile = savedConnections.find((row) => row.id === btn.dataset.use);
          document.getElementById("connect-profile-id").value = profile.id;
          document.getElementById("connect-name").value = profile.name;
          document.getElementById("connect-tunnel-id").value = profile.tunnel_id;
          document.getElementById("connect-api-key").value = "";
          document.getElementById("connect-name").focus();
        };
      });
      document.querySelectorAll("[data-forget]").forEach((btn) => {
        btn.onclick = () => {
          const profile = savedConnections.find((row) => row.id === btn.dataset.forget);
          showModal({ title: "Forget connection", body: `${profile.name}\n\n${UI.translate("This removes the saved key from this device. It does not revoke the key at OpenAI.")}`,
            danger: true, confirmLabel: "Forget", onSubmit: async () => {
              await api("/connect/forget", { method: "POST", body: { profile_id: profile.id } });
              toast("Forgotten.");
              await refresh();
            } });
        };
      });
    };
    const paint = () => {
      if (paintedOnce) morphContent(pageHtml());
      wire();
      paintedOnce = true;
    };
    paint();
    api("/connect/providers").then((r) => {
      if (stale(gen)) return;
      providerInfoCache = r.providers || {};
      renderProviderLogin(gen, "codex", "Codex");
      renderProviderLogin(gen, "claude", "Claude Code");
    }).catch(() => {
      for (const provider of ["codex", "claude"]) {
        const el = document.getElementById(`provider-login-${provider}`);
        if (el) el.innerHTML = `<div class="kv-row"><span class="k muted">${esc(UI.translate("Could not check native CLI login status."))}</span></div>`;
      }
    });
    livePoll(gen, async () => {
      const fresh = await api("/connect/status");
      if (stale(gen)) return;
      if (JSON.stringify(fresh) !== JSON.stringify(status)) { status = fresh; lastConnectStatus = fresh; paint(); }
      if (status.state === "connected" || status.state === "starting" || status.state === "stopping") {
        const logs = await api("/connect/logs");
        if (!stale(gen)) renderLog(logs.lines);
      }
    }, 2000);
  }

  function driftChangeLabel(change) {
    const part = change.component ? change.component.replace("base_contract:", "") : "";
    if (change.change === "major") return `${part}: ${UI.translate("meaning changed")} (${change.pinned} → ${change.current})`;
    if (change.change === "text") return `${part}: ${UI.translate("wording changed")} (${change.pinned} → ${change.current})`;
    if (change.change === "added") return `${part}: ${UI.translate("a new rule was added")}`;
    if (change.change === "removed") return `${part}: ${UI.translate("a rule was removed")}`;
    return UI.translate("The saved rules could not be read");
  }

  // Accepting a change to ccc-mcp's fixed rules is deliberate: the person reads what changed,
  // types a phrase naming this task, step and contract, and only that exact change is accepted.
  function reviewContractDrift(taskBase, step, done) {
    const changes = (step.changes || []).filter((change) => change.blocking).map((change) => `• ${driftChangeLabel(change)}`).join("\n");
    confirmTextModal({
      title: UI.translate("Accept this change to the fixed rules?"),
      description: `${step.name} · ${step.step_id}\n\n${changes}\n\n${UI.translate("This role was saved under older fixed rules. Accepting lets this task go on with the current rules for this one step. It does not change the role, it covers no other task, and if the rules change again you will be asked again.")}`,
      challenge: step.confirmation_phrase,
      confirmLabel: UI.translate("Accept this change"),
      onConfirm: async () => {
        await api(`${taskBase}/contract/accept`, { method: "POST", body: { step_id: step.step_id, snapshot_id: step.snapshot_id, contract_hash: step.current_contract_hash, confirmation: step.confirmation_phrase } });
        toast("Change accepted for this task.");
        if (done) done();
      },
    });
  }

  // ---- Projects: what ccc-mcp may touch ---------------------------------------------------------
  function projectRowHtml(p) {
    const link = `#/p/${encodeURIComponent(p.id)}`;
    return `<div class="project-row" data-key="project:${esc(p.id)}">
        <a class="project-row-link" href="${link}/overview">
          <span class="project-row-text">
            <span class="project-row-name" data-no-i18n>${esc(p.id)}</span>
            <span class="project-row-path mono">${esc(p.root)}</span>
          </span>
        </a>
        <span class="project-row-meta">${accessPill(p.access)}<a class="btn small" href="${link}/tasks">Tasks</a></span>
      </div>`;
  }

  async function renderProjects() {
    const gen = renderGen;
    layout(loadingView());
    let projects;
    try { projects = await api("/projects"); } catch (err) { if (!stale(gen)) errorView(err); return; }
    if (stale(gen)) return;
    const list = (items) => items.length
      ? items.map(projectRowHtml).join("")
      : `<div class="empty" data-key="empty">${esc(UI.translate("No projects yet. Add an existing folder or create a new one."))}</div>`;
    layout(`
      <div class="title-row"><div><h1>Projects</h1><div class="sub">${esc(UI.translate("The folders ccc-mcp is allowed to work in."))}</div></div>
        <div class="title-actions">
          <button class="btn" id="btn-import-project">Import backup</button>
          <button class="btn" id="btn-create-project">New project</button>
          <button class="btn primary" id="btn-add-project">Add existing folder</button>
        </div></div>
      <div class="project-list" id="project-list">${list(projects)}</div>
    `);
    document.getElementById("btn-add-project").onclick = openAddProjectModal;
    document.getElementById("btn-create-project").onclick = openCreateProjectModal;
    document.getElementById("btn-import-project").onclick = openImportProjectModal;
    const box = document.getElementById("project-list");
    let signature = JSON.stringify(projects.map((p) => [p.id, p.root, p.access.mode]));
    livePoll(gen, async () => {
      const fresh = await api("/projects");
      const next = JSON.stringify(fresh.map((p) => [p.id, p.root, p.access.mode]));
      if (stale(gen) || next === signature) return;
      signature = next;
      window.cccMorph.morph(box, list(fresh), UI.localize);
    }, 5000);
  }

  const teamUi = window.cccTeam({ api, esc, UI, toast, mountModal, closeModal });
  const tasksUi = window.cccTasks({
    api, esc, UI, layout, navTo, toast, showModal, parseRoute, stale, getGen: () => renderGen, livePoll,
    whenHtml, loadingView, errorView, rememberedProject, taskStateTone, confirmDeleteTask,
    removableStates: REMOVABLE_STATES,
  });
  const skillsUi = window.cccSkills({
    api, esc, UI, layout, toast, showModal, confirmTextModal, mountModal, closeModal, stale,
    getGen: () => renderGen, loadingView, errorView, rememberedProject, pageCard,
  });
  const goalsView = window.cccGoals({
    api, esc, UI, layout, navTo, toast, showModal, parseRoute, stale, getGen: () => renderGen, livePoll,
    morphContent, whenHtml, loadingView, errorView,
  });

  function route() {
    renderGen += 1;
    captureTokenFromHash();
    const r = parseRoute();
    refreshConnectDot();
    if (r.view === "settings") return renderConnect();
    if (r.view === "projects") return renderProjects();
    if (r.view === "skills") return skillsUi.render();
    if (r.view === "tasks-home") return tasksUi.renderHome();
    if (r.view === "tasks") return tasksUi.renderList();
    if (r.view === "goal") return goalsView.renderGoalDetail();
    if (r.view === "task") return renderTaskDetail();
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
