from __future__ import annotations

import logging
import re

import pytest
from django.db import DatabaseError
from django.test import Client

from config.log_format import JsonFormatter
from config.request_id import RequestIdFilter

pytestmark = pytest.mark.django_db


def test_health_check_reports_ok_when_the_database_is_reachable(client: Client) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_check_is_read_only(client: Client) -> None:
    assert client.post("/healthz").status_code == 405


def test_every_response_carries_a_generated_request_id(client: Client) -> None:
    response = client.get("/healthz")

    assert re.fullmatch(r"[0-9a-f]{32}", response["X-Request-ID"])


def test_a_well_formed_upstream_request_id_is_kept_for_end_to_end_tracing(client: Client) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": "gateway-7f3a9c21"})

    assert response["X-Request-ID"] == "gateway-7f3a9c21"


@pytest.mark.parametrize(
    "hostile",
    [
        pytest.param('x" , "level": "ERROR', id="json injection"),
        pytest.param("a" * 500, id="oversized"),
        pytest.param("short", id="too short to be unique"),
    ],
)
def test_a_malformed_upstream_request_id_is_replaced_not_logged(
    client: Client, hostile: str
) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": hostile})

    assert response["X-Request-ID"] != hostile
    assert re.fullmatch(r"[0-9a-f]{32}", response["X-Request-ID"])


def test_each_request_is_logged_with_its_outcome_and_request_id_but_no_query_string(
    client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    request_ids = RequestIdFilter()
    caplog.handler.addFilter(request_ids)  # what the stdout handler does in production
    try:
        with caplog.at_level(logging.INFO, logger="config.access_log"):
            response = client.get("/accounts/login/?next=/secret-looking-token")
    finally:
        caplog.handler.removeFilter(request_ids)

    (record,) = [r for r in caplog.records if r.name == "config.access_log"]
    assert (record.method, record.path, record.status) == ("GET", "/accounts/login/", 200)  # type: ignore[attr-defined]
    assert record.duration_ms >= 0  # type: ignore[attr-defined]
    assert "secret-looking-token" not in JsonFormatter().format(record)
    # Written while the id is still set, so the line joins up with the rest of the request.
    assert record.request_id == response["X-Request-ID"]  # type: ignore[attr-defined]


def test_a_healthy_probe_is_not_logged_but_an_unhealthy_one_is(
    client: Client, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    with caplog.at_level(logging.INFO, logger="config.access_log"):
        client.get("/healthz")
    assert not [r for r in caplog.records if r.name == "config.access_log"]

    def unreachable(*args: object, **kwargs: object) -> None:
        raise DatabaseError("connection refused")

    monkeypatch.setattr("config.health.connection.cursor", unreachable)
    with caplog.at_level(logging.INFO, logger="config.access_log"):
        assert client.get("/healthz").status_code == 503
    (record,) = [r for r in caplog.records if r.name == "config.access_log"]
    assert record.status == 503  # type: ignore[attr-defined]


def test_the_admin_requires_a_login(client: Client) -> None:
    response = client.get("/admin/rentals/reservation/")

    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]
