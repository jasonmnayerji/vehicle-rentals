from __future__ import annotations

import logging
import os
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from accounts.models import User

logger = logging.getLogger(__name__)

USERNAME_VARIABLE = "DEV_SUPERUSER_USERNAME"
PASSWORD_VARIABLE = "DEV_SUPERUSER_PASSWORD"  # noqa: S105  the variable's name, not a password


class Command(BaseCommand):
    help = (
        "Create a superuser for local testing, from DEV_SUPERUSER_USERNAME and "
        "DEV_SUPERUSER_PASSWORD. Refuses to run unless DJANGO_ENV=dev. Safe to run repeatedly."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--if-dev",
            action="store_true",
            help="Outside a dev environment, do nothing and succeed instead of failing. "
            "For start-up scripts that run the same steps everywhere.",
        )

    def handle(self, *args: Any, if_dev: bool = False, **options: Any) -> None:
        # The gate comes first and reads nothing else. A well-known account with every
        # permission must be impossible to create by accident anywhere that matters, and
        # an unset DJANGO_ENV means production.
        if settings.ENVIRONMENT != "dev":
            if if_dev:
                self.stdout.write(f"skipped  DJANGO_ENV is {settings.ENVIRONMENT!r}, not 'dev'")
                return
            raise CommandError(
                f"refusing to create a test superuser: DJANGO_ENV is "
                f"{settings.ENVIRONMENT!r}, not 'dev'"
            )

        username = os.environ.get(USERNAME_VARIABLE, "").strip() or "admin"
        # No default and never an argument: a password on the command line ends up in shell
        # history and process listings, and a default would be the same on every machine.
        password = os.environ.get(PASSWORD_VARIABLE, "")
        if not password:
            raise CommandError(f"environment variable {PASSWORD_VARIABLE} is required")

        existing = User.objects.filter(username=username).first()
        if existing is None:
            user = User.objects.create_superuser(username=username, password=password)
            logger.info("dev superuser created", extra={"user_id": user.pk})
            self.stdout.write(f"created  superuser {username!r}")
        elif existing.is_superuser:
            # Never overwrites: a password changed through the admin survives a restart.
            self.stdout.write(f"exists   superuser {username!r}")
        else:
            # Sign-up is open, so the name may belong to a customer. Promoting that account
            # would hand it every permission along with a password its owner did not choose.
            raise CommandError(
                f"user {username!r} already exists and is not a superuser; "
                f"choose another {USERNAME_VARIABLE}"
            )
