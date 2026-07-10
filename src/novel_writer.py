"""
大任务 B: 写作引擎 NovelWriter (v0.40 重构: Anthropic 兼容 + stream + M2.7)
==========================================================================
封装 minimax M2.7 API 调用 (Anthropic 兼容协议), 读取 prompt 模板,
生成单章正文 (3000 字) + 封面 prompt。

v0.40 重大变更 (2026-07-10, B+C 方案):
----------------------------------------------------------------
旧 (v0.34)                                    新 (v0.40)
---------------------------------------------- ---------------------------------------
requests.post + OpenAI 兼容 (/v1/chat/        anthropic SDK + Anthropic 兼容
  completions)                                   (/anthropic/v1/messages)
非流式 (等 180s 拿完整响应)                    stream context manager (实时 first-token
                                                检测, fail-fast 可选)
拿不到 stop_reason (空响应无法诊断)            stop_reason: end_turn / max_tokens /
                                                refusal / tool_use (精确分类)
M3 模型 (1M 上下文 Coding 优化)                M2.7 模型 (204K 通用任务, 60 TPS,
                                                写小说对口)
max_tokens 4000 (thinking 吃光)                 max_tokens 8000 (留 thinking buffer)
接受 0 字进 renderer (renderer 切分空)         0 字硬校验立即抛 LLMError (不再进下一阶段)
默认 min_word_count 2000 (放宽)                 2700 (v0.34 原值, 严控质量)
prev_chapter_summary 永远 fallback 默认值       (publisher.py 已注入 — 见 P2 TODO)

输出契约 (ChapterDraft):
- raw_text:      ~3000 ± 200 中文字符的纯文本小说正文
- cover_prompt:  适配 minimax image-01 的英文封面 prompt (3:4, no text)
- word_count:    raw_text 的中文字符数
- usage:         {input_tokens, output_tokens} 监控用
- stop_reason:   end_turn / max_tokens / refusal / tool_use
- duration_s:    本次调用耗时 (秒)

依赖: anthropic>=0.40.0, python-dotenv (已在 .venv 装好)
"""

from __future__ import annotations

import os
import pathlib
import re
import time
from dataclasses import dataclass
from typing import Any

import anthropic
from dotenv import load_dotenv

# 模块加载时一次性加载 .env (确保 key 已注入)
load_dotenv()

PROMPT_DIR = pathlib.Path(__file__).parent.parent / "assets" / "prompts"


# --------------------------------------------------------------------------
# 异常层次
# --------------------------------------------------------------------------


class LLMError(Exception):
    """minimax LLM 调用失败 (重试耗尽 / 参数错 / 协议错 / 0 字输出)"""


class ContentFilterError(LLMError):
    """stop_reason == 'refusal' (内容审查触发) — 不可重试, 改 prompt 才行"""


# --------------------------------------------------------------------------
# 数据契约
# --------------------------------------------------------------------------


@dataclass
class ChapterDraft:
    raw_text: str
    cover_prompt: str
    word_count: int
    usage: dict | None = None  # token 计数
    stop_reason: str | None = None  # 监控 / 调试用
    duration_s: float | None = None  # 本次调用耗时 (秒)


# --------------------------------------------------------------------------
# 纯函数
# --------------------------------------------------------------------------


def _load_template(name: str) -> str:
    """从 assets/prompts/ 加载 prompt 模板"""
    path = PROMPT_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"prompt 模板缺失: {path}")
    return path.read_text(encoding="utf-8")


def count_chinese_chars(text: str) -> int:
    """统计中文字符数 (排除标点/英文/空白, 接近"字数"概念)"""
    return sum(1 for c in text if "\u4e00" <= c <= "\u9fff")


def strip_think_block(text: str) -> str:
    """剥除残留的 XML 思考块标签 (Anthropic 协议下 ThinkingBlock 已在
    _call_anthropic_stream 独立剥过, 此处做兜底, 兼容 M2.7 偶发 XML 标签残留).

    兼容格式:
    - <think>...</think> (官方)
    - <reasoning>...</reasoning>
    - <thinking>...</thinking>
    - 【思考】...【/思考】
    - [思考]...[/思考]
    """
    patterns = [
        r"<think>.*?</think>",
        r"<reasoning>.*?</reasoning>",
        r"<thinking>.*?</thinking>",
        r"【思考】.*?【/思考】",
        r"\[思考\].*?\[/思考\]",
    ]
    for p in patterns:
        text = re.sub(p, "", text, flags=re.DOTALL)
    return text.strip()


# --------------------------------------------------------------------------
# 主体: NovelWriter
# --------------------------------------------------------------------------


class NovelWriter:
    """单例式 LLM 客户端 (本章一次写一章, 不并发)

    v0.40 改: 用 anthropic SDK + Anthropic 兼容协议
    """

    # 7-10 改: M3 → M2.7 (M3 是 Coding/Agent 优化, 写小说非其优势场景)
    DEFAULT_MODEL = "MiniMax-M2.7"
    # 7-10 改: /v1 → /anthropic (Anthropic 兼容协议, 支持 thinking + stop_reason)
    DEFAULT_BASE_URL = "https://api.minimaxi.com/anthropic"
    DEFAULT_TEMPERATURE = 0.9
    # 7-10 改: 4000 → 8000 (留 thinking 块 buffer ~1500-2000 tokens, 防截断 0 字)
    DEFAULT_MAX_TOKENS = 8000
    # 7-10 改: 180 → 600 (M2.7 60 TPS × 8000 tokens ≈ 133s 最坏, 给 600s 留余量)
    DEFAULT_TIMEOUT = 600
    DEFAULT_RETRIES = 3
    # 7-10 改: 2000 → 2700 (v0.34 原值, 严控质量 — 治标修复曾临时放宽, 现回归)
    DEFAULT_MIN_WORD_COUNT = 2700

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key or os.environ.get("MINIMAXI_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "MINIMAXI_API_KEY 未配置 (.env 或环境变量)。"
                "凭据安全协议见 OPERATIONS §1"
            )
        self.base_url = (
            base_url
            or os.environ.get("MINIMAXI_BASE_URL", self.DEFAULT_BASE_URL)
        ).rstrip("/")
        self.model = model or os.environ.get("MINIMAXI_TEXT_MODEL", self.DEFAULT_MODEL)
        # 不打印 key (避免进日志) — 只打印前后缀摘要
        self._key_fingerprint = (
            f"{self.api_key[:6]}...{self.api_key[-4:]} (len={len(self.api_key)})"
        )
        # anthropic client (单例, 我们自己 retry, 不让 SDK 内部重试)
        self._client = anthropic.Anthropic(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.DEFAULT_TIMEOUT,
            max_retries=0,
        )
        self._last_usage: dict[str, Any] = {}

    def __repr__(self) -> str:
        return (
            f"NovelWriter(model={self.model}, base_url={self.base_url}, "
            f"key={self._key_fingerprint})"
        )

    # ------------------------------------------------------------------
    # 公开 API: write_chapter
    # ------------------------------------------------------------------

    def write_chapter(
        self,
        chapter_idx: int,
        truth_snapshot: dict,
        style_guide: dict,
        *,
        max_retries: int = DEFAULT_RETRIES,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        min_word_count: int = DEFAULT_MIN_WORD_COUNT,
        max_word_count: int = 3200,
    ) -> ChapterDraft:
        """写一章。失败/字数不达自动重试 max_retries 次。

        v0.40 关键改: 字数不足 OR 0 字 → 立即抛 LLMError, **不再接受 0 字进 renderer**

        Args:
            chapter_idx:     章节号 (从 1 开始)
            truth_snapshot:  当前小说的 truth 快照 (人物/世界观/伏笔), dict
            style_guide:     风格指南 (文风/语气/字数偏好), dict
            max_retries:     失败重试次数 (默认 3)
            temperature:     M2.7 温度参数 (默认 0.9)
            max_tokens:      M2.7 输出 token 上限 (默认 8000)
            min_word_count:  字数下限, 不足则重生 (默认 2700)
            max_word_count:  字数上限, 超过则接受 (允许)

        Returns:
            ChapterDraft(raw_text, cover_prompt, word_count, usage, stop_reason, duration_s)

        Raises:
            LLMError: 重试耗尽 / 0 字 / 4xx 参数错
            ContentFilterError: stop_reason == 'refusal' (不可重试, 需改 prompt)
        """
        last_err: Exception | None = None
        for attempt in range(max_retries):
            try:
                draft = self._call_m3(
                    chapter_idx=chapter_idx,
                    truth_snapshot=truth_snapshot,
                    style_guide=style_guide,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                # 0 字硬校验 (v0.40 新增) — 立即抛, 不再让 0 字进 renderer
                if not draft.raw_text or not draft.raw_text.strip():
                    raise LLMError(
                        f"empty content after strip (attempt {attempt+1}/{max_retries}, "
                        f"stop_reason={draft.stop_reason})"
                    )
                # 字数下限
                if draft.word_count < min_word_count:
                    if attempt < max_retries - 1:
                        wait = 2**attempt
                        print(
                            f"[novel_writer] 字数不足 {draft.word_count}<{min_word_count}, "
                            f"重生 {attempt+1}/{max_retries}, 等 {wait}s "
                            f"(stop_reason={draft.stop_reason}, duration={draft.duration_s:.1f}s)"
                        )
                        time.sleep(wait)
                        continue
                    else:
                        raise LLMError(
                            f"min_word_count not met after {max_retries} retries: "
                            f"got {draft.word_count}, need {min_word_count}, "
                            f"stop_reason={draft.stop_reason}"
                        )
                return draft
            except ContentFilterError:
                # 不可重试 — 立即抛
                raise
            except LLMError as e:
                last_err = e
                if attempt < max_retries - 1:
                    wait = 2**attempt
                    print(f"[novel_writer] 重试 {attempt+1}/{max_retries}, 等 {wait}s: {e}")
                    time.sleep(wait)
                else:
                    raise

        # unreachable
        raise LLMError(f"unreachable: {last_err}")

    # ------------------------------------------------------------------
    # 内部: M2.7 调用 (anthropic stream) + prompt 渲染 + 后处理
    # ------------------------------------------------------------------

    def _call_m3(
        self,
        *,
        chapter_idx: int,
        truth_snapshot: dict,
        style_guide: dict,
        temperature: float,
        max_tokens: int,
    ) -> ChapterDraft:
        system_prompt = _load_template("system.txt")
        user_template = _load_template("user.txt")

        # user 模板变量注入
        chapter_title = truth_snapshot.get("chapter_title", f"第{chapter_idx}章")
        try:
            user_prompt = user_template.format(
                chapter_idx=chapter_idx,
                total_chapters=truth_snapshot.get("total_chapters", 16),
                chapter_title=chapter_title,
                chapter_goal=truth_snapshot.get("chapter_goal", ""),
                truth_snapshot=self._json_dumps(truth_snapshot),
                style_guide=self._json_dumps(style_guide),
                prev_chapter_summary=truth_snapshot.get(
                    "prev_chapter_summary", "(无前章, 这是开篇)"
                ),
            )
        except KeyError as e:
            # 模板里 { } 没被全部替换 → 4xx 类参数错, 立即抛 (不重试)
            raise LLMError(f"user prompt 模板变量缺失: {e}") from e

        # anthropic stream 调用 — 拿 text + stop_reason
        text, stop_reason, usage, duration_s = self._call_anthropic_stream(
            system=system_prompt,
            user=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # 兜底 strip (防 XML 标签残留, 正常情况下 _call_anthropic_stream 已剥过)
        clean_text = strip_think_block(text)
        word_count = count_chinese_chars(clean_text)

        # 封面 prompt: 由本章主题 + 主角 + 关键道具 (v0.6 重写后不变)
        cover_prompt = self._compose_cover_prompt(chapter_title, truth_snapshot)

        return ChapterDraft(
            raw_text=clean_text,
            cover_prompt=cover_prompt,
            word_count=word_count,
            usage=usage,
            stop_reason=stop_reason,
            duration_s=duration_s,
        )

    def _call_anthropic_stream(
        self,
        *,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, str, dict, float]:
        """anthropic stream 调用 — B+C 方案核心

        Returns:
            (text, stop_reason, usage, duration_s)

        Raises:
            LLMError: 4xx / 5xx / 连接错 / 超时 / 协议错
            ContentFilterError: stop_reason == 'refusal' (不可重试)

        协议细节:
        - endpoint: {base_url}/v1/messages
        - 请求: model + max_tokens + system + messages + temperature
        - 响应 content: [ThinkingBlock, TextBlock, ...] (按 type 过滤)
        - 响应 stop_reason: end_turn / max_tokens / refusal / tool_use
        - 错误: APIStatusError (4xx/5xx) / APIConnectionError / APITimeoutError / APIError
        """
        started = time.monotonic()
        try:
            with self._client.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                temperature=temperature,
            ) as stream:
                # 拿 final 完整消息 (含 stop_reason + usage)
                final = stream.get_final_message()
                duration_s = time.monotonic() - started
        except anthropic.APIStatusError as e:
            # 4xx / 5xx
            if 400 <= e.status_code < 500:
                raise LLMError(f"M2.7 4xx ({e.status_code}): {e.message[:500]}") from e
            # 5xx → 上层 retry
            raise LLMError(f"M2.7 5xx ({e.status_code}): {e.message[:500]}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"M2.7 连接错: {e}") from e
        except anthropic.APITimeoutError as e:
            raise LLMError(f"M2.7 timeout ({self.DEFAULT_TIMEOUT}s): {e}") from e
        except anthropic.APIError as e:
            raise LLMError(f"M2.7 协议错: {e}") from e

        # 解析 content blocks — 只取 text (skip thinking / tool_use)
        text_parts: list[str] = []
        for block in final.content:
            # block.type: "thinking" | "text" | "tool_use" | ...
            if getattr(block, "type", None) == "text":
                text_parts.append(getattr(block, "text", "") or "")
            # thinking / tool_use 跳过 (不算入正文)
        text = "".join(text_parts)

        stop_reason = getattr(final, "stop_reason", None) or "unknown"

        usage_obj = getattr(final, "usage", None)
        usage = {
            "input_tokens": getattr(usage_obj, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage_obj, "output_tokens", 0) or 0,
        }
        self._last_usage = usage

        # content filter / refusal 单独抛 (不可重试)
        if stop_reason == "refusal":
            raise ContentFilterError(
                f"M2.7 内容审查触发 (refusal): {text[:200] if text else '(empty)'}"
            )

        return text, stop_reason, usage, duration_s

    # ------------------------------------------------------------------
    # 通用接口: _call_raw (供 topic_gen 等模块复用)
    # ------------------------------------------------------------------

    def _call_raw(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.85,
        max_tokens: int = 2000,
        max_retries: int = DEFAULT_RETRIES,
    ) -> str:
        """通用 M2.7 调用接口 (供 topic_gen 等其他模块复用).

        v0.40 改: 走 anthropic 协议, 不用 stop_reason 分类 (topic_gen 不需要精细控制)

        Returns:
            M2.7 返回的 raw 文本 (thinking 块已剥, 调用方按需处理)

        Raises:
            LLMError: 4xx 立即抛 / 5xx + 空响应 重试耗尽 / 响应格式异常
        """
        last_err: Exception | None = None
        for attempt in range(max_retries):
            try:
                with self._client.messages.stream(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    temperature=temperature,
                ) as stream:
                    final = stream.get_final_message()
                    duration_s = time.monotonic()
                    self._last_usage = {
                        "input_tokens": getattr(final.usage, "input_tokens", 0) or 0,
                        "output_tokens": getattr(final.usage, "output_tokens", 0) or 0,
                        "duration_s": round(duration_s, 2),
                    }
                    # 拼 text (skip thinking / tool_use)
                    text = "".join(
                        getattr(b, "text", "") or ""
                        for b in final.content
                        if getattr(b, "type", None) == "text"
                    )
                    if not text or not text.strip():
                        raise LLMError(
                            f"M2.7 empty content (attempt {attempt+1}/{max_retries})"
                        )
                    return text
            except anthropic.APIStatusError as e:
                last_err = e
                if 400 <= e.status_code < 500:
                    # 4xx 参数错: 立即抛
                    raise LLMError(f"M2.7 4xx ({e.status_code}): {e.message[:500]}") from e
                # 5xx → 重试
                if attempt < max_retries - 1:
                    wait = 2**attempt
                    print(f"[_call_raw] 5xx 重试 {attempt+1}/{max_retries}, 等 {wait}s")
                    time.sleep(wait)
                    continue
            except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
                last_err = e
                if attempt < max_retries - 1:
                    wait = 2**attempt
                    print(
                        f"[_call_raw] {type(e).__name__} 重试 {attempt+1}/{max_retries}, "
                        f"等 {wait}s"
                    )
                    time.sleep(wait)
                    continue
            except LLMError as e:
                # empty content 重试
                last_err = e
                if "empty content" in str(e) and attempt < max_retries - 1:
                    wait = 2**attempt
                    print(f"[_call_raw] empty 重试 {attempt+1}/{max_retries}, 等 {wait}s")
                    time.sleep(wait)
                    continue
                raise
        raise LLMError(f"M2.7 调用失败 {max_retries} 次: {last_err}")

    # ------------------------------------------------------------------
    # 内部: 封面 prompt 构造 (v0.6 不变, 兼容切模型)
    # ------------------------------------------------------------------

    def _compose_cover_prompt(self, chapter_title: str, truth_snapshot: dict) -> str:
        """生成 minimax image-01 的英文封面 prompt (3:4, no text)

        v0.6 重写: genre-aware + 角色锁定 + 场景细节 + 调色板自适应
        老板反馈 v0.5 封面丑, 原因: prompt 太简, 缺角色/场景/调色板
        修复: 根据小说 category + chapter_goal + characters 生成精细 prompt

        支持题材 (genre→palette/style 映射):
        - 修仙/玄幻/武侠/都市玄幻: 中国风, 古韵, 仙雾, 道法
        - 科幻/赛博/末世: cyberpunk neon, sci-fi tech, futuristic
        - 爱情/现代/职场: 现代写实, 时尚, 都市
        - 悬疑/惊悚/恐怖: 暗调, 哥特, 哥德, 蒸汽

        Args:
            chapter_title:  章节标题 (例: "第119号")
            truth_snapshot: 含 category/world/characters/chapter_goal/keywords

        Returns:
            英文 prompt, 3:4, no text
        """
        # 1. 题材判定 (从 category + keywords 反推)
        category = (truth_snapshot.get("category") or "").lower()
        keywords = truth_snapshot.get("keywords") or []
        genre = self._detect_genre(category, keywords)

        # 2. 主角描述 (取第一个主角)
        characters = truth_snapshot.get("characters") or []
        protagonist = ""
        if characters and isinstance(characters[0], dict):
            name = characters[0].get("name", "")
            age = characters[0].get("age", "")
            occ = characters[0].get("occupation", "")
            key_art = characters[0].get("key_artifact", "")
            # 主角服饰 + 道具
            protagonist = f"a {age}-year-old {occ} named {name}" if age else f"a {occ} named {name}"
            if key_art:
                protagonist += f" holding {key_art}"

        # 3. 场景细节 (从 chapter_goal 提炼)
        chapter_goal = truth_snapshot.get("chapter_goal", "") or truth_snapshot.get(
            "prev_chapter_summary", ""
        )
        scene_detail = chapter_goal[:120].rstrip("。,") if chapter_goal else ""

        # 4. 调色板 + 风格 (根据题材)
        palette, style_anchor, lighting = self._style_for_genre(genre)

        # 5. 组装 prompt
        parts = [
            f"{protagonist}" if protagonist else f"a {genre} protagonist",
            f"in {scene_detail}" if scene_detail else f"in a {genre} setting",
            f"{palette}",
            f"{lighting}",
            f"{style_anchor}",
            # v0.6.1 修复: 加回 chapter_title 作为氛围参考 (测试断言 + 让 image 有主题锚点)
            f"inspired by the concept of '{chapter_title}' (mood reference only, do not render text)",
            "high detail, professional composition, 3:4 aspect ratio, "
            "ABSOLUTELY no text, no watermark, no logo, no letters, no Chinese characters, no symbols",
        ]
        prompt = ", ".join(p for p in parts if p)
        return prompt

    # ------------------------------------------------------------------
    # 内部: 题材检测 + 风格映射
    # ------------------------------------------------------------------

    _GENRE_KEYWORDS = {
        "xianxia": [
            "修仙",
            "仙侠",
            "玄幻",
            "修真",
            "灵根",
            "灵素",
            "灵气",
            "道法",
            "古武",
            "武侠",
            "末法",
        ],
        "scifi": [
            "科幻",
            "赛博",
            "太空",
            "星际",
            "机器人",
            "AI",
            "末世",
            "未来",
            "宇航",
            "银河",
            "量子",
        ],
        "romance": ["爱情", "现代", "都市", "职场", "恋爱", "青春", "校园"],
        "mystery": ["悬疑", "惊悚", "恐怖", "推理", "犯罪", "灵异", "鬼"],
    }

    def _detect_genre(self, category: str, keywords: list) -> str:
        """根据 category + keywords 判定题材

        Returns: xianxia / scifi / romance / mystery / general
        """
        haystack = (category + " " + " ".join(keywords or [])).lower()
        for genre, kws in self._GENRE_KEYWORDS.items():
            for kw in kws:
                if kw.lower() in haystack or kw in haystack:
                    return genre
        return "general"

    def _style_for_genre(self, genre: str) -> tuple[str, str, str]:
        """题材 → (调色板, 风格参考, 光照) 三元组

        Returns:
            (palette, style_anchor, lighting)
        """
        styles = {
            "xianxia": (
                "jade green and gold with ethereal mist and floating talismans",
                "in the style of Chinese xianxia concept art, ethereal and majestic",
                "soft diffused lighting through clouds, glowing aura around subject",
            ),
            "scifi": (
                "cool cyan and magenta neon, holographic interfaces, futuristic tech",
                "in the style of cyberpunk concept art, Blade Runner aesthetic",
                "harsh rim lighting, neon reflections, dramatic shadows",
            ),
            "romance": (
                "warm pastel tones, soft golden hour light, romantic atmosphere",
                "in the style of contemporary Asian romance illustration",
                "soft natural lighting, dreamy bokeh, gentle shadows",
            ),
            "mystery": (
                "deep indigo and crimson with fog and dim lanterns",
                "in the style of gothic noir concept art",
                "low-key chiaroscuro lighting, deep shadows, mysterious atmosphere",
            ),
            "general": (
                "warm earth tones with cinematic color grading",
                "in the style of cinematic concept art",
                "dramatic side lighting, balanced shadows and highlights",
            ),
        }
        return styles.get(genre, styles["general"])

    # ------------------------------------------------------------------
    # 内部: JSON 序列化 helper (Edge Runtime 兼容, 无 indent)
    # ------------------------------------------------------------------

    @staticmethod
    def _json_dumps(obj: Any) -> str:
        """json.dumps with ensure_ascii=False, indent=2 (truth_snapshot / style_guide 注入 prompt 用)"""
        import json as _json
        return _json.dumps(obj, ensure_ascii=False, indent=2)
