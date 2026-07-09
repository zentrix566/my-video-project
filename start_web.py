"""🚀 AI 视频生成工作台 - 一键启动脚本

功能：
1. 自动检查并安装后端Python依赖
2. 自动安装前端npm依赖（首次）
3. 自动构建前端（首次或有更新时）
4. 启动后端服务
5. 自动打开浏览器

使用：直接双击 start.bat 或运行 python start_web.py
"""
from __future__ import annotations

import os
import sys
import subprocess
import webbrowser
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB_UI_DIR = ROOT / "web-ui"
DIST_DIR = WEB_UI_DIR / "dist"
REQUIREMENTS = ROOT / "requirements.txt"
PACKAGE_JSON = WEB_UI_DIR / "package.json"

# 终端颜色
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
RESET = "\033[0m"


def print_step(msg: str, color=BLUE):
    print(f"\n{color}{'='*60}{RESET}")
    print(f"{color}  {msg}{RESET}")
    print(f"{color}{'='*60}{RESET}\n")


def print_ok(msg: str):
    print(f"{GREEN}✅ {msg}{RESET}")


def print_warn(msg: str):
    print(f"{YELLOW}⚠️  {msg}{RESET}")


def print_err(msg: str):
    print(f"{RED}❌ {msg}{RESET}")


def run(cmd: list[str], cwd: Path = None, check: bool = True):
    """运行命令，实时输出"""
    print(f"$ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        shell=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"命令执行失败 (exit code {result.returncode})")
    return result


def check_python_deps():
    """检查后端依赖是否安装"""
    print_step("1/4 检查后端Python依赖")
    required_packages = ["fastapi", "uvicorn", "pydantic", "PIL", "pyJianYingDraft"]
    missing = []
    for pkg in required_packages:
        try:
            if pkg == "PIL":
                import PIL
            else:
                __import__(pkg)
        except ImportError:
            missing.append(pkg)

    if missing:
        print_warn(f"缺少依赖包: {', '.join(missing)}")
        print("正在自动安装依赖...")
        run([sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)])
        print_ok("Python依赖安装完成")
    else:
        print_ok("Python依赖已就绪")


def check_npm_deps():
    """检查前端npm依赖是否安装"""
    print_step("2/4 检查前端依赖")
    node_modules = WEB_UI_DIR / "node_modules"
    if not node_modules.exists() or not (node_modules / ".package-lock.json").exists():
        print_warn("前端依赖未安装，正在执行 npm install ...")
        run(["npm", "install"], cwd=WEB_UI_DIR)
        print_ok("前端依赖安装完成")
    else:
        print_ok("前端依赖已就绪")


def build_frontend():
    """构建前端（如果dist不存在）"""
    print_step("3/4 构建前端")
    index_html = DIST_DIR / "index.html"
    if index_html.exists():
        # 检查是否有文件比dist新，需要重新构建
        need_rebuild = False
        src_mtime = 0
        for p in WEB_UI_DIR.rglob("*"):
            if p.is_file() and "node_modules" not in str(p) and "dist" not in str(p):
                mtime = p.stat().st_mtime
                if mtime > src_mtime:
                    src_mtime = mtime
        dist_mtime = index_html.stat().st_mtime
        if src_mtime > dist_mtime:
            print_warn("检测到前端文件有更新，重新构建...")
            need_rebuild = True
        else:
            print_ok("前端已构建，无需重复构建")
            return
    else:
        need_rebuild = True
        print_warn("前端未构建，开始构建...")

    if need_rebuild:
        run(["npm", "run", "build"], cwd=WEB_UI_DIR)
        print_ok("前端构建完成")


def open_browser():
    """延迟打开浏览器"""
    time.sleep(2.5)
    print_ok(f"浏览器已打开: http://localhost:8000")
    webbrowser.open("http://localhost:8000")


def start_server():
    """启动后端服务"""
    print_step("4/4 启动后端服务")
    print(f"""
{GREEN}  🎬 AI 视频生成工作台已启动！{RESET}

  🌐 访问地址: http://localhost:8000
  📖 API文档:  http://localhost:8000/docs
  📁 上传目录: {ROOT / 'uploads'}

{YELLOW}  按 Ctrl+C 停止服务{RESET}
""")
    # 自动打开浏览器
    threading.Thread(target=open_browser, daemon=True).start()

    import uvicorn
    uvicorn.run(
        "web.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        workers=1,
    )


def check_node_npm():
    """检查node和npm是否安装（Windows下兼容PowerShell/cmd）"""
    # 先检查常见安装路径
    common_paths = [
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "nodejs",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "nodejs",
    ]
    for p in common_paths:
        if (p / "node.exe").exists():
            os.environ["PATH"] = str(p) + os.pathsep + os.environ.get("PATH", "")
            break

    # 在Windows上也尝试用where查找
    try:
        result = subprocess.run(["where", "node"], capture_output=True, text=True, shell=True)
        if result.returncode != 0 or not result.stdout.strip():
            result = subprocess.run(["node", "--version"], capture_output=True, shell=True)
            if result.returncode != 0:
                return False
        subprocess.run(["npm", "--version"], capture_output=True, shell=True, check=True)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    os.chdir(str(ROOT))

    # 解析命令行参数
    skip_build = "--skip-build" in sys.argv or "--fast" in sys.argv
    no_browser = "--no-browser" in sys.argv

    print(f"""
{GREEN}  ╔══════════════════════════════════════════════╗
  ║       🎬 AI 视频生成工作台 - 一键启动        ║
  ╚══════════════════════════════════════════════╝{RESET}
""")

    # 检查Python版本
    py_ver = sys.version_info
    if py_ver < (3, 10):
        print_warn(f"当前Python版本: {py_ver.major}.{py_ver.minor}，推荐使用 3.10+")

    has_node = check_node_npm()
    dist_exists = (DIST_DIR / "index.html").exists()

    if skip_build:
        print_warn("使用 --skip-build 参数，跳过依赖检查和前端构建，直接启动")
    else:
        if not has_node and not dist_exists:
            print_err("未检测到 Node.js 和 npm，且前端未构建！")
            print("""
两种解决方案:
1. 安装 Node.js: 访问 https://nodejs.org/ 下载 LTS 版本安装后重新运行
2. 如果已经在其他地方构建过前端，直接运行: python start_web.py --skip-build
""")
            sys.exit(1)
        if not has_node and dist_exists:
            print_warn("未检测到Node.js，但前端已构建，直接启动服务")
        else:
            print_ok("Node.js/npm 已安装")

        try:
            check_python_deps()
            if has_node:
                check_npm_deps()
                build_frontend()
        except Exception as e:
            print_err(f"依赖检查/构建失败: {e}")
            if dist_exists:
                print_warn("尝试使用已有构建启动...")
            else:
                raise

    try:
        start_server()
    except KeyboardInterrupt:
        print(f"\n{YELLOW}服务已停止{RESET}")
    except Exception as e:
        print_err(f"启动失败: {e}")
        import traceback
        traceback.print_exc()
        input("\n按回车键退出...")
        sys.exit(1)
