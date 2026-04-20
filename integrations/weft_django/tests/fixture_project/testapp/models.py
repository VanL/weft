from __future__ import annotations

from django.db import models


class EventRecord(models.Model):
    key = models.CharField(max_length=64)
    value = models.CharField(max_length=255)
