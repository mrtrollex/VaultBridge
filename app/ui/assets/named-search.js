import {NoteResults} from "./browse.js";

export function createSearch(options) {
  const results = new NoteResults("named-search", options, form => {
    const semantic = form.elements.mode.value === "semantic";
    return {
      action: semantic ? "semantic" : "literal",
      capability: semantic ? "semantic_retrieval" : "literal_search",
      input: form.elements.query,
      maxBytes: semantic ? 4096 : 8192,
    };
  });
  results.form.elements.mode.addEventListener("change", event => {
    results.limit.value = event.target.value === "semantic" ? "5" : "10";
    results.updateScope();
  });
  return results;
}
