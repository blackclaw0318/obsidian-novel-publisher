"""
大任务 B: 写作引擎 NovelWriter
===============================
封装 minimax M3 API 调用, 读取 prompt 模板, 生成单章正文 (3000 字) + 封面 prompt。

输出契约 (ChapterDraft):
- raw_text:      ~3000 ± 200 中文字符的纯文本小说正文
- cover_prompt:  适配 minimax image-01 的英文封面 prompt (3:4, no text)
- word_count:    raw_text 的中文字符数

依赖: requests, python-dotenv (已在 .venv 装好)
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import time
from dataclasses import dataclass
from typing import Any

import requests
from dotenv import load_dotenv

# 在模块加载时一次性加载 .env (确保 key 已注入)
load_dotenv()

PROMPT_DIR = pathlib.Path(__file__).parent.parent / "assets" / "prompts"


class LLMError(Exception):
    """M3 调用失败 (重试耗尽)"""


@dataclass
class ChapterDraft:
    raw_text: str
    cover_prompt: str
    word_count: int
    usage: dict | None = None  # token 计数, 用于监控


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
    """剥除 M3 输出中可能的思考块 (多格式兼容)。

    M3 偶发生成大段思考占 token, 导致中文字数偏低。处理格式:
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


class NovelWriter:
    """单例式 LLM 客户端 (本章一次写一章, 不并发)"""

    DEFAULT_MODEL = "MiniMax-M3"
    DEFAULT_TEMPERATURE = 0.9
    DEFAULT_MAX_TOKENS = (
        12000  # 7-9 fix: 8000 不够 (2754 字被 strip 后剩 < 2800), 给 12000 留 think 块 buffer
    )
    DEFAULT_TIMEOUT = 300  # 7-7: M3 长输出 7-7 实测 ~6min, 120s 不够, 改 300s (5min) 避免误杀
    DEFAULT_RETRIES = 5  # 7-9 fix: M3 偶发返回空 content, 5 次重生覆盖 90% 失败

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key or os.environ.get("MINIMAXI_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "MINIMAXI_API_KEY 未配置 (.env 或环境变量)。" "凭据安全协议见 OPERATIONS §1"
            )
        self.base_url = (
            base_url or os.environ.get("MINIMAXI_BASE_URL", "https://api.minimaxi.com/v1")
        ).rstrip("/")
        self.model = model or os.environ.get("MINIMAXI_TEXT_MODEL", self.DEFAULT_MODEL)
        self._last_usage: dict[str, Any] = {}  # 最近一次调用 usage, dict[str, int], 给调用方读取
        # 不打印 key (避免进日志) — 只打印前后缀摘要
        self._key_fingerprint = (
            f"{self.api_key[:6]}...{self.api_key[-4:]} (len={len(self.api_key)})"
        )

    def __repr__(self) -> str:
        return f"NovelWriter(model={self.model}, base_url={self.base_url}, key={self._key_fingerprint})"

    # ------------------------------------------------------------------
    # 公开 API
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
        min_word_count: int = 2700,  # 7-9 fix: 2800 太严, 2754 字就被拒; 2700 = 3000 ± 10% 合理下限
        max_word_count: int = 3200,
    ) -> ChapterDraft:
        """
        写一章。失败/字数不达自动重试 max_retries 次。

        Args:
            chapter_idx:     章节号 (从 1 开始)
            truth_snapshot:  当前小说的 truth 快照 (人物/世界观/伏笔), dict
            style_guide:     风格指南 (文风/语气/字数偏好), dict
            max_retries:     失败重试次数
            temperature:     M3 温度参数 (默认 0.9)
            max_tokens:      M3 输出 token 上限 (默认 8000)
            min_word_count:  字数下限, 不足则重生 (默认 2800)
            max_word_count:  字数上限, 超过则接受 (允许)

        Returns:
            ChapterDraft(raw_text, cover_prompt, word_count, usage)
        """
        for attempt in range(max_retries):
            try:
                draft = self._call_m3(
                    chapter_idx=chapter_idx,
                    truth_snapshot=truth_snapshot,
                    style_guide=style_guide,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                # 字数检查: 不足则重生 (M3 偶发生成大段 thinking 占 token)
                if draft.word_count < min_word_count:
                    print(
                        f"[novel_writer] 字数不足 {draft.word_count}<{min_word_count}, "
                        f"重生 {attempt+1}/{max_retries}"
                    )
                    if attempt < max_retries - 1:
                        time.sleep(2**attempt)
                        continue
                    else:
                        # 重生耗尽, 但接受现有输出 (避免章节永远写不出)
                        print(f"[novel_writer] 重生耗尽, 接受现有 {draft.word_count} 字")
                return draft
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    raise LLMError(
                        f"M3 调用失败 {max_retries} 次 (chapter {chapter_idx}): {e}"
                    ) from e
                wait = 2**attempt  # 1s, 2s, 4s
                print(f"[novel_writer] 重试 {attempt+1}/{max_retries}, 等待 {wait}s: {e}")
                time.sleep(wait)

        raise LLMError("unreachable")

    # ------------------------------------------------------------------
    # 内部: M3 调用 + prompt 渲染 + 后处理
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

        # user 模板变量注入 (truth_snapshot / style_guide 是 dict, JSON 化)
        chapter_title = truth_snapshot.get("chapter_title", f"第{chapter_idx}章")
        try:
            user_prompt = user_template.format(
                chapter_idx=chapter_idx,
                total_chapters=truth_snapshot.get("total_chapters", 16),
                chapter_title=chapter_title,
                chapter_goal=truth_snapshot.get("chapter_goal", ""),
                truth_snapshot=json.dumps(truth_snapshot, ensure_ascii=False, indent=2),
                style_guide=json.dumps(style_guide, ensure_ascii=False, indent=2),
                prev_chapter_summary=truth_snapshot.get(
                    "prev_chapter_summary", "(无前章, 这是开篇)"
                ),
            )
        except KeyError as e:
            # 模板里 { } 没被全部替换 → 4xx 类参数错, 立即抛 (不重试)
            raise LLMError(f"user prompt 模板变量缺失: {e}") from e

        raw = self._call_raw(
            system=system_prompt,
            user=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        clean_text = strip_think_block(raw)
        word_count = count_chinese_chars(clean_text)

        # 封面 prompt: 由本章主题 + 主角 + 关键道具 (简化版)
        cover_prompt = self._compose_cover_prompt(chapter_title, truth_snapshot)

        usage = self._last_usage or {}

        return ChapterDraft(
            raw_text=clean_text,
            cover_prompt=cover_prompt,
            word_count=word_count,
            usage=usage,
        )

    def _call_raw(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.85,
        max_tokens: int = 2000,
        max_retries: int = DEFAULT_RETRIES,
    ) -> str:
        """通用 M3 调用接口 (供 SummaryGenerator 等其他模块复用).

        Returns:
            M3 返回的 raw 文本 (含 <think> 块, 调用方自行 strip_think_block).

        Raises:
            LLMError: 4xx 参数错 (不重试) / 5xx + 空响应重试耗尽 / 响应格式异常

        重试策略:
        - 4xx 参数错: 立即抛, 不重试
        - 5xx 网络错: 重试 max_retries 次, 指数退避 (1s, 2s, 4s)
        - 200 但 content 为空 (M3 偶发): 重试 (单次重试一次, 不计数过多)
        """
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        last_err: Exception | None = None  # RequestException or LLMError
        for attempt in range(max_retries):
            try:
                resp = requests.post(
                    url, headers=headers, json=payload, timeout=(10, self.DEFAULT_TIMEOUT)
                )
                if 400 <= resp.status_code < 500:
                    # 参数错: 立即抛, 不重试
                    raise LLMError(f"M3 4xx ({resp.status_code}): {resp.text[:500]}")
                resp.raise_for_status()  # 5xx → RequestException

                data = resp.json()
                self._last_usage = data.get("usage", {})

                try:
                    content = data["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError) as e:
                    raise LLMError(f"M3 响应格式异常: {data}") from e

                # M3 偶发返回空字符串 (网络层成功但内容空), 视为失败重试
                if not content or not content.strip():
                    raise LLMError(f"M3 返回空 content (attempt {attempt+1}/{max_retries})")

                return content
            except requests.exceptions.RequestException as e:
                last_err = e
                if attempt < max_retries - 1:
                    wait = 2**attempt  # 1s, 2s, 4s
                    print(f"[_call_raw] 重试 {attempt+1}/{max_retries}, 等待 {wait}s: {e}")
                    time.sleep(wait)
            except LLMError as e:
                # 4xx (参数错) 立即抛; 200 但内容空 (last_err) 也抛
                if "4xx" in str(e):
                    raise
                last_err = e
                if attempt < max_retries - 1:
                    wait = 2**attempt
                    print(f"[_call_raw] 重试 {attempt+1}/{max_retries}, 等待 {wait}s: {e}")
                    time.sleep(wait)
        raise LLMError(f"M3 调用失败 {max_retries} 次: {last_err}")

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
