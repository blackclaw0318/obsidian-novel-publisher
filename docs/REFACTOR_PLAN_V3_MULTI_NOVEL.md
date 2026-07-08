# obsidian-novel-publisher — 多本连载 + 风格化封面 v3 重构方案

> **作者**: 黑 (Hei)
> **日期**: 2026-07-08
> **状态**: ⏸️ 等老板拍 Q1-Q6 后启动 P0
> **背景**: 老板 7-8 09:40 提出 4 大问题, 现有 v0.2 架构 (单本一次性选题 + 硬编码 + 通用封面) 不能满足
> **目标版本**: v0.3 (大版本, 架构级重构)
> **估时**: 5 工作日
> **核心变化**: 单本一次性 → 多本并行 · 一次性选题 → 老板审过大纲后开写 · 通用封面 prompt → 风格指南驱动

---

## 0. 老板原始需求 (4 大问题)

> 1. **我无法指定小说选题** — 当前 M3 自行脑洞, 我希望自己指定关键词/方向
> 2. **测试 3 篇是独立小说, 不是连载** — 我需要**每天同时更新多本小说**, 每本有自己的大纲, **开新书前把大纲推到 backups 让我审核修改**, 之后每次写正文前**先拉取最新大纲** (老板可能改了)
> 3. **封面风格过于固定** (都是赛博朋克写实) — 同本小说每张封面可固定, **不同小说应有差异**, 同一人物/场景**一致性**, **封面 prompt 应随大纲一起推到 backups 让我改**
> 4. **「」标点容易把 」 单放一行** — 不好看, 请修排版增加可读性

---

## 1. 现状 vs 目标对比

| 维度 | v0.2 现状 | v0.3 目标 |
|---|---|---|
| 选题 | topic_gen 一次性 M3 脑洞, 无关键词入口 | `novels.yaml` 显式声明每本 + 关键词, M3 按指定方向 |
| 小说数量 | 单本 `meta_realm_obsidian` 硬编码 | **多本并行**, `novels.yaml` 列表 + `enabled` 开关 |
| 大纲 | M3 即兴, 不持久化, 不开放修改 | **backups 仓持久化**, 老板在 GitHub PR 修改, publisher 每次拉最新 |
| 风格指南 | `style_guide` dict 随章节生成, 不固定 | `style_guide.md` 持久化 (含 style_prompt + character_refs + scene_palette) |
| 人物一致性 | 无机制 | character_refs 固定描述, 跨章复用 |
| 封面 prompt | M3 即兴, 默认走"通用降级" prompt | style_guide.md 显式 prompt, 老板可改 |
| 排版 | `text_punct.py` 标点规范化, **未处理单字符行** | **加 `_merge_orphan_quotes`** + prompt 强化 |
| state | 单 `data/state.json` 全局一份 | **按 novel_id 拆分** `data/state/{novel_id}.json` |
| backups 推送 | 已实现 (P3) | **扩展**: outline/style_guide/characters 也推 + 开新书审核流 |

---

## 2. 新架构 — 文件与数据流

### 2.1 `novels.yaml` (仓库根, 老板可改)

```yaml
# ============================================================
# obsidian-novel-publisher — 多本小说注册表
# 老板可手编, 改后 commit 即可 (publisher 启动时读)
# ============================================================
novels:
  - id: meta_realm_obsidian
    title: 元界
    description: 一个关于意识、边界与觉醒的科幻故事。
    status: ongoing            # ongoing | completed | hiatus
    enabled: true
    daily_chapter: true         # 8/12/18 三档时是否为本本写章 (false = 跳过)
    target_word_count: 3000
    category: 科幻
    keywords: [意识上传, 月球基地, AI 觉醒]  # 用于 M3 关键词模式
    # backups 仓路径 (相对 backups 仓根)
    paths:
      outline: novels/meta_realm_obsidian/outline.md
      outline_meta: novels/meta_realm_obsidian/outline.meta.json
      style_guide: novels/meta_realm_obsidian/style_guide.md
      characters: novels/meta_realm_obsidian/characters.md
      state: novels/meta_realm_obsidian/state.json
      chapters_dir: novels/meta_realm_obsidian/chapters/
    created_at: 2026-07-05T00:00:00Z
  - id: glass_sea_obsidian
    title: 玻璃海
    description: 沿海小城, 退潮后海滩上出现的不该存在的东西。
    status: ongoing
    enabled: false              # ⚠️ 暂未开 (大纲待老板审)
    daily_chapter: true
    target_word_count: 3000
    category: 玄幻
    keywords: [退潮, 海市蜃楼, 失忆, 玻璃海]
    paths:
      outline: novels/glass_sea_obsidian/outline.md
      outline_meta: novels/glass_sea_obsidian/outline.meta.json
      style_guide: novels/glass_sea_obsidian/style_guide.md
      characters: novels/glass_sea_obsidian/characters.md
      state: novels/glass_sea_obsidian/state.json
      chapters_dir: novels/glass_sea_obsidian/chapters/
    created_at: 2026-07-08T00:00:00Z

# 推送时间窗 (systemd timer 配置, 这里只声明 publisher 期望节奏)
schedule:
  hours: [8, 12, 18]  # 每天 3 次
  per_run_novel_limit: 1  # 每次 run_once 写 1 章 (1 本) — 不堆压 LLM
```

### 2.2 backups 仓 `blackclaw0318/obsidian-novel-backups` 新结构

```
obsidian-novel-backups/
├── README.md                                  # 老板可读 (workflow 说明)
├── _index.json                                # 全小说索引 (自动更新)
├── novels/
│   ├── meta_realm_obsidian/                   # 第 1 本 (已存在 3 章)
│   │   ├── outline.md                         # 老板可编辑
│   │   ├── outline.meta.json                  # 结构化 (自动解析)
│   │   ├── style_guide.md                     # 老板可编辑 (含封面 prompt)
│   │   ├── characters.md                      # 老板可编辑 (人物卡)
│   │   ├── state.json                         # 自动 (publisher 写)
│   │   ├── chapters/
│   │   │   ├── ch-001.md
│   │   │   ├── ch-001.cover.jpg
│   │   │   ├── ch-001.meta.json
│   │   │   ├── ch-002.md
│   │   │   └── ...
│   │   └── CHANGELOG.md                       # 自动 (每日汇总)
│   └── glass_sea_obsidian/                    # 第 2 本 (待审)
│       ├── outline.md                         # ⏳ 老板审
│       ├── outline.meta.json
│       ├── style_guide.md                     # ⏳ 老板审
│       ├── characters.md                      # ⏳ 老板审
│       └── state.json
```

### 2.3 `data/` (本机 publisher 工作区, gitignored)

```
data/
├── state/                           # ⭐ 新拆分 (per-novel)
│   ├── meta_realm_obsidian.json
│   └── glass_sea_obsidian.json
├── covers/
│   ├── meta_realm_obsidian/
│   │   ├── 001.jpg
│   │   └── ...
│   └── glass_sea_obsidian/
├── cache/                           # 拉取的 outline/style_guide/characters 缓存
│   ├── meta_realm_obsidian/
│   │   ├── outline.md
│   │   ├── style_guide.md
│   │   └── characters.md
│   └── glass_sea_obsidian/
└── logs/
```

---

## 3. 核心工作流 (老板可读)

### 3.1 启动新书流 (`/new-novel`)

```
老板在 webchat 说: "开新书, 关键词: 退潮, 玻璃海, 失忆"
                                  ↓
        ┌─────────────────────────────────────────┐
        │ topic_gen.create_novel_draft(keywords)  │
        │  - 调 M3 生成 outline (300-500 字)       │
        │  - 调 M3 生成 style_guide (含封面 prompt) │
        │  - 调 M3 生成 characters.md              │
        │  - 自动生成 id = "{genre}_obsidian_{ts}"  │
        └──────────────────┬──────────────────────┘
                           ↓
        PUT 到 backups 仓 novels/{id}/:
        - outline.md + outline.meta.json
        - style_guide.md
        - characters.md
        - state.json (init, last_pushed_idx=0)
                           ↓
        PR 链接推给老板 (webchat / WeCom)
                           ↓
        ⏳ 老板在 GitHub 上审 outline / style_guide / characters
           - 直接 commit 修改
           - 或评论改哪儿
                           ↓
        老板说 "通过, 开写" → novels.yaml 加 enabled: true → commit
```

### 3.2 日常推送流 (systemd timer 8/12/18)

```
publisher run_once
  │
  ├─ load novels.yaml → 拿到 enabled 列表
  │
  ├─ for novel in enabled_novels:
  │   ├─ 1. 拉最新 outline + style_guide + characters
  │   │     (GET backups 仓 raw 链接, 缓存到 data/cache/{id}/)
  │   │
  │   ├─ 2. 检测 outline.md 在 backups 的 sha
  │   │     (若 sha != 上次记录的 sha → 说明老板改了 → 记录到 log)
  │   │
  │   ├─ 3. 读 data/state/{id}.json
  │   │     - next_idx = 4 (准备写 ch-004)
  │   │     - last_pushed_idx = 3
  │   │
  │   ├─ 4. 检查 novel.daily_chapter & 配额
  │   │     (每 8h 一次, 一本一天 1 章 — 防 publisher 跑飞)
  │   │
  │   ├─ 5. M3 写章节 (prompt 注入)
  │   │     user.txt 模板新增字段:
  │   │       {outline}              ← 从 backups 拉
  │   │       {characters}           ← 从 backups 拉
  │   │       {style_guide}          ← 从 backups 拉
  │   │       {prev_chapter_summary} ← 从 ch-(N-1) meta.json 读
  │   │
  │   ├─ 6. 画封面 (prompt 注入 style_guide)
  │   │     cover_prompt 模板:
  │   │       base: {chapter_scene}  ← 从本章正文抽
  │   │       style: {style_prompt}  ← style_guide.md
  │   │       characters: {char_refs} ← characters.md 主角描述
  │   │
  │   ├─ 7. 推 obsidian-journal (POST /api/external/chapters)
  │   │     body 字段:
  │   │       novel_slug, novel_title, novel_description, novel_status
  │   │       volume_title, volume_order
  │   │       chapter_slug, chapter_title, chapter_content, chapter_excerpt
  │   │       external_id = "{id}-ch{idx:03d}"
  │   │
  │   ├─ 8. 推 backups (md + jpg + meta)
  │   │     truth/novels/{id}/chapters/ch-NNN.{md,jpg}
  │   │     truth/novels/{id}/chapters/ch-NNN.meta.json
  │   │     truth/novels/{id}/state.json (auto)
  │   │     truth/novels/{id}/CHANGELOG.md (append)
  │   │
  │   └─ 9. 更新 data/state/{id}.json (mark_pushed)
  │
  └─ 全部完成 → 写日报
```

---

## 4. 4 大问题对应解法

### 4.1 问题 1: 指定选题

**解法**: `novels.yaml` 显式 `keywords: [...]` + `topic_gen` 改用关键词驱动路径

**代码** (`src/topic_gen.py` 加):
```python
def create_novel_draft(
    keywords: list[str],
    genre_hint: str,
    novel_id: str,
    title_hint: str = "",
) -> NovelDraft:
    """关键词驱动生成大纲 + 风格指南 + 人物卡 (开新书用)

    Returns:
        NovelDraft(outline_md, outline_meta, style_guide_md, characters_md)
    """
    outline = _gen_outline(keywords, genre_hint, title_hint)        # M3 300-500 字
    style_guide = _gen_style_guide(outline, genre_hint)             # M3 封面 prompt + 色板
    characters = _gen_characters(outline)                            # M3 主角 + 配角卡
    outline_meta = _parse_outline_to_meta(outline, keywords, novel_id)
    return NovelDraft(outline, outline_meta, style_guide, characters)
```

**老板拍 Q1**: novels.yaml 放 `repo root` 还是 `config/novels.yaml`? (黑推荐 root, 与 README/PLAN 平级)

### 4.2 问题 2: 多本并行 + 老板审大纲

**解法**: 3 段式 (开新书 → 审核 → 日常写章), 详见 §3.1 + §3.2

**关键代码**:
- `src/novel_registry.py` (新): `load_novels() → list[Novel]`, 单例缓存
- `src/novel_outline.py` (新): `fetch_outline(novel) → str`, GET backups raw URL + 缓存 + sha 检测
- `src/style_guide.py` (新): `fetch_style_guide(novel) → StyleGuide`, 同上
- `src/state_per_novel.py` (新): `PublishState` 拆分路径 `data/state/{novel_id}.json`
- `src/publisher.py`: `run_once` 改 loop, 单本失败不阻塞其他

**老板拍 Q2-Q3**:
- Q2: 一次 run_once 写 N 本 vs 1 本? (黑推荐 **每 8h 写 1 章, 3 次/天可切 3 本** — 详见 Q5)
- Q3: 老板在 GitHub 直接 commit 改 outline 后, publisher 是否需要通知"已采用你的修改"? (黑推荐**不通知**, state.sha 记录 + 日报里显示)

### 4.3 问题 3: 封面风格多样 + 人物一致

**解法**: `style_guide.md` 持久化 + M3 注入 cover_prompt

**`style_guide.md` 模板** (开新书时 M3 生成, 老板可改):
```markdown
# 元界 — 风格指南

## 风格主调
日式水墨 + 极简科幻, 主色调蓝灰 + 月光银, 留白多, 远景人物剪影为主。
- 不要: 赛博朋克霓虹, 暖色调, 高饱和度
- 镜头: 大量负空间, 单点光源, 仰视

## 核心人物 (固定, 跨章一致)
- **林深 (主角)**: 28 岁东亚男性, 短黑发, 银白实验服, 1.82m, 瘦削, 右手腕旧疤
- **月小满 (AI 助手)**: 虚拟投影, 少女轮廓, 月白长袍, 周围有微光粒子
- **陈砚 (导师)**: 60+ 银发, 深灰长衫, 戴圆框眼镜, 笑容温和

## 场景色板
- 月球基地: 蓝灰 #2C3E50 + 月光银 #C0C0C0 + 警示橙 #FF6B35
- 意识空间: 紫黑 #1A0B2E + 萤光 #00F5D4
- 飞船内部: 深木红 #4A2C2A + 黄铜 #B08D57

## 封面 prompt 模板 (image-01 英文)
```
A minimalist sci-fi book cover in Japanese ink-wash style.
Main character: {char_main_description} (consistent across all chapters).
Scene: {chapter_scene_specific}.
Color palette: {scene_palette}.
Style: hand-drawn, low saturation, negative space, single light source.
No text, no logos, no faces with eyes.
3:4 aspect ratio, painterly texture.
```

## 章节场景示例 (ch-001)
- 场景: 月球基地 A3 实验舱, 主角林深独自面对漂浮的样本瓶
- 构图: 仰视, 主角剪影, 蓝灰背景, 月光从左侧洒入
```

**关键代码** (`src/cover_gen.py` 改):
```python
def compose_chapter_cover_prompt(
    *,
    chapter_idx: int,
    chapter_scene: str,
    style_guide: StyleGuide,   # 从 backups 拉
    characters: Characters,    # 从 backups 拉
) -> str:
    """组合章节封面 prompt: 章节场景 + 风格指南 + 人物描述"""
    template = style_guide.cover_prompt_template
    return template.format(
        chapter_scene_specific=chapter_scene,
        char_main_description=characters.main.description,  # 林深固定描述
        scene_palette=style_guide.scene_palette_for_chapter(chapter_idx),
    )
```

**一致性机制** (跨章主角不"换脸"):
- 主角描述 `林深: 28 岁东亚男性, 短黑发, 银白实验服, ...` 100% 复用
- 场景色板按章节场景切换 (但总在 style_guide 色板内)
- prompt 强制 "consistent across all chapters" 短语

**多样性机制** (跨小说风格差异):
- 不同小说用不同 `style_guide.md`, style_prompt 完全不同
- e.g. 元界=水墨科幻 / 玻璃海=印象派海景 / 灰烬局=硬核废土

**老板拍 Q4-Q5**:
- Q4: style_guide.md 老板改后, publisher 检测到 outline/style_guide 矛盾时 (e.g. style 写"水墨", outline 写"赛博朋克") 怎么办? (黑推荐**log warning 继续跑, 不阻断** — 老板审美是 source of truth)
- Q5: 人物一致性用 prompt 描述 vs image-01 image reference? (黑推荐**先用 prompt 描述, 后续若 image-01 支持 image-2-image 引用固定角色再升级**)

### 4.4 问题 4: 「」 排版

**根因**: LLM 输出按 token 拆, 引号被换行拆开
- 出现: `「喂, ` + 换行 + `」` + 换行 + `。`
- 不出现: 中文标点规范 GB/T 15834 规定引号紧贴文字

**解法**: 3 层防护

**Layer 1 — Prompt 强化** (`assets/prompts/system.txt` 加):
```
【排版铁律 (7-8 补充)】★ 严禁违反
- 「 不允许单独出现在行首, 除非是段首引语
- 」 不允许单独占一行, 必须紧贴上一行末尾文字
- 引语中间换行时, 第一个引号可换行, 后续文字紧贴, 收尾的 」 紧贴最后一句
- 段落之间用一个空行分隔, 严禁连续 2+ 空行
- 章节首段不留空行, 直接顶格

【正例】
她低声说:「我做了一个梦,
梦里全是光。」

【反例 — 严禁】
她低声说:「我做了一个梦,
梦里全是光。
」
```

**Layer 2 — 后处理正则** (`src/text_punct.py` 加):
```python
def _merge_orphan_quotes(text: str) -> str:
    """合并单字符行: 「\\n + \\n」 → 删中间换行

    规则:
    1. 「 紧跟换行 (允许中间少量空白) → 删换行
    2. 换行 (允许中间少量空白) 紧跟 」 → 删换行
    3. 」 单独成行 → 合并到上一行
    4. 多个连续空行 → 单个空行
    5. 段首空行 → 删
    """
    # 1. 「 后接换行 → 删
    text = re.sub(r"「\s*\n+", "「", text)
    # 2. 换行 后接 」 → 删
    text = re.sub(r"\n+\s*」", "」", text)
    # 3. 段落开头空行 → 删
    text = re.sub(r"^\s*\n+", "", text)
    # 4. 多空行 → 单空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text
```

**Layer 3 — Renderer 兜底** (`src/markdown_renderer.py`):
- 检测到孤行 `「` 或 `」`, 合并到上下行
- 段落渲染前过一遍 `_merge_orphan_quotes`

**测试** (`tests/integration/text_punct_orphan_quotes.test.py`):
```python
# 用例 1: 老板截图的 3 种反例 + 期望正例
def test_orphan_close_quote_merged_to_prev():
    text = "她低声说:「我做了一个梦,\n梦里全是光。\n」"
    out = _merge_orphan_quotes(text)
    assert "光。」" in out
    assert "\n」" not in out

# 用例 2: 引号内强制换行也合并
def test_quote_with_internal_newline():
    text = "「第一行\n第二行\n」"
    out = _merge_orphan_quotes(text)
    # 引号内换行保留 (叙事需要), 但首尾引号紧贴文字
    assert out.startswith("「")
    assert "第二行」" in out
```

**老板拍 Q6**: 排版修复只走后处理 vs 后处理 + prompt 双保险? (黑推荐**双保险, 1+2+3 全上**)

---

## 5. 文件改动清单 (按模块)

### 5.1 新增文件 (5 个)

| 文件 | LOC (估) | 作用 |
|---|---|---|
| `novels.yaml` | 50 | 多本注册表 (老板可改) |
| `src/novel_registry.py` | 120 | `load_novels() → list[Novel]` + 校验 |
| `src/novel_outline.py` | 180 | 拉 + 缓存 + sha 检测 outline.md |
| `src/style_guide.py` | 150 | 拉 + 解析 + 应用 style_guide.md |
| `src/state_per_novel.py` | 100 | state 按 novel_id 拆分 |
| `src/cover_prompt_builder.py` | 100 | style_guide → chapter cover prompt 组合 |
| `tests/integration/multi_novel.test.py` | 200 | 多本并行流测试 |
| `tests/integration/text_punct_orphan_quotes.test.py` | 80 | 排版用例 |

### 5.2 修改文件 (7 个)

| 文件 | 改动 | 估 LOC |
|---|---|---|
| `src/publisher.py` | `run_once` 改 loop 多本 + 错误隔离 + 配额 | +60 |
| `src/topic_gen.py` | 加 `create_novel_draft()` 开新书 | +120 |
| `src/github_backup.py` | 加 `upload_outline` / `upload_style_guide` / `upload_characters` / `upload_chapter` | +150 |
| `src/cover_gen.py` | `compose_chapter_cover_prompt()` 注入 style_guide | +30 |
| `src/text_punct.py` | 加 `_merge_orphan_quotes()` | +30 |
| `src/markdown_renderer.py` | 渲染前过 `_merge_orphan_quotes` 兜底 | +10 |
| `assets/prompts/system.txt` | 排版铁律 + 人物一致性约束 | +30 |
| `assets/prompts/user.txt` | 模板字段 {outline} {characters} {style_guide} {prev_chapter_summary} | +20 |
| `assets/prompts/topic_user.txt` | 关键词驱动 + 风格偏好 | +15 |

### 5.3 backups 仓初始化 (1 commit)

| 操作 | 内容 |
|---|---|
| PUT `README.md` | 老板可读: 工作流说明 + 4 大原则 |
| PUT `_index.json` | `{"novels": ["meta_realm_obsidian", ...], "updated_at": "..."}` |
| PUT `novels/meta_realm_obsidian/outline.md` | (从 truth 现有的 outline 迁移) |
| PUT `novels/meta_realm_obsidian/style_guide.md` | (新建, 7-8 老板审) |
| PUT `novels/meta_realm_obsidian/characters.md` | (新建, 7-8 老板审) |
| PUT `novels/meta_realm_obsidian/state.json` | (从现有 state.json 迁移) |
| PUT `novels/meta_realm_obsidian/chapters/*` | (从现有 truth 迁移, 已存在) |

---

## 6. 实施步骤 (估时 5d)

| 阶段 | 内容 | 估时 | 累计 |
|---|---|---|---|
| **P0** | novels.yaml + novel_registry + state 拆分 + backups README | 1d | 1d |
| **P1** | novel_outline + style_guide + characters 拉取/缓存 | 1d | 2d |
| **P2** | publisher.run_once 改 loop 多本 + 错误隔离 | 0.5d | 2.5d |
| **P3** | cover_prompt_builder + style_guide 驱动 + 人物一致性 | 0.5d | 3d |
| **P4** | text_punct 排版修复 + prompt 强化 + 测试 | 0.3d | 3.3d |
| **P5** | 集成测试 + 真 dry-run + 推 GitHub + 文档 | 1d | 4.3d |
| **P6** | (可选) 接入 systemd timer 多本错峰 | 0.7d | 5d |

**关键里程碑**:
- P0 完: 能用 `python -m src.novel_registry` 列出 2 本
- P1 完: 能用 `python -m src.novel_outline fetch --novel meta_realm_obsidian` 拉大纲
- P2 完: `python -m src.publisher run-once --all` 串行写 2 本
- P3 完: 不同小说 prompt 输出风格可区分 (人工验)
- P4 完: `text_punct_orphan_quotes` 测试 100% 过
- P5 完: 推 GitHub + dry-run PASS + 老板拍可上 prod

---

## 7. 数据迁移 (老 3 章)

| 老位置 (v0.2) | 新位置 (v0.3) |
|---|---|
| `truth/novels/meta_realm_obsidian/chapters/ch-001.md` | (backups 仓) `novels/meta_realm_obsidian/chapters/ch-001.md` |
| `truth/novels/meta_realm_obsidian/chapters/ch-001.jpg` | (backups 仓) `novels/meta_realm_obsidian/chapters/ch-001.cover.jpg` |
| `data/state.json` | (拆分) `data/state/meta_realm_obsidian.json` + 老路径保留作 fallback |

**老板拍 Q7**: 老的 `truth/` 目录保留作 v0.2 历史归档, 还是清掉? (黑推荐**保留 1 个月, 加 .archive 标记**)

---

## 8. 风险表 (v0.3 风险评估)

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| backups 仓 README/结构老板看不懂 | 🟡 中 | 老板不会审 | README 写明"改这 3 个文件"具体指引 |
| style_guide.md 老板改后, 后续 prompt 注入崩 | 🟢 低 | publisher 跑挂 | schema 校验 (缺字段报错) + 回滚到 defaults |
| 多本并行, 配额失控 (3 次/天 写 9 本) | 🟡 中 | LLM 成本爆 | novels.yaml `per_run_novel_limit: 1` 限流 |
| 排版修复改了老章节渲染 | 🟢 低 | 排版变化 | 只对**新推**的章节生效, 老的保留 |
| outline.md M3 生成的 500 字太粗, 写 50 章就崩 | 🟡 中 | 中期连贯性断 | 老板审 + 持续修改 + 200 章内会重生成 |
| image-01 同人物跨章做不到像素一致 | 🟡 中 | 老板感受"换脸" | 详细 char description + 低 acceptance threshold + prompt 锁词 |

---

## 9. 老板决策清单 (Q1-Q7, 拍后启动 P0)

| # | 决策 | 候选 | 黑推荐 |
|---|---|---|---|
| **Q1** | novels.yaml 位置 | `repo root` / `config/novels.yaml` / `novels/{id}.yaml` | **repo root** (与 README/PLAN 平级, 一眼可见) |
| **Q2** | 一次 run_once 写几本? | 1 本 / N 本 (按 novels.yaml 数) / 单本轮转 | **1 本 (轮转)** (8h 写 1 章, 3 次/天 = 3 本轮流, 1 天 3 本都有更新) |
| **Q3** | 老板在 GitHub 改 outline 后通知方式? | 不通知 + 日报显示 / WeCom 实时通知 / GitHub PR comment | **不通知 + 日报显示** (state.outline_sha 记录, 老板看日报知道自己的修改被采用了) |
| **Q4** | style_guide vs outline 矛盾时? | log warning 继续 / 抛出要求人工解决 / 静默用 outline | **log warning 继续** (老板审美 source of truth, 矛盾可能是剧情推进中的演变) |
| **Q5** | 人物一致性机制? | prompt 描述 / image reference (image-2-image) / 两者 | **先 prompt 描述** (image-01 不支持稳定 char ref, 等 2026 后续模型升级再升级) |
| **Q6** | 「」 排版 3 层防护? | 仅后处理 / 仅 prompt / 后处理 + prompt + renderer | **3 层全上** (3 段独立保险, 任一段漏另一段兜) |
| **Q7** | 老的 `truth/` 目录处理? | 保留 1 月归档 / 立即清 / 保留不归档 | **保留 1 月** (`.archive` 标记, 30 天后清理) |

---

## 10. 不在 v0.3 范围 (留 v0.4+)

- ❌ image-01 升级到 image-2-image 引用固定角色 (等模型升级)
- ❌ 章节级大纲细纲 (e.g. 30 章后大纲漂移, 需要 chapter-level outline) — v0.4
- ❌ 多小说世界观交叉 (e.g. 元界 + 玻璃海 同一宇宙) — v0.4
- ❌ Web 端老板审大纲的可视化界面 — v0.4 (短期 GitHub 直接看)
- ❌ 自动从老章节摘要生成下章大纲 (self-prompt) — v0.4

---

## 11. 实施前置 (老板拍后立即开 P0)

1. ⏳ 等老板拍 Q1-Q7 (本文 §9)
2. ⏳ 等老板把现有 3 章状态 approve
3. ⏳ 等 obsidian-novel-backups 仓 README 写好 (P0 第 1 件事)
4. ⏳ 等老板决定 Q5 (人物一致性机制) — 决定 cover_prompt_builder 复杂度

---

## 12. 引用

- 老板 4 大问题: 2026-07-08 09:40 webchat
- 现状勘察: `src/{publisher,topic_gen,state,text_punct}.py` + `assets/prompts/*.txt` (7-8 10:50)
- `/api/external/chapters` 契约: `projects/obsidian-journal/app/api/external/chapters/route.ts` (v0.38 P2)
- 上一份方案: `docs/PLAN_NOVEL_AUTHOR_UNIFY.md` (v3 实证完成, 不冲突)
- 老 3 章数据: `data/state.json` (next_idx=4, last_pushed_idx=3)

---

# 🔄 v0.3.1 补充 (2026-07-08 11:24 老板新提 2 点)

> **背景**: 原方案 v0.3 老板看后补充 2 点, 黑勘察后并入新设计 (不重写原 §1-§11)
> **来源**: https://www.shangkun.uk/novels + https://www.shangkun.uk/novels/meta-realm (老板给链接)
> **勘察时间**: 2026-07-08 11:25

---

## 13. 补充点 1: 完整 3-Tier (Novel > Volume > Chapter) 推送

### 13.1 现状 (勘察发现)

**DB 中 novels/volumes/chapters 真实状态** (2026-07-08 11:25 sqlite 实证):

```sql
-- novels: 2 个
('meta-realm', '元界', 'ongoing', '一个关于意识、边界与觉醒的科幻故事。')
('novel_d015ochhmrauz1ez', NULL, 'ongoing', NULL)  -- ⚠️ 残留空 novel (0 chapter)

-- novel_volumes: 2 个
('第一卷: 星海之始', 1, 'meta-realm')  -- 3 chapter
('第一卷', 1, 'novel_d015ochhmrauz1ez')  -- ⚠️ 0 chapter (残留)

-- chapters: 3 个 (slug 不一致!)
('chapter-1-awakening',  '觉醒',        1, 'meta-realm')
('chapter-2-realm',      '元界',        2, 'meta-realm')  -- ⚠️ slug 不一致
('meta-realm-ch003',     '第 3 章 · 替睡人', 3, 'meta-realm')  -- 后改的模式
```

**老板看到的页面** (`/novels` + `/novels/meta-realm`):
- `/novels`: 显示 1 本 (meta-realm), "1 卷 · 共 3 章"
- `/novels/meta-realm`: 显示 Novel (元界) → Volume 1 (第一卷: 星海之始) → 3 chapter 列表
- 页面层级: `小说 / 元界 / 第 1 卷 · 第一卷: 星海之始 / 觉醒`

**问题 1 — slug 不一致**:
- ch-001 用 `chapter-1-awakening` (英文 title slugs)
- ch-002 用 `chapter-2-realm` (英文 title slugs)
- ch-003 用 `meta-realm-ch003` (novel-prefixed 编号)
- 未来推送会继续混乱

**问题 2 — Volume 1 名称不统一**:
- 一个叫 "第一卷: 星海之始" (3 chapter)
- 另一个叫 "第一卷" (0 chapter, 残留)
- 推新本时不能复用现有 volume 名

**问题 3 — `/api/external/chapters` 已支持 3-tier, 但 publisher 只用了一半**:
- API 接受 `novel_slug / novel_title / volume_title / volume_order / chapter_*` 全套
- publisher 当前**硬编码** `novel_slug="meta-realm"` + `volume_title="第一卷 · 星海之始"` + `volume_order=1`
- 新小说需要改代码, 而不是改配置

### 13.2 方案: novels.yaml + chapter slug 规范 + state 升级

#### 13.2.1 novels.yaml 加 Volume + Slug 字段

```yaml
novels:
  - id: meta_realm_obsidian
    # ============ 3-tier 基础信息 (老板可改) ============
    title: 元界
    slug: meta-realm           # 老板可改, 但改后破坏老链接
    description: 一个关于意识、边界与觉醒的科幻故事。
    status: ongoing
    
    # ============ Volume 配置 (支持多卷) ============
    volumes:
      - order: 1
        title: 第一卷: 星海之始
        description: 觉醒前的最后平静。
        # 卷内章节范围 (由 state.json 自动追踪, 这里只声明初始)
        start_chapter: 1
        end_chapter: 50  # 满了自动通知老板开新卷
    
    # ============ chapter slug 规范 (新推章节) ============
    chapter_slug_template: "{novel_slug}-ch{idx:03d}"
    # 老 slug (chapter-1-awakening / chapter-2-realm) 保留, 不重命名
    
    # ============ 推送规则 ============
    enabled: true
    daily_chapter: true
    target_word_count: 3000
    category: 科幻
    keywords: [意识上传, 月球基地, AI 觉醒]
    
    # ============ backups 仓路径 ============
    paths:
      outline: novels/meta_realm_obsidian/outline.md
      style_guide: novels/meta_realm_obsidian/style_guide.md
      characters: novels/meta_realm_obsidian/characters.md
      chapters_dir: novels/meta_realm_obsidian/chapters/
    
    created_at: 2026-07-05T00:00:00Z
  
  - id: glass_sea_obsidian
    title: 玻璃海
    slug: glass-sea             # 新本, slug 不与老冲突
    description: 沿海小城, 退潮后海滩上出现的不该存在的东西。
    status: ongoing
    enabled: false              # 待大纲审核
    volumes:
      - order: 1
        title: 卷一: 退潮
        description: 故事开始, 退潮后第一夜。
        start_chapter: 1
        end_chapter: 30
    chapter_slug_template: "{novel_slug}-ch{idx:03d}"
    target_word_count: 3000
    keywords: [退潮, 海市蜃楼, 失忆]
    paths:
      outline: novels/glass_sea_obsidian/outline.md
      style_guide: novels/glass_sea_obsidian/style_guide.md
      characters: novels/glass_sea_obsidian/characters.md
      chapters_dir: novels/glass_sea_obsidian/chapters/
    created_at: 2026-07-08T00:00:00Z
```

#### 13.2.2 publisher 改造: 完整 3-tier 推送

**新代码** (`src/publisher.py` 重构 Step 7):

```python
# 7. 推 obsidian-journal (POST /api/external/chapters)
# v0.3.1: 完全利用 3-tier API, 不再硬编码 novel/volume
body = {
    # ============ Novel 层 (从 novels.yaml 读) ============
    "novel_slug": novel.slug,                       # "meta-realm" / "glass-sea"
    "novel_title": novel.title,                     # "元界" / "玻璃海"
    "novel_description": novel.description,
    "novel_status": novel.status,                   # ongoing / completed / hiatus
    
    # ============ Volume 层 (state 自动算当前卷) ============
    "volume_title": current_volume.title,           # "第一卷: 星海之始"
    "volume_order": current_volume.order,           # 1 / 2 / 3
    "volume_description": current_volume.description,
    
    # ============ Chapter 层 (state 自动算 idx) ============
    "chapter_slug": chapter_slug,                   # 规范: "{novel_slug}-ch{idx:03d}"
    "chapter_title": rendered.title,
    "chapter_content": rendered.content_markdown,
    "chapter_excerpt": rendered.excerpt,
    "chapter_published": True,
    
    # ============ 幂等 + 元数据 ============
    "external_id": f"{novel.id}-ch{idx:03d}",
    "idempotency_key": idem_key,
    "external_meta": {
        "novel_id": novel.id,
        "volume_id": current_volume.id,
        "outline_sha": state.outline_sha,
        "style_guide_sha": state.style_guide_sha,
        "word_count": draft.word_count,
    },
}
```

**卷切换逻辑** (`src/novel_registry.py`):

```python
def current_volume(novel: Novel, chapter_idx: int) -> Volume:
    """根据 chapter_idx 返回当前所属卷
    
    逻辑:
    - 遍历 volumes, 找 start_chapter <= chapter_idx <= end_chapter
    - 超过 end_chapter → 抛 VolumeFullError, 提示老板加新卷
    - chapter_idx > last volume 的 end_chapter → 抛 VolumeFullError
    """
    for vol in novel.volumes:
        if vol.start_chapter <= chapter_idx <= vol.end_chapter:
            return vol
    # 已超最后一卷
    last_vol = novel.volumes[-1]
    if chapter_idx > last_vol.end_chapter:
        raise VolumeFullError(
            f"小说 {novel.id} 第 {chapter_idx} 章超过最后一卷 ({last_vol.title}) 上限 "
            f"({last_vol.end_chapter} 章), 请在 novels.yaml 加新卷"
        )
    return last_vol
```

**老 slug 数据迁移策略**:
- **不重命名** — `/chapters/chapter-1-awakening` 等老链接已生成, 重命名会 404
- 新推章节走规范 slug `meta-realm-ch004` / `meta-realm-ch005` / ...
- 老 3 章保留 (ch-001/2/3 还在 DB), 与新 slug 并存
- state 推进不依赖 slug, 只用 chapter_idx 数字

**残留数据清理** (一次性 SQL):
```sql
-- 删除空 novel + 空 volume (老板拍 Q8 后执行)
DELETE FROM novel_volumes WHERE novel_id IN (SELECT id FROM novels WHERE description IS NULL);
DELETE FROM novels WHERE description IS NULL;
```

---

## 14. 补充点 2: 网站版权 + 文章内容版权

### 14.1 现状 (勘察)

**Footer 现状** (`components/Footer.tsx`):
```tsx
<p className="text-xs">
  © {year} {siteName} · Built by 黑 (Hei) · 主题: {defaultTheme}
</p>
```

- 只有 "© 2026 黑曜石日志" 整站版权
- **没有文章内容版权声明**
- **没有 /copyright / /legal 页面**
- 每章末尾/每文末尾也没有版权信息

**SiteConfig schema 现状**:
```sql
site_config: site_name, site_tagline, site_description, site_keywords, default_theme,
             allow_custom_html, baidu_push_enabled, baidu_push_token, og_image, favicon, analytics
```
- **没有 license 字段**
- **没有 copyright_holder 字段**

### 14.2 方案: 3 层版权声明

#### 14.2.1 Schema 加 4 字段 (DB migration)

```sql
-- ALTER TABLE site_config 加 4 字段
ALTER TABLE site_config ADD COLUMN site_license TEXT NOT NULL DEFAULT 'CC BY-NC-SA 4.0';
ALTER TABLE site_config ADD COLUMN site_license_url TEXT NOT NULL DEFAULT 'https://creativecommons.org/licenses/by-nc-sa/4.0/';
ALTER TABLE site_config ADD COLUMN copyright_holder TEXT NOT NULL DEFAULT '上坤';
ALTER TABLE site_config ADD COLUMN aigc_disclosure INTEGER NOT NULL DEFAULT 1;
-- 0 = 不标, 1 = 自动标 AIGC
```

#### 14.2.2 Layer 1 — Footer (整站版权)

**新 Footer** (`components/Footer.tsx`):
```tsx
const year = new Date().getFullYear();
const siteLicense = config?.site_license ?? "CC BY-NC-SA 4.0";
const siteLicenseUrl = config?.site_license_url ?? "https://creativecommons.org/licenses/by-nc-sa/4.0/";
const copyrightHolder = config?.copyright_holder ?? "上坤";

<p className="text-xs">
  © {year} {siteName} · Built by 黑 (Hei) · 主题: {defaultTheme}
</p>
<p className="text-xs mt-1">
  本站内容采用{" "}
  <a href={siteLicenseUrl} target="_blank" rel="noopener noreferrer" className="underline">
    {siteLicense}
  </a>{" "}
  授权 · © {year} {copyrightHolder} · 保留所有权利
  {" · "}
  <Link href="/copyright" className="underline">版权声明</Link>
</p>
```

#### 14.2.3 Layer 2 — 文章末尾 (单篇版权)

**新组件** `components/ArticleCopyright.tsx`:
```tsx
// 在 posts / chapters / novels 详情页末尾渲染
export function ArticleCopyright({ type = "post" }: { type?: "post" | "chapter" | "novel" }) {
  const config = siteConfigRepo.get();
  const year = new Date().getFullYear();
  const siteLicense = config?.site_license ?? "CC BY-NC-SA 4.0";
  const siteLicenseUrl = config?.site_license_url ?? "https://creativecommons.org/licenses/by-nc-sa/4.0/";
  const copyrightHolder = config?.copyright_holder ?? "上坤";
  const aigcEnabled = config?.aigc_disclosure ?? 1;
  
  return (
    <div className="mt-12 pt-6 border-t border-border text-xs text-fg-muted space-y-2">
      {aigcEnabled === 1 && (
        <p>
          ⚠️ <strong>AI 辅助生成声明</strong>: 本文/本章由 AI (minimax M3 + image-01) 辅助生成,
          人工审核后发布。内容不代表 100% 事实, 仅供参考。
        </p>
      )}
      <p>
        版权: © {year} {copyrightHolder} · 采用{" "}
        <a href={siteLicenseUrl} target="_blank" rel="noopener noreferrer" className="underline">
          {siteLicense}
        </a>{" "}
        授权 · 转载需注明出处
      </p>
      {type === "chapter" && (
        <p>
          永久链接: <Link href={`/chapters/${slug}`} className="underline">/chapters/{slug}</Link>
        </p>
      )}
    </div>
  );
}
```

**接入位置**:
- `app/chapters/[slug]/page.tsx` (v0.38 已存在) — 末尾插 `<ArticleCopyright type="chapter" />`
- `app/posts/[slug]/page.tsx` (v0.20 已存在) — 末尾插 `<ArticleCopyright type="post" />`
- `app/novels/[slug]/page.tsx` (v0.38 已存在) — 末尾插 `<ArticleCopyright type="novel" />` (仅 novel 层级)

#### 14.2.4 Layer 3 — 独立 /copyright 页面 (新)

**新页面** `app/copyright/page.tsx` (v0.39 新增):
- 整站版权声明
- 文章内容版权 (CC BY-NC-SA 4.0 全文)
- AI 生成内容声明 (AIGC disclosure)
- 免责声明
- 联系信息 (老板可填)
- **新导航入口**: Nav 末尾 "版权" 链接

**页面内容** (老板可编辑, 存 SiteConfig):
```markdown
# 版权声明

最后更新: 2026-07-08

## 1. 整站版权
本站 (黑曜石日志) 的源代码、UI 设计、原创插图、原创 logo 由 © 2026 上坤 创作,
采用 [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) 授权。

## 2. 文章/章节内容版权
本站发布的所有文章、小说章节、翻译内容同样采用 CC BY-NC-SA 4.0 授权:
- **允许**: 转载、引用、改编 (注明出处即可)
- **不允许**: 商业使用、署名删除
- **强制**: 转载需保留原作者 (上坤) + 永久链接 + 同样协议

## 3. AI 辅助生成声明
本站小说 (《元界》等) 由 AI (minimax M3 + image-01) 辅助生成, 经人工审核后发布。
- 故事设定 / 人物 / 大纲: 人工设计
- 章节正文: AI 写作 + 人工润色
- 封面图: AI 生成 + 人工挑选
- 内容不代表 100% 事实, 仅供参考

## 4. 免责声明
- 本站内容不构成任何投资 / 法律 / 医疗建议
- AI 生成内容可能存在事实错误, 老板会在评论区更正
- 引用第三方内容已尽量注明出处, 如有侵权请联系删除

## 5. 联系
- GitHub: https://github.com/blackclaw0318
- 邮箱: (待老板填)
```

**SiteConfig 升级 (admin UI)**:
- `app/admin/(admin)/settings/page.tsx` 加 "版权设置" 区块
  - 整站许可证下拉 (CC BY / CC BY-SA / CC BY-NC / CC BY-NC-SA / All Rights Reserved)
  - 版权持有人输入框
  - AIGC 披露开关
  - /copyright 页面 Markdown 编辑器 (富文本 → 渲染)

### 14.3 估时 (v0.3.1 增量)

| 模块 | 估时 | 备注 |
|---|---|---|
| obsidian-novel-publisher 3-tier 完整化 | 1d | P0.5: novels.yaml 升级 + state 加 volume + publisher 改造 |
| 残留数据清理 (空 novel/volume) | 0.1d | 一次性 SQL + 老 slug 保持 |
| obsidian-journal SiteConfig schema 扩展 | 0.2d | 4 字段 + ALTER TABLE + 类型 + repo 函数 |
| Footer 升级 | 0.2d | 整站许可证链接 + 版权声明链接 |
| ArticleCopyright 组件 | 0.3d | 3 处接入 (chapter / post / novel) |
| /copyright 新页 | 0.5d | 富文本 + admin 端编辑 + nav 接入 |
| admin settings UI 升级 | 0.5d | 许可证下拉 + 版权持有人 + AIGC 开关 + Markdown 编辑 |
| **增量估时** | **2.8d** | + 1d 缓冲 = 3.8d |

**v0.3 总估时更新**:
- 原 §6 估时 5d
- + v0.3.1 增量 3.8d
- **新总估时: 8.8d ≈ 9d** (老板拍 Q1-Q8 + v0.3.1 Q9 后启动)

---

## 15. 更新后的文件改动 (合并 §5 + v0.3.1)

### 15.1 新增 (10 个 → 13 个)

| 文件 | LOC | 作用 | 来源 |
|---|---|---|---|
| `novels.yaml` | 80 | 多本注册表 (含 volume + slug_template) | 原 + v0.3.1 |
| `src/novel_registry.py` | 200 | `load_novels()` + `current_volume()` + 校验 | 原 + v0.3.1 |
| `src/novel_outline.py` | 180 | 拉 + 缓存 + sha 检测 outline.md | 原 |
| `src/style_guide.py` | 150 | 拉 + 解析 + 应用 style_guide.md | 原 |
| `src/state_per_novel.py` | 120 | state 按 novel_id 拆分 (含 volume 追踪) | 原 + v0.3.1 |
| `src/cover_prompt_builder.py` | 100 | style_guide → chapter cover prompt 组合 | 原 |
| `tests/integration/multi_novel.test.py` | 200 | 多本并行流测试 | 原 |
| `tests/integration/text_punct_orphan_quotes.test.py` | 80 | 排版用例 | 原 |
| `tests/integration/three_tier_chapter.test.py` | 150 | **3-tier 完整推送测试** | **v0.3.1** |
| `app/copyright/page.tsx` | 200 | **/copyright 独立页面** | **v0.3.1** |
| `components/ArticleCopyright.tsx` | 80 | **单篇末尾版权组件** | **v0.3.1** |
| `tests/integration/copyright.test.ts` | 100 | **版权组件 + schema 测试** | **v0.3.1** |
| `tests/e2e/admin-copyright.spec.ts` | 80 | **admin 设置端到端** | **v0.3.1** |

### 15.2 修改 (7 → 11 个)

| 文件 | 改动 | 来源 |
|---|---|---|
| `src/publisher.py` | run_once 改 loop + 完整 3-tier body 构造 | 原 + v0.3.1 |
| `src/topic_gen.py` | `create_novel_draft()` 开新书 | 原 |
| `src/github_backup.py` | upload_outline / style_guide / characters / chapter | 原 |
| `src/cover_gen.py` | 注入 style_guide | 原 |
| `src/text_punct.py` | `_merge_orphan_quotes()` | 原 |
| `src/markdown_renderer.py` | 渲染前过 _merge_orphan_quotes | 原 |
| `assets/prompts/system.txt` | 排版铁律 + 人物一致性 | 原 |
| `assets/prompts/user.txt` | 注入 outline + characters + style_guide + prev_summary | 原 |
| `assets/prompts/topic_user.txt` | 关键词驱动 + 风格偏好 | 原 |
| **`lib/db.ts`** | **site_config 加 4 字段 (license/holder/...)** | **v0.3.1** |
| **`lib/repo.ts`** | **siteConfigRepo 加 4 字段 CRUD + ArticleCopyright 引用** | **v0.3.1** |
| **`components/Footer.tsx`** | **整站许可证 + 版权声明链接** | **v0.3.1** |
| **`app/chapters/[slug]/page.tsx`** | **末尾插 ArticleCopyright** | **v0.3.1** |
| **`app/posts/[slug]/page.tsx`** | **末尾插 ArticleCopyright** | **v0.3.1** |
| **`app/novels/[slug]/page.tsx`** | **末尾插 ArticleCopyright** | **v0.3.1** |
| **`components/Nav.tsx`** | **末尾加"版权"链接 → /copyright** | **v0.3.1** |
| **`app/admin/(admin)/settings/page.tsx`** | **版权设置区块 (许可证/持有人/AIGC/Markdown)** | **v0.3.1** |

---

## 16. 更新后的实施步骤 (合并 §6 + v0.3.1)

| 阶段 | 内容 | 估时 | 累计 |
|---|---|---|---|
| **P0** | novels.yaml + novel_registry + state 拆分 + backups README | 1d | 1d |
| **P0.5** | **3-tier 完整化 (v0.3.1 补充)** | 1d | 2d |
| **P1** | novel_outline + style_guide + characters 拉取/缓存 | 1d | 3d |
| **P2** | publisher.run_once 改 loop 多本 + 错误隔离 | 0.5d | 3.5d |
| **P3** | cover_prompt_builder + style_guide 驱动 + 人物一致性 | 0.5d | 4d |
| **P4** | text_punct 排版修复 + prompt 强化 + 测试 | 0.3d | 4.3d |
| **P5** | 集成测试 + 真 dry-run + 推 GitHub + 文档 | 1d | 5.3d |
| **P5.5** | **obsidian-journal 版权声明 (v0.3.1 补充)** | 2d | 7.3d |
| **P6** | (可选) 接入 systemd timer 多本错峰 | 0.7d | 8d |
| **P7** | **admin 版权设置 UI + /copyright 页面 (v0.3.1 收尾)** | 1d | 9d |

---

## 17. 更新后的老板决策清单 (Q1-Q8, 拍后启动 P0)

| # | 决策 | 候选 | 黑推荐 |
|---|---|---|---|
| **Q1** | novels.yaml 位置 | `repo root` / `config/novels.yaml` / `novels/{id}.yaml` | **repo root** |
| **Q2** | 一次 run_once 写几本? | 1 本 / N 本 / 单本轮转 | **1 本 (轮转)** |
| **Q3** | 老板在 GitHub 改 outline 后通知方式? | 不通知 + 日报 / WeCom / PR comment | **不通知 + 日报显示** |
| **Q4** | style_guide vs outline 矛盾时? | log warning / 抛错 / 静默用 outline | **log warning 继续** |
| **Q5** | 人物一致性机制? | prompt 描述 / image ref / 两者 | **先 prompt 描述** |
| **Q6** | 「」 排版 3 层防护? | 仅后处理 / 仅 prompt / 后处理+prompt+renderer | **3 层全上** |
| **Q7** | 老的 `truth/` 目录处理? | 保留 1 月归档 / 立即清 / 保留不归档 | **保留 1 月** (.archive) |
| **Q8** | **老 slug (chapter-1-awakening 等) + 残留空 novel/volume** | **保留 + 手动清 / 一次性 SQL 清 / 不动** | **一次性 SQL 清空 novel, 老 slug 保留** |
| **Q9** | **整站 license 选哪个?** | CC BY / CC BY-SA / CC BY-NC / CC BY-NC-SA / All Rights Reserved | **CC BY-NC-SA 4.0** (博客最常用, 商业留余地) |
| **Q10** | **AIGC 披露开关默认?** | 默认开 / 默认关 | **默认开** (合规友好, 老板可关) |
| **Q11** | **/copyright 页面内容维护方式?** | 硬编码 TSX / 存 SiteConfig Markdown / 用 pages 表 (Block 编辑器) | **存 SiteConfig Markdown** (admin 可编辑, 与 pages 隔离) |
| **Q12** | **Footer 版权声明显示方式?** | 简版 (1 行) / 完整 (含链接) | **完整版** (含许可证链接 + /copyright 链接) |

### 17.1 新增引用

- 老板 2 个补充点: 2026-07-08 11:24 webchat
- 页面勘察: https://www.shangkun.uk/novels + https://www.shangkun.uk/novels/meta-realm (7-8 11:25)
- DB 实证: `projects/obsidian-journal/data/dev.db` 查 novels/volumes/chapters (7-8 11:25)
- 残留空 novel: `novel_d015ochhmrauz1ez` (0 chapter, 需 Q8 决定清不清)
- slug 不一致: ch-001=`chapter-1-awakening`, ch-002=`chapter-2-realm`, ch-003=`meta-realm-ch003` (v0.3.1 统一新规范)

### 17.2 风险 (v0.3.1 新增)

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 老 slug 重命名破坏 SEO / 外链 | 🟡 中 | 老链接 404 | **不重命名**, 新推走规范 slug, 老 3 章保留 |
| 残留空 novel 还在 DB | 🟢 低 | UI 显示空 card | Q8 一次性 SQL 清 |
| CC BY-NC-SA 4.0 商业限制 | 🟡 中 | 老板未来想做付费内容受限 | Q9 拍板前想清; 后期可换 CC BY |
| AIGC 披露影响 SEO / 流量 | 🟡 中 | 平台对 AI 内容流量降权 | Q10 拍板; 可关 |
| /copyright 页面没人看 | 🟢 低 | 形式主义 | Footer 强制链接 + admin 提醒 |

---

## 18. 文档版本史

| 版本 | 日期 | 老板决策 | 状态 |
|---|---|---|---|
| v0.3 | 2026-07-08 10:50 | 老板提 4 大问题 | ⏸️ 等拍 Q1-Q7 |
| **v0.3.1** | 2026-07-08 11:25 | 老板补充 2 点 (3-tier 完整 + 版权) | ⏸️ 等拍 Q1-Q12 |

---

## 19. 引用 (更新)

- 老板 4 大问题: 2026-07-08 09:40 webchat
- 老板 2 补充点: 2026-07-08 11:24 webchat
- 现状勘察: `src/{publisher,topic_gen,state,text_punct}.py` + `assets/prompts/*.txt` (7-8 10:50)
- 页面勘察: https://www.shangkun.uk/novels + https://www.shangkun.uk/novels/meta-realm (7-8 11:25)
- DB 实证: `projects/obsidian-journal/data/dev.db` 查 novels/volumes/chapters (7-8 11:25)
- `/api/external/chapters` 契约: `projects/obsidian-journal/app/api/external/chapters/route.ts` (v0.38 P2)
- SiteConfig schema: `projects/obsidian-journal/lib/db.ts` + `lib/repo.ts` (7-8 11:26)
- 上一份方案: `docs/PLAN_NOVEL_AUTHOR_UNIFY.md` (v3 实证完成, 不冲突)
- 老 3 章数据: `data/state.json` (next_idx=4, last_pushed_idx=3)
