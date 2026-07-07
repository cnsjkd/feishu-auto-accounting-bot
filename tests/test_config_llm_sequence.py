"""Tests for LLM fallback ordering."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import LLMConfig, Settings  # noqa: E402


def make_settings(provider: str) -> Settings:
    return Settings(
        feishu_app_id="cli_test",
        feishu_app_secret="secret",
        bitable_app_token="base_default",
        table_id="tbl_default",
        bitable_view_url="https://example.feishu.cn/base/base_default?table=tbl_default",
        llm_provider=provider,
        llm_configs={
            "qwen": LLMConfig("qwen", "qwen-key", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3.7-plus"),
            "openai": LLMConfig("openai", "openai-key", "https://api.openai.com/v1", "gpt-4o-mini"),
            "legacy": LLMConfig("legacy", "legacy-key", "https://legacy.example.com/v1", "gpt-legacy"),
        },
    )


def main() -> int:
    fallback_sequence = [item.name for item in make_settings("fallback").get_llm_sequence()]
    assert fallback_sequence == ["openai", "legacy", "qwen"]

    qwen_sequence = [item.name for item in make_settings("qwen").get_llm_sequence()]
    assert qwen_sequence == ["qwen"]

    openai_sequence = [item.name for item in make_settings("openai").get_llm_sequence()]
    assert openai_sequence == ["openai"]

    print("LLM fallback 顺序测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
