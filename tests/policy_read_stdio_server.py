"""Private named test process, deliberately separate from production entry points."""

import json
import os
from functools import wraps
from pathlib import Path

from app.core.config import Settings
from app.mcp_server import create_private_read_server
from app.services.persisted_space_reads import compose_persisted_space_reads


def audit_input_boundary():
    """Optional subprocess evidence; install only after the real profile exists."""
    target = os.environ.get("VB_TEST_INPUT_AUDIT")
    if target is None:
        return
    from app.services.knowledge_spaces import SpacePolicyResolver
    from app.services.space_admission import ScopedOperationAdmission
    from app.services.vault import VaultService

    for owner, name in (
        (SpacePolicyResolver, "authorize_read"),
        (ScopedOperationAdmission, "operation"),
        (VaultService, "read_note"),
        (VaultService, "scoped_session"),
    ):
        original = getattr(owner, name)

        def wrap(original, name):
            @wraps(original)
            def recorded(*args, **kwargs):
                with Path(target).open("a", encoding="utf-8") as audit:
                    audit.write(name + "\n")
                return original(*args, **kwargs)

            return recorded

        setattr(owner, name, wrap(original, name))


def main():
    settings = Settings.model_validate(json.loads(os.environ["VB_TEST_PRIVATE_SETTINGS"]))
    with compose_persisted_space_reads(settings) as profile:
        audit_input_boundary()
        if os.environ.get("VB_TEST_FAIL_REJECTION"):
            from app import mcp_read_input

            def fail(_):
                raise ValueError("synthetic private rejection construction")

            mcp_read_input._rejection_message = fail
        create_private_read_server(settings=settings, operations=profile.operations).run()


if __name__ == "__main__":
    main()
