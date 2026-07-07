# ============================================================
# test_novel_writer.py - 单测 v0.2 P6.1
# ============================================================
# 覆盖:
#   - 纯函数: count_chinese_chars / strip_think_block / _load_template
#   - NovelWriter.__init__ key 校验 / model 默认
#   - _call_raw: 4xx 立即抛 / 5xx 重试 / 空 content 重试 / 成功路径
#   - write_chapter: 字数不足重生 / 字数合格一次过 / 网络错重试
#   - _compose_cover_prompt: 4 类题材检测 + palette/style 注入
#   - _detect_genre + _style_for_genre: keyword 反推题材
# ============================================================

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.novel_writer import (
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
    """用 fake key + mock base_url 构造 NovelWriter"""
    return NovelWriter(api_key="sk-test-1234", base_url="https://mock.api/v1", model="test-model")


def _mock_m3_response(content: str, status: int = 200, usage: dict | None = None) -> MagicMock:
    """构造 mock requests.Response"""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": usage or {"prompt_tokens": 100, "completion_tokens": 1000},
    }
    resp.text = json.dumps(resp.json.return_value)
    resp.raise_for_status = MagicMock(
        side_effect=requests.exceptions.HTTPError(f"{status}") if status >= 400 else None
    )
    return resp


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

    def test_default_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINIMAXI_API_KEY", "sk-test")
        monkeypatch.delenv("MINIMAXI_TEXT_MODEL", raising=False)
        w = NovelWriter()
        assert w.model == "MiniMax-M3"

    def test_repr_no_key_leak(self) -> None:
        w = NovelWriter(api_key="sk-very-secret-key", base_url="https://x")
        r = repr(w)
        assert "sk-very-secret-key" not in r
        assert "sk-ver" in r  # fingerprint prefix only (first 6 chars)
        assert "-key" in r  # suffix (last 4 chars)


# ============ _call_raw ============
class TestCallRaw:
    def test_4xx_immediate_raise_no_retry(self, writer: NovelWriter) -> None:
        """4xx 参数错 → 立即抛 LLMError, 不重试"""
        resp_400 = _mock_m3_response("", status=400)
        resp_400.raise_for_status = MagicMock()  # raise_for_status 自己处理 4xx
        resp_400.json = MagicMock(side_effect=ValueError("not json"))

        with patch("src.novel_writer.requests.post", return_value=resp_400) as mock_post:
            with pytest.raises(LLMError, match="4xx"):
                writer._call_raw(system="s", user="u", max_retries=3)
            # 关键: 只调 1 次, 不重试
            assert mock_post.call_count == 1

    def test_5xx_retry_then_succeed(self, writer: NovelWriter) -> None:
        """5xx → 重试 → 第 2 次 200 成功"""
        resp_500 = _mock_m3_response("", status=500)
        resp_500.raise_for_status = MagicMock(side_effect=requests.exceptions.HTTPError("500"))
        resp_500.json = MagicMock(side_effect=ValueError("server error"))

        resp_200 = _mock_m3_response("OK content")

        with patch("src.novel_writer.requests.post", side_effect=[resp_500, resp_200]) as mock_post:
            with patch("src.novel_writer.time.sleep"):  # 跳过 sleep 加速
                content = writer._call_raw(system="s", user="u", max_retries=3)
            assert content == "OK content"
            assert mock_post.call_count == 2

    def test_5xx_retry_exhausted(self, writer: NovelWriter) -> None:
        """5xx 一直失败 → 重试耗尽抛 LLMError"""
        resp_500 = _mock_m3_response("", status=500)
        resp_500.raise_for_status = MagicMock(side_effect=requests.exceptions.HTTPError("500"))

        with patch("src.novel_writer.requests.post", return_value=resp_500):
            with patch("src.novel_writer.time.sleep"):
                with pytest.raises(LLMError, match="M3 调用失败"):
                    writer._call_raw(system="s", user="u", max_retries=2)

    def test_empty_content_retry(self, writer: NovelWriter) -> None:
        """200 但 content 空 → 重试"""
        resp_empty = _mock_m3_response("   ")  # 全空白
        resp_ok = _mock_m3_response("正常内容")

        with patch("src.novel_writer.requests.post", side_effect=[resp_empty, resp_ok]):
            with patch("src.novel_writer.time.sleep"):
                content = writer._call_raw(system="s", user="u", max_retries=3)
            assert content == "正常内容"

    def test_response_format_error(self, writer: NovelWriter) -> None:
        """响应缺 choices 字段 → LLMError"""
        resp_bad = MagicMock()
        resp_bad.status_code = 200
        resp_bad.json.return_value = {"error": "format"}
        resp_bad.raise_for_status = MagicMock()

        with patch("src.novel_writer.requests.post", return_value=resp_bad):
            with pytest.raises(LLMError, match="响应格式异常"):
                writer._call_raw(system="s", user="u", max_retries=1)


# ============ write_chapter ============
class TestWriteChapter:
    def test_happy_path(self, writer: NovelWriter) -> None:
        """字数合格 → 一次成功"""
        with patch.object(writer, "_call_raw", return_value=SAMPLE_CHAPTER_TEXT):
            draft = writer.write_chapter(
                chapter_idx=1,
                truth_snapshot={"chapter_title": "测试章", "chapter_goal": "目标"},
                style_guide={},
            )
        assert draft.word_count > 2800
        assert draft.raw_text == SAMPLE_CHAPTER_TEXT
        assert draft.cover_prompt  # 非空

    def test_short_word_count_retry(self, writer: NovelWriter) -> None:
        """字数不足 → 重生 → 第 2 次长文本"""
        with (
            patch.object(writer, "_call_raw", side_effect=[SAMPLE_SHORT_TEXT, SAMPLE_CHAPTER_TEXT]),
            patch("src.novel_writer.time.sleep"),
        ):
            draft = writer.write_chapter(
                chapter_idx=2,
                truth_snapshot={"chapter_title": "测试", "chapter_goal": "目标"},
                style_guide={},
            )
        assert draft.word_count > 2800

    def test_llm_error_after_retries(self, writer: NovelWriter) -> None:
        """LLM 一直失败 → 最终 LLMError"""
        with patch.object(writer, "_call_raw", side_effect=LLMError("fail")):
            with patch("src.novel_writer.time.sleep"):
                with pytest.raises(LLMError):
                    writer.write_chapter(
                        chapter_idx=3,
                        truth_snapshot={"chapter_title": "x", "chapter_goal": "y"},
                        style_guide={},
                    )


# ============ _compose_cover_prompt ============
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
