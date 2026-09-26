import { initializeOverview } from "./overview.js";
import { initializeSearch } from "./search.js";

const SESSION_STATES = ["checking-session", "locked", "unlocked", "unavailable"];

const body = document.body;
const sessionCard = document.querySelector(".session-card");
const sessionState = document.querySelector("#session-state");
const unlockHeading = document.querySelector("#unlock-heading");
const unlockForm = document.querySelector("#unlock-form");
const apiKeyInput = document.querySelector("#api-key");
const unlockButton = document.querySelector("#unlock-button");
const logoutButton = document.querySelector("#logout-button");
const apiLogoutButton = document.querySelector("#api-logout-button");
const authenticatedSession = document.querySelector("#authenticated-session");
const retrySessionButton = document.querySelector("#retry-session-button");
const globalStatus = document.querySelector("#global-status");
const applicationBase = document.querySelector("#application-base");
const searchNavigationButton = document.querySelector("#nav-search");
const apiNavigationButton = document.querySelector("#nav-api");

const navigation = new Map([
  [document.querySelector("#nav-overview"), document.querySelector("#overview-panel")],
  [searchNavigationButton, document.querySelector("#search-panel")],
  [document.querySelector("#nav-api"), document.querySelector("#api-panel")],
  [document.querySelector("#nav-about"), document.querySelector("#about-panel")],
]);

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

function selectPanel(selectedButton) {
  if (selectedButton !== searchNavigationButton) {
    searchController?.deactivate();
  }
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

function showApiPanel(focusApiKey = false) {
  selectPanel(apiNavigationButton);
  if (focusApiKey && !unlockForm.hidden) {
    apiKeyInput.focus();
  } else if (focusApiKey) {
    apiNavigationButton.focus();
  }
}

function setSessionState(state, message, hasStoredCredential = false) {
  for (const knownState of SESSION_STATES) {
    body.classList.remove(`state-${knownState}`);
  }
  body.classList.add(`state-${state}`);

  const unlocked = state === "unlocked";
  const checking = state === "checking-session";
  sessionCard.dataset.sessionState = state;
  logoutButton.hidden = !unlocked;
  authenticatedSession.hidden = !unlocked;
  retrySessionButton.hidden = state !== "unavailable" || !hasStoredCredential;
  unlockForm.hidden = checking || unlocked || (state === "unavailable" && hasStoredCredential);

  const labels = {
    "checking-session": "CHECKING SESSION",
    locked: "LOCKED",
    unlocked: "UNLOCKED",
    unavailable: "UNAVAILABLE",
  };
  setText(sessionState, labels[state]);
  setText(
    unlockHeading,
    unlocked
      ? "Protected access unlocked"
      : checking && hasStoredCredential
        ? "Restoring protected access"
        : "Unlock protected features",
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

function clearCredentialInput() {
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
  const { signal: callerSignal = null, ...fetchOptions } = options;

  const headers = new Headers(fetchOptions.headers || {});
  headers.set("X-VaultBridge-UI-Request", "1");
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
      credentials: "same-origin",
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
    await clearServerSession();
    throw new ProtectedRequestError("authentication-required");
  }
  if (response.status === 429) {
    throw new ProtectedRequestError("rate-limited", parseRetryAfter(response));
  }
  if (response.status === 503) {
    throw new ProtectedRequestError("service-unavailable");
  }
  if (response.status === 404) {
    throw new ProtectedRequestError("not-found");
  }
  if (response.status === 400 || response.status === 422) {
    throw new ProtectedRequestError("request-rejected");
  }
  if (response.status >= 500) {
    throw new ProtectedRequestError("server-error");
  }
  throw new ProtectedRequestError("unexpected-response");
}

async function sessionRequest(method, credential = null) {
  const headers = new Headers({ Accept: "application/json" });
  const options = { method, credentials: "same-origin", headers };
  if (credential !== null) {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify({ api_key: credential });
  }

  let response;
  try {
    response = await fetch(applicationUrl("ui/session"), options);
  } catch {
    throw new ProtectedRequestError("network");
  }
  if (response.ok) {
    return response;
  }
  if (response.status === 401) {
    throw new ProtectedRequestError("authentication-required");
  }
  if (response.status === 429) {
    throw new ProtectedRequestError("rate-limited", parseRetryAfter(response));
  }
  if (response.status === 503) {
    throw new ProtectedRequestError("service-unavailable");
  }
  if (response.status >= 500) {
    throw new ProtectedRequestError("server-error");
  }
  throw new ProtectedRequestError("unexpected-response");
}

async function clearServerSession() {
  try {
    await sessionRequest("DELETE");
  } catch {
    // Authentication is still cleared from the current page state.
  }
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

async function unlockSession(credential, focusAfterSuccess) {
  try {
    const response = await sessionRequest("POST", credential);
    if (response.body !== null) {
      await response.body.cancel();
    }
    setApiKeyInvalid(false);
    setSessionState("unlocked", "Authentication successful. Protected requests are available.");
    if (focusAfterSuccess) {
      logoutButton.focus();
    }
  } catch (error) {
    if (error instanceof StaleRequestError) {
      return;
    }
    const authenticationFailed = error.kind === "authentication-required";
    setApiKeyInvalid(authenticationFailed);
    setSessionState(authenticationFailed ? "locked" : "unavailable", messageForRequestError(error));
    if (focusAfterSuccess) {
      showApiPanel(true);
    }
  } finally {
    clearCredentialInput();
    unlockButton.disabled = false;
  }
}

async function restoreSession(focusAfterSuccess = false) {
  try {
    const response = await sessionRequest("GET");
    if (response.body !== null) {
      await response.body.cancel();
    }
    setSessionState("unlocked", "Saved dashboard session restored.");
    if (focusAfterSuccess) {
      logoutButton.focus();
    }
  } catch (error) {
    const authenticationFailed = error.kind === "authentication-required";
    setSessionState(
      authenticationFailed ? "locked" : "unavailable",
      authenticationFailed
        ? "Enter an API key to unlock protected features."
        : messageForRequestError(error),
      !authenticationFailed,
    );
    if (authenticationFailed && focusAfterSuccess) {
      showApiPanel(true);
    }
  } finally {
    retrySessionButton.disabled = false;
  }
}

async function logout() {
  invalidateProtectedRequests();
  clearCredentialInput();
  await clearServerSession();
  setSessionState("locked", "Logged out. Enter an API key to unlock protected access.");
  showApiPanel(true);
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
  void unlockSession(credential, true);
});

apiKeyInput.addEventListener("input", () => setApiKeyInvalid(false));

retrySessionButton.addEventListener("click", () => {
  retrySessionButton.disabled = true;
  setSessionState("checking-session", "Revalidating the saved session.", true);
  void restoreSession(true);
});

logoutButton.addEventListener("click", () => void logout());
apiLogoutButton.addEventListener("click", () => void logout());

setText(applicationBase, applicationUrl("").href);
initializeOverview(applicationUrl);
searchController = initializeSearch({
  authenticatedFetch,
  navigateToApi: () => showApiPanel(true),
  onAuthenticationRequired: () => {
    setSessionState("locked", "Authentication required");
    showApiPanel(true);
  },
});
void restoreSession();
