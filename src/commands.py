"""User-facing command parsing for the Feishu accounting bot."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

BIND_PREFIXES = ("绑定账本", "绑定表格", "绑定多维表格", "bind")
STATUS_COMMANDS = {"账本状态", "绑定状态", "状态", "status"}
HELP_COMMANDS = {"帮助", "help", "/help"}


@dataclass(frozen=True)
class BitableLink:
    app_token: str
    table_id: str
    view_url: str
    wiki_token: str = ""


@dataclass(frozen=True)
class BotCommand:
    name: str
    argument: str = ""


def parse_command(text: str) -> BotCommand | None:
    normalized = " ".join(text.strip().split())
    if not normalized:
        return None
    lowered = normalized.lower()
    for prefix in BIND_PREFIXES:
        if lowered == prefix.lower():
            return BotCommand(name="bind", argument="")
        if lowered.startswith(prefix.lower() + " "):
            return BotCommand(name="bind", argument=normalized[len(prefix) :].strip())
    if normalized in STATUS_COMMANDS or lowered in STATUS_COMMANDS:
        return BotCommand(name="status")
    if normalized in HELP_COMMANDS or lowered in HELP_COMMANDS:
        return BotCommand(name="help")
    return None


def parse_bitable_link(raw_url: str) -> BitableLink:
    """Parse a Feishu base/wiki link and extract tokens."""
    url = raw_url.strip().strip("<>")
    if not url:
        raise ValueError("请在命令后附上飞书多维表格链接。")
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("链接格式不正确，请复制完整的飞书多维表格 URL。")

    table_id = parse_qs(parsed.query).get("table", [""])[0]
    if not table_id:
        raise ValueError("链接里缺少 table=tbl...，请进入具体表格后复制完整链接。")

    base_match = re.search(r"/base/([^/?#]+)", parsed.path)
    if base_match:
        return BitableLink(app_token=base_match.group(1), table_id=table_id, view_url=url)

    wiki_match = re.search(r"/wiki/([^/?#]+)", parsed.path)
    if wiki_match:
        return BitableLink(app_token="", table_id=table_id, view_url=url, wiki_token=wiki_match.group(1))

    raise ValueError("链接中没有找到 /base/<token> 或 /wiki/<token>，请确认这是飞书多维表格链接。")


def build_help_text() -> str:
    return "\n".join(
        [
            "自动记账机器人使用方式：",
            "1. 绑定个人账本：绑定账本 <飞书多维表格链接>",
            "2. 记一笔支出：今天午饭花了28元",
            "3. 记一笔收入：今天工资到账10000元",
            "4. 查看绑定状态：账本状态",
            "",
            "说明：每个飞书用户绑定自己的账本，数据互相隔离；系统会按月份自动写入 YYYY-MM 子表。",
        ]
    )
