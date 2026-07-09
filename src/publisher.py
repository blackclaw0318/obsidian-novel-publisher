"""
大任务 P2-5: 主入口 Publisher
==============================================
编排一次完整推送流程:

  1. load_state          (JSON 状态机)
  2. 选题                 (TopicGenerator.generate_one_shot)
  3. 写章节               (NovelWriter.write_chapter)
  4. 画封面               (CoverGenerator.generate, 本地 tmp)
  5. 上传封面             (CoverUploader.upload → URL)
  6. 渲染 markdown       (MarkdownRenderer.render)
  7. HMAC 签名           (HmacClient.sign)
  8. POST 到博客         (requests.post)
  9. 更新 state          (save_state: mark_pushed / failed / skipped)
 10. (可选) GitHub 备份   (P3 落地)

依赖: state / hmac_client / cover_upload / markdown_renderer
      + novel_writer / topic_gen / cover_gen (P1)

设计原则:
- 每个步骤 raise 特定异常, 外层 cli_main 接住 + 写 state + 告警 (P5)
- 不静默失败: 任何步骤抛错都 mark_failed + 不推进 next_idx
- idempotency_key 全程贯穿 (state 记录, blog 侧去重)
- 失败可重入: 下次 run 自动从 next_idx 接着来
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import signal
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

# P2: 多本并行调度
from .backup_reader import BackupReader
from .character_loader import Characters as CharactersParsed
from .character_loader import fetch_characters
from .cover_gen import CoverGenerator
from .cover_prompt_builder import build_cover_prompt
from .cover_upload import CoverUploader, CoverUploadError
from .github_backup import ChapterMeta, GithubBackup
from .hmac_client import HmacClient, HmacConfig
from .markdown_renderer import render as render_markdown
from .novel_outline import fetch_outline
from .novel_registry import (
    DEFAULT_NOVELS_YAML,
    Novel,
    NovelRegistry,
    Schedule,
    current_volume,
    get_enabled_novels,
    load_novels,
    render_chapter_slug,
)
from .novel_writer import ChapterDraft, LLMError, NovelWriter
from .state import (
    DEFAULT_STATE_PATH,
    PublishState,
    load_state,
    save_state,
)
from .state_per_novel import (
    DEFAULT_STATE_DIR,
    load_state_for_novel,
    save_state_for_novel,
)
from .style_guide import StyleGuide as StyleGuideParsed
from .style_guide import fetch_style_guide
from .text_punct import _merge_orphan_quotes, normalize_cn_punctuation
from .topic_gen import generate_one_shot

logger = logging.getLogger(__name__)


def stable_idempotency_key(novel_id: str, chapter_idx: int) -> str:
    """稳定的 idempotency_key (per novel + chapter)

    7-9 fix: 不用 UUID v4 (每次重生成), 用 sha256(novel_id + chapter_idx)[:32]
    这样同一章重推 → 同 key → obsidian 端 幂等去重 (返 200 + 旧数据)
    不同章 → 不同 key → 正常创建
    """
    raw = f"{novel_id}-ch{chapter_idx:03d}"
    return hashlib.sha256(raw.encode()).hexdigest()[
        :32
    ]  # noqa: PLR2004 — 32-char sha256 prefix is intentional


# --------------------------------------------------------------------------
# 配置 (从 .env 读)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PublisherConfig:
    """publisher 完整配置 (从 .env 一次性 load)"""

    # LLM
    minimaxi_api_key: str
    minimaxi_base_url: str
    minimaxi_text_model: str
    minimaxi_image_model: str

    # obsidian-journal 推送
    obsidian_publish_url: str
    obsidian_publish_id: str
    obsidian_publish_secret: str
    obsidian_admin_token: str  # 用于 cover_upload
    obsidian_admin_base_url: str  # 用于 cover_upload (e.g. 'https://shangkun.uk')

    # GitHub 备份 (P3)
    github_backup_repo: str  # e.g. 'blackclaw0318/obsidian-novel-backups'
    github_backup_token: str  # fine-grained PAT, Contents: Read+Write on backups

    # 状态
    state_path: Path
    cover_tmp_dir: Path

    @classmethod
    def from_env(cls) -> PublisherConfig:
        """从 .env + 系统 env 构造, 缺关键字段立即抛"""
        required = {
            "MINIMAXI_API_KEY": os.environ.get("MINIMAXI_API_KEY", ""),
            "OBSIDIAN_PUBLISH_SECRET": os.environ.get("OBSIDIAN_PUBLISH_SECRET", ""),
            "OBSIDIAN_PUBLISH_ID": os.environ.get("OBSIDIAN_PUBLISH_ID", ""),
        }
        missing = [k for k, v in required.items() if not v or v == "***" or v.startswith("change")]
        if missing:
            raise PublisherConfigError(f"环境变量缺失或为占位符: {missing} — 请检查 .env")

        return cls(
            minimaxi_api_key=required["MINIMAXI_API_KEY"],
            minimaxi_base_url=os.environ.get(
                "MINIMAXI_BASE_URL", "https://api.minimaxi.com/v1"
            ).rstrip("/"),
            minimaxi_text_model=os.environ.get("MINIMAXI_TEXT_MODEL", "MiniMax-M3"),
            minimaxi_image_model=os.environ.get("MINIMAXI_IMAGE_MODEL", "image-01"),
            obsidian_publish_url=os.environ.get(
                "OBSIDIAN_PUBLISH_URL", "https://shangkun.uk/api/external/chapters"
            ),
            obsidian_publish_id=required["OBSIDIAN_PUBLISH_ID"],
            obsidian_publish_secret=(
                # 优先用服务器侧名 (与 obsidian-journal .env 一致), 兑底为旧名
                os.environ.get("OBSIDIAN_NOVEL_PUBLISH_SECRET", "").strip()
                or required["OBSIDIAN_PUBLISH_SECRET"]
            ),
            obsidian_admin_token=os.environ.get("OBSIDIAN_ADMIN_TOKEN", ""),
            obsidian_admin_base_url=os.environ.get(
                "OBSIDIAN_ADMIN_BASE_URL", "https://shangkun.uk"
            ).rstrip("/"),
            state_path=Path(os.environ.get("PUBLISH_STATE_PATH", str(DEFAULT_STATE_PATH))),
            cover_tmp_dir=Path(os.environ.get("COVER_TMP_DIR", "./data/covers")),
            github_backup_repo=os.environ.get(
                "GITHUB_BACKUP_REPO", "blackclaw0318/obsidian-novel-backups"
            ),
            github_backup_token=os.environ.get("GITHUB_BACKUP_TOKEN", ""),
        )


class PublisherConfigError(Exception):
    """publisher 配置缺失 / 占位符未替换"""


# --------------------------------------------------------------------------
# 异常族
# --------------------------------------------------------------------------


class PublisherError(Exception):
    """publisher 基础异常"""


class TopicGenError(PublisherError):
    """选题失败 (LLM 5xx 重试耗尽 / 解析失败)"""


class ChapterGenError(PublisherError):
    """章节生成失败"""


class CoverGenError(PublisherError):
    """封面生成失败"""


class PublishError(PublisherError):
    """推送博客失败 (签名错 / HTTP 错 / 业务错)"""


class RunTimeoutError(PublisherError):
    """单次 run 超过全局硬时限 (防止手动验证时沙箱无限期卡死)"""


# 全局硬时限: 一次 run_once 的 wall-clock 上限 (秒)。
# 正常 happy path ≈ topic 1-4min + write ~5min + 封面 ~1.5min + 推送 <1min ≈ 12min,
# 900s (15min) 留余量; 超时即硬停, 保证无论哪个环节挂死都不会无限卡沙箱。
# 可用 PUBLISH_HARD_TIMEOUT_S 覆盖; 设 0 关闭 (不推荐)。
DEFAULT_HARD_TIMEOUT_S = 900


def _hard_timeout_handler(signum, frame):  # noqa: ARG001
    raise RunTimeoutError("单次 run 超过全局硬时限, 已硬停 (防沙箱卡死)")


def _arm_hard_timeout(seconds: float):
    """启动全局硬时限 (SIGALRM)。返回旧 handler (用于 cancel 时还原), 或 None 表示未启用。

    仅 Unix 主线程生效 (systemd oneshot / 手动运行都是主线程)。
    seconds<=0 或平台不支持 → 不启用, 返回 None。
    """
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        return None
    old = signal.signal(signal.SIGALRM, _hard_timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    return old


def _cancel_hard_timeout(old_handler) -> None:
    """取消全局硬时限, 还原旧 handler。old_handler=None 时无操作。"""
    if old_handler is None or not hasattr(signal, "SIGALRM"):
        return
    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, old_handler)


def _resolve_hard_timeout(override: float | None) -> float:
    """解析单次 run 硬时限: 显式参数 > PUBLISH_HARD_TIMEOUT_S > 默认。"""
    if override is not None:
        return override
    raw = os.environ.get("PUBLISH_HARD_TIMEOUT_S", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            logger.warning(
                "PUBLISH_HARD_TIMEOUT_S 非法值 %r, 用默认 %ds", raw, DEFAULT_HARD_TIMEOUT_S
            )
    return float(DEFAULT_HARD_TIMEOUT_S)


# --------------------------------------------------------------------------
# P2: 多本并行 + 配额检查 + 错误隔离
# --------------------------------------------------------------------------


@dataclass
class NovelRunResult:
    """单本小说 run 结果"""

    novel_id: str
    status: str  # success | failed | skipped
    error: str | None = None
    chapter_idx: int | None = None
    slot: str = ""


@dataclass
class AggregateResult:
    """多本并行汇总"""

    total: int
    success: int
    failed: int
    skipped: int
    details: list[NovelRunResult]

    @classmethod
    def from_results(cls, results: list[NovelRunResult]) -> AggregateResult:
        return cls(
            total=len(results),
            success=sum(1 for r in results if r.status == "success"),
            failed=sum(1 for r in results if r.status == "failed"),
            skipped=sum(1 for r in results if r.status == "skipped"),
            details=results,
        )

    def is_all_success(self) -> bool:
        return self.failed == 0

    def __str__(self) -> str:
        lines = [
            f"📊 多本汇总: total={self.total} success={self.success} failed={self.failed} skipped={self.skipped}"
        ]
        for r in self.details:
            mark = {"success": "✅", "failed": "❌", "skipped": "⊘"}.get(r.status, "?")
            extra = f" ({r.error})" if r.error else ""
            slot = f" slot={r.slot}" if r.slot else ""
            lines.append(f"  {mark} {r.novel_id} → {r.status} idx={r.chapter_idx}{slot}{extra}")
        return "\n".join(lines)


def _current_slot(now: datetime, schedule: Schedule, tz_name: str = "Asia/Shanghai") -> str:
    """计算当前推送档位标识 (e.g. "2026-07-08-08")

    逻辑:
    - 把 now 转 tz_name
    - 找最近一个 schedule.hours 里的 hour (向下取整, e.g. 8:30 → 8)
    - 格式: "{YYYY-MM-DD}-{HH}"
    """
    import zoneinfo

    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except (KeyError, ValueError):
        tz = zoneinfo.ZoneInfo("UTC")
    local = now.astimezone(tz)
    # 找 <= local.hour 的最大 schedule.hours (e.g. local.hour=15, hours=[8,12,18] → 12)
    eligible = [h for h in schedule.hours if h <= local.hour]
    slot_hour = max(eligible) if eligible else schedule.hours[0]
    return f"{local.strftime('%Y-%m-%d')}-{slot_hour:02d}"


def _should_skip_slot(state: PublishState, slot: str) -> bool:
    """本档已写过 → 跳过

    逻辑: state.last_pushed_slot == slot 表示本档已推过, 跳
    """
    if not state.last_pushed_slot:
        return False  # 从未写过
    return state.last_pushed_slot == slot


# --------------------------------------------------------------------------
# P5+: outline 按章节切片 + chapter_goal 显式
# --------------------------------------------------------------------------

_CHAPTER_HEADER_RE = re.compile(r"(^###\s+Ch\d+.*?$)", re.MULTILINE)


def _extract_chapter_outline(outline_text: str, chapter_idx: int) -> str:
    """从全本 outline.md 抽出单章描述 (~1500 字)

    寻 "### Ch{N}" 段, 取到下一个 "### Ch" 或文件末尾。
    找不到则回退到前 1500 字 (兼容旧 outline schema)。

    Args:
        outline_text: 完整 outline.md 内容 (从 backups 仓拉)
        chapter_idx:  目标章节号 (1-based)

    Returns:
        单章切片 markdown 文本 (含 ### Ch{N} header)
    """
    if not outline_text:
        return ""
    pattern = rf"^###\s+Ch{chapter_idx}[^\n]*$"
    m = re.search(pattern, outline_text, re.MULTILINE)
    if not m:
        logger.warning("[outline] 找不到 Ch%d header, 回退前 1500 字", chapter_idx)
        return outline_text[:1500]
    start = m.start()
    # 找下一个 ### Ch{N+1} 或 Ch{N+...}
    next_m = re.search(r"^###\s+Ch(?!\d{0,2}$)\d+", outline_text[m.end() :], re.MULTILINE)
    end = m.end() + next_m.start() if next_m else len(outline_text)
    slice_md = outline_text[start:end].strip()
    # 截断到 1800 字, 留点 buffer
    if len(slice_md) > 1800:
        slice_md = slice_md[:1800]
    return slice_md


def _compose_chapter_outline_skeleton(novel_title: str, chapter_idx: int) -> str:
    """当 outline.md 缺失/格式不对时, 用本小说标题 + 章节号构造兜底骨架"""
    return (
        f"### Ch{chapter_idx} · (本章由 publisher 自动生成, 老板可后修)\n\n"
        f"小说《{novel_title}》第 {chapter_idx} 章, "
        f"~3000 字中篇科幻, 主角驱动剧情, 章末留钩子。"
    )


def _to_backup_raw_url(
    obsidian_cover_url: str, config: PublisherConfig, novel_id: str, prev_idx: int
) -> str | None:
    """把 obsidian 公网封面 URL 转成 backups 仓 raw.githubusercontent.com URL

    minimax 服务端访问不到 shangkun.uk (Cloudflare bot 防护),
    但能访问 raw.githubusercontent.com (GitHub CDN)。
    返回 None 表示不需要转 (走原 obsidian URL)。

    路径映射: truth/novels/{novel_id}/covers/ch-{idx:03d}.jpg
    → https://raw.githubusercontent.com/{repo}/main/truth/novels/{novel_id}/covers/ch-{idx:03d}.jpg
    """
    if not config.github_backup_repo:
        return None
    return (
        f"https://raw.githubusercontent.com/{config.github_backup_repo}/main/"
        f"truth/novels/{novel_id}/covers/ch-{prev_idx:03d}.jpg"
    )


def _compose_chapter_goal(chapter_outline: str, novel_title: str, chapter_idx: int) -> str:
    """chapter_goal: 显式 LLM 写作指令 (≤300 字)

    不直接 dump outline (太长), 只取章节目标 + 关键冲突 + 钩子要求。
    """
    if not chapter_outline:
        return _compose_chapter_outline_skeleton(novel_title, chapter_idx)
    # 取章节 outline 里的 "章节目标" 行 (第一段 "**章节目标**: ..." 之后到下一个 ** 之前)
    m = re.search(r"\*\*章节目标\*\*\s*[::]\s*(.+?)(?=\n\n|\Z)", chapter_outline, re.DOTALL)
    goal = m.group(1).strip() if m else chapter_outline[:200]
    if len(goal) > 300:
        goal = goal[:300]
    # 显式加 3 项硬性要求
    return (
        f"{goal}\n\n"
        f"【硬性写作要求】\n"
        f"1. 字数: 2800-3200 中文字符 (硬下限 2800)\n"
        f"2. 章末: 必须留钩子 (未解之谜 / 反转 / 悬念)\n"
        f"3. 严禁: AI 自述 / 政治敏感 / 半角标点出现在中文字符之间"
    )


def _run_one_novel(
    config: PublisherConfig,
    novel: Novel,
    schedule: Schedule,
    *,
    force: bool = False,
    hard_timeout_s: float | None = None,
    backup_reader: BackupReader | None = None,
) -> PublishState:
    """单本小说的完整推送 (P2 抽出来供 run_once / run_all_novels 复用)

    与 v0.2 run_once 区别:
    1. state 路径: load_state_for_novel(novel.id) (从 novel.id 推)
    2. novel slug/title/desc/status 从 novel 读 (不硬编码)
    3. volume_title/volume_order 用 current_volume(novel, idx) 算
    4. chapter_slug 用 render_chapter_slug(novel, idx)
    5. 配额检查: 本档已写过 → mark_skipped 返
    6. outline/style_guide/characters 喂给 writer (从 backups 仓拉)

    Raises:
        PublisherError: 任何步骤失败 (state 已被 mark_failed)
    """

    state = load_state_for_novel(novel.id)

    # 0. skip_next 手动跳过 (P0 老逻辑保留, quota check 已在 run_all_novels 循环里做)
    if state.skip_next and not force:
        logger.info("[%s] state.skip_next=True, 本次跳过 (idx=%d)", novel.id, state.next_idx)
        state.mark_skipped(state.next_idx, reason="skip_next")
        save_state_for_novel(state, novel.id)
        return state

    slot = _current_slot(_now_utc(), schedule)

    idx = state.next_idx

    # 1. 拉 outline / style_guide / characters (P1 集成)
    outline_text = ""
    style_guide_dict: dict = {}
    characters_dict: dict = {}
    sg_parsed: StyleGuideParsed | None = None
    ch_parsed: CharactersParsed | None = None
    if backup_reader is not None:
        try:
            o = fetch_outline(
                backup_reader,
                novel.id,
                novel.paths.outline,
                cache_dir=DEFAULT_STATE_DIR.parent / "cache",
            )
            outline_text = o.content
            logger.info(
                "[%s] outline: %d 字节, sha 变=%s", novel.id, len(outline_text), o.is_changed
            )
        except Exception as e:
            logger.warning("[%s] 拉 outline 失败, 退化: %s", novel.id, e)
        try:
            sg, _ = fetch_style_guide(
                backup_reader,
                novel.id,
                novel.paths.style_guide,
                cache_dir=DEFAULT_STATE_DIR.parent / "cache",
            )
            sg_parsed = sg
            style_guide_dict = {
                "title": novel.title,
                "genre_hint": novel.category,
                "style_description": sg.style_description,
                "character_refs": [
                    {"name": c.name, "role": c.role, "description": c.description}
                    for c in sg.character_refs
                ],
                "scene_palette": sg.scene_palette,
                "cover_prompt_template": sg.cover_prompt_template,
            }
            logger.info(
                "[%s] style_guide: %d 人物, %d 色板",
                novel.id,
                len(sg.character_refs),
                len(sg.scene_palette),
            )
        except Exception as e:
            logger.warning("[%s] 拉 style_guide 失败, 退化: %s", novel.id, e)
        try:
            ch, _ = fetch_characters(
                backup_reader,
                novel.id,
                novel.paths.characters,
                cache_dir=DEFAULT_STATE_DIR.parent / "cache",
            )
            ch_parsed = ch
            if ch.main:
                characters_dict = {
                    "main": {
                        "name": ch.main.name,
                        "role": ch.main.role,
                        "gender": ch.main.gender,
                        "age": ch.main.age,
                        "appearance": ch.main.appearance,
                        "personality": ch.main.personality,
                    }
                }
                logger.info(
                    "[%s] characters: main=%s, supporting=%d",
                    novel.id,
                    ch.main.name,
                    len(ch.supporting),
                )
        except Exception as e:
            logger.warning("[%s] 拉 characters 失败, 退化: %s", novel.id, e)

    # 2. 构造模块
    writer = NovelWriter(api_key=config.minimaxi_api_key)
    cover_gen = CoverGenerator(api_key=config.minimaxi_api_key)
    uploader = CoverUploader(
        base_url=config.obsidian_admin_base_url,
        admin_token=config.obsidian_admin_token,
    )
    hmac = HmacClient(
        HmacConfig(
            publish_id=config.obsidian_publish_id,
            publish_secret=config.obsidian_publish_secret,
        )
    )

    hard_timeout_s = _resolve_hard_timeout(hard_timeout_s)
    _old_alarm = _arm_hard_timeout(hard_timeout_s)

    try:
        # 3. 选题 (v0.3.2 P5 fix: 传 novel.keywords + style_guide 给 topic_gen, 避免 LLM 跑偏到无关题材)
        logger.info("[%s/%d] 选题中…", novel.id, idx)
        topic_kwargs: dict = {"n_candidates": 1}
        if novel.keywords:
            topic_kwargs["keywords"] = list(novel.keywords)
        if style_guide_dict:
            topic_kwargs["style_guide"] = {
                "title": novel.title,
                "description": novel.description,
                "category": novel.category,
                "style_description": style_guide_dict.get("style_description", ""),
            }
        topic = generate_one_shot(**topic_kwargs)[0]
        logger.info(
            "[%s/%d] 选题结果: %s (genre=%s, keywords_used=%s)",
            novel.id,
            idx,
            topic.title,
            topic.genre_hint,
            topic.keywords_used,
        )

        # 4. 写章节 (v0.3.2 P5+ fix: outline 按章节切片 + chapter_goal 显式, 避免 5143 字节 outline 超 token 预算)
        logger.info("[%s/%d] 写章节: %s", novel.id, idx, topic.title)
        chapter_outline = _extract_chapter_outline(outline_text, idx)
        chapter_goal = _compose_chapter_goal(chapter_outline, novel.title, idx)
        truth_snapshot: dict = {
            "topic": topic.title,
            "outline": chapter_outline,  # 仅本章节切片, ~1500 字
            "keywords": topic.keywords_used,
            "category": novel.category or "科幻",
            "chapter_goal": chapter_goal,
        }
        if characters_dict.get("main"):
            truth_snapshot["main_character"] = characters_dict["main"]
        style_guide_full = {**style_guide_dict, "outline": topic.outline, "title": topic.title}
        draft: ChapterDraft = writer.write_chapter(
            chapter_idx=idx,
            truth_snapshot=truth_snapshot,
            style_guide=style_guide_full,
        )

        # 5. 封面 (尽力而为, 失败不阻塞)
        cover_url = ""
        cover_path: Path | None = None
        try:
            config.cover_tmp_dir.mkdir(parents=True, exist_ok=True)
            # 7-8 P2.5: ch-2+ 用 ch-(idx-1) 封面公网 URL 作 subject_reference
            # 7-9 fix: minimax 服务端访问不到 shangkun.uk (Cloudflare 保护), 用 backups 仓 raw URL 代替
            subject_ref_url: str | None = None
            if idx >= 2:
                prev_url = state.cover_urls.get(str(idx - 1), "")
                if prev_url:
                    # 优先用 backups 仓 raw URL (minimax 能访问 GitHub raw)
                    # 备选: obsidian 公网 URL (fallback)
                    raw_url = _to_backup_raw_url(prev_url, config, novel.id, idx - 1)
                    if raw_url:
                        subject_ref_url = raw_url
                        logger.info(
                            "[%s/%d] image-to-image 启用, 参考图 (raw): %s",
                            novel.id,
                            idx,
                            raw_url[:80] + "...",
                        )
                    else:
                        subject_ref_url = prev_url
                        logger.info(
                            "[%s/%d] image-to-image 启用, 参考图 (公网 fallback): %s",
                            novel.id,
                            idx,
                            prev_url[:60] + "...",
                        )

            # 7-8 P3: 用 style_guide + characters 驱动 cover prompt (替代 draft.cover_prompt)
            # 老板拍: style_guide.md 显式 prompt + character_refs 固定描述, 跨章一致
            if sg_parsed is not None:
                cover_prompt = build_cover_prompt(
                    style_guide=sg_parsed,
                    characters=ch_parsed,
                    chapter_idx=idx,
                    chapter_scene=outline_text[:200] if outline_text else "",
                )
                logger.info(
                    "[%s/%d] cover prompt 用 style_guide 驱动 (length=%d, template=%s)",
                    novel.id,
                    idx,
                    len(cover_prompt),
                    "CUSTOM" if sg_parsed.cover_prompt_template.strip() else "DEFAULT",
                )
            else:
                cover_prompt = draft.cover_prompt  # fallback: novel_writer 生成的
                logger.info(
                    "[%s/%d] cover prompt 用 novel_writer 退化 (length=%d)",
                    novel.id,
                    idx,
                    len(cover_prompt),
                )

            logger.info("[%s/%d] 画封面…", novel.id, idx)
            cover_local_path = cover_gen.generate(
                prompt=cover_prompt,
                chapter_idx=idx,
                subject_reference_url=subject_ref_url,
            )
            cp = Path(cover_local_path)
            if not cp.exists():
                raise CoverGenError(f"CoverGenerator 返回路径不存在: {cp}")
            cover_result = uploader.upload(cp, title=f"ch-{idx:03d}")
            cover_url = cover_result.url
            cover_path = cp
        except Exception as e:
            logger.warning(
                "[%s/%d] 封面失败, 降级无封面推送: %s: %s", novel.id, idx, type(e).__name__, e
            )

        # 6. 渲染
        if cover_url and cover_url.startswith("/"):
            cover_url = config.obsidian_admin_base_url.rstrip("/") + cover_url
        clean_raw_text = normalize_cn_punctuation(draft.raw_text)
        rendered = render_markdown(
            raw_text=clean_raw_text,
            cover_url=cover_url,
            chapter_idx=idx,
            chapter_title=topic.title,
        )

        # 7. 推 obsidian (3-tier Novel>Volume>Chapter)
        idem_key = stable_idempotency_key(
            novel.id, idx
        )  # 7-9 fix: 用 stable key 而非 UUID, 重推幂等
        vol = current_volume(novel, idx)
        chapter_slug = render_chapter_slug(novel, idx)
        body = {
            "novel_slug": novel.slug,
            "novel_title": novel.title,
            "novel_description": novel.description,
            "novel_status": novel.status,
            "volume_title": vol.title,
            "volume_order": vol.order,
            "chapter_slug": chapter_slug,
            "chapter_title": rendered.title,
            "chapter_content": rendered.content_markdown,
            "chapter_excerpt": rendered.excerpt,
            "chapter_published": True,
            "external_id": f"{novel.id}-ch{idx:03d}",
            "idempotency_key": idem_key,
            # v0.38 P6: 版权声明 2 字段 (随每章推送, obsidian 端入库)
            "license": os.environ.get("PUBLISHER_LICENSE", "CC BY-NC-SA 4.0"),
            "license_url": os.environ.get(
                "PUBLISHER_LICENSE_URL",
                "https://creativecommons.org/licenses/by-nc-sa/4.0/",
            ),
            "copyright_holder": os.environ.get("PUBLISHER_COPYRIGHT_HOLDER", "上坤"),
            "aigc_disclosure": int(os.environ.get("PUBLISHER_AIGC_DISCLOSURE", "1")),
        }
        raw_body = json.dumps(body, ensure_ascii=False)
        sig_headers = hmac.sign(body, idempotency_key=idem_key, raw_body=raw_body)
        logger.info("[%s/%d] 推送博客: POST %s", novel.id, idx, config.obsidian_publish_url)
        resp = _post_with_sig(
            url=config.obsidian_publish_url,
            body=body,
            raw_body=raw_body,
            sig_headers=sig_headers,
        )

        # 8. GitHub 备份 (可选)
        if config.github_backup_token and config.github_backup_token.strip():
            try:
                backup = GithubBackup(
                    repo=config.github_backup_repo,
                    token=config.github_backup_token,
                )
                post_url = ""
                if isinstance(resp, dict):
                    post_url = (resp.get("chapter") or {}).get("url", "") or resp.get("url", "")
                backup_meta = ChapterMeta.now(
                    chapter_idx=idx,
                    title=topic.title,
                    word_count=draft.word_count,
                    llm_usage=draft.usage or {},
                    obsidian_post_url=post_url,
                )
                backup_result = backup.upload(
                    chapter_md=clean_raw_text,
                    cover_jpg=cover_path.read_bytes() if cover_path else b"",
                    meta=backup_meta,
                )
                logger.info(
                    "[%s/%d] GitHub 备份 ✅ commit=%s",
                    novel.id,
                    idx,
                    backup_result.commit_sha[:12],
                )
            except Exception as e:
                logger.warning(
                    "[%s/%d] GitHub 备份失败 (主推送仍成功): %s: %s",
                    novel.id,
                    idx,
                    type(e).__name__,
                    e,
                )

        # 9. 成功: 写 state (带 slot + cover_url, 多本并行配额检查 + image-to-image)
        state.mark_pushed(idx, idem_key, slot=slot, cover_url=cover_url)
        save_state_for_novel(state, novel.id)
        logger.info(
            "[%s/%d] ✓ 推送成功: idem=%s slot=%s cover=%s",
            novel.id,
            idx,
            idem_key[:8],
            slot,
            cover_url[:60] if cover_url else "(无)",
        )
        return state

    except LLMError as e:
        state.mark_failed(idx, f"LLM error: {e}")
        save_state_for_novel(state, novel.id)
        raise ChapterGenError(f"[{novel.id}/{idx}] LLM 调用失败: {e}") from e
    except CoverUploadError as e:
        state.mark_failed(idx, f"cover upload error: {e}")
        save_state_for_novel(state, novel.id)
        raise CoverGenError(f"[{novel.id}/{idx}] 封面上传失败: {e}") from e
    except RunTimeoutError as e:
        state.mark_failed(idx, f"hard timeout: {e}")
        save_state_for_novel(state, novel.id)
        logger.error(
            "[%s/%d] ✗ 命中全局硬时限 (%.0fs), 本次失败: %s",
            novel.id,
            idx,
            hard_timeout_s,
            e,
        )
        raise
    except Exception as e:
        state.mark_failed(idx, f"unexpected: {type(e).__name__}: {e}")
        save_state_for_novel(state, novel.id)
        raise PublisherError(f"[{novel.id}/{idx}] 未预期错误: {e}") from e
    finally:
        _cancel_hard_timeout(_old_alarm)


def _now_utc() -> datetime:
    """当前 UTC 时间 (实为运行机器墙钟)"""
    from datetime import datetime as _dt

    return _dt.now(UTC)


def run_all_novels(
    config: PublisherConfig,
    *,
    force: bool = False,
    hard_timeout_s: float | None = None,
    registry: NovelRegistry | None = None,
) -> AggregateResult:
    """多本并行推送 (P2)

    Args:
        config:          publisher 配置
        force:           True = 忽略配额检查 / skip_next
        hard_timeout_s:  每本硬时限 (秒)
        registry:        注入测试用; 不传则从 novels.yaml 读

    Returns:
        AggregateResult { total, success, failed, skipped, details }

    行为:
        1. 加载 novels.yaml → 拿 enabled 列表
        2. for novel in enabled:
            - 调 _run_one_novel(novel, ...)
            - try/except 隔离: 1 本失败不阻塞其他本
        3. 汇总 返 AggregateResult

    Raises:
        PublisherError: 配置缺失
    """
    if registry is None:
        try:
            registry = load_novels()
        except FileNotFoundError as e:
            raise PublisherError(f"novels.yaml 不存在: {e}") from e

    enabled = get_enabled_novels(registry)
    if not enabled:
        logger.warning("[P2] novels.yaml 里 0 本 enabled, 退出")
        return AggregateResult(total=0, success=0, failed=0, skipped=0, details=[])

    # 构造 BackupReader (P1 集成, 喂给 outline/style_guide/characters)
    backup_reader: BackupReader | None = None
    if config.github_backup_token and config.github_backup_token.strip():
        try:
            backup_reader = BackupReader(
                repo=config.github_backup_repo,
                token=config.github_backup_token,
            )
        except Exception as e:
            logger.warning("[P2] BackupReader 构造失败, 退化不拉 outline/...: %s", e)

    slot = _current_slot(_now_utc(), registry.schedule)
    logger.info(
        "[P2] 多本推送开始: %d 本 enabled, slot=%s",
        len(enabled),
        slot,
    )

    results: list[NovelRunResult] = []
    for novel in enabled:
        # 配额检查提到 run_all_novels 循环里 (避免 _run_one_novel mock 后不起作用)
        if not force:
            from .state_per_novel import load_state_for_novel as _load_s

            _state = _load_s(novel.id)
            if _should_skip_slot(_state, slot):
                logger.info(
                    "[P2] novel %s 本档 %s 已推过 (last_pushed_slot=%s), 跳过",
                    novel.id,
                    slot,
                    _state.last_pushed_slot,
                )
                _state.mark_skipped(_state.next_idx, reason=f"slot_already_pushed:{slot}")
                from .state_per_novel import save_state_for_novel as _save_s

                _save_s(_state, novel.id)
                results.append(
                    NovelRunResult(
                        novel_id=novel.id,
                        status="skipped",
                        chapter_idx=_state.next_idx,
                        slot=slot,
                    )
                )
                continue

        try:
            state = _run_one_novel(
                config,
                novel,
                registry.schedule,
                force=force,
                hard_timeout_s=hard_timeout_s,
                backup_reader=backup_reader,
            )
            results.append(
                NovelRunResult(
                    novel_id=novel.id,
                    status=state.last_status,
                    chapter_idx=state.last_pushed_idx,
                    slot=state.last_pushed_slot,
                )
            )
        except PublisherError as e:
            logger.error(
                "[P2] novel %s 推送失败, 继续下一本: %s",
                novel.id,
                e,
            )
            results.append(
                NovelRunResult(
                    novel_id=novel.id,
                    status="failed",
                    error=str(e),
                    slot=slot,
                )
            )

    agg = AggregateResult.from_results(results)
    logger.info("\n%s", agg)
    return agg


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def run_once(
    config: PublisherConfig,
    *,
    force: bool = False,
    hard_timeout_s: float | None = None,
) -> PublishState:
    """执行一次完整推送流程, 返回更新后的 state

    Args:
        config:          publisher 配置
        force:           True = 忽略 skip_next 强制推送 (默认 False)
        hard_timeout_s:  全局硬时限 (秒); None = 读 PUBLISH_HARD_TIMEOUT_S / 默认 900s。
                         超时即硬停并 mark_failed, 保证不无限卡死。

    Returns:
        更新后的 PublishState (已 save)

    Raises:
        PublisherError: 任何步骤失败 (state 已被 mark_failed)
    """
    state = load_state(config.state_path)

    # 0. 检查 skip_next
    if state.skip_next and not force:
        logger.info("state.skip_next=True, 本次跳过 (idx=%d)", state.next_idx)
        state.mark_skipped(state.next_idx, reason="skip_next")
        save_state(state, config.state_path)
        return state

    idx = state.next_idx

    # 1. 构造模块
    writer = NovelWriter(api_key=config.minimaxi_api_key)
    cover_gen = CoverGenerator(api_key=config.minimaxi_api_key)
    uploader = CoverUploader(
        base_url=config.obsidian_admin_base_url,
        admin_token=config.obsidian_admin_token,
    )
    hmac = HmacClient(
        HmacConfig(
            publish_id=config.obsidian_publish_id,
            publish_secret=config.obsidian_publish_secret,
        )
    )

    # 全局硬时限: 任何环节挂死都不会无限卡死 (SIGALRM 强制中断)
    hard_timeout_s = _resolve_hard_timeout(hard_timeout_s)
    _old_alarm = _arm_hard_timeout(hard_timeout_s)

    try:
        # 2. 选题
        logger.info("[%d] 选题中…", idx)
        topic = generate_one_shot(n_candidates=1)[0]

        # 3. 写章节
        logger.info("[%d] 写章节: %s", idx, topic.title)
        # 7-7 fix: 把 topic.genre_hint + outline 注入 truth_snapshot,
        # 让 _compose_cover_prompt 能拿到 category/chapter_goal, 不再降级到通用 prompt
        truth_snapshot: dict = {
            "topic": topic.title,
            "outline": topic.outline,
            "keywords": topic.keywords_used,
            "category": topic.genre_hint or "科幻",  # 兜底: 科幻 (本项目主题材)
            "chapter_goal": topic.outline[:200],  # 喂给封面 prompt
        }
        style_guide: dict = {
            "title": topic.title,
            "genre_hint": topic.genre_hint,
            "outline": topic.outline,
        }
        draft: ChapterDraft = writer.write_chapter(
            chapter_idx=idx,
            truth_snapshot=truth_snapshot,
            style_guide=style_guide,
        )

        # 4+5. 封面 (生成 + 上传) — 尽力而为, 失败不阻塞小说推送
        # 设计: 图片是可选增强, 挂了就降级为无封面纯文本推送 (cover_url="")
        # 任一步 (生成/下载/上传) 异常都被本地兜住, 不冒泡到外层 except
        cover_url = ""
        cover_path: Path | None = None
        try:
            config.cover_tmp_dir.mkdir(parents=True, exist_ok=True)
            logger.info("[%d] 画封面 (prompt=%s...)", idx, draft.cover_prompt[:40])
            cover_local_path = cover_gen.generate(
                prompt=draft.cover_prompt,
                chapter_idx=idx,
            )
            cp = Path(cover_local_path)
            if not cp.exists():
                raise CoverGenError(f"CoverGenerator 返回路径不存在: {cp}")
            logger.info("[%d] 上传封面到博客: %s", idx, cp)
            cover_result = uploader.upload(cp, title=f"ch-{idx:03d}")
            cover_url = cover_result.url
            cover_path = cp
        except Exception as e:
            logger.warning(
                "[%d] 封面失败, 降级为无封面推送 (小说正文不受影响): %s: %s",
                idx,
                type(e).__name__,
                e,
            )

        # 6. 渲染 markdown (cover_url 为空时 renderer 自动省略封面块)
        # 7-7 fix: 封面 URL 在 obsidian-journal 返回的是相对路径 (/uploads/xxx.jpg),
        # 在 Cloudflare 边缘 / 路由下会被误 404, 需要在 publisher 拼绝对 URL。
        # 官网 base 从 config.obsidian_admin_base_url 取 (与 .env OBSIDIAN_ADMIN_BASE_URL 一致)
        if cover_url and cover_url.startswith("/"):
            cover_url = config.obsidian_admin_base_url.rstrip("/") + cover_url

        # 7-7 fix: M3 v2 输出 ASCII 半角标点 (, . : ; ? ! ' "), 走一遍全角化
        # 7-7 修复: 在生成 draft 后立即 normalization, 一份 clean text 既给 renderer
        # 也给 GitHub 备份 (与发布上线的文本一致)
        clean_raw_text = normalize_cn_punctuation(draft.raw_text)
        # 7-8 P4: 「」 孤行合并 (L2, 渲染前 markdown_renderer 还有 L3 兜底)
        clean_raw_text = _merge_orphan_quotes(clean_raw_text)

        rendered = render_markdown(
            raw_text=clean_raw_text,
            cover_url=cover_url,
            chapter_idx=idx,
            chapter_title=topic.title,
        )

        # 7. HMAC 签名 + POST
        # 7-9 fix: 单本模式无 novel.id, 用 "single" + idx 作 stable key
        idem_key = stable_idempotency_key("single", idx)
        # 2026-07-08 fix: publisher 改推 /api/external/chapters (3-tier Novel + Volume + Chapter)
        # 取代 /api/external/posts (单层 Post, 落 /posts 列表, 架构错位)
        # 同 novel_slug 多次推送 → 同一 Novel + Volume + chapter order 自增
        body = {
            "novel_slug": "meta-realm",
            "novel_title": "元界",
            "novel_description": "一个关于意识、边界与觉醒的科幻故事。",
            "novel_status": "ongoing",
            "volume_title": "第一卷 · 星海之始",
            "volume_order": 1,
            "chapter_slug": f"meta-realm-ch{idx:03d}",
            "chapter_title": rendered.title,
            "chapter_content": rendered.content_markdown,
            "chapter_excerpt": rendered.excerpt,
            "chapter_published": True,
            "external_id": f"meta_realm_obsidian-ch{idx:03d}",
            "idempotency_key": idem_key,
        }
        # 7-7 fix: 与 obsidian-journal 服务端契约对齐, 签 over 真实 HTTP body (rawBody)
        # 服务端 verifyHmac(rawBody, ...) — 不能用 sort_keys+无空白 canonical, 否则中间格式不同 → bad_signature
        raw_body = json.dumps(body, ensure_ascii=False)
        sig_headers = hmac.sign(body, idempotency_key=idem_key, raw_body=raw_body)
        logger.info("[%d] 推送博客: POST %s", idx, config.obsidian_publish_url)

        resp = _post_with_sig(
            url=config.obsidian_publish_url,
            body=body,
            raw_body=raw_body,
            sig_headers=sig_headers,
        )

        # 8.5 GitHub 备份 (P3) — 失败不阻塞主推送, 仅 logger.warning
        if config.github_backup_token and config.github_backup_token.strip():
            try:
                backup = GithubBackup(
                    repo=config.github_backup_repo,
                    token=config.github_backup_token,
                    novel_id="single",  # 7-9 fix: run_once 是单本模式 (无 novel 对象), 用 "single" 隔离 (跟 idempotency_key 一致)
                )
                post_url = ""
                if isinstance(resp, dict):
                    # 2026-07-08: 推 /chapters 后响应是 {ok, chapter: {url, ...}}
                    post_url = (resp.get("chapter") or {}).get("url", "") or resp.get("url", "")
                backup_meta = ChapterMeta.now(
                    chapter_idx=idx,
                    title=topic.title,
                    word_count=draft.word_count,
                    llm_usage=draft.usage or {},
                    obsidian_post_url=post_url,
                )
                backup_result = backup.upload(
                    chapter_md=clean_raw_text,
                    cover_jpg=cover_path.read_bytes() if cover_path else b"",
                    meta=backup_meta,
                )
                logger.info(
                    "[%d] GitHub 备份 ✅ commit=%s files=%d",
                    idx,
                    backup_result.commit_sha[:12],
                    len(backup_result.pushed_files),
                )
            except (
                Exception
            ) as e:  # noqa: BLE001 备份阶段任何异常都不能阻主推送 (含 pydantic/网络/JSON)
                logger.warning(
                    "[%d] GitHub 备份失败 (主推送仍成功): %s: %s",
                    idx,
                    type(e).__name__,
                    e,
                )
        else:
            logger.info("[%d] GitHub 备份未配置 (GITHUB_BACKUP_TOKEN 缺失), 跳过", idx)

        # 8. 成功: 更新 state
        state.mark_pushed(idx, idem_key)
        save_state(state, config.state_path)
        logger.info("[%d] ✓ 推送成功: idem_key=%s resp=%s", idx, idem_key, resp)
        return state

    except LLMError as e:
        state.mark_failed(idx, f"LLM error: {e}")
        save_state(state, config.state_path)
        raise ChapterGenError(f"[{idx}] LLM 调用失败: {e}") from e
    except CoverUploadError as e:
        state.mark_failed(idx, f"cover upload error: {e}")
        save_state(state, config.state_path)
        raise CoverGenError(f"[{idx}] 封面上传失败: {e}") from e
    except RunTimeoutError as e:
        state.mark_failed(idx, f"hard timeout: {e}")
        save_state(state, config.state_path)
        logger.error("[%d] ✗ 命中全局硬时限 (%.0fs), 本次失败: %s", idx, hard_timeout_s, e)
        raise
    except Exception as e:
        state.mark_failed(idx, f"unexpected: {type(e).__name__}: {e}")
        save_state(state, config.state_path)
        raise PublisherError(f"[{idx}] 未预期错误: {e}") from e
    finally:
        _cancel_hard_timeout(_old_alarm)


def _post_with_sig(
    url: str, body: dict, sig_headers: dict[str, str], *, raw_body: str | None = None
) -> dict:
    """POST 到博客 + 验签 + 解析响应

    Args:
        url:         POST URL
        body:        请求体 dict (保留以便测试 / 调错)
        sig_headers: hmac.sign() 返回的 4 个 X-* 头
        raw_body:    实际发出去的 HTTP body 字节串; 须与签名时一致, 否则服务端验签失败。
                     不传则用 json.dumps(body) 默认序列化 (保证与签名侧一致)。
    """
    import requests

    payload = raw_body if raw_body is not None else json.dumps(body, ensure_ascii=False)
    headers = {"Content-Type": "application/json", **sig_headers}
    resp = requests.post(url, data=payload.encode("utf-8"), headers=headers, timeout=(10, 60))
    resp.raise_for_status()
    return resp.json()


# --------------------------------------------------------------------------
# CLI 入口
# --------------------------------------------------------------------------


def cli_main(argv: list[str] | None = None) -> int:
    """CLI 入口: `python -m src.publisher [--state PATH] [--skip-next|--clear-skip] [--force] [--dry-run]`

    Returns:
        exit code (0=success, 1=failed, 2=skipped)
    """
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="publisher",
        description="obsidian-novel-publisher CLI — 单次推送 / skip 控制 / 状态查看",
    )
    parser.add_argument("--state", type=Path, default=None, help="state.json 路径")
    parser.add_argument(
        "--skip-next",
        action="store_true",
        help="标记下次跳过 (用于 systemd timer 前老板手动说今天别发)",
    )
    parser.add_argument("--clear-skip", action="store_true", help="清除 skip_next 标记")
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略 skip_next 强制推送 (与 --skip-next 互斥)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="跑完整流程但不真推送 (仅到 HMAC 签名前, 用于本地调试)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="P2 多本并行: 遍历 novels.yaml 所有 enabled 小说, 各写 1 章 (错误隔离)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG 日志")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 仅 skip / clear-skip 路径
    if args.skip_next:
        state = load_state(args.state or DEFAULT_STATE_PATH)
        state.skip_next = True
        save_state(state, args.state or DEFAULT_STATE_PATH)
        print(f"✓ 已标记 skip_next (下次跳过 idx={state.next_idx})")
        return 0

    if args.clear_skip:
        state = load_state(args.state or DEFAULT_STATE_PATH)
        state.skip_next = False
        save_state(state, args.state or DEFAULT_STATE_PATH)
        print("✓ skip_next 已清除")
        return 0

    # 正常推送路径
    try:
        config = PublisherConfig.from_env()
        if args.state:
            config = PublisherConfig(
                minimaxi_api_key=config.minimaxi_api_key,
                minimaxi_base_url=config.minimaxi_base_url,
                minimaxi_text_model=config.minimaxi_text_model,
                minimaxi_image_model=config.minimaxi_image_model,
                obsidian_publish_url=config.obsidian_publish_url,
                obsidian_publish_id=config.obsidian_publish_id,
                obsidian_publish_secret=config.obsidian_publish_secret,
                obsidian_admin_token=config.obsidian_admin_token,
                obsidian_admin_base_url=config.obsidian_admin_base_url,
                state_path=args.state,
                cover_tmp_dir=config.cover_tmp_dir,
                github_backup_repo=config.github_backup_repo,
                github_backup_token=config.github_backup_token,
            )

        if args.dry_run:
            return _dry_run(config)

        if args.all:
            agg = run_all_novels(config, force=args.force)
            if agg.failed == 0 and agg.success > 0:
                return 0
            if agg.success == 0 and agg.failed > 0:
                return 1
            # 部分失败返 1 (避免 "success" 状态)
            return 1 if agg.failed > 0 else 0

        state = run_once(config, force=args.force)
        if state.last_status == "skipped":
            print("⊘ 跳过 (skip_next)")
            return 2
        if state.last_status == "success":
            print(
                f"✓ 推送成功 idx={state.last_pushed_idx} "
                f"next_idx={state.next_idx} at {state.last_pushed_at}"
            )
            return 0
        print(f"? 状态: {state.last_status}")
        return 1

    except PublisherError as e:
        logger.error("推送失败: %s", e)
        return 1


def _dry_run(config: PublisherConfig) -> int:
    """dry-run 模式: 加载 state + 配置校验 + 不真发请求"""
    print("=" * 60)
    print("DRY RUN — 不真推送, 仅校验")
    print("=" * 60)

    state = load_state(config.state_path)
    print(f"state.next_idx    = {state.next_idx}")
    print(f"state.last_status = {state.last_status}")
    print(f"state.skip_next   = {state.skip_next}")
    print(f"config.minimaxi_text_model  = {config.minimaxi_text_model}")
    print(f"config.minimaxi_image_model = {config.minimaxi_image_model}")
    print(f"config.obsidian_publish_url = {config.obsidian_publish_url}")
    print(f"config.obsidian_publish_id  = {config.obsidian_publish_id}")
    print(f"config.obsidian_admin_base_url = {config.obsidian_admin_base_url}")
    print("✓ config 校验通过 (下一步需 P3 github_backup + P4 接收侧)")
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())
