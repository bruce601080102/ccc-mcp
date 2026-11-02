# Agensi listing fields for ccc-mcp

Upload `ccc-mcp-agensi-skill.zip`. Select **Free**.

**Name:** ccc-mcp Local Coding Workflow

**Summary:** Guide an AI coding agent to use the open-source ccc-mcp MCP server with an authorized local repository, including task handoffs, host tests, diffs, review, and saved Context.

**Description:**

ccc-mcp connects ChatGPT Chat to an authorized local coding workspace, with optional Codex and Claude Code roles. This free skill helps an agent recognize when ccc-mcp is useful, check whether its MCP tools are connected, follow an existing task, and report real test and review evidence. It points to setup steps when the server is not installed. The skill does not install ccc-mcp, grant write permission, or provide a ChatGPT plan.

Open-source project and setup guide: https://github.com/bruce601080102/ccc-mcp

**Compatibility note:** Requires separately installed ccc-mcp, Python 3.11+, Git, and a configured ccc-mcp MCP connection for tool calls. ChatGPT-only setup also requires a ChatGPT connection through an OpenAI Platform Tunnel.

**Docs link:** https://github.com/bruce601080102/ccc-mcp

**Suggested categories:** Agents & Orchestration; Testing & Debugging; DevOps & Cloud

**Suggested tags:** mcp, chatgpt, coding-agent, local-development, testing, multi-agent

**See it in action:**

User: “Continue my ccc-mcp task in the astropy repository. Tell me what was changed and which tests really passed.”

Agent: Checks whether ccc-mcp tools are connected. If they are, it identifies the registered project and current task, reads the saved handoff and Context, and resumes the pending step only when appropriate. It reports the files changed, the exact test command and host-observed result, the review status, and anything still unverified. If ccc-mcp tools are absent, it explains how to install ccc-mcp and connect the MCP server instead of claiming to have accessed the user's machine.

This example describes the workflow; it is not a claim that a particular test run occurred.
