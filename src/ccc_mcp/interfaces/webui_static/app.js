(() => {
  "use strict";
  const UI = window.cccI18n;

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
    const fieldsHtml = (fields || []).map((f, i) => `
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
      const values = (fields || []).map((_, i) => document.getElementById("mf-" + i).value);
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
  }

  function confirmTextModal({ title, description, challenge, confirmLabel, onConfirm }) {
    showModal({
      title,
      body: description,
      fields: [{ label: `${UI.translate("Type")} ${challenge} ${UI.translate("to confirm")}`, placeholder: challenge }],
      confirmLabel: confirmLabel || "Confirm",
      danger: true,
      onSubmit: async ([typed]) => {
        if (typed !== challenge) throw new Error(UI.translate("Confirmation text did not match."));
        await onConfirm(typed);
      },
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
    return { view: "dashboard" };
  }

  function navTo(path) { location.hash = path; }

  const ICON = {
    projects: `<svg class="icon" viewBox="0 0 16 16"><path d="M1.5 4.5a1 1 0 0 1 1-1h3l1.2 1.5H13.5a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-7.5Z"/></svg>`,
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
      <div class="sidebar">
        <div class="brand"><span class="mark">C</span><span>ccc-mcp<small>CONTROL CENTER</small></span></div>
        <a class="nav-item ${currentRoute.view === "dashboard" ? "active" : ""}" href="#/">${ICON.projects}All projects</a>
        <a class="nav-item ${currentRoute.view === "global-skills" ? "active" : ""}" href="#/global-skills">${ICON.skills}Global Skills</a>
        ${projectNav}
        <div class="nav-group-label">&nbsp;</div>
        <a class="nav-item ${currentRoute.view === "connect" ? "active" : ""}" href="#/connect">${ICON.connect}Connect<span id="nav-connect-dot" class="${dotClass}"></span></a>
        <a class="nav-item ${currentRoute.view === "docs" ? "active" : ""}" href="#/docs">${ICON.docs}Documentation</a>
        <div class="sidebar-spacer"></div>
        <button type="button" id="theme-toggle" class="nav-item" aria-pressed="${currentTheme() === "dark"}" aria-label="Toggle dark mode">${currentTheme() === "dark" ? ICON.moon : ICON.sun}<span>Dark mode</span><span class="dot${currentTheme() === "dark" ? " on" : ""}"></span></button>
        <div class="sidebar-locale"><span>Interface language</span><select id="ui-locale" aria-label="Interface language"><option value="zh-TW">繁體中文</option><option value="en">English</option></select></div>
        <div class="sidebar-foot">LOCAL CONSOLE <span class="status-dot on"></span></div>
      </div>
      <div class="frame"><div class="sheet" id="sheet"><div class="content${narrow ? " narrow" : ""}">${contentHtml}</div></div></div>
    `;
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

  async function renderDashboard() {
    const gen = renderGen;
    layout(`<div class="empty">Loading projects…</div>`);
    let projects = [], organization = { groups: [], assignments: {} };
    try {
      [projects, organization] = await Promise.all([api("/projects"), api("/project-groups")]);
    } catch (err) {
      if (!stale(gen)) errorView(err);
      return;
    }
    if (stale(gen)) return;
    const writable = projects.filter((p) => p.access.mode === "read-write").length;
    const groups = organization.groups || [];
    const assignments = organization.assignments || {};
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
        <a class="project-row-link" href="#/p/${encodeURIComponent(p.id)}/overview">
          <span class="project-avatar small">${esc(p.id.slice(0, 1).toUpperCase())}</span>
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
      return `<section class="project-group">
        <div class="section-heading project-heading"><div><h2 data-no-i18n>${esc(group ? groupName(group) : UI.translate("Ungrouped"))}</h2><p>${members.length} ${UI.translate("registered projects")}</p></div>
        ${group ? `<div class="group-actions">${index > 0 ? `<button class="btn small ghost icon-button" data-group-up="${esc(group.id)}" aria-label="${esc(UI.translate("Move group up"))}" title="${esc(UI.translate("Move group up"))}">${ICON.up}</button>` : ""}${index < groups.length - 1 ? `<button class="btn small ghost icon-button" data-group-down="${esc(group.id)}" aria-label="${esc(UI.translate("Move group down"))}" title="${esc(UI.translate("Move group down"))}">${ICON.down}</button>` : ""}<button class="btn small" data-group-rename="${esc(group.id)}">${esc(UI.translate("Rename"))}</button><button class="btn small danger" data-group-delete="${esc(group.id)}">${esc(UI.translate("Delete group"))}</button></div>` : ""}</div>
        <div class="project-list" data-drop-group="${esc(groupId)}">${members.length ? members.map(row).join("") : `<div class="group-empty">${esc(UI.translate("Drag projects here, or use Move to on a card."))}</div>`}</div>
      </section>`;
    };
    layout(`
      <div class="title-row">
        <div><h1>Projects</h1><div class="sub">Every project registered with this ccc-mcp home.</div></div>
        <div class="title-actions">
          <button class="btn" id="btn-group">${esc(UI.translate("New group"))}</button>
          <button class="btn" id="btn-add">${ICON.projects}Add existing folder</button>
          <button class="btn primary" id="btn-create">Create new project</button>
        </div>
      </div>
      ${projects.length ? `
        <div class="kpi-strip">
          <div class="kpi"><div class="v num">${projects.length}</div><div class="l">Projects</div></div>
          <div class="kpi"><div class="v num">${writable}</div><div class="l">Read-write</div></div>
          <div class="kpi"><div class="v num">${projects.length - writable}</div><div class="l">Read-only</div></div>
        </div>
      ` : ""}
      ${projects.length || groups.length ? groups.map(section).join("") + section(null, groups.length) : `<div class="empty">No projects yet. Add an existing folder or create a new one.</div>`}
    `);
    document.getElementById("btn-add").addEventListener("click", openAddProjectModal);
    document.getElementById("btn-create").addEventListener("click", openCreateProjectModal);
    document.getElementById("btn-group").addEventListener("click", () => showModal({
      title: "New group", body: "Group projects on this device without changing their folders or settings.",
      fields: groupFields(null), confirmLabel: "Create", onSubmit: async (values) => {
        await api("/project-groups", { method: "POST", body: { action: "create", ...saveGroupNames(values) } });
        renderDashboard();
      },
    }));
    const update = async (body) => {
      try { await api("/project-groups", { method: "POST", body }); renderDashboard(); }
      catch (err) { toast(err.message, true); }
    };
    document.querySelectorAll(".project-move").forEach((select) => select.addEventListener("change", () => update({ action: "move", project_id: select.dataset.projectId, group_id: select.value })));
    document.querySelectorAll("[data-group-rename]").forEach((btn) => btn.addEventListener("click", () => {
      const group = groups.find((g) => g.id === btn.dataset.groupRename);
      showModal({ title: "Rename group", body: "Set the names shown for each interface language.", fields: groupFields(group), confirmLabel: "Save", onSubmit: async (values) => {
        await api("/project-groups", { method: "POST", body: { action: "rename", group_id: group.id, ...saveGroupNames(values) } });
        renderDashboard();
      } });
    }));
    document.querySelectorAll("[data-group-delete]").forEach((btn) => btn.addEventListener("click", () => {
      const group = groups.find((g) => g.id === btn.dataset.groupDelete);
      showModal({ title: "Delete group", body: "Projects in this group will become ungrouped. No project files are deleted.", confirmLabel: "Delete group", danger: true, onSubmit: async () => {
        await api("/project-groups", { method: "POST", body: { action: "delete", group_id: group.id } });
        renderDashboard();
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
    watchForChanges(gen, () => Promise.all([api("/projects"), api("/project-groups")]), renderDashboard);
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

  async function renderProjectOverview() {
    const gen = renderGen;
    const route = parseRoute();
    const id = route.project;
    layout(`<div class="empty">Loading…</div>`);
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
    layout(`
      <div class="title-row">
        <div><h1>${esc(id)}</h1><div class="sub mono">${esc(project.root)}</div></div>
        <div class="title-actions">
          <a class="btn" href="#/p/${encodeURIComponent(id)}/tasks">View tasks</a>
          <a class="btn" href="#/p/${encodeURIComponent(id)}/settings">Settings</a>
        </div>
      </div>
      <div class="section">
        <div class="section-row">
          <div class="section-label">
            <div class="t">Access</div>
            <div class="d">Whether ccc-mcp can write files in this project and run its test command.</div>
          </div>
          <div class="section-panel">
            <div class="kv-row"><span class="k">Mode</span><span class="v">${accessPill(access)}</span></div>
            ${access.mode === "read-write" ? `
              <div class="kv-row"><span class="k">Authorized at</span><span class="v">${esc(access.authorized_at || "")}</span></div>
              <div class="kv-row"><span class="k">Identity</span><span class="v mono">${esc(access.identity || "")}</span></div>
            ` : ""}
            <div style="margin-top:16px; display:flex; gap:8px">
              ${access.mode === "read-write"
                ? `<button class="btn" id="btn-revoke">Revoke write (back to read-only)</button>`
                : `<button class="btn primary" id="btn-enable-write">Enable write</button>`}
            </div>
          </div>
        </div>
      </div>
      <div class="section">
        <div class="section-row">
          <div class="section-label">
            <div class="t">Clear workflow history</div>
            <div class="d">Archives the database and resets tasks/context for this project. Source files, settings and authorization are preserved.</div>
          </div>
          <div class="section-panel">
            <button class="btn" id="btn-clear-history">Clear history…</button>
          </div>
        </div>
      </div>
      <div class="section">
        <div class="section-row">
          <div class="section-label">
            <div class="t">Remove from ccc-mcp</div>
            <div class="d">Removes this project from the list and archives its ccc-mcp history. You can register the ID again. Choose whether to keep or delete the folder.</div>
          </div>
          <div class="section-panel">
            <div class="project-remove-actions">
              <button class="btn primary" id="btn-remove">Remove, keep folder…</button>
              <button class="btn danger" id="btn-remove-files">Remove and delete folder…</button>
            </div>
          </div>
        </div>
      </div>
      <div class="section">
        <div class="section-row">
          <div class="section-label">
            <div class="t">Permanently retire project ID</div>
            <div class="d">Permanently retires this project ID. Never deletes files on disk or historical records. Cannot be undone.</div>
          </div>
          <div class="section-panel">
            <button class="btn danger" id="btn-delete" ${access.mode === "read-write" ? "disabled" : ""}>Retire ID…</button>
            ${access.mode === "read-write" ? `<div class="hint">Revoke write access first.</div>` : ""}
          </div>
        </div>
      </div>
    `, { narrow: true });
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
          onConfirm: async (typed) => {
            const result = await api(`/projects/${encodeURIComponent(id)}/remove`, {
              method: "POST", body: { confirm: typed, delete_files: deleteFiles },
            });
            if (result.cleanup_error) {
              toast(`${UI.translate("Project removed, but folder cleanup failed. Remaining path:")} ${result.remaining_path}`, true);
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

  async function renderTasksList() {
    const gen = renderGen;
    const id = parseRoute().project;
    layout(`<div class="empty">Loading…</div>`);
    let tasks;
    try { tasks = await api(`/projects/${encodeURIComponent(id)}/tasks`); }
    catch (err) { if (!stale(gen)) errorView(err); return; }
    if (stale(gen)) return;
    const rows = tasks.map((t) => `
      <tr class="clickable" data-task="${esc(t.id)}" tabindex="0" role="link" aria-label="${esc(t.id)}">
        <td class="mono">${esc(t.id)}</td>
        <td>${esc(UI.translate(t.state))}</td>
        <td>${esc((t.requirement || "").slice(0, 80))}</td>
        <td>${esc(t.created || "")}</td>
      </tr>
    `).join("");
    layout(`
      <div class="title-row"><div><h1>Tasks</h1><div class="sub">${esc(id)}</div></div></div>
      ${tasks.length ? `
        <table>
          <thead><tr><th>Task</th><th>State</th><th>Requirement</th><th>Created</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      ` : `<div class="empty">${esc(UI.translate("Tasks created through ChatGPT or the CLI will appear here."))}</div>`}
    `);
    document.querySelectorAll("tr[data-task]").forEach((tr) => {
      const open = () => navTo(`/p/${encodeURIComponent(id)}/tasks/${encodeURIComponent(tr.dataset.task)}`);
      tr.addEventListener("click", open);
      tr.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); }
      });
    });
    watchForChanges(gen, () => api(`/projects/${encodeURIComponent(id)}/tasks`), renderTasksList);
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

  async function renderTaskDetail() {
    const gen = renderGen;
    const route = parseRoute();
    const id = route.project, taskId = route.sub;
    layout(`<div class="empty">Loading…</div>`);
    let status;
    try { status = await api(`/projects/${encodeURIComponent(id)}/tasks/${encodeURIComponent(taskId)}`); }
    catch (err) { if (!stale(gen)) errorView(err); return; }
    if (stale(gen)) return;
    const brief = status.brief || {};
    const executions = (status.executions || []).map((e) => `
      <tr>
        <td class="mono">${esc((e.id || "").slice(0, 12))}</td>
        <td>${esc(e.role)}</td>
        <td>${esc(UI.translate(e.status))}</td>
        <td>${esc(e.error ? String(e.error).slice(0, 120) : "")}</td>
      </tr>
    `).join("");
    layout(`
      <div class="title-row">
        <div><h1 data-no-i18n>${esc(taskId)}</h1><div class="sub">${esc(id)} · ${esc(UI.translate(status.state))}</div></div>
        <div class="title-actions"><a class="btn" href="#/p/${encodeURIComponent(id)}/tasks">Back to tasks</a></div>
      </div>
      ${stageStrip(status.state)}
      <div class="stage-labels"><span>${esc(UI.translate(STAGES[0]))}</span><span>${esc(UI.translate(STAGES[STAGES.length - 1]))}</span></div>
      <div class="section">
        <div class="kv-row"><span class="k">Waiting on</span><span class="v">${esc(brief.waiting_on || "—")}</span></div>
        <div class="kv-row"><span class="k">Agent / role</span><span class="v">${esc(brief.agent || "—")} / ${esc(brief.role || "—")}</span></div>
        <div class="kv-row"><span class="k">Execution status</span><span class="v">${esc(brief.execution_status || "—")}</span></div>
        <div class="kv-row"><span class="k">Host verification</span><span class="v">${verificationLine(brief.tests)}</span></div>
        ${testScopeLine(brief.tests) ? `<div class="kv-row"><span class="k">Test scope</span><span class="v">${testScopeLine(brief.tests)}</span></div>` : ""}
        ${dependencyLine(brief.tests) ? `<div class="kv-row"><span class="k">Dependencies</span><span class="v">${dependencyLine(brief.tests)}</span></div>` : ""}
        ${brief.summary ? `<div class="kv-row"><span class="k">Summary</span><span class="v">${esc(brief.summary)}</span></div>` : ""}
        ${brief.next_action ? `<div class="kv-row"><span class="k">Next action</span><span class="v">${esc(brief.next_action)}</span></div>` : ""}
      </div>
      <div class="section">
        <div class="t" style="font-weight:500; margin-bottom:12px">Executions</div>
        ${executions ? `<table><thead><tr><th>Execution</th><th>Role</th><th>Status</th><th>Error</th></tr></thead><tbody>${executions}</tbody></table>` : `<div class="empty">No executions yet.</div>`}
      </div>
      <div class="section">
        <div class="t" style="font-weight:500; margin-bottom:8px">Requirement</div>
        <div class="muted">${esc(status.requirement || "—")}</div>
      </div>
      <div class="section">
        <div class="tabs" role="tablist">
          <button class="tab active" data-tab="usage" role="tab" aria-selected="true">Usage &amp; CLI evidence</button>
          <button class="tab" data-tab="result" role="tab" aria-selected="false">Raw result</button>
        </div>
        <div id="task-tab-body"><div class="empty">Loading…</div></div>
      </div>
    `);
    const tabCache = {};
    const tabBody = document.getElementById("task-tab-body");
    async function loadTab(name) {
      if (!tabCache[name]) {
        const path = name === "usage" ? "usage" : "result";
        try {
          tabCache[name] = await api(`/projects/${encodeURIComponent(id)}/tasks/${encodeURIComponent(taskId)}/${path}`);
        } catch (err) {
          tabBody.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
          return;
        }
      }
      tabBody.innerHTML = `<pre class="panel mono" style="white-space:pre-wrap; overflow:auto; max-height:420px">${esc(JSON.stringify(tabCache[name], null, 2))}</pre>`;
    }
    document.querySelectorAll(".tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach((b) => { b.classList.remove("active"); b.setAttribute("aria-selected", "false"); });
        btn.classList.add("active");
        btn.setAttribute("aria-selected", "true");
        loadTab(btn.dataset.tab);
      });
    });
    loadTab("usage");
    if (!TERMINAL_FAIL.has(status.state) && status.state !== "COMPLETED") {
      watchForChanges(
        gen,
        () => api(`/projects/${encodeURIComponent(id)}/tasks/${encodeURIComponent(taskId)}`),
        renderTaskDetail,
        5000
      );
    }
  }

  async function renderSettings() {
    const gen = renderGen;
    const id = parseRoute().project;
    layout(`<div class="empty">Loading…</div>`);
    let overview;
    try { overview = await api(`/projects/${encodeURIComponent(id)}/settings`); }
    catch (err) { if (!stale(gen)) errorView(err); return; }
    if (stale(gen)) return;
    const roles = overview.editable.roles || {};
    const providers = Object.keys(overview.available_providers || {});
    const roleRows = Object.keys(roles).map((role) => `
      <div class="kv-row">
        <span class="k">${esc(role)}</span>
        <span class="v">
          <select data-role="${esc(role)}" class="role-select">
            ${providers.map((p) => `<option value="${esc(p)}" ${roles[role].provider === p ? "selected" : ""}>${esc(p)}</option>`).join("")}
          </select>
        </span>
      </div>
    `).join("");
    const mm = overview.editable.meeting_mode || {};
    const runtime = overview.editable.runtime || {};
    layout(`
      <div class="title-row"><div><h1>Settings</h1><div class="sub">${esc(id)} · ${UI.translate("profile")}: ${esc(overview.profile)}</div></div></div>
      <div class="section">
        <div class="section-row">
          <div class="section-label"><div class="t">Profile</div><div class="d">Apply a bundled provider combination to every role at once.</div></div>
          <div class="section-panel">
            <button class="btn ${overview.profile === "chatgpt-only" ? "selected" : ""}" data-profile="chatgpt-only" aria-pressed="${overview.profile === "chatgpt-only"}">chatgpt-only</button>
            <button class="btn ${overview.profile === "best-model" ? "selected" : ""}" data-profile="best-model" aria-pressed="${overview.profile === "best-model"}">best-model</button>
          </div>
        </div>
      </div>
      <div class="section">
        <div class="section-row">
          <div class="section-label"><div class="t">Roles</div><div class="d">Provider assigned to each workflow role.</div></div>
          <div class="section-panel">${roleRows}</div>
        </div>
      </div>
      <div class="section">
        <div class="section-row">
          <div class="section-label"><div class="t">Language</div><div class="d">What Codex/Claude/ChatGPT receive in reports.</div></div>
          <div class="section-panel">
            <select id="language-select">
              ${["zh-TW", "ch", "en"].map((l) => `<option value="${l}" ${overview.editable.language === l ? "selected" : ""}>${l}</option>`).join("")}
            </select>
          </div>
        </div>
      </div>
      <div class="section">
        <div class="section-row">
          <div class="section-label"><div class="t">Test command</div><div class="d">After each implementation, ccc-mcp installs declared dependencies and runs tests automatically before REVIEW.</div></div>
          <div class="section-panel">
            <div class="field">
              <input type="text" id="test-command" value="${esc((overview.editable.test_command || []).join(" "))}" placeholder="e.g. python -m pytest -q" />
              <div class="hint">Leave blank to test changed Python test files automatically. If none changed, or pytest settings changed, pytest uses the project's configured scope. Enter a command only to override this.</div>
            </div>
            <button class="btn small" id="save-test-command">Save</button>
          </div>
        </div>
      </div>
      <div class="section">
        <div class="section-row">
          <div class="section-label"><div class="t">Project runtime</div><div class="d">Choose the language and installed runtime used for this project's code and tests.</div></div>
          <div class="section-panel">
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
            <div class="field">
              <label for="runtime-executable">Local executable (optional)</label>
              <input type="text" id="runtime-executable" value="${esc(runtime.executable)}" placeholder="e.g. C:/Python39/python.exe" autocomplete="off" />
              <div class="hint">Absolute executable path or command name, without arguments. Leave empty to find an installed runtime.</div>
            </div>
            <div class="hint">Python uses a separate virtual environment per selection. Set a test command for other languages. Install the required runtime locally first.</div>
            <div class="actions" style="margin-top:12px">
              <button class="btn small" id="save-runtime">Save runtime</button>
              <button class="btn small" id="check-runtime">Check local version</button>
            </div>
            <div id="runtime-status" class="hint" role="status" aria-live="polite"></div>
          </div>
        </div>
      </div>
      <div class="section">
        <div class="section-row">
          <div class="section-label"><div class="t">Meeting Mode</div><div class="d">Bounded SD/RD↔SA clarification rounds before implementation.</div></div>
          <div class="section-panel">
            <div class="checkbox-row">
              <input type="checkbox" id="mm-enabled" ${mm.enabled ? "checked" : ""} />
              <label for="mm-enabled" style="margin:0">Enabled</label>
            </div>
            <div class="field" style="margin-top:12px">
              <label>Max rounds (0-5)</label>
              <input type="text" id="mm-max" value="${esc(mm.max_rounds)}" />
            </div>
            <button class="btn small" id="save-meeting-mode">Save</button>
          </div>
        </div>
      </div>
    `, { narrow: true });
    document.querySelectorAll(".role-select").forEach((sel) => {
      sel.addEventListener("change", async () => {
        try {
          await api(`/projects/${encodeURIComponent(id)}/config/role`, {
            method: "POST", body: { role: sel.dataset.role, provider: sel.value },
          });
          toast("Role updated.");
        } catch (err) { toast(err.message, true); }
      });
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
    watchForChanges(gen, () => api(`/projects/${encodeURIComponent(id)}/settings`), renderSettings);
  }

  async function renderContext() {
    const gen = renderGen;
    const id = parseRoute().project;
    layout(`<div class="empty">Loading…</div>`);
    let ctx;
    try { ctx = await api(`/projects/${encodeURIComponent(id)}/context`); }
    catch (err) { if (!stale(gen)) errorView(err); return; }
    if (stale(gen)) return;
    layout(`
      <div class="title-row"><div><h1>Context</h1><div class="sub">${esc(id)} · ${UI.translate("read-only PM view")}</div></div></div>
      <pre class="panel mono" style="white-space:pre-wrap; overflow:auto">${esc(JSON.stringify(ctx, null, 2))}</pre>
    `);
  }

  async function renderDocs() {
    const gen = renderGen;
    const docRoute = parseRoute();
    const slug = docRoute.sub || "README";
    const language = UI.getLocale() === "zh-TW" ? "ch" : "en";
    layout(`<div class="empty">${esc(UI.translate("Loading documentation…"))}</div>`);
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
      sections.forEach((heading) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "docs-toc-item";
        button.textContent = heading.textContent;
        button.addEventListener("click", () => heading.scrollIntoView({ block: "start" }));
        toc.appendChild(button);
      });
      document.getElementById("docs-layout").classList.remove("no-toc");
      document.getElementById("docs-layout").appendChild(toc);
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
    layout(`<div class="empty">Loading…</div>`);
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
      <tr>
        <td><strong>${esc(skill.name || skill.id)}</strong><div class="table-sub mono">${esc(skill.id)}</div></td>
        <td>${esc(skill.description || "")}</td>
        <td class="table-actions"><button class="btn small danger" data-remove-global-skill="${esc(skill.id)}">Remove</button></td>
      </tr>
    `).join("");
    const discoveredRows = discovered.map((skill, index) => `
      <tr>
        <td><input type="checkbox" data-global-skill-choice="${index}" aria-label="${esc(`${UI.translate("Install for all projects")}: ${skill.name}`)}" ${skill.usable && !skill.already_installed ? "" : "disabled"} /></td>
        <td><strong>${esc(skill.name)}</strong><div class="table-sub">${esc(UI.translate(skill.provider))}</div></td>
        <td>${esc(skill.description || "")}</td>
        <td>${skill.already_installed
          ? `<span class="pill accent">${esc(UI.translate("Already installed"))}</span>`
          : skill.usable
            ? `<span class="pill positive">${esc(UI.translate("Can install"))}</span>`
            : `<span class="pill negative" title="${esc(skill.reason || "")}">${esc(UI.translate("Not usable"))}</span>`}</td>
      </tr>
    `).join("");
    layout(`
      <div class="title-row"><div><h1>Global Skills</h1><div class="sub">${esc(UI.translate("Installed Skills are shared with every project and agent role."))}</div></div><div class="title-actions"><button class="btn" id="refresh-global-skills">Refresh</button></div></div>
      <div class="section">
        <div class="section-heading"><div><h2>Installed for all projects</h2><p>${esc(UI.translate("Removing a Skill here removes it from every project's future handoffs."))}</p></div></div>
        ${installed.length ? `<div class="table-wrap"><table><thead><tr><th>Name</th><th>Description</th><th></th></tr></thead><tbody>${installedRows}</tbody></table></div>` : `<div class="empty">${esc(UI.translate("No global Skills are installed yet."))}</div>`}
      </div>
      <div class="section">
        <div class="section-heading"><div><h2>Discover local Skills</h2><p>${esc(UI.translate("Choose Skills from Claude Code or Codex and install them for every project."))}</p></div></div>
        ${discovered.length ? `
          <div class="skill-bulk-actions">
            <label class="checkbox-row"><input type="checkbox" id="select-all-global-skills" ${available.length ? "" : "disabled"} /><span>${esc(UI.translate("Select all available"))}</span></label>
            <button class="btn primary" id="install-selected-skills" disabled>${esc(UI.translate("Install selected"))} (0)</button>
          </div>
          <div class="table-wrap"><table><thead><tr><th></th><th>Name</th><th>Description</th><th>Status</th></tr></thead><tbody>${discoveredRows}</tbody></table></div>
        ` : `<div class="empty">${esc(UI.translate("No local Skills were found in ~/.claude/skills or ~/.codex/skills."))}</div>`}
      </div>
    `);
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
    layout(`<div class="empty">Loading…</div>`);
    let skills = [], mcp = { discovered: [], installed: {} }, calls = [];
    try {
      [skills, mcp, calls] = await Promise.all([
        api("/skills"),
        api(`/projects/${encodeURIComponent(id)}/mcp`),
        api("/mcp/calls"),
      ]);
    } catch (err) { if (!stale(gen)) errorView(err); return; }
    if (stale(gen)) return;
    const skillRows = skills.map((s) => `<tr><td>${esc(s.name || s.id)}</td><td>${esc(s.description || "")}</td></tr>`).join("");
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
      <tr>
        <td><strong>${esc(item.name)}</strong><div class="table-sub">${esc(UI.translate(item.client))}</div></td>
        <td class="mono">${esc(item.command || "")}</td>
        <td>${item.has_env ? `<span class="pill warning">env</span>` : `<span class="pill neutral">stdio</span>`}</td>
        <td class="table-actions"><button class="btn small" data-install="${index}">Install</button></td>
      </tr>
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
      <div class="section">
        <div class="section-heading"><div><h2>MCP servers</h2><p>Installed for this project — click Tools on a row below to actually call it</p></div></div>
        ${installed.length ? `<div class="table-wrap"><table><thead><tr><th>Name</th><th>Scope</th><th class="num">Tools</th><th>Status</th><th></th></tr></thead><tbody>${mcpRows}</tbody></table></div>` : `<div class="empty">Nothing installed yet — calling a tool needs one install step first. Pick a server below, click Install, choose which tools to allow, then come back here and click Tools to call one.</div>`}
      </div>
      <div class="section">
        <div class="section-heading"><div><h2>Discover local servers</h2><p>${esc(UI.translate("Candidates from MCP client settings and local executables. Select one to verify its tools before installing."))}</p></div><button class="btn small" id="mcp-install-by-name">${esc(UI.translate("Install by name…"))}</button></div>
        ${discovered.length ? `<div class="table-wrap"><table><thead><tr><th>Name</th><th>Command</th><th>Source</th><th></th></tr></thead><tbody>${discoveredRows}</tbody></table></div>` : `<div class="empty">No local MCP servers were discovered.</div>`}
        <p class="table-sub" style="margin-top:10px">${esc(UI.translate("Not listed? Use Install by name to enter a command or an exact executable path."))}</p>
      </div>
      <div class="section">
        <div class="section-heading"><div><h2>Recent MCP calls</h2><p>${esc(id)}</p></div></div>
        ${recent.length ? `<div class="table-wrap"><table><thead><tr><th>Time</th><th>Server</th><th>Tool</th><th>Status</th></tr></thead><tbody>${callRows}</tbody></table></div>` : `<div class="empty">No MCP calls for this project yet.</div>`}
      </div>
      <div class="section">
        <div class="section-heading"><div><h2>Global Skills</h2><p>${esc(UI.translate("These Skills are shared with every project and agent role."))}</p></div><a class="btn small" href="#/global-skills">${esc(UI.translate("Manage global Skills"))}</a></div>
        ${skills.length ? `<div class="table-wrap"><table><thead><tr><th>Name</th><th>Description</th></tr></thead><tbody>${skillRows}</tbody></table></div>` : `<div class="empty">No skills installed.</div>`}
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
      <div class="section">
        <div class="status-row"><span class="status-dot ${dotCls}"></span><strong>${esc(UI.translate(status.state))}</strong>${status.tunnel_id ? ` <span class="mono muted">${esc(status.tunnel_id)}</span>` : ""}</div>
        ${status.error ? `<div class="kv-row"><span class="k">Error</span><span class="v" style="color:var(--negative)">${esc(status.error)}</span></div>` : ""}
        ${!status.tunnel_client_installed ? `<div class="hint">tunnel-client is not installed yet — it downloads and verifies automatically on first connect.</div>` : ""}
        <div id="connect-log-wrap" style="margin-top:16px"></div>
        <div style="margin-top:16px; display:flex; gap:8px">
          ${live ? `<button class="btn danger" id="btn-disconnect">Disconnect</button>` : ""}
        </div>
      </div>
      ${!live ? `
      <div class="section">
        <div class="section-row">
          <div class="section-label"><div class="t">Connection profile</div><div class="d">Save several named connections, each with its own Tunnel ID and runtime key. One connection runs at a time.</div></div>
          <div class="section-panel">
            <input type="hidden" id="connect-profile-id" />
            <div class="field">
              <label for="connect-name">Connection name</label>
              <input type="text" id="connect-name" placeholder="e.g. Work / Personal" maxlength="60" />
            </div>
            <div class="field">
              <label for="connect-tunnel-id">Tunnel ID</label>
              <input type="text" id="connect-tunnel-id" placeholder="tunnel_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" />
            </div>
            <div class="field">
              <label for="connect-api-key">Runtime API key</label>
              <input type="password" id="connect-api-key" placeholder="leave empty to use a remembered key" autocomplete="off" />
              <div class="hint">${esc(UI.translate("Saved keys are never shown again. Enter a new key here to replace one."))}</div>
            </div>
            <div class="checkbox-row" style="margin-bottom:16px">
              <input type="checkbox" id="connect-remember" />
              <label for="connect-remember">Remember this key on this device</label>
            </div>
            <div class="connect-actions"><button class="btn primary" id="btn-connect">Connect</button><button class="btn" id="btn-save-connection">Save profile</button><button class="btn ghost" id="btn-clear-connection">New profile</button></div>
            <div class="hint" id="connect-progress"></div>
          </div>
        </div>
      </div>
      ${savedConnections.length ? `
      <div class="section">
        <div class="section-row">
          <div class="section-label"><div class="t">Saved connections</div><div class="d">Saved to ~/.ccc-mcp/tunnel/credentials.json, readable only by your OS account. Keys are never displayed here.</div></div>
          <div class="section-panel">
            ${savedConnections.map((profile) => `
              <div class="saved-connection">
                <div><strong>${esc(profile.name)}</strong><div class="mono muted">${esc(profile.tunnel_id)}</div></div>
                <span class="connect-row-actions">
                  <button class="btn small" data-use="${esc(profile.id)}">Use</button>
                  <button class="btn small danger" data-forget="${esc(profile.id)}">Forget</button>
                </span>
              </div>
            `).join("")}
          </div>
        </div>
      </div>
      ` : ""}
      ` : ""}
      <div class="section">
        <div class="section-row">
          <div class="section-label"><div class="t">Native CLI login</div><div class="d">Codex and Claude Code sign in independently of the Tunnel; ccc-mcp never stores their credentials.</div></div>
          <div class="section-panel" id="provider-status"><div class="kv-row"><span class="k muted"><span class="spinner small"></span> Checking…</span></div></div>
        </div>
      </div>
    `, { narrow: true });
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
    if (!r.project) return renderDashboard();
    if (r.view === "tasks" && r.sub) return renderTaskDetail();
    if (r.view === "tasks") return renderTasksList();
    if (r.view === "settings") return renderSettings();
    if (r.view === "context") return renderContext();
    if (r.view === "skills") return renderSkills();
    return renderProjectOverview();
  }

  window.addEventListener("hashchange", route);
  route();
})();
