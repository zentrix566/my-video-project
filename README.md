# my-video-project · 通用「主题 → 剪映视频」流水线

给它一个主题短语，它就把 **人物事迹撰稿 → 场景切分 → 图片生成 → 配音合成 → 剪映草稿装配** 从头到尾跑完，产物直接落进本机剪映草稿目录，打开剪映即可编辑/导出。

参考自 `E:\github\test-agent-plan`（诗词/故事流水线），在其之上多加了一层「主题 → 旁白稿」的 LLM 步骤，把「必须先写好一整段 story」的门槛压到了最低。

---

## 一句话使用

```bash
python make_video.py --topic "南明李定国"
```

跑完在 `D:/Program Files/JianyingPro Drafts/南明李定国_<时间戳>/` 里出现一个新草稿，包含图片轨、配音轨、字幕轨、运镜与叠化转场。

---

## 目录结构

```
my-video-project/
├── make_video.py                 # 主入口 CLI（主题 → 视频）
├── make_meme_video.py            # 附入口 CLI（本地图 → 梗图剪映草稿）
├── curate_photos.py              # 一键分类：可用 / 不可用 / 动图 + 重命名
├── curate_manual.py              # 人肉挑图 GUI（tkinter，键盘操作）
├── pipeline/
│   ├── topic_to_story.py         # Step 0  主题 → 旁白稿           (LLM)
│   ├── scene_split.py            # Step 1  旁白稿 → 场景数组       (LLM)
│   ├── image_gen.py              # Step 2  豆包 Seedream 图片生成
│   ├── tts.py                    # Step 3  字节 Seed-TTS 配音
│   ├── draft_composer.py         # Step 4  pyJianYingDraft 装配草稿
│   ├── meme_composer.py          # 梗图专用装配（无音频轨、模糊背景）
│   ├── photo_filter.py           # 图片筛选（尺寸/宽高比阈值）
│   ├── photo_ledger.py           # 已用清单账本（跨次运行不重复挑）
│   ├── blur_bg.py                # 模糊背景生成器（去黑边，PIL 缓存）
│   ├── camera.py                 # 8 种运镜预设（zoom/pan/diagonal）
│   ├── subtitles.py              # 5 种字幕预设（cinema/classic/vlog/comic/default）
│   ├── styles.py                 # 从 configs/styles/*.json 读取风格
│   └── helpers.py                # env/retry/parallel/logger/费用估算
├── configs/styles/
│   ├── epic.json                 # 默认 · 史诗历史片 1920x1080
│   ├── documentary.json          # 沉稳纪录片 1920x1080
│   └── shorts.json               # 竖屏短视频 1080x1920
├── outputs/
│   ├── logs/*.jsonl              # 结构化流水线日志
│   ├── projects/<slug>/<ts>/     # 每次运行的中间产物（图片/音频/JSON）
│   └── blur_cache/               # 模糊背景 PIL 缓存（自动重建）
└── examples/
    ├── run_li_dingguo.sh         # 主流水线样例
    └── salvage_last_step0.py     # LLM JSON 崩坏时的救援脚本
```

剪映草稿输出到 `D:/Program Files/JianyingPro Drafts/`（可通过 `--draft-folder` 覆盖）。

---

## 安装

前置：Windows + Python 3.10+ + 已安装剪映专业版 (JianyingPro)。

```bash
cd E:\github\my-video-project
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

配置密钥（火山方舟 Ark Key，可与 `test-agent-plan` 共用同一把）：

```bash
copy .env.example .env
# 编辑 .env，把 AGENT_API_KEY=... 填上真实值
# 或者直接从参考项目复制：
copy ..\test-agent-plan\.env .env
```

只需要 `AGENT_API_KEY` 一个必填值；其它字段（模型名、URL、音色…）都有合理默认值，或可在风格预设 JSON 里覆盖。

---

## 常用命令

```bash
# 1) 最简：只给一个主题
.\.venv\Scripts\python make_video.py --topic "南明李定国"

# 2) 加要点、指定场景数
.\.venv\Scripts\python make_video.py --topic "南明李定国" \
    --brief "两蹶名王、磨盘山之战、忠贞殉国" --scenes 8

# 3) 换风格
.\.venv\Scripts\python make_video.py --topic "苏轼被贬黄州" --style documentary --scenes 10
.\.venv\Scripts\python make_video.py --topic "岳飞抗金"     --style shorts

# 4) 先估算费用（不消耗任何配额）
.\.venv\Scripts\python make_video.py --topic "南明李定国" --dry-run

# 5) 断点续跑（例如上次剪映合成失败，只重跑合成步骤）
.\.venv\Scripts\python make_video.py --topic "南明李定国" \
    --resume-latest --skip-llm --skip-image --skip-tts

# 6) 提高并发（默认 2 线程生图）
.\.venv\Scripts\python make_video.py --topic "南明李定国" --workers 3

# 7) 查看所有风格
.\.venv\Scripts\python make_video.py --list-styles

# 8) 只生成音频（拿到 mp3 配音，不出图、不做剪映草稿）—— 常用于先听效果
.\.venv\Scripts\python make_video.py --topic "南明李定国" --audio-only

# 9) 只生成图片（不做 TTS、不做剪映草稿）—— 常用于挑图
.\.venv\Scripts\python make_video.py --topic "南明李定国" --images-only

# 10) 换 TTS 音色 / 资源包（覆盖风格预设里的默认值）
.\.venv\Scripts\python make_video.py --topic "南明李定国" \
    --speaker zh_male_yangguang_emo_v2_mars_bigtts

# 11) 关掉交互卡点，一路跑到底（脚本化时用）
.\.venv\Scripts\python make_video.py --topic "南明李定国" -y
```

## 交互式确认卡点（默认开启）

图片生成是流水线里最贵的一步，所以默认在两处停下来让你过一眼文案：

**卡点 1（Step 0 之后）**：LLM 写完旁白稿后，把 project_name / title / 全文打印出来。

**卡点 2（Step 1 之后，图片生成之前）**：把每个场景的旁白 + 画面 prompt 打印出来。

两处都给你三个选项：

| 输入 | 动作 |
|---|---|
| `y` / 直接回车 | 确认，继续下一步 |
| `n` | 中止；已生成的文件全部保留在 `outputs/projects/<slug>/<ts>/` 下 |
| `e` | 暂停 → 你去编辑器里改 `generated_story.json` 或 `generated_scenes.json` → 保存 → 按回车 → 脚本重载文件并继续 |

**跳过卡点**：加 `--yes` 或 `-y`。

**从卡点中止后想继续**：改完文件后，加 `--resume-latest --skip-llm` 从图片开始续跑（旁白与场景直接读磁盘上的 JSON，不再花 LLM 钱）。

---

## 风格预设

| 预设 | 画布 | 音色 | 字幕 | 运镜 | 视觉风格 |
|---|---|---|---|---|---|
| **epic**（默认） | 1920×1080 | `zh_male_shaonianzixin_moon_bigtts` | cinema（白字半透黑底） | mixed（缩放+平移+对角） | 国风写实电影感、光影考究 |
| documentary | 1920×1080 | 同上 | classic（米黄色字·古风） | pan（缓慢平移） | 水墨工笔历史插画、暗色调 |
| shorts | 1080×1920 | 同上 | vlog（大白字+阴影） | all（快节奏） | 竖屏鲜艳、强对比 |

修改风格：直接编辑 `configs/styles/<name>.json`。新增风格：新建一个 JSON 文件即可自动被 `--style` 识别。

---

## 五步流水线

```
主题 (--topic)
    │  Step 0  pipeline.topic_to_story   → generated_story.json
    ▼
旁白稿 (story 800-1500 字, project_name, title, author)
    │  Step 1  pipeline.scene_split      → generated_scenes.json
    ▼
场景数组 [{id, narration, image_prompt}, …]
    │  Step 2  pipeline.image_gen        → outputs/projects/<slug>/<ts>/images/*.png
    │  Step 3  pipeline.tts              → outputs/projects/<slug>/<ts>/audio/*.mp3
    ▼
Step 4  pipeline.draft_composer    → D:/Program Files/JianyingPro Drafts/<slug>_<ts>/
                                     直接打开剪映即可看到时间线
```

- **Step 0** 是相对参考项目的新增能力：用户不用自己写整段故事，LLM 会写。
- 所有中间产物按 `outputs/projects/<slug>/<YYYYMMDD_HHMMSS>/` 目录组织，便于事后重跑。
- 结构化日志：`outputs/logs/pipeline_<ts>.jsonl`，每步 API 调用一行 JSON。

---

## 常见问题

**Q：剪映打开后看到「相对路径找不到」？**
A：`pyJianYingDraft` 写入的是绝对路径，重启剪映即可刷新草稿列表。

**Q：LLM 生成的旁白偏离主题？**
A：加上 `--brief` 强调重点，例如 `--brief "重点：两蹶名王与磨盘山之战"`。

**Q：图片生成超时/限流？**
A：降低 `--workers`（比如 1），提高 `--request-delay`（比如 3.5）；网络恢复后加 `--resume-latest --skip-llm` 续跑即可。

**Q：我的剪映装在别的路径？**
A：`--draft-folder "D:/我的剪映草稿目录"`。

**Q：TTS 报错 `resource ID is mismatched with speaker related resource` (code 55000000)？**
A：账户里的 `seed-tts-2.0` 资源包没开通那个音色。当前所有风格预设默认音色是已知可用的 `zh_female_vv_uranus_bigtts`。想换别的音色，用 `--speaker <id>` 覆盖；或直接改 `configs/styles/<name>.json` 里的 `tts.speaker`。同一账户开通的其它音色可以去火山方舟控制台确认。

**Q：只想要音频/图片，不要视频草稿？**
A：`--audio-only` 或 `--images-only`。中间产物在 `outputs/projects/<slug>/<时间戳>/{audio,images}/` 下。

**Q：Step 0 报错 `Expecting ',' delimiter` / `Expecting value` 之类的 JSON 解析错？**
A：LLM 偶尔会在字符串里塞未转义的英文引号，破坏了 JSON。已经做了两层加固：
1. system prompt 明确要求字符串内引号用中文「」或 ""。
2. 客户端有兜底修复器（`pipeline/helpers.py::_repair_bare_quotes_in_string_values`），自动把裸英文引号替换成中文右双引号，覆盖 `topic_to_story` 平铺 JSON 与 `scene_split` 嵌套数组两种结构。

（注：Ark 的 `ark-code-latest` 目前不支持 `response_format=json_object`，所以主要靠 prompt 约束 + 客户端修复。）

若真的又碰到（Step 0 已花钱、却因为解析报错崩了），运行救援脚本从磁盘上救回来，不用重跑：
```bash
.\.venv\Scripts\python examples\salvage_last_step0.py "outputs\projects\<主题>\<时间戳>"
```
它会用同一个容错链解析原始响应、写出 `generated_story.json`，然后你 `--resume-latest --skip-llm` 从 Step 1 接着跑。

**Q：想跑一段自己已经写好的故事？**
A：跳过 Step 0：先把自己的 `generated_story.json` 放到某个 `output_dir` 下，再用 `--output-dir <dir> --skip-llm` 从 Step 1 开始跑。或者直接改用参考项目 `test-agent-plan/run_story_pipeline.py`（要求 config 里已有 story 字段）。

---

## 授权与致谢

- 底层剪映草稿构建：[pyJianYingDraft](https://pypi.org/project/pyJianYingDraft/)
- 文本/图片：火山引擎 Ark（豆包 Seedream 5.0-lite + ark-code-latest 文本大模型）
- 语音：字节 OpenSpeech Seed-TTS 2.0
- 灵感与直接复用的工程模块：`E:\github\test-agent-plan`

---

## 附：本地图片 → 梗图剪映草稿

主流水线要花钱调 API 生成图和配音；如果你手上已经有一堆图片（微信保存的、截图、随手拍…），想快速拼一段梗图视频，用下面这两个附带工具，不产生任何 API 费用。

### 一、先给图源分类重命名（一次性）：`curate_photos.py`

```bash
# 干跑，先看看会怎么分
.\.venv\Scripts\python curate_photos.py --source "C:/Users/EDY/Pictures/2026-06" --dry-run

# 正式执行：在源目录下就地建 可用/不可用/动图 三个子目录，可用按 mtime 排序后重命名 001..N
.\.venv\Scripts\python curate_photos.py --source "C:/Users/EDY/Pictures/2026-06" -y
```

产出示例：
```
C:/Users/EDY/Pictures/2026-06/
├── 可用/     001.jpg 002.jpg ... 221.jpg  ← 按 mtime 排序后重命名
├── 不可用/   ...（原名保留，太窄/太小的都在这里）
└── 动图/     *.gif *.mp4（原名保留）
```

不想重命名就加 `--no-rename`；自定义子目录名用 `--usable-dir usable` 之类；筛图阈值可调 `--min-dim 400 --min-ratio 0.5 --max-ratio 2.5`。

### 二、（可选）人肉挑图 GUI：`curate_manual.py`

自动分类过滤不了"内容层面不合适"（比如某张图内容不有趣、不适合作梗图）。用这个原生窗口一张张过图，按键即可移到"不太合适"目录：

```bash
# 打开一个原生窗口，浏览 可用/ 里剩下的图
.\.venv\Scripts\python curate_manual.py --source "C:/Users/EDY/Pictures/2026-06/可用"

# 自定义"不合适"目录名
.\.venv\Scripts\python curate_manual.py --source "..." --reject-dir "先放一放"
```

快捷键：`→ / D / 空格` 下一张 · `← / A` 上一张 · `X / Delete` 标为不合适 → 立刻移走 → 跳到下一张 · `Z / Ctrl+Z` 撤销 · `F` 全屏 · `Q / Esc` 退出。移动即时生效、支持撤销栈、同名冲突自动改名。

### 三、按序号取图做梗图剪映草稿：`make_meme_video.py`

```bash
# 取 001–020 张，contain 模式（图片完整显示，宽高比不匹配时留边）
.\.venv\Scripts\python make_meme_video.py --source "C:/Users/EDY/Pictures/2026-06/可用" --range 1-20

# 多段拼接
.\.venv\Scripts\python make_meme_video.py --source "..." --range 1-10,15-25

# 加 BGM，视频时长跟着 BGM 走（图片时长自动 = BGM 时长 / 张数）
.\.venv\Scripts\python make_meme_video.py --source "..." --range 1-20 \
    --bgm "D:/Music/sore-sore.mp3" --fit-to-bgm

# 图片张数自动算：让 BGM 长度决定张数（每张 ~7 秒目标）
.\.venv\Scripts\python make_meme_video.py --source "..." \
    --seconds 7 --auto-count --bgm "D:/Music/sore-sore.mp3"

# 铺满画布（可能裁剪超出部分；contain 是默认）
.\.venv\Scripts\python make_meme_video.py --source "..." --range 1-20 --fit-mode cover

# 关掉模糊背景，contain 模式下留纯黑边
.\.venv\Scripts\python make_meme_video.py --source "..." --range 1-20 --no-bg-blur

# 开运镜（默认关，梗图一般不需要）
.\.venv\Scripts\python make_meme_video.py --source "..." --range 1-20 --movement

# 启用「已用清单」：本次跳过账本里已用过的图，结束后把选中的写进账本
.\.venv\Scripts\python make_meme_video.py --source "..." \
    --seconds 7 --auto-count --bgm "..." --use-ledger

# 只做筛图预览、不写草稿
.\.venv\Scripts\python make_meme_video.py --source "..." --scan-only
```

### 关键行为

| 项 | 值 | 说明 |
|---|---|---|
| **fit-mode** | `contain`（默认） | 图片完整显示，方屏画布下窄图/宽图会留 letterbox 位置 |
| fit-mode | `cover` | 图片铺满画布，超出部分裁剪 |
| **bg-blur** | 开（默认） | contain 模式下用同图模糊版填 letterbox，去黑边；关掉用 `--no-bg-blur` |
| **sort** | `name`（默认） | 配合 001,002... 命名，取图顺序 = 序号顺序 |
| sort | `newest` / `oldest` / `random` | 其它取图策略 |
| **range** | `1-20` / `1-10,15-25` / `5` | 精确指定要哪几张，避免 random 抽重 |
| **默认无运镜** | 静态切换 + 叠化 | 更贴 B 站梗图节奏；开 `--movement` 才叠加运镜关键帧 |
| **BGM** | `--bgm <path>` | 直接挂到音频轨；短则循环、长则截断；`--fit-to-bgm` 让视频匹配 BGM 长度 |
| **auto-count** | `--auto-count` | 以 `--seconds` 为每张目标秒数，自动算需要几张让视频 = BGM 时长（需配 `--bgm`） |
| **ledger** | `--use-ledger` | 启用「已用清单」（`<source>/.used_photos.json`），只从未用图挑；`--show-ledger` 查看，`--reset-ledger` 清空 |

### 完整推荐流程

```bash
# 1) 自动分类 + 重命名（一次性，把长截图/太小图挑走）
.\.venv\Scripts\python curate_photos.py --source "C:/Users/EDY/Pictures/2026-06" -y

# 2)（可选）人肉复筛 —— 键盘打字般过图，剔除内容不合适的
.\.venv\Scripts\python curate_manual.py --source "C:/Users/EDY/Pictures/2026-06/可用"

# 3) 用账本做视频，多次运行不会抽到重复图
.\.venv\Scripts\python make_meme_video.py `
    --source "C:/Users/EDY/Pictures/2026-06/可用" `
    --seconds 7 --auto-count `
    --bgm "D:/Music/sore-sore.mp3" `
    --use-ledger

# 想看进度：账本里还剩多少张没用
.\.venv\Scripts\python make_meme_video.py --source "..." --show-ledger

# 想全部重来
.\.venv\Scripts\python make_meme_video.py --source "..." --reset-ledger --show-ledger
```

---

## 作者

zentrix566

## 许可证

[MIT](./LICENSE)

打开剪映专业版，每次都会出现一份新草稿（名字里带时间戳，不覆盖）。
