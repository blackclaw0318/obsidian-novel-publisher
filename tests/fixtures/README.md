# 黄金样本 (Golden Samples) - v0.2 P6.4

> 用于测渲染/签名/拆分的**确定性输入**

## 文件清单

| 文件 | 用途 |
|---|---|
| `chapter_text.txt` | 真 LLM 输出的章节正文 (~3000 字), 用于 renderer / word_count / split 基准 |
| `topic_output.json` | 真实选题 LLM 输出, 用于 _parse_json_array / _repair_inner_quotes 基准 |
| `cover_prompt.txt` | 真实封面 prompt, 用于 image API prompt 稳定性测试 |
| `hmac_signature.txt` | 已知 body + secret → 已知 signature (test_hmac_client 用) |

## 生成方式

```bash
# 跑一次真 LLM 生成样本 (老板手动)
python -c "
from src.novel_writer import NovelWriter
import os
w = NovelWriter()
text = w.write_chapter(
    chapter_idx=1,
    truth_snapshot={'chapter_title': '第一道光', 'chapter_goal': '引入主角和世界观'},
    style_guide={'title': '第一道光', 'genre_hint': '科幻'},
).raw_text
with open('tests/fixtures/chapter_text.txt', 'w') as f:
    f.write(text)
print(f'已写入 {len(text)} 字符')
"
```

⚠️ **凭据安全**: 生成过程需要真 `MINIMAXI_API_KEY`, 但 fixture 文件本身**不含任何凭据**, 仅 LLM 输出文本, 可入 git。

## 为什么不放 commit history 大块文本

- **fixture 体积**: 一章 ~3000 字 ≈ 9KB UTF-8, 5 章 ≈ 45KB, 仓库会胖
- **可重现性**: prompt 模板入仓即可, LLM 输出每次都不同 (老板无需回归)
- **策略**: 真要回归, 老板跑 `python scripts/regenerate-fixtures.py` 重生成

## 当前状态

⏳ **空目录占位** — P6.4 fixtures 由 `scripts/dry-run.sh` 实跑后, 老板手动 copy 入此目录。