const refreshOverviewButton = document.querySelector("#refresh-overview-button");
const overviewUpdateStatus = document.querySelector("#overview-update-status");
const overviewContent = document.querySelector("#overview-content");
const overallStatus = document.querySelector("#overall-status");
const applicationHealth = document.querySelector("#application-health");
const vaultAvailable = document.querySelector("#vault-available");
const vaultNotes = document.querySelector("#vault-notes");
const semanticIndexState = document.querySelector("#semantic-index-state");
const semanticIndexReady = document.querySelector("#semantic-index-ready");
const semanticSearchAvailable = document.querySelector("#semantic-search-available");
const indexedNotes = document.querySelector("#indexed-notes");
const semanticChunks = document.querySelector("#semantic-chunks");
const lastSuccessfulSync = document.querySelector("#last-successful-sync");
const semanticIndexerRunning = document.querySelector("#semantic-indexer-running");
const fullSyncRequired = document.querySelector("#full-sync-required");

const countFormatter = new Intl.NumberFormat();
const dateTimeFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "medium",
});
const HEALTH_INDEX_STATES = ["uninitialized", "indexing", "ready", "error"];

function setText(element, value) {
  element.textContent = String(value);
}

function isHealthPayload(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }

  const booleanFields = [
    "ok",
    "vault_exists",
    "semantic_index_ready",
    "semantic_search_available",
    "semantic_indexer_running",
    "full_sync_required",
  ];
  const countFields = ["indexed_notes", "semantic_chunks", "vault_notes"];

  return booleanFields.every((field) => typeof value[field] === "boolean")
    && countFields.every((field) => Number.isSafeInteger(value[field]) && value[field] >= 0)
    && HEALTH_INDEX_STATES.includes(value.semantic_index_state)
    && (value.last_successful_sync === null || typeof value.last_successful_sync === "string");
}

function displayBoolean(value, trueLabel, falseLabel) {
  return value ? trueLabel : falseLabel;
}

function displayIndexState(value) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function deriveOverviewState(health) {
  if (!health.vault_exists) {
    return "unavailable";
  }
  if (health.semantic_index_state === "indexing") {
    return "indexing";
  }
  if (
    health.ok
    && health.semantic_search_available
    && health.semantic_index_state === "ready"
  ) {
    return "ready";
  }
  return "degraded";
}

function renderLastSuccessfulSync(value) {
  lastSuccessfulSync.removeAttribute("datetime");
  if (value === null) {
    setText(lastSuccessfulSync, "Never");
    return;
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    setText(lastSuccessfulSync, "Not available");
    return;
  }

  lastSuccessfulSync.setAttribute("datetime", value);
  setText(lastSuccessfulSync, dateTimeFormatter.format(parsed));
}

function renderHealth(health) {
  const overviewState = deriveOverviewState(health);
  const overviewLabels = {
    ready: "Ready",
    indexing: "Indexing",
    degraded: "Degraded",
    unavailable: "Unavailable",
  };

  overallStatus.dataset.overviewState = overviewState;
  setText(overallStatus, overviewLabels[overviewState]);
  setText(applicationHealth, displayBoolean(health.ok, "Healthy", "Not healthy"));
  setText(vaultAvailable, displayBoolean(health.vault_exists, "Yes", "No"));
  setText(vaultNotes, countFormatter.format(health.vault_notes));
  setText(semanticIndexState, displayIndexState(health.semantic_index_state));
  setText(semanticIndexReady, displayBoolean(health.semantic_index_ready, "Yes", "No"));
  setText(
    semanticSearchAvailable,
    displayBoolean(health.semantic_search_available, "Available", "Unavailable"),
  );
  setText(indexedNotes, countFormatter.format(health.indexed_notes));
  setText(semanticChunks, countFormatter.format(health.semantic_chunks));
  renderLastSuccessfulSync(health.last_successful_sync);
  setText(
    semanticIndexerRunning,
    displayBoolean(health.semantic_indexer_running, "Running", "Stopped"),
  );
  setText(fullSyncRequired, displayBoolean(health.full_sync_required, "Yes", "No"));

  overviewContent.hidden = false;
  overviewContent.setAttribute("aria-busy", "false");
  setText(overviewUpdateStatus, "Health information updated.");
}

function setOverviewLoading() {
  refreshOverviewButton.disabled = true;
  overviewContent.setAttribute("aria-busy", "true");
  setText(overviewUpdateStatus, "Loading health information.");
}

function resetHealthValues() {
  overallStatus.dataset.overviewState = "unavailable";
  setText(overallStatus, "Unavailable");
  for (const element of [
    applicationHealth,
    vaultAvailable,
    vaultNotes,
    semanticIndexState,
    semanticIndexReady,
    semanticSearchAvailable,
    indexedNotes,
    semanticChunks,
    semanticIndexerRunning,
    fullSyncRequired,
  ]) {
    setText(element, "Not available");
  }
  lastSuccessfulSync.removeAttribute("datetime");
  setText(lastSuccessfulSync, "Not available");
}

function setOverviewError(kind) {
  resetHealthValues();
  overviewContent.hidden = false;
  overviewContent.setAttribute("aria-busy", "false");
  const message = kind === "malformed"
    ? "VaultBridge returned unexpected health information. Try refreshing."
    : "Health information is unavailable. Check the connection and try again.";
  setText(overviewUpdateStatus, message);
}

async function loadOverview(applicationUrl) {
  setOverviewLoading();
  try {
    const response = await fetch(applicationUrl("health"), {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      setOverviewError("unavailable");
      return;
    }

    let health;
    try {
      health = await response.json();
    } catch {
      setOverviewError("malformed");
      return;
    }
    if (!isHealthPayload(health)) {
      setOverviewError("malformed");
      return;
    }
    renderHealth(health);
  } catch {
    setOverviewError("unavailable");
  } finally {
    refreshOverviewButton.disabled = false;
  }
}

export function initializeOverview(applicationUrl) {
  refreshOverviewButton.addEventListener("click", () => {
    void loadOverview(applicationUrl);
  });
  void loadOverview(applicationUrl);
}
