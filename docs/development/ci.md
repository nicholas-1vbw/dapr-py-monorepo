# CI

Two GitHub Actions workflows run the same `poe` tasks as developers.

| Workflow | Trigger | Steps |
| --- | --- | --- |
| `.github/workflows/ci.yml` | push to `main`, pull requests | **check**: setup, `poe check`. **integration**: Dapr CLI and slim runtime, setup, `poe integration --workers 2` |
| `.github/workflows/e2e.yml` | manual (`workflow_dispatch`) | kind and kubectl, setup, `poe e2e` |

- Runners are `ubuntu-24.04` (Linux x86_64), the only platform CI verifies.
- Tool versions come from `deploy/platform/versions.yaml` (read with
  `python -m scripts.tasks.versions`): Buf and Dapr, which local runs use too, and the Helm, kind
  and kubectl versions CI installs. Python comes from `.python-version`, dependencies from
  `uv.lock`.
- Integration runs use two workers so that parallel tasks are exercised on every change.
- Run records (`.run/`) are uploaded as artifacts when integration tests fail, and always after
  end-to-end runs.

Run the end-to-end workflow from the Actions tab (**E2E → Run workflow**) before merging changes to
images, charts, Dapr versions or anything the end-to-end scenarios cover.
