const searchForm = document.querySelector("#search-form");
const searchWorkspace = document.querySelector("#search-workspace");
const searchFieldset = document.querySelector("#search-fieldset");
const searchAccessState = document.querySelector("#search-access-state");
const searchAccessMessage = document.querySelector("#search-access-message");
const searchAccessAction = document.querySelector("#search-access-action");
const literalModeInput = document.querySelector("#search-mode-literal");
const semanticModeInput = document.querySelector("#search-mode-semantic");
const queryInput = document.querySelector("#search-query");
const queryHelp = document.querySelector("#search-query-help");
const folderInput = document.querySelector("#search-folder");
const limitInput = document.querySelector("#search-limit");
const semanticScoreField = document.querySelector("#semantic-score-field");
const minScoreInput = document.querySelector("#search-min-score");
const submitButton = document.querySelector("#search-submit");
const searchStatus = document.querySelector("#search-status");
const searchResults = document.querySelector("#search-results");
const noteReader = document.querySelector("#note-reader");
const noteReaderBack = document.querySelector("#note-reader-back");
const noteReaderTitle = document.querySelector("#note-reader-title");
const noteReaderPath = document.querySelector("#note-reader-path");
const noteReaderStatus = document.querySelector("#note-reader-status");
const noteReaderContent = document.querySelector("#note-reader-content");
const noteRelationships = document.querySelector("#note-relationships");
const outgoingLinksStatus = document.querySelector("#outgoing-links-status");
const outgoingLinksList = document.querySelector("#outgoing-links-list");
const backlinksStatus = document.querySelector("#backlinks-status");
const backlinksList = document.querySelector("#backlinks-list");

const RELATIONSHIP_RENDER_LIMIT = 20;

const scoreFormatter = new Intl.NumberFormat(undefined, {
  minimumFractionDigits: 0,
  maximumFractionDigits: 4,
});

let authenticatedFetch;
let navigateToApi;
let onAuthenticationRequired;
let accessState = "checking-session";
let unlocked = false;
let selectedMode = "literal";
let requestGeneration = 0;
let activeController = null;
let noteRequestGeneration = 0;
let activeNoteController = null;
let relationshipRequestGeneration = 0;
let activeRelationshipController = null;
let returnFocusTarget = null;

function setText(element, value) {
  element.textContent = String(value);
}

function appendTextElement(parent, tagName, className, value) {
  const element = document.createElement(tagName);
  if (className) {
    element.className = className;
  }
  setText(element, value);
  parent.appendChild(element);
  return element;
}

function clearResults() {
  searchResults.replaceChildren();
}

function abortActiveSearch() {
  requestGeneration += 1;
  activeController?.abort();
  activeController = null;
  submitButton.disabled = !unlocked;
}

function abortActiveNote() {
  noteRequestGeneration += 1;
  activeNoteController?.abort();
  activeNoteController = null;
  relationshipRequestGeneration += 1;
  activeRelationshipController?.abort();
  activeRelationshipController = null;
}

function setStatus(state, message, visible = true) {
  searchStatus.hidden = !visible;
  searchStatus.dataset.searchState = state;
  setText(searchStatus, message);
}

function setReaderStatus(state, message) {
  noteReaderStatus.dataset.noteState = state;
  setText(noteReaderStatus, message);
}

function setRelationshipStatus(element, state, message) {
  element.dataset.relationshipState = state;
  setText(element, message);
}

function clearRelationships() {
  outgoingLinksList.replaceChildren();
  backlinksList.replaceChildren();
  setText(outgoingLinksStatus, "");
  setText(backlinksStatus, "");
  outgoingLinksStatus.removeAttribute("data-relationship-state");
  backlinksStatus.removeAttribute("data-relationship-state");
  noteRelationships.hidden = true;
}

function clearNoteReader() {
  setText(noteReaderTitle, "Note");
  setText(noteReaderPath, "");
  setText(noteReaderStatus, "");
  noteReaderStatus.removeAttribute("data-note-state");
  setText(noteReaderContent, "");
  noteReaderContent.hidden = true;
  clearRelationships();
  returnFocusTarget = null;
}

function showSearchWorkspace(restoreFocus = false) {
  const focusTarget = returnFocusTarget;
  noteReader.hidden = true;
  searchWorkspace.hidden = false;
  clearNoteReader();
  if (restoreFocus && focusTarget?.isConnected) {
    focusTarget.focus();
  }
}

function renderAccessState() {
  if (accessState === "unlocked") {
    searchAccessState.hidden = true;
    searchAccessAction.hidden = true;
    setStatus("idle", "Ready to search.");
    return;
  }

  searchAccessState.hidden = false;
  setStatus(accessState === "checking-session" ? "checking" : "locked", "", false);
  if (accessState === "checking-session") {
    setText(searchAccessMessage, "Checking protected access…");
    searchAccessAction.hidden = true;
    return;
  }
  if (accessState === "unavailable") {
    setText(searchAccessMessage, "Protected access could not be confirmed.");
    setText(searchAccessAction, "Review in API / Integration →");
    searchAccessAction.hidden = false;
    return;
  }
  setText(searchAccessMessage, "Protected search requires unlock.");
  setText(searchAccessAction, "Unlock in API / Integration →");
  searchAccessAction.hidden = false;
}

function resetProtectedState() {
  abortActiveSearch();
  abortActiveNote();
  showSearchWorkspace(false);
  searchForm.reset();
  selectedMode = "literal";
  applyMode("literal");
  clearResults();
}

function applyMode(mode) {
  selectedMode = mode;
  const semantic = mode === "semantic";
  semanticScoreField.hidden = !semantic;
  queryInput.minLength = semantic ? 2 : 1;
  queryInput.maxLength = semantic ? 4000 : 300;
  limitInput.max = semantic ? "20" : "50";
  limitInput.value = semantic ? "5" : "10";
  setText(
    queryHelp,
    semantic
      ? "Describe the text or concept to compare with the vault."
      : "Search note titles and contents literally.",
  );
}

function switchMode(mode) {
  abortActiveSearch();
  clearResults();
  applyMode(mode);
  renderAccessState();
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isNullableString(value) {
  return value === null || typeof value === "string";
}

function isNullableNumber(value) {
  return value === null || (typeof value === "number" && Number.isFinite(value));
}

function isLiteralResult(value) {
  return isObject(value)
    && typeof value.title === "string"
    && typeof value.path === "string"
    && isNullableString(value.snippet);
}

function isSemanticResult(value) {
  return isObject(value)
    && typeof value.title === "string"
    && typeof value.path === "string"
    && isNullableString(value.heading)
    && isNullableString(value.snippet)
    && isNullableNumber(value.score)
    && isNullableNumber(value.semantic_score)
    && isNullableNumber(value.lexical_score);
}

function isSearchPayload(value, mode) {
  if (!isObject(value) || !Array.isArray(value.results)) {
    return false;
  }
  const resultValidator = mode === "semantic" ? isSemanticResult : isLiteralResult;
  return value.results.every(resultValidator);
}

function isNotePayload(value) {
  return isObject(value)
    && typeof value.path === "string"
    && typeof value.content === "string";
}

function isOutgoingLink(value) {
  return isObject(value)
    && typeof value.target === "string"
    && isNullableString(value.heading)
    && isNullableString(value.alias)
    && (value.state === "resolved" || value.state === "unresolved")
    && isNullableString(value.resolved_path)
    && (value.state !== "resolved" || value.resolved_path !== null)
    && (value.state !== "unresolved" || value.resolved_path === null);
}

function isBacklink(value) {
  return isObject(value)
    && typeof value.source_path === "string"
    && typeof value.target === "string"
    && isNullableString(value.heading)
    && isNullableString(value.alias);
}

function isRelationshipPayload(value, collection, validator) {
  return isObject(value)
    && Array.isArray(value[collection])
    && value[collection].every(validator);
}

function appendRelationshipFact(item, label, value) {
  if (value !== null) {
    appendTextElement(item, "p", "relationship-item__fact", `${label}: ${value}`);
  }
}

function renderOutgoingLink(link) {
  const item = document.createElement("li");
  item.className = "relationship-item";
  appendRelationshipFact(item, "Written target", link.target);
  appendRelationshipFact(item, "State", link.state === "resolved" ? "Resolved" : "Unresolved");
  appendRelationshipFact(item, "Resolved path", link.resolved_path);
  appendRelationshipFact(item, "Heading", link.heading);
  appendRelationshipFact(item, "Display alias", link.alias);
  outgoingLinksList.appendChild(item);
}

function renderBacklink(backlink) {
  const item = document.createElement("li");
  item.className = "relationship-item";
  appendRelationshipFact(item, "Source note", backlink.source_path);
  appendRelationshipFact(item, "Heading", backlink.heading);
  appendRelationshipFact(item, "Display alias", backlink.alias);
  backlinksList.appendChild(item);
}

function renderRelationshipGroup(items, list, status, emptyMessage, noun, renderItem) {
  list.replaceChildren();
  if (items.length === 0) {
    setRelationshipStatus(status, "empty", emptyMessage);
    return;
  }
  items.slice(0, RELATIONSHIP_RENDER_LIMIT).forEach(renderItem);
  const hiddenCount = items.length - RELATIONSHIP_RENDER_LIMIT;
  const shownCount = Math.min(items.length, RELATIONSHIP_RENDER_LIMIT);
  const suffix = hiddenCount > 0 ? ` ${hiddenCount} additional ${noun} not shown.` : "";
  setRelationshipStatus(status, "ready", `${shownCount} ${noun} shown.${suffix}`);
}

function messageForRelationshipError(error) {
  if (error.kind === "rate-limited") {
    return error.retryAfter === null
      ? "Relationship data is rate limited. Retry later."
      : `Relationship data is rate limited. Retry in ${error.retryAfter} seconds.`;
  }
  if (error.kind === "network") {
    return "Unable to load relationship data. Check the connection and try again.";
  }
  if (error.kind === "not-found") {
    return "Relationship data is unavailable because the note no longer exists.";
  }
  return "Relationship data is temporarily unavailable.";
}

async function loadRelationshipGroup(
  { endpoint, notePath, collection, validator, list, status, emptyMessage, noun, renderItem },
  generation,
  signal,
) {
  try {
    const response = await authenticatedFetch(
      `${endpoint}?path=${encodeURIComponent(notePath)}`,
      { method: "GET", headers: { Accept: "application/json" }, signal },
    );
    let payload;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (generation !== relationshipRequestGeneration || !unlocked || noteReader.hidden) {
      return;
    }
    if (!isRelationshipPayload(payload, collection, validator)) {
      list.replaceChildren();
      setRelationshipStatus(status, "error", "VaultBridge returned unexpected relationship data.");
      return;
    }
    renderRelationshipGroup(payload[collection], list, status, emptyMessage, noun, renderItem);
  } catch (error) {
    if (generation !== relationshipRequestGeneration || error.name === "StaleRequestError") {
      return;
    }
    if (error.kind === "authentication-required") {
      onAuthenticationRequired();
      return;
    }
    list.replaceChildren();
    setRelationshipStatus(status, "error", messageForRelationshipError(error));
  }
}

async function loadRelationships(notePath) {
  relationshipRequestGeneration += 1;
  activeRelationshipController?.abort();
  const generation = relationshipRequestGeneration;
  const controller = new AbortController();
  activeRelationshipController = controller;
  outgoingLinksList.replaceChildren();
  backlinksList.replaceChildren();
  noteRelationships.hidden = false;
  setRelationshipStatus(outgoingLinksStatus, "loading", "Loading outgoing links…");
  setRelationshipStatus(backlinksStatus, "loading", "Loading backlinks…");

  const groups = [
    {
      endpoint: "api/v1/notes/links",
      notePath,
      collection: "links",
      validator: isOutgoingLink,
      list: outgoingLinksList,
      status: outgoingLinksStatus,
      emptyMessage: "No outgoing links.",
      noun: "outgoing links",
      renderItem: renderOutgoingLink,
    },
    {
      endpoint: "api/v1/notes/backlinks",
      notePath,
      collection: "backlinks",
      validator: isBacklink,
      list: backlinksList,
      status: backlinksStatus,
      emptyMessage: "No backlinks.",
      noun: "backlinks",
      renderItem: renderBacklink,
    },
  ];

  try {
    await Promise.all(groups.map((group) => (
      loadRelationshipGroup(group, generation, controller.signal)
    )));
  } finally {
    if (generation === relationshipRequestGeneration) {
      activeRelationshipController = null;
    }
  }
}

function displayScore(value) {
  return value === null ? "Not available" : scoreFormatter.format(value);
}

function appendSemanticScores(card, result) {
  const scores = document.createElement("dl");
  scores.className = "search-result__scores";
  for (const [label, value] of [
    ["Combined score", result.score],
    ["Semantic score", result.semantic_score],
    ["Lexical score", result.lexical_score],
  ]) {
    const score = document.createElement("div");
    appendTextElement(score, "dt", "", label);
    appendTextElement(score, "dd", "", displayScore(value));
    scores.appendChild(score);
  }
  card.appendChild(scores);
}

function renderResult(result, mode, index) {
  const item = document.createElement("li");
  item.className = "search-result";

  const heading = document.createElement("div");
  heading.className = "search-result__heading";
  if (mode === "semantic") {
    appendTextElement(heading, "span", "search-result__rank", `#${index + 1}`);
  }
  const title = document.createElement("h3");
  const openButton = document.createElement("button");
  openButton.className = "search-result__open";
  openButton.type = "button";
  setText(openButton, result.title);
  openButton.addEventListener("click", () => {
    void openNote(result, openButton);
  });
  title.appendChild(openButton);
  heading.appendChild(title);
  item.appendChild(heading);
  appendTextElement(
    item,
    "p",
    result.snippet ? "search-result__snippet" : "search-result__snippet muted",
    result.snippet || "No snippet available.",
  );
  if (mode === "semantic" && result.heading) {
    appendTextElement(item, "p", "search-result__context", `Heading: ${result.heading}`);
  }
  appendTextElement(item, "p", "search-result__path", result.path);

  if (mode === "semantic") {
    appendSemanticScores(item, result);
  }
  searchResults.appendChild(item);
}

function messageForNoteError(error) {
  if (error.kind === "not-found") {
    return "This note is no longer available in the vault.";
  }
  if (error.kind === "rate-limited") {
    return error.retryAfter === null
      ? "Rate limit reached. Retry later."
      : `Rate limit reached. Retry in ${error.retryAfter} seconds.`;
  }
  if (error.kind === "network") {
    return "Unable to connect to VaultBridge. Check the connection and try again.";
  }
  if (error.kind === "service-unavailable" || error.kind === "server-error") {
    return "The complete note is temporarily unavailable. Try again later.";
  }
  return "VaultBridge returned an unexpected note response.";
}

async function openNote(result, trigger) {
  abortActiveNote();
  const generation = noteRequestGeneration;
  const controller = new AbortController();
  activeNoteController = controller;
  returnFocusTarget = trigger;

  searchWorkspace.hidden = true;
  noteReader.hidden = false;
  setText(noteReaderTitle, result.title);
  setText(noteReaderPath, result.path);
  setText(noteReaderContent, "");
  noteReaderContent.hidden = true;
  clearRelationships();
  setReaderStatus("loading", "Loading complete note…");
  noteReader.focus();

  try {
    const response = await authenticatedFetch(
      `api/v1/notes/read?path=${encodeURIComponent(result.path)}`,
      { method: "GET", headers: { Accept: "application/json" }, signal: controller.signal },
    );
    let payload;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (generation !== noteRequestGeneration || !unlocked || noteReader.hidden) {
      return;
    }
    if (!isNotePayload(payload)) {
      setReaderStatus("error", "VaultBridge returned an unexpected note response.");
      return;
    }
    setText(noteReaderPath, payload.path);
    setText(noteReaderContent, payload.content);
    noteReaderContent.hidden = false;
    setReaderStatus("ready", "Complete note loaded.");
    void loadRelationships(payload.path);
  } catch (error) {
    if (generation !== noteRequestGeneration || error.name === "StaleRequestError") {
      return;
    }
    if (error.kind === "authentication-required") {
      onAuthenticationRequired();
      return;
    }
    setReaderStatus("error", messageForNoteError(error));
  } finally {
    if (generation === noteRequestGeneration) {
      activeNoteController = null;
    }
  }
}

function renderResults(results, mode) {
  clearResults();
  if (results.length === 0) {
    setStatus("empty", `No matching notes found in ${mode} search.`);
    return;
  }
  results.forEach((result, index) => renderResult(result, mode, index));
  const suffix = results.length === 1 ? "result" : "results";
  setStatus("results", `${results.length} ${suffix} returned by ${mode} search.`);
}

function requestDetails(mode) {
  const folder = folderInput.value;
  const limit = Number(limitInput.value);
  if (mode === "semantic") {
    return {
      path: "api/v1/notes/related",
      body: {
        text: queryInput.value,
        folder,
        limit,
        min_score: Number(minScoreInput.value),
      },
    };
  }
  return {
    path: "api/v1/notes/search",
    body: { query: queryInput.value, folder, limit },
  };
}

function messageForSearchError(error, mode) {
  if (error.kind === "rate-limited") {
    return error.retryAfter === null
      ? "Rate limit reached. Retry later."
      : `Rate limit reached. Retry in ${error.retryAfter} seconds.`;
  }
  if (error.kind === "service-unavailable" && mode === "semantic") {
    return "Semantic search is currently unavailable. Literal search remains available.";
  }
  if (error.kind === "request-rejected") {
    return "Search inputs were rejected. Check the values and try again.";
  }
  if (error.kind === "network") {
    return "Unable to connect to VaultBridge. Check the connection and try again.";
  }
  if (error.kind === "service-unavailable" || error.kind === "server-error") {
    return "Search is temporarily unavailable. Try again later.";
  }
  return "Search failed because VaultBridge returned an unexpected response.";
}

async function submitSearch() {
  if (!unlocked) {
    setStatus("locked", "Unlock the dashboard before searching.");
    return;
  }

  abortActiveSearch();
  const mode = selectedMode;
  const generation = requestGeneration;
  const controller = new AbortController();
  activeController = controller;
  submitButton.disabled = true;
  clearResults();
  setStatus("loading", `Searching in ${mode} mode.`);

  const request = requestDetails(mode);
  try {
    const response = await authenticatedFetch(request.path, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request.body),
      signal: controller.signal,
    });
    const payload = await response.json();
    if (generation !== requestGeneration || mode !== selectedMode || !unlocked) {
      return;
    }
    if (!isSearchPayload(payload, mode)) {
      setStatus("error", "VaultBridge returned unexpected search results.");
      return;
    }
    renderResults(payload.results, mode);
  } catch (error) {
    if (generation !== requestGeneration || error.name === "StaleRequestError") {
      return;
    }
    clearResults();
    if (error.kind === "authentication-required") {
      onAuthenticationRequired();
      return;
    }
    setStatus("error", messageForSearchError(error, mode));
  } finally {
    if (generation === requestGeneration) {
      activeController = null;
      submitButton.disabled = !unlocked;
    }
  }
}

literalModeInput.addEventListener("change", () => {
  if (literalModeInput.checked) {
    switchMode("literal");
  }
});

semanticModeInput.addEventListener("change", () => {
  if (semanticModeInput.checked) {
    switchMode("semantic");
  }
});

searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!searchForm.checkValidity()) {
    searchForm.reportValidity();
    return;
  }
  void submitSearch();
});

searchAccessAction.addEventListener("click", () => navigateToApi());

noteReaderBack.addEventListener("click", () => {
  abortActiveNote();
  showSearchWorkspace(true);
});

export function initializeSearch(options) {
  authenticatedFetch = options.authenticatedFetch;
  navigateToApi = options.navigateToApi;
  onAuthenticationRequired = options.onAuthenticationRequired;
  applyMode("literal");
  return {
    setAccessState(value) {
      accessState = value;
      unlocked = accessState === "unlocked";
      searchFieldset.disabled = !unlocked;
      if (!unlocked) {
        resetProtectedState();
      } else {
        submitButton.disabled = false;
      }
      renderAccessState();
    },
    deactivate() {
      abortActiveNote();
      showSearchWorkspace(false);
    },
  };
}
