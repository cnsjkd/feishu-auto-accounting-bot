"""大模型账单解析模块。"""

from __future__ import annotations

from typing import Any

import requests

from config import LLMConfig, Settings
from models import Bill
from utils import build_dedupe_id, extract_json_object, json_dumps, normalize_bill_data, today_context


class GPTBillParser:
    def __init__(self, settings: Settings):
        self.settings = settings

    def parse(self, text: str, source: str = "飞书机器人") -> Bill:
        """调用配置的大模型，将自然语言账单解析为 Bill。"""
        if not text or not text.strip():
            raise ValueError("待解析文本为空")

        result = self._call_with_fallback(text)
        try:
            content = result["choices"][0]["message"]["content"]
            model_data = extract_json_object(content)
            normalized = normalize_bill_data(model_data, original_text=text, source=source)
            dedupe_id = build_dedupe_id(normalized)
            return Bill(
                date=normalized["日期"],
                time=normalized["时间"],
                bill_type=normalized["类型"],
                amount=normalized["金额"],
                currency=normalized["币种"],
                category=normalized["分类"],
                payment_method=normalized["支付方式"],
                merchant=normalized["商户或对象"],
                note=normalized["备注"],
                original_text=normalized["原始文本"],
                source=normalized["记录来源"],
                dedupe_id=dedupe_id,
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError(f"解析大模型返回失败: {exc}; 原始返回: {result}") from exc

    def _call_with_fallback(self, text: str) -> dict[str, Any]:
        """按配置顺序调用模型，失败时自动尝试下一个。"""
        errors: list[str] = []
        llm_sequence = self.settings.get_llm_sequence()
        for llm_config in llm_sequence:
            for attempt in range(1, self.settings.llm_max_retries + 1):
                try:
                    print(
                        f"[INFO] 正在调用模型: {llm_config.name}/{llm_config.model} "
                        f"(第 {attempt}/{self.settings.llm_max_retries} 次)",
                        flush=True,
                    )
                    return self._call_chat_completions(llm_config, text)
                except Exception as exc:  # noqa: BLE001 - fallback 需要收集所有模型错误
                    error_message = f"{llm_config.name}/{llm_config.model}: {exc}"
                    errors.append(error_message)
                    if attempt < self.settings.llm_max_retries:
                        print(f"[WARN] 模型调用失败，准备重试: {error_message}", flush=True)
                    else:
                        print(f"[WARN] 模型调用失败，准备尝试下一个: {error_message}", flush=True)
        raise RuntimeError("所有模型调用均失败: " + " | ".join(errors))

    def _call_chat_completions(self, llm_config: LLMConfig, text: str) -> dict[str, Any]:
        """调用 OpenAI 兼容 Chat Completions 接口。"""
        ctx = today_context()
        system_prompt = (
            "你是一个个人记账解析器。请只输出一个 JSON 对象，不要输出解释。"
            "字段必须包含：日期、时间、类型、金额、币种、分类、支付方式、商户或对象、备注。"
            "类型只能是收入或支出。"
            "分类只能是餐饮、交通、购物、住宿、工资、报销、其他。"
            "支付方式只能是微信、支付宝、银行卡、现金、其他。"
            "币种默认 CNY。金额必须是数字。"
            "如果用户没有说明时间，用当前时间；没有说明日期，根据上下文判断，默认今天。"
        )
        user_prompt = (
            f"当前日期上下文：{json_dumps(ctx)}\n"
            f"请解析这条记账消息：{text}\n"
            "输出示例：{\"日期\":\"2026-07-02\",\"时间\":\"12:30:00\",\"类型\":\"支出\",\"金额\":38.5,\"币种\":\"CNY\",\"分类\":\"餐饮\",\"支付方式\":\"其他\",\"商户或对象\":\"美团外卖\",\"备注\":\"午餐\"}"
        )
        payload = {
            "model": llm_config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {llm_config.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(
                llm_config.chat_completions_url,
                headers=headers,
                json=payload,
                timeout=self.settings.llm_timeout,
            )
            try:
                result = response.json()
            except ValueError as exc:
                raise RuntimeError(
                    f"大模型接口返回非 JSON 内容: HTTP {response.status_code}; {response.text}"
                ) from exc
            if response.status_code >= 400:
                raise RuntimeError(f"大模型接口 HTTP {response.status_code}: {result}")
            return result
        except requests.RequestException as exc:
            raise RuntimeError(f"调用大模型接口失败: {exc}") from exc
