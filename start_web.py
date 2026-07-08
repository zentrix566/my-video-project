"""一键启动 Web 工作台
后端：FastAPI (http://localhost:8000)
前端：已经构建到 web-ui/dist/，由后端直接托管
启动后直接访问 http://localhost:8000 即可使用
"""
from __future__ import annotations

import os
import sys
import webbrowser
import threading
import time
from pathlib import Path

def open_browser():
    """延迟2秒打开浏览器"""
    time.sleep(2)
    webbrowser.open("http://localhost:8000")

if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("  🎬 AI 视频生成工作台")
    print("=" * 60)
    print(f"  后端 API: http://localhost:8000/api")
    print(f"  前端界面: http://localhost:8000")
    print(f"  API文档:  http://localhost:8000/docs")
    print("=" * 60)
    print()
    print("启动中... 浏览器将自动打开，如果没有请手动访问 http://localhost:8000")
    print()

    threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run(
        "web.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        workers=1,
    )
