"""
大任务 T-1: 选题生成器 TopicGenerator (v0.5 新增)
=================================================
基于关键词 / 无关键词 → 生成 N 个小说选题候选 (题目 + 大纲).

设计要点:
- 复用 NovelWriter._call_raw (M3 通用接口 + 3 次重试)
- 支持关键词模式 (1-10 个) 和无关键词模式 (LLM 自行脑洞)
- 候选数不限 (老板 D17 决策)
- JSON 严格解析 (含 <think> 块剥离 + ```json 围栏剥离)
- temperature 0.7 (低于创作 0.85, 避免飘移)
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field

from .novel_writer import LLMError, NovelWriter, _load_template

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

MAX_KEYWORDS = 10
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 4000


# --------------------------------------------------------------------------
# 异常
# --------------------------------------------------------------------------


class TopicGenError(Exception):
    """选题生成失败"""


# --------------------------------------------------------------------------
# 数据类
# --------------------------------------------------------------------------


@dataclass
class TopicCandidate:
    """单个选题候选.

    Attributes:
        title:         题目 (≤ 20 字)
        outline:       大纲 (300-500 字)
        keywords_used: 用了哪些关键词 (无关键词模式为空)
        genre_hint:    类型提示 (科幻 / 玄幻 / 都市 / 混合)
        source:        来源 ("user" | "hotspot" | "auto")
        outline_hash:  SHA256(outline)[:16], pool 去重用
    """

    title: str
    outline: str
    keywords_used: list[str] = field(default_factory=list)
    genre_hint: str = ""
    source: str = "auto"
    outline_hash: str = ""

    def __post_init__(self):
        if not self.outline_hash:
            self.outline_hash = hashlib.sha256(self.outline.encode()).hexdigest()[:16]
        if len(self.title) > 20:
            logger.warning(f"[TopicGen] 标题超 20 字: {self.title!r}, 截断")
            self.title = self.title[:20]


# --------------------------------------------------------------------------
# 主类
# --------------------------------------------------------------------------


class TopicGenerator:
    """v0.5 选题生成器.

    用法:
        gen = TopicGenerator()

        # 关键词模式
        candidates = gen.generate_candidates(
            keywords=["西部牛仔", "多巴胺星喵", "赛博朋克"],
            n_candidates=5,
        )

        # 无关键词模式 (LLM 自行脑洞)
        candidates = gen.generate_candidates(
            keywords=None,
            n_candidates=5,
        )
    """

    def __init__(self, writer: NovelWriter | None = None):
        self.writer = writer or NovelWriter()

    def generate_candidates(
        self,
        keywords: list[str] | None = None,
        *,
        n_candidates: int = 5,
        style_guide: dict | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        source: str = "user",
    ) -> list[TopicCandidate]:
        """生成 N 个选题候选.

        Args:
            keywords:     关键词列表 (1-10 个); None/空 = LLM 自行脑洞 (无关键词模式)
            n_candidates: 候选数 (默认 5, 不限上限)
            style_guide:  风格偏好 dict (可选, JSON 化后注入 prompt)
            temperature:  M3 温度 (默认 0.7, 结构化输出)
            max_tokens:   M3 输出 token 上限 (默认 4000, 5 候选 × 500 字)
            source:       候选来源标识 ("user" | "hotspot" | "auto")

        Returns:
            N 个 TopicCandidate 列表

        Raises:
            TopicGenError: 关键词 > 10 / M3 失败 / JSON 解析失败
        """
        # 关键词长度校验
        if keywords is not None and len(keywords) > MAX_KEYWORDS:
            raise TopicGenError(f"关键词 ≤{MAX_KEYWORDS}, 收到 {len(keywords)}")

        # 1. 加载 prompt 模板
        try:
            system = _load_template("topic_system.txt")
            user_template = _load_template("topic_user.txt")
        except FileNotFoundError as e:
            raise TopicGenError(f"Topic prompt 模板缺失: {e}") from e

        # 2. 注入 user 模板变量
        try:
            user = user_template.format(
                keywords=keywords if keywords else "(无 — LLM 自行脑洞科幻题材)",
                style_guide=json.dumps(style_guide or {}, ensure_ascii=False, indent=2),
                n_candidates=n_candidates,
            )
        except KeyError as e:
            raise TopicGenError(f"user prompt 模板变量缺失: {e}") from e

        # 3. 调 M3
        try:
            raw = self.writer._call_raw(
                system=system,
                user=user,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except LLMError as e:
            raise TopicGenError(f"M3 调用失败: {e}") from e

        # 4. 解析 JSON 数组
        parsed = self._parse_json_array(raw, expected_count=n_candidates)

        # 5. 包装为 TopicCandidate
        return [
            TopicCandidate(
                title=p["title"],
                outline=p["outline"],
                keywords_used=list(keywords) if keywords else [],
                genre_hint=p.get("genre_hint", ""),
                source=source,
            )
            for p in parsed
        ]

    @staticmethod
    def _parse_json_array(raw: str, expected_count: int) -> list[dict]:
        """从 M3 输出中提取 JSON 数组.

        处理:
        - <think>...</think> 块剥离
        - ```json ... ``` 围栏剥离
        - 多余前后文截取到第一个 [ 到最后一个 ]
        - **修复 outline/title 字段内未转义的 ASCII " (M3 中文场景常见)** ← 新增
        - JSON 解析
        """
        from .novel_writer import strip_think_block

        cleaned = strip_think_block(raw).strip()

        # 剥离 markdown ```json 围栏
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(line for line in lines if not line.strip().startswith("```"))

        # 找 [ 和 ]
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise TopicGenError(f"M3 输出未找到 JSON 数组: {cleaned[:300]}")

        json_str = cleaned[start : end + 1]

        # 修复: 字符串值内的未转义 ASCII " → 中文弯引号
        # M3 偶尔在 outline/title/genre_hint 内嵌套 ASCII 引号 (中文场景常见),
        # 导致 JSON 解析失败. 用 state machine 区分 字段边界引号 vs 字段值内引号.
        json_str_repaired = TopicGenerator._repair_inner_quotes(json_str)

        try:
            arr = json.loads(json_str_repaired)
        except json.JSONDecodeError as e:
            raise TopicGenError(
                f"JSON 解析失败 (含 repair): {e}; raw={json_str_repaired[:300]}"
            ) from e

        if not isinstance(arr, list) or len(arr) < 1:
            raise TopicGenError(f"JSON 不是非空数组: {arr!r}")

        # 字段校验
        for i, item in enumerate(arr):
            if not isinstance(item, dict):
                raise TopicGenError(f"第 {i} 项不是 dict: {item!r}")
            if "title" not in item or "outline" not in item:
                raise TopicGenError(f"第 {i} 项缺 title/outline: {item.keys()}")

        logger.info(f"[TopicGen] 解析 JSON 成功: {len(arr)} 个候选 (期望 {expected_count})")
        return arr

    @staticmethod
    def _repair_inner_quotes(json_str: str) -> str:
        """修复 JSON 字符串值内未转义的 ASCII " (M3 中文场景常见).

        Strategy: state machine 扫描, 区分 字段边界 " vs 字段值内 ".
        遇到未转义 " 且前一个 token 是 string 内部 → 替换为中文弯引号 "" (左右交替).
        """
        result = []
        in_string = False
        escape_next = False
        inner_quote_count = 0  # 用于交替替换为 " 和 "

        i = 0
        while i < len(json_str):
            ch = json_str[i]

            if escape_next:
                result.append(ch)
                escape_next = False
                i += 1
                continue

            if ch == "\\":
                result.append(ch)
                escape_next = True
                i += 1
                continue

            if ch == '"':
                if not in_string:
                    # 字符串开始
                    in_string = True
                    result.append(ch)
                else:
                    # 字符串结束 vs 内部未转义 " 判断:
                    # 看后面跳过空白后是  , } ] : 之一 → 字符串结束
                    j = i + 1
                    while j < len(json_str) and json_str[j] in " \t\n\r":
                        j += 1
                    if j < len(json_str) and json_str[j] in ",}]:":
                        # 字符串结束
                        in_string = False
                        inner_quote_count = 0
                        result.append(ch)
                    else:
                        # 内部未转义 ", 替换为中文弯引号
                        if inner_quote_count % 2 == 0:
                            result.append("\u201c")  # 左引号 "
                        else:
                            result.append("\u201d")  # 右引号 "
                        inner_quote_count += 1
                i += 1
                continue

            result.append(ch)
            i += 1

        return "".join(result)


# --------------------------------------------------------------------------
# 便捷函数
# --------------------------------------------------------------------------


def generate_one_shot(
    keywords: list[str] | None = None,
    *,
    n_candidates: int = 5,
    source: str = "user",
    **kwargs,
) -> list[TopicCandidate]:
    """一次性便捷接口."""
    gen = TopicGenerator()
    return gen.generate_candidates(
        keywords=keywords,
        n_candidates=n_candidates,
        source=source,
        **kwargs,
    )
