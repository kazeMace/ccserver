"""Command line entrypoint for Drama Engine Web service."""

from __future__ import annotations

import argparse
import logging

import uvicorn
from dotenv import load_dotenv

# 启动时加载 .env（override=False：shell env 优先，.env 仅作兜底）。
# 这样 AI 玩家用的 CCSERVER_MODEL / ANTHROPIC_BASE_URL / DEEPSEEK_API_KEY
# 既能 shell 临时传参，也能写 .env 持久化。必须在 uvicorn 加载 app 之前执行。
load_dotenv(override=False)


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(description="Drama Engine Web Service")
    parser.add_argument("--host", default="127.0.0.1", help="监听 host，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8766, help="监听端口，默认 8766")
    parser.add_argument("--reload", action="store_true", help="启用 uvicorn reload")
    parser.add_argument("--log-level", default="info", help="日志级别，默认 info")
    return parser


def main() -> None:
    """启动服务。"""
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        "drama_engine.service.server.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
