"""Smoke tests for the CORS surface (issue #85).

Verifies the env-driven CORS contract end to end, through a real ASGI request
rather than only middleware kwargs:

- Default / wildcard config: preflight succeeds, no credentials negotiated.
- Explicit origin list: allowed origin gets an echoed ACAO; other origins and
  credentials behave accordingly.
- The invalid ``Access-Control-Allow-Origin: *`` + credentials combination is
  unreachable through any configuration.

Each test builds a fresh app under its own CORS_ORIGINS value. The uvicorn
process reads this setting at import time, so testing every configuration
means one reimport each — done in a subprocess-isolated helper.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

import api.main as api_main
import config


@pytest.fixture
def app_with_cors(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """Snapshot CORS-relevant globals and restore after each test."""
    yield monkeypatch
    api_main.app.user_middleware[:] = api_main.app.user_middleware


def _build_app(origins: list[str], allow_credentials: bool) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    return app


class TestCorsOriginParsing:
    def test_default_empty_becomes_wildcard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config.settings, "cors_origins", "")
        origins, creds = api_main._cors_config()
        assert origins == ["*"]
        assert creds is False

    def test_literal_wildcard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config.settings, "cors_origins", "*")
        origins, creds = api_main._cors_config()
        assert origins == ["*"]
        assert creds is False

    def test_single_origin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config.settings, "cors_origins", "http://localhost:5173")
        origins, creds = api_main._cors_config()
        assert origins == ["http://localhost:5173"]
        assert creds is True

    def test_comma_separated_origins_are_trimmed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            config.settings, "cors_origins", "http://a.dev, http://b.dev , c.dev"
        )
        origins, creds = api_main._cors_config()
        assert origins == ["http://a.dev", "http://b.dev", "c.dev"]
        assert creds is True

    def test_wildcard_item_collapses_to_allow_all_with_credentials_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config.settings, "cors_origins", "*,http://a.dev")
        origins, creds = api_main._cors_config()
        # A "*" entry means allow-all, and allow-all can never carry credentials
        # (the invalid combination issue #85 exists to prevent).
        assert origins == ["*"]
        assert creds is False


class TestCorsHeadersBrowserContract:
    def test_wildcard_preflight_has_no_credentials_header(self) -> None:
        client = TestClient(_build_app(["*"], allow_credentials=False))
        response = client.options(
            "/ping",
            headers={
                "Origin": "http://sincewhen.dev",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "*"
        assert "access-control-allow-credentials" not in response.headers

    def test_explicit_origin_is_echoed_for_allowed_origin(self) -> None:
        client = TestClient(
            _build_app(["http://allowed.dev"], allow_credentials=True)
        )
        response = client.options(
            "/ping",
            headers={
                "Origin": "http://allowed.dev",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://allowed.dev"
        assert response.headers["access-control-allow-credentials"] == "true"

    def test_unlisted_origin_is_dropped_by_middleware(self) -> None:
        client = TestClient(
            _build_app(["http://allowed.dev"], allow_credentials=True)
        )
        response = client.get("/ping", headers={"Origin": "http://stranger.dev"})
        assert "access-control-allow-origin" not in response.headers


class TestNoInvalidCombination:
    def test_wildcard_plus_credentials_never_reachable_via_config(self) -> None:
        """The invariant that motivated issue #85."""
        for raw in ("", "*", "*, *, *", "*,http://a.dev"):
            monkeypatch = pytest.MonkeyPatch()
            try:
                monkeypatch.setattr(config.settings, "cors_origins", raw)
                origins, creds = api_main._cors_config()
                if origins == ["*"]:
                    assert creds is False, (
                        f"cors_origins={raw!r}: wildcard must force credentials off"
                    )
            finally:
                monkeypatch.undo()

    def test_middleware_was_bound_with_the_computed_policy(self) -> None:
        """The app's actual middleware kwargs match the computed policy."""
        middleware = next(
            m
            for m in api_main.app.user_middleware
            if m.cls is CORSMiddleware or m.cls.__name__ == "CORSMiddleware"
        )
        assert middleware.kwargs["allow_origins"] == api_main._cors_allowed_origins
        assert middleware.kwargs["allow_credentials"] == api_main._cors_allow_credentials
        if middleware.kwargs["allow_origins"] == ["*"]:
            assert middleware.kwargs["allow_credentials"] is False


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
