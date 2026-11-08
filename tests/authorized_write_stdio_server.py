"""Private named write process; production named startup stays guarded."""

import json
import os

from app.core.config import Settings
from app.mcp_server import create_private_read_server
from app.services.space_runtime import compose_space_runtime
from tests.policy_read_stdio_server import audit_input_boundary


def main():
    settings = Settings.model_validate(json.loads(os.environ["VB_TEST_PRIVATE_SETTINGS"]))
    with compose_space_runtime(settings) as runtime:
        audit_input_boundary()
        create_private_read_server(settings=settings, operations=runtime.operations).run()


if __name__ == "__main__":
    main()
