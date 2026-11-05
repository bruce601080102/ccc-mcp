# Repository workflow policy

- GitHub Actions must never start automatically after a push, pull request, tag, or schedule. `CI` and `Release` are started by the user with `workflow_dispatch` only. A reusable workflow may use `workflow_call` only when called by those manual workflows.
- Do not add automatic GitHub Actions triggers unless the user explicitly changes this policy.
- The user chooses the release version in the manual GitHub Release form. Do not choose or bump a release version on the user's behalf.
