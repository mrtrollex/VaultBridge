#!/usr/bin/env python3
"""Verify the durable bundle and the exact recorded implementation inputs.

Standard library only. No writes, network calls or runtime test execution.
"""
from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import json
from pathlib import Path
import re
import subprocess

BUNDLE = Path(__file__).resolve().parent
ROOT = BUNDLE.parents[2]
FINGERPRINT = "cf8961d2a813117393a08aa7159c602548e5fdfbe4538ea1ce0a649eb801a32e"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads((BUNDLE / path).read_text(encoding="utf-8"))


def jsonl(path):
    return [json.loads(line) for line in (BUNDLE / path).read_text(encoding="utf-8").splitlines()]


def restore_recorded_newlines(data, metadata):
    """Accept only identical LF content, then restore recorded line-ending bytes."""
    assert metadata["text_utf8"]
    lf = data.replace(b"\r\n", b"\n")
    assert sha(lf) == metadata["lf_sha256"], "source content differs, beyond line endings"
    endings = metadata["recorded_line_endings"]
    parts = lf.split(b"\n")
    assert len(parts) - 1 == endings["newline_count"]
    exceptions = set(endings["exceptions"])
    result = bytearray()
    for line, part in enumerate(parts[:-1], 1):
        crlf = endings["default"] == "crlf"
        if line in exceptions:
            crlf = not crlf
        result.extend(part)
        result.extend(b"\r\n" if crlf else b"\n")
    result.extend(parts[-1])
    return bytes(result)


def verify_source(git_normalized=False, self_test=False):
    spec = read("source-fingerprint.json")
    paths = {
        p.relative_to(ROOT).as_posix()
        for base in ("app", "tests", "scripts")
        for p in (ROOT / base).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".pyo")
    }
    paths.update(("Dockerfile", "requirements.txt", "requirements-dev.txt", "pyproject.toml", ".dockerignore"))
    assert paths == set(spec["ordered_inputs"]) == set(spec["files"]), "source input set changed"
    hashes = {}
    reconstructed = []
    for relative in sorted(paths):
        metadata = spec["files"][relative]
        data = (ROOT / relative).read_bytes()
        if sha(data) != metadata["sha256"]:
            assert git_normalized and metadata["text_utf8"], f"raw source mismatch: {relative}"
            data = restore_recorded_newlines(data, metadata)
            reconstructed.append(relative)
        assert sha(data) == metadata["sha256"] and len(data) == metadata["bytes"], relative
        hashes[relative] = sha(data)
        if self_test and metadata["text_utf8"]:
            assert sha(restore_recorded_newlines(data.replace(b"\r\n", b"\n"), metadata)) == metadata["sha256"]
            try:
                restore_recorded_newlines(data + b"X", metadata)
            except AssertionError:
                pass
            else:
                raise AssertionError("content mutation accepted by newline reconstruction")
    aggregate = sha(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    assert aggregate == spec["sha256"] == FINGERPRINT
    for path in spec["windows_linux_equivalence"]["evidence_paths"]:
        recorded = read(path)
        assert recorded["sha256"] == aggregate and recorded["files"] == hashes, path
    print(f"SOURCE PASS: {aggregate}; {len(hashes)} exact inputs; {len(reconstructed)} recorded-EOL reconstructions")


def cases(path):
    return [case for suite in read(path)["suites"] for case in suite["cases"]]


def identity(case):
    return case["classname"] + "::" + case["name"]


def native_identity(node):
    module, name = node.split("::", 1)
    return module.removesuffix(".py").replace("/", ".") + "::" + name


def accounting(path, expected):
    document = read(path)
    result = collections.Counter(case["outcome"] for case in cases(path))
    assert dict(result) == expected, (path, result)
    for suite in document["suites"]:
        if document["selection"] == "all original testcases":
            assert int(suite["attributes"]["tests"]) == len(suite["cases"])
    return {identity(case): case for case in cases(path)}


def verify_original_case_identities():
    """Recover credential-bearing parameter identities locally, without printing values."""
    trees = {}
    aliases = {}
    source_paths = read("source-fingerprint.json")["files"]
    for row in read("sanitization/credential-aliases.json"):
        values = set()
        for ref in row["source_literal_locations"]:
            assert ref["path"] in source_paths
            if ref["path"] not in trees:
                trees[ref["path"]] = ast.parse((ROOT / ref["path"]).read_text(encoding="utf-8"))
            found = [n.value for n in ast.walk(trees[ref["path"]]) if isinstance(n, ast.Constant)
                     and isinstance(n.value, str) and n.lineno == ref["line"] and n.col_offset == ref["column"]]
            assert len(found) == 1
            values.add(found[0])
        assert len(values) == 1
        aliases[row["alias"]] = values.pop()
    redacted = 0
    for p in (BUNDLE / "validation").glob("*.json"):
        document = read(p.relative_to(BUNDLE))
        for suite in document["suites"]:
            for case in suite["cases"]:
                original = identity(case)
                for alias, value in aliases.items():
                    original = original.replace(alias, value)
                assert sha(original.encode()) == case["original_identity_sha256"], (p.name, case["name"])
                redacted += case["identity_parameter_sanitized"]
    print(f"IDENTITIES PASS: exact original identity hashes verified; {redacted} credential-bearing parameter identities reconstructed only in memory")


def verify_results():
    matrix = read("execution-matrix.json")
    builds_expected = {
        "windows": ("1.63.0", {"chromium": ("153.0.8010.12", "1243"), "firefox": ("155.0", "1543")}),
        "linux": ("1.64.0", {"chromium": ("156.0.8078.4", "1248"), "firefox": ("157.0", "1555"), "webkit": ("27.2", "2370")}),
    }
    all_nodes = {}
    for system, total, mutations in (("windows", 501, 16), ("linux", 749, 24)):
        nodes = accounting(f"validation/{system}-browsers.json", {"passed": total})
        all_nodes[system] = nodes
        run = next(row for row in matrix if row["platform"] == system)
        assert run["run"]["source"] == FINGERPRINT and run["run"]["exit"] == 0
        assert run["run"]["started"].startswith("2026-11-22") and run["run"]["ended"].startswith("2026-11-22")
        build = read(run["builds"])
        sdk, engines = builds_expected[system]
        assert build["playwright"] == sdk and set(run["qualified_engines"]) == set(engines)
        catalog = {row["name"]: row for row in build["browsers"]}
        for engine, (version, revision) in engines.items():
            assert (catalog[engine]["browserVersion"], catalog[engine]["revision"]) == (version, revision)
        native = jsonl(f"native-browser/{system}-events.jsonl")
        by_node = collections.defaultdict(list)
        for row in native:
            assert row["source_fingerprint"] == FINGERPRINT and row["playwright"] == sdk
            assert row["platform"] == ("Windows" if system == "windows" else "Linux")
            assert row["browser"] in {version for version, _ in engines.values()}
            revision = next(rev for version, rev in engines.values() if version == row["browser"])
            assert row["recorded_executable_build"].endswith("-" + revision)
            node = re.sub(r"-(?:dependency|lock)$", "", row["nodeid"])
            assert native_identity(node) in nodes, node
            by_node[node].append(row)
        facts = read(f"security/{system}-mutation-observations.json")
        assert len(facts) == mutations
        for case in facts:
            assert native_identity(case["nodeid"]) in nodes and case["source_fingerprint"] == FINGERPRINT
            value = case["facts"]
            assert value["phase"] == "uncertain" and value["native"]
            assert any(e["method"] == value["method"] and e["event"] in ("fetch-failed", "body-failed") for e in value["observations"])
            assert not any(e["method"] == value["method"] and e["event"] == "complete-json" for e in value["observations"])
            assert value["cookie_present"] == (value["method"] == ("DELETE" if value["failure"] == "tcp" else "POST"))
        proxies = read(f"security/{system}-tcp-delete-chronology.json")
        assert len(proxies) == (4 if system == "windows" else 6)
        for case in proxies:
            assert native_identity(case["nodeid"]) in nodes
            events = case["events"]
            begin = next(e for e in events if e["event"] == "disconnect-begin")
            ack = next(e for e in events if e["event"] == "disconnect-ack")
            assert ack["outstanding"] == 0 and begin["sequence"] < ack["sequence"]
            assert sum(e["event"] == "http-request" and e.get("method") == "DELETE" for e in events) == 1
            assert not any(e["event"] == "forwarded" and e["generation"] == begin["generation"] for e in events[begin["sequence"] + 1:])
            assert all(e["sequence"] > ack["sequence"] for e in events if e["event"] in ("http-response-start", "http-response-body") and e.get("method") == "DELETE")
            recorded = by_node[case["nodeid"]][0]["events"]
            request = next(e for e in recorded if e["event"] == "request" and e.get("method") == "DELETE")
            later = recorded[request["sequence"] + 1:]
            assert any(e["event"] == "failed" and e.get("path", "").endswith("/ui/session") for e in later)
            assert not any(e["event"] == "response" and e.get("status") == 200 and e.get("path", "").endswith("/ui/session") for e in later)
        measurements = read(f"layout/{system}-measurements.json")
        assert len(measurements) == (6 if system == "windows" else 9)
        for row in measurements:
            m = row["measurements"]
            assert m["scrollWidth"] <= m["clientWidth"] <= m["innerWidth"]
            if m["innerWidth"] == 320:
                assert m["scrollWidth"] == m["clientWidth"] == 320
            else:
                assert m["innerWidth"] in (375, 1280) and m["fitted"] == m["baseline"]
        properties = [p for suite in read(f"validation/{system}-browsers.json")["suites"] for p in suite["properties"]]
        auth = [p for p in properties if "test_native_authenticated_history_qualification" in p["name"]]
        assert len(auth) == (16 if system == "windows" else 24)
        for row in auth:
            shown = [e for e in row["value"]["events"] if e["type"] == "pageshow"]
            assert shown and not any(e["persisted"] is True for e in shown)
        cookie = read(f"security/{system}-cookie-delivery.json")
        assert len(cookie) == (8 if system == "windows" else 12)
        for row in cookie:
            obs = row["observations"]
            assert obs["stored_same_site"] == "Strict"
            expected = {"same-host-other-port-navigation": True, "different-host-same-site-navigation": False,
                        "same-site-sibling-to-issuer-navigation": True, "cross-site-navigation": False,
                        "same-host-other-port-fetch": True, "same-site-sibling-to-issuer-fetch": True,
                        "cross-site-fetch": False, "different-host-same-site-fetch": False, "secure-cookie-over-http": False}
            assert len(obs["cases"]) == 9
            assert all(c["cookie_received"] == expected[c["case"]] for c in obs["cases"])
    backend = accounting("validation/linux-python.json", {"passed": 4277, "skipped": 92})
    assert sum(c["classname"].endswith("test_named_dashboard") and c["outcome"] == "passed" for c in backend.values()) == 84
    claims = read("security/claim-test-map.json")
    for system, count in (("windows", 200), ("linux", 300)):
        assert len(claims[system]["http_401"]) == count
        for group in claims[system].values():
            assert all(node in all_nodes[system] and all_nodes[system][node]["outcome"] == "passed" for node in group)
    log = (BUNDLE / "validation/windows-finish.log.txt").read_text(encoding="utf-8")
    section = log.split("RUN pytest (unit/integration, includes tests/eval):", 1)[1].split("RUN compileall:", 1)[0]
    progress = "".join(re.findall(r"^([.s]+)\s+\[\s*\d+%\]", section, re.MULTILINE))
    assert (progress.count("."), progress.count("s")) == (4053, 316)
    assert log.count("  PASS ") == 8 and "Overall: PASS" in log
    for record in read("validation-results.json"):
        assert record["record"]["source"] == FINGERPRINT and record["record"]["exit"] == 0
    accounting("validation/linux-focused-final.json", {"passed": 42})
    traces = jsonl("native-browser/trace-extracts.jsonl")
    assert len(traces) == 46
    assert sum("positive control" in row["classification"] for row in traces) == 6
    assert any(n["response"]["body"].get("authenticated") is False for t in traces for n in t["network"])
    for t in traces:
        assert t["source_fingerprint"] == FINGERPRINT
        assert all("text" not in n["response"]["body"] and "value" not in c for n in t["network"] for c in n["response"]["cookies"])
    print("RESULTS PASS: Windows 501; native Linux 749; Python 4053/316 and 4277/92; ten native TCP/DELETE cases; 40 history observations")


def verify_integrity():
    manifest_path = BUNDLE / "manifest.json"
    checksum = (BUNDLE / "manifest.sha256").read_text().split()
    assert checksum == [sha(manifest_path.read_bytes()), "manifest.json"]
    manifest = read("manifest.json")
    listed = set()
    for row in manifest["records"]:
        path = BUNDLE / row["path"]
        assert path.resolve().is_relative_to(BUNDLE) and path.is_file()
        assert sha(path.read_bytes()) == row["sha256"], row["path"]
        assert row["sanitization_method"] and row["test_source_relationship"]
        listed.add(row["path"])
    actual = {p.relative_to(BUNDLE).as_posix() for p in BUNDLE.rglob("*") if p.is_file()}
    assert actual == listed | {"manifest.json", "manifest.sha256"}, "unindexed or missing artifact"
    for p in BUNDLE.rglob("*"):
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        assert not re.search(r"[A-Za-z]:[\\/]+Users[\\/]+|/home/[A-Za-z0-9]|/mnt/[a-z]/Users/", text), p.name
        assert not re.search(r"\bv1\.\d{1,12}\.\d{1,12}\.[A-Za-z0-9_-]{43}", text), p.name
        assert not re.search(r'"(?:authorization|cookie|set-cookie)"\s*:\s*"', text, re.IGNORECASE), p.name
        if p.suffix == ".md":
            for target in re.findall(r"\]\(([^)]+)\)", text):
                if re.match(r"^[a-z]+:", target) or target.startswith("#"):
                    continue
                assert (p.parent / target.split("#", 1)[0]).is_file(), (p.name, target)
    for path in manifest["immutable_reference_hashes"]:
        assert sha((ROOT / path).read_bytes()) == manifest["immutable_reference_hashes"][path], path
    alternative = read("history/alternative-evidence-map.json")
    for decision in alternative.values():
        for key in ("artifacts", "final_evidence", "historical_supplements"):
            for relative in decision.get(key, []):
                assert (BUNDLE / relative).is_file(), relative
    for run in read("execution-matrix.json"):
        assert (BUNDLE / run["builds"]).is_file() and (BUNDLE / run["evidence_junit"]).is_file()
    if (ROOT / ".git").exists():
        process = subprocess.run(["git", "check-ignore", "--stdin"], input="\n".join(str((BUNDLE / p).relative_to(ROOT)) for p in actual), text=True, capture_output=True)
        assert process.returncode == 1 and not process.stdout, "bundle artifact ignored by Git"
        attrs = subprocess.check_output(["git", "check-attr", "text", "--", str(manifest_path)], text=True)
        assert attrs.strip().endswith(": unset"), "hash-indexed artifact bytes may be transformed by checkout"
    print(f"INTEGRITY PASS: {len(listed)} indexed artifacts; manifest checksum, links, privacy patterns and immutable references")


def show_case(node):
    found = []
    for system in ("windows", "linux"):
        found.extend(r for r in jsonl(f"native-browser/{system}-events.jsonl") if r["nodeid"] == node)
        found.extend(r for r in read(f"security/{system}-tcp-delete-chronology.json") if r["nodeid"] == node)
        found.extend(r for r in read(f"security/{system}-mutation-observations.json") if r["nodeid"] == node)
    assert found, "case not present in native channels; inspect JUnit for backend/legacy cases"
    print(json.dumps(found, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git-normalized", action="store_true", help="restore recorded line endings only after matching LF-content hashes")
    parser.add_argument("--self-test", action="store_true", help="also verify newline reconstruction and rejection of changed content without writing files")
    parser.add_argument("--show-case", help="print the original sanitized native channels for one exact node identity")
    args = parser.parse_args()
    verify_integrity()
    verify_source(args.git_normalized, args.self_test)
    verify_original_case_identities()
    verify_results()
    if args.show_case:
        show_case(args.show_case)
