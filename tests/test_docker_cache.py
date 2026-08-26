import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _compose_value(filename: str, key: str) -> str:
    text = (REPOSITORY_ROOT / filename).read_text(encoding="utf-8")
    match = re.search(rf"^\s+{re.escape(key)}:\s*([^#\r\n]+)$", text, re.MULTILINE)
    assert match is not None
    return match.group(1).strip().strip('"')


def test_generic_compose_uses_writable_derived_hugging_face_cache():
    assert _compose_value("docker-compose.yml", "user") == "${PUID:-1000}:${PGID:-1000}"
    assert _compose_value("docker-compose.yml", "SEMANTIC_DATA_PATH") == (
        "/vault/.obsidian-chatgpt-data"
    )
    assert _compose_value("docker-compose.yml", "HF_HOME") == (
        "/vault/.obsidian-chatgpt-data/huggingface"
    )


def test_truenas_compose_uses_writable_derived_hugging_face_cache():
    assert _compose_value("compose.truenas.yml", "user") == "568:568"
    assert _compose_value("compose.truenas.yml", "SEMANTIC_DATA_PATH") == "/data"
    assert _compose_value("compose.truenas.yml", "HF_HOME") == "/data/huggingface"


def test_image_default_prepares_dedicated_non_root_cache():
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "HF_HOME=/data/huggingface" in dockerfile
    assert "mkdir -p /data/huggingface" in dockerfile
    assert "chown -R 568:568 /data" in dockerfile
