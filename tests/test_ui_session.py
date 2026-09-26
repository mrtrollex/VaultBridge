from __future__ import annotations

from http.cookies import SimpleCookie

import pytest
from fastapi.testclient import TestClient

from app.core.ui_session import (
    UI_REQUEST_HEADER,
    UI_SESSION_COOKIE_NAME,
    UI_SESSION_LIFETIME_SECONDS,
    create_ui_session_token,
)
from tests.test_api import client_for
from tests.test_request_observability import capture_application_logs


def session_cookie(response) -> str:
    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    return cookies[UI_SESSION_COOKIE_NAME].value


def assert_single_no_store(response) -> None:
    assert response.headers.get_list("cache-control") == ["no-store"]


@pytest.mark.parametrize("method", ["PUT", "OPTIONS"])
def test_unsupported_session_methods_are_non_cacheable(tmp_path, method):
    client = client_for(tmp_path, api_key="synthetic-current-key")

    response = client.request(method, "/ui/session")

    assert response.status_code == 405
    assert response.headers["allow"]
    assert_single_no_store(response)


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_trailing_slash_session_redirects_are_non_cacheable(tmp_path, method):
    client = client_for(tmp_path, api_key="synthetic-current-key")

    response = client.request(method, "/ui/session/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "http://testserver/ui/session"
    assert_single_no_store(response)


def test_unrelated_routing_response_does_not_gain_session_cache_policy(tmp_path):
    client = client_for(tmp_path, api_key="synthetic-current-key")

    response = client.put("/health")

    assert response.status_code == 405
    assert response.headers.get_list("cache-control") == []


@pytest.mark.parametrize("api_key", ["synthetic-current-key", "synthetic-previous-key"])
def test_current_and_previous_api_keys_create_current_key_session(tmp_path, api_key):
    client = client_for(
        tmp_path,
        api_key="synthetic-current-key",
        previous_api_key="synthetic-previous-key",
    )

    response = client.post("/ui/session", json={"api_key": api_key})

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}
    assert_single_no_store(response)
    assert session_cookie(response)


def test_invalid_unlock_is_private_non_cacheable_and_does_not_set_cookie(tmp_path):
    client = client_for(tmp_path, api_key="synthetic-current-key")
    credential = "synthetic-invalid-private-key"

    with capture_application_logs() as stream:
        response = client.post("/ui/session", json={"api_key": credential})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid API key"}
    assert_single_no_store(response)
    assert "set-cookie" not in response.headers
    assert credential not in response.text
    assert credential not in stream.getvalue()


@pytest.mark.parametrize(
    "payload",
    [
        {"api_key": ["synthetic-private-array-secret"]},
        {"api_key": {"nested": "synthetic-private-object-secret"}},
        {"api_key": None},
        {"unexpected": "synthetic-private-extra-secret"},
    ],
)
def test_invalid_session_request_shapes_are_generic_private_and_non_cacheable(tmp_path, payload):
    client = client_for(tmp_path, api_key="synthetic-current-key")
    serialized_payload = str(payload)

    with capture_application_logs() as stream:
        response = client.post("/ui/session", json=payload)

    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid session request"}
    assert_single_no_store(response)
    for private_value in (
        "synthetic-private-array-secret",
        "synthetic-private-object-secret",
        "synthetic-private-extra-secret",
    ):
        assert private_value not in response.text
        assert private_value not in stream.getvalue()
    assert serialized_payload not in response.text


def test_malformed_session_json_is_generic_private_and_non_cacheable(tmp_path):
    client = client_for(tmp_path, api_key="synthetic-current-key")
    credential = "synthetic-private-malformed-secret"

    with capture_application_logs() as stream:
        response = client.post(
            "/ui/session",
            content=f'{{"api_key":"{credential}"',
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid session request"}
    assert_single_no_store(response)
    assert credential not in response.text
    assert credential not in stream.getvalue()


def test_session_cookie_has_bounded_security_attributes_and_https_secure_flag(tmp_path):
    http_client = client_for(tmp_path / "http", api_key="synthetic-current-key")
    http_response = http_client.post(
        "/ui/session",
        json={"api_key": "synthetic-current-key"},
    )
    http_cookie = http_response.headers["set-cookie"]

    https_client = TestClient(
        client_for(tmp_path / "https", api_key="synthetic-current-key").app,
        base_url="https://testserver",
    )
    https_response = https_client.post(
        "/ui/session",
        json={"api_key": "synthetic-current-key"},
    )
    https_cookie = https_response.headers["set-cookie"]

    for cookie in (http_cookie, https_cookie):
        assert "HttpOnly" in cookie
        assert "SameSite=strict" in cookie
        assert "Path=/" in cookie
        assert f"Max-Age={UI_SESSION_LIFETIME_SECONDS}" in cookie
        assert "expires=" in cookie.lower()
        assert "Domain=" not in cookie
        assert "synthetic-current-key" not in cookie
    assert "Secure" not in http_cookie
    assert "Secure" in https_cookie


def test_session_survives_new_app_instance_with_same_key_and_rotation_invalidates_it(tmp_path):
    original = client_for(tmp_path / "original", api_key="synthetic-stable-key")
    token = session_cookie(
        original.post("/ui/session", json={"api_key": "synthetic-stable-key"})
    )
    cookie_header = {"Cookie": f"{UI_SESSION_COOKIE_NAME}={token}"}

    restarted = client_for(tmp_path / "restarted", api_key="synthetic-stable-key")
    restored = restarted.get("/ui/session", headers=cookie_header)
    rotated = client_for(
        tmp_path / "rotated",
        api_key="synthetic-rotated-key",
        previous_api_key="synthetic-stable-key",
    )

    assert restored.status_code == 200
    assert restored.json() == {"authenticated": True}
    assert_single_no_store(restored)
    assert session_cookie(restored)
    assert rotated.get("/ui/session", headers=cookie_header).status_code == 401
    fresh = rotated.post("/ui/session", json={"api_key": "synthetic-stable-key"})
    assert fresh.status_code == 200
    assert session_cookie(fresh) != token


@pytest.mark.parametrize(
    "token",
    [
        "",
        "malformed",
        "v2.100.604900.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "v1.not-a-time.604900.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "x" * 161,
    ],
)
def test_malformed_and_unsupported_session_tokens_are_rejected(tmp_path, token):
    client = client_for(tmp_path, api_key="synthetic-current-key")

    response = client.get(
        "/ui/session",
        headers={"Cookie": f"{UI_SESSION_COOKIE_NAME}={token}"},
    )

    assert response.status_code == 401
    assert_single_no_store(response)
    if token:
        assert token not in response.text


def test_tampered_and_expired_session_tokens_are_rejected(tmp_path):
    client = client_for(tmp_path, api_key="synthetic-current-key")
    settings = client.app.state.settings
    valid = create_ui_session_token(settings, now=1_000_000)
    tampered = f"{valid[:-1]}{'A' if valid[-1] != 'A' else 'B'}"
    expired = create_ui_session_token(settings, now=1)

    for token in (tampered, expired):
        response = client.get(
            "/ui/session",
            headers={"Cookie": f"{UI_SESSION_COOKIE_NAME}={token}"},
        )
        assert response.status_code == 401


def test_session_restore_slides_token_lifetime(tmp_path, monkeypatch):
    client = client_for(tmp_path, api_key="synthetic-current-key")
    monkeypatch.setattr("app.core.ui_session.time.time", lambda: 1_000_000)
    original = session_cookie(
        client.post("/ui/session", json={"api_key": "synthetic-current-key"})
    )

    monkeypatch.setattr("app.core.ui_session.time.time", lambda: 1_000_100)
    renewed_response = client.get(
        "/ui/session",
        headers={"Cookie": f"{UI_SESSION_COOKIE_NAME}={original}"},
    )
    renewed = session_cookie(renewed_response)

    assert renewed_response.status_code == 200
    assert renewed != original
    assert renewed.split(".")[1:3] == ["1000100", str(1_000_100 + UI_SESSION_LIFETIME_SECONDS)]


def test_logout_is_idempotent_and_clears_session_cookie(tmp_path):
    client = client_for(tmp_path, api_key="synthetic-current-key")
    client.post("/ui/session", json={"api_key": "synthetic-current-key"})

    first = client.delete("/ui/session")
    second = client.delete("/ui/session")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"authenticated": False}
    assert_single_no_store(first)
    assert_single_no_store(second)
    assert f"{UI_SESSION_COOKIE_NAME}=" in first.headers["set-cookie"]
    assert "Max-Age=0" in first.headers["set-cookie"]
    assert client.get("/ui/session").status_code == 401


def test_ui_cookie_requires_marker_and_marker_requires_valid_cookie(tmp_path):
    client = client_for(tmp_path, api_key="synthetic-current-key")
    client.post("/ui/session", json={"api_key": "synthetic-current-key"})

    cookie_only = client.get("/api/v1/notes/list")
    authorized = client.get(
        "/api/v1/notes/list",
        headers={UI_REQUEST_HEADER: "1"},
    )
    marker_only = client_for(tmp_path / "marker", api_key="synthetic-current-key").get(
        "/api/v1/notes/list",
        headers={UI_REQUEST_HEADER: "1"},
    )

    assert cookie_only.status_code == 401
    assert authorized.status_code == 200
    assert marker_only.status_code == 401


def test_unlock_and_protected_requests_share_peer_rate_limit(tmp_path):
    unlock_client = client_for(
        tmp_path / "unlock",
        api_key="synthetic-current-key",
        rate_limit_requests=1,
    )
    assert unlock_client.post("/ui/session", json={"api_key": "invalid"}).status_code == 401
    private_value = "synthetic-rate-limited-private-key"
    with capture_application_logs() as stream:
        rate_limited = unlock_client.post("/ui/session", json={"api_key": private_value})
    assert rate_limited.status_code == 429
    assert rate_limited.json() == {"detail": "Rate limit exceeded"}
    assert_single_no_store(rate_limited)
    assert private_value not in rate_limited.text
    assert private_value not in stream.getvalue()

    protected_client = client_for(
        tmp_path / "protected",
        api_key="synthetic-current-key",
        rate_limit_requests=2,
    )
    assert protected_client.post(
        "/ui/session",
        json={"api_key": "synthetic-current-key"},
    ).status_code == 200
    assert protected_client.get(
        "/api/v1/notes/list",
        headers={UI_REQUEST_HEADER: "1"},
    ).status_code == 200
    assert protected_client.get(
        "/api/v1/notes/list",
        headers={UI_REQUEST_HEADER: "1"},
    ).status_code == 429


def test_unrelated_api_validation_keeps_default_fastapi_diagnostics(tmp_path):
    client = client_for(tmp_path, api_key="synthetic-current-key")

    response = client.post(
        "/notes",
        headers={"Authorization": "Bearer synthetic-current-key"},
        json={"content": ""},
    )

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert response.headers.get("cache-control") != "no-store"
