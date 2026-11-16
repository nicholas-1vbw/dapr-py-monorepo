"""Greeting domain.

- `greeting.counter`: `CounterService`, which counts visits per name in a Dapr
  state store.
- `greeting.gateway`: the public HTTP API, which greets a visitor and asks the
  counter service for the visit number.
"""
