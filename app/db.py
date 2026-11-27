import itertools
import logging
import os
import time
import asyncpg

WRITE_URL = os.getenv("DATABASE_URL", "postgresql://shop:shop@pg-primary:5432/shop")
READ_URLS = [u.strip() for u in os.getenv("DATABASE_READ_URLS", WRITE_URL).split(",") if u.strip()] or [WRITE_URL]

POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))
STATEMENT_CACHE = int(os.getenv("DB_STATEMENT_CACHE_SIZE", "100"))

log = logging.getLogger(__name__)

NETWORK_ERRORS = (
    OSError,
    TimeoutError,
    asyncpg.PostgresConnectionError,
    asyncpg.CannotConnectNowError,
    asyncpg.AdminShutdownError,
)


class Database:
    async def connect(self):
        self.reads = []
        self.write = await self._pool(WRITE_URL)

        try:
            for url in READ_URLS:
                self.reads.append(await self._pool(url, reader=True))
        except BaseException:
            await self.close()
            raise

        self._rr = itertools.cycle(range(len(self.reads)))
        self._retry_at = [0.0] * len(self.reads)
        self._probing = [False] * len(self.reads)

    async def _pool(self, url, reader=False):
        return await asyncpg.create_pool(
            url,
            min_size=0 if reader else 1,
            max_size=POOL_MAX,
            statement_cache_size=STATEMENT_CACHE,
            timeout=1.5 if reader else 60,
        )

    @property
    def read(self):
        return self

    async def _execute(self, pool, method, *args, **kwargs):
        kwargs.setdefault("timeout", 3)

        async with pool.acquire(timeout=1.5) as conn:
            return await getattr(conn, method)(*args, **kwargs)

    async def _read(self, method, *args, **kwargs):
        count = len(self.reads)
        start = next(self._rr)

        for offset in range(count):
            index = (start + offset) % count

            if self._retry_at[index] > time.monotonic():
                continue

            probe = self._retry_at[index] != 0

            if probe:
                if self._probing[index]:
                    continue
                self._probing[index] = True

            try:
                result = await self._execute(
                    self.reads[index], method, *args, **kwargs
                )

                if probe:
                    self._retry_at[index] = 0
                    log.info("Replica %s recovered", index + 1)

                return result

            except NETWORK_ERRORS as exc:
                self._retry_at[index] = time.monotonic() + 5
                log.warning(
                    "Replica %s unavailable: %s",
                    index + 1, type(exc).__name__
                )

            finally:
                if probe:
                    self._probing[index] = False

        return await self._execute(
            self.write, method, *args, **kwargs
        )

    async def fetch(self, *args, **kwargs):
        return await self._read("fetch", *args, **kwargs)

    async def fetchrow(self, *args, **kwargs):
        return await self._read("fetchrow", *args, **kwargs)

    async def fetchval(self, *args, **kwargs):
        return await self._read("fetchval", *args, **kwargs)

    async def close(self):
        for pool in self.reads:
            await pool.close()
        await self.write.close()