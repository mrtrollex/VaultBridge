import { initializeOverview } from "./overview.js";
import { initializeSearch } from "./search.js";

const SESSION_STORAGE_KEY = "vaultbridge.ui.apiKey";
const SESSION_STATES = ["checking-session", "locked", "unlocked", "unavailable"];

const body = document.body;
const sessionCard = document.querySelector(".session-card");
const sessionState = document.querySelector("#session-state");
const unlockHeading = document.querySelector("#unlock-heading");
const unlockForm = document.querySelector("#unlock-form");
const apiKeyInput = document.querySelector("#api-key");
const unlockButton = document.querySelector("#unlock-button");
const logoutButton = document.querySelector("#logout-button");
const retrySessionButton = document.querySelector("#retry-session-button");
const globalStatus = document.querySelector("#global-status");
const applicationBase = document.querySelector("#application-base");
const searchNavigationButton = document.querySelector("#nav-search");

const navigation = new Map([
  [document.querySelector("#nav-overview"), document.querySelector("#overview-panel")],
  [searchNavigationButton, document.querySelector("#search-panel")],
  [document.querySelector("#nav-api"), document.querySelector("#api-panel")],
  [document.querySelector("#nav-about"), document.querySelector("#about-panel")],
]);

let activeCredential = null;
let requestGeneration = 0;
let searchController = null;
const activeRequests = new Set();

class ProtectedRequestError extends Error {
  constructor(kind, retryAfter = null) {
    super(kind);
    this.name = "ProtectedRequestError";
    this.kind = kind;
    this.retryAfter = retryAfter;
  }
}

class StaleRequestError extends Error {
  constructor() {
    super("stale-request");
    this.name = "StaleRequestError";
  }
}

function setText(element, value) {
  element.textContent = String(value);
}

function applicationUrl(relativePath) {
  return new URL(`../${relativePath}`, document.baseURI);
}

function readStoredCredential() {
  try {
    return { available: true, value: sessionStorage.getItem(SESSION_STORAGE_KEY) };
  } catch {
    return { available: false, value: null };
  }
}

function storeCredential(credential) {
  try {
    sessionStorage.setItem(SESSION_STORAGE_KEY, credential);
    return true;
  } catch {
    return false;
  }
}

function removeStoredCredential() {
  try {
    sessionStorage.removeItem(SESSION_STORAGE_KEY);
  } catch {
    // The in-memory value is still cleared when browser storage is unavailable.
  }
}

function selectPanel(selectedButton) {
  for (const [button, panel] of navigation) {
    const isSelected = button === selectedButton;
    panel.hidden = !isSelected;
    if (isSelected) {
      button.setAttribute("aria-current", "page");
    } else {
      button.removeAttribute("aria-current");
    }
  }
}

function setSessionState(state, message, hasStoredCredential = false) {
  for (const knownState of SESSION_STATES) {
    body.classList.remove(`state-${knownState}`);
  }
  body.classList.add(`state-${state}`);

  const unlocked = state === "unlocked";
  const checking = state === "checking-session";
  sessionCard.hidden = unlocked;
  logoutButton.hidden = !unlocked;
  retrySessionButton.hidden = state !== "unavailable" || !hasStoredCredential;
  unlockForm.hidden = checking || unlocked || (state === "unavailable" && hasStoredCredential);

  const labels = {
    "checking-session": "Checking session",
    locked: "Locked",
    unlocked: "Ready — unlocked",
    unavailable: "Unavailable",
  };
  setText(sessionState, labels[state]);
  setText(
    unlockHeading,
    checking && hasStoredCredential ? "Restoring protected access" : "Unlock protected features",
  );
  setText(globalStatus, message);
  searchController?.setAccessState(state);
}

function invalidateProtectedRequests() {
  requestGeneration += 1;
  for (const controller of activeRequests) {
    controller.abort();
  }
  activeRequests.clear();
}

function clearCredentialState() {
  removeStoredCredential();
  activeCredential = null;
  apiKeyInput.value = "";
  apiKeyInput.removeAttribute("aria-invalid");
}

function setApiKeyInvalid(invalid) {
  if (invalid) {
    apiKeyInput.setAttribute("aria-invalid", "true");
  } else {
    apiKeyInput.removeAttribute("aria-invalid");
  }
}

function parseRetryAfter(response) {
  const value = response.headers.get("Retry-After");
  if (value === null || !/^[1-9]\d*$/.test(value)) {
    return null;
  }
  const seconds = Number(value);
  return Number.isSafeInteger(seconds) ? seconds : null;
}

async function authenticatedFetch(relativePath, options = {}) {
  const {
    credential: pendingCredential = null,
    signal: callerSignal = null,
    ...fetchOptions
  } = options;
  const storedCredential = readStoredCredential();
  const credential = pendingCredential ?? (
    storedCredential.available && storedCredential.value === activeCredential
      ? storedCredential.value
      : null
  );
  if (!credential) {
    throw new ProtectedRequestError("authentication-required");
  }

  const headers = new Headers(fetchOptions.headers || {});
  headers.set("Authorization", `Bearer ${credential}`);
  const controller = new AbortController();
  const abortFromCaller = () => controller.abort();
  if (callerSignal?.aborted) {
    controller.abort();
  } else {
    callerSignal?.addEventListener("abort", abortFromCaller, { once: true });
  }
  const generation = requestGeneration;
  activeRequests.add(controller);

  let response;
  try {
    response = await fetch(applicationUrl(relativePath), {
      ...fetchOptions,
      headers,
      signal: controller.signal,
    });
  } catch (error) {
    if (generation !== requestGeneration || error.name === "AbortError") {
      throw new StaleRequestError();
    }
    throw new ProtectedRequestError("network");
  } finally {
    callerSignal?.removeEventListener("abort", abortFromCaller);
    activeRequests.delete(controller);
  }

  if (generation !== requestGeneration) {
    throw new StaleRequestError();
  }
  if (response.ok) {
    return response;
  }
  if (response.status === 401) {
    invalidateProtectedRequests();
    clearCredentialState();
    throw new ProtectedRequestError("authentication-required");
  }
  if (response.status === 429) {
    throw new ProtectedRequestError("rate-limited", parseRetryAfter(response));
  }
  if (response.status === 503) {
    throw new ProtectedRequestError("service-unavailable");
  }
  if (response.status === 400 || response.status === 422) {
    throw new ProtectedRequestError("request-rejected");
  }
  if (response.status >= 500) {
    throw new ProtectedRequestError("server-error");
  }
  throw new ProtectedRequestError("unexpected-response");
}

function messageForRequestError(error) {
  if (error.kind === "authentication-required") {
    return "Authentication required";
  }
  if (error.kind === "rate-limited") {
    return error.retryAfter === null
      ? "Rate limit reached. Retry later."
      : `Rate limit reached. Retry in ${error.retryAfter} seconds.`;
  }
  if (error.kind === "service-unavailable") {
    return "VaultBridge is temporarily unavailable. Try again.";
  }
  if (error.kind === "network") {
    return "Unable to connect to VaultBridge. Check the connection and try again.";
  }
  if (error.kind === "request-rejected") {
    return "VaultBridge rejected the request.";
  }
  if (error.kind === "server-error") {
    return "VaultBridge encountered a server error. Try again later.";
  }
  return "VaultBridge returned an unexpected response.";
}

async function validateCredential(credential, restoreSession, focusAfterSuccess) {
  try {
    const response = await authenticatedFetch("api/v1/notes/list?limit=1", { credential });
    if (response.body !== null) {
      await response.body.cancel();
    }

    if (!restoreSession && !storeCredential(credential)) {
      activeCredential = null;
      setSessionState(
        "unavailable",
        "Browser session storage is unavailable. The credential was not retained.",
      );
      return;
    }

    activeCredential = credential;
    apiKeyInput.value = "";
    setApiKeyInvalid(false);
    setSessionState("unlocked", "Authentication successful. Protected requests are available.");
    if (focusAfterSuccess) {
      logoutButton.focus();
    }
  } catch (error) {
    if (error instanceof StaleRequestError) {
      return;
    }
    activeCredential = null;
    apiKeyInput.value = "";
    const retainedCredential = restoreSession && error.kind !== "authentication-required";
    if (!retainedCredential) {
      removeStoredCredential();
    }
    const nextState = retainedCredential || error.kind !== "authentication-required"
      ? "unavailable"
      : "locked";
    setApiKeyInvalid(error.kind === "authentication-required" && !retainedCredential);
    setSessionState(nextState, messageForRequestError(error), retainedCredential);
    if (!retainedCredential) {
      apiKeyInput.focus();
    }
  } finally {
    unlockButton.disabled = false;
    retrySessionButton.disabled = false;
  }
}

function logout() {
  invalidateProtectedRequests();
  clearCredentialState();
  setSessionState("locked", "Logged out. Enter an API key to unlock protected features.");
  apiKeyInput.focus();
}

for (const [button] of navigation) {
  button.addEventListener("click", () => selectPanel(button));
}

unlockForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const credential = apiKeyInput.value;
  if (!credential) {
    setApiKeyInvalid(true);
    setSessionState("locked", "Enter an API key to continue.");
    apiKeyInput.focus();
    return;
  }
  unlockButton.disabled = true;
  setApiKeyInvalid(false);
  setSessionState("checking-session", "Validating the API key.");
  void validateCredential(credential, false, true);
});

apiKeyInput.addEventListener("input", () => setApiKeyInvalid(false));

retrySessionButton.addEventListener("click", () => {
  const storedCredential = readStoredCredential();
  if (!storedCredential.available || storedCredential.value === null) {
    clearCredentialState();
    setSessionState("locked", "Authentication required");
    return;
  }
  retrySessionButton.disabled = true;
  setSessionState("checking-session", "Revalidating the saved session.", true);
  void validateCredential(storedCredential.value, true, true);
});

logoutButton.addEventListener("click", logout);

setText(applicationBase, applicationUrl("").href);
initializeOverview(applicationUrl);
searchController = initializeSearch({
  authenticatedFetch,
  onAuthenticationRequired: () => {
    setSessionState("locked", "Authentication required");
    apiKeyInput.focus();
  },
});
const initialCredential = readStoredCredential();
if (!initialCredential.available) {
  setSessionState(
    "unavailable",
    "Browser session storage is unavailable. Unlock cannot retain a credential.",
  );
} else if (initialCredential.value === null) {
  setSessionState("locked", "Enter an API key to unlock protected features.");
} else {
  void validateCredential(initialCredential.value, true, false);
}
