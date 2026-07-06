"""
大任务 P3: GitHub 备份 (私仓 obsidian-novel-backups)
====================================================
每次成功推送博客后, 把章节 (md + 封面 jpg + meta json) 备份到 GitHub 私仓,
作为 obsidian-journal 站点丢失时的离线副本。

设计:
- 走 GitHub Contents API (PUT /repos/{owner}/{repo}/contents/{path})
- 不走 git push (轻量, 无需本地 clone, 失败可重试)
- 编码 base64 (API 标准, 封面 ~200KB → ~270KB 可承受)
- 失败重试 3 次 (1s/2s/4s 指数退避), 4xx 不重试
- **失败不阻塞主推送**: 备份失败 → logger.warning + WeCom 告警, 不抛
- sha 必传: 覆盖已存在文件必须先 GET 拿 sha, 否则 422 错
- 写入路径:
    truth/novels/meta_realm_obsidian/chapters/ch-NNN.md
    truth/novels/meta_realm_obsidian/covers/ch-NNN.jpg
    truth/novels/meta_realm_obsidian/meta/ch-NNN.json
    truth/novels/meta_realm_obsidian/index.json   ← 追加, 不覆盖
    CHANGELOG.md                                  ← 追加每日汇总, 不覆盖

依赖: requests
凭据: GITHUB_BACKUP_TOKEN (fine-grained PAT, 至少 Contents: Read+Write on backups 仓)
"""

from __future__ import annotations

import base64
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

import requests
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 30
DEFAULT_MAX_RETRIES = 3
DEFAULT_NOVEL_ID = "meta_realm_obsidian"

# 重试退避秒数 (1s, 2s, 4s)
_RETRY_BACKOFF_S = (1, 2, 4)

# API 限速: 5000 req/h (auth) — 3 章/天 × 5 文件 = 15 req/天, 无压力
GITHUB_API_BASE = "https://api.github.com"


# ============================================================
# 异常族
# ============================================================


class GithubBackupError(Exception):
    """GitHub 备份失败 (重试耗尽 / 4xx 参数错 / 网络错)

    注: 调用方 (publisher.run_once) 应该 try/except 捕获, 不要 re-raise,
    备份失败不能回滚已成功的博客推送。
    """


# ============================================================
# 数据模型
# ============================================================


class ChapterMeta(BaseModel):
    """单章节元信息 (用于备份 + 索引 + 汇总)

    字段:
        chapter_idx:     章节号 (1-based, 0 保留给"未开始")
        title:           章节标题
        word_count:      中文字数 (不含标点)
        created_at:      ISO 8601 UTC (e.g. "2026-07-06T08:00:00.000Z")
        llm_usage:       LLM 调用统计 {prompt_tokens, completion_tokens, total_tokens}
        obsidian_post_url: 博客侧推送后的 post URL
        extra:           自由扩展字段 (novel_id, model, duration_ms, ...)
    """

    chapter_idx: int = Field(..., ge=0)
    title: str = Field(..., min_length=1)
    word_count: int = Field(..., ge=0)
    created_at: str = Field(..., min_length=1)
    llm_usage: dict[str, int] = Field(default_factory=dict)
    obsidian_post_url: str = Field(default="")
    extra: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def now(
        cls,
        chapter_idx: int,
        title: str,
        word_count: int,
        llm_usage: dict[str, int] | None = None,
        obsidian_post_url: str = "",
        **extra: Any,
    ) -> ChapterMeta:
        """便捷构造: 自动填 created_at = UTC now"""
        return cls(
            chapter_idx=chapter_idx,
            title=title,
            word_count=word_count,
            created_at=datetime.now(UTC).isoformat(),
            llm_usage=llm_usage or {},
            obsidian_post_url=obsidian_post_url,
            extra=extra,
        )


class BackupResult(BaseModel):
    """单次备份结果

    字段:
        commit_sha:   一次 upload 产生的 commit SHA (最后一次 PUT 的 commit)
        pushed_files: 推上去的文件路径列表 (相对仓库根, e.g. 'truth/.../ch-001.md')
    """

    commit_sha: str = ""
    pushed_files: list[str] = Field(default_factory=list)


# ============================================================
# 主类
# ============================================================


class GithubBackup:
    """GitHub Contents API 客户端 — 备份章节到 obsidian-novel-backups

    用法:
        backup = GithubBackup(repo="blackclaw0318/obsidian-novel-backups", token=...)
        result = backup.upload(
            chapter_md="# 第一章 ...\\n\\n正文...",
            cover_jpg=Path("/tmp/001.jpg").read_bytes(),
            meta=ChapterMeta.now(chapter_idx=1, title="第一章", word_count=3000, ...),
        )
        print(result.commit_sha)  # GitHub commit sha
    """

    def __init__(
        self,
        repo: str,
        token: str,
        branch: str = "main",
        novel_id: str = DEFAULT_NOVEL_ID,
        *,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        api_base: str = GITHUB_API_BASE,
    ):
        """
        Args:
            repo:        "owner/name" 格式 (e.g. "blackclaw0318/obsidian-novel-backups")
            token:       fine-grained PAT (Contents: Read+Write on backups 仓)
            branch:      目标分支 (默认 main)
            novel_id:    小说 ID (用于路径隔离, 默认 'meta_realm_obsidian')
            timeout_s:   HTTP 超时秒数
            max_retries: 失败重试次数 (默认 3)
            api_base:    API 根 URL (默认 https://api.github.com)
        """
        if "/" not in repo:
            raise ValueError(f"repo 必须是 'owner/name' 格式: {repo!r}")
        if not token or not token.strip():
            raise ValueError("token 不能为空 (或纯空白)")

        self.owner, self.name = repo.split("/", 1)
        self.token = token
        self.branch = branch
        self.novel_id = novel_id
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.api_base = api_base.rstrip("/")

    # ---------- public API ----------

    def upload(
        self,
        chapter_md: str,
        cover_jpg: bytes,
        meta: ChapterMeta,
    ) -> BackupResult:
        """完整备份一次: 3 文件 (md + jpg + json) + 更新 index.json + 更新 CHANGELOG.md

        Args:
            chapter_md: 完整章节 markdown 文本 (含封面图 URL, 由 markdown_renderer 渲染)
            cover_jpg:  封面图二进制 (从本地 /tmp/covers/NNN.jpg 读)
            meta:       章节元信息 (ChapterMeta, 含 chapter_idx / title / word_count / ...)

        Returns:
            BackupResult { commit_sha, pushed_files }

        Raises:
            GithubBackupError: 备份失败 (4xx 参数错 / 重试 3 次耗尽 / 网络错)
        """
        pushed: list[str] = []
        last_commit_sha = ""

        # 1. 推 3 个章节文件 (chapters / covers / meta)
        files_to_push: list[tuple[str, str | bytes, str]] = [
            (self._chapter_path(meta.chapter_idx), chapter_md, "text/markdown; charset=utf-8"),
            (self._cover_path(meta.chapter_idx), cover_jpg, "image/jpeg"),
            (self._meta_path(meta.chapter_idx), meta.model_dump_json(indent=2), "application/json"),
        ]

        for path, content, mime in files_to_push:
            logger.info("[github_backup] PUT %s (%d bytes)", path, len(content))
            # 3 章节文件: 幂等覆盖 (旧文件传 sha, 新文件不传)
            result = self._put_file(path, content, mime, meta, allow_overwrite=True)
            last_commit_sha = result["commit_sha"]
            pushed.append(path)

        # 2. 更新 index.json (追加, 不覆盖)
        index_path = self._index_path()
        existing_index = self._get_existing_content(index_path)
        index_data = self._build_index(existing_index, meta)
        logger.info("[github_backup] PUT %s (index)", index_path)
        result = self._put_file(
            index_path,
            json.dumps(index_data, ensure_ascii=False, indent=2),
            "application/json",
            meta,
            allow_overwrite=True,
        )
        last_commit_sha = result["commit_sha"]
        pushed.append(index_path)

        # 3. 更新 CHANGELOG.md (追加每日汇总, 不覆盖)
        changelog_path = "CHANGELOG.md"
        existing_changelog = self._get_existing_content(changelog_path) or "# CHANGELOG\n\n"
        changelog_data = self._build_changelog(existing_changelog, meta)
        logger.info("[github_backup] PUT %s (changelog)", changelog_path)
        result = self._put_file(
            changelog_path,
            changelog_data,
            "text/markdown; charset=utf-8",
            meta,
            allow_overwrite=True,
        )
        last_commit_sha = result["commit_sha"]
        pushed.append(changelog_path)

        logger.info(
            "[github_backup] ✅ 备份完成, commit=%s, files=%d",
            last_commit_sha[:12],
            len(pushed),
        )

        return BackupResult(commit_sha=last_commit_sha, pushed_files=pushed)

    # ---------- 路径生成 ----------

    def _chapter_path(self, idx: int) -> str:
        return f"truth/novels/{self.novel_id}/chapters/ch-{idx:03d}.md"

    def _cover_path(self, idx: int) -> str:
        return f"truth/novels/{self.novel_id}/covers/ch-{idx:03d}.jpg"

    def _meta_path(self, idx: int) -> str:
        return f"truth/novels/{self.novel_id}/meta/ch-{idx:03d}.json"

    def _index_path(self) -> str:
        return f"truth/novels/{self.novel_id}/index.json"

    # ---------- index + changelog 构建 ----------

    def _build_index(
        self,
        existing_content: str | None,
        meta: ChapterMeta,
    ) -> dict[str, Any]:
        """追加新章节到 index.json

        Schema:
            {
              "novel_id": "meta_realm_obsidian",
              "chapters": [
                {
                  "idx": 1, "title": "...", "word_count": 3000,
                  "created_at": "...", "obsidian_post_url": "...",
                  "files": {
                    "chapter": "truth/.../ch-001.md",
                    "cover": "truth/.../ch-001.jpg",
                    "meta": "truth/.../ch-001.json"
                  }
                }
              ],
              "updated_at": "..."
            }
        """
        if existing_content:
            try:
                data = json.loads(existing_content)
            except json.JSONDecodeError:
                logger.warning("[github_backup] index.json 解析失败, 重置为空 dict")
                data = {}
        else:
            data = {}

        chapters = data.get("chapters", [])
        # 追加 (idempotent: 同一 idx 已存在则覆盖条目, 避免重复)
        chapters = [c for c in chapters if c.get("idx") != meta.chapter_idx]
        chapters.append(
            {
                "idx": meta.chapter_idx,
                "title": meta.title,
                "word_count": meta.word_count,
                "created_at": meta.created_at,
                "obsidian_post_url": meta.obsidian_post_url,
                "files": {
                    "chapter": self._chapter_path(meta.chapter_idx),
                    "cover": self._cover_path(meta.chapter_idx),
                    "meta": self._meta_path(meta.chapter_idx),
                },
            }
        )
        chapters.sort(key=lambda c: c.get("idx", 0))

        return {
            "novel_id": self.novel_id,
            "chapters": chapters,
            "updated_at": datetime.now(UTC).isoformat(),
        }

    def _build_changelog(
        self,
        existing_content: str,
        meta: ChapterMeta,
    ) -> str:
        """追加每日汇总到 CHANGELOG.md

        格式:
            # CHANGELOG

            ## 2026-07-06

            ### ch-001 — 第一章 (2026-07-06T08:00:00Z)
            - 字数: 3000
            - 博客: https://...
            - 备份 commit: <sha>
        """
        date_str = meta.created_at[:10]  # YYYY-MM-DD
        section_header = f"## {date_str}"
        entry = (
            f"### ch-{meta.chapter_idx:03d} — {meta.title} "
            f"({meta.created_at})\n"
            f"- 字数: {meta.word_count}\n"
        )
        if meta.obsidian_post_url:
            entry += f"- 博客: {meta.obsidian_post_url}\n"
        if meta.llm_usage:
            tokens_total = sum(meta.llm_usage.values())
            entry += f"- LLM tokens: {tokens_total} (prompt={meta.llm_usage.get('prompt_tokens', 0)}, completion={meta.llm_usage.get('completion_tokens', 0)})\n"

        # 如果当日 section 不存在, 加 header; 已存在则插入到当日 section 末尾
        if section_header in existing_content:
            # 在 section_header 之后插入 (找下一个 ## 或文末)
            parts = existing_content.split(section_header, 1)
            before, after = parts[0], parts[1]
            # 找 after 里的下一个 ## (或文末)
            next_section_idx = after.find("\n## ")
            if next_section_idx == -1:
                # 末尾追加
                new_after = after.rstrip() + "\n\n" + entry
            else:
                new_after = (
                    after[:next_section_idx].rstrip() + "\n\n" + entry + after[next_section_idx:]
                )
            return before + section_header + new_after
        else:
            # 末尾追加新 section
            return existing_content.rstrip() + f"\n\n{section_header}\n\n{entry}"

    # ---------- GitHub Contents API 底层 ----------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _url(self, path: str) -> str:
        return f"{self.api_base}/repos/{self.owner}/{self.name}/contents/{path}"

    def _get_existing_content(self, path: str) -> str | None:
        """GET 已存在文件内容 (用于 index/changelog 追加)

        Returns:
            str:  原始内容 (utf-8 解码失败回退 latin-1)
            None: 文件不存在 (404) 或 API 错
        """
        url = self._url(path)
        try:
            resp = requests.get(url, headers=self._headers(), timeout=self.timeout_s)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            body = resp.json()
            content_b64 = body.get("content", "")
            # GitHub Contents API 返回 base64 with newlines
            content_b64_clean = content_b64.replace("\n", "")
            return base64.b64decode(content_b64_clean).decode("utf-8")
        except (requests.exceptions.RequestException, ValueError, KeyError) as e:
            logger.warning("[github_backup] GET %s 失败: %s (返回 None)", path, e)
            return None

    def _put_file(
        self,
        path: str,
        content: str | bytes,
        mime: str,
        meta: ChapterMeta,
        *,
        allow_overwrite: bool = False,
    ) -> dict[str, str]:
        """PUT 单个文件 (含重试)

        Args:
            path:            仓库内路径
            content:         文本或二进制
            mime:            MIME (仅 commit message 用, GitHub API 不需要)
            meta:            ChapterMeta (用于 commit message)
            allow_overwrite: True 内部 GET sha (新文件 404 → None, 旧文件 200 → sha, 幂等覆盖)
                             False 强制不查 sha (新文件专用, 旧文件会 422 错)

        Returns:
            {"commit_sha": "..."}

        Raises:
            GithubBackupError: 4xx 不重试, 5xx/网络错重试 3 次
        """
        # 1. base64 编码
        content_bytes = content.encode("utf-8") if isinstance(content, str) else content
        content_b64 = base64.b64encode(content_bytes).decode("ascii")

        # 2. 拿 sha (覆盖需要)
        sha: str | None = None
        if allow_overwrite:
            sha = self._get_existing_sha(path)

        # 3. commit message
        commit_msg = self._commit_message(path, meta, is_overwrite=sha is not None)

        # 4. PUT body
        body: dict[str, Any] = {
            "message": commit_msg,
            "content": content_b64,
            "branch": self.branch,
        }
        if sha:
            body["sha"] = sha

        url = self._url(path)

        # 5. 重试循环
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.debug(
                    "[github_backup] PUT attempt %d/%d %s (%d bytes)",
                    attempt,
                    self.max_retries,
                    path,
                    len(content_bytes),
                )
                resp = requests.put(url, headers=self._headers(), json=body, timeout=self.timeout_s)

                # 4xx: 参数错, 立即抛
                if 400 <= resp.status_code < 500:
                    raise GithubBackupError(
                        f"4xx ({resp.status_code}) PUT {path} 失败 (不重试): {resp.text[:300]}"
                    )

                # 5xx: 重试
                if resp.status_code >= 500:
                    raise GithubBackupError(f"5xx ({resp.status_code}) PUT {path} 服务器错 (重试)")

                resp.raise_for_status()
                resp_body = resp.json()
                commit_sha = resp_body.get("commit", {}).get("sha", "")
                logger.info(
                    "[github_backup] PUT %s ✅ commit=%s",
                    path,
                    commit_sha[:12] if commit_sha else "(empty)",
                )
                return {"commit_sha": commit_sha}

            except requests.exceptions.RequestException as e:
                last_err = e
                logger.warning(
                    "[github_backup] PUT %s 网络错 (attempt %d/%d): %s",
                    path,
                    attempt,
                    self.max_retries,
                    e,
                )

            except GithubBackupError as e:
                last_err = e
                # 4xx 立即抛, 5xx 走重试
                if "4xx" in str(e):
                    raise
                logger.warning(
                    "[github_backup] PUT %s 5xx (attempt %d/%d): %s",
                    path,
                    attempt,
                    self.max_retries,
                    e,
                )

            # 退避 (最后一次不睡)
            if attempt < self.max_retries:
                sleep_s = _RETRY_BACKOFF_S[min(attempt - 1, len(_RETRY_BACKOFF_S) - 1)]
                time.sleep(sleep_s)

        # 重试耗尽
        raise GithubBackupError(f"PUT {path} 重试 {self.max_retries} 次后仍失败: {last_err}")

    def _get_existing_sha(self, path: str) -> str | None:
        """GET 单个文件 sha (用于覆盖)

        Returns:
            str:  文件 sha (覆盖时必传)
            None: 文件不存在 (新文件, 不传 sha)
        """
        url = self._url(path)
        try:
            resp = requests.get(url, headers=self._headers(), timeout=self.timeout_s)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json().get("sha")
        except requests.exceptions.RequestException as e:
            logger.warning("[github_backup] GET %s sha 失败: %s", path, e)
            return None

    def _commit_message(self, path: str, meta: ChapterMeta, *, is_overwrite: bool) -> str:
        """生成 commit message

        新文件: 'backup ch-001: 第一章'
        覆盖:   'update index.json for ch-001: 第一章'
        """
        basename = path.rsplit("/", 1)[-1] if "/" in path else path
        if is_overwrite and (basename == "index.json" or basename == "CHANGELOG.md"):
            return f"update {basename} for ch-{meta.chapter_idx:03d}: {meta.title}"
        return f"backup ch-{meta.chapter_idx:03d}: {meta.title}"
