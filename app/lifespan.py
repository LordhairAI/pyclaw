
import os
from dotenv import load_dotenv
from fastapi import FastAPI
from app.workflow import get_app_graph, initialize_async_components, close_async_components
from contextlib import asynccontextmanager
from urllib.parse import quote_plus
from psycopg_pool import AsyncConnectionPool
from langgraph.store.postgres import AsyncPostgresStore  
import logging
import asyncio
logger = logging.getLogger("uvicorn.error")
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 初始化数据库连接池
    password = quote_plus(os.getenv('PG_CONFIG_PASSWORD'))
    db_url = f"postgresql://{os.getenv('PG_CONFIG_USERNAME')}:{password}@{os.getenv('PG_CONFIG_HOST')}:{os.getenv('PG_CONFIG_PORT')}/{os.getenv('PG_CONFIG_DATABASE')}"
    async def connection_health_check(conn):
        try:
            await conn.execute("SELECT 1")
        except Exception:
            await conn.rollback()
            raise

    pool = AsyncConnectionPool(
        conninfo=db_url,
        min_size=10,
        max_size=60,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=False,
        max_idle=300,  
        timeout=30,
        reconnect_timeout=5,
        check=connection_health_check
    )
    try:
        await pool.open()
        app.state.store = AsyncPostgresStore(pool)
        await initialize_async_components(pool)
        app.state.app_graph = await get_app_graph(pool)
        yield
    finally:
        logger.info("应用关闭")
        await close_async_components()
        await asyncio.sleep(0.1)
        await pool.close()
