"""Exact evidence must survive numeric backend and process-order differences."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.services import semantic_search
from tests import space_integration_evidence


@pytest.mark.parametrize("core,seed", [(None, "1"), ("Prescott", "2")])
def test_reference_bytes_in_fresh_numeric_backends(tmp_path, core, seed):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "evidence.json"
    environment = {**os.environ, "PYTHONHASHSEED": seed}
    if core is not None:
        # SSE2 baseline on x86-64: do not require AVX/AVX512 from a test host.
        environment["OPENBLAS_CORETYPE"] = core
    else:
        environment.pop("OPENBLAS_CORETYPE", None)
    completed = subprocess.run(
        [sys.executable, "-m", "tests.space_integration_evidence", "--output", str(output)],
        cwd=root, env=environment, capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    actual = output.read_bytes()
    expected = (root / "tests/fixtures/vb151_g_reference.json").read_bytes()
    assert json.loads(actual) == json.loads(expected)
    assert actual == expected


def test_numeric_backend_is_restored_after_export_failure(monkeypatch):
    original = semantic_search.np

    def fail(root):
        assert semantic_search.np is not original
        assert original.dot is space_integration_evidence.np.dot
        raise RuntimeError("injected evidence failure")

    monkeypatch.setattr(space_integration_evidence, "export_evidence", fail)
    with pytest.raises(RuntimeError, match="injected evidence failure"):
        space_integration_evidence.evidence_bytes()
    assert semantic_search.np is original
