"""Shared runtime support for the apps in this repository.

The package wires together what every app needs at startup and on every
request: settings, structured logging, OpenTelemetry tracing, Protovalidate
request validation, error mapping and the Dapr sidecar/component checks.
"""

from app_runtime.app import Runtime, create_app, mount_connect, run
from app_runtime.components import ComponentCheckError, ComponentRequirement
from app_runtime.settings import RuntimeSettings

__all__ = [
    "ComponentCheckError",
    "ComponentRequirement",
    "Runtime",
    "RuntimeSettings",
    "create_app",
    "mount_connect",
    "run",
]
