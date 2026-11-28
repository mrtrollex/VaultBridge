import {exact} from "./scoped-client.js";

const states = {complete: "Complete", partial: "Partial", unavailable: "Unavailable"};
const basis = {
  none: "",
  compatible_ready: "Compatible index",
  compatible_previous_refresh: "Previous compatible index — refresh in progress or required",
  compatible_previous_error: "Previous compatible index — refresh failed",
};
const reasons = {
  root_unavailable: "Space storage unavailable", index_unavailable: "Index unavailable",
  discovery_limit: "Discovery limit reached", path_limit: "Path limit reached",
  alias_limit: "Alias limit reached", content_limit: "Content limit reached",
  relationship_limit: "Relationship limit reached", semantic_limit: "Semantic work limit reached",
  note_unavailable: "A note became unavailable",
};
const ordering = {canonical_path: "Canonical path", modified: "Last modified", semantic: "Semantic rank"};

function validate(data, selected) {
  const rows = data.coverage;
  if (!Array.isArray(rows) || rows.length !== selected.length) throw Error("invalid");
  const remaining = new Set(selected);
  for (const row of rows) {
    if (!exact(row, ["space_id", "state", "reasons", "semantic_index_basis",
      "enumerated_paths", "evaluated_paths", "returned_candidates"]) ||
        !remaining.delete(row.space_id) || typeof row.state !== "string" || !Object.hasOwn(states, row.state) ||
        typeof row.semantic_index_basis !== "string" || !Object.hasOwn(basis, row.semantic_index_basis) ||
        !Array.isArray(row.reasons) || (row.state === "complete") !== (row.reasons.length === 0) ||
        !row.reasons.every(reason => typeof reason === "string" && Object.hasOwn(reasons, reason)) ||
        ![row.enumerated_paths, row.evaluated_paths, row.returned_candidates].every(
          count => Number.isSafeInteger(count) && count >= 0)) throw Error("invalid");
  }
  const unavailable = new Set(rows.filter(row => row.state === "unavailable").map(row => row.space_id));
  if (unavailable.size === rows.length ||
      data.state !== (rows.every(row => row.state === "complete") ? "complete" : "partial") ||
      data.items.some(row => unavailable.has(row.identity.space_id))) throw Error("invalid");
}

export function showCoverage(region, data, selected) {
  validate(data, selected);
  const summary = document.createElement("p");
  summary.textContent = `${states[data.state]} coverage. Order: ${ordering[data.ordering]}.`;
  const rows = document.createElement("ul");
  for (const row of data.coverage) {
    const item = document.createElement("li");
    const facts = [states[row.state], basis[row.semantic_index_basis], ...row.reasons.map(reason => reasons[reason])];
    item.textContent = `${row.space_id}: ${facts.filter(Boolean).join(". ")}.`;
    rows.append(item);
  }
  region.replaceChildren(summary, rows);
}
