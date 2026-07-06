"""
大任务 P2-3: Markdown 渲染器
==============================================
把章节正文 + 封面图 + 元数据 拼成 obsidian-journal posts 表的 content markdown

输入:
- raw_text:    NovelWriter 输出的 ~3000 字中文正文
- cover_url:   已上传到 obsidian-journal 资源库的封面图 URL
- chapter_idx: 章节号 (用于 markdown 内的 H2 锚点)
- chapter_title: 章节标题 (用于 H1)

输出:
- 单字符串 markdown, 形如:

    # 第 N 章 · {title}

    ![封面]({cover_url})

    {正文第一段 ~1500 字}

    ---

    {正文第二段 ~1500 字}

    ---

    {末段 (剩余字数)}

切分策略:
- 按句子切 (句号/问号/感叹号 + 换行)
- 单段 ≤ 1500 汉字 (避免 obsidian-journal 单章节过长)
- 段间隔用 `\n\n---\n\n` (Markdown 横线, 视觉清楚)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 单段最大字符数 (汉字 + 标点), 1500 是经验值, 避免 obsidian-journal 单章节过长
MAX_SECTION_CHARS = 1500


@dataclass(frozen=True)
class RenderedPost:
    """渲染结果 (供 publisher 拼装 HMAC body)"""

    title: str
    content_markdown: str
    excerpt: str  # 前 ~150 字作为 excerpt


def render(
    raw_text: str,
    cover_url: str,
    chapter_idx: int,
    chapter_title: str,
    *,
    max_section_chars: int = MAX_SECTION_CHARS,
) -> RenderedPost:
    """渲染单章节 markdown

    Args:
        raw_text:            章节正文 (~3000 字)
        cover_url:           封面图绝对 URL (从 cover_upload 返回)
        chapter_idx:         章节号 (1, 2, 3, ...)
        chapter_title:       章节标题
        max_section_chars:   单段最大字符数 (默认 1500)

    Returns:
        RenderedPost { title, content_markdown, excerpt }
    """
    # 1. 清理 raw_text: 去首尾空白 + 合并连续换行
    cleaned = _normalize_text(raw_text)

    # 2. 切分章节 (按段)
    sections = _split_into_sections(cleaned, max_chars=max_section_chars)
    if not sections:
        raise ValueError("raw_text 切分后为空, 请检查输入")

    # 3. 拼 markdown
    title = f"第 {chapter_idx} 章 · {chapter_title}"
    md_parts: list[str] = [f"# {title}", ""]

    if cover_url:
        md_parts.append(f"![{chapter_title} 封面]({cover_url})")
        md_parts.append("")

    md_parts.extend(_join_sections(sections))

    content_md = "\n".join(md_parts)

    # 4. excerpt: 前 ~150 字 (用 cleaned 文本, 不用 markdown, 避免含图片标记)
    excerpt = cleaned[:150].rstrip()
    if len(cleaned) > 150:
        excerpt += "…"

    return RenderedPost(title=title, content_markdown=content_md, excerpt=excerpt)


# --------------------------------------------------------------------------
# 内部 helper
# --------------------------------------------------------------------------

# 句末标点 (中文 + 英文)
_SENTENCE_END = re.compile(r"([。！？!?][\s\n]*|[\n]{2,})")


def _normalize_text(text: str) -> str:
    """清理文本: 去首尾空白 + 合并连续空行 + 去连续空白"""
    text = text.strip()
    # 合并 3+ 连续空行为 2 个 (即一个空段间隔)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _split_into_sections(text: str, *, max_chars: int) -> list[str]:
    """把长文本切成 ≤ max_chars 的段

    策略: 贪心累加句子, 超过 max_chars 就开新段。
    最后一段可能短于 max_chars。
    """
    # 句子切分: 按句末标点 + 空行 split, 保留分隔符
    parts = _SENTENCE_END.split(text)
    sentences: list[str] = []
    buf = ""
    for p in parts:
        if p is None:
            continue
        buf += p
        if _SENTENCE_END.match(p):
            sentences.append(buf.strip())
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())

    # 贪心分段
    sections: list[str] = []
    cur = ""
    for s in sentences:
        # 单句已经超 max_chars → 硬切
        if len(s) > max_chars:
            if cur:
                sections.append(cur)
                cur = ""
            # 硬切: 按 max_chars 切
            for i in range(0, len(s), max_chars):
                sections.append(s[i : i + max_chars])
            continue
        # cur + sentence 超限 → 开新段
        if len(cur) + len(s) > max_chars and cur:
            sections.append(cur)
            cur = s
        else:
            cur = (cur + "\n\n" + s).strip() if cur else s

    if cur:
        sections.append(cur)

    return sections


def _join_sections(sections: list[str]) -> list[str]:
    """段间用 `\\n\\n---\\n\\n` 拼接"""
    out: list[str] = []
    for i, sec in enumerate(sections):
        out.append(sec)
        if i < len(sections) - 1:
            out.append("")
            out.append("---")
            out.append("")
    return out
