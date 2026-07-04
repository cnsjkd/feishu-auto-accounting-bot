"""飞书机器人事件接收服务。

使用标准库 http.server，不引入 Flask/FastAPI 等框架。
"""

from __future__ import annotations

import json
import re
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from typing import TYPE_CHECKING, Any

from user_registry import extract_user_identity
from utils import build_external_dedupe_id, json_dumps

BILL_REPLY_FIELDS = [
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
]
PROCESSED_MESSAGE_CACHE_LIMIT = 1000

if TYPE_CHECKING:
    from service import AccountingService


def extract_message_info(payload: dict[str, Any]) -> tuple[str, str, str]:
    """从飞书事件回调 payload 中提取消息文本、来源描述和 message_id。"""
    # 飞书事件订阅 v2.0 格式：{"event":{"message":...}}
    # 部分场景或旧版回调可能把 message 放在根级，兼容读取。
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    if not message and isinstance(payload.get("message"), dict):
        message = payload["message"]

    # 兼容飞书旧版机器人消息事件：文本直接放在 event.text，类型是 event.msg_type。
    legacy_message = False
    if not message and (event.get("msg_type") or event.get("message_type") or event.get("text")):
        message = event
        legacy_message = True

    message_type = message.get("message_type") or message.get("msg_type")
    if message_type != "text":
        return "", f"飞书机器人:{message_type or 'unknown'}", ""

    content = message.get("content") or "{}"
    try:
        content_data = json.loads(content) if isinstance(content, str) else content
    except json.JSONDecodeError:
        content_data = {}
    if not isinstance(content_data, dict):
        content_data = {}

    raw_text = (
        content_data.get("text")
        or content_data.get("content")
        or message.get("text_without_at_bot")
        or message.get("text")
        or ""
    )
    text = _clean_feishu_text(str(raw_text))
    message_id = message.get("message_id") or message.get("open_message_id") or message.get("uuid") or ""
    source_prefix = "飞书机器人旧版事件" if legacy_message else "飞书机器人"
    source = f"{source_prefix}:{message_id}" if message_id else source_prefix
    return text, source, str(message_id)


def extract_message_text(payload: dict[str, Any]) -> tuple[str, str]:
    """从飞书事件回调 payload 中提取消息文本和来源描述。"""
    text, source, _message_id = extract_message_info(payload)
    return text, source


_AT_MENTION_RE = re.compile(r"<at\b[^>]*>.*?</at>", re.IGNORECASE | re.DOTALL)


def build_accounting_reply(result: dict[str, Any], table_url: str = "") -> str:
    """根据记账结果生成飞书回复文案。"""
    if result.get("reply"):
        return str(result["reply"])

    bill = result.get("bill") if isinstance(result.get("bill"), dict) else {}
    if result.get("queued"):
        title = "记账失败，已保存到本地待重试队列。"
        reason = f"原因：{result.get('error', '未知错误')}"
    elif result.get("created"):
        title = "记账成功，已写入多维表格。"
        reason = ""
    else:
        title = "这条账单已存在，本次没有重复写入。"
        reason = ""

    lines = [title]
    if reason:
        lines.append(reason)
    if bill:
        lines.append("")
        lines.append("本次记录：")
        lines.extend(_format_bill_summary_lines(bill))
    table_url = str(result.get("table_url") or table_url)
    if result.get("month_key"):
        lines.append(f"月份：{result['month_key']}")
    if table_url:
        lines.append("")
        lines.append(f"查看完整记账表格：{table_url}")
    return "\n".join(lines)


def build_exception_reply(error: Exception, table_url: str = "") -> str:
    """生成未进入业务结果时的异常回复。"""
    reason = _format_user_facing_error(error)
    lines = ["记账失败，未写入多维表格。", f"原因：{reason}"]
    if table_url:
        lines.append("")
        lines.append(f"查看记账表格：{table_url}")
    return "\n".join(lines)


def _format_user_facing_error(error: Exception) -> str:
    """把内部异常压缩成适合聊天窗口展示的短提示。"""
    message = str(error)
    if "解析大模型返回失败" in message or "模型返回内容中未找到 JSON 对象" in message:
        return "这条消息不像一条明确的账单。请补充金额、用途或收入/支出信息后再发一次。"
    if "所有模型调用均失败" in message or "调用大模型接口失败" in message:
        return "大模型解析暂时失败，请稍后重试。"
    if "写入 Bitable" in message or "Bitable" in message:
        return message.split("; 原始返回", 1)[0][:300]
    return message.split("; 原始返回", 1)[0][:300]


def _format_bill_summary_lines(bill: dict[str, Any]) -> list[str]:
    lines = []
    for field in BILL_REPLY_FIELDS:
        value = bill.get(field)
        if value not in (None, ""):
            lines.append(f"- {field}：{value}")
    return lines


def _clean_feishu_text(text: str) -> str:
    """清理飞书文本消息中的 @ 标签和多余空白。"""
    without_mentions = _AT_MENTION_RE.sub("", text)
    return " ".join(without_mentions.split()).strip()


def make_handler(service: "AccountingService") -> type[BaseHTTPRequestHandler]:
    processing_message_ids: set[str] = set()
    processed_message_ids: set[str] = set()
    processed_message_order: deque[str] = deque()
    processing_lock = Lock()

    class FeishuEventHandler(BaseHTTPRequestHandler):
        server_version = "AutoAccountingFeishu/1.0"

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send_json(200, {"ok": True})
                return
            self._send_json(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:
            try:
                payload = self._read_json()
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return

            # 飞书 URL verification 事件需要原样返回 challenge。
            # 兼容常见格式：
            # 1. {"type":"url_verification","challenge":"..."}
            # 2. {"schema":"2.0","header":{"event_type":"url_verification"},"event":{"challenge":"..."}}
            challenge = self._extract_challenge(payload)
            if challenge:
                print(f"[INFO] 收到飞书 URL 校验请求，已返回 challenge。path={self.path}", flush=True)
                self._send_json(200, {"challenge": challenge})
                return

            if payload.get("encrypt"):
                print(
                    "[ERROR] 收到加密的飞书事件 payload，但当前服务未配置解密。"
                    "请在飞书开放平台「事件与回调 -> 加密策略」关闭事件加密，或扩展代码配置 ENCRYPT_KEY。",
                    flush=True,
                )
                self._send_json(
                    200,
                    {
                        "ok": False,
                        "error": "收到加密事件，无法提取 challenge。请关闭飞书事件加密或配置解密。",
                    },
                )
                return

            print(
                f"[DEBUG] 收到飞书 POST: path={self.path}, keys={list(payload.keys())}",
                flush=True,
            )

            if self.path not in {"/feishu/events", "/"}:
                self._send_json(404, {"ok": False, "error": "not found"})
                return

            text, source, message_id = extract_message_info(payload)
            identity = extract_user_identity(payload)
            if not text:
                event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
                message = event.get("message") if isinstance(event.get("message"), dict) else {}
                if not message and isinstance(payload.get("message"), dict):
                    message = payload["message"]
                message_type = message.get("message_type") or message.get("msg_type") or "unknown"
                content_preview = str(message.get("content") or "")[:200]
                print(
                    f"[INFO] 已忽略飞书事件: message_type={message_type}, content_preview={content_preview}",
                    flush=True,
                )
                self._send_json(200, {"ok": True, "ignored": True, "reason": "非文本消息或文本为空"})
                return

            print(f"[INFO] 提取到飞书记账文本: {text} source={source}", flush=True)
            event_dedupe_id = build_external_dedupe_id("feishu_message", message_id) if message_id else ""
            if event_dedupe_id and service.db.has_processed_event(event_dedupe_id):
                print(
                    f"[INFO] 忽略飞书重复投递: message_id={message_id}, reason=duplicate_persisted",
                    flush=True,
                )
                self._send_json(200, {"ok": True, "ignored": True, "reason": "duplicate_persisted"})
                return
            duplicate_reason = self._mark_message_processing(message_id) if message_id else ""
            if duplicate_reason:
                print(
                    f"[INFO] 忽略飞书重复投递: message_id={message_id}, reason={duplicate_reason}",
                    flush=True,
                )
                self._send_json(200, {"ok": True, "ignored": True, "reason": duplicate_reason})
                return
            try:
                result = service.handle_user_text(text, identity=identity, source=source, dedupe_id=event_dedupe_id)
                print(
                    f"[INFO] 飞书记账处理完成: created={result.get('created')}, queued={result.get('queued')}, dedupe_id={result.get('dedupe_id')}",
                    flush=True,
                )
                reply_text = build_accounting_reply(result, service.feishu_client.settings.bitable_view_url)
                print(f"[INFO] 准备回复飞书消息: message_id={message_id}, reply_preview={reply_text[:120]}", flush=True)
                self._reply_to_message(message_id, reply_text)
                if event_dedupe_id:
                    service.db.mark_event_processed(event_dedupe_id, tenant_key=identity.tenant_key, open_id=identity.open_id)
                self._send_json(200, {"ok": True, **result})
            except Exception as exc:  # noqa: BLE001 - 入口层需要兜底打印清晰错误
                print(f"[ERROR] 处理飞书事件失败: {exc}", flush=True)
                reply_text = build_exception_reply(exc, service.feishu_client.settings.bitable_view_url)
                print(f"[INFO] 准备回复飞书失败消息: message_id={message_id}, reply_preview={reply_text[:120]}", flush=True)
                self._reply_to_message(message_id, reply_text)
                if event_dedupe_id:
                    service.db.mark_event_processed(event_dedupe_id, tenant_key=identity.tenant_key, open_id=identity.open_id, status="failed")
                self._send_json(200, {"ok": False, "error": str(exc)})
            finally:
                if message_id:
                    self._mark_message_done(message_id)

        def log_message(self, format: str, *args: Any) -> None:
            print(f"[HTTP] {self.address_string()} - {format % args}", flush=True)

        def _mark_message_processing(self, message_id: str) -> str:
            """标记消息处理中；返回非空字符串表示重复事件应被忽略。"""
            with processing_lock:
                if message_id in processed_message_ids:
                    return "duplicate_processed"
                if message_id in processing_message_ids:
                    return "duplicate_in_flight"
                processing_message_ids.add(message_id)
                return ""

        def _mark_message_done(self, message_id: str) -> None:
            with processing_lock:
                processing_message_ids.discard(message_id)
                if message_id in processed_message_ids:
                    return
                processed_message_ids.add(message_id)
                processed_message_order.append(message_id)
                while len(processed_message_order) > PROCESSED_MESSAGE_CACHE_LIMIT:
                    expired_message_id = processed_message_order.popleft()
                    processed_message_ids.discard(expired_message_id)

        def _reply_to_message(self, message_id: str, text: str) -> None:
            """尽力回复飞书消息，回复失败不影响事件确认。"""
            if not message_id:
                print("[WARN] 飞书事件缺少 message_id，无法回复聊天消息。", flush=True)
                return
            try:
                service.feishu_client.reply_message(message_id, text)
                print(f"[INFO] 已回复飞书消息: message_id={message_id}", flush=True)
            except Exception as exc:  # noqa: BLE001 - 回复失败不应触发飞书重试整个事件
                print(f"[ERROR] 回复飞书消息失败: {exc}", flush=True)

        def _extract_challenge(self, payload: dict[str, Any]) -> str:
            """提取飞书 URL verification challenge。"""
            direct_challenge = payload.get("challenge")
            if payload.get("type") == "url_verification" and direct_challenge:
                return str(direct_challenge)

            event = payload.get("event") or {}
            event_challenge = event.get("challenge") if isinstance(event, dict) else None
            header = payload.get("header") or {}
            event_type = header.get("event_type") if isinstance(header, dict) else ""
            if event_type == "url_verification" and event_challenge:
                return str(event_challenge)

            nested_challenge = payload.get("challenge_code") or payload.get("challengeCode")
            if nested_challenge:
                return str(nested_challenge)
            return ""

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length).decode("utf-8") if length else "{}"
            try:
                payload = json.loads(raw_body)
            except json.JSONDecodeError as exc:
                raise ValueError(f"请求体不是合法 JSON: {exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError("请求体 JSON 必须是对象")
            return payload

        def _send_json(self, status_code: int, data: dict[str, Any]) -> None:
            body = json_dumps(data).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return FeishuEventHandler


def run_server(host: str, port: int, service: "AccountingService") -> None:
    """启动飞书事件 HTTP 服务。"""
    server = ThreadingHTTPServer((host, port), make_handler(service))
    print(f"[INFO] 飞书事件服务已启动: http://{host}:{port}/feishu/events", flush=True)
    print("[INFO] 已启用记账结果聊天回复功能", flush=True)
    print("[INFO] 健康检查: /health", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] 收到退出信号，正在关闭服务...", flush=True)
    finally:
        server.server_close()
