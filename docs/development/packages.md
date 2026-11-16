# Packages

A package under `packages/` holds reusable code: domain logic, RPC implementations, adapters to
Dapr. Apps combine packages; packages never import apps.

Name packages after a domain (`greeting`, `orders`, `billing`), not after a role (`core`,
`utils`). Infrastructure shared by every app belongs in `app-runtime`.

## Create a package

Example: an `orders` package.

1. Create the layout:

   ```text
   packages/orders/
   ├── pyproject.toml
   ├── src/orders/__init__.py
   └── tests/
   ```

2. Write `packages/orders/pyproject.toml`. Declare every package you import directly, including
   other workspace members:

   ```toml
   [project]
   name = "orders"
   version = "0.1.0"
   description = "Order handling."
   requires-python = ">=3.14"
   dependencies = [
       "app-runtime",
       "contracts",
   ]

   [tool.uv.sources]
   app-runtime = { workspace = true }
   contracts = { workspace = true }

   [build-system]
   requires = ["uv_build>=0.13.7,<0.14"]
   build-backend = "uv_build"
   ```

3. Add it to the root `pyproject.toml` so it is installed in the development environment:

   ```toml
   dependencies = [..., "orders"]

   [tool.uv.sources]
   orders = { workspace = true }
   ```

   `packages/*` is already a workspace member pattern; no other registration is needed.

4. Lock and install:

   ```bash
   uv lock
   uv sync
   ```

5. Add unit tests in `packages/orders/tests/test_*.py`. `uv run poe unit` discovers them
   automatically. If the package talks to Dapr, add integration tests in
   `tests/integration/orders/` (see [Testing](testing.md)); `uv run poe integration` picks the new
   directory up by the package name.

6. Use it from an app by adding `orders` to the app's dependencies (see [Apps](apps.md)). The image
   build includes every workspace member's wheel; each image installs only what its app depends
   on.

## Guidelines

- **Keep Dapr at the edges.** Put Dapr calls behind a small interface (like
  `greeting.counter.StateStore`) so the logic can be unit tested with an in-memory fake.
- **Implement generated service protocols by subclassing them**
  (`class CounterServiceImpl(CounterService)`), so type checking catches signature drift after a
  contract change.
- **Do not create clients at import time.** Async clients (Dapr, Connect) belong to the event loop
  of the server; create them in the app's startup hook.
- **Third-party dependencies** go into the package that uses them, with a lower bound. The exact
  versions are in `uv.lock`; run `uv lock` after changing dependencies.
- **Dapr extensions** come as extras of the SDK, e.g. `dapr[fastapi]` or `dapr[workflow]`.
