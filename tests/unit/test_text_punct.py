"""
test_text_punct.py — normalize_cn_punctuation 单测
"""

from __future__ import annotations

from src.text_punct import normalize_cn_punctuation


class TestBasicReplacement:
    """邻接 CJK 字符时 ASCII 标点 → 全角"""

    def test_comma_between_chinese(self):
        assert normalize_cn_punctuation("林述, 鹤") == "林述，鹤"

    def test_period_after_chinese(self):
        assert normalize_cn_punctuation("她啯了一口唾沫.") == "她啯了一口唾沫。"

    def test_colon_between_chinese(self):
        assert normalize_cn_punctuation("标签: 科幻") == "标签：科幻"

    def test_semicolon_between_chinese(self):
        assert normalize_cn_punctuation("呼吸; 沉重") == "呼吸；沉重"

    def test_question_mark(self):
        assert normalize_cn_punctuation("能听见吗?") == "能听见吗？"

    def test_exclamation(self):
        assert normalize_cn_punctuation("听到了!") == "听到了！"


class TestQuotePairs:
    """引号成对映射到 「」"""

    def test_double_quotes_pair(self):
        # 第一次 → 「, 第二次 → 」, 第三次 → 「, 第四次 → 」
        assert normalize_cn_punctuation('他"说"你"好"') == "他「说」你「好」"

    def test_single_quote_pair(self):
        assert normalize_cn_punctuation("他'说'你'好'") == "他「说」你「好」"

    def test_curly_quotes_pair(self):
        assert normalize_cn_punctuation("他说\u201c你好\u201d") == "他说「你好」"

    def test_quote_with_adjacent_english_unchanged(self):
        # 英文短语 "hello" 不动 (邻接不是 CJK)
        assert normalize_cn_punctuation('say "hello" please') == 'say "hello" please'


class TestAdjacencyRules:
    """标点必须至少邻接一个 CJK 字符才替换"""

    def test_ascii_phrase_preserved(self):
        assert normalize_cn_punctuation("hello, world") == "hello, world"

    def test_mixed_chinese_english(self):
        # "3000字" 里的 "字" 是 CJK, 数字 3000 不是, 标点不在 → 但逗号邻接字也算
        assert normalize_cn_punctuation("写满 3000 字, 不, 算") == "写满 3000 字，不，算"

    def test_punctuation_at_start(self):
        # 句首标点邻接 CJK → 替换
        assert normalize_cn_punctuation(",她开始") == "，她开始"

    def test_punctuation_at_end(self):
        # 句末标点邻接 CJK → 替换
        assert normalize_cn_punctuation("结束了.") == "结束了。"


class TestCollapseRepeats:
    """重复全角标点折叠为单个"""

    def test_double_period(self):
        assert normalize_cn_punctuation("结束。。") == "结束。"

    def test_triple_comma(self):
        assert normalize_cn_punctuation("等，，，待") == "等，待"

    def test_double_question(self):
        assert normalize_cn_punctuation("什么??") == "什么？"

    def test_collapse_quote_pair(self):
        assert normalize_cn_punctuation("「「开始」」") == "「开始」"


class TestRealExcerpt:
    """老板截图里的真实样例"""

    def test_excerpt_punctuation(self):
        # 老板截图里 2.png 的 1-3 行:
        # 」她啯了一口唾沫,洞唇膏的薄荷味在舌尖散开。
        # 「不是单核震荡。
        # 是……多核。
        text = (
            "\u300d她啯了一口唾沫,洞唇膏的薄荷味在舌尖散开。\n"
            "\u300c不是单核震荡。\n"
            "是……多核。\n"
        )
        result = normalize_cn_punctuation(text)
        assert "，" in result  # ASCII 逗号 → 全角
        assert "," not in result  # 没有半角逗号残留
        # 句号保持全角
        assert "。" in result
        # 省略号保持
        assert "……" in result


class TestMarkdownNotMangled:
    """不破坏 markdown 结构"""

    def test_hash_heading(self):
        # 标题 # 不动
        assert normalize_cn_punctuation("# 第 1 章") == "# 第 1 章"

    def test_markdown_link(self):
        # 链接 []() 里的标点不动
        assert normalize_cn_punctuation("[点击](http://x.com)") == "[点击](http://x.com)"


class TestEmptyAndNoOp:
    """空串 + 已是全角 不动"""

    def test_empty(self):
        assert normalize_cn_punctuation("") == ""

    def test_already_fullwidth(self):
        assert normalize_cn_punctuation("已经，使用。全角") == "已经，使用。全角"
