(() => {
  "use strict";

  const zh = {
    "Interface language": "介面語言", "CONTROL CENTER": "控制中心", "LOCAL CONSOLE": "本機控制台",
    "All projects": "所有專案", "Overview": "總覽", "Tasks": "任務", "Settings": "設定",
    "Documentation": "文件", "Loading documentation…": "正在載入文件…",
    "Guides for setup, daily use, and troubleshooting.": "安裝、日常使用與疑難排解指南。", "All guides": "所有指南", "On this page": "本頁章節",
    "Context": "脈絡", "Skills & MCP": "Skills 與 MCP", "Connect": "連線",
    "Projects": "專案", "Every project registered with this ccc-mcp home.": "這個 ccc-mcp 工作空間已註冊的專案。",
    "Add existing folder": "加入現有資料夾", "Create new project": "建立新專案",
    "Your workspace": "你的工作空間", "registered projects": "個已註冊專案", "Open project": "開啟專案",
    "New group": "新增群組", "Ungrouped": "未分類", "Move to": "移至", "Move project to group": "將專案移至群組",
    "Group": "群組", "Move group up": "將群組上移", "Move group down": "將群組下移",
    "Rename": "重新命名", "Rename group": "重新命名群組", "Delete group": "刪除群組", "Group name": "群組名稱",
    "Chinese name": "中文名稱", "English name": "英文名稱",
    "Shown when the interface is in Traditional Chinese.": "介面切換成繁體中文時顯示。",
    "Shown when the interface is in English.": "介面切換成英文時顯示。",
    "Set the names shown for each interface language.": "設定各介面語言顯示的群組名稱。",
    "Enter a Chinese or English group name": "請輸入中文或英文群組名稱",
    "Group projects on this device without changing their folders or settings.": "只在此裝置替專案分組，不會變更專案資料夾或設定。",
    "Projects in this group will become ungrouped. No project files are deleted.": "群組中的專案將回到未分類；不會刪除任何專案檔案。",
    "Drag projects here, or use Move to on a card.": "拖曳專案到此處，或使用卡片上的「移至」。",
    "Read-write": "可讀寫", "Read-only": "唯讀", "read-write": "可讀寫", "read-only": "唯讀",
    "Project": "專案", "Path": "路徑", "Access": "存取權限", "No projects yet. Add an existing folder or create a new one.": "目前沒有專案。加入現有資料夾或建立新專案。",
    "Folder": "資料夾", "Project ID (optional)": "專案 ID（選填）", "Project ID": "專案 ID",
    "Register a folder that already exists as a ccc-mcp project. This never creates or modifies any file in it unless you also enable write below.": "將現有資料夾註冊為 ccc-mcp 專案。若未啟用寫入，不會變更資料夾內的檔案。",
    "Also enable continuous write authorization now": "同時啟用持續寫入授權",
    "ccc-mcp will be able to write files inside this folder and run its configured test command automatically before each review round. Dependency installation can run package scripts, which are inside this same boundary.": "ccc-mcp 將可寫入此資料夾，並在審查前自動執行設定的測試指令。安裝相依套件也可能執行套件腳本。",
    "Add project": "加入專案", "Creates a new directory with a Git baseline. It never overwrites an existing folder.": "建立新的資料夾和 Git 基準，不會覆寫現有資料夾。",
    "Path (optional)": "路徑（選填）", "Create": "建立", "Cancel": "取消", "Confirm": "確認",
    "Type": "輸入", "to confirm": "以確認", "Confirmation text did not match.": "確認文字不符。",
    "Project ID is required.": "請輸入專案 ID。", "Project not found": "找不到專案",
    "View tasks": "查看任務", "Mode": "模式", "Authorized at": "授權時間", "Identity": "身分識別",
    "Whether ccc-mcp can write files in this project and run its test command.": "ccc-mcp 是否可以寫入此專案並執行其測試指令。",
    "Revoke write (back to read-only)": "撤銷寫入權限", "Enable write": "啟用寫入",
    "Enable continuous write": "啟用持續寫入", "Write authorization enabled.": "已啟用寫入授權。",
    "Clear workflow history": "清除工作流程紀錄", "Clear history…": "清除紀錄…", "Clear history": "清除紀錄",
    "Archives the database and resets tasks/context for this project. Source files, settings and authorization are preserved.": "封存資料庫並重設此專案的任務與 Context。原始碼、設定與授權會保留。",
    "Deregister project": "註銷專案", "Delete project…": "註銷專案…", "Delete project": "註銷專案",
    "Permanently retires this project ID. Never deletes files on disk or historical records. Cannot be undone.": "永久註銷此專案 ID；不刪除磁碟檔案或歷史紀錄。這項操作無法復原。",
    "Revoke write access first.": "請先撤銷寫入權限。", "Revoke write access": "撤銷寫入權限",
    "ccc-mcp will no longer be able to write files in this project or run its test command automatically.": "ccc-mcp 將無法再寫入此專案或自動執行測試指令。",
    "Revoke": "撤銷", "Reverted to read-only.": "已改為唯讀。", "History cleared.": "已清除紀錄。", "Project deregistered.": "已註銷專案。",
    "State": "狀態", "Requirement": "需求", "Created": "建立時間", "No tasks yet for this project.": "此專案尚無任務。",
    "Tasks created through ChatGPT or the CLI will appear here.": "透過 ChatGPT 或 CLI 建立的任務會顯示在這裡。",
    "Back to tasks": "返回任務", "Waiting on": "等待項目", "Agent / role": "Agent／角色", "Execution status": "執行狀態",
    "Host verification": "主機驗證", "Summary": "摘要", "Next action": "下一步", "Executions": "執行紀錄",
    "Execution": "執行", "Role": "角色", "Status": "狀態", "Error": "錯誤", "No executions yet.": "尚無執行紀錄。",
    "RUNNING": "執行中", "SUCCEEDED": "成功", "TIMED_OUT": "逾時", "PENDING": "等待中",
    "Usage & CLI evidence": "用量與 CLI 證據", "Raw result": "原始結果", "host tests not run": "主機測試未執行",
    "no tests collected": "沒有收集到測試", "host tests passed": "主機測試通過", "host tests failed": "主機測試失敗",
    "Profile": "設定檔", "profile": "設定檔", "read-only PM view": "PM 唯讀檢視", "Apply a bundled provider combination to every role at once.": "一次為所有角色套用預設的 Provider 組合。",
    "Roles": "角色", "Provider assigned to each workflow role.": "各工作流程角色使用的 Provider。",
    "Language": "報告語言", "What Codex/Claude/ChatGPT receive in reports.": "Codex、Claude、ChatGPT 收到的報告語言。",
    "Project runtime": "專案執行環境", "Programming language": "程式語言", "Runtime version": "執行環境版本",
    "Default environment": "預設環境", "Local executable (optional)": "本機執行檔（選填）",
    "Choose the language and installed runtime used for this project's code and tests.": "指定此專案開發與測試使用的程式語言及已安裝的執行環境。",
    "3.9 accepts any 3.9.x patch. JavaScript and TypeScript use the Node.js version.": "3.9 代表任意 3.9.x 修補版本。JavaScript 與 TypeScript 填入 Node.js 版本。",
    "Absolute executable path or command name, without arguments. Leave empty to find an installed runtime.": "填入執行檔的絕對路徑或指令名稱，不含參數；留空會尋找已安裝的環境。",
    "Python uses a separate virtual environment per selection. Set a test command for other languages. Install the required runtime locally first.": "Python 會依設定使用獨立虛擬環境；其他語言請另外設定測試指令。請先在本機安裝需要的執行環境。",
    "Save runtime": "儲存執行環境", "Check local version": "檢查本機版本", "Runtime saved.": "已儲存執行環境。",
    "Runtime saved. Check the local version to verify availability.": "已儲存設定。請檢查本機版本，確認環境可用。",
    "Runtime available": "執行環境可用", "Runtime unavailable or version mismatch": "找不到環境或版本不符",
    "Default environment; no runtime version selected.": "使用預設環境；未指定執行版本。",
    "e.g. 3.9 or 22.14.0": "例如 3.9 或 22.14.0", "e.g. C:/Python39/python.exe": "例如 C:/Python39/python.exe",
    "Test command": "測試指令", "Runs on the host automatically before each REVIEW round, once the project has write access.": "專案取得寫入權限後，在每輪 REVIEW 前於主機自動執行。",
    "Space-separated argv, never a shell string.": "以空格分隔參數；不使用 shell 字串。", "Save": "儲存",
    "Meeting Mode": "會議模式", "Bounded SD/RD↔SA clarification rounds before implementation.": "實作前限定 SD/RD 與 SA 的釐清回合數。",
    "Enabled": "已啟用", "Max rounds (0-5)": "最多回合（0–5）", "Role updated.": "已更新角色。",
    "Profile applied.": "已套用設定檔。", "Language updated.": "已更新報告語言。", "Test command saved.": "已儲存測試指令。", "Meeting Mode saved.": "已儲存會議模式。",
    "Installed skills": "已安裝 Skills", "Name": "名稱", "Description": "說明", "No skills installed.": "尚未安裝 Skills。",
    "MCP servers": "MCP 伺服器", "Scope": "範圍", "Command": "執行檔", "found": "可用", "missing": "找不到執行檔",
    "No MCP servers installed for this project.": "此專案尚未安裝 MCP 伺服器。",
    "Link this ccc-mcp home to ChatGPT over an OpenAI Platform Tunnel.": "透過 OpenAI Platform Tunnel 將此 ccc-mcp 工作空間連接至 ChatGPT。",
    "Disconnect": "中斷連線", "New connection": "新增連線", "Tunnel ID": "Tunnel ID", "Runtime API key": "Runtime API Key",
    "Connection profile": "連線設定", "Connection name": "連線名稱", "Save profile": "儲存設定", "New profile": "新增設定", "Saved connections": "已儲存的連線",
    "Save several named connections, each with its own Tunnel ID and runtime key. One connection runs at a time.": "可以儲存多組具名稱的連線，各自使用不同 Tunnel ID 與金鑰；同時間只執行一組連線。",
    "Saved keys are never shown again. Enter a new key here to replace one.": "已儲存的金鑰不會再次顯示。輸入新金鑰即可替換。",
    "Saved to ~/.ccc-mcp/tunnel/credentials.json, readable only by your OS account. Keys are never displayed here.": "儲存在 ~/.ccc-mcp/tunnel/credentials.json，僅本機帳號可讀；此頁不會顯示金鑰。",
    "Connection saved.": "已儲存連線。", "e.g. Work / Personal": "例如：工作／個人",
    "Forget connection": "忘記連線", "This removes the saved key from this device. It does not revoke the key at OpenAI.": "這會從此裝置刪除已儲存的金鑰，不會撤銷 OpenAI 上的金鑰。",
    "The OpenAI Platform runtime Secret Key is entered locally and only ever sent to the tunnel-client subprocess on this machine.": "OpenAI Platform Runtime Secret Key 只在本機輸入，並傳給本機 tunnel-client 子程序。",
    "Remember this key on this device": "在此裝置記住金鑰", "Remembered tunnels": "已記住的 Tunnel",
    "Saved to ~/.ccc-mcp/tunnel/credentials.json, readable only by your OS account.": "儲存在 ~/.ccc-mcp/tunnel/credentials.json，僅本機使用者帳號可讀。",
    "Use": "使用", "Forget": "忘記", "Native CLI login": "原生 CLI 登入",
    "Codex and Claude Code sign in independently of the Tunnel; ccc-mcp never stores their credentials.": "Codex 和 Claude Code 分別登入；ccc-mcp 不會儲存其憑證。",
    "not installed": "未安裝", "signed in": "已登入", "not signed in": "未登入",
    "tunnel-client is not installed yet — it downloads and verifies automatically on first connect.": "尚未安裝 tunnel-client；首次連線時會自動下載並驗證。",
    "Waiting for tunnel-client output…": "等待 tunnel-client 輸出…", "Connecting…": "連線中…", "Connected.": "已連線。", "Disconnected.": "已中斷連線。", "Forgotten.": "已忘記。", "Tunnel ID is required.": "請輸入 Tunnel ID。",
    "Could not check native CLI login status.": "無法檢查原生 CLI 登入狀態。",
    "Still checking — verifying the runtime key and tunnel access can take up to a minute…": "仍在檢查；驗證 Runtime Key 與 Tunnel 存取權可能需要約一分鐘…",
    "Loading…": "載入中…", "Loading projects…": "正在載入專案…", "Checking…": "檢查中…", ".. (parent)": "..（上層資料夾）", "New data is available.": "有新資料可供載入。",
    "derived from folder name": "由資料夾名稱產生", "leave empty to use the default projects directory": "留空則使用預設專案資料夾",
    "e.g. python -m pytest -q": "例如 python -m pytest -q", "leave empty to use a remembered key": "留空則使用已記住的金鑰",
    "MCP tools": "MCP 工具", "Available on this computer": "此電腦可使用", "Installed for this project": "此專案已安裝",
    "Manage local tools available to this project's agents.": "管理此專案 Agent 可使用的本機工具。",
    "Recent MCP calls": "最近的 MCP 呼叫", "Discover local servers": "探索本機伺服器", "Refresh": "重新整理",
    "Source": "來源", "Tools": "工具", "Install": "安裝", "Remove": "移除", "Project only": "僅此專案", "Global": "全域", "Time": "時間", "Server": "伺服器", "Tool": "工具", "Succeeded": "成功",
    "No local MCP servers were discovered.": "未發現本機 MCP 伺服器。", "No MCP calls for this project yet.": "此專案尚無 MCP 呼叫紀錄。",
    "This server is installed globally. Remove it with the CLI if you intend to affect every project.": "此伺服器為全域安裝。若要影響所有專案，請使用 CLI 移除。",
    "Select tools to enable": "選擇要啟用的工具", "No tools are selected. Choose at least one.": "尚未選擇工具，請至少勾選一項。",
    "Checking server and tools…": "正在檢查伺服器與工具…", "Install to project": "安裝到專案", "Remove from project": "從專案移除",
    "The MCP process will be started to verify its tools. Only selected tools will be available to this project's agents.": "系統會啟動 MCP 程序確認工具。此專案的 Agent 只可使用勾選的工具。",
    "The server defines environment variables in another client. They are not copied into ccc-mcp.": "此伺服器在其他 Client 設有環境變數；ccc-mcp 不會複製這些變數。",
    "No tools were exposed by this server.": "此伺服器未提供工具。", "MCP server installed.": "已安裝 MCP 伺服器。", "MCP server removed.": "已移除 MCP 伺服器。",
    "Installed for this project — click Tools on a row below to actually call it": "此專案已安裝——點選下方列的「工具」才能真正呼叫",
    "Nothing installed yet — calling a tool needs one install step first. Pick a server below, click Install, choose which tools to allow, then come back here and click Tools to call one.": "尚未安裝任何伺服器——要呼叫工具，得先安裝一次。從下方清單選一個伺服器點選「安裝」、勾選要允許的工具，安裝完成後回到這裡點「工具」即可呼叫。",
    "Available on this computer — Install here, then use Tools above to call it": "此電腦可使用——在這裡安裝，再到上方用「工具」呼叫",
    "MCP server installed. Opening its tools now…": "已安裝 MCP 伺服器，正在開啟工具…",
    "Read only": "唯讀", "Can change data": "可變更資料",
    "Choose an enabled tool and provide JSON arguments. Its result is shown here and is not saved by this page.": "選擇已啟用的工具並填入 JSON 參數。結果會顯示在這裡，頁面不會儲存結果。",
    "Input schema": "輸入格式", "Arguments (JSON)": "參數（JSON）", "No enabled tools are available.": "目前沒有可用的已授權工具。",
    "Close": "關閉", "Run tool": "執行工具", "Running tool…": "工具執行中…", "Tool result": "工具結果", "Tool returned an error": "工具回傳錯誤", "truncated": "已截斷",
    "Arguments must be a JSON object.": "參數必須是 JSON 物件。",
    "This removes this project's MCP access. The MCP program itself stays installed on your computer.": "這會移除此專案的 MCP 使用權；電腦上的程式仍會保留。",
    "CREATED": "已建立", "ANALYZING": "分析中", "AWAITING_PM_DECISION": "等待 PM 決定",
    "AWAITING_USER_APPROVAL": "等待使用者核准", "APPROVED": "已核准", "IMPLEMENTING": "實作中",
    "AWAITING_SA_CLARIFICATION": "等待 SA 釐清", "REVIEWING": "審查中", "AWAITING_PM_REVIEW": "等待 PM 審查",
    "COMPLETED": "已完成", "FAILED": "失敗", "CANCELLED": "已取消", "REJECTED": "已拒絕", "BLOCKED": "受阻",
    "NEEDS_REVISION": "需要修訂", "AWAITING_FINAL_ACCEPTANCE": "等待最終驗收",
    "connected": "已連線", "disconnected": "未連線", "starting": "啟動中", "stopping": "停止中", "error": "錯誤",
  };

  let locale;
  try { locale = localStorage.getItem("ccc_ui_locale"); } catch (_) {}
  if (locale !== "en" && locale !== "zh-TW") {
    locale = navigator.language && navigator.language.toLowerCase().startsWith("zh") ? "zh-TW" : "en";
  }
  document.documentElement.lang = locale;
  document.title = locale === "zh-TW" ? "ccc-mcp 控制台" : "ccc-mcp console";

  function translate(value) {
    if (locale !== "zh-TW") return value;
    return zh[value] || value;
  }

  function localize(container) {
    if (locale !== "zh-TW") return;
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const parent = node.parentElement;
      if (!parent || parent.closest(".mono, pre, code, [data-no-i18n]")) continue;
      if (parent.closest("td") && !parent.closest("button, .pill")) continue;
      const original = node.nodeValue;
      const trimmed = original.trim();
      if (zh[trimmed]) node.nodeValue = original.replace(trimmed, zh[trimmed]);
    }
    container.querySelectorAll("[placeholder], [aria-label]").forEach((element) => {
      for (const attr of ["placeholder", "aria-label"]) {
        const value = element.getAttribute(attr);
        if (zh[value]) element.setAttribute(attr, zh[value]);
      }
    });
  }

  window.cccI18n = {
    getLocale: () => locale,
    setLocale(value) {
      locale = value === "zh-TW" ? "zh-TW" : "en";
      try { localStorage.setItem("ccc_ui_locale", locale); } catch (_) {}
      document.documentElement.lang = locale;
      document.title = locale === "zh-TW" ? "ccc-mcp 控制台" : "ccc-mcp console";
    },
    translate,
    localize,
  };
})();
