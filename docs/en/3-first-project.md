# 3. Create a first project

After [installation](1-install-and-sign-in.md), create a small project that can be verified locally.

```bash
ccc-mcp project create hello-demo
ccc-mcp project authorize hello-demo
```

Projects are created under `~/ccc-mcpProjects/` by default. Use `--path` for one project in another directory, or register an existing directory instead:

```bash
ccc-mcp project add "/path/to/existing-project" --id hello-demo
ccc-mcp project authorize hello-demo
```

Authorization is a local, explicit grant to modify that one project and run its configured test command. It does not grant access outside the project.

With the ccc-mcp ChatGPT connection selected, ask for a small task such as:

> Create `hello.py` in `hello-demo`; it should print `Hello, World!`, with a test that verifies the output.

Verify from the owning machine:

```bash
ccc-mcp --project hello-demo task status
ccc-mcp --project hello-demo task usage
ccc-mcp --project hello-demo context show
```

`cli_invoked: true` is evidence that a native Codex or Claude CLI actually ran. A chat response alone is not completion evidence.

Continue with [Run and resume the workflow](4-workflow.md).
