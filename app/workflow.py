import os
import logging
import asyncio
from langgraph.store.postgres.aio import AsyncPostgresStore
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg_pool import AsyncConnectionPool
from app.agents.main_agent import create_main_agent
from app.extensions.registry import ExtensionSnapshot, get_extension_registry
from app.cron.cron_manage import shutdown_cron_manager

from urllib.parse import quote_plus
logger = logging.getLogger("uvicorn.error")

store = None
checkpointer = None
app_graph = None
fallback_app_graph = None
pool = None
is_external_pool = False

async def initialize_async_components(external_pool=None):
    global store, checkpointer, app_graph, pool, is_external_pool

    if external_pool:
        pool = external_pool
        is_external_pool = True
        logger.info(f"Using external LangGraph pool: min_size={pool.min_size}, max_size={pool.max_size}")
    else:
        is_external_pool = False
        password = quote_plus(os.getenv('PG_CONFIG_PASSWORD'))
        db_uri = f"postgresql://{os.getenv('PG_CONFIG_USERNAME')}:{password}@{os.getenv('PG_CONFIG_HOST')}:{os.getenv('PG_CONFIG_PORT')}/{os.getenv('PG_CONFIG_DATABASE')}"

        pool = AsyncConnectionPool(
            conninfo=db_uri,
            min_size=2,
            max_size=10,
            kwargs={"autocommit": True},
            open=False
        )
        await pool.open()
        logger.info(f"Created new LangGraph pool: min_size=2, max_size=10")

    # Log pool status for diagnostics
    # logger.info(f"LangGraph Pool stats - Min: {pool.min_size}, Max: {pool.max_size}, Size: {pool.size}, Num queued: {pool.num_queued}")

    store = AsyncPostgresStore(pool)
    # Some internal LangGraph control objects (e.g. Send) are not msgpack-serializable.
    # Enable pickle fallback so checkpoint writes don't fail on those values.
    checkpointer = AsyncPostgresSaver(
        pool,
        serde=JsonPlusSerializer(pickle_fallback=True),
    )
    
    extension_registry = get_extension_registry()
    snapshot = await extension_registry.reload()

    # create_agent already returns a compiled LangGraph.
    # Attach checkpoint/store directly to avoid nested graph checkpoint writes.
    app_graph = create_main_agent(
        checkpointer=checkpointer,
        store=store,
        extension_tools=snapshot.tools,
    )

async def get_app_graph(pool=None):
    global app_graph
    if app_graph is None:
        await initialize_async_components(pool)
    return app_graph


async def get_fallback_app_graph():
    """Return an app graph without checkpointer as a serialization-safe fallback."""
    global fallback_app_graph, store
    if fallback_app_graph is None:
        extension_snapshot = get_extension_registry().get_snapshot()
        fallback_app_graph = create_main_agent(
            checkpointer=None,
            store=store,
            extension_tools=extension_snapshot.tools,
        )
    return fallback_app_graph


async def reload_graph_from_extensions() -> ExtensionSnapshot:
    global app_graph, fallback_app_graph, checkpointer, store
    extension_registry = get_extension_registry()
    snapshot = await extension_registry.reload()
    app_graph = create_main_agent(
        checkpointer=checkpointer,
        store=store,
        extension_tools=snapshot.tools,
    )
    fallback_app_graph = None
    return snapshot

async def close_async_components():
    global store, checkpointer, fallback_app_graph, pool, is_external_pool
    
    if store and hasattr(store, 'aclose'):
        await store.aclose()
    
    if checkpointer and hasattr(checkpointer, 'aclose'):
        await checkpointer.aclose()

    fallback_app_graph = None

    shutdown_cron_manager()
    
    if pool and not is_external_pool:
        try:
            await pool.close()
        except asyncio.CancelledError:
            # Pool workers can be cancelled during interpreter/event-loop teardown.
            logger.debug("Pool close cancelled during shutdown; ignoring.")

    store = None
    checkpointer = None
    pool = None
    is_external_pool = False
