#!/usr/bin/env python3
"""一条命令启动公共座椅管护系统。

用法：
  python3 run.py            初始化演示数据（含重复报损案例）并启动服务
  python3 run.py reset      清空后重新生成演示数据再启动
  python3 run.py --port 9000
"""
import sys

from app.seed import seed
from app.server import run

if __name__ == "__main__":
    args = sys.argv[1:]
    port = 8000
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    seed(reset="reset" in args)
    run(host="127.0.0.1", port=port)
