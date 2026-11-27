"""Кэш Redis по схеме Cache-Aside."""
import json
from datetime import date, datetime
from decimal import Decimal


def _default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Не могу сериализовать {type(obj)}")


class Cache:
    def __init__(self, client, enabled=True):
        self.r = client
        self.enabled = enabled

    async def get(self, key):
        if not self.enabled:
            return None
        try:
            raw = await self.r.get(key)
            return json.loads(raw) if raw is not None else None
        except Exception:
            return None

    async def set(self, key, value, ttl):
        if not self.enabled:
            return
        try:
            await self.r.set(key, json.dumps(value, default=_default), ex=ttl)
        except Exception:
            pass

    async def delete(self, *keys):
        if not self.enabled:
            return
        for key in keys:
            try:
                await self.r.delete(key)
            except Exception:
                pass
