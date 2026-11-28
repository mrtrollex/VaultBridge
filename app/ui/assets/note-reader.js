import {unavailableMessage, ViewRequest} from "./scoped-client.js";

export class NoteReader {
  constructor(client, deny) {
    this.client = client;
    this.deny = deny;
    this.request = new ViewRequest(client.session);
    this.panel = document.querySelector("#named-reader");
    this.title = this.panel.querySelector("h2");
    this.identity = this.panel.querySelector(".named-scope");
    this.feedback = this.panel.querySelector("[role=status]");
    this.markdown = this.panel.querySelector("pre");
    this.panel.querySelector("button").addEventListener("click", () => this.back());
  }

  reset() {
    this.request.clear();
    this.origin = null;
    this.title.textContent = "";
    this.identity.textContent = "";
    this.feedback.textContent = "";
    this.markdown.textContent = "";
    this.markdown.hidden = true;
    this.panel.hidden = true;
    this.panel.setAttribute("aria-busy", "false");
  }

  async open(panel, button, identity, title) {
    this.reset();
    this.origin = {panel, button, scroll: window.scrollY};
    const pinned = {...identity};
    const request = this.request.begin();
    panel.hidden = true;
    this.title.textContent = title || pinned.path;
    this.identity.textContent = `${pinned.space_id} · ${pinned.path}`;
    this.feedback.textContent = "Loading note…";
    this.panel.hidden = false;
    this.panel.setAttribute("aria-busy", "true");
    this.title.focus();
    try {
      const markdown = await this.client.read(pinned, request);
      if (!request.current()) return;
      this.markdown.textContent = markdown;
      this.markdown.hidden = false;
      this.feedback.textContent = "Raw Markdown";
    } catch (error) {
      if (!request.current()) return;
      if (error.message === "denied") this.deny();
      else this.feedback.textContent = unavailableMessage(error, "Note");
    } finally {
      request.finish();
      if (request.current()) this.panel.setAttribute("aria-busy", "false");
    }
  }

  back() {
    const origin = this.origin;
    if (!origin || !this.client.session.check()) return;
    this.reset();
    if (!origin.button.isConnected) return;
    origin.panel.hidden = false;
    origin.button.focus({preventScroll: true});
    window.scrollTo(0, origin.scroll);
  }
}
