"""
text_punct — 中文标点规范化 (P1 修复 7-7)
===========================================

M3 v2 倾向输出 ASCII 半角标点 (", . : ; ? ! ' \" 等), 中文章节需要全角 (，。：；？！""'')
才能在 obsidian-journal 等中文站点阅读起来不拥挤。

策略:
- 在 CJK 字符之间的 ASCII 标点 → 全角
- 中文与 ASCII 数字/字母交界 (如"3000字"中的逗号) 不动
- " " " ' ' (中文弯引号) → 「 」 (与项目已有风格一致)
- 重复标点 (". ." "。。" ) 折叠为单个全角
- 不动 markdown 标记 (# * [ ] 等)
- **顺手吃掉 ASCII 标点与 CJK 之间的小空白 (space/tab)**
  (中文排版规范: 标点紧贴汉字; \n 保留用于段落分隔)

仅处理下面这些字符, 其他 ASCII 字符 (a-z, 0-9, % 等) 保留原样。

依赖: 无 (纯 stdlib)
"""

from __future__ import annotations

import re

# ============== 映射表 ==============

# 7 个核心标点: ASCII -> 全角
_PUNCT_MAP: dict[str, str] = {
    ",": "，",
    ".": "。",
    ";": "；",
    ":": "：",
    "?": "？",
    "!": "！",
    '"': "「",  # 占位 — 实际会成对替换
    "'": "「",  # 占位
}

# 字符分类
_SMALL_WS = " \t\u3000"  # 标点与汉字之间可吞的小空白
_BIG_WS = "\n\r"  # 段落分隔的换行, 保留
_ALL_WS = _SMALL_WS + _BIG_WS


def _is_cjk(c: str) -> bool:
    """是否中日韩文字 (含汉字 + 日文假名 + 韩文, 标点判定用)"""
    if not c:
        return False
    cp = ord(c)
    return (
        0x4E00 <= cp <= 0x9FFF  # CJK 统一汉字
        or 0x3040 <= cp <= 0x30FF  # 日文假名
        or 0xAC00 <= cp <= 0xD7AF  # 韩文音节
        or 0x3400 <= cp <= 0x4DBF  # CJK 扩展 A
    )


def _is_cjk_like(c: str) -> bool:
    """CJK 或全角标点 (链式: 已替换的标点也算近邻)"""
    return _is_cjk(c) or c in "，。；：？！「」"


def normalize_cn_punctuation(text: str) -> str:
    """将中文语境下的 ASCII 半角标点规范为全角。

    规则 (按字符位置):
    - 在 CJK 字符"近邻"范围内的 ASCII 标点 → 全角 (近邻 = 跳过小空白的 CJK 字符)
    - 顺手吃掉 ASCII 标点两侧与 CJK 之间的小空白 (space/tab)
    - 已替换的全角标点视为 CJK 近邻, 链式处理重复 (?? → ？？ → 折叠 = ？)
    - 弯引号 " " ' ' → 「」, 与项目已有风格一致, 成对交替
    - 重复全角标点折叠 (如 '。。' -> '。')
    - 段落换行 \\n 保留

    Args:
        text: 原始文本

    Returns:
        标点规范化后的文本
    """
    if not text:
        return text

    out: list[str] = []
    n = len(text)
    quote_count = 0
    i = 0

    while i < n:
        ch = text[i]

        # 大空白 (\n \r) 保留, 用于段落分隔
        if ch in _BIG_WS:
            out.append(ch)
            i += 1
            continue

        # 小空白 (space/tab/全角空格) — 只在"被替换标点的邻接空白"时吞掉
        # 这里是普通位置: 先原样输出, 后续如果它处于"标点紧贴汉字"语境, 那段空白会在标点处理时一并被吞 (见 _eat_ws_around)
        if ch in _SMALL_WS:
            out.append(ch)
            i += 1
            continue

        # 判断 punct 是否要替换
        prev = _prev_cjk_like(out)
        nxt = _next_cjk(text, i + 1, n)
        adjacent = bool(prev or nxt)
        is_quote = ch in '"\u201c\u201d' or ch in "'\u2018\u2019"
        is_punct = ch in _PUNCT_MAP

        if (is_quote or is_punct) and adjacent:
            # 吞 out 末尾小空白 + 跳过后续连续小空白 (标点紧贴汉字)
            while out and out[-1] in _SMALL_WS:
                out.pop()
            if is_quote:
                out.append("「" if quote_count % 2 == 0 else "」")
                quote_count += 1
            else:
                out.append(_PUNCT_MAP[ch])
            i += 1
            # 跳过紧接着的小空白 (下次 _prev_cjk_like 看到的是刚加的全角标点)
            while i < n and text[i] in _SMALL_WS:
                i += 1
            continue

        # 其他字符原样
        out.append(ch)
        i += 1

    result = "".join(out)

    # 重复全角标点折叠
    result = re.sub(r"([。，；：？！])\1+", r"\1", result)
    result = re.sub(r"([「」])\1+", r"\1", result)

    return result


def _prev_cjk_like(out: list[str]) -> str:
    """从 out 末尾向回看 (skip 小空白), 第一个 CJK-like 字符"""
    j = len(out) - 1
    while j >= 0 and out[j] in _SMALL_WS:
        j -= 1
    if j < 0:
        return ""
    return out[j] if _is_cjk_like(out[j]) else ""


def _next_cjk(text: str, from_idx: int, n: int) -> str:
    """从 text[from_idx] 向右看 (skip 小空白), 第一个 CJK 字符"""
    j = from_idx
    while j < n and text[j] in _SMALL_WS:
        j += 1
    if j < n and _is_cjk(text[j]):
        return text[j]
    return ""


# ============================================================
# 7-8 P4: 「」 孤行合并 (L2 后处理)
# ============================================================
# M3 输出按 token 拆时偶现:
#   她低声说:「我做了一个梦,
#   梦里全是光。
#   」  ← 老板截图反例
# 中文排版规范: 引号紧贴文字; 段内引语可多行但首尾引号贴邻
# 这里我们 3 层防护:
#   L1: prompt 强化 (assets/prompts/system.txt 排版铁律)
#   L2: 本函数 (text_punct._merge_orphan_quotes) — normalize 后调用
#   L3: markdown_renderer._normalize_text 渲染前过一遍


# 3 个独立正则 (顺序重要):
#  1. 「\n+ → 「 (开引号后换行: 删)
#  2. \n+」 → 」 (闭引号前换行: 删)
#  3. 」\n\n+」 → 」」 (多空行折叠, 防 」 后空行再接 」)
_RE_OPEN_QUOTE_NEWLINE = re.compile(r"「\s*\n+")
_RE_NEWLINE_BEFORE_CLOSE = re.compile(r"\n+\s*」")
_RE_DUPLICATE_CLOSE = re.compile(r"」(\s*\n\s*){2,}」")


def _merge_orphan_quotes(text: str) -> str:
    """合并 「」 孤行 (3 层防护 L2)

    场景示例:
        反例:
            她低声说:「我做了一个梦,
            梦里全是光。
            」
        正例:
            她低声说:「我做了一个梦,
            梦里全是光。」

    规则:
        1. 「 后紧跟 1+ 换行 → 删 (开引号后直接接文字)
        2. 1+ 换行后紧跟 」 → 删 (闭引号前直接接文字)
        3. 」 与下一个 」 之间多空行 → 折叠 (罕见, 防御)
        4. 不动: 段首独立引语 (\n\n「 仍保留; 不破坏段落结构)

    Args:
        text: 已 normalize_cn_punctuation 的文本

    Returns:
        合并后的文本
    """
    if not text:
        return text
    # 1. 开引号后换行 → 删
    text = _RE_OPEN_QUOTE_NEWLINE.sub("「", text)
    # 2. 闭引号前换行 → 删
    text = _RE_NEWLINE_BEFORE_CLOSE.sub("」", text)
    # 3. 」 之间的多空行折叠
    text = _RE_DUPLICATE_CLOSE.sub("」」", text)
    return text
