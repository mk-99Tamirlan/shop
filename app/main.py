"""
Мини-магазин: FastAPI + PostgreSQL.
"""
import asyncio
import os
import socket
from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as redis
from cache import Cache
from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

# --- Настройки из переменных окружения ---
# По умолчанию — база на этом компьютере (для запуска без Docker)
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://shop:shop@localhost:5432/shop")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "1") == "1"
TTL_PRODUCT = 60
TTL_LIST = 30
TTL_TOP = 30
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))
# Имитация медленного платёжного шлюза
PAYMENT_DELAY = float(os.getenv("PAYMENT_DELAY_SEC", "0.2"))
INSTANCE = os.getenv("INSTANCE_NAME") or socket.gethostname()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Выполняется при старте приложения: создаём пул соединений
    app.state.db = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=DB_POOL_MAX)
    app.state.redis = redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=1, socket_timeout=1, retry_on_timeout=False)
    app.state.cache = Cache(app.state.redis, enabled=CACHE_ENABLED)
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await app.state.db.close()


app = FastAPI(title="Мини-магазин", lifespan=lifespan)


@app.middleware("http")
async def add_instance_header(request: Request, call_next):
    # К каждому ответу добавляем имя воркера
    response = await call_next(request)
    response.headers["X-Instance"] = INSTANCE
    return response


class OrderIn(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(default=1, gt=0, le=100)


@app.get("/")
async def index():
    return {"service": "mini-shop", "instance": INSTANCE}


@app.get("/health")
async def health():
    await app.state.db.fetchval("SELECT 1")
    await app.state.redis.ping()
    return {"status": "ok", "instance": INSTANCE}


# ВАЖНО: /products/search объявляем РАНЬШЕ /products/{product_id},
# иначе FastAPI сочтёт слово "search" номером товара.
@app.get("/products/search")
async def search_products(q: str = Query(min_length=2, max_length=50),
                          limit: int = Query(20, ge=1, le=100)):
    rows = await app.state.db.fetch(
        "SELECT id, name, price FROM products WHERE name ILIKE $1 LIMIT $2",
        f"%{q}%", limit,
    )
    return [dict(r) for r in rows]


@app.get("/products")
async def list_products(response: Response, category_id: int = Query(1, ge=1),
                        limit: int = Query(20, ge=1, le=100),
                        offset: int = Query(0, ge=0, le=10000)):
    """Товары категории, сначала самые дорогие."""
    key = f"products:cat:{category_id}:{limit}:{offset}"
    cached = await app.state.cache.get(key)
    if cached is not None:
        response.headers["X-Cache"] = "HIT"
        return cached
    rows = await app.state.db.fetch(
        """SELECT id, name, price, stock FROM products
           WHERE category_id = $1
           ORDER BY price DESC
           LIMIT $2 OFFSET $3""",
        category_id, limit, offset,
    )
    data = [dict(r) for r in rows]
    await app.state.cache.set(key, data, TTL_LIST)
    response.headers["X-Cache"] = "MISS"
    return data


@app.get("/products/{product_id}")
async def get_product(product_id: int, response: Response):
    key = f"product:{product_id}"
    cached = await app.state.cache.get(key)
    if cached is not None:
        response.headers["X-Cache"] = "HIT"
        return cached
    # $1 — параметр запроса. Никогда не вставляйте значения в SQL
    # через f-строки — это SQL-инъекция.
    row = await app.state.db.fetchrow(
        """SELECT p.id, p.name, p.description, p.price, p.stock, c.name AS category
           FROM products p JOIN categories c ON c.id = p.category_id
           WHERE p.id = $1""",
        product_id,
    )
    if row is None:
        raise HTTPException(404, "Товар не найден")
    data = dict(row)
    await app.state.cache.set(key, data, TTL_PRODUCT)
    response.headers["X-Cache"] = "MISS"
    return data


@app.get("/stats/top-products")
async def top_products(response: Response):
    """Топ-10 продаваемых товаров за неделю — «тяжёлый» запрос."""
    key = "stats:top-products"
    cached = await app.state.cache.get(key)
    if cached is not None:
        response.headers["X-Cache"] = "HIT"
        return cached
    rows = await app.state.db.fetch(
        """SELECT p.id, p.name, t.sold
           FROM (SELECT product_id, sum(quantity) AS sold
                 FROM orders
                 WHERE created_at > now() - interval '7 days'
                 GROUP BY product_id
                 ORDER BY sold DESC
                 LIMIT 10) t
           JOIN products p ON p.id = t.product_id
           ORDER BY t.sold DESC"""
    )
    data = [dict(r) for r in rows]
    await app.state.cache.set(key, data, TTL_TOP)
    response.headers["X-Cache"] = "MISS"
    return data


@app.post("/orders", status_code=201)
async def create_order(order: OrderIn):
    await asyncio.sleep(PAYMENT_DELAY)  # имитация платёжного шлюза

    async with app.state.db.acquire() as conn:
        async with conn.transaction():
            # FOR UPDATE блокирует строку: два покупателя не купят один товар
            product = await conn.fetchrow(
                "SELECT price, stock FROM products WHERE id = $1 FOR UPDATE",
                order.product_id,
            )
            if product is None:
                raise HTTPException(404, "Товар не найден")
            if product["stock"] < order.quantity:
                raise HTTPException(409, "Недостаточно товара на складе")

            await conn.execute(
                "UPDATE products SET stock = stock - $1 WHERE id = $2",
                order.quantity, order.product_id,
            )
            row = await conn.fetchrow(
                """INSERT INTO orders (product_id, quantity, total, status, processed_at)
                   VALUES ($1, $2, $3, 'processed', now())
                   RETURNING id, product_id, quantity, total, status, created_at""",
                order.product_id, order.quantity, product["price"] * order.quantity,
            )
    await app.state.cache.delete(f"product:{order.product_id}")
    return dict(row)

@app.get("/orders/{order_id}")
async def get_order(order_id: int):
    row = await app.state.db.fetchrow(
        "SELECT id, product_id, quantity, total, status, created_at, processed_at FROM orders WHERE id = $1", order_id)
    if row is None:
        raise HTTPException(404, "Заказ не найден")
    return dict(row)
