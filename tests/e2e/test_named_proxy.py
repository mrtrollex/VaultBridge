"""Native socket schedules for the qualification proxy, without browser mocks."""

import socket
import socketserver
import threading
from contextlib import contextmanager

from tests.e2e.named_server import _Proxy, _Tunnel

SUCCESS = b'HTTP/1.1 200 OK\r\nContent-Length: 23\r\n\r\n{"authenticated":false}'


@contextmanager
def servers(*, hold_response=False):
    parsing = threading.Event()
    executed = threading.Event()
    release = threading.Event()
    requests = []

    class ObservedReader:
        def __init__(self, reader):
            self.reader = reader

        def readline(self, *args):
            parsing.set()
            return self.reader.readline(*args)

        def __getattr__(self, name):
            return getattr(self.reader, name)

    class PendingTunnel(_Tunnel):
        def setup(self):
            super().setup()
            self.rfile = ObservedReader(self.rfile)

        def readline(self, connection):
            parsing.set()
            return super().readline(connection)

    class Target(socketserver.StreamRequestHandler):
        def handle(self):
            requests.append(self.rfile.readline())
            while self.rfile.readline() not in (b"\r\n", b""):
                pass
            executed.set()
            if hold_response:
                assert release.wait(5)
            try:
                self.wfile.write(SUCCESS)
            except OSError:
                pass

    target = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Target)
    target.daemon_threads = True
    proxy = _Proxy(("127.0.0.1", 0), PendingTunnel)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (target, proxy)]
    for thread in threads:
        thread.start()
    try:
        yield proxy, target.server_address[1], parsing, executed, release, requests
    finally:
        release.set()
        proxy.disconnect()
        for server in (proxy, target):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()


def connect_request(port):
    return f"CONNECT vaultbridge.example.test:{port} HTTP/1.1\r\nHost: vaultbridge.example.test\r\n\r\n".encode()


def read_available(client):
    data = bytearray()
    try:
        while chunk := client.recv(65536):
            data.extend(chunk)
    except (ConnectionError, socket.timeout):
        pass
    return bytes(data)


def test_pending_connect_cannot_escape_disconnect():
    with servers() as (proxy, port, parsing, executed, release, requests):
        with socket.create_connection(proxy.server_address, timeout=5) as client:
            assert parsing.wait(5), "handler must already be blocked in CONNECT parsing"
            proxy.disconnect()
            try:
                client.sendall(connect_request(port))
                if client.recv(4096):
                    client.sendall(b"DELETE /ui/session HTTP/1.1\r\nHost: vaultbridge.example.test\r\n\r\n")
            except ConnectionError:
                pass
            assert b'{"authenticated":false}' not in read_available(client)
            assert not requests, "a pre-disconnect accepted socket executed DELETE after disconnect"


def test_terminal_failure_rejects_retry_and_drains_active_response():
    with servers(hold_response=True) as (proxy, port, parsing, executed, release, requests):
        with socket.create_connection(proxy.server_address, timeout=5) as client:
            client.sendall(connect_request(port))
            assert b"200 Connection established" in client.recv(4096)
            client.sendall(b"DELETE /proxy/ui/session HTTP/1.1\r\nHost: vaultbridge.example.test\r\n\r\n")
            assert executed.wait(5)
            proxy.disconnect()
            release.set()
            assert b'{"authenticated":false}' not in read_available(client)
            with socket.create_connection(proxy.server_address, timeout=5) as retry:
                try:
                    retry.sendall(connect_request(port))
                except ConnectionError:
                    pass
                assert b"200" not in read_available(retry)
            assert len(requests) == 1


def test_disconnect_ack_waits_for_accepted_worker_and_fences_queued_send(monkeypatch):
    accepted = threading.Event()
    dispatch = threading.Event()
    acknowledged = threading.Event()
    sent = []
    original = _Proxy.process_request

    def hold_worker(proxy, request, address):
        accepted.set()
        assert dispatch.wait(5)
        original(proxy, request, address)

    monkeypatch.setattr(_Proxy, "process_request", hold_worker)
    with servers() as (proxy, port, parsing, executed, release, requests):
        with socket.create_connection(proxy.server_address, timeout=5) as client:
            assert accepted.wait(5)
            connection = next(iter(proxy.connections.values()))

            def disconnect():
                proxy.disconnect()
                acknowledged.set()

            stopper = threading.Thread(target=disconnect)
            stopper.start()
            try:
                with proxy.condition:
                    assert proxy.condition.wait_for(lambda: proxy.failed, timeout=5)
                    assert not acknowledged.is_set(), "invocation is not disposal acknowledgement"
                    sender = threading.Thread(
                        target=lambda: sent.append(proxy.send(connection, connection.client, SUCCESS)))
                    sender.start()
                dispatch.set()
                assert acknowledged.wait(5)
                sender.join(5)
                assert not sender.is_alive() and sent == [False]
                proxy.recover()
                assert not proxy.send(connection, connection.client, SUCCESS), "old generation revived"
                assert b"200" not in read_available(client)
            finally:
                dispatch.set()
                stopper.join(5)
                assert not stopper.is_alive()
