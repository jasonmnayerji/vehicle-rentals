"""The kinds of car that can be rented."""

from __future__ import annotations

from enum import Enum


class CarType(Enum):
    """A closed set: the business rents exactly these three kinds of car.

    An enum rather than a class per type. Sedan, SUV and van differ in data (a name, how many
    the fleet has), not in behaviour, so ``Sedan(Car)`` and friends would be three empty
    classes. Inheritance is for behaviour that varies; the day vans need a licence check that
    sedans do not, that rule gets a type of its own.
    """

    label: str

    SEDAN = ("sedan", "Sedan")
    SUV = ("suv", "SUV")
    VAN = ("van", "Van")

    def __new__(cls, value: str, label: str) -> CarType:
        member = object.__new__(cls)
        member._value_ = value  # so CarType("suv") works, and "suv" is what gets stored
        member.label = label
        return member

    def __str__(self) -> str:
        return self.label
