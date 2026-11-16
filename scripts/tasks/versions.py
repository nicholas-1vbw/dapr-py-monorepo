"""Print versions from deploy/platform/versions.yaml as `name=value` lines.

Each argument is `name=key.path`, for example `dapr=dapr.runtimeVersion`. CI
appends the output to $GITHUB_OUTPUT to install the same tool versions as local
environments.
"""

import sys

from scripts.framework.repo import versions
from scripts.framework.runs import entrypoint


def main() -> None:
    for argument in sys.argv[1:]:
        name, _, path = argument.partition("=")
        value = versions()
        for key in path.split("."):
            value = value[key]
        print(f"{name}={value}")


if __name__ == "__main__":
    entrypoint(main)
