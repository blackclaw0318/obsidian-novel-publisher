# ============================================================
# test_novel_writer.py - 单测 v0.40 (Anthropic 兼容 + stream + M2.7)
# ============================================================
# 覆盖:
#   - 纯函数: count_chinese_chars / strip_think_block / _load_template
#   - NovelWriter.__init__ key 校验 / model 默认 (M2.7) / base_url 默认 (anthropic)
#   - _call_anthropic_stream: text block 提取 / thinking block 跳过 / stop_reason
#   - _call_anthropic_stream: 4xx 立即抛 / 5xx 重试 / 超时重试 / refusal 不可重试
#   - _call_raw: empty content 重试 / 成功路径 (topic_gen 复用)
#   - write_chapter: 字数合格一次过 / 字数不足重生 / 0 字硬校验抛 / network error 重试
#   - _compose_cover_prompt: 4 类题材检测 + palette/style 注入 (v0.6 不变)
#   - _detect_genre + _style_for_genre: keyword 反推题材
#
# 变更: v0.34 → v0.40
#   - mock target: requests.post → anthropic.Anthropic
#   - mock response: OpenAI chat.completion → Anthropic Message (content blocks)
#   - 新增: stop_reason 分类测试 / refusal 不可重试测试 / stream 解析测试
# ============================================================

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest

from src.novel_writer import (
    ContentFilterError,
    LLMError,
    NovelWriter,
    _load_template,
    count_chinese_chars,
    strip_think_block,
)

# ============ Fixtures ============
SAMPLE_CHAPTER_TEXT = "夜色笼罩了整座荒原。" * 1500  # ~9000 中文字符
SAMPLE_SHORT_TEXT = "夜色笼罩。" * 100  # ~400 中文字符 (字数不足)
SAMPLE_THINK_TEXT = "<think>这是思考过程</think>正文开始。" * 1500


@pytest.fixture
def writer() -> NovelWriter:
    """用 fake key + mock base_url 构造 NovelWriter (不连真 API)"""
    return NovelWriter(
        api_key="sk-test-1234",
        base_url="https://mock.api/anthropic",
        model="test-model",
    )


def _make_text_block(text: str) -> MagicMock:
    """构造 anthropic TextBlock mock"""
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_thinking_block(text: str = "思考过程...") -> MagicMock:
    """构造 anthropic ThinkingBlock mock"""
    block = MagicMock()
    block.type = "thinking"
    block.thinking = text
    return block


def _mock_anthropic_message(
    text: str,
    stop_reason: str = "end_turn",
    input_tokens: int = 100,
    output_tokens: int = 1000,
    include_thinking: bool = False,
) -> MagicMock:
    """构造 anthropic Message mock (含 stop_reason + usage + content blocks)"""
    content = []
    if include_thinking:
        content.append(_make_thinking_block("模型思考中..."))
    content.append(_make_text_block(text))

    msg = MagicMock()
    msg.content = content
    msg.stop_reason = stop_reason
    msg.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    return msg


def _mock_httpx_request() -> httpx.Request:
    """构造 mock httpx.Request (anthropic 0.116 APIStatusError 等需要)"""
    return httpx.Request("POST", "https://api.minimaxi.com/anthropic/v1/messages")


def _mock_httpx_response(status_code: int) -> httpx.Response:
    """构造 mock httpx.Response"""
    return httpx.Response(status_code, request=_mock_httpx_request())


@contextmanager
def _mock_stream_context(final_msg: MagicMock):
    """mock anthropic stream context manager
    用法:
        with _mock_stream_context(_mock_anthropic_message("OK")) as ctx:
            # stream.get_final_message() 返 final_msg
    """
    stream_cm = MagicMock()
    stream_cm.get_final_message = MagicMock(return_value=final_msg)

    @contextmanager
    def _ctx():
        yield stream_cm

    yield _ctx()


# ============ 纯函数 ============
class TestPureFunctions:
    def test_count_chinese_chars(self) -> None:
        assert count_chinese_chars("你好世界") == 4
        assert count_chinese_chars("hello world") == 0
        assert count_chinese_chars("Mix 中文 123 。") == 2
        assert count_chinese_chars("") == 0

    def test_strip_think_block_official(self) -> None:
        text = "<think>思考</think>正式内容"
        assert strip_think_block(text) == "正式内容"

    def test_strip_think_block_reasoning(self) -> None:
        text = "<reasoning>思考</reasoning>正文"
        assert strip_think_block(text) == "正文"

    def test_strip_think_block_thinking(self) -> None:
        text = "<thinking>思考</thinking>正文"
        assert strip_think_block(text) == "正文"

    def test_strip_think_block_brackets(self) -> None:
        text = "【思考】思考【/思考】正文"
        assert strip_think_block(text) == "正文"

    def test_strip_think_block_no_block(self) -> None:
        text = "纯文本无思考块"
        assert strip_think_block(text) == "纯文本无思考块"

    def test_load_template_existing(self) -> None:
        text = _load_template("system.txt")
        assert "科幻" in text or "获奖" in text

    def test_load_template_missing_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            _load_template("nonexistent.txt")


# ============ NovelWriter init ============
class TestInit:
    def test_no_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MINIMAXI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="MINIMAXI_API_KEY"):
            NovelWriter()

    def test_default_model_m27(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """v0.40: 默认 model = MiniMax-M2.7 (不再 M3)"""
        monkeypatch.setenv("MINIMAXI_API_KEY", "sk-test")
        monkeypatch.delenv("MINIMAXI_TEXT_MODEL", raising=False)
        w = NovelWriter()
        assert w.model == "MiniMax-M2.7"

    def test_default_base_url_anthropic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """v0.40: 默认 base_url = anthropic 兼容端点"""
        monkeypatch.setenv("MINIMAXI_API_KEY", "sk-test")
        monkeypatch.delenv("MINIMAXI_BASE_URL", raising=False)
        w = NovelWriter()
        assert w.base_url == "https://api.minimaxi.com/anthropic"

    def test_repr_no_key_leak(self) -> None:
        w = NovelWriter(api_key="sk-very-secret-key", base_url="https://x")
        r = repr(w)
        assert "sk-very-secret-key" not in r
        assert "sk-ver" in r  # fingerprint prefix only (first 6 chars)
        assert "-key" in r  # suffix (last 4 chars)


# ============ _call_anthropic_stream ============
class TestCallAnthropicStream:
    def test_text_only_success(self, writer: NovelWriter) -> None:
        """text 块 → 直接返回"""
        msg = _mock_anthropic_message("正常内容")
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(get_final_message=MagicMock(return_value=msg)))
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(writer._client.messages, "stream", return_value=ctx):
            text, stop_reason, usage, duration_s = writer._call_anthropic_stream(
                system="s", user="u", temperature=0.9, max_tokens=8000
            )
        assert text == "正常内容"
        assert stop_reason == "end_turn"
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 1000
        assert duration_s > 0

    def test_thinking_block_skipped(self, writer: NovelWriter) -> None:
        """thinking 块 → 跳过, 只返回 text"""
        msg = _mock_anthropic_message("正文内容", include_thinking=True)
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(get_final_message=MagicMock(return_value=msg)))
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(writer._client.messages, "stream", return_value=ctx):
            text, _, _, _ = writer._call_anthropic_stream(
                system="s", user="u", temperature=0.9, max_tokens=8000
            )
        assert text == "正文内容"
        assert "思考" not in text

    def test_refusal_raises_content_filter(self, writer: NovelWriter) -> None:
        """stop_reason='refusal' → ContentFilterError 不可重试"""
        msg = _mock_anthropic_message("敏感内容", stop_reason="refusal")
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(get_final_message=MagicMock(return_value=msg)))
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(writer._client.messages, "stream", return_value=ctx):
            with pytest.raises(ContentFilterError, match="内容审查"):
                writer._call_anthropic_stream(
                    system="s", user="u", temperature=0.9, max_tokens=8000
                )

    def test_max_tokens_stop_reason(self, writer: NovelWriter) -> None:
        """stop_reason='max_tokens' → 正常返回, 不抛 (上层 write_chapter 字数校验会处理)"""
        msg = _mock_anthropic_message("部分内容被截断", stop_reason="max_tokens")
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(get_final_message=MagicMock(return_value=msg)))
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(writer._client.messages, "stream", return_value=ctx):
            text, stop_reason, _, _ = writer._call_anthropic_stream(
                system="s", user="u", temperature=0.9, max_tokens=8000
            )
        assert text == "部分内容被截断"
        assert stop_reason == "max_tokens"

    def test_4xx_raises_immediately(self, writer: NovelWriter) -> None:
        """4xx → LLMError 立即抛, 不重试 (上层 write_chapter 也不重试)"""
        error = anthropic.APIStatusError(
            message="bad request",
            response=_mock_httpx_response(400),
            body={"error": "invalid model"},
        )

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(side_effect=error)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(writer._client.messages, "stream", return_value=ctx):
            with pytest.raises(LLMError, match="4xx"):
                writer._call_anthropic_stream(
                    system="s", user="u", temperature=0.9, max_tokens=8000
                )

    def test_5xx_raises_for_retry(self, writer: NovelWriter) -> None:
        """5xx → LLMError, 上层 write_chapter 会 retry"""
        error = anthropic.APIStatusError(
            message="server error",
            response=_mock_httpx_response(500),
            body={"error": "internal"},
        )

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(side_effect=error)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(writer._client.messages, "stream", return_value=ctx):
            with pytest.raises(LLMError, match="5xx"):
                writer._call_anthropic_stream(
                    system="s", user="u", temperature=0.9, max_tokens=8000
                )

    def test_connection_error_raises(self, writer: NovelWriter) -> None:
        """APIConnectionError → LLMError"""
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(
            side_effect=anthropic.APIConnectionError(request=_mock_httpx_request())
        )
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(writer._client.messages, "stream", return_value=ctx):
            with pytest.raises(LLMError, match="连接错"):
                writer._call_anthropic_stream(
                    system="s", user="u", temperature=0.9, max_tokens=8000
                )

    def test_timeout_raises(self, writer: NovelWriter) -> None:
        """APITimeoutError → LLMError"""
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(side_effect=anthropic.APITimeoutError("timeout"))
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(writer._client.messages, "stream", return_value=ctx):
            with pytest.raises(LLMError, match="timeout"):
                writer._call_anthropic_stream(
                    system="s", user="u", temperature=0.9, max_tokens=8000
                )


# ============ _call_raw (通用接口, topic_gen 复用) ============
class TestCallRaw:
    def test_happy_path(self, writer: NovelWriter) -> None:
        """正常 text → 返回"""
        msg = _mock_anthropic_message("选题 JSON 输出")
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(get_final_message=MagicMock(return_value=msg)))
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(writer._client.messages, "stream", return_value=ctx):
            content = writer._call_raw(system="s", user="u", max_retries=3)
        assert content == "选题 JSON 输出"

    def test_empty_content_retry(self, writer: NovelWriter) -> None:
        """200 但 content 空 → 重试 → 第 2 次成功"""
        msg_empty = _mock_anthropic_message("   ")  # 全空白 text
        msg_ok = _mock_anthropic_message("正常内容")

        ctx_empty = MagicMock()
        ctx_empty.__enter__ = MagicMock(
            return_value=MagicMock(get_final_message=MagicMock(return_value=msg_empty))
        )
        ctx_empty.__exit__ = MagicMock(return_value=False)

        ctx_ok = MagicMock()
        ctx_ok.__enter__ = MagicMock(
            return_value=MagicMock(get_final_message=MagicMock(return_value=msg_ok))
        )
        ctx_ok.__exit__ = MagicMock(return_value=False)

        with patch.object(
            writer._client.messages, "stream", side_effect=[ctx_empty, ctx_ok]
        ):
            with patch("src.novel_writer.time.sleep"):
                content = writer._call_raw(system="s", user="u", max_retries=3)
        assert content == "正常内容"

    def test_4xx_raises_immediately(self, writer: NovelWriter) -> None:
        """4xx → 立即抛"""
        error = anthropic.APIStatusError(
            message="bad", response=_mock_httpx_response(400), body={}
        )
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(side_effect=error)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(writer._client.messages, "stream", return_value=ctx):
            with pytest.raises(LLMError, match="4xx"):
                writer._call_raw(system="s", user="u", max_retries=3)


# ============ write_chapter ============
class TestWriteChapter:
    def test_happy_path(self, writer: NovelWriter) -> None:
        """字数合格 → 一次成功"""
        msg = _mock_anthropic_message(SAMPLE_CHAPTER_TEXT)
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(
            return_value=MagicMock(get_final_message=MagicMock(return_value=msg))
        )
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(writer._client.messages, "stream", return_value=ctx):
            draft = writer.write_chapter(
                chapter_idx=1,
                truth_snapshot={"chapter_title": "测试章", "chapter_goal": "目标"},
                style_guide={},
            )
        assert draft.word_count > 2800
        assert draft.raw_text == SAMPLE_CHAPTER_TEXT
        assert draft.cover_prompt  # 非空
        assert draft.stop_reason == "end_turn"
        assert draft.duration_s > 0

    def test_short_word_count_retry(self, writer: NovelWriter) -> None:
        """字数不足 → 重生 → 第 2 次长文本"""
        msg_short = _mock_anthropic_message(SAMPLE_SHORT_TEXT)
        msg_long = _mock_anthropic_message(SAMPLE_CHAPTER_TEXT)

        ctx_short = MagicMock()
        ctx_short.__enter__ = MagicMock(
            return_value=MagicMock(get_final_message=MagicMock(return_value=msg_short))
        )
        ctx_short.__exit__ = MagicMock(return_value=False)

        ctx_long = MagicMock()
        ctx_long.__enter__ = MagicMock(
            return_value=MagicMock(get_final_message=MagicMock(return_value=msg_long))
        )
        ctx_long.__exit__ = MagicMock(return_value=False)

        with (
            patch.object(
                writer._client.messages, "stream", side_effect=[ctx_short, ctx_long]
            ),
            patch("src.novel_writer.time.sleep"),
        ):
            draft = writer.write_chapter(
                chapter_idx=2,
                truth_snapshot={"chapter_title": "测试", "chapter_goal": "目标"},
                style_guide={},
            )
        assert draft.word_count > 2800

    def test_empty_content_raises(self, writer: NovelWriter) -> None:
        """v0.40 新增: 0 字硬校验 → LLMError 立即抛, 不再进 renderer"""
        msg_empty = _mock_anthropic_message("")  # 真正空

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(
            return_value=MagicMock(get_final_message=MagicMock(return_value=msg_empty))
        )
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(writer._client.messages, "stream", return_value=ctx):
            with patch("src.novel_writer.time.sleep"):
                with pytest.raises(LLMError, match="empty content"):
                    writer.write_chapter(
                        chapter_idx=3,
                        truth_snapshot={"chapter_title": "x", "chapter_goal": "y"},
                        style_guide={},
                    )

    def test_llm_error_after_retries(self, writer: NovelWriter) -> None:
        """LLM 一直失败 → 最终 LLMError"""
        error = anthropic.APIStatusError(
            message="server fail", response=_mock_httpx_response(500), body={}
        )
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(side_effect=error)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(writer._client.messages, "stream", return_value=ctx):
            with patch("src.novel_writer.time.sleep"):
                with pytest.raises(LLMError):
                    writer.write_chapter(
                        chapter_idx=4,
                        truth_snapshot={"chapter_title": "x", "chapter_goal": "y"},
                        style_guide={},
                    )

    def test_refusal_propagates_without_retry(self, writer: NovelWriter) -> None:
        """ContentFilterError → 不可重试, 立即抛"""
        msg = _mock_anthropic_message("敏感内容", stop_reason="refusal")
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(
            return_value=MagicMock(get_final_message=MagicMock(return_value=msg))
        )
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(writer._client.messages, "stream", return_value=ctx):
            with pytest.raises(ContentFilterError):
                writer.write_chapter(
                    chapter_idx=5,
                    truth_snapshot={"chapter_title": "x", "chapter_goal": "y"},
                    style_guide={},
                )


# ============ _compose_cover_prompt (v0.6 不变) ============
class TestCoverPrompt:
    def test_scifi_genre_detection(self, writer: NovelWriter) -> None:
        """科幻关键词 → cyberpunk / neon 风格"""
        prompt = writer._compose_cover_prompt(
            "星际旅行",
            {
                "category": "sci-fi",
                "keywords": ["赛博朋克", "未来", "人工智能"],
                "characters": [
                    {"name": "凯", "age": "30", "occupation": "飞行员", "key_artifact": "光剑"}
                ],
                "chapter_goal": "在赛博城市追逐战",
            },
        )
        assert "cyberpunk" in prompt.lower() or "neon" in prompt.lower()
        assert "no text" in prompt.lower() or "no chinese" in prompt.lower()

    def test_fantasy_genre(self, writer: NovelWriter) -> None:
        """玄幻关键词 → 中国风 / 仙雾"""
        prompt = writer._compose_cover_prompt(
            "剑破苍穹",
            {
                "category": "fantasy",
                "keywords": ["修仙", "道法", "仙侠"],
                "characters": [],
                "chapter_goal": "",
            },
        )
        # 应有中文风关键词
        assert any(kw in prompt.lower() for kw in ["chinese", "xian", "ancient", "mystical", "fog"])

    def test_romance_keywords_to_romance_style(self, writer: NovelWriter) -> None:
        """现代/都市/职场关键词 → romance 风格 (代码实际行为)"""
        prompt = writer._compose_cover_prompt(
            "都市风云",
            {
                "category": "modern",
                "keywords": ["职场", "都市"],
                "characters": [],
                "chapter_goal": "",
            },
        )
        # romance palette: warm pastel tones / soft golden hour
        assert "pastel" in prompt.lower() or "golden hour" in prompt.lower()

    def test_prompt_no_text_clause(self, writer: NovelWriter) -> None:
        """无论题材, prompt 必须有 'no text' 约束 (image-01 防文字渲染)"""
        prompt = writer._compose_cover_prompt(
            "Any", {"category": "unknown", "keywords": [], "characters": [], "chapter_goal": ""}
        )
        assert "no text" in prompt.lower()

    def test_prompt_3_4_aspect(self, writer: NovelWriter) -> None:
        """必须包含 3:4 aspect ratio"""
        prompt = writer._compose_cover_prompt(
            "X", {"category": "sci-fi", "keywords": [], "characters": [], "chapter_goal": ""}
        )
        assert "3:4" in prompt


# ============ 入口 ============
if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
