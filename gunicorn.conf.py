"""Gunicorn settings. Gunicorn loads this file from the working directory on its own."""

import os

# All interfaces *inside the container*; compose publishes the port to 127.0.0.1 only.
bind = "0.0.0.0:8000"
workers = int(os.environ.get("WEB_CONCURRENCY", "3"))
# The management socket (gunicornc) is unused here. Left on, gunicorn tries to create it
# under the home directory, which fails on the read-only filesystem and logs an error at
# every start. Off is also one less thing listening.
control_socket_disable = True

# No accesslog: gunicorn's is plain text and cannot carry the request id. The application
# writes the access line itself (config.access_log). Gunicorn's own messages (start-up,
# worker timeouts, crashes) go through the same JSON formatter as everything else, so
# stdout is one JSON object per line with no exceptions for a log collector to trip on.
logconfig_dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "config.log_format.JsonFormatter"}},
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json",
        },
    },
    "root": {"handlers": ["stdout"], "level": "INFO"},
    "loggers": {
        "gunicorn.error": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
        "gunicorn.access": {"handlers": [], "level": "INFO", "propagate": False},
    },
}
