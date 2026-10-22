# 2. Connect ChatGPT

ccc-mcp runs locally. A Secure MCP Tunnel lets the ChatGPT web app call that local server without exposing an unauthenticated endpoint.

## Connect

1. Create a Tunnel in the OpenAI Platform and copy its `tunnel_...` ID and runtime API key.
2. In the terminal where ccc-mcp is installed, run:

   ```bash
   ccc-mcp connect tunnel_your_actual_id
   ```

3. Enter the runtime API key into the hidden terminal prompt—never paste it into chat or a repository.
4. Keep the terminal running, then add/select the corresponding ccc-mcp connection in ChatGPT.

`connect` verifies the local CLI and Tunnel before work begins. Fix a failed check before attempting a project request.

Add `--reload` to keep that terminal running across a later `ccc-mcp update`: it watches the installed ccc-mcp package (version and file timestamps) and automatically restarts the tunnel connection the moment it changes, reusing the runtime key already entered so you are never prompted for it again. Without `--reload`, an update to ccc-mcp requires Ctrl+C and a fresh `connect` (and a fresh key prompt) to take effect.

## Diagnose a connection

- A 401 from Tunnel poll normally means the runtime key or Tunnel ID is wrong; recreate or copy the current value and reconnect.
- If ChatGPT cannot see tools such as `list_workspace` or `read_workspace`, verify that the correct connection is selected and that the local `connect` process is still running.
- For a local probe, use the repository's `examples/mcp_probe.py` after the server is running.

An HTTPS + OAuth deployment is possible for an externally hosted service, but the Secure MCP Tunnel is the intended path for a personal local setup.

Continue with [Create a first project](3-first-project.md).
