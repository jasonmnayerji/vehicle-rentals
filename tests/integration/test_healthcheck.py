"""The script Docker runs to decide whether the container is healthy."""

from __future__ import annotations

import socket

import pytest
from pytest_django.live_server_helper import LiveServer

from healthcheck import allowed_host_header, check


@pytest.mark.django_db(transaction=True)
def test_a_running_server_with_a_reachable_database_is_healthy(live_server: LiveServer) -> None:
    assert check(live_server.thread.host, live_server.thread.port) == 0


@pytest.mark.django_db(transaction=True)
def test_a_deploy_that_allows_only_its_public_name_is_still_healthy(
    live_server: LiveServer, settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.ALLOWED_HOSTS = ["rentals.example.com"]  # type: ignore[attr-defined]
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "rentals.example.com")
    host, port = live_server.thread.host, live_server.thread.port

    assert check(host, port) == 0
    # The address the probe connects to is not an allowed name: Django answers 400.
    assert check(host, port, host_header="127.0.0.1") == 1


@pytest.mark.parametrize(
    ("allowed_hosts", "expected"),
    [
        pytest.param("rentals.example.com,127.0.0.1", "rentals.example.com", id="first name"),
        pytest.param(".example.com", "example.com", id="subdomain wildcard"),
        pytest.param("*", "localhost", id="allow all"),
        pytest.param("", "localhost", id="unset"),
    ],
)
def test_the_probe_names_a_host_the_application_accepts(
    monkeypatch: pytest.MonkeyPatch, allowed_hosts: str, expected: str
) -> None:
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", allowed_hosts)

    assert allowed_host_header() == expected


def test_nothing_listening_is_unhealthy_rather_than_a_crash() -> None:
    with socket.socket() as placeholder:
        placeholder.bind(("127.0.0.1", 0))
        free_port = placeholder.getsockname()[1]

    assert check("127.0.0.1", free_port, timeout=0.5) == 1
