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
