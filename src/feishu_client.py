"""飞书开放平台客户端。"""

from __future__ import annotations

import time
from typing import Any

import requests

from config import Settings
from models import Bill
from utils import json_dumps


REQUIRED_BITABLE_FIELDS = [
    "日期",
    "时间",
    "类型",
    "金额",
    "币种",
    "分类",
    "支付方式",
    "商户或对象",
    "备注",
    "原始文本",
    "记录来源",
    "创建时间",
    "唯一去重 ID",
]


class FeishuClient:
    BASE_URL = "https://open.feishu.cn/open-apis"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._tenant_access_token = ""
        self._token_expires_at = 0.0
        self._field_names_cache: set[str] | None = None

    def get_tenant_access_token(self) -> str:
        """获取并缓存 tenant_access_token。"""
        if self._tenant_access_token and time.time() < self._token_expires_at - 120:
            return self._tenant_access_token

        url = f"{self.BASE_URL}/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self.settings.feishu_app_id,
            "app_secret": self.settings.feishu_app_secret,
        }
        try:
            response = requests.post(url, json=payload, timeout=self.settings.request_timeout)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"获取飞书 tenant_access_token 失败: {exc}") from exc
        except ValueError as exc:
            raise RuntimeError(f"飞书 token 接口返回非 JSON 内容: {exc}") from exc

        if data.get("code") != 0:
            raise RuntimeError(f"获取飞书 tenant_access_token 失败: {data}")

        self._tenant_access_token = data["tenant_access_token"]
        self._token_expires_at = time.time() + int(data.get("expire", 7200))
        return self._tenant_access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.get_tenant_access_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def list_field_names(self) -> set[str]:
        """读取当前 Bitable 表已有字段名。"""
        if self._field_names_cache is not None:
            return self._field_names_cache

        url = (
            f"{self.BASE_URL}/bitable/v1/apps/{self.settings.bitable_app_token}"
            f"/tables/{self.settings.table_id}/fields?page_size=100"
        )
        field_names: set[str] = set()
        page_token = ""
        while True:
            params = {"page_token": page_token} if page_token else None
            try:
                response = requests.get(
                    url,
                    headers=self._headers(),
                    params=params,
                    timeout=self.settings.request_timeout,
                )
                data = response.json()
            except requests.RequestException as exc:
                print(f"[WARN] 获取 Bitable 字段列表失败，将按完整字段尝试写入: {exc}", flush=True)
                self._field_names_cache = set(REQUIRED_BITABLE_FIELDS)
                return self._field_names_cache
            except ValueError:
                print("[WARN] 获取 Bitable 字段列表返回非 JSON，将按完整字段尝试写入", flush=True)
                self._field_names_cache = set(REQUIRED_BITABLE_FIELDS)
                return self._field_names_cache

            if data.get("code") != 0:
                print(f"[WARN] 获取 Bitable 字段列表失败，将按完整字段尝试写入: {data}", flush=True)
                self._field_names_cache = set(REQUIRED_BITABLE_FIELDS)
                return self._field_names_cache

            for item in data.get("data", {}).get("items", []):
                name = item.get("field_name")
                if name:
                    field_names.add(str(name))
            if not data.get("data", {}).get("has_more"):
                break
            page_token = data.get("data", {}).get("page_token", "")
            if not page_token:
                break

        self._field_names_cache = field_names
        return field_names

    def has_dedupe_id(self, dedupe_id: str) -> bool:
        """查询 Bitable 中是否已存在唯一去重 ID。"""
        field_names = self.list_field_names()
        if "唯一去重 ID" not in field_names:
            print("[WARN] 当前表缺少 `唯一去重 ID` 字段，本次将跳过去重直接写入。建议尽快补齐该字段。", flush=True)
            return False

        url = (
            f"{self.BASE_URL}/bitable/v1/apps/{self.settings.bitable_app_token}"
            f"/tables/{self.settings.table_id}/records/search?page_size=1"
        )
        payload = {
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {
                        "field_name": "唯一去重 ID",
                        "operator": "is",
                        "value": [dedupe_id],
                    }
                ],
            }
        }
        try:
            response = requests.post(
                url,
                headers=self._headers(),
                json=payload,
                timeout=self.settings.request_timeout,
            )
            try:
                data = response.json()
            except ValueError as exc:
                raise RuntimeError(
                    f"Bitable 查询接口返回非 JSON 内容: HTTP {response.status_code}; {response.text}"
                ) from exc
            if response.status_code >= 400:
                self._raise_bitable_error(f"查询 Bitable 去重记录失败 HTTP {response.status_code}", data)
        except requests.RequestException as exc:
            raise RuntimeError(f"查询 Bitable 去重请求失败: {exc}") from exc

        if data.get("code") != 0:
            if self._is_missing_field_error(data):
                print("[WARN] `唯一去重 ID` 字段不存在，本次跳过去重直接写入。", flush=True)
                return False
            self._raise_bitable_error("查询 Bitable 去重记录失败", data)

        items = data.get("data", {}).get("items", [])
        return len(items) > 0

    def reply_message(self, message_id: str, text: str) -> dict[str, Any]:
        """回复飞书消息。"""
        if not message_id:
            raise RuntimeError("缺少 message_id，无法回复飞书消息")

        url = f"{self.BASE_URL}/im/v1/messages/{message_id}/reply"
        payload = {
            "msg_type": "text",
            "content": json_dumps({"text": text}),
        }
        try:
            response = requests.post(url, headers=self._headers(), json=payload, timeout=self.settings.request_timeout)
            try:
                data = response.json()
            except ValueError as exc:
                raise RuntimeError(
                    f"飞书消息回复接口返回非 JSON 内容: HTTP {response.status_code}; {response.text}"
                ) from exc
            if response.status_code >= 400:
                self._raise_message_error(f"回复飞书消息失败 HTTP {response.status_code}", data)
        except requests.RequestException as exc:
            raise RuntimeError(f"回复飞书消息请求失败: {exc}") from exc

        if data.get("code") != 0:
            self._raise_message_error("回复飞书消息失败", data)
        return data

    def create_bitable_record(self, bill: Bill) -> dict[str, Any]:
        """向飞书多维表格写入一条账单记录。"""
        url = (
            f"{self.BASE_URL}/bitable/v1/apps/{self.settings.bitable_app_token}"
            f"/tables/{self.settings.table_id}/records"
        )
        fields = self._filter_existing_fields(bill.to_bitable_fields())
        payload = {"fields": fields}
        try:
            response = requests.post(url, headers=self._headers(), json=payload, timeout=self.settings.request_timeout)
            try:
                data = response.json()
            except ValueError as exc:
                raise RuntimeError(
                    f"Bitable 写入接口返回非 JSON 内容: HTTP {response.status_code}; {response.text}"
                ) from exc
            if response.status_code >= 400:
                self._raise_bitable_error(f"写入 Bitable 失败 HTTP {response.status_code}", data)
        except requests.RequestException as exc:
            raise RuntimeError(f"写入 Bitable 请求失败: {exc}") from exc

        if data.get("code") != 0:
            self._raise_bitable_error("写入 Bitable 失败", data)
        return data

    def save_bill_once(self, bill: Bill) -> tuple[bool, dict[str, Any] | None]:
        """按唯一去重 ID 写入账单，已存在则跳过。"""
        if self.has_dedupe_id(bill.dedupe_id):
            return False, None
        return True, self.create_bitable_record(bill)

    def _filter_existing_fields(self, fields: dict[str, Any]) -> dict[str, Any]:
        """只写入当前表已存在的字段；必要时降级写入默认文本列。"""
        existing_fields = self.list_field_names()
        filtered = {key: value for key, value in fields.items() if key in existing_fields}
        missing = [key for key in fields if key not in existing_fields]
        if missing:
            print(
                "[WARN] 当前 Bitable 缺少这些字段，本次不会按结构化列写入对应值: " + "、".join(missing),
                flush=True,
            )
        if filtered:
            return filtered
        fallback_field = self._detect_text_fallback_field(existing_fields)
        if fallback_field:
            print(
                f"[WARN] 当前表还没有记账字段，将临时写入默认文本列 `{fallback_field}`。建议后续补齐结构化字段。",
                flush=True,
            )
            return {fallback_field: self._format_bill_for_text_field(fields)}
        raise RuntimeError(
            "当前 Bitable 没有任何可写入的目标字段。请至少创建字段：日期、类型、金额、原始文本；"
            "或保留一个文本字段，字段名为 `文本`、`账单`、`内容`、`备注`、`原始文本` 之一。"
        )

    def _detect_text_fallback_field(self, existing_fields: set[str]) -> str:
        """检测可用于临时落账的普通文本字段名。"""
        for name in ["文本", "账单", "内容", "备注", "原始文本", "记录"]:
            if name in existing_fields:
                return name
        return ""

    def _format_bill_for_text_field(self, fields: dict[str, Any]) -> str:
        """把结构化账单压成单列文本，便于没有建字段时先跑通写入。"""
        summary = (
            f"{fields.get('日期', '')} {fields.get('时间', '')} "
            f"{fields.get('类型', '')} {fields.get('金额', '')} {fields.get('币种', '')} "
            f"{fields.get('分类', '')} {fields.get('支付方式', '')} "
            f"{fields.get('商户或对象', '')} {fields.get('备注', '')}"
        ).strip()
        return f"{summary}\n原始文本: {fields.get('原始文本', '')}\n去重ID: {fields.get('唯一去重 ID', '')}\nJSON: {json_dumps(fields)}"

    def _is_missing_field_error(self, data: dict[str, Any]) -> bool:
        message = str(data.get("msg") or data.get("error", {}).get("message") or data)
        detail = str(data.get("error", {}).get("message", ""))
        combined = f"{message} {detail}"
        return "not found field_name" in combined or ("field_name" in combined and "not found" in combined)

    def _raise_message_error(self, prefix: str, data: dict[str, Any]) -> None:
        """把飞书消息回复常见错误转换为可操作提示。"""
        message = str(data.get("msg") or data.get("error", {}).get("message") or data)
        detail = data.get("error", {}).get("message", "")
        combined = f"{message} {detail}"
        if "im:message" in combined or "Forbidden" in combined or str(data.get("code")) in {"99991663", "230027"}:
            raise RuntimeError(
                f"{prefix}: 应用缺少回复消息权限或未发布生效。"
                "请在飞书开放平台权限管理中添加 `im:message:send_as_bot`，发布新版本并重新安装应用。"
            )
        raise RuntimeError(f"{prefix}: {data}")

    def _raise_bitable_error(self, prefix: str, data: dict[str, Any]) -> None:
        """把飞书 Bitable 常见错误转换为可操作提示。"""
        message = str(data.get("msg") or data.get("error", {}).get("message") or data)
        detail = data.get("error", {}).get("message", "")
        combined = f"{message} {detail}"
        if self._is_missing_field_error(data):
            fields = "、".join(REQUIRED_BITABLE_FIELDS)
            raise RuntimeError(
                f"{prefix}: 目标 Bitable 缺少必要字段。请先在表格中创建这些字段：{fields}。"
                "其中 `唯一去重 ID` 必须存在，用于重复账单判断。"
            )
        if "Forbidden" in combined or str(data.get("code")) == "91403":
            raise RuntimeError(
                f"{prefix}: 飞书返回 91403 Forbidden，表示当前应用身份对目标多维表格没有写入权限。"
                "请依次确认：1）开放平台权限已添加 `base:record:create`，如需自动建字段再加 `base:field:create`；"
                "2）已在「版本管理与发布」创建新版本并发布/重新安装应用；"
                "3）目标知识库/多维表格已把这个自建应用添加为协作者或授予可编辑权限；"
                "4）如果文档在知识库中，还要确认知识库节点/空间没有限制应用访问。"
            )
        if "base:record:create" in combined:
            raise RuntimeError(
                f"{prefix}: 应用缺少新增多维表格记录权限。"
                "请在飞书开放平台权限管理中添加 `base:record:create`，发布并重新安装应用。"
            )
        if "base:field:read" in combined or "bitable:app:readonly" in combined:
            raise RuntimeError(
                f"{prefix}: 应用缺少读取 Bitable 字段权限。"
                "请在飞书开放平台权限管理中添加 `base:field:read` 或 `bitable:app:readonly`，发布并重新安装应用。"
            )
        if "base:field:create" in combined or "bitable:app" in combined:
            raise RuntimeError(
                f"{prefix}: 应用缺少创建 Bitable 字段权限。"
                "如果要自动建字段，请在飞书开放平台权限管理中添加 `base:field:create`，发布并重新安装应用；"
                "或者手动在多维表格中创建 README 里列出的字段。"
            )
        raise RuntimeError(f"{prefix}: {data}")
