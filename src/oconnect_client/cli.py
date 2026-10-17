"""安装后的命令行入口；默认操作完全使用独立身份。"""

import argparse
import json
import time
from pathlib import Path

from . import __version__
from .client import ConnectClient
from .identity import create_identity, summary
from .storage import lock_instance
from .sync import sync_recordings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--identity", type=Path, required=True, help="本机独立身份配置")
    parser.add_argument("--host", help="手机当前 Wi-Fi IPv4，init 时不需要")
    parser.add_argument("--port", type=int, default=10152, help="手机 SCP 端口")
    parser.add_argument(
        "--state-dir", type=Path, default=Path.home() / ".oconnect_client" / "state"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="在本机生成全新身份，拒绝覆盖已有配置")
    init.add_argument("--name", default="Python Connect")
    pair = sub.add_parser("pair", help="首次手机确认并保存配对材料")
    pair.add_argument(
        "--hold-seconds", type=float, default=60, help="认证后保持连接秒数，便于确认手机 UI"
    )
    sub.add_parser("list", help="只列出手表录音，输出 JSON")
    sync = sub.add_parser("sync", help="单次增量同步，不删除手机源文件")
    sync.add_argument("--output", type=Path, default=Path.home() / "Recordings" / "OPPO")
    sync.add_argument("--limit", type=int, default=0, help="本轮最多下载条数，0 为不限")
    watch = sub.add_parser("watch", help="持续增量同步，连接失败后自动重试")
    watch.add_argument("--output", type=Path, default=Path.home() / "Recordings" / "OPPO")
    watch.add_argument("--interval", type=float, default=60)
    watch.add_argument("--cycles", type=int, default=0, help="默认 0 表示持续运行")
    args = parser.parse_args(argv)
    if args.command == "init":
        try:
            identity = create_identity(args.identity, args.name)
        except (OSError, ValueError) as error:
            parser.exit(1, f"生成身份失败：{error}\n")
        print(json.dumps(summary(identity), ensure_ascii=False, indent=2))
        return
    if not args.host:
        parser.error("此命令需要 --host 指定手机地址")
    if args.command == "pair" and args.hold_seconds < 0:
        parser.error("--hold-seconds 不能为负数")
    if args.command == "sync" and args.limit < 0:
        parser.error("--limit 不能为负数")
    if args.command == "watch" and (args.interval < 5 or args.cycles < 0):
        parser.error("--interval 至少为 5 秒，--cycles 不能为负数")
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.state_dir / "sync.lock").open("a+b") as lock:
        try:
            lock_instance(lock)
        except BlockingIOError:
            parser.exit(2, "已有同状态目录的同步任务运行\n")
        cycles = 0
        while True:
            failed = False
            try:
                with ConnectClient(
                    args.host,
                    args.port,
                    timeout=90 if args.command == "pair" else 12,
                    identity_path=args.identity,
                    pairing=args.command == "pair",
                ) as client:
                    if args.command == "pair":
                        print("单一反向控制与文件通道已认证；请确认手机显示连接成功。", flush=True)
                        time.sleep(args.hold_seconds)
                        return
                    rows = client.list_watch()
                    if args.command == "list":
                        print(
                            json.dumps(
                                {"count": len(rows), "recordings": rows},
                                ensure_ascii=False,
                                indent=2,
                            )
                        )
                        return
                    sync_recordings(
                        client, rows, args.output, args.state_dir, getattr(args, "limit", 0)
                    )
            except KeyboardInterrupt:
                print("同步已停止。", flush=True)
                return
            except Exception as error:
                failed = True
                if args.command != "watch":
                    parser.exit(1, f"同步未完成：{type(error).__name__}: {error}\n")
                print(
                    f"本轮未完成：{type(error).__name__}: {error}；{args.interval:g} 秒后重新连接。",
                    flush=True,
                )
            cycles += 1
            if args.command != "watch" or (args.cycles and cycles >= args.cycles):
                if failed:
                    parser.exit(1)
                return
            try:
                time.sleep(args.interval)
            except KeyboardInterrupt:
                print("同步已停止。", flush=True)
                return
