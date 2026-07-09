# ============================================================
# test_topic_gen.py - 单测 v0.2 P6.1
# ============================================================
# 覆盖:
#   - TopicCandidate dataclass: outline_hash 自动算 / title > 20 截断
#   - TopicGenerator.generate_candidates: 关键词模式 / 无关键词 / 超 10 拒
#   - _parse_json_array: <think> 块 / markdown 围栏 / 多余前后文
#   - _repair_inner_quotes: 中文字符串内 ASCII " → 中文弯引号
#   - JSON 解析失败抛 TopicGenError
# ============================================================

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.topic_gen import (
    TopicCandidate,
    TopicGenerator,
    TopicGenError,
)

# ============ Fixtures ============
SAMPLE_LLM_OUTPUT_JSON = json.dumps(
    [
        {"title": "星际迷航", "outline": "未来人类探索银河系, 遭遇外星文明", "genre_hint": "科幻"},
        {
            "title": "时间裂痕",
            "outline": "时间旅行者试图改变历史, 引发连锁反应",
            "genre_hint": "科幻",
        },
    ],
    ensure_ascii=False,
)


@pytest.fixture
def gen() -> TopicGenerator:
    return TopicGenerator()


# ============ TopicCandidate ============
class TestTopicCandidate:
    def test_outline_hash_auto(self) -> None:
        c = TopicCandidate(title="X", outline="大纲内容")
        assert c.outline_hash  # 自动算
        assert len(c.outline_hash) == 16

    def test_outline_hash_override(self) -> None:
        c = TopicCandidate(title="X", outline="Y", outline_hash="custom-hash-1234")
        assert c.outline_hash == "custom-hash-1234"

    def test_title_truncated_over_20(self) -> None:
        c = TopicCandidate(title="这是一段非常非常非常长的标题超过二十字了", outline="短")
        assert len(c.title) <= 20

    def test_default_source_auto(self) -> None:
        c = TopicCandidate(title="X", outline="Y")
        assert c.source == "auto"


# ============ generate_candidates (mock writer) ============
class TestGenerateCandidates:
    def test_happy_path_keywords(self, gen: TopicGenerator) -> None:
        gen.writer = MagicMock()
        gen.writer._call_raw.return_value = SAMPLE_LLM_OUTPUT_JSON

        result = gen.generate_candidates(keywords=["科幻", "太空"], n_candidates=2)
        assert len(result) == 2
        assert result[0].title == "星际迷航"
        assert "科幻" in result[0].keywords_used

    def test_no_keywords_mode(self, gen: TopicGenerator) -> None:
        gen.writer = MagicMock()
        gen.writer._call_raw.return_value = SAMPLE_LLM_OUTPUT_JSON

        result = gen.generate_candidates(keywords=None, n_candidates=2)
        assert len(result) == 2
        assert result[0].keywords_used == []

    def test_too_many_keywords_raises(self, gen: TopicGenerator) -> None:
        with pytest.raises(TopicGenError, match="关键词 ≤10"):
            gen.generate_candidates(keywords=[f"kw{i}" for i in range(11)])

    def test_llm_error_raises_topic_gen_error(self, gen: TopicGenerator) -> None:
        from src.novel_writer import LLMError

        gen.writer = MagicMock()
        gen.writer._call_raw.side_effect = LLMError("API fail")
        with pytest.raises(TopicGenError, match="M3 调用失败"):
            gen.generate_candidates(keywords=None)


# ============ _parse_json_array ============
class TestParseJsonArray:
    def test_clean_json(self) -> None:
        result = TopicGenerator._parse_json_array(SAMPLE_LLM_OUTPUT_JSON, expected_count=2)
        assert len(result) == 2

    def test_think_block_stripped(self) -> None:
        wrapped = f"<think>思考过程</think>\n{SAMPLE_LLM_OUTPUT_JSON}"
        result = TopicGenerator._parse_json_array(wrapped, expected_count=2)
        assert len(result) == 2

    def test_markdown_fence_stripped(self) -> None:
        wrapped = f"```json\n{SAMPLE_LLM_OUTPUT_JSON}\n```"
        result = TopicGenerator._parse_json_array(wrapped, expected_count=2)
        assert len(result) == 2

    def test_extra_text_around(self) -> None:
        wrapped = f"以下是候选:\n{SAMPLE_LLM_OUTPUT_JSON}\n结束。"
        result = TopicGenerator._parse_json_array(wrapped, expected_count=2)
        assert len(result) == 2

    def test_no_array_raises(self) -> None:
        with pytest.raises(TopicGenError, match="未找到 JSON 数组"):
            TopicGenerator._parse_json_array("纯文本无 JSON", expected_count=1)

    def test_invalid_json_raises(self) -> None:
        # '[' 和 ']' 都存在, 但 JSON 语法错 → 应走 JSON 解析失败分支
        with pytest.raises(TopicGenError, match="M3 输出未找到|JSON 解析"):
            TopicGenerator._parse_json_array("[{broken}]", expected_count=1)

    def test_missing_fields_raises(self) -> None:
        bad = json.dumps([{"title": "x"}])  # 缺 outline
        with pytest.raises(TopicGenError, match="缺 title/outline"):
            TopicGenerator._parse_json_array(bad, expected_count=1)


# ============ _repair_inner_quotes ============
class TestRepairInnerQuotes:
    def test_clean_json_unchanged(self) -> None:
        clean = SAMPLE_LLM_OUTPUT_JSON
        assert TopicGenerator._repair_inner_quotes(clean) == clean

    def test_inner_ascii_quote_replaced(self) -> None:
        """中文 outline 内含未转义 ASCII " → 中文弯引号"""
        bad = '[{"title": "x", "outline": "他说"你好"然后走了", "genre_hint": ""}]'
        repaired = TopicGenerator._repair_inner_quotes(bad)
        # 解析应成功
        parsed = json.loads(repaired)
        assert parsed[0]["outline"]  # 有内容

    def test_alternating_quotes(self) -> None:
        """连续内部 " → 交替替换 " 和"""
        bad = '[{"title": "a"b"c"d", "outline": "e"}]'
        repaired = TopicGenerator._repair_inner_quotes(bad)
        # 应有中文弯引号
        assert "\u201c" in repaired or "\u201d" in repaired


# ============ 入口 ============
if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))


# ============ Retry 行为 (7-9 加) ============
INVALID_JSON_NO_ARRAY = '/* 这里没有 JSON, 只有一段说明文字\n[\n  {"title": "我的分子会修仙"'  # 模拟 M3 偶发 invalid JSON
VALID_JSON_1 = json.dumps(
    [
        {
            "title": "我的分子会修仙",
            "outline": "主角程远意外合成 C99 簇, 开启硬核修仙",
            "genre_hint": "硬科幻",
        },
    ],
    ensure_ascii=False,
)


class TestGenerateCandidatesRetry:
    """7-9 加: M3 偶发 invalid JSON, 5 次 retry + exp backoff 容错"""

    def test_retry_on_invalid_json_then_success(self, monkeypatch: pytest.MonkeyPatch, gen: TopicGenerator) -> None:
        """第 1 次返回 invalid JSON → 第 2 次返回 valid → 整体成功"""
        gen.writer = MagicMock()
        gen.writer._call_raw.side_effect = [INVALID_JSON_NO_ARRAY, VALID_JSON_1]
        # 跳过 backoff (避免测试等 1+2+4+8=15s)
        monkeypatch.setattr("src.topic_gen.time.sleep", lambda _: None)

        result = gen.generate_candidates(keywords=["碳簇"], n_candidates=1)
        assert len(result) == 1
        assert result[0].title == "我的分子会修仙"
        # 实际应调用 2 次 (第 1 次失败, 第 2 次成功)
        assert gen.writer._call_raw.call_count == 2

    def test_retry_exhausts_raises_topic_gen_error(
        self, monkeypatch: pytest.MonkeyPatch, gen: TopicGenerator
    ) -> None:
        """5 次全部 invalid JSON → 最终抛 TopicGenError"""
        gen.writer = MagicMock()
        gen.writer._call_raw.side_effect = [INVALID_JSON_NO_ARRAY] * 5
        monkeypatch.setattr("src.topic_gen.time.sleep", lambda _: None)

        with pytest.raises(TopicGenError, match="M3 输出未找到|JSON 解析"):
            gen.generate_candidates(keywords=["x"], n_candidates=1, max_retries=5)
        # 5 次都试过
        assert gen.writer._call_raw.call_count == 5

    def test_retry_with_max_retries_2(self, monkeypatch: pytest.MonkeyPatch, gen: TopicGenerator) -> None:
        """max_retries=2 时, 2 次失败就抛错"""
        gen.writer = MagicMock()
        gen.writer._call_raw.side_effect = [INVALID_JSON_NO_ARRAY] * 2
        monkeypatch.setattr("src.topic_gen.time.sleep", lambda _: None)

        with pytest.raises(TopicGenError):
            gen.generate_candidates(keywords=["x"], n_candidates=1, max_retries=2)
        assert gen.writer._call_raw.call_count == 2

    def test_retry_backoff_exp_called(
        self, monkeypatch: pytest.MonkeyPatch, gen: TopicGenerator
    ) -> None:
        """失败中间 backoff time.sleep 被调, 指数 1+2+4=7 次 (4 次 retry 间隔)"""
        sleep_mock = MagicMock()
        monkeypatch.setattr("src.topic_gen.time.sleep", sleep_mock)

        gen.writer = MagicMock()
        gen.writer._call_raw.side_effect = [INVALID_JSON_NO_ARRAY] * 5

        with pytest.raises(TopicGenError):
            gen.generate_candidates(keywords=["x"], n_candidates=1, max_retries=5)
        # retry 5 次 → 4 次 backoff 中间等待
        # 但 last raise (第 5 次) 没有 sleep, 所以睡 4 次
        # backoff: 1, 2, 4, 8
        assert sleep_mock.call_count == 4
        sleep_args = [call.args[0] for call in sleep_mock.call_args_list]
        assert sleep_args == [1, 2, 4, 8]

    def test_retry_on_json_loads_failure_then_success(self, monkeypatch: pytest.MonkeyPatch, gen: TopicGenerator) -> None:
        """JSON 找到了 [ ] 但内部语法错 (json.loads 失败) → retry"""
        # 有完整 [...] 但内部 JSON 损坏 (e.g. 内部 missing comma)
        broken_json = '[{"title": "我的分子会修仙" "outline": "x"}]'  # 缺逗号
        gen.writer = MagicMock()
        gen.writer._call_raw.side_effect = [broken_json, VALID_JSON_1]
        monkeypatch.setattr("src.topic_gen.time.sleep", lambda _: None)

        result = gen.generate_candidates(keywords=["碳簇"], n_candidates=1)
        assert len(result) == 1
        assert gen.writer._call_raw.call_count == 2


    def test_keywords_too_many_no_retry(self, monkeypatch: pytest.MonkeyPatch, gen: TopicGenerator) -> None:
        """参数错 (关键词 >10) 直接抛, 不重试, 不调 LLM"""
        gen.writer = MagicMock()

        with pytest.raises(TopicGenError, match="关键词 ≤"):
            gen.generate_candidates(keywords=["x"] * 11, n_candidates=1)
        # 0 次 LLM 调用
        assert gen.writer._call_raw.call_count == 0
