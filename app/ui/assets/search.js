const searchForm = document.querySelector("#search-form");
const searchFieldset = document.querySelector("#search-fieldset");
const searchAccessState = document.querySelector("#search-access-state");
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

const scoreFormatter = new Intl.NumberFormat(undefined, {
  minimumFractionDigits: 0,
  maximumFractionDigits: 4,
});

let authenticatedFetch;
let onAuthenticationRequired;
let unlocked = false;
let selectedMode = "literal";
let requestGeneration = 0;
let activeController = null;

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

function setStatus(state, message) {
  searchStatus.dataset.searchState = state;
  setText(searchStatus, message);
}

function resetProtectedState() {
  abortActiveSearch();
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
  setStatus(unlocked ? "idle" : "locked", unlocked ? "Ready to search." : "Search is locked.");
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

function displayScore(value) {
  return value === null ? "Not available" : scoreFormatter.format(value);
}

function appendSemanticScores(card, result) {
  const scores = document.createElement("dl");
  scores.className = "search-result__scores";
  for (const [label, value] of [
    ["Score", result.score],
    ["Semantic", result.semantic_score],
    ["Lexical", result.lexical_score],
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
  appendTextElement(heading, "h3", "", result.title);
  item.appendChild(heading);
  appendTextElement(item, "p", "search-result__path", result.path);

  if (mode === "semantic" && result.heading) {
    appendTextElement(item, "p", "search-result__context", `Heading: ${result.heading}`);
  }
  appendTextElement(
    item,
    "p",
    result.snippet ? "search-result__snippet" : "search-result__snippet muted",
    result.snippet || "No snippet available.",
  );

  if (mode === "semantic") {
    appendSemanticScores(item, result);
  }
  searchResults.appendChild(item);
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

export function initializeSearch(options) {
  authenticatedFetch = options.authenticatedFetch;
  onAuthenticationRequired = options.onAuthenticationRequired;
  applyMode("literal");
  return {
    setUnlocked(value) {
      unlocked = value;
      searchFieldset.disabled = !unlocked;
      if (!unlocked) {
        resetProtectedState();
        setText(searchAccessState, "Unlock the dashboard to use protected search.");
        setStatus("locked", "Search is locked.");
        return;
      }
      submitButton.disabled = false;
      setText(searchAccessState, "Protected search is ready.");
      setStatus("idle", "Ready to search.");
    },
  };
}
