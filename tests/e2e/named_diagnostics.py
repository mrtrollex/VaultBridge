"""Test-only, content-free native event evidence; never a readiness substitute."""

import json
import platform
import time
import uuid
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

LIFECYCLE = """
(() => {
if (window.qualificationEvents) return;
window.qualificationEvents = [];
for (const type of ['pageshow', 'pagehide', 'visibilitychange', 'DOMContentLoaded', 'load']) {
  addEventListener(type, event => {
    // Public lifecycle observations. The erasure test registers its authoritative
    // departure observer after application initialization.
    queueMicrotask(() => {
      const spaces = document.querySelector('#spaces');
      const row = {type, persisted: event.persisted ?? null,
        hidden: document.hidden, privateHidden: spaces ? spaces.hidden : null,
        statusEmpty: !document.querySelector('#index-status')?.textContent,
        selectionEmpty: !document.querySelector('#single-space')?.value,
        keyEmpty: !document.querySelector('#api-key')?.value};
      qualificationEvents.push(row);
      console.debug('VB156B_LIFECYCLE ' + JSON.stringify(row));
    });
  });
}
})();
"""


class NativeEvidence:
    def __init__(self, context, nodeid, *, trace=False):
        self.context = context
        self.nodeid = nodeid
        self.events = []
        self.started = time.monotonic()
        self.pages = {}
        self.trace_path = None
        if trace:
            root = Path(".agent/remaining-qualification/native-traces")
            root.mkdir(parents=True, exist_ok=True)
            self.trace_path = root / (uuid.uuid4().hex + ".zip")
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
        context.add_init_script(LIFECYCLE)
        context.on("page", self.page)
        context.on("request", lambda r: self.record("request", method=r.method, path=urlsplit(r.url).path))
        context.on("requestfinished", lambda r: self.record("finished", method=r.method, path=urlsplit(r.url).path))
        context.on("requestfailed", lambda r: self.record("failed", path=urlsplit(r.url).path, failure=r.failure))
        context.on("response", self.response)

    def record(self, event, **data):
        self.events.append(dict(sequence=len(self.events), ms=round((time.monotonic() - self.started) * 1000, 3),
                                event=event, **data))

    def response(self, response):
        headers = response.headers
        self.record("response", path=urlsplit(response.url).path, status=response.status,
                    cache_control=headers.get("cache-control"), content_type=headers.get("content-type"))

    def page(self, page):
        number = len(self.pages)
        self.pages[page] = number
        page.on("console", lambda message: self.record("lifecycle", page=number,
                data=json.loads(message.text.removeprefix("VB156B_LIFECYCLE ")))
                if message.text.startswith("VB156B_LIFECYCLE ") else None)
        page.on("pageerror", lambda error: self.record("pageerror", page=number, error=str(error)))
        page.on("close", lambda: self.record("page-close", page=number))
        page.on("domcontentloaded", lambda: self.record("domcontentloaded", page=number))
        page.on("load", lambda: self.record("load", page=number))

    def snapshot(self, page, label):
        state = page.evaluate("""async () => ({
          ready: document.readyState, message: document.querySelector('#session-message')?.textContent,
          locks: await navigator.locks.query(),
          phases: Object.keys(localStorage).filter(k=>k.startsWith('vaultbridge.named-ui-session.'))
            .map(k=>({key:k, phase:JSON.parse(localStorage.getItem(k)).phase ?? null})),
          events: window.qualificationEvents,
          navigation: performance.getEntriesByType('navigation').map(n=>({type:n.type,
            notRestoredReasons:n.notRestoredReasons?.toJSON?.() ?? null}))
        })""")
        self.record("snapshot", page=self.pages[page], label=label, data=state)
        return state

    def finish_trace(self):
        if self.trace_path is not None:
            self.context.tracing.stop(path=self.trace_path)
            self.record("native-trace-saved", path=self.trace_path.as_posix())
            self.trace_path = None

    def save(self):
        root = Path(".agent/remaining-qualification/native-events")
        root.mkdir(parents=True, exist_ok=True)
        target = root / (uuid.uuid4().hex + ".json")
        target.write_text(json.dumps(dict(nodeid=self.nodeid, browser=self.context.browser.version,
                                         platform=platform.system(), playwright=version("playwright"),
                                         executable=self.context.browser.browser_type.executable_path,
                                         events=self.events), indent=2), encoding="utf-8")
        print("Native qualification events:", target.as_posix())
