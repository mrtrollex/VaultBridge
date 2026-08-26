from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.services.rate_limiter import FixedWindowRateLimiter
from tests.test_api import RecordingIndexer, auth, client_for


class MutableClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def limiter_for(
    clock: MutableClock,
    *,
    requests: int = 2,
    window_seconds: int = 10,
    max_clients: int = 4,
) -> FixedWindowRateLimiter:
    return FixedWindowRateLimiter(
        requests=requests,
        window_seconds=window_seconds,
        max_clients=max_clients,
        clock=clock,
    )


def test_fixed_window_allows_below_limit_rejects_excess_and_recovers_without_sleep():
    clock = MutableClock()
    limiter = limiter_for(clock)

    assert limiter.check("client-a").allowed is True
    assert limiter.check("client-a").allowed is True

    rejected = limiter.check("client-a")
    assert rejected.allowed is False
    assert rejected.retry_after_seconds == 10

    clock.advance(3.1)
    assert limiter.check("client-a").retry_after_seconds == 7

    clock.advance(6.9)
    assert limiter.check("client-a").allowed is True


def test_different_client_identities_have_separate_allowance():
    clock = MutableClock()
    limiter = limiter_for(clock, requests=1)

    assert limiter.check("client-a").allowed is True
    assert limiter.check("client-a").allowed is False
    assert limiter.check("client-b").allowed is True


def test_client_state_cap_uses_deterministic_least_recently_used_eviction():
    clock = MutableClock()
    limiter = limiter_for(clock, requests=1, max_clients=2)

    assert limiter.check("client-a").allowed is True
    assert limiter.check("client-b").allowed is True
    assert limiter.check("client-a").allowed is False
    assert limiter.check("client-c").allowed is True

    assert limiter.client_count == 2
    assert limiter.check("client-b").allowed is True
    assert limiter.client_count == 2


def test_stale_clients_are_reclaimed_before_capacity_eviction():
    clock = MutableClock()
    limiter = limiter_for(clock, requests=1, window_seconds=5, max_clients=2)

    limiter.check("client-a")
    limiter.check("client-b")
    assert limiter.client_count == 2

    clock.advance(5)
    assert limiter.check("client-c").allowed is True
    assert limiter.client_count == 1
    assert limiter.check("client-a").allowed is True
    assert limiter.client_count == 2


def test_concurrent_checks_are_thread_safe_and_never_exceed_allowance():
    clock = MutableClock()
    limiter = limiter_for(clock, requests=50, max_clients=2)

    with ThreadPoolExecutor(max_workers=16) as executor:
        decisions = list(executor.map(lambda _index: limiter.check("shared-client"), range(200)))

    assert sum(decision.allowed for decision in decisions) == 50
    assert limiter.client_count == 1


def test_http_429_shape_retry_after_and_window_recovery(tmp_path):
    clock = MutableClock()
    limiter = limiter_for(clock)
    client = client_for(
        tmp_path,
        semantic_indexer=RecordingIndexer(),
        rate_limit_requests=2,
        rate_limit_window_seconds=10,
        rate_limiter=limiter,
    )

    assert client.get("/notes/list", headers=auth()).status_code == 200
    assert client.get("/notes/list", headers=auth()).status_code == 200

    rejected = client.get("/notes/list", headers=auth())
    assert rejected.status_code == 429
    assert rejected.json() == {"detail": "Rate limit exceeded"}
    assert rejected.headers["retry-after"] == "10"

    clock.advance(10)
    assert client.get("/notes/list", headers=auth()).status_code == 200


def test_health_and_privacy_routes_are_never_limited(tmp_path):
    client = client_for(
        tmp_path,
        rate_limit_requests=1,
    )

    for path, expected_status in (
        ("/health", 200),
        ("/health/live", 200),
        ("/health/ready", 503),
        ("/privacy", 200),
    ):
        assert client.get(path).status_code == expected_status
        assert client.get(path).status_code == expected_status

    assert client.app.state.rate_limiter.client_count == 0


def test_legacy_and_v1_routes_share_one_peer_allowance(tmp_path):
    client = client_for(
        tmp_path,
        semantic_indexer=RecordingIndexer(),
        rate_limit_requests=1,
    )

    assert client.get("/notes/list", headers=auth()).status_code == 200
    response = client.get("/api/v1/notes/list", headers=auth())

    assert response.status_code == 429
    assert response.json() == {"detail": "Rate limit exceeded"}


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/notes", {"title": "Limited write", "content": "private write body"}),
        ("/notes/search", {"query": "private literal query"}),
        ("/notes/related", {"text": "private semantic query"}),
        ("/notes/duplicates", {"title": "Private duplicate title"}),
    ],
)
def test_write_literal_semantic_and_duplicate_routes_limit_before_auth(tmp_path, path, payload):
    client = client_for(
        tmp_path,
        semantic_indexer=RecordingIndexer(),
        rate_limit_requests=1,
    )
    invalid_headers = {"Authorization": "Bearer private-invalid-key"}

    assert client.post(path, headers=invalid_headers, json=payload).status_code == 401
    response = client.post(path, headers=invalid_headers, json=payload)

    assert response.status_code == 429
    assert response.json() == {"detail": "Rate limit exceeded"}
    assert "private-invalid-key" not in response.text
    assert all(private_value not in response.text for private_value in payload.values())


def test_invalid_auth_is_401_before_allowance_is_exhausted_then_429(tmp_path):
    client = client_for(
        tmp_path,
        semantic_indexer=RecordingIndexer(),
        rate_limit_requests=2,
    )
    headers = {"Authorization": "Bearer private-invalid-key"}

    for _ in range(2):
        response = client.get("/api/v1/notes/list", headers=headers)
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid API key"}

    rejected = client.get("/api/v1/notes/list", headers=headers)
    assert rejected.status_code == 429
    assert rejected.json() == {"detail": "Rate limit exceeded"}
    assert "private-invalid-key" not in rejected.text


def test_current_and_previous_keys_remain_valid_below_limit(tmp_path):
    client = client_for(
        tmp_path,
        previous_api_key="test-previous-secret",
        semantic_indexer=RecordingIndexer(),
        rate_limit_requests=2,
    )

    current = client.get("/notes/list", headers=auth())
    previous = client.get(
        "/api/v1/notes/list",
        headers={"Authorization": "Bearer test-previous-secret"},
    )

    assert current.status_code == 200
    assert previous.status_code == 200


def test_disabled_rate_limiter_never_returns_429_or_allocates_state(tmp_path):
    client = client_for(
        tmp_path,
        semantic_indexer=RecordingIndexer(),
        rate_limit_enabled=False,
        rate_limit_requests=1,
    )

    for _ in range(4):
        assert client.get("/notes/list", headers=auth()).status_code == 200

    assert client.app.state.rate_limiter.client_count == 0


def test_missing_current_api_key_keeps_server_configuration_error(tmp_path):
    client = client_for(
        tmp_path,
        api_key="",
        previous_api_key="test-previous-secret",
        semantic_indexer=RecordingIndexer(),
        rate_limit_requests=1,
    )

    for _ in range(2):
        response = client.get(
            "/notes/list",
            headers={"Authorization": "Bearer test-previous-secret"},
        )
        assert response.status_code == 500
        assert response.json() == {"detail": "Server API_KEY is not configured"}

    assert client.app.state.rate_limiter.client_count == 0


def test_forwarded_headers_do_not_change_peer_identity(tmp_path):
    client = client_for(
        tmp_path,
        semantic_indexer=RecordingIndexer(),
        rate_limit_requests=1,
        peer=("198.51.100.10", 50000),
    )

    first = client.get(
        "/notes/list",
        headers={
            **auth(),
            "X-Forwarded-For": "203.0.113.1",
            "X-Real-IP": "203.0.113.2",
            "Forwarded": "for=203.0.113.3",
        },
    )
    second = client.get(
        "/notes/list",
        headers={
            **auth(),
            "X-Forwarded-For": "192.0.2.1",
            "X-Real-IP": "192.0.2.2",
            "Forwarded": "for=192.0.2.3",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert client.app.state.rate_limiter.client_count == 1


def test_two_asgi_peer_addresses_have_separate_http_allowance(tmp_path):
    first_client = client_for(
        tmp_path,
        semantic_indexer=RecordingIndexer(),
        rate_limit_requests=1,
        peer=("198.51.100.10", 50000),
    )
    second_client = TestClient(
        first_client.app,
        client=("198.51.100.11", 50000),
    )

    assert first_client.get("/notes/list", headers=auth()).status_code == 200
    assert first_client.get("/notes/list", headers=auth()).status_code == 429
    assert second_client.get("/notes/list", headers=auth()).status_code == 200
