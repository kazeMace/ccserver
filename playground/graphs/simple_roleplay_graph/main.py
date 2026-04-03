"""
main — 启动入口。

    python playground/graphs/simple_roleplay_graph/main.py
"""

import os
import sys

# 项目根目录加入 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import uvicorn
from ccserver.log import setup_logging

setup_logging(stderr=True)

# server 和 pipeline 在同目录，直接加入 path 后导入
sys.path.insert(0, os.path.dirname(__file__))

from server import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
