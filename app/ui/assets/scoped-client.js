const noteActions = {
  list: ["GET", "list", "modified"],
  literal: ["POST", "search", "canonical_path"],
  semantic: ["POST", "related", "semantic"],
};
const scopedErrors = {
  400: ["invalid_path"],
  403: ["scope_not_allowed"],
  404: ["note_not_found"],
  409: ["capability_unavailable"],
  413: ["size_limit"],
  422: ["invalid_scope", "invalid_request"],
  503: ["space_unavailable", "semantic_unavailable", "operation_cancelled"],
};

export const exact = (value, keys) => value && typeof value === "object" &&
  !Array.isArray(value) && Object.keys(value).length === keys.length &&
  keys.every(key => Object.hasOwn(value, key));
export const spaceId = value => typeof value === "string" && /^[a-z][a-z0-9_-]{0,63}$/.test(value);
export const utf8Size = value => new TextEncoder().encode(value).length;

function canonicalPath(path) {
  return typeof path === "string" && /\.md$/i.test(path) &&
    !/^[a-z]:|[\\\x00]|[\uD800-\uDFFF]/iu.test(path) &&
    path.split("/").every(part => part && part !== "." && part !== "..");
}

const nullableText = value => value === null || typeof value === "string";

function validMatch(row, semantic) {
  if (!exact(row, semantic ?
      ["identity", "title", "snippet", "heading", "score", "semantic_score", "lexical_score"] :
      ["identity", "title", "snippet", "modified"]) ||
      !exact(row.identity, ["space_id", "path"]) || !canonicalPath(row.identity.path) ||
      typeof row.title !== "string") return false;
  return semantic ? typeof row.snippet === "string" && nullableText(row.heading) &&
    [row.score, row.semantic_score, row.lexical_score].every(Number.isFinite) :
    nullableText(row.snippet) && nullableText(row.modified);
}

export class ScopedClient {
  constructor(session, base) { this.session = session; this.base = base; }

  get(path, params, {signal, current}) {
    if (!["api/v1/knowledge/spaces", "api/v1/knowledge/spaces/status"].includes(path)) throw Error("invalid_request");
    return this.#request(path, params, {signal, current});
  }

  async results(action, selection, {limit, folder, query}, request) {
    const [method, endpoint, ordering] = noteActions[action];
    const spaces = [...selection];
    if (!spaces.length || spaces.length > 8 || !spaces.every(spaceId) ||
        new Set(spaces).size !== spaces.length || (action === "list" && spaces.length !== 1) ||
        !Number.isInteger(limit) || limit < 1 || limit > 100) throw Error("invalid_request");
    const refinements = {limit, ...(folder ? {folder} : {})};
    const params = action === "list" ? [["spaces", spaces[0]], ...Object.entries(refinements)] : [];
    const body = method === "POST" ? {spaces, ...refinements, [action === "literal" ? "query" : "text"]: query} : undefined;
    const data = await this.#request("api/v1/notes/" + endpoint, params, {...request, method, body, scoped: true});
    if (!exact(data, ["items", "coverage", "state", "ordering", "result_limited"]) ||
        !Array.isArray(data.items) || data.items.length > limit || data.ordering !== ordering ||
        typeof data.result_limited !== "boolean") throw Error("invalid");
    const identities = new Set();
    for (const row of data.items) {
      if (!validMatch(row, action === "semantic") || !spaces.includes(row.identity.space_id)) throw Error("invalid");
      const key = JSON.stringify([row.identity.space_id, row.identity.path]);
      if (identities.has(key)) throw Error("invalid");
      identities.add(key);
    }
    return data;
  }

  async read({space_id, path}, request) {
    if (!spaceId(space_id) || !canonicalPath(path)) throw Error("invalid_request");
    const data = await this.#request("api/v1/notes/read", {space: space_id, path},
      {...request, scoped: true});
    // The read adapter uses the native separator on Windows. Accept only
    // that exact spelling or the canonical POSIX spelling from the result.
    if (!exact(data, ["space_id", "result"]) || data.space_id !== space_id ||
        !exact(data.result, ["path", "content"]) ||
        ![path, path.replaceAll("/", "\\")].includes(data.result.path) ||
        typeof data.result.content !== "string") throw Error("invalid");
    return data.result.content;
  }

  async #request(path, params, {signal, current = () => true, method = "GET", body, scoped = false}) {
    const snapshot = this.session.snapshot;
    const epoch = this.session.epoch;
    const guard = () => {
      if (!current() || epoch !== this.session.epoch || !this.session.check(snapshot)) throw Error("stale");
    };
    guard();
    const url = new URL(path, this.base);
    if (url.origin !== location.origin) throw Error("origin");
    url.search = new URLSearchParams(params).toString();
    const response = await fetch(url, {method, signal, cache: "no-store", credentials: "same-origin", redirect: "error",
      headers: {"X-VaultBridge-UI-Request": "1", ...(body ? {"Content-Type": "application/json"} : {})},
      ...(body ? {body: JSON.stringify(body)} : {})});
    // Authentication failure is authoritative even when its body is unreadable.
    // Keep the captured session guards before the coordinated, synchronous blank.
    if (response.status === 401) {
      guard();
      await this.session.unauthorized(snapshot); throw Error("session");
    }
    guard();
    if (scoped && response.status === 403) throw Error("denied");
    const data = await response.json();
    guard();
    if (!response.ok) {
      const detail = scopedErrors[response.status]?.includes(data?.detail) ? data.detail : "unavailable";
      const error = Error(({403: "denied", 429: "rate_limited"})[response.status] || (scoped ? detail : "unavailable"));
      const retry = response.headers.get("Retry-After");
      if (retry && /^[0-9]{1,5}$/.test(retry)) error.retryAfter = Number(retry);
      throw error;
    }
    return data;
  }
}

// One pending operation per active content view. Clear on selection, navigation
// and session changes as well as new submissions. Identity remains decisive if
// the browser delivers a completion despite cancellation.
export class ViewRequest {
  constructor(session) { this.session = session; this.controller = null; }

  clear() {
    const controller = this.controller;
    this.controller = null;
    controller?.abort();
    this.session.reads.delete(controller);
  }

  begin() {
    this.clear();
    const controller = this.controller = new AbortController();
    const epoch = this.session.epoch;
    this.session.reads.add(controller);
    return {
      signal: controller.signal,
      current: () => this.controller === controller && this.session.epoch === epoch && this.session.check(),
      finish: () => this.session.reads.delete(controller),
    };
  }
}

export function unavailableMessage(error, subject) {
  switch (error.message) {
    case "note_not_found": return "Note no longer available.";
    case "capability_unavailable": return "The selected space or spaces do not support this operation.";
    case "invalid_path": return "Enter a valid vault-relative path.";
    case "invalid_request":
    case "invalid_scope": return "The request was rejected. Check the scope and filters.";
    case "size_limit": return "The note exceeds the allowed size.";
    case "operation_cancelled": return "";
    case "rate_limited": return error.retryAfter === undefined ? "Rate limited. Wait before retrying." :
      `Rate limited. Retry after ${error.retryAfter} seconds.`;
    default: return `${subject} unavailable. Retry explicitly.`;
  }
}
