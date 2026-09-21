"""Container healthcheck: exit 0 when /healthz answers 200, otherwise 1.

A script rather than curl because the slim base image ships no HTTP client, and installing
one only to probe localhost would add packages to patch for no benefit.
"""

from __future__ import annotations

import http.client
import os
import sys


def allowed_host_header() -> str:
    """A Host header the application will accept.

    The probe connects over loopback, but Django validates the *header* against
    ALLOWED_HOSTS, and a real deploy lists only its public name there. Sending
    "127.0.0.1" would then be answered 400 and the container marked unhealthy for ever.
    """
    for entry in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(","):
        name = entry.strip()
        if name and name != "*":
            return name.removeprefix(".")  # ".example.com" also matches "example.com"
    return "localhost"  # unset or "*": the settings default accepts it


def check(
    host: str = "127.0.0.1",
    port: int = 8000,
    timeout: float = 2.0,
    host_header: str | None = None,
) -> int:
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request(
            "GET", "/healthz", headers={"Host": host_header or allowed_host_header()}
        )
        return 0 if connection.getresponse().status == 200 else 1
    except (OSError, http.client.HTTPException):
        # Docker only reads the exit code; the application logs why it is unhealthy.
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(check())
