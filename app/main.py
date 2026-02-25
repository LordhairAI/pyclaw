import os
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from fastapi.middleware.cors import CORSMiddleware
from urllib.parse import quote_plus
from app.channels.routers import chat, extensions
import uuid
from app.lifespan import lifespan
from dotenv import load_dotenv
import uvicorn
import logging
from app.cron import cron_manage as _cron_bootstrap
load_dotenv()
logger = logging.getLogger("uvicorn.error")

try:
    _cron_bootstrap.start_cron_manager()
    logger.info("Started cron manager")
except Exception as exc:
    logger.error("Failed to start cron manager during app startup: %s", exc)

app = FastAPI(title="AI智能客", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 在生产环境中应该指定具体的域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, prefix="/api/v1", tags=["聊天接口"])
app.include_router(extensions.router, prefix="/api/v1", tags=["扩展管理"])


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        workers=2,  # 减少工作进程数量
        limit_concurrency=50,  # 限制并发连接数
        timeout_keep_alive=30,  # 设置较短的连接保持时间
        backlog=100,  # 增加待处理连接队列
        access_log=False,  # 关闭访问日志
    )
    
# uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 > ./logs/run_main_output.log 2>&1 &
