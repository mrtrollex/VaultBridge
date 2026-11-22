export class ScopedClient {
  constructor(session, base) { this.session = session; this.base = base; }

  async get(path, params, signal) {
    const snapshot = this.session.snapshot;
    const epoch = this.session.epoch;
    if (!this.session.check(snapshot)) throw Error("session");
    const url = new URL(path, this.base);
    if (url.origin !== location.origin) throw Error("origin");
    url.search = new URLSearchParams(params).toString();
    const response = await fetch(url, {signal, cache: "no-store", credentials: "same-origin", redirect: "error",
      headers: {"X-VaultBridge-UI-Request": "1"}});
    // Authentication failure is authoritative even when its body is unreadable.
    // Keep the captured session guards before the coordinated, synchronous blank.
    if (response.status === 401) {
      if (epoch !== this.session.epoch || !this.session.check(snapshot)) throw Error("stale");
      await this.session.unauthorized(snapshot); throw Error("session");
    }
    const data = await response.json();
    if (epoch !== this.session.epoch || !this.session.check(snapshot)) throw Error("stale");
    if (!response.ok) {
      const error = Error(({403: "denied", 429: "rate_limited", 503: "unavailable"})[response.status] || "unavailable");
      const retry = response.headers.get("Retry-After");
      if (retry && /^[0-9]{1,5}$/.test(retry)) error.retryAfter = Number(retry);
      throw error;
    }
    return data;
  }
}
