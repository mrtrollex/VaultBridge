#!/usr/bin/env python3
"""Measure a synthetic VB-102 live backlink scan without a latency gate."""

from __future__ import annotations

import argparse
import json
import platform
import tempfile
import time
from pathlib import Path

from app.services.relationships import RelationshipService
from app.services.vault import VaultService

DEFAULT_NOTE_COUNT = 1_000
MAX_NOTE_BYTES = 1_000_000


def run_benchmark(note_count: int = DEFAULT_NOTE_COUNT) -> dict[str, object]:
    """Create sanitized Markdown and time one complete live backlink scan."""
    if note_count < 2:
        raise ValueError("note_count must include a target and at least one source")

    with tempfile.TemporaryDirectory(prefix="vaultbridge-backlinks-") as temporary:
        vault_root = Path(temporary)
        (vault_root / "Target.md").write_text("# Synthetic target\n", encoding="utf-8")
        for index in range(note_count - 1):
            (vault_root / f"Source {index:05d}.md").write_text(
                "# Synthetic source\n\n[[Target#Benchmark|Target]]\n",
                encoding="utf-8",
            )

        service = RelationshipService(
            VaultService(vault_root=vault_root, max_note_bytes=MAX_NOTE_BYTES)
        )
        started = time.perf_counter()
        backlinks = service.backlinks("Target.md")
        elapsed_ms = (time.perf_counter() - started) * 1_000

    return {
        "schema_version": 1,
        "benchmark": "vaultbridge-live-backlinks",
        "notes_scanned": note_count,
        "backlinks_returned": len(backlinks),
        "elapsed_ms": round(elapsed_ms, 3),
        "timer": "time.perf_counter",
        "python_version": platform.python_version(),
        "operating_system": platform.system(),
        "machine_architecture": platform.machine(),
        "caveat": "Synthetic local filesystem result; architecture evidence, not a CI latency gate.",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure a synthetic read-only VB-102 live backlink scan."
    )
    parser.add_argument(
        "--notes",
        type=int,
        default=DEFAULT_NOTE_COUNT,
        help="total synthetic Markdown notes including the target (default: 1000)",
    )
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    print(json.dumps(run_benchmark(arguments.notes), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
