# my-video-project · AI 短视频剪映草稿生成工具集

一个人也能又快又好地做短视频。给一个题材，它把 **文案/画面/配音/字幕/装配** 全跑完，产物直接落进本机剪映草稿目录，打开剪映编辑或导出即可。

**支持三条流水线（三选一，各自独立入口）**：

| 场景 | 输入 | 命令 | 单次成本 |
|---|---|---|---|
| 🎬 **主题 → 历史/人物介绍片** | 一个主题短语 | `make_video.py --topic "xxx"` | ~1-2 元 |
| 🖼 **本地图片 → 梗图/图集片** | 一个图片文件夹 | `make_meme_video.py --source "xxx"` | 0 元 |
| 💻 **前端项目 → 代码走读片** | 一个 Vue/React 项目路径 | `make_code_walk.py --project "xxx"` | ~0.2 元 |

三种模式共用同一套 **Seed-TTS 配音 + pyJianYingDraft 剪映装配** 底座，产出的草稿在剪映专业版里打开就能进时间线编辑。

---

## 三种模式各自的定位

### 🎬 主题 → 历史/人物介绍片 (`make_video.py`)

给一个主题短语（比如「南明李定国」），LLM 自动完成"人物事迹撰稿 → 场景切分 → 豆包 Seedream 图片生成 → Seed-TTS 配音 → 剪映草稿"全流程。适合 B 站历史人物介绍、文学典故科普、传统文化解读一类内容。参考自 `E:\github\test-agent-plan`（诗词/故事流水线），加了「主题 → 旁白稿」的 LLM 步骤，把"必须先写好一整段 story"的门槛压到最低。

### 🖼 本地图片 → 梗图/图集片 (`make_meme_video.py`)

已经有一堆图片（微信保存的、截图、随手拍…），想快速拼成一段视频。零 API 费用，可挂 BGM，可以按序号取图，可以启用「已用清单」跨次运行不抽重。适合抖音/小红书的梗图合集、生活图记、纪念相册。附带 `curate_photos.py` 和 `curate_manual.py` 两个筛图工具。

### 💻 前端项目 → 代码走读片 (`make_code_walk.py`)

**技术向 up 主专用**。给一个本地 Vue/React 项目路径（如 `E:/github/crazy-people`），流水线会：

1. **扫**：读 `package.json` + README + 关键源码文件，识别框架和路由
2. **写**：LLM 生成 8 段讲稿，包含 6 段动态 UI 视频（真正启动 dev server 用 Playwright 录制）+ 2-3 段代码高亮图（Pygments 渲染 + Mac 窗口装饰）
3. **拍**：起 `npm run dev` → Playwright 抓 UI 动图，代码段渲染成 1920×1080 高清 PNG
4. **配**：Seed-TTS 配音 + 剪映混排图/视频

**已知能扫描的框架**：Vue2 / Vue3 / React / Preact / Svelte / SolidJS / Angular / Next.js / Nuxt。

一部 5-6 分钟的代码走读视频 ≈ **0.2 元 + 3 分钟**，比传统录屏解说效率高 10 倍以上。首帧就是运行中的项目动画（Playwright 白屏帧已自动 ffmpeg 剪除），抓眼球效果拉满。

详细命令与参数见文末「代码走读模式」章节。

---

## 一句话使用（默认走主题模式）

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
├── make_code_walk.py             # 附入口 CLI（前端项目 → 代码走读视频）
├── curate_photos.py              # 一键分类：可用 / 不可用 / 动图 + 重命名
├── curate_manual.py              # 人肉挑图 GUI（tkinter，键盘操作）
├── pipeline/
│   ├── topic_to_story.py         # Step 0    主题 → 旁白稿           (LLM)
│   ├── title_variants.py         # Step 0.5  旁白稿 → B站标题候选     (LLM)
│   ├── scene_split.py            # Step 1    旁白稿 → 场景数组       (LLM)
│   ├── image_gen.py              # Step 2  豆包 Seedream 图片生成
│   ├── tts.py                    # Step 3  字节 Seed-TTS 配音
│   ├── draft_composer.py         # Step 4  pyJianYingDraft 装配草稿
│   ├── project_scan.py           # 代码走读 Step 0：扫描 Vue/React 前端项目元信息
│   ├── walk_narrator.py          # 代码走读 Step 1：LLM 生成讲解稿 + 分镜规范
│   ├── shot_renderer.py          # 代码走读 Step 2：Playwright 抓 UI + Pygments 渲染代码
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
│   ├── shorts.json               # 竖屏短视频 1080x1920
│   └── codewalk.json             # 代码走读 1920x1080（Fira Code + one-dark）
├── templates/
│   └── code_shot.html            # 代码截图 HTML 模板（Mac 窗口装饰 + 渐变背景）
├── outputs/
│   ├── logs/*.jsonl              # 结构化流水线日志
│   ├── projects/<slug>/<ts>/     # 主流水线中间产物
│   ├── code_walks/<slug>/<ts>/   # 代码走读流水线中间产物
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

**仅代码走读模式额外需要**：安装 Playwright 的 Chromium（一次性，~110MB）：

```bash
# 走火山镜像，国内下载快
$env:PLAYWRIGHT_DOWNLOAD_HOST="https://npmmirror.com/mirrors/playwright"
.\.venv\Scripts\python -m playwright install chromium
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

# 12) 跳过 B 站标题候选（少花 ~0.04 元；默认是开启的）
.\.venv\Scripts\python make_video.py --topic "南明李定国" --skip-titles
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
| **epic**（默认） | 1920×1080 | `zh_female_vv_uranus_bigtts` | cinema（白字半透黑底） | mixed（缩放+平移+对角） | 国风写实电影感、光影考究 |
| documentary | 1920×1080 | 同上 | classic（米黄色字·古风） | pan（缓慢平移） | 水墨工笔历史插画、暗色调 |
| shorts | 1080×1920 | 同上 | vlog（大白字+阴影） | all（快节奏） | 竖屏鲜艳、强对比 |
| codewalk | 1920×1080 | 同上 | classic（代码走读专用） | 无 | Fira Code + one-dark，仅代码走读入口使用 |

> 所有风格预设的默认音色都是账户里已知可用的 `zh_female_vv_uranus_bigtts`；想换其它音色可用 `--speaker <id>` 或改对应 JSON 里的 `tts.speaker`。

修改风格：直接编辑 `configs/styles/<name>.json`。新增风格：新建一个 JSON 文件即可自动被 `--style` 识别。

---

## 五步流水线

```
主题 (--topic)
    │  Step 0    pipeline.topic_to_story   → generated_story.json
    ▼
旁白稿 (story 800-1500 字, project_name, title, author)
    │  Step 0.5  pipeline.title_variants   → titles.json  （B站标题候选 10 条）
    │  Step 1    pipeline.scene_split      → generated_scenes.json
    ▼
场景数组 [{id, narration, image_prompt}, …]
    │  Step 2    pipeline.image_gen        → outputs/projects/<slug>/<ts>/images/*.png
    │  Step 3    pipeline.tts              → outputs/projects/<slug>/<ts>/audio/*.mp3
    ▼
Step 4  pipeline.draft_composer    → D:/Program Files/JianyingPro Drafts/<slug>_<ts>/
                                     直接打开剪映即可看到时间线
```

- **Step 0** 是相对参考项目的新增能力：用户不用自己写整段故事，LLM 会写。
- **Step 0.5** 给"介绍类"视频自动产出 10 条 B 站标题候选（结果落盘为 `titles.json`），详见下方 [B 站标题候选](#b-站标题候选step-05) 章节；不想要就加 `--skip-titles`。
- 所有中间产物按 `outputs/projects/<slug>/<YYYYMMDD_HHMMSS>/` 目录组织，便于事后重跑。
- 结构化日志：`outputs/logs/pipeline_<ts>.jsonl`，每步 API 调用一行 JSON。

---

## B 站标题候选（Step 0.5）

介绍类视频最费脑的往往是起标题。Step 0.5 在旁白稿写完后自动跑一次轻量 LLM 调用（约 15 秒 / ~0.04 元），基于同一份稿子产出 **10 条不同定位的候选标题**，覆盖 5 大类（流量向 / 正经历史 / 悬念钩子 / 引经据典 / 系列人设），每类 2 条：

| 类别 | 定位 | 举例 |
|---|---|---|
| **traffic 流量向** | 数字/极端词/名场面钩子 | 「44 岁孤守扬州，五封绝笔殉国」 |
| **historical 正经历史** | 信息量高、克制不夸张 | 「史可法督师江北：南明半壁的最后支点」 |
| **hook 悬念钩子** | 反问/悬念/未完成句 | 「城破那夜，他为什么端坐堂上等死？」 |
| **literary 引经据典** | 诗句/文言/文学感 | 「一寸丹心付梅岭·史可法孤忠记」 |
| **series 系列人设** | 账号 IP 化 | 「【南明孤忠 01】史可法·扬州不肯降的书生」 |

每条附一句 15-30 字的推荐理由，最后 ★ 标出 LLM 认为最优的一条；流水线开始与结束时各打印一次方便复制。

- **产物**：`outputs/projects/<slug>/<ts>/titles.json`，结构 `{primary_title, variants:[{category,title,reason},...], recommended_index, recommend_reason}`——可直接读进任何投稿脚本填 B 站/抖音/YouTube 投稿页 title 框。
- **开关**：`--skip-titles` 关掉（省 ~0.04 元 / ~15 秒）。
- **复用**：`--resume-latest --skip-llm` 会直接读盘上的 `titles.json`，不会重跑；想强制重生成就删掉 `titles.json` 再跑同样命令。

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

## 梗图模式 —— 本地图片 → 剪映草稿

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

# 傻瓜模式：什么都不指定，自动挑最新 可用/ + 剪映最新 BGM，开 auto-count + ledger
.\.venv\Scripts\python make_meme_video.py --auto

# 傻瓜模式 + 只看它会挑什么，不真的写草稿
.\.venv\Scripts\python make_meme_video.py --auto --scan-only

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
| **auto 傻瓜模式** | `--auto` | 自动挑 `~/Pictures/<年月>/可用/` + 剪映最新 mp3 BGM + 默认 `--auto-count --use-ledger`。一条命令零参数出片 |

### 完整推荐流程

**懒人一键版**（前提是已经跑过一次 `curate_photos.py` 并在剪映里下过一首 BGM）：

```bash
.\.venv\Scripts\python make_meme_video.py --auto
```

**分步版**：

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

## 代码走读模式 —— 前端项目 → 剪映草稿

主流水线适合讲历史/人物/文学；如果你想做的是「讲一个开源前端项目」（GitHub 上的 Vue/React 小玩具、Demo、自己写的 side project…），画面素材应该是**项目跑起来的真实截图**加**关键代码段的语法高亮图**，而不是 AI 画的想象图。这个入口就是干这个的：读取本地 Vue/React 项目 → 起 dev server → Playwright 抓 UI 截图 + Pygments 渲染代码高亮图 → 混排出一部 5-6 分钟的技术走读视频。

跟主流水线的关键区别：**Step 0-2 完全换掉**（不再花图片钱），Step 3-4 复用（TTS + 剪映合成不变）。

### 一键出片

以讲解 `E:\github\crazy-people` 为例：

```bash
.\.venv\Scripts\python make_code_walk.py `
    --project "E:/github/crazy-people" `
    --brief "一个 Vue3 前端 Demo，讲讲它怎么用几百行代码模拟疯人院" `
    --draft-folder "D:/software/JianyingPro Drafts"
```

跑完在你的剪映草稿目录出现 `<项目名>_<时间戳>/`，打开剪映即可编辑。费用约 **0.20 元**（比图片流水线便宜一个数量级；只有 Step 1 LLM 和 Step 3 TTS 花钱）。

### 常用命令

```bash
# 1) 只估费，不真跑
.\.venv\Scripts\python make_code_walk.py --project "E:/github/crazy-people" --dry-run

# 2) 加要点、指定场景数、跳过所有交互卡点
.\.venv\Scripts\python make_code_walk.py `
    --project "E:/github/crazy-people" `
    --brief "重点讲 world.js 的 tick 循环和 constants.js 的配置驱动" `
    --scenes 8 -y

# 3) dev server 已在跑（比如你手动 npm run dev 了），别再起一次
.\.venv\Scripts\python make_code_walk.py `
    --project "E:/github/crazy-people" --skip-dev-server

# 4) dev server 端口不是默认 5173
.\.venv\Scripts\python make_code_walk.py `
    --project "E:/github/some-react-app" --dev-port 3000

# 5) 断点续跑：LLM 已跑完、图也截好了，只重跑剪映合成
.\.venv\Scripts\python make_code_walk.py `
    --project "E:/github/crazy-people" `
    --resume-latest --skip-scan --skip-llm --skip-shots --skip-tts

# 6) 讲稿不满意，编辑 generated_scenes.json 后从 Step 2 起续跑
.\.venv\Scripts\python make_code_walk.py `
    --project "E:/github/crazy-people" --resume-latest --skip-llm

# 7) 只出稿件不出图（想先看讲得对不对）
.\.venv\Scripts\python make_code_walk.py `
    --project "E:/github/crazy-people" `
    --skip-shots --skip-tts --skip-jianying

# 8) 换 TTS 音色 / 覆盖剪映目录
.\.venv\Scripts\python make_code_walk.py `
    --project "E:/github/crazy-people" `
    --speaker zh_male_yangguang_emo_v2_mars_bigtts `
    --draft-folder "D:/software/JianyingPro Drafts"
```

### 五步流水线（代码走读版）

```
--project <PATH>
    │  Step 0  pipeline.project_scan   → project_meta.json
    ▼                                    （扫 package.json/README/vite.config，挑 12 个关键文件）
项目元信息（含 README 全文 + 关键文件前 60 行摘录）
    │  Step 1  pipeline.walk_narrator  → generated_scenes.json      (LLM)
    ▼                                    （8 段讲稿 + 每段截图规范）
scenes[]（含 shot_spec: {type: ui/code/cover, url_path/wait_ms/interactions | file/focus_lines/language}）
    │  Step 2  pipeline.shot_renderer  → outputs/code_walks/<slug>/<ts>/images/*.png
    │                                    （UI 截图 = Playwright + dev server；代码图 = Pygments + Playwright）
    │  Step 3  pipeline.tts            → outputs/code_walks/<slug>/<ts>/audio/*.mp3
    ▼                                    （字节 Seed-TTS，复用主流水线）
Step 4  pipeline.draft_composer   → <draft_folder>/<项目名>_<时间戳>/
                                    （pyJianYingDraft，复用主流水线）
```

### LLM 输出的分镜规范

`generated_scenes.json` 里每段 scene 除了 `id`/`narration`，还有一个 `shot_spec`，控制那一段画面怎么截。你可以在卡点 2 手动改 JSON 后续跑：

**UI 截图**（`type: "ui"`）—— 从 dev server 抓：
```json
{
  "type": "ui",
  "url_path": "/",              // 有 vue-router 就填对应路径，否则恒为 "/"
  "wait_ms": 3000,              // 加载后再等几毫秒（让动态内容有时间展开）
  "interactions": [
    {"action": "click_text", "text": "混乱 +", "times": 3, "interval_ms": 300},
    {"action": "wait", "ms": 500},
    {"action": "click_selector", "selector": ".btn-primary"}
  ]
}
```

**代码高亮图**（`type: "code"`）—— 用 Pygments 渲染带 Mac 窗口装饰的截图：
```json
{
  "type": "code",
  "file": "src/game/world.js",  // 相对项目根，必须在 project_meta.json 的 key_files 里
  "focus_lines": [1, 42],       // 1-based 闭区间，最多 42 行（超了字号会太小）
  "language": "javascript",     // prism 标识：javascript/typescript/jsx/tsx/vue/json/html/css
  "title": "src/game/world.js"
}
```

**封面**（`type: "cover"`）—— 首页 UI 上叠一层大标题：
```json
{
  "type": "cover",
  "title": "crazy-people",
  "subtitle": "Vue3 · 密闭空间发疯小人",
  "background_url_path": "/"
}
```

### 交互式确认卡点

跟主流水线一致，两处停下让你确认：

- **卡点 1（Step 0 之后）**：打印扫描结果（框架、路由、12 个关键文件）
- **卡点 2（Step 1 之后，截图之前）**：打印 8 段讲稿 + 每段的 shot_spec 摘要；选 `e` 可以打开 `generated_scenes.json` 手动改动分镜（换代码文件、调 focus_lines、加/删 interactions），保存后回车续跑

加 `--yes` / `-y` 关闭卡点一路跑到底。

### 常见问题

**Q：dev server 起不来？端口占用？**
A：先手动 `npm run dev` 起来，然后加 `--skip-dev-server`。或者换端口 `--dev-port 5174`。

**Q：Playwright 装 Chromium 卡住？**
A：先设镜像变量再装：`$env:PLAYWRIGHT_DOWNLOAD_HOST="https://npmmirror.com/mirrors/playwright"`，然后 `playwright install chromium`。装过一次以后不用再装。

**Q：LLM 选的代码文件我不喜欢？**
A：Step 1 跑完在卡点 2 选 `e`，打开 `generated_scenes.json` 手动改 `shot_spec.file` 和 `focus_lines`（文件必须在 `project_meta.json` 的 `key_files` 列表里），保存后回车续跑。或者直接编辑 `pipeline/project_scan.py` 里的 `ROLE_WEIGHT` 调整关键文件挑选权重。

**Q：只想看 8 张图长啥样，不想跑 TTS？**
A：`--skip-tts --skip-jianying`，图会落在 `outputs/code_walks/<项目名>/<时间戳>/images/`。

**Q：UI 截图内容随机（动画/随机数）导致每次不同？**
A：这是特性不是 bug —— 交互 Demo 类项目本来就要展示"活的画面"。LLM 生成的 narration 已经用了"你会看到"这种不依赖具体像素的话术。想要固定截图，可以手动改 shot_spec 加 `wait_ms` 或 `interactions` 控制状态。

**Q：项目不是 Vue？**
A：`project_scan.py` 已支持识别 Vue2/Vue3/React/Next.js/Nuxt/Svelte 六种框架，都从 `package.json` 的依赖里推断，dev 端口从对应的 `*.config.js` 里抠或用框架默认值。逻辑通用，理论上任何 `npm run dev` 起得来的 SPA 都能跑。

---

## 在 ZCode 里对话触发（可选）

项目自带一份 ZCode Skill 于 `~/.zcode/skills/video-maker/SKILL.md`。在 ZCode 会话里说以下任一句，AI 会自动识别并按上面工作流跑：

- "帮我生成一个视频" / "做一版视频" / "剪映草稿"
- "拼个梗图" / "把 6 月的图做成视频"
- "介绍南明李定国" / "讲讲苏轼被贬"
- 显式：`/video-maker`

AI 会**先展示计划**（选中的图源/BGM/张数/费用估算），你回复 `OK` / `跑` / `确认` 就一路跑到底；想改哪项直接说。

---

## 作者

zentrix566

## 许可证

[MIT](./LICENSE)

打开剪映专业版，每次都会出现一份新草稿（名字里带时间戳，不覆盖）。
