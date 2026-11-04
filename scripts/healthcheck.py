#!/usr/bin/env python3
import json
import os
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
host = os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1").split(",")[0].strip().lstrip(".")
request = urllib.request.Request(
    "http://127.0.0.1:8000/healthz", headers={"Host": host if host != "*" else "127.0.0.1"}
)
with opener.open(request, timeout=4) as response:
    if response.status != 200 or json.load(response).get("status") != "ok":
        raise SystemExit(1)
