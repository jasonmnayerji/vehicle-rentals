"""The test superuser: convenient on a developer's machine, impossible anywhere else."""

from __future__ import annotations

import logging
from collections.abc import Callable
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client

from accounts.models import User

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def dev(settings: object, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.ENVIRONMENT = "dev"  # type: ignore[attr-defined]
    monkeypatch.setenv("DEV_SUPERUSER_USERNAME", "root-for-tests")
    monkeypatch.setenv("DEV_SUPERUSER_PASSWORD", PASSWORD)


@pytest.fixture
def production(settings: object, monkeypatch: pytest.MonkeyPatch) -> None:
    # Set explicitly, never inherited: the suite also runs with a developer's .env, which
    # says dev. Credentials are supplied so that only the environment stands in the way.
    settings.ENVIRONMENT = "production"  # type: ignore[attr-defined]
    monkeypatch.setenv("DEV_SUPERUSER_PASSWORD", PASSWORD)


def run(*args: str) -> str:
    out = StringIO()
    call_command("create_dev_superuser", *args, stdout=out)
    return out.getvalue()


@pytest.mark.usefixtures("dev")
def test_in_dev_it_creates_a_superuser_who_can_sign_in_to_the_admin(client: Client) -> None:
    run()

    user = User.objects.get(username="root-for-tests")
    assert (user.is_superuser, user.is_staff, user.is_active) == (True, True, True)
    assert client.login(username="root-for-tests", password=PASSWORD)
    assert client.get("/admin/").status_code == 200


@pytest.mark.usefixtures("production")
def test_outside_dev_it_refuses_even_when_credentials_are_supplied() -> None:
    with pytest.raises(CommandError, match="refusing"):
        run()

    assert not User.objects.exists()


@pytest.mark.usefixtures("production")
def test_a_start_up_script_can_run_it_everywhere_and_it_does_nothing_outside_dev() -> None:
    assert "skipped" in run("--if-dev")
    assert not User.objects.exists()


@pytest.mark.usefixtures("dev")
def test_running_it_again_changes_nothing_not_even_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run()
    monkeypatch.setenv("DEV_SUPERUSER_PASSWORD", "a-different-password")

    assert "exists" in run()

    assert User.objects.count() == 1
    assert User.objects.get().check_password(PASSWORD)


@pytest.mark.usefixtures("dev")
def test_the_password_has_no_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEV_SUPERUSER_PASSWORD")

    with pytest.raises(CommandError, match="DEV_SUPERUSER_PASSWORD is required"):
        run()

    assert not User.objects.exists()


@pytest.mark.usefixtures("dev")
def test_a_customer_who_took_the_name_first_is_not_promoted(
    make_user: Callable[..., User],
) -> None:
    # Sign-up is open. Promoting the account would give a stranger every permission.
    customer = make_user("root-for-tests")

    with pytest.raises(CommandError, match="not a superuser"):
        run()

    customer.refresh_from_db()
    assert not customer.is_superuser
    assert not customer.is_staff
    assert not customer.has_usable_password()  # and its password was not replaced


@pytest.mark.usefixtures("dev")
def test_the_password_is_never_printed_or_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        output = run()

    assert PASSWORD not in output
    assert all(PASSWORD not in str(vars(record)) for record in caplog.records)
