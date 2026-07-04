"""自动记账系统主入口。"""

from __future__ import annotations

import argparse
import sys

from config import get_settings
from db import AccountingDB
from feishu_client import FeishuClient
from feishu_event import run_server
from gpt_parser import GPTBillParser
from monthly_summary import MonthlySummaryService
from monthly_table_manager import MonthlyTableManager
from scheduler import MonthlySummaryScheduler
from service import AccountingService
from user_registry import UserRegistry
from utils import json_dumps


def build_service() -> AccountingService:
    """组装业务服务依赖。"""
    settings = get_settings()
    parser = GPTBillParser(settings)
    feishu_client = FeishuClient(settings)
    db = AccountingDB()
    user_registry = UserRegistry(db, feishu_client)
    monthly_table_manager = MonthlyTableManager(db, feishu_client)
    return AccountingService(
        parser=parser,
        feishu_client=feishu_client,
        db=db,
        user_registry=user_registry,
        monthly_table_manager=monthly_table_manager,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="飞书机器人自动记账系统")
    parser.add_argument("--serve", action="store_true", help="启动飞书事件接收服务")
    parser.add_argument("--text", help="直接解析并写入一条自然语言账单，用于本地测试")
    parser.add_argument("--run-monthly-summary", metavar="YYYY-MM", help="手动生成指定月份月报，用于验证定时任务逻辑")
    parser.add_argument("--send-summary", action="store_true", help="配合 --run-monthly-summary，把月报私聊发送给用户")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        service = build_service()
        settings = get_settings()
        if args.text:
            result = service.handle_text(args.text, source="本地命令行")
            print(json_dumps(result))
            return 0
        if args.run_monthly_summary:
            summary_service = MonthlySummaryService(service.db, service.feishu_client)
            summaries = summary_service.run_for_month(
                args.run_monthly_summary,
                send_message=args.send_summary,
                force=True,
            )
            print(json_dumps({"count": len(summaries), "summaries": [item.text for item in summaries]}))
            return 0
        if args.serve:
            summary_scheduler = MonthlySummaryScheduler(MonthlySummaryService(service.db, service.feishu_client))
            summary_scheduler.start()
            run_server(settings.server_host, settings.server_port, service)
            summary_scheduler.stop()
            return 0
        print("请指定 --serve 启动服务，或使用 --text '今天中午美团点外卖花了38.5' 做本地测试。")
        return 2
    except Exception as exc:  # noqa: BLE001 - 主入口打印清晰错误
        print(f"[ERROR] 程序执行失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
