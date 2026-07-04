"""记账业务编排服务。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from commands import build_help_text, parse_command
from db import AccountingDB, UserBinding, ensure_default_user_binding
from local_queue import append_pending_bill
from models import Bill
from user_registry import FeishuUserIdentity, UserRegistry

if TYPE_CHECKING:
    from feishu_client import FeishuClient
    from gpt_parser import GPTBillParser
    from monthly_table_manager import MonthlyTableManager


class AccountingService:
    def __init__(
        self,
        parser: "GPTBillParser",
        feishu_client: "FeishuClient",
        db: AccountingDB,
        user_registry: UserRegistry,
        monthly_table_manager: "MonthlyTableManager",
    ):
        self.parser = parser
        self.feishu_client = feishu_client
        self.db = db
        self.user_registry = user_registry
        self.monthly_table_manager = monthly_table_manager

    def handle_user_text(
        self,
        text: str,
        *,
        identity: FeishuUserIdentity,
        source: str = "飞书机器人",
        dedupe_id: str = "",
    ) -> dict[str, Any]:
        """Handle one Feishu user message, including bot commands and accounting."""
        command = parse_command(text)
        if command:
            return self.handle_command(command.name, command.argument, identity)

        user = self.user_registry.get_binding(identity)
        if not user:
            return {
                "command": "needs_binding",
                "reply": "你还没有绑定个人账本。请先发送：绑定账本 <飞书多维表格链接>",
            }
        return self.handle_text(text, source=source, dedupe_id=dedupe_id, user=user)

    def handle_command(self, name: str, argument: str, identity: FeishuUserIdentity) -> dict[str, Any]:
        if name == "help":
            return {"command": "help", "reply": build_help_text()}
        if name == "status":
            user = self.user_registry.get_binding(identity)
            if not user:
                return {"command": "status", "reply": "当前还没有绑定账本。请发送：绑定账本 <飞书多维表格链接>"}
            return {
                "command": "status",
                "reply": "\n".join(
                    [
                        "当前账本已绑定。",
                        f"用户：{user.user_name or user.open_id}",
                        f"账本链接：{user.bitable_view_url or '未配置可见链接'}",
                        "说明：后续账单会按月份自动写入 YYYY-MM 子表。",
                    ]
                ),
            }
        if name == "bind":
            user = self.user_registry.bind_from_link(identity, argument)
            return {
                "command": "bind",
                "reply": "\n".join(
                    [
                        "账本绑定成功。",
                        "以后你的账单只会写入你绑定的多维表格，不会和其他用户混在一起。",
                        "系统会按月份自动写入 YYYY-MM 子表。",
                        f"账本链接：{user.bitable_view_url}",
                    ]
                ),
                "user_id": user.id,
            }
        return {"command": name, "reply": "暂不支持这个命令。发送“帮助”查看用法。"}

    def handle_text(
        self,
        text: str,
        source: str = "飞书机器人",
        dedupe_id: str = "",
        user: UserBinding | None = None,
    ) -> dict[str, Any]:
        """解析自然语言账单并写入用户对应的月度 Bitable。"""
        if user is None:
            user = ensure_default_user_binding(self.feishu_client.settings, self.db)
        bill: Bill = self.parser.parse(text, source=source, dedupe_id=dedupe_id)
        monthly_table = self.monthly_table_manager.get_or_create_for_bill_date(user, bill.date)
        bill_fields = bill.to_bitable_fields()
        try:
            created, response = self.feishu_client.save_bill_once(
                bill,
                app_token=user.bitable_app_token,
                table_id=monthly_table.table_id,
            )
            return {
                "created": created,
                "queued": False,
                "dedupe_id": bill.dedupe_id,
                "bill": bill_fields,
                "month_key": monthly_table.month_key,
                "table_id": monthly_table.table_id,
                "table_name": monthly_table.table_name,
                "table_url": user.bitable_view_url,
                "feishu_response": response,
            }
        except Exception as exc:  # noqa: BLE001 - 业务层需要兜底落本地队列
            queue_path = append_pending_bill(
                {
                    **bill_fields,
                    "用户": user.open_id,
                    "月份": monthly_table.month_key,
                    "目标表": monthly_table.table_id,
                },
                str(exc),
            )
            print(f"[ERROR] 写入 Bitable 失败，已保存到本地待重试队列: {queue_path}", flush=True)
            return {
                "created": False,
                "queued": True,
                "queue_path": str(queue_path),
                "dedupe_id": bill.dedupe_id,
                "bill": bill_fields,
                "month_key": monthly_table.month_key,
                "table_id": monthly_table.table_id,
                "table_name": monthly_table.table_name,
                "table_url": user.bitable_view_url,
                "error": str(exc),
            }
