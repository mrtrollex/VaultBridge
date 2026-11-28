"""Disposable production TLS app, pre-header barriers and local CONNECT mapping.

The proxy resolves test hostnames locally for all three native browser engines;
it is test transport, never an authorization substitute or deployment validator.
"""

import asyncio
import errno
import json
import os
import select
import shutil
import socket
import socketserver
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core.config import Settings
from app.main import create_app
from app.services.knowledge_spaces import Capability, Dialect

KEY = "named-browser-synthetic-key"
COOKIE = "vaultbridge_ui_session"
INTENT = "vaultbridge.named-ui-session.intent.v1"
JOURNAL = "vaultbridge.named-ui-session.journal.v1"


class Barrier:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.method = None
        self.headers = None
        self.failure = None
        self.path = "/ui/session"
        self.stage = "headers"
        self.truncate = False
        self.finished = threading.Event()
        self.repeat = False
        self.captured_headers = []

    def arm(self, method, failure=None, path="/ui/session", *, stage="headers", truncate=False, repeat=False):
        self.entered.clear()
        self.release.clear()
        self.method = method
        self.failure = failure
        self.headers = None
        self.path = path
        self.stage = stage
        self.truncate = truncate
        self.finished.clear()
        self.repeat = repeat
        self.captured_headers.clear()

    async def __call__(self, app, scope, receive, send):
        held = (
            self.method is not None
            and scope["path"].endswith(self.path)
            and scope["method"] == self.method
            and (not self.repeat or not self.release.is_set())
        )
        failure = self.failure if held else None
        if held and not self.repeat:
            self.method = None

        async def intercept(message):
            if held and message["type"] == "http.response.start":
                self.headers = dict(message["headers"])
                self.captured_headers.append(self.headers)
            stopping = held and (
                (self.stage == "headers" and message["type"] == "http.response.start")
                or (self.stage == "body" and message["type"] == "http.response.body")
            )
            if stopping:
                self.entered.set()
                assert await asyncio.to_thread(self.release.wait, 20), "Test did not release response headers"
                if self.truncate and message["type"] == "http.response.body":
                    await send({**message, "body": message["body"][:1], "more_body": True})
                    raise RuntimeError("Synthetic truncated HTTP response")
            await send(message)

        try:
            if failure is not None:
                from starlette.responses import JSONResponse

                await JSONResponse(
                    {"detail": "test_failure"}, status_code=failure, headers={"Cache-Control": "no-store"}
                )(scope, receive, intercept)
            else:
                await app(scope, receive, intercept)
        finally:
            if held:
                self.finished.set()


@dataclass
class _Connection:
    number: int
    generation: int
    client: socket.socket
    wake: tuple[socket.socket, socket.socket]
    peer: socket.socket | None = None


class _Tunnel(socketserver.StreamRequestHandler):
    # Never buffer TLS bytes sent immediately after CONNECT headers: bytes
    # prefetched by rfile would be invisible to the socket forwarding loop.
    rbufsize = 0

    def readline(self, connection):
        return self.server.readline(connection)

    def handle(self):
        try:
            self.forward()
        except (OSError, ValueError, UnicodeError):
            # Disconnect interrupts pending parsing/connect/IO. The server's
            # shutdown_request still owns resource release and acknowledgement.
            return

    def forward(self):
        proxy = self.server
        with proxy.condition:
            connection = proxy.connections[self.connection]
            if not proxy.live(connection):
                return
            proxy.record("parsing", connection=connection.number)
        first = self.readline(connection).decode("ascii").strip().split()
        if len(first) != 3:
            return
        connect = first[0] == "CONNECT"
        target = urlsplit(first[1]) if not connect else None
        host, port = first[1].rsplit(":", 1) if connect else (target.hostname, target.port or 80)
        if host not in {"vaultbridge.example.test", "other.example.test", "foreign.invalid.test"}:
            return
        headers = []
        while (line := self.readline(connection)) not in {b"\r\n", b"\n"}:
            if not line or len(line) > 65536:
                return
            if line.split(b":", 1)[0].lower() not in {b"connection", b"proxy-connection"}:
                headers.append(line)
        if not proxy.connect(connection, int(port)):
            return
        peer = connection.peer
        self.connection.setblocking(False)
        if connect:
            if not proxy.send(connection, self.connection, b"HTTP/1.1 200 Connection established\r\n\r\n"):
                return
        else:
            assert target.scheme == "http"
            path = target.path or "/"
            if target.query:
                path += "?" + target.query
            if not proxy.send(connection, peer,
                              f"{first[0]} {path} {first[2]}\r\n".encode() + b"".join(headers)
                              + b"Connection: close\r\n\r\n"):
                return
        while True:
            ready, _, _ = select.select([self.connection, peer, connection.wake[0]], [], [], 20)
            if not ready or connection.wake[0] in ready:
                return
            for source in ready:
                try:
                    data = source.recv(65536)
                    destination = peer if source is self.connection else self.connection
                    if not data or not proxy.send(connection, destination, data):
                        return
                except BlockingIOError:
                    continue


class _Proxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, *args):
        self.condition = threading.Condition(threading.RLock())
        self.connections = {}
        self.events = []
        self.generation = 0
        self.number = 0
        self.failed = False
        super().__init__(*args)
        # accept and registration share the failure lock without blocking it.
        self.socket.setblocking(False)

    def record(self, event, **facts):
        with self.condition:
            self.events.append(dict(sequence=len(self.events), ns=time.monotonic_ns(),
                                    generation=self.generation, event=event, **facts))
            self.condition.notify_all()

    def get_request(self):
        with self.condition:
            client, address = super().get_request()
            self.number += 1
            if self.failed:
                self.record("rejected", connection=self.number)
                client.close()
                raise OSError("Qualification proxy disconnected")
            client.setblocking(False)
            try:
                wake = socket.socketpair()
            except OSError:
                client.close()
                raise
            for endpoint in wake:
                endpoint.setblocking(False)
            self.connections[client] = _Connection(self.number, self.generation, client, wake)
            self.record("accepted", connection=self.number)
            return client, address

    def live(self, connection):
        return (not self.failed and connection.generation == self.generation
                and self.connections.get(connection.client) is connection)

    def readline(self, connection):
        # Read exactly through LF, never consuming prefetched TLS bytes. Native
        # wakeup interrupts a pending CONNECT on Windows as well as POSIX.
        line = bytearray()
        while len(line) <= 65536:
            ready, _, _ = select.select([connection.client, connection.wake[0]], [], [], 5)
            if not ready or connection.wake[0] in ready:
                return b""
            try:
                byte = connection.client.recv(1)
            except BlockingIOError:
                continue
            if not byte:
                return b""
            line.extend(byte)
            if byte == b"\n":
                return bytes(line)
        return b""

    def connect(self, connection, port):
        with self.condition:
            if not self.live(connection):
                return False
            peer = connection.peer = socket.socket()
            peer.setblocking(False)
            error = peer.connect_ex(("127.0.0.1", port))
        if error not in {0, errno.EISCONN}:
            if error not in {errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, 10035, 10036}:
                return False
            ready, writable, _ = select.select([connection.wake[0]], [peer], [peer], 5)
            if ready or not writable or peer.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR):
                return False
        with self.condition:
            if not self.live(connection):
                return False
            self.record("connected", connection=connection.number)
            return True

    def send(self, connection, destination, data):
        remaining = memoryview(data)
        while remaining:
            with self.condition:
                if not self.live(connection):
                    return False
                try:
                    sent = destination.send(remaining)
                    if not sent:
                        return False
                    self.record("forwarded", connection=connection.number, bytes=sent,
                                direction="browser" if destination is connection.client else "server")
                    remaining = remaining[sent:]
                    continue
                except BlockingIOError:
                    pass
            # Never hold the failure lock while waiting for a writable socket.
            ready, writable, _ = select.select([connection.wake[0]], [destination], [], 20)
            if ready or not writable:
                return False
        return True

    def shutdown_request(self, request):
        with self.condition:
            connection = self.connections.get(request)
            super().shutdown_request(request)
            if connection is not None:
                for endpoint in (connection.peer, *connection.wake):
                    if endpoint is not None:
                        endpoint.close()
                del self.connections[request]
                self.record("closed", connection=connection.number)

    def disconnect(self):
        """Fail this generation, interrupt all IO, and acknowledge actual release.

        Registration and every nonblocking send share this lock: after the
        failure event there can be no successful forwarding, including retries.
        Wake sockets interrupt parsing, connect and forwarding waits on both OSes.
        Only worker cleanup acknowledges disposal, never invocation of disconnect.
        """
        with self.condition:
            self.failed = True
            self.record("disconnect-begin", outstanding=len(self.connections))
            for connection in self.connections.values():
                connection.wake[1].send(b"x")
                for endpoint in (connection.client, connection.peer):
                    if endpoint is not None:
                        try:
                            endpoint.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
            assert self.condition.wait_for(lambda: not self.connections, timeout=5), "Proxy teardown not acknowledged"
            self.record("disconnect-ack", outstanding=0)
            return dict(generation=self.generation, sequence=len(self.events) - 1, outstanding=0)

    def recover(self):
        # Explicit test-only network recovery starts a NEW generation. No socket
        # from the terminal failed generation survives or becomes live again.
        with self.condition:
            assert self.failed and not self.connections
            self.generation += 1
            self.failed = False
            self.record("recovered")


def certificate(root):
    executable = shutil.which("openssl")
    if executable is None and os.name == "nt":
        executable = str(Path(os.environ["ProgramFiles"]) / "Git/usr/bin/openssl.exe")
    cert, key = root / "cert.pem", root / "key.pem"
    result = subprocess.run(
        [
            executable,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=vaultbridge.example.test",
            "-addext",
            "subjectAltName=DNS:vaultbridge.example.test,DNS:other.example.test,IP:127.0.0.1",
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, "Disposable TLS certificate generation unavailable"
    return cert, key


@contextmanager
def serve(app, cert=None, key=None):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    ready = threading.Event()

    class ReadyServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                ready.set()

    server = ReadyServer(
        uvicorn.Config(
            app,
            access_log=False,
            log_level="error",
            lifespan="on",
            ssl_certfile=str(cert) if cert else None,
            ssl_keyfile=str(key) if key else None,
            proxy_headers=False,
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        assert ready.wait(10), "Disposable server startup did not complete"
        yield port
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        assert not thread.is_alive(), "Disposable TLS server did not stop"


@contextmanager
def named_server(root, prefix, *, denied=(), plain=False, termination=False, trust_proxy=True, prepare=None):
    records = []
    for sid in ("default", "research"):
        vault = root / sid
        vault.mkdir()
        records.append(
            dict(
                space_id=sid,
                root=str(vault),
                semantic_data_path=str(root / (sid + "-data")),
                read="deny" if sid in denied else "allow",
                write="deny",
                indexing="disabled",
                dialects=[d.value for d in Dialect],
                capabilities=[c.value for c in Capability],
            )
        )
    if prepare is not None:
        prepare(records)
    settings = Settings(
        API_KEY=KEY,
        VAULT_PATH=records[0]["root"],
        SEMANTIC_DATA_PATH=records[0]["semantic_data_path"],
        KNOWLEDGE_SPACES_JSON=json.dumps(dict(version=1, spaces=records)),
        RATE_LIMIT_ENABLED=False,
        SEMANTIC_WATCH_ENABLED=False,
    )
    app = create_app(settings=settings)
    barrier = Barrier()
    inventory = []
    deliveries = []
    observations = []

    async def wrapper(scope, receive, send):
        if scope["type"] == "http":
            proxy.record("http-request", method=scope["method"], path=scope["path"])
            inventory.append((scope["method"], scope["path"]))
            deliveries.append((scope["path"], dict(scope["headers"]).get(b"cookie", b"")))
            observations.append(
                {
                    "path": scope["path"],
                    "query": scope["query_string"],
                    "method": scope["method"],
                    "headers": dict(scope["headers"]),
                    "scheme": scope["scheme"],
                }
            )
            if scope["path"].startswith(prefix + "/"):
                scope = {**scope, "root_path": prefix}
        async def observed_send(message):
            await send(message)
            if message["type"] == "http.response.start":
                proxy.record("http-response-start", method=scope["method"], path=scope["path"],
                             status=message["status"])
            elif message["type"] == "http.response.body":
                proxy.record("http-response-body", method=scope["method"], path=scope["path"],
                             bytes=len(message.get("body", b"")), more=message.get("more_body", False))

        await barrier(app, scope, receive, observed_send)

    cert, key = certificate(root)
    proxy = _Proxy(("127.0.0.1", 0), _Tunnel)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    try:

        def result(port):
            return dict(
                url=f"{'http' if plain else 'https'}://vaultbridge.example.test:{port}{prefix}",
                origin=f"{'http' if plain else 'https'}://vaultbridge.example.test:{port}",
                app=app,
                barrier=barrier,
                proxy={"server": f"http://127.0.0.1:{proxy.server_address[1]}"},
                inventory=inventory,
                deliveries=deliveries,
                observations=observations,
                proxy_server=proxy,
                cert=cert,
                key=key,
            )

        if termination:
            # Real TLS edge -> real plaintext backend, with an explicit trusted
            # direct-peer profile. Never trust client-supplied forwarded values.
            import httpx
            from starlette.requests import Request

            backend = ProxyHeadersMiddleware(wrapper, trusted_hosts=["127.0.0.1"] if trust_proxy else ["192.0.2.10"])
            with serve(backend) as backend_port:

                async def edge(scope, receive, send):
                    if scope["type"] == "lifespan":
                        while True:
                            event = await receive()
                            await send({"type": event["type"] + ".complete"})
                            if event["type"] == "lifespan.shutdown":
                                return
                    else:
                        body = await Request(scope, receive).body()
                        headers = [
                            (k, v)
                            for k, v in scope["headers"]
                            if k.lower()
                            not in {
                                b"x-forwarded-proto",
                                b"x-forwarded-for",
                                b"x-forwarded-host",
                                b"forwarded",
                                b"connection",
                                b"transfer-encoding",
                            }
                        ]
                        headers.append((b"x-forwarded-proto", b"https"))
                        target = f"http://127.0.0.1:{backend_port}" + scope["path"]
                        if scope["query_string"]:
                            target += "?" + scope["query_string"].decode("ascii")
                        async with httpx.AsyncClient(trust_env=False) as upstream:
                            response = await upstream.request(scope["method"], target, content=body, headers=headers)
                        response_headers = [
                            (k, v)
                            for k, v in response.headers.raw
                            if k.lower() not in {b"connection", b"transfer-encoding"}
                        ]
                        await send(
                            {"type": "http.response.start", "status": response.status_code, "headers": response_headers}
                        )
                        await send({"type": "http.response.body", "body": response.content})

                with serve(edge, cert, key) as port:
                    yield result(port)
        else:
            with serve(wrapper, None if plain else cert, None if plain else key) as port:
                yield result(port)
    finally:
        barrier.release.set()
        proxy.disconnect()
        proxy.shutdown()
        proxy.server_close()
        thread.join(5)
        assert not thread.is_alive(), "Proxy listener did not stop"
        evidence = Path(".agent/two-findings/proxy-events")
        evidence.mkdir(parents=True, exist_ok=True)
        target = evidence / (uuid.uuid4().hex + ".json")
        target.write_text(json.dumps(dict(nodeid=getattr(proxy, "label", None), events=proxy.events), indent=2))
        print("Native qualification proxy events:", target.as_posix())
