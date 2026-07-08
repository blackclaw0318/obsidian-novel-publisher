"""
markdown_renderer._normalize_text 兜底 (v0.3.2 P4 L3)
=====================================================
- render() 内部会过 _normalize_text, 触发 _merge_orphan_quotes
- 端到端: 老板截图反例经 render() 出来是干净的
- L3 边界: 段间空行 (」\n\n她说) 由 _normalize_text 折叠, 不归 _merge_orphan_quotes
"""
from __future__ import annotations

from src.markdown_renderer import render


class TestRenderOrphanQuoteFallback:
    def test_render_merges_orphan_close_quote(self):
        """老板截图反例经 render() 出来, 」 贴邻光。"""
        text = "她低声说:「我做了一个梦,\n梦里全是光。\n」"
        out = render(text, cover_url="", chapter_idx=1, chapter_title="测试章")
        md = out.content_markdown
        # _normalize_text 触发 L3 兜底
        assert "光。」" in md
        # 不应有独立的 \n」 行
        assert "\n」\n" not in md
        assert "\n\n」" not in md

    def test_render_merges_open_quote_newline(self):
        text = "她低声说:「\n我做了一个梦。\n」"
        out = render(text, cover_url="", chapter_idx=1, chapter_title="测试")
        md = out.content_markdown
        # 开引号后换行被吃
        assert "「我做了一个梦。" in md
        assert "「\n我" not in md

    def test_render_clean_text_unchanged(self):
        """已经干净的文本, render 后不变"""
        text = "她低声说:「我做了一个梦,\n梦里全是光。」"
        out = render(text, cover_url="", chapter_idx=1, chapter_title="测试")
        md = out.content_markdown
        assert "光。」" in md

    def test_three_layer_pipeline(self):
        """L1 prompt + L2 normalize 后 + L3 render 兜底 三层都走"""
        from src.text_punct import normalize_cn_punctuation, _merge_orphan_quotes

        # 输入: 已经是 「」 (模拟 normalize_cn_punctuation 已跑) + 老板反例排版
        text = "她低声说:「我做了一个梦,\n梦里全是光。\n」"
        # 模拟 L2 链路
        text = normalize_cn_punctuation(text)
        text = _merge_orphan_quotes(text)
        # L3: render 内 _normalize_text 兜底 (虽然已经干净, 应幂等)
        out = render(text, cover_url="", chapter_idx=1, chapter_title="测试")
        md = out.content_markdown
        # 三层后: 文本干净
        assert "光。」" in md
        assert "\n」" not in md

    def test_render_preserves_paragraph_breaks(self):
        """合法段落空行 (\n\n) 保留, 不被 _merge_orphan_quotes 吃"""
        text = "第一段内容。\n\n第二段内容。\n\n「引语。」"
        out = render(text, cover_url="", chapter_idx=1, chapter_title="测试")
        md = out.content_markdown
        # 段间 \n\n 保留
        assert "第一段内容。\n\n第二段内容。" in md
        # 段首引号独立行 (合法的: 段首引语)
        assert "「引语。」" in md
