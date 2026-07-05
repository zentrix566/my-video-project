"""pipeline 包 —— 通用「主题 → 剪映视频」流水线的内部模块。

模块划分：
    helpers        — 环境变量加载、重试、并行图片生成、日志、费用估算
    camera         — 8 种运镜效果（zoom / pan / diagonal）
    subtitles      — 字幕样式预设（default / cinema / classic / vlog / comic）
    draft_composer — 剪映草稿构建器 JianyingDraftBuilder（基于 pyJianYingDraft）
    topic_to_story — Step 0：主题 → 旁白稿（本项目相对参考项目新增的一层）
    scene_split    — Step 1：旁白稿 → 场景数组（narration + image_prompt）
    image_gen      — Step 2：豆包 Seedream 出图
    tts            — Step 3：字节 Seed-TTS 出配音
    styles         — 风格预设 JSON 加载器（epic / documentary / shorts）
"""
