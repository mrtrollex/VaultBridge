// ADR 0011: this journal gates cooperating UI only; it never authorizes REST.
export const LOCK = "vaultbridge.named-ui-session.v1";
export const INTENT = "vaultbridge.named-ui-session.intent.v1";
export const JOURNAL = "vaultbridge.named-ui-session.journal.v1";
const dirty = new Set(["posting", "deleting", "uncertain"]);
const phases = new Set(["locked", "restorable", ...dirty, "logout_pending"]);
const nonce = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, "0")).join("");
const isNonce = value => typeof value === "string" && /^[0-9a-f]{32}$/.test(value);
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);

export class NamedSession {
  constructor(url, changed) {
    this.url = url;
    this.changed = changed;
    this.epoch = 0;
    this.snapshot = null;
    this.reads = new Set();
    this.supported = window.isSecureContext && window.top === window.self && !!navigator.locks?.request;
    try { this.records(); } catch { this.supported = false; }
    this.state = this.supported ? "locked" : "unsupported";
    window.addEventListener("storage", e => {
      if (e.key === null || e.key === INTENT || e.key === JOURNAL) this.check();
    });
    window.addEventListener("pagehide", () => this.blank("locked"));
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) this.blank("locked");
      else this.resume();
    });
    window.addEventListener("pageshow", () => this.resume());
    window.addEventListener("focus", () => this.resume());
  }

  resume() {
    if (this.mutating) return;
    try {
      const journal = this.records().journal;
      // A first-visit locked form has no session to restore. In particular,
      // focusing that form must not erase a key the user is entering.
      if (this.snapshot || (journal && journal.phase !== "locked")) void this.restore();
    } catch { this.blank("unsupported"); }
  }

  read(key) {
    const raw = localStorage.getItem(key);
    if (raw === null) return null;
    if (new TextEncoder().encode(raw).length > 512) throw Error("journal");
    const value = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) throw Error("journal");
    return value;
  }

  records() {
    const intent = this.read(INTENT);
    const journal = this.read(JOURNAL);
    if (intent && (Object.keys(intent).sort().join() !== "nonce,version" || intent.version !== 1 || !isNonce(intent.nonce))) throw Error("journal");
    if (journal && (journal.version !== 1 || !phases.has(journal.phase) || !isNonce(journal.revision) ||
        !(journal.intent === null || isNonce(journal.intent)) ||
        (journal.mutation !== undefined && !isNonce(journal.mutation)) ||
        Object.keys(journal).some(k => !["version", "intent", "phase", "revision", "mutation"].includes(k)))) throw Error("journal");
    return {intent: intent?.nonce ?? null, journal};
  }

  write(phase, intent, mutation) {
    const journal = {version: 1, intent, phase, revision: nonce(), ...(mutation ? {mutation} : {})};
    localStorage.setItem(JOURNAL, JSON.stringify(journal));
    if (!equal(this.records().journal, journal)) throw Error("journal");
    return journal;
  }

  blank(state) {
    this.epoch++;
    this.snapshot = null;
    for (const controller of this.reads) controller.abort();
    this.reads.clear();
    this.state = state;
    this.changed(state);
  }

  check(snapshot = this.snapshot) {
    if (!this.supported) return false;
    try {
      const record = this.records();
      if (snapshot && record.intent === snapshot.intent && equal(record.journal, snapshot) && snapshot.phase === "restorable") return true;
      if (this.snapshot) this.blank(dirty.has(record.journal?.phase) ? "pending" : "logout_pending");
    } catch { this.blank("unsupported"); }
    return false;
  }

  async locked(action) {
    if (!this.supported) { this.blank("unsupported"); return; }
    try { return await navigator.locks.request(LOCK, {mode: "exclusive"}, action); }
    catch { this.blank(this.mutating ? "uncertain" : "unsupported"); }
  }

  async wire(method, body, signal) {
    const response = await fetch(this.url, {
      method, credentials: "same-origin", cache: "no-store", redirect: "error", signal,
      headers: {"X-VaultBridge-UI-Request": "1", ...(body ? {"Content-Type": "application/json"} : {})},
      ...(body ? {body} : {}),
    });
    // A complete body is part of settlement. Never abort cookie mutations.
    const data = await response.json();
    if (!data || typeof data !== "object" || Array.isArray(data)) throw Error("response");
    if (response.ok && (Object.keys(data).join() !== "authenticated" || data.authenticated !== (method !== "DELETE"))) throw Error("response");
    const retry = response.headers.get("Retry-After");
    this.retryAfter = retry && /^[0-9]{1,5}$/.test(retry) ? Number(retry) : null;
    return response;
  }

  async login(key) {
    if (!this.supported) { this.blank("unsupported"); return; }
    let captured;
    try {
      const {intent, journal} = this.records();
      captured = intent;
      // A submission during pending deletion is not a deliberate login AFTER
      // confirmed logout. Never let waiting for the lock turn it into one.
      if (journal?.phase === "uncertain") { this.blank("uncertain"); return; }
      if (journal?.phase === "deleting" || journal?.phase === "logout_pending" ||
          (journal && journal.intent !== intent) || (!journal && intent !== null)) {
        this.blank("logout_pending");
        return;
      }
    } catch { this.blank("unsupported"); return; }
    this.blank("pending");
    await this.locked(async () => {
      let record = this.records();
      if (record.intent !== captured) { this.blank("locked"); return; }
      if (dirty.has(record.journal?.phase)) { this.blank("uncertain"); return; }
      if (record.journal && record.journal.intent !== captured) { this.blank("logout_pending"); return; }
      if (record.journal?.phase === "logout_pending") { this.blank("logout_pending"); return; }
      this.mutating = true;
      let sent = false;
      try {
        this.write("posting", captured, nonce());
        sent = true;
        const response = await this.wire("POST", JSON.stringify({api_key: key}));
        key = "";
        // 5xx is conservatively ambiguous, even if its body was received.
        if (response.status >= 500) throw Error("outcome");
        const journal = this.write(response.ok ? "restorable" : "locked", captured);
        if (this.records().intent !== captured) { this.blank("locked"); return; }
        if (document.hidden) { this.blank("locked"); return; }
        if (response.ok) { this.snapshot = journal; this.state = "authenticated"; this.changed(this.state); }
        else this.blank(response.status === 429 ? "rate_limited" : "rejected");
      } catch {
        if (!sent) {
          // No issuer was dispatched. Unwritable required browser storage is
          // unsupported, not evidence of an abandoned HTTP response.
          this.supported = false;
          this.blank("unsupported");
          return;
        }
        // The durable posting record remains dirty if writing uncertainty fails.
        try { this.write("uncertain", captured); } catch { /* fail closed */ }
        this.blank("uncertain");
      } finally { key = ""; this.mutating = false; }
    });
    key = "";
  }

  async restore() {
    if (!this.supported) { this.blank("unsupported"); return; }
    this.blank("checking");
    const epoch = this.epoch;
    const snapshot = await this.locked(() => {
      const {intent, journal} = this.records();
      if (dirty.has(journal?.phase)) { this.blank("uncertain"); return null; }
      if (!journal || journal.intent !== intent || journal.phase !== "restorable") {
        this.blank(journal?.phase === "logout_pending" ? "logout_pending" : "locked");
        return null;
      }
      // Detect write denial without modifying the revision or granting access.
      localStorage.setItem(JOURNAL, JSON.stringify(journal));
      return journal;
    });
    if (!snapshot || epoch !== this.epoch) return;
    const controller = new AbortController();
    this.reads.add(controller);
    try {
      const response = await this.wire("GET", null, controller.signal);
      if (epoch !== this.epoch) return;
      if (!this.check(snapshot)) {
        if (epoch === this.epoch) {
          const {intent, journal} = this.records();
          this.blank(dirty.has(journal?.phase) ? "uncertain" :
            journal?.phase === "logout_pending" || (journal && journal.intent !== intent) ? "logout_pending" : "locked");
        }
        return;
      }
      if (response.ok) { this.snapshot = snapshot; this.state = "authenticated"; this.changed(this.state); }
      else if (response.status === 401) await this.unauthorized(snapshot);
      else this.blank(response.status === 429 ? "rate_limited" : "unavailable");
    } catch { if (epoch === this.epoch) this.blank("unavailable"); }
    finally { this.reads.delete(controller); }
  }

  async logout() {
    this.blank("pending");
    if (!this.supported) { this.blank("unsupported"); return; }
    try { localStorage.setItem(INTENT, JSON.stringify({version: 1, nonce: nonce()})); }
    catch { this.blank("uncertain"); return; }
    await this.locked(() => this.remove());
  }

  async unauthorized(snapshot) {
    // Compare under the mutation lock BEFORE publishing intent or clearing a
    // newer session. GET/read responses themselves never delete cookies.
    if (!this.check(snapshot)) return;
    this.blank("pending");
    await this.locked(async () => {
      if (!this.check(snapshot)) return;
      this.blank("pending");
      localStorage.setItem(INTENT, JSON.stringify({version: 1, nonce: nonce()}));
      await this.remove();
    });
  }

  async remove() {
    const {intent, journal} = this.records();
    if (dirty.has(journal?.phase)) { this.blank("uncertain"); return; }
    this.mutating = true;
    try {
      this.write("deleting", intent, nonce());
      const response = await this.wire("DELETE");
      if (response.status >= 500) throw Error("outcome");
      this.write(response.ok ? "locked" : "logout_pending", intent);
      if (this.records().intent !== intent) { this.blank("logout_pending"); return; }
      this.blank(response.ok ? "logged_out" : "logout_pending");
    } catch {
      try { this.write("uncertain", intent); } catch { /* retain dirty evidence */ }
      this.blank("uncertain");
    } finally { this.mutating = false; }
  }
}
