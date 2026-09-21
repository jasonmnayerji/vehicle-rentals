"""DJANGO_ENV: which kind of deployment this is. It gates dev-only conveniences."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings import _one_of


def environment() -> str:
    return _one_of("DJANGO_ENV", allowed=("dev", "production"), default="production")


def test_an_unset_environment_is_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DJANGO_ENV", raising=False)

    assert environment() == "production"


def test_the_value_is_not_case_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DJANGO_ENV", " DEV ")

    assert environment() == "dev"


def test_a_typo_stops_start_up_instead_of_being_guessed_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DJANGO_ENV", "development")

    with pytest.raises(ImproperlyConfigured, match="must be one of dev, production"):
        environment()
