"""扫描剪映草稿目录，恢复历史任务记录到task_history.json"""
from __future__ import annotations
import json
import re
from datetime import datetime
from pathlib import Path

# 配置
DRAFT_FOLDER = Path("D:/software/JianyingPro Drafts")
HISTORY_FILE = Path(__file__).parent / "outputs" / "task_history.json"
HISTORY_FILE.parent.mkdir(exist_ok=True)

# 草稿名模式匹配
WORKFLOW_PATTERNS = [
    (r"代码走读|走读|项目", "code_walk", "代码走读视频"),
    (r"主题|历史|人物|介绍", "topic", "主题AI生成片"),
    (r"梗图|图集", "meme", "梗图/图集视频"),
    (r"轮播|卡片", "carousel", "卡片轮播短视频"),
    (r"需求|讲解|PDF|pdf", "doc_video", "需求讲解视频"),
    (r"录屏|演示|配音", "narration", "录屏讲解视频"),
]

def guess_workflow(name: str) -> tuple[str, str]:
    """根据草稿名猜测工作流类型"""
    for pattern, wf_type, label in WORKFLOW_PATTERNS:
        if re.search(pattern, name):
            return wf_type, label
    # 默认按主题片处理
    return "topic", "主题AI生成片"

def parse_timestamp(name: str) -> datetime | None:
    """从目录名解析时间戳 格式：_YYYYMMDD_HHMMSS"""
    m = re.search(r"_(\d{8})_(\d{6})$", name)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)}{m.group(2)}", "%Y%m%d%H%M%S")
    except ValueError:
        return None

def main():
    if not DRAFT_FOLDER.exists():
        print(f"草稿目录不存在: {DRAFT_FOLDER}")
        return

    # 读取现有历史
    existing = []
    if HISTORY_FILE.exists():
        existing = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    existing_ids = {item["task_id"] for item in existing}

    # 扫描草稿目录
    recovered = 0
    for draft_dir in DRAFT_FOLDER.iterdir():
        if not draft_dir.is_dir() or draft_dir.name.startswith("."):
            continue
        
        # 解析时间和名称
        created_at = parse_timestamp(draft_dir.name)
        if not created_at:
            continue
        task_name = re.sub(r"_\d{8}_\d{6}$", "", draft_dir.name)
        task_id = f"recovered_{created_at.strftime('%Y%m%d%H%M%S')}"
        
        if task_id in existing_ids:
            continue

        wf_type, wf_label = guess_workflow(draft_dir.name)
        
        task = {
            "task_id": task_id,
            "name": task_name,
            "workflow": wf_type,
            "status": "success",
            "created_at": created_at.isoformat(),
            "started_at": created_at.isoformat(),
            "finished_at": created_at.isoformat(),
            "params": {
                "note": "从剪映草稿目录自动恢复的历史记录"
            },
            "draft_path": str(draft_dir.absolute()),
            "output_dir": str(draft_dir.absolute()),
            "error": None,
            "progress": 100.0,
            "current_step": "完成",
        }
        existing.append(task)
        recovered += 1
        print(f"✅ 恢复: {draft_dir.name} -> {wf_label}")

    # 按时间倒序排序，保留100条
    existing.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    existing = existing[:100]
    
    HISTORY_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n🎉 共恢复 {recovered} 条历史记录，总计 {len(existing)} 条")
    print(f"历史文件已保存到: {HISTORY_FILE}")
    print("请刷新浏览器，或者重启工作台即可看到历史任务")

if __name__ == "__main__":
    main()
