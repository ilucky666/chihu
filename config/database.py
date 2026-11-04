"""Database settings shared by development and managed production databases."""

import os
from urllib.parse import parse_qs, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured


def from_url(url):
    parsed = urlparse(url)
    common = {
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "localhost",
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
    query = parse_qs(parsed.query)
    if parsed.scheme == "mysql":
        options = {
            "charset": "utf8mb4",
            "isolation_level": "read committed",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION'",
        }
        ca = os.getenv("MYSQL_SSL_CA", "")
        if ca:
            options["ssl"] = {"ca": ca, "check_hostname": True}
        return {
            **common,
            "ENGINE": "django.db.backends.mysql",
            "PORT": parsed.port or 3306,
            "OPTIONS": options,
            "TEST": {"CHARSET": "utf8mb4", "COLLATION": "utf8mb4_bin"},
        }
    if parsed.scheme in ("postgres", "postgresql"):
        options = {}
        for name in ("sslmode", "sslrootcert"):
            if name in query:
                options[name] = query[name][-1]
        return {
            **common,
            "ENGINE": "django.db.backends.postgresql",
            "PORT": parsed.port or 5432,
            "OPTIONS": options,
        }
    raise ImproperlyConfigured("DATABASE_URL must use mysql:// or postgresql://")
