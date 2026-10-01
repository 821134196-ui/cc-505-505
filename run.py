#!/usr/bin/env python3
"""一条命令启动座椅养护系统。

用法：
    python run.py                # 初始化种子数据（含重复报损案例）并启动
    python run.py --reset        # 清空后重新播种
    python run.py --no-seed      # 保留现有数据直接启动
    python run.py --port 8080
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import webbrowser
from pathlib import Path

from benchcare.seed import seed
from benchcare.server import make_server
from benchcare.service import BenchService

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "benchcare.db"
PHOTO_DIR = DATA_DIR / "photos"


def already_seeded(svc: BenchService) -> bool:
    try:
        return svc.conn.execute("SELECT COUNT(*) c FROM benches").fetchone()["c"] > 0
    except sqlite3.Error:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="公共座椅养护管理系统")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reset", action="store_true", help="清空数据库并重新播种")
    ap.add_argument("--no-seed", action="store_true", help="不播种，保留现有数据")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args(argv)

    DATA_DIR.mkdir(exist_ok=True)
    svc = BenchService(DB_PATH, photo_dir=PHOTO_DIR)

    if args.reset:
        print("→ 清空并重新播种演示数据…")
        info = seed(svc, reset=True)
    elif args.no_seed:
        print("→ 使用现有数据启动…")
        info = None
    else:
        if already_seeded(svc):
            print("→ 检测到已有数据，直接启动（如需重置：python run.py --reset）")
            info = None
        else:
            print("→ 首次启动，播种演示数据（含重复报损案例）…")
            info = seed(svc, reset=False)

    if info:
        for e in info["events"]:
            print(f"  · {e['bench']}：{e['title']}（事件 #{e['event_id']}）")

    httpd = make_server(svc, args.host, args.port)
    url = f"http://{args.host}:{args.port}/"
    print(f"\n✓ 座椅养护系统已启动：{url}")
    print("  演示账号（下拉切换）：市民 / 维保一班 / 维保二班 / 复验员老周")
    print("  Ctrl+C 停止\n")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
