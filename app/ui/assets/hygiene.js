// Presentation of the existing KnowledgeHygieneScanResponse; no diagnostic rules live here.
const SCAN_REASONS = ["path_ceiling", "enumeration_unavailable", "note_unavailable", "relationship_unavailable"];
const CANDIDATE_REASONS = ["scan_partial", "source_unavailable", "candidate_unavailable", "semantic_unavailable"];
const DERIVED_STATUSES = [
  "not_requested", "compatible_ready", "compatible_previous_refresh", "compatible_previous_error",
  "missing", "incompatible", "invalid_metadata", "corrupt_storage", "storage_unavailable", "inspection_unavailable",
];
const RULE_KINDS = [
  "isolated_note", "empty_authored_body", "duplicate_candidate", "near_duplicate_candidate",
  "previous_compatible_index", "derived_index_unavailable",
];
const RELATIONSHIP_KINDS = ["missing_relationship_target", "unsafe_relationship_target", "ambiguous_relationship_target"];

const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const keys = (value, names) => object(value) && Object.keys(value).length === names.length
  && names.every((name) => Object.hasOwn(value, name));
const integer = (value, min, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(value) && value >= min && value <= max;
const nullableInteger = (value, min, max) => value === null || integer(value, min, max);
const text = (value) => typeof value === "string" && value.length > 0;
const reasons = (value, allowed) => Array.isArray(value) && value.length <= allowed.length
  && value.every((reason) => allowed.includes(reason));
const label = (value) => value.replaceAll("_", " ");

function validEvidence(finding) {
  const e = finding.evidence;
  if (RELATIONSHIP_KINDS.includes(finding.kind)) {
    return keys(e, ["origin", "source_order"]) && ["obsidian_wikilink", "markdown_link"].includes(e.origin)
      && integer(e.source_order, 0);
  }
  if (finding.kind === "invalid_frontmatter") {
    return keys(e, ["line", "column"]) && nullableInteger(e.line, 1) && nullableInteger(e.column, 1);
  }
  if (["invalid_portable_field", "empty_portable_field_value"].includes(finding.kind)) {
    return keys(e, ["field", "source_index"]) && ["aliases", "tags"].includes(e.field)
      && nullableInteger(e.source_index, 0, 255);
  }
  if (finding.kind === "duplicate_alias_in_note") {
    return keys(e, ["source_indices"]) && Array.isArray(e.source_indices) && e.source_indices.length === 2
      && e.source_indices.every((index) => integer(index, 0, 255));
  }
  if (finding.kind === "colliding_alias") {
    return keys(e, ["source_index", "peer_count", "related_paths_truncated"])
      && integer(e.source_index, 0, 255) && integer(e.peer_count, 1, 9999)
      && typeof e.related_paths_truncated === "boolean";
  }
  return RULE_KINDS.includes(finding.kind) && keys(e, []);
}

function validPayload(value) {
  if (!keys(value, ["findings", "scan", "candidates", "derived_index", "findings_truncated"])) return false;
  const { scan, candidates, derived_index: derived } = value;
  return keys(scan, ["state", "reasons", "eligible_paths", "inspected_notes", "unavailable_notes"])
    && ["complete", "partial"].includes(scan.state) && reasons(scan.reasons, SCAN_REASONS)
    && integer(scan.eligible_paths, 0, 10000) && integer(scan.inspected_notes, 0, 10000)
    && integer(scan.unavailable_notes, 0, 10000)
    && keys(candidates, ["state", "source_notes", "reasons"])
    && ["not_requested", "complete", "partial", "unavailable"].includes(candidates.state)
    && integer(candidates.source_notes, 0, 20) && reasons(candidates.reasons, CANDIDATE_REASONS)
    && keys(derived, ["status"]) && DERIVED_STATUSES.includes(derived.status)
    && typeof value.findings_truncated === "boolean"
    && Array.isArray(value.findings) && value.findings.length <= 500
    && value.findings.every((finding) => keys(finding, ["kind", "primary_path", "related_paths", "evidence_source", "category", "evidence"])
      && (finding.primary_path === null || text(finding.primary_path))
      && Array.isArray(finding.related_paths) && finding.related_paths.length <= 10 && finding.related_paths.every(text)
      && ["live_markdown", "derived_index"].includes(finding.evidence_source) && text(finding.category)
      && validEvidence(finding));
}

function append(parent, tag, value) {
  const element = document.createElement(tag);
  element.textContent = value;
  parent.append(element);
  return element;
}

function evidenceRows(finding) {
  const e = finding.evidence;
  if (RELATIONSHIP_KINDS.includes(finding.kind)) {
    return [["Origin", label(e.origin)], ["Source order (zero-based)", e.source_order]];
  }
  if (finding.kind === "invalid_frontmatter") {
    return [["Line", e.line ?? "Not available"], ["Column", e.column ?? "Not available"]];
  }
  if (["invalid_portable_field", "empty_portable_field_value"].includes(finding.kind)) {
    return [["Field", e.field], ["Source index (zero-based)", e.source_index ?? "Not available"]];
  }
  if (finding.kind === "duplicate_alias_in_note") {
    return [["Source indices (zero-based)", e.source_indices.join(", ")]];
  }
  if (finding.kind === "colliding_alias") {
    return [["Source index (zero-based)", e.source_index], ["Peer count", e.peer_count],
      ["Related paths truncated", e.related_paths_truncated ? "Yes" : "No"]];
  }
  return [];
}

function row(list, name, value) {
  const container = document.createElement("div");
  append(container, "dt", name);
  append(container, "dd", String(value));
  list.append(container);
}

function render(results, payload) {
  const { scan, candidates, derived_index: derived, findings, findings_truncated: truncated } = payload;
  append(results, "h3", "Scan summary");
  const summary = append(results, "dl", "");
  summary.className = "hygiene-facts";
  row(summary, "Returned findings", findings.length);
  row(summary, "Scan coverage", label(scan.state));
  row(summary, "Scan reasons", scan.reasons.map(label).join(", ") || "None");
  row(summary, "Eligible paths", scan.eligible_paths);
  row(summary, "Inspected notes", scan.inspected_notes);
  row(summary, "Unavailable notes", scan.unavailable_notes);
  row(summary, "Findings truncated", truncated ? "Yes — additional findings were omitted by the server" : "No");
  row(summary, "Candidate coverage", label(candidates.state));
  row(summary, "Candidate source notes", candidates.source_notes);
  row(summary, "Candidate reasons", candidates.reasons.map(label).join(", ") || "None");
  row(summary, "Derived index inspection", label(derived.status));
  append(results, "p", "Counts describe returned findings in this scan, not all possible defects. Candidate coverage and index inspection are independent of scan coverage.");

  const groups = new Map();
  for (const finding of findings) {
    if (!groups.has(finding.kind)) groups.set(finding.kind, []);
    groups.get(finding.kind).push(finding);
  }
  if (groups.size === 0) return;
  append(results, "h3", "Returned findings by kind");
  const counts = append(results, "ul", "");
  counts.className = "hygiene-counts";
  for (const [kind, items] of groups) append(counts, "li", `${label(kind)}: ${items.length}`);
  append(results, "h3", "Detailed findings");
  for (const [kind, items] of groups) {
    append(results, "h4", label(kind));
    const list = append(results, "ol", "");
    list.className = "hygiene-findings";
    for (const finding of items) {
      const item = append(list, "li", "");
      append(item, "p", finding.primary_path ?? "Vault-level diagnostic").className = "hygiene-path";
      const facts = append(item, "dl", "");
      facts.className = "hygiene-facts";
      row(facts, "Category", finding.category);
      row(facts, "Evidence source", label(finding.evidence_source));
      for (const [name, value] of evidenceRows(finding)) row(facts, name, value);
      if (finding.related_paths.length) {
        append(item, "p", "Related canonical paths");
        const paths = append(item, "ul", "");
        for (const path of finding.related_paths) append(paths, "li", path);
      }
    }
  }
}

export function initializeHygiene({ authenticatedFetch, navigateToApi, onAuthenticationRequired, messageForRequestError }) {
  const run = document.querySelector("#hygiene-run");
  const access = document.querySelector("#hygiene-access");
  const status = document.querySelector("#hygiene-status");
  const results = document.querySelector("#hygiene-results");
  let unlocked = false;
  let generation = 0;
  let controller = null;
  const setStatus = (state, message) => {
    status.dataset.hygieneState = state;
    status.textContent = message;
  };
  function cancel() {
    generation += 1;
    controller?.abort();
    controller = null;
    results.setAttribute("aria-busy", "false");
    run.disabled = !unlocked;
    run.setAttribute("aria-disabled", String(!unlocked));
  }
  async function scan() {
    if (!unlocked || controller !== null) return;
    cancel();
    const current = generation;
    controller = new AbortController();
    results.replaceChildren();
    results.setAttribute("aria-busy", "true");
    // Keep keyboard focus while preventing duplicate submissions in scan().
    run.setAttribute("aria-disabled", "true");
    setStatus("loading", "Scanning live Markdown… This may take a while.");
    try {
      const response = await authenticatedFetch("api/v1/knowledge/hygiene/scan", {
        method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ groups: ["relationships", "isolation", "frontmatter", "aliases"], finding_limit: 500,
          duplicate_source_limit: 0, semantic_candidates: false, inspect_derived_index: false }),
        signal: controller.signal,
      });
      const payload = await response.json();
      if (current !== generation || !unlocked) return;
      if (!validPayload(payload)) throw new Error("malformed-response");
      render(results, payload);
      const partial = payload.scan.state === "partial";
      const truncated = payload.findings_truncated;
      setStatus(partial ? "partial" : truncated ? "truncated" : payload.findings.length ? "findings" : "clean",
        `${partial ? "Partial scan" : "Complete scan"}. ${payload.findings.length} returned ${payload.findings.length === 1 ? "finding" : "findings"}.`
        + (truncated ? " Findings truncated; additional findings were omitted." : "")
        + (!partial && !truncated && !payload.findings.length ? " No findings in the scanned diagnostics." : ""));
      run.textContent = "Refresh scan";
    } catch (error) {
      if (current !== generation || !unlocked || error.name === "StaleRequestError" || error.name === "AbortError") return;
      results.replaceChildren();
      if (error.kind === "authentication-required") {
        onAuthenticationRequired();
      } else if (!error.kind) {
        setStatus("malformed", "VaultBridge returned an empty or malformed scan response. Try again.");
      } else {
        setStatus(error.kind, messageForRequestError(error));
      }
    } finally {
      if (current === generation) {
        controller = null;
        results.setAttribute("aria-busy", "false");
        run.disabled = !unlocked;
        run.setAttribute("aria-disabled", String(!unlocked));
      }
    }
  }
  run.addEventListener("click", () => void scan());
  access.addEventListener("click", navigateToApi);
  return {
    setAccessState(state) {
      unlocked = state === "unlocked";
      cancel();
      if (!unlocked) {
        results.replaceChildren();
        run.textContent = "Run scan";
      }
      access.hidden = unlocked || state === "checking-session";
      setStatus(unlocked ? "idle" : state, unlocked ? "Ready. Run a read-only scan when you choose."
        : state === "checking-session" ? "Checking protected access…"
          : state === "unavailable" ? "Protected access could not be confirmed. Review API / Integration."
            : "Authentication required. Unlock to run a scan.");
    },
    deactivate() {
      if (controller !== null) {
        cancel();
        results.replaceChildren();
        setStatus("idle", "Scan interrupted. Run a scan when you return. Browser cancellation does not stop server scan work.");
      }
    },
  };
}
