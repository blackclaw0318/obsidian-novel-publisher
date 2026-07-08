"""
text_punct._merge_orphan_quotes 单测 (v0.3.2 P4)
=================================================
- 「\\n 删换行 (开引号后)
- \\n」 删换行 (闭引号前)
- 老板截图反例 → 正例
- 段首独立引语保留
- 多个嵌套引号
"""
from __future__ import annotations

import pytest

from src.text_punct import _merge_orphan_quotes


class TestMergeOrphanQuotes:
    def test_open_quote_newline_removed(self):
        """开引号后紧跟换行 → 删换行"""
        text = "她低声说:「\n我做了一个梦。"
        out = _merge_orphan_quotes(text)
        assert out == "她低声说:「我做了一个梦。"

    def test_close_quote_newline_removed(self):
        """闭引号前换行 → 删换行"""
        text = "梦里全是光。\n」"
        out = _merge_orphan_quotes(text)
        assert out == "梦里全是光。」"

    def test_boss_screenshot_case(self):
        """老板截图的 3 行反例 → 1 行正例"""
        text = "她低声说:「我做了一个梦,\n梦里全是光。\n」"
        out = _merge_orphan_quotes(text)
        assert "光。」" in out
        assert "\n」" not in out
        assert "\n\n」" not in out

    def test_multi_line_quote_preserves_internal_newlines(self):
        """引语内部多行保留 (叙事需要), 首尾紧贴"""
        text = "他忽然说道:「第一行\n第二行\n第三行\n」"
        out = _merge_orphan_quotes(text)
        # 首尾紧贴, 中间 1 个换行保留 (开引号后 1 个换行被吃)
        # 实际: 「\n 被吃 → 「第一行\n第二行\n第三行」
        # 然后 \n」 被吃 → 「第一行\n第二行\n第三行」
        assert out == "他忽然说道:「第一行\n第二行\n第三行」"
        # 内部 2 个换行保留
        assert out.count("\n") == 2

    def test_paragraph_start_quote_preserved(self):
        """段首独立引语 (\n\n「) 保留 (不破坏段落结构)"""
        text = "她没再说话。\n\n「怎么?」林深抬头看她。"
        out = _merge_orphan_quotes(text)
        # \n\n 在 「 之前 → 保留 (这是段间空行, 不是引号内换行)
        assert "\n\n「" in out or "\n\n" in out

    def test_multiple_quotes_in_text(self):
        """多对引号都合并"""
        text = "他问:「你去哪?\n」她说:「月球。\n」"
        out = _merge_orphan_quotes(text)
        assert "去哪?」" in out
        assert "月球。」" in out
        # 2 个 \n」 都没了
        assert out.count("\n」") == 0

    def test_no_quotes_unchanged(self):
        """没引号的文本不动"""
        text = "她做了个梦。\n梦里全是光。\n她醒了。"
        out = _merge_orphan_quotes(text)
        assert out == text

    def test_empty_string(self):
        assert _merge_orphan_quotes("") == ""

    def test_only_open_quote(self):
        """只有开引号, 不动"""
        text = "她说:「"
        out = _merge_orphan_quotes(text)
        assert out == text

    def test_only_close_quote(self):
        """只有闭引号, 不动"""
        text = "光。」"
        out = _merge_orphan_quotes(text)
        assert out == text

    def test_close_then_close_with_blank_lines(self):
        """」 与 」 之间的多空行折叠 (L2 自身职责, 仅管两个 」 邻接)"""
        text = "他说:「第一段\n」\n\n\n」"
        out = _merge_orphan_quotes(text)
        # 两个 」 邻接时, 之间的多空行折叠
        assert "\n\n\n" not in out
        assert "」」" in out

    def test_close_to_next_speaker_blank_lines_unchanged(self):
        """」 到下一说话人的多空行 — 不归 L2 管, L3 (renderer) 才折"""
        text = "他说:「第一段\n」\n\n\n她说:「第二段\n」"
        out = _merge_orphan_quotes(text)
        # L2 只吃 「\n 和 \n」, 段间多空行由 L3 折
        # 这里确认 L2 不报: 单独 \n\n\n 由 L3 折
        assert "「第一段」" in out  # L2 自身
        assert "「第二段」" in out  # L2 自身
        # 3+ 换行保留 (L2 不动, L3 才动)

    def test_preserves_legitimate_double_newlines(self):
        """合法段落空行 (2 个换行) 保留"""
        text = "第一段内容。\n\n第二段内容。\n\n「引语。」"
        out = _merge_orphan_quotes(text)
        # 段间 \n\n 保留
        assert "第一段内容。\n\n第二段内容。" in out

    def test_real_chapter_with_normalize_first(self):
        """模拟真实链路: 输入已经是 「」 (模拟 normalize_cn_punctuation 已跑) → _merge_orphan_quotes"""
        from src.text_punct import normalize_cn_punctuation
        # 输入是已 normalize 后的 (CJK 邻接的 「」, M3 输出 ASCII 「 通常不在 CJK 邻接, 需手动转)
        text = '她低声说:「我做了一个梦,\n梦里全是光。\n」'
        # 1. 全角化 (本测试输入已全角化, normalize 走一遍幂等)
        text = normalize_cn_punctuation(text)
        # 2. 合并孤行
        text = _merge_orphan_quotes(text)
        # 期望: 全部紧贴
        assert "光。」" in text
        assert "\n」" not in text
        assert "\n\n」" not in text
