const capabilities = ["knowledge_query", "list_notes", "literal_search", "read_note", "relationships", "semantic_retrieval"];
const exact = (value, keys) => value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).sort().join() === keys.slice().sort().join();
const sortedSubset = (values, allowed) => Array.isArray(values) && values.every((v, i) => allowed.includes(v) && (!i || v > values[i - 1]));
const id = value => typeof value === "string" && /^[a-z][a-z0-9_-]{0,63}$/.test(value);

export class Spaces {
  constructor(client, session) {
    this.client = client; this.session = session; this.epoch = 0; this.selected = []; this.choices = [];
    this.region = document.querySelector("#spaces");
    document.querySelector("#reload-spaces").addEventListener("click", () => this.load(false));
    document.querySelector("#single-space").addEventListener("change", e => {
      this.apply(e.target.value ? [e.target.value] : []);
    });
    document.querySelector("#choose-multiple").addEventListener("click", () => this.edit());
    document.querySelector("#cancel-selection").addEventListener("click", () => this.cancel());
    document.querySelector("#selection-form").addEventListener("submit", e => {
      e.preventDefault();
      const values = [...document.querySelectorAll("#space-options input:checked")].map(input => input.value);
      this.cancel(); this.apply(values);
    });
    document.querySelector("#refresh-status").addEventListener("click", () => this.status());
  }

  reset() {
    this.epoch++; this.controller?.abort(); this.controller = null;
    this.choices = []; this.selected = [];
    for (const name of ["space-options", "capabilities", "index-status"]) document.getElementById(name).replaceChildren();
    document.querySelector("#single-space").replaceChildren(new Option("Choose a space", ""));
    document.querySelector("#active-selection").textContent = "No spaces selected";
    document.querySelector("#spaces-message").textContent = "";
    this.cancel(); this.region.hidden = true;
    this.region.setAttribute("aria-busy", "false");
  }

  begin() {
    this.controller?.abort(); this.controller = new AbortController();
    this.session.reads.add(this.controller);
    return {epoch: ++this.epoch, controller: this.controller};
  }

  current(request) { return request.epoch === this.epoch && this.session.check(); }
  message(text) { document.querySelector("#spaces-message").textContent = text; }

  async load(initial = true) {
    this.reset(); this.region.hidden = false;
    const request = this.begin(); this.message("Loading readable spaces…");
    this.region.setAttribute("aria-busy", "true");
    try {
      const data = await this.client.get("api/v1/knowledge/spaces", [], request.controller.signal);
      if (!this.current(request)) return;
      if (!exact(data, ["spaces"]) || !Array.isArray(data.spaces) || data.spaces.length > 8 ||
          data.spaces.some((row, i) => !exact(row, ["space_id", "read_capabilities", "relationship_dialects", "indexing"]) ||
          !id(row.space_id) || (i && row.space_id <= data.spaces[i - 1].space_id) ||
          !sortedSubset(row.read_capabilities, capabilities) ||
          !sortedSubset(row.relationship_dialects, ["markdown_link", "obsidian_wikilink"]) ||
          (!row.read_capabilities.includes("relationships") && row.relationship_dialects.length) ||
          !["enabled", "disabled"].includes(row.indexing))) throw Error("invalid");
      this.choices = data.spaces;
      const select = document.querySelector("#single-space");
      for (const row of this.choices) select.add(new Option(row.space_id, row.space_id));
      this.message(this.choices.length ? "Choose a space or explicitly apply multiple spaces." : "No readable spaces available");
      if (initial && this.choices.some(row => row.space_id === "default")) {
        select.value = "default"; this.apply(["default"]);
      }
    } catch (error) { if (this.current(request)) this.failure(error); }
    finally {
      this.session.reads.delete(request.controller);
      if (this.current(request)) this.region.setAttribute("aria-busy", "false");
    }
  }

  edit() {
    const options = document.querySelector("#space-options"); options.replaceChildren();
    for (const row of this.choices) {
      const label = document.createElement("label"), input = document.createElement("input");
      input.type = "checkbox"; input.value = row.space_id; input.checked = this.selected.includes(row.space_id);
      label.append(input, document.createTextNode(row.space_id)); options.append(label);
    }
    document.querySelector("#selection-form").hidden = false;
  }
  cancel() { document.querySelector("#selection-form").hidden = true; }

  apply(values) {
    this.epoch++; this.controller?.abort(); this.selected = [...values];
    document.querySelector("#active-selection").textContent = values.length ? `Active selection: ${values.join(", ")}` : "No spaces selected";
    document.querySelector("#single-space").value = values.length === 1 ? values[0] : "";
    const region = document.querySelector("#capabilities"); region.replaceChildren();
    for (const row of this.choices.filter(row => values.includes(row.space_id))) {
      const p = document.createElement("p");
      p.textContent = `${row.space_id}: permitted reads: ${row.read_capabilities.join(", ") || "none"}. Relationship dialects: ${row.relationship_dialects.join(", ") || "none"}. Indexing policy: ${row.indexing}.`;
      region.append(p);
    }
    void this.status();
  }

  failure(error) {
    if (error.message === "denied") {
      this.selected = []; this.choices = [];
      document.querySelector("#active-selection").textContent = "No spaces selected";
      document.querySelector("#capabilities").replaceChildren();
      document.querySelector("#single-space").replaceChildren(new Option("Choose a space", ""));
      document.querySelector("#space-options").replaceChildren(); this.cancel();
      document.querySelector("#refresh-status").disabled = true;
      this.message("Selection unavailable. Reload spaces explicitly.");
    } else this.message(error.message === "rate_limited" ?
      `Rate limited. ${error.retryAfter !== undefined ? `Retry after ${error.retryAfter} seconds.` : "Wait before retrying explicitly."}` : "Spaces unavailable. Retry explicitly.");
  }

  async status() {
    const request = this.begin(), selected = [...this.selected];
    const region = document.querySelector("#index-status"); region.replaceChildren();
    document.querySelector("#refresh-status").disabled = !selected.length;
    if (!selected.length) {
      this.session.reads.delete(request.controller);
      this.message("No spaces selected. Choose spaces explicitly."); return;
    }
    this.message("Loading selected-space status…");
    this.region.setAttribute("aria-busy", "true");
    try {
      const data = await this.client.get("api/v1/knowledge/spaces/status", selected.map(value => ["spaces", value]), request.controller.signal);
      if (!this.current(request)) return;
      if (!exact(data, ["spaces"]) || !Array.isArray(data.spaces) || data.spaces.length !== selected.length ||
          data.spaces.some((row, i) => !exact(row, ["space_id", "index_state", "search_available", "refresh_required"]) ||
            row.space_id !== selected.slice().sort()[i] || !["disabled", "not_permitted", "indexing", "ready", "unavailable"].includes(row.index_state) ||
            typeof row.search_available !== "boolean" || typeof row.refresh_required !== "boolean")) throw Error("invalid");
      for (const row of data.spaces) {
        const p = document.createElement("p");
        p.textContent = `${row.space_id}: ${row.index_state}. Semantic search ${row.search_available ? "available" : "unavailable"}. Refresh ${row.refresh_required ? "required" : "not required"}.`;
        region.append(p);
      }
      this.message("Status is an observation, separate from policy permission and future search coverage.");
    } catch (error) { if (this.current(request)) this.failure(error); }
    finally {
      this.session.reads.delete(request.controller);
      if (this.current(request)) this.region.setAttribute("aria-busy", "false");
    }
  }
}
