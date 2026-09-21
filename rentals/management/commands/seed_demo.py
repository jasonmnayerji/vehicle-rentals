from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from rentals.models import CarType, CarTypeName

DEMO_FLEET = {CarTypeName.SEDAN: 3, CarTypeName.SUV: 2, CarTypeName.VAN: 1}


class Command(BaseCommand):
    help = "Create the demo car types if they do not exist. Safe to run repeatedly."

    def handle(self, *args: Any, **options: Any) -> None:
        for name, fleet_size in DEMO_FLEET.items():
            # Never overwrites: a fleet size edited in the admin survives a restart.
            _, created = CarType.objects.get_or_create(
                name=name, defaults={"fleet_size": fleet_size}
            )
            self.stdout.write(f"{'created' if created else 'exists '}  {name.label}")
