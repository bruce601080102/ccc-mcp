// "Skills & MCP": the two ways to give the AI team more to work with.
//
// * A Skill is a written guide (a SKILL.md file) that is added to every role's briefing. It is installed
//   once and shared by every project.
// * An MCP server is a small local program that gives the AI extra abilities. It is installed for one
//   project, and only the tools you tick may be used.
//
// Everything from the server is put into the page through esc().
window.cccSkills = function (ctx) {
  "use strict";
  const { api, esc, UI, layout, toast, showModal, confirmTextModal, mountModal, closeModal, stale, getGen,
    loadingView, errorView, rememberedProject, pageCard } = ctx;
  const T = (text) => esc(UI.translate(text));
  let chosenProject = "";

  async function render() {
    const gen = getGen();
    layout(loadingView());
    let installed = [], discovered = [], projects = [];
    try {
      [installed, discovered, projects] = await Promise.all([api("/skills"), api("/skills/discover"), api("/projects")]);
    } catch (err) { if (!stale(gen)) errorView(err); return; }
    if (stale(gen)) return;
    const wanted = chosenProject || rememberedProject();
    const projectId = (projects.find((p) => p.id === wanted) || projects[0] || {}).id || "";
    chosenProject = projectId;
    let mcp = { discovered: [], installed: {} };
    let calls = [];
    if (projectId) {
      try {
        [mcp, calls] = await Promise.all([api(`/projects/${encodeURIComponent(projectId)}/mcp`), api("/mcp/calls")]);
      } catch (err) { if (!stale(gen)) errorView(err); return; }
      if (stale(gen)) return;
    }

    // -- Skills
    const installedRows = installed.map((skill) => `
      <div class="skill-card has-action">
        <div class="skill-card-head"><div><strong>${esc(skill.name || skill.id)}</strong><span class="skill-id mono">${esc(skill.id)}</span></div><button type="button" class="btn small danger" data-remove-skill="${esc(skill.id)}">${T("Remove")}</button></div>
        <p>${esc(skill.description || "")}</p>
      </div>`).join("");
    const available = discovered.map((skill, index) => ({ skill, index })).filter(({ skill }) => skill.usable && !skill.already_installed);
    const discoveredRows = discovered.map((skill, index) => `
      <li class="pick-row ${skill.usable && !skill.already_installed ? "" : "is-muted"}">
        <input type="checkbox" data-skill-choice="${index}" aria-label="${esc(`${UI.translate("Install for all projects")}: ${skill.name}`)}" ${skill.usable && !skill.already_installed ? "" : "disabled"} />
        <div class="pick-main"><strong>${esc(skill.name)}</strong><span class="server-client">${esc(skill.provider)}</span><p>${esc(skill.description || "")}</p></div>
        ${skill.already_installed ? `<span class="pill positive">${T("Installed")}</span>`
          : skill.usable ? "" : `<span class="pill negative" title="${esc(skill.reason || "")}">${T("Not usable")}</span>`}
      </li>`).join("");

    // -- MCP servers
    const servers = Object.entries(mcp.installed || {});
    const serverRows = servers.map(([name, spec]) => `
      <tr>
        <td><strong>${esc(name)}</strong><div class="table-sub mono">${esc(spec.command || "")}</div></td>
        <td><span class="pill ${spec.scope === "global" ? "neutral" : "accent"}">${T(spec.scope === "global" ? "Global" : "Project only")}</span></td>
        <td class="num">${esc((spec.allowed_tools || []).length)}</td>
        <td>${spec.command_exists ? `<span class="pill positive">${T("found")}</span>` : `<span class="pill negative">${T("missing")}</span>`}</td>
        <td class="table-actions"><button type="button" class="btn small" data-tools="${esc(name)}" ${spec.command_exists ? "" : "disabled"}>${T("Tools")}</button>${spec.scope === "project" ? `<button type="button" class="btn small danger" data-remove-server="${esc(name)}">${T("Remove")}</button>` : `<span class="muted" title="${esc(UI.translate("This server is installed globally. Remove it with the CLI if you intend to affect every project."))}"> · ${T("Global")}</span>`}</td>
      </tr>`).join("");
    const found = (mcp.discovered || []).filter((item) => item.transport === "stdio");
    const foundRows = found.map((item, index) => `
      <li class="server-row">
        <span class="server-avatar" aria-hidden="true">${esc((item.name || "?").slice(0, 1).toUpperCase())}</span>
        <div class="server-main"><strong>${esc(item.name)}</strong><span class="server-client">${T(item.client)}</span><code class="server-command" title="${esc(item.command || "")}">${esc(item.command || "")}</code></div>
        ${item.has_env ? `<span class="pill warning">env</span>` : `<span class="pill neutral">stdio</span>`}
        <button type="button" class="btn small" data-install="${index}">${T("Install")}</button>
      </li>`).join("");
    const recent = calls.filter((entry) => entry.project === projectId).slice(-8).reverse();
    const callRows = recent.map((entry) => `
      <tr><td class="mono">${esc(entry.ts || "")}</td><td>${esc(entry.server || "")}</td><td class="mono">${esc(entry.tool || "")}</td>
        <td>${entry.error || entry.is_error ? `<span class="pill negative">${T("Error")}</span>` : `<span class="pill positive">${T("Succeeded")}</span>`}</td></tr>`).join("");

    const picker = projects.length ? `
      <div class="team-flow-row">
        <label class="team-flow-label" for="skills-project">${T("Project")}</label>
        <select id="skills-project">${projects.map((p) => `<option value="${esc(p.id)}"${p.id === projectId ? " selected" : ""} data-no-i18n>${esc(p.id)}</option>`).join("")}</select>
      </div>
      <p class="hint">${T("MCP servers are installed for one project. The servers below belong to the project chosen here.")}</p>` : "";
    const mcpBody = !projectId
      ? `<div class="empty">${T("Add a project on the Projects page first. MCP servers are installed for a project.")} <a href="#/">${T("Open Projects")}</a></div>`
      : `${picker}
        ${servers.length ? `<div class="table-wrap"><table><thead><tr><th>${T("Name")}</th><th>${T("Scope")}</th><th class="num">${T("Tools")}</th><th>${T("Status")}</th><th></th></tr></thead><tbody>${serverRows}</tbody></table></div>`
          : `<div class="empty">${T("Nothing installed yet. Pick a server in the list below, click Install, choose which tools the AI may use, then come back here and click Tools to try one.")}</div>`}`;

    layout(`
      <div class="title-row"><div><h1>Skills &amp; MCP</h1><div class="sub">${T("Extra guidance and local tools your AI team can use.")}</div></div>
        <div class="title-actions"><button type="button" class="btn" id="skills-refresh">${T("Refresh")}</button></div></div>
      <div class="settings-stack">
        ${pageCard("package", "Skills", esc(UI.translate("A Skill is a written guide (a SKILL.md file) added to every AI role's briefing, such as your team's coding style. Install it once and every project and role can use it.")),
          installed.length ? `<div class="skill-grid wide">${installedRows}</div>` : `<div class="empty">${T("No Skills are installed yet. Pick some from the list below.")}</div>`)}
        ${pageCard("search", "Skills found on this computer", esc(UI.translate("Skills already installed for Claude Code or Codex. Tick the ones to share with every project and role.")),
          discovered.length ? `
            <div class="skill-bulk-actions">
              <label class="checkbox-row"><input type="checkbox" id="skills-select-all" ${available.length ? "" : "disabled"} /><span>${T("Select all available")}</span></label>
              <button type="button" class="btn small primary" id="skills-install-selected" disabled>${T("Install selected")} (0)</button>
            </div>
            <ul class="server-list pick-list">${discoveredRows}</ul>`
            : `<div class="empty">${T("No local Skills were found in ~/.claude/skills or ~/.codex/skills.")}</div>`)}
        ${pageCard("server", "MCP servers", esc(UI.translate("An MCP server is a small local program that gives the AI extra abilities, such as searching a code index. You choose which of its tools the AI may use.")),
          mcpBody, "", { flush: projectId && servers.length > 0 })}
        ${projectId ? pageCard("search", "MCP servers found on this computer", esc(UI.translate("Found in other apps' settings and on your PATH. Install one to check its tools before the AI may use them.")),
          found.length ? `<ul class="server-list">${foundRows}</ul>` : `<div class="empty">${T("No local MCP servers were discovered.")}</div>`,
          `<span class="table-sub">${T("Not listed? Use Install by name to enter a command or an exact executable path.")}</span>`,
          { actions: `<button type="button" class="btn small" id="mcp-install-by-name">${T("Install by name…")}</button>` }) : ""}
        ${projectId ? pageCard("clock", "Recent MCP calls", esc(projectId),
          recent.length ? `<div class="table-wrap"><table><thead><tr><th>${T("Time")}</th><th>${T("Server")}</th><th>${T("Tool")}</th><th>${T("Status")}</th></tr></thead><tbody>${callRows}</tbody></table></div>` : `<div class="empty">${T("No MCP calls for this project yet.")}</div>`,
          "", { flush: recent.length > 0 }) : ""}
      </div>`);

    document.getElementById("skills-refresh").onclick = render;
    const picked = document.getElementById("skills-project");
    if (picked) picked.onchange = () => { chosenProject = picked.value; render(); };

    document.querySelectorAll("[data-remove-skill]").forEach((button) => {
      button.onclick = () => {
        const skillId = button.dataset.removeSkill;
        confirmTextModal({
          title: "Remove Skill",
          description: `${skillId}\n\n${UI.translate("This removes the Skill from every project's future handoffs. Source files on your computer remain unchanged.")}`,
          challenge: `REMOVE SKILL ${skillId}`, confirmLabel: "Remove",
          onConfirm: async (typed) => {
            await api(`/skills/${encodeURIComponent(skillId)}/remove`, { method: "POST", body: { confirm: typed } });
            toast("Skill removed.");
            await render();
          },
        });
      };
    });

    const all = document.getElementById("skills-select-all");
    const install = document.getElementById("skills-install-selected");
    const choices = [...document.querySelectorAll("[data-skill-choice]")];
    const picks = () => choices.filter((input) => input.checked && !input.disabled);
    const syncPicks = () => {
      install.textContent = `${UI.translate("Install selected")} (${picks().length})`;
      install.disabled = !picks().length;
      all.checked = Boolean(available.length) && picks().length === available.length;
    };
    if (all) {
      all.onchange = () => { choices.forEach((input) => { if (!input.disabled) input.checked = all.checked; }); syncPicks(); };
      choices.forEach((input) => { input.onchange = syncPicks; });
      install.onclick = async () => {
        install.disabled = true;
        let done = 0;
        try {
          for (const input of picks()) {
            const skill = discovered[Number(input.dataset.skillChoice)];
            await api("/skills/install-discovered", { method: "POST", body: { id: skill.id, path: skill.path } });
            done += 1;
          }
          toast("Selected Skills installed for all projects.");
        } catch (err) {
          toast(done ? `${UI.translate("Some selected Skills were installed before an error.")} ${err.message}` : err.message, true);
        }
        await render();
      };
    }

    document.querySelectorAll("[data-install]").forEach((button) => {
      button.onclick = () => openMcpInstall(projectId, found[Number(button.dataset.install)]);
    });
    const byName = document.getElementById("mcp-install-by-name");
    if (byName) byName.onclick = () => openMcpInstallByName(projectId);
    document.querySelectorAll("[data-tools]").forEach((button) => {
      button.onclick = () => openMcpTools(projectId, button.dataset.tools);
    });
    document.querySelectorAll("[data-remove-server]").forEach((button) => {
      button.onclick = () => {
        const name = button.dataset.removeServer;
        showModal({
          title: "Remove from project",
          body: `${name}\n${UI.translate("This removes this project's MCP access. The MCP program itself stays installed on your computer.")}`,
          confirmLabel: "Remove",
          danger: true,
          onSubmit: async () => {
            await api(`/projects/${encodeURIComponent(projectId)}/mcp/${encodeURIComponent(name)}/remove`, { method: "POST" });
            toast("MCP server removed.");
            await render();
          },
        });
      };
    });
  }

  // ---- install and try an MCP server ---------------------------------------------------------------

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
        await render();
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
          await render();
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

  return { render };
};
