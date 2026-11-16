# Testing

| Layer | Location | Runs on | Proves |
| --- | --- | --- | --- |
| Unit | `<member>/tests/` | pytest only: no Dapr, no Docker | logic, validation, error mapping, startup checks with fakes |
| Integration | `tests/integration/<member>/` | pytest under `dapr run`, with the infrastructure instances enabled for `integration` | Dapr APIs, service invocation, component checks, trace propagation |
| End-to-end | `tests/e2e/` | every app from its image, with its sidecar, on kind with the standard Dapr control plane | the whole system from the outside: the apps working together, including tracing |

Test each behaviour in the cheapest layer that can prove it. A rendered chart does not prove a
component loads; a successful call does not prove invalid input is rejected.

## Unit tests

```bash
uv run poe unit                 # all unit tests
uv run pytest packages/greeting # one member
```

Plain `pytest` collects only unit tests (`testpaths` in the root `pyproject.toml`); async tests run
with pytest-asyncio. They need no Dapr, Docker or other tool. Fake Dapr behind small interfaces (`StateStore`, `VisitCounter`) instead of
mocking the SDK.

## Integration tests

```bash
uv run poe integration                          # every member with integration tests, one at a time
uv run poe integration --workers 2              # two package tasks in parallel
uv run poe integration --packages greeting      # selected members
```

How a run works:

1. Validate arguments: `--packages` names must be workspace members with a directory in
   `tests/integration/`; errors stop the run before anything is created.
2. Start the infrastructure instances enabled for `integration` once for the run (Docker
   Compose, no fixed host ports) and render the Dapr resources once into `.run/<run-id>/`, without
   scopes.
3. Run one task per package, at most `--workers` at a time (default 1):

   ```text
   dapr run --app-id it-<run>-<package> --app-port <free> ... -- python -m pytest tests/integration/<package>
   ```

   Each task has its own sidecar, app id, ports and pytest process; all tasks share the
   infrastructure and the resources. Tasks also get the `appEnv` variables of the instances.
4. Tear down the run's containers and processes, and print one line per package with its log.
   JUnit reports and logs stay in `.run/<run-id>/packages/<package>/`.

A failed package does not stop the others; the run exits non-zero. Ctrl+C stops the running tasks
and cleans up.

### Writing integration tests

The fixtures in `tests/integration/conftest.py` provide `it_env` (run id, app id, app port) and
`unique_name`. `tests/integration/support.py` has `serve(app, port)`, which runs an app in
a background thread.

- **Calls that need Dapr to reach your code** (service invocation): serve the app on
  `it_env.app_port`, the port the sidecar forwards to, and call it through the sidecar with
  `dapr_client(<GeneratedClient>, it_env.app_id, ...)`. See `tests/integration/greeting/`.
- **Outbound calls only** (state, metadata): use the Dapr client directly; no server needed. See
  `tests/integration/app-runtime/`.
- **Isolate data**: build keys from `unique_name`, and delete what you write. The state store also
  prefixes keys with the task's app id, which includes the run id.
- Behaviour across several apps and sidecars belongs in end-to-end tests.

## End-to-end tests

```bash
uv run poe e2e           # fresh cluster, deleted afterwards
uv run poe e2e --keep    # keep the cluster for inspection
```

`e2e` builds the images and creates a kind cluster named `e2e-<id>` in parallel, installs the
standard Dapr control plane, the infrastructure instances, the Dapr resources and the apps (the same steps as
`kind up`, on free host ports and a separate kubeconfig), then runs the scenarios in
`tests/e2e/features/`. Pod status, events and app/sidecar logs are saved in
`.run/<run-id>/diagnostics/` before the cluster is deleted.

Scenarios use [pytest-bdd](https://pytest-bdd.readthedocs.io/): Gherkin files in `features/`, step
definitions in `test_*.py`. Steps talk only to public endpoints (the API and Jaeger's query API).

`e2e` passes the exposed ports of its cluster to the tests in `E2E_ENDPOINTS`, keyed
`<deployment or instance>.<port>` (for example `gateway.http`). Without that variable the tests use
the declared host ports, so they run directly against a `kind up` environment:

```bash
uv run pytest tests/e2e
```
