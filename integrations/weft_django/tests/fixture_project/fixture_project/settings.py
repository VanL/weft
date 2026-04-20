from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(
    os.environ.get("WEFT_DJANGO_FIXTURE_BASE_DIR", Path(__file__).resolve().parent)
)
SECRET_KEY = "weft-django-tests"
DEBUG = True
USE_TZ = True
ROOT_URLCONF = "fixture_project.urls"
ALLOWED_HOSTS = ["*"]
MIDDLEWARE: list[str] = []
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "weft_django",
    "testapp",
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ["WEFT_DJANGO_FIXTURE_DB_PATH"],
    }
}
MIGRATION_MODULES = {"testapp": None}
WEFT_DJANGO: dict[str, object] = {
    "CONTEXT": os.environ.get("WEFT_DJANGO_FIXTURE_WEFT_CONTEXT"),
    "AUTHZ": "fixture_project.authz:authorize",
    "AUTODISCOVER_MODULE": "weft_tasks",
    "REQUEST_ID_PROVIDER": "fixture_project.request_id_provider:get_current",
    "DEFAULT_TASK": {
        "runner": "host",
        "timeout": None,
        "memory_mb": 256,
        "cpu_percent": None,
        "stream_output": False,
        "metadata": {},
    },
    "REALTIME": {
        "TRANSPORT": "sse",
    },
}
