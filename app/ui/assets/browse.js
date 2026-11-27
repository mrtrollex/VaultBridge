import {showCoverage} from "./coverage.js";
import {unavailableMessage, utf8Size, ViewRequest} from "./scoped-client.js";

// Browse and Search use the same qualified results and submission lifecycle.
export class NoteResults {
  constructor(id, {client, open, deny}, parameters) {
    this.client = client;
    this.open = open;
    this.deny = deny;
    this.parameters = parameters;
    this.request = new ViewRequest(client.session);
    this.panel = document.getElementById(id);
    this.form = this.panel.querySelector("form");
    this.fields = this.form.querySelector("fieldset");
    this.folder = this.form.elements.folder;
    this.limit = this.form.elements.limit;
    this.scope = this.panel.querySelector(".named-scope");
    this.feedback = this.panel.querySelector("[role=status]");
    this.coverage = this.panel.querySelector(".named-coverage");
    this.rows = this.panel.querySelector("ol");
    this.form.addEventListener("submit", event => {
      event.preventDefault();
      void this.submit();
    });
    this.form.addEventListener("input", () => this.clear());
  }

  clear() {
    this.request.clear();
    this.rows.replaceChildren();
    this.coverage.replaceChildren();
    this.feedback.textContent = "";
    this.panel.setAttribute("aria-busy", "false");
  }

  reset() {
    this.form.reset();
    this.scope.textContent = "";
    this.selection = [];
    this.fields.disabled = true;
    this.clear();
  }

  select(choices) {
    this.selection = choices;
    this.updateScope();
  }

  updateScope() {
    const {capability, action} = this.parameters(this.form);
    const reason = !this.selection.length ? "Choose spaces in Spaces." :
      this.selection.some(row => !row.read_capabilities.includes(capability)) ?
        "This operation is unavailable for the current selection." :
        action === "semantic" && this.selection.some(row => row.indexing === "disabled") ?
          "Semantic indexing is disabled for part or all of the selection." : "";
    this.scope.textContent = [this.selection.map(row => row.space_id).join(", "), reason].filter(Boolean).join(" · ");
    this.form.querySelector('button[type="submit"]').disabled = !!reason;
    this.fields.disabled = !this.selection.length;
  }

  validate(field, message) {
    this.feedback.textContent = message;
    field.focus();
  }

  async submit() {
    if (this.panel.hidden || this.form.querySelector('button[type="submit"]').disabled ||
        !this.client.session.check()) return;
    this.clear();
    const {action, input, maxBytes} = this.parameters(this.form);
    const query = input?.value;
    const folder = this.folder.value;
    if (input && (!query.trim() || utf8Size(query) > maxBytes)) {
      this.validate(input, `Enter query text up to ${maxBytes} UTF-8 bytes.`);
      return;
    }
    if (utf8Size(folder) > 1024) {
      this.validate(this.folder, "Enter a folder path up to 1024 UTF-8 bytes.");
      return;
    }
    const selection = this.selection;
    const selected = selection.map(row => row.space_id);
    const readable = new Set(selection.filter(row => row.read_capabilities.includes("read_note")).map(row => row.space_id));
    const request = this.request.begin();
    this.feedback.textContent = "Loading notes…";
    this.panel.setAttribute("aria-busy", "true");
    try {
      const data = await this.client.results(action, selected, {query, folder, limit: Number(this.limit.value)}, request);
      if (!request.current()) return;
      showCoverage(this.coverage, data, selected);
      for (const row of data.items) this.add(row, readable);
      this.feedback.textContent = [
        data.items.length ? `${data.items.length} returned ${data.items.length === 1 ? "note" : "notes"}.` :
          data.state === "partial" ? "No notes returned with partial coverage." : "No matching notes.",
        data.result_limited ? "Result limit reached. Refine the request or increase the limit." : "",
      ].filter(Boolean).join(" ");
    } catch (error) {
      if (!request.current()) return;
      if (error.message === "denied") this.deny();
      else if (error.message === "invalid_path") this.validate(this.folder, unavailableMessage(error, "Results"));
      else this.feedback.textContent = unavailableMessage(error, "Results");
    } finally {
      request.finish();
      if (request.current()) this.panel.setAttribute("aria-busy", "false");
    }
  }

  add(row, readable) {
    const item = document.createElement("li");
    const {space_id, path} = row.identity;
    item.dataset.identity = JSON.stringify([space_id, path]);
    const title = document.createElement("h3");
    title.textContent = row.title || path;
    const identity = document.createElement("p");
    identity.textContent = `${space_id} · ${path}`;
    item.append(title, identity);
    for (const text of [row.modified, row.heading, row.snippet,
      row.score === undefined ? "" : `Score ${row.score} · Semantic ${row.semantic_score} · Lexical ${row.lexical_score}`]) {
      if (!text) continue;
      const field = document.createElement("p");
      field.textContent = text;
      item.append(field);
    }
    const button = document.createElement("button");
    button.type = "button";
    button.disabled = !readable.has(space_id);
    button.textContent = button.disabled ? "Open note unavailable" : "Open note";
    button.setAttribute("aria-label", `${button.textContent} in ${space_id}: ${path}`);
    button.addEventListener("click", () => {
      if (!this.panel.hidden && !button.disabled && button.isConnected && this.client.session.check()) {
        this.open(this.panel, button, row.identity, row.title);
      }
    });
    item.append(button);
    this.rows.append(item);
  }
}

export function createBrowse(options) {
  return new NoteResults("browse", options, () => ({action: "list", capability: "list_notes"}));
}
