"""Private OSS files, always downloaded through the application's permission checks."""

import os
import posixpath
import re
from tempfile import SpooledTemporaryFile
from urllib.parse import urlsplit

import oss2
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.core.files import File
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible
from django.views.decorators.debug import sensitive_variables


@deconstructible
class PrivateOSSStorage(Storage):
    @sensitive_variables("access_key", "secret", "token", "auth")
    def __init__(self):
        endpoint = os.getenv("OSS_ENDPOINT", "")
        bucket = os.getenv("OSS_BUCKET", "")
        access_key = os.getenv("OSS_ACCESS_KEY_ID", "")
        secret = os.getenv("OSS_ACCESS_KEY_SECRET", "")
        token = os.getenv("OSS_SECURITY_TOKEN", "")
        prefix = os.getenv("OSS_PROJECT", "chihu").strip("/")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
            or not all((bucket, access_key, secret))
            or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", prefix)
        ):
            raise ImproperlyConfigured(
                "OSS 需要 HTTPS endpoint、私有 bucket、凭证和有效 OSS_PROJECT"
            )
        self.prefix = prefix + "/"
        auth = oss2.StsAuth(access_key, secret, token) if token else oss2.Auth(access_key, secret)
        self.bucket = oss2.Bucket(auth, endpoint, bucket, connect_timeout=30)

    def _key(self, name):
        name = str(name).replace("\\", "/")
        normalized = posixpath.normpath(name)
        if name.startswith("/") or normalized in (".", "..") or normalized.startswith("../"):
            raise SuspiciousFileOperation("Invalid private object name")
        return self.prefix + normalized

    def _open(self, name, mode="rb"):
        if mode not in ("rb", "r"):
            raise ValueError("OSS files are opened read-only; use save() to write")
        output = SpooledTemporaryFile(max_size=5 * 1024 * 1024)
        try:
            response = self.bucket.get_object(self._key(name))
            try:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            finally:
                response.close()
            output.seek(0)
            return File(output, name=name)
        except Exception:
            output.close()
            raise

    def _save(self, name, content):
        content.seek(0)
        self.bucket.put_object(
            self._key(name),
            content,
            headers={"x-oss-object-acl": "private", "x-oss-forbid-overwrite": "true"},
        )
        return name

    def exists(self, name):
        return self.bucket.object_exists(self._key(name))

    def size(self, name):
        return self.bucket.get_object_meta(self._key(name)).content_length

    def delete(self, name):
        self.bucket.delete_object(self._key(name))

    def url(self, name):
        raise NotImplementedError("Private media requires an authenticated application download")
