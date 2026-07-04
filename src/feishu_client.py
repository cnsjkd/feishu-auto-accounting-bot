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
        self._field_names_cache: dict[tuple[str, str], set[str]] = {}

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

    def list_field_names(self, app_token: str = "", table_id: str = "") -> set[str]:
        """读取当前 Bitable 表已有字段名。"""
        app_token = app_token or self.settings.bitable_app_token
        table_id = table_id or self.settings.table_id
        cache_key = (app_token, table_id)
        if cache_key in self._field_names_cache:
            return self._field_names_cache[cache_key]

        url = f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/fields?page_size=100"
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
                self._field_names_cache[cache_key] = set(REQUIRED_BITABLE_FIELDS)
                return self._field_names_cache[cache_key]
            except ValueError:
                print("[WARN] 获取 Bitable 字段列表返回非 JSON，将按完整字段尝试写入", flush=True)
                self._field_names_cache[cache_key] = set(REQUIRED_BITABLE_FIELDS)
                return self._field_names_cache[cache_key]

            if data.get("code") != 0:
                print(f"[WARN] 获取 Bitable 字段列表失败，将按完整字段尝试写入: {data}", flush=True)
                self._field_names_cache[cache_key] = set(REQUIRED_BITABLE_FIELDS)
                return self._field_names_cache[cache_key]

            for item in data.get("data", {}).get("items", []):
                name = item.get("field_name")
                if name:
                    field_names.add(str(name))
            if not data.get("data", {}).get("has_more"):
                break
            page_token = data.get("data", {}).get("page_token", "")
            if not page_token:
                break

        self._field_names_cache[cache_key] = field_names
        return field_names

    def has_dedupe_id(self, dedupe_id: str, app_token: str = "", table_id: str = "") -> bool:
        """查询 Bitable 中是否已存在唯一去重 ID。"""
        app_token = app_token or self.settings.bitable_app_token
        table_id = table_id or self.settings.table_id
        field_names = self.list_field_names(app_token, table_id)
        if "唯一去重 ID" not in field_names:
            print("[WARN] 当前表缺少 `唯一去重 ID` 字段，本次将跳过去重直接写入。建议尽快补齐该字段。", flush=True)
            return False

        url = f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records/search?page_size=1"
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

    def resolve_wiki_obj_token(self, wiki_token: str) -> str:
        """Resolve a wiki token to the underlying Bitable app token."""
        url = f"{self.BASE_URL}/wiki/v2/spaces/get_node"
        try:
            response = requests.get(
                url,
                headers=self._headers(),
                params={"token": wiki_token},
                timeout=self.settings.request_timeout,
            )
            data = response.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"调用飞书 wiki get_node 接口失败: {exc}") from exc
        except ValueError as exc:
            raise RuntimeError(f"飞书 wiki get_node 接口返回非 JSON 内容: {exc}") from exc
        if response.status_code >= 400 or data.get("code") != 0:
            raise RuntimeError(f"飞书 wiki get_node 接口返回错误: HTTP {response.status_code}; {data}")
        node = data.get("data", {}).get("node", {})
        obj_token = node.get("obj_token")
        if not obj_token:
            raise RuntimeError(f"飞书 wiki get_node 结果缺少 obj_token: {data}")
        return str(obj_token)

    def list_tables(self, app_token: str) -> list[dict[str, Any]]:
        """List tables in a Bitable app."""
        url = f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables"
        tables: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            try:
                response = requests.get(url, headers=self._headers(), params=params, timeout=self.settings.request_timeout)
                data = response.json()
            except requests.RequestException as exc:
                raise RuntimeError(f"读取 Bitable table 列表请求失败: {exc}") from exc
            except ValueError as exc:
                raise RuntimeError(f"读取 Bitable table 列表返回非 JSON 内容: {exc}") from exc
            if response.status_code >= 400 or data.get("code") != 0:
                self._raise_bitable_error(f"读取 Bitable table 列表失败 HTTP {response.status_code}", data)
            tables.extend(data.get("data", {}).get("items", []))
            if not data.get("data", {}).get("has_more"):
                break
            page_token = data.get("data", {}).get("page_token", "")
            if not page_token:
                break
        return tables

    def create_table(self, app_token: str, table_name: str) -> str:
        """Create a Bitable table and return table_id."""
        url = f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables"
        payload = {"table": {"name": table_name}}
        try:
            response = requests.post(url, headers=self._headers(), json=payload, timeout=self.settings.request_timeout)
            data = response.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"创建 Bitable table 请求失败: {exc}") from exc
        except ValueError as exc:
            raise RuntimeError(f"创建 Bitable table 返回非 JSON 内容: {exc}") from exc
        if response.status_code >= 400 or data.get("code") != 0:
            self._raise_bitable_error(f"创建 Bitable table 失败 HTTP {response.status_code}", data)
        table = data.get("data", {}).get("table", {})
        table_id = table.get("table_id") or data.get("data", {}).get("table_id")
        if not table_id:
            raise RuntimeError(f"创建 Bitable table 成功但响应缺少 table_id: {data}")
        self._field_names_cache.pop((app_token, str(table_id)), None)
        return str(table_id)

    def ensure_bitable_fields(self, app_token: str, table_id: str) -> None:
        """Ensure all accounting fields exist in a table."""
        existing = self.list_field_names(app_token, table_id)
        for field in _required_field_payloads():
            field_name = field["field_name"]
            if field_name in existing:
                continue
            url = f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
            try:
                response = requests.post(url, headers=self._headers(), json=field, timeout=self.settings.request_timeout)
                data = response.json()
            except requests.RequestException as exc:
                raise RuntimeError(f"创建 Bitable 字段请求失败 {field_name}: {exc}") from exc
            except ValueError as exc:
                raise RuntimeError(f"创建 Bitable 字段返回非 JSON {field_name}: {exc}") from exc
            if response.status_code >= 400 or data.get("code") != 0:
                self._raise_bitable_error(f"创建 Bitable 字段失败 {field_name}", data)
            existing.add(field_name)
        self._field_names_cache[(app_token, table_id)] = existing

    def list_records(self, app_token: str, table_id: str, page_size: int = 500) -> list[dict[str, Any]]:
        """Read all records from a Bitable table."""
        url = f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        records: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            try:
                response = requests.get(url, headers=self._headers(), params=params, timeout=self.settings.request_timeout)
                data = response.json()
            except requests.RequestException as exc:
                raise RuntimeError(f"读取 Bitable 记录请求失败: {exc}") from exc
            except ValueError as exc:
                raise RuntimeError(f"读取 Bitable 记录返回非 JSON 内容: {exc}") from exc
            if response.status_code >= 400 or data.get("code") != 0:
                self._raise_bitable_error(f"读取 Bitable 记录失败 HTTP {response.status_code}", data)
            records.extend(data.get("data", {}).get("items", []))
            if not data.get("data", {}).get("has_more"):
                break
            page_token = data.get("data", {}).get("page_token", "")
            if not page_token:
                break
        return records

    def send_text_to_open_id(self, open_id: str, text: str) -> dict[str, Any]:
        """Send a direct bot message to a Feishu user open_id."""
        if not open_id:
            raise RuntimeError("缺少 open_id，无法发送飞书私聊消息")
        url = f"{self.BASE_URL}/im/v1/messages"
        payload = {
            "receive_id": open_id,
            "msg_type": "text",
            "content": json_dumps({"text": text}),
        }
        try:
            response = requests.post(
                url,
                headers=self._headers(),
                params={"receive_id_type": "open_id"},
                json=payload,
                timeout=self.settings.request_timeout,
            )
            data = response.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"发送飞书私聊消息请求失败: {exc}") from exc
        except ValueError as exc:
            raise RuntimeError(f"发送飞书私聊消息返回非 JSON 内容: {exc}") from exc
        if response.status_code >= 400 or data.get("code") != 0:
            self._raise_message_error(f"发送飞书私聊消息失败 HTTP {response.status_code}", data)
        return data

    def create_bitable_record(self, bill: Bill, app_token: str = "", table_id: str = "") -> dict[str, Any]:
        """向飞书多维表格写入一条账单记录。"""
        app_token = app_token or self.settings.bitable_app_token
        table_id = table_id or self.settings.table_id
        url = f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        fields = self._filter_existing_fields(bill.to_bitable_fields(), app_token, table_id)
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

    def save_bill_once(self, bill: Bill, app_token: str = "", table_id: str = "") -> tuple[bool, dict[str, Any] | None]:
        """按唯一去重 ID 写入账单，已存在则跳过。"""
        if self.has_dedupe_id(bill.dedupe_id, app_token, table_id):
            return False, None
        return True, self.create_bitable_record(bill, app_token, table_id)

    def _filter_existing_fields(self, fields: dict[str, Any], app_token: str = "", table_id: str = "") -> dict[str, Any]:
        """只写入当前表已存在的字段；必要时降级写入默认文本列。"""
        existing_fields = self.list_field_names(app_token, table_id)
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


def _required_field_payloads() -> list[dict[str, Any]]:
    """Return Bitable field creation payloads for accounting tables."""
    return [
        {"field_name": "日期", "type": 1},
        {"field_name": "时间", "type": 1},
        {"field_name": "类型", "type": 1},
        {"field_name": "金额", "type": 2},
        {"field_name": "币种", "type": 1},
        {"field_name": "分类", "type": 1},
        {"field_name": "支付方式", "type": 1},
        {"field_name": "商户或对象", "type": 1},
        {"field_name": "备注", "type": 1},
        {"field_name": "原始文本", "type": 1},
        {"field_name": "记录来源", "type": 1},
        {"field_name": "创建时间", "type": 1},
        {"field_name": "唯一去重 ID", "type": 1},
    ]
