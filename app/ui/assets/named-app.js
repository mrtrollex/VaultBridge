import {NamedSession} from "./named-session.js";
import {ScopedClient} from "./scoped-client.js";
import {Spaces} from "./spaces.js";

const base = new URL("../", document.baseURI);
const form = document.querySelector("#unlock-form"), key = document.querySelector("#api-key");
const logout = document.querySelector("#logout"), retry = document.querySelector("#retry-session");
const messages = {
  unsupported: "Named browser sessions unavailable. A secure top-level context, native Web Locks and working shared localStorage are required.",
  uncertain: "Session outcome uncertain; browser access blocked. Operator recovery is required; reload cannot reset this state.",
  logout_pending: "Page locked; session removal not confirmed. Retry logout explicitly.",
  logged_out: "Browser session removal confirmed. Copied tokens remain valid until expiry or current-key rotation.",
  locked: "Page locked. Enter the existing API key to unlock.", checking: "Checking browser session…", pending: "Session change pending…",
  authenticated: "Browser session verified. Selection stays in memory only.", rejected: "Unlock rejected. Check the API key.",
  unavailable: "Session unverified or unavailable. Retry explicitly.", rate_limited: "Rate limited. Wait before retrying explicitly.",
};
let spaces;
const session = new NamedSession(new URL("ui/session", base), state => {
  key.value = "";
  document.querySelector("#session-message").textContent = (messages[state] || messages.locked) +
    (state === "rate_limited" && session.retryAfter !== null ? ` Retry after ${session.retryAfter} seconds.` : "");
  form.hidden = ["authenticated", "pending", "checking", "unsupported", "uncertain", "logout_pending"].includes(state);
  logout.hidden = !["authenticated", "logout_pending", "pending", "checking", "locked",
    "rejected", "unavailable", "rate_limited"].includes(state);
  logout.textContent = state === "logout_pending" ? "Retry logout" : "Log out";
  retry.hidden = !["unavailable", "rate_limited"].includes(state);
  if (state === "authenticated") void spaces?.load();
  else spaces?.reset();
});
spaces = new Spaces(new ScopedClient(session, base), session);
form.addEventListener("submit", event => {
  event.preventDefault(); const submitted = key.value; key.value = "";
  void session.login(submitted);
});
logout.addEventListener("click", () => session.logout());
retry.addEventListener("click", () => session.restore());
void session.restore();
