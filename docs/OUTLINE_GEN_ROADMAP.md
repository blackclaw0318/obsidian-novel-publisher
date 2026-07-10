# outline_gen 模块路线图 (v0.42)

> ⚠️ **P0 漏洞**: 当前 publisher 是 outline **read-only**, 新启用的书如果 outline.md 缺章节 header,
> 写章节时只能回退前 1500 字 (空 outline = 空字符串[:1500] = 写出垃圾)。
> 已实锤: 2026-07-10 meta_realm + glass_sea enable 时 outline 不存在, 本来要写垃圾。

## 设计

新增 `src/outline_gen.py` 模块, 包含:

### 1. `generate_chapter_outline(novel_id, chapter_idx) -> str`
- 调用 minimax M3 生成单章 outline (~200 token prompt, 输出 ~1500 字 markdown)
- prompt 模板: 基于 novels.yaml 的 `description` + 已写章节的最后一段 + keywords
- 返回 markdown 文本, 含 `### Ch{N} · ...` header

### 2. `append_to_outline(backup_repo, novel_id, chapter_outline_md)`
- 通过 GitHub API PUT 到 `novels/<novel_id>/outline.md`
- 自动追加到文件末尾 (`### Ch{N+1}` 之后)

### 3. publisher.py 集成
- 在 `_extract_chapter_outline` fallback 路径加:
  ```python
  if not m:
      # v0.42 fix: outline 缺本章 → 调 outline_gen 自动补
      from .outline_gen import generate_chapter_outline, append_to_outline
      chapter_outline = generate_chapter_outline(novel.id, idx)
      append_to_outline(github_backup, novel.id, chapter_outline)
      logger.info("[outline] auto-generated Ch%d (1/1)", idx)
      return chapter_outline
  ```

## 触发场景

- 新书 enabled, outline 仓为空
- Vol2+ 新卷, outline 只到上一卷
- 任何 outline.md 缺 `### Ch{N}` header 的情况

## 工作量

- 1 天: outline_gen.py + 集成 + 单测
- 0.5 天: outline_appender 走 GitHub API (复用 github_backup.py)
- 0.5 天: e2e 测试 + 文档

**总计: 2d**

## 测试

- 单测: outline_gen prompt 模板 + minimax mock
- 集成: publisher 在 outline 缺时自动调 outline_gen, 写完后 outline.md 包含新 header
- e2e: 启新书 (没 outline) → 跑一次 → outline 自动补 → 后续跑不重复补

## 优先级

P0 (2026-07-10 黑冷峻指出): **不修这个, 每本新书都得老板手动写 outline, 跟 LLM 自动写小说的初衷矛盾**。

