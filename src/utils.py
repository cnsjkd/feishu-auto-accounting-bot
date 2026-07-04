"""通用工具函数。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any


SUPPORTED_TYPES = {"收入", "支出"}
SUPPORTED_CATEGORIES = {"餐饮", "交通", "购物", "住宿", "工资", "报销", "投资", "烟酒", "娱乐", "医疗", "教育", "其他"}
SUPPORTED_PAYMENTS = {"微信", "支付宝", "银行卡", "现金", "其他"}
BEIJING_TZ = timezone(timedelta(hours=8))


def beijing_now() -> datetime:
    """返回带 UTC+8 时区信息的北京时间。"""
    return datetime.now(BEIJING_TZ)


def beijing_today() -> date:
    """返回北京时间日期。"""
    return beijing_now().date()


def beijing_now_iso() -> str:
    """返回北京时间 ISO 字符串，包含 +08:00 偏移。"""
    return beijing_now().isoformat(timespec="seconds")


def today_context() -> dict[str, str]:
    """返回解析自然语言账单时需要的北京时间日期上下文。"""
    now = beijing_now()
    today = now.date()
    return {
        "timezone": "Asia/Shanghai",
        "today": today.isoformat(),
        "yesterday": (today - timedelta(days=1)).isoformat(),
        "tomorrow": (today + timedelta(days=1)).isoformat(),
        "now_time": now.strftime("%H:%M:%S"),
    }


def _clean_json_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned.removeprefix("```json").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```").strip()
    if cleaned.endswith("```"):
        cleaned = cleaned.removesuffix("```").strip()
    return cleaned


def extract_json_object(text: str) -> dict[str, Any]:
    """从大模型输出中提取第一个 JSON 对象。"""
    data = extract_json_value(text)
    if not isinstance(data, dict):
        raise ValueError("模型返回 JSON 不是对象")
    return data


def extract_json_value(text: str) -> Any:
    """从大模型输出中提取 JSON 对象或数组。"""
    cleaned = _clean_json_text(text)

    try:
        data = json.loads(cleaned)
        if isinstance(data, (dict, list)):
            return data
    except json.JSONDecodeError:
        pass

    for pattern in (r"\{.*\}", r"\[.*\]"):
        match = re.search(pattern, cleaned, flags=re.S)
        if match:
            data = json.loads(match.group(0))
            if isinstance(data, (dict, list)):
                return data
    raise ValueError("模型返回内容中未找到 JSON 对象或数组")


def extract_bill_items(data: Any) -> list[dict[str, Any]]:
    """从模型返回值中提取账单明细列表，兼容单对象和数组。"""
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = []
        for key in ("账单列表", "账单", "bills", "items", "records"):
            value = data.get(key)
            if isinstance(value, list):
                items = value
                break
        if not items:
            items = [data]
    else:
        raise ValueError("模型返回 JSON 必须是账单对象或账单数组")

    bill_items = [item for item in items if isinstance(item, dict)]
    if not bill_items:
        raise ValueError("模型返回中没有可用的账单明细")
    return bill_items


def normalize_bill_items(data: Any, original_text: str, source: str) -> list[dict[str, Any]]:
    """标准化模型返回的一条或多条账单。"""
    normalized_items: list[dict[str, Any]] = []
    for item in extract_bill_items(data):
        item_original_text = str(item.get("原始文本") or item.get("original_text") or original_text).strip()
        normalized_items.append(normalize_bill_data(item, item_original_text or original_text, source))
    return normalized_items


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

    本地命令行没有外部消息 ID，按账单核心字段生成哈希。
    飞书事件应优先使用飞书 message_id 生成去重 ID，避免重复投递时模型解析时间不同导致重复记账。
    """
    raw = "|".join(
        str(bill_data.get(key, ""))
        for key in ["日期", "时间", "类型", "金额", "币种", "商户或对象", "原始文本", "记录来源"]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def build_external_dedupe_id(namespace: str, external_id: str) -> str:
    """根据外部事件 ID 生成稳定去重 ID。"""
    raw = f"{namespace}:{external_id.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def build_child_dedupe_id(parent_dedupe_id: str, index: int, bill_data: dict[str, Any]) -> str:
    """为同一条外部消息中的多笔账单生成稳定子去重 ID。"""
    if not parent_dedupe_id:
        return build_dedupe_id(bill_data)
    raw = "|".join(
        [
            parent_dedupe_id,
            str(index),
            str(bill_data.get("日期", "")),
            str(bill_data.get("时间", "")),
            str(bill_data.get("类型", "")),
            str(bill_data.get("金额", "")),
            str(bill_data.get("币种", "")),
            str(bill_data.get("商户或对象", "")),
            str(bill_data.get("备注", "")),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
