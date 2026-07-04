"""通用工具函数。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from typing import Any


SUPPORTED_TYPES = {"收入", "支出"}
SUPPORTED_CATEGORIES = {"餐饮", "交通", "购物", "住宿", "工资", "报销", "其他"}
SUPPORTED_PAYMENTS = {"微信", "支付宝", "银行卡", "现金", "其他"}


def today_context() -> dict[str, str]:
    """返回解析自然语言账单时需要的当前日期上下文。"""
    now = datetime.now()
    return {
        "today": now.date().isoformat(),
        "yesterday": (now.date() - timedelta(days=1)).isoformat(),
        "tomorrow": (now.date() + timedelta(days=1)).isoformat(),
        "now_time": now.strftime("%H:%M:%S"),
    }


def extract_json_object(text: str) -> dict[str, Any]:
    """从大模型输出中提取第一个 JSON 对象。"""
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned.removeprefix("```json").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```").strip()
    if cleaned.endswith("```"):
        cleaned = cleaned.removesuffix("```").strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if not match:
        raise ValueError("模型返回内容中未找到 JSON 对象")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("模型返回 JSON 不是对象")
    return data


def normalize_bill_data(data: dict[str, Any], original_text: str, source: str) -> dict[str, Any]:
    """标准化模型解析结果，避免分类、类型、支付方式越界。"""
    ctx = today_context()
    bill_type = str(data.get("类型") or data.get("type") or "支出").strip()
    if bill_type not in SUPPORTED_TYPES:
        bill_type = "支出"

    category = str(data.get("分类") or data.get("category") or "其他").strip()
    if category not in SUPPORTED_CATEGORIES:
        category = "其他"

    payment_method = str(data.get("支付方式") or data.get("payment_method") or "其他").strip()
    if payment_method not in SUPPORTED_PAYMENTS:
        payment_method = "其他"

    amount = data.get("金额") or data.get("amount") or 0
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        raise ValueError(f"金额无法解析为数字: {amount}")
    if amount <= 0:
        raise ValueError("金额必须大于 0")

    bill_date = str(data.get("日期") or data.get("date") or ctx["today"]).strip()
    bill_time = str(data.get("时间") or data.get("time") or ctx["now_time"]).strip()
    currency = str(data.get("币种") or data.get("currency") or "CNY").strip().upper()
    merchant = str(data.get("商户或对象") or data.get("merchant") or "").strip()
    note = str(data.get("备注") or data.get("note") or "").strip()

    # 兼容 HH:MM，补齐秒；兼容空值。
    if re.fullmatch(r"\d{2}:\d{2}", bill_time):
        bill_time = bill_time + ":00"
    if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", bill_time):
        bill_time = ctx["now_time"]

    # 日期不是 ISO 格式时回退到今天，避免写入脏数据。
    try:
        date.fromisoformat(bill_date)
    except ValueError:
        bill_date = ctx["today"]

    return {
        "日期": bill_date,
        "时间": bill_time,
        "类型": bill_type,
        "金额": amount,
        "币种": currency or "CNY",
        "分类": category,
        "支付方式": payment_method,
        "商户或对象": merchant,
        "备注": note,
        "原始文本": original_text,
        "记录来源": source,
    }


def build_dedupe_id(bill_data: dict[str, Any]) -> str:
    """生成稳定去重 ID。

    第一版按日期、时间、类型、金额、商户/对象、原始文本和来源生成哈希。
    后续可改为消息 ID + 账单字段组合，降低同一秒多笔账的误判概率。
    """
    raw = "|".join(
        str(bill_data.get(key, ""))
        for key in ["日期", "时间", "类型", "金额", "币种", "商户或对象", "原始文本", "记录来源"]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
