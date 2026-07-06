"""Step 0（代码走读版）：扫描目标前端项目，产出项目元信息 JSON。

这一步是**纯本地文件扫描**，不调用 LLM，也不需要密钥。
产物 project_meta.json 会被 walk_narrator.py 当作输入喂给 LLM 生成讲解稿。

覆盖内容：
    - package.json 摘要（name、version、scripts、主要依赖）
    - README.md 全文（截断到 8KB）
    - 前端框架识别（vue2/vue3/react/svelte/其他）
    - dev 命令 + 端口（从 vite.config.* / package.json script 里推断）
    - 路由列表（如果存在 vue-router / react-router，用正则扫 routes 定义）
    - 关键文件清单（含 path/role/loc/首 60 行摘录）
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from pipeline.helpers import PipelineLogger, safe_slug


# ---- 关键文件角色启发式规则 ----
# 顺序即优先级：先命中的角色标签更"重要"，用于最终排序
ROLE_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("entry",   re.compile(r"(^|/)src/main\.(js|ts|jsx|tsx)$")),
    ("root",    re.compile(r"(^|/)src/App\.(vue|jsx|tsx|js|ts)$")),
    ("router",  re.compile(r"(^|/)src/router/(index|routes)\.(js|ts)$")),
    ("state",   re.compile(r"(^|/)src/(store|stores)/[^/]+\.(js|ts)$")),
    ("api",     re.compile(r"(^|/)src/api/[^/]+\.(js|ts)$")),
    ("view",    re.compile(r"(^|/)src/views/[^/]+\.(vue|jsx|tsx)$")),
    ("config",  re.compile(r"(^/|^)(vite|vue|next|nuxt|webpack)\.config\.(js|ts|mjs|cjs)$")),
    ("core",    re.compile(r"(^|/)src/[^/]+/[^/]+\.(js|ts)$")),
    ("component", re.compile(r"(^|/)src/components/[^/]+\.(vue|jsx|tsx)$")),
]

ROLE_WEIGHT = {
    "entry":     100,
    "root":      95,
    "router":    90,
    "state":     85,
    "config":    70,
    "view":      65,
    "api":       60,
    "core":      55,
    "component": 40,
    "other":     10,
}


def _classify_role(rel_path: str) -> str:
    for role, pattern in ROLE_RULES:
        if pattern.search(rel_path):
            return role
    return "other"


def _detect_framework(pkg: dict[str, Any]) -> str:
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    if "vue" in deps:
        version = str(deps["vue"])
        return "vue3" if re.search(r"[\^~]?3", version) else "vue2"
    if "react" in deps:
        return "react"
    if "svelte" in deps:
        return "svelte"
    if "next" in deps:
        return "nextjs"
    if "nuxt" in deps:
        return "nuxt"
    return "unknown"


_PORT_RE = re.compile(r"port\s*:\s*(\d{2,5})")


def _detect_dev_port(project_root: Path, framework: str) -> int:
    """从 vite/webpack/nuxt 配置里抠 port；抠不到用框架默认值。"""
    candidates = [
        "vite.config.js", "vite.config.ts", "vite.config.mjs",
        "vue.config.js", "vue.config.ts",
        "next.config.js", "next.config.ts", "next.config.mjs",
        "nuxt.config.js", "nuxt.config.ts",
        "webpack.config.js",
    ]
    for name in candidates:
        p = project_root / name
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = _PORT_RE.search(text)
        if m:
            return int(m.group(1))

    defaults = {"vue3": 5173, "vue2": 8080, "react": 3000, "nextjs": 3000, "nuxt": 3000}
    return defaults.get(framework, 5173)


def _detect_dev_command(pkg: dict[str, Any]) -> str:
    scripts = pkg.get("scripts") or {}
    for key in ("dev", "serve", "start"):
        if key in scripts:
            return f"npm run {key}"
    return "npm run dev"


_ROUTE_PATH_RE = re.compile(r"""path\s*:\s*['"]([^'"]+)['"]""")


def _scan_routes(project_root: Path) -> list[dict[str, str]]:
    """尽力抠出 vue-router / react-router 的 path 列表，抠不到就返回空。"""
    router_files = list((project_root / "src" / "router").glob("*.*")) if (project_root / "src" / "router").exists() else []
    if not router_files:
        for candidate in (project_root / "src").rglob("router*.js"):
            router_files.append(candidate)
        for candidate in (project_root / "src").rglob("router*.ts"):
            router_files.append(candidate)
    routes: list[dict[str, str]] = []
    seen: set[str] = set()
    for rf in router_files[:5]:
        try:
            text = rf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in _ROUTE_PATH_RE.finditer(text):
            path = m.group(1).strip()
            if path and path not in seen and not path.startswith(":"):
                seen.add(path)
                routes.append({"path": path, "source": rf.name})
    return routes


def _relative(path: Path, root: Path) -> str:
    """给 walk_narrator 用的 posix 相对路径。"""
    return path.relative_to(root).as_posix()


def _read_text_safely(path: Path, max_bytes: int = 20000) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > max_bytes:
        data = data[:max_bytes]
    return data.decode("utf-8", errors="ignore")


def _line_count(text: str) -> int:
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


def _walk_source_files(project_root: Path) -> list[Path]:
    """收集 src/ 下所有源码文件；若无 src/，退化为项目根一层（排除 node_modules/dist/等）。"""
    exts = {".vue", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}
    ignored_dirs = {"node_modules", "dist", "build", ".git", ".next", ".nuxt", "out", "coverage"}

    src_dir = project_root / "src"
    if src_dir.exists() and src_dir.is_dir():
        base = src_dir
    else:
        base = project_root

    results: list[Path] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in exts:
            continue
        # 剔除被忽略目录
        parts = set(path.relative_to(project_root).parts)
        if parts & ignored_dirs:
            continue
        results.append(path)
    return results


def _pick_key_files(
    all_files: list[Path],
    project_root: Path,
    *,
    max_files: int = 12,
) -> list[dict[str, Any]]:
    """按 role 权重 + 文件大小综合排序，挑出讲解视频最值得展示的文件。"""
    scored: list[tuple[int, int, Path, str]] = []
    for path in all_files:
        rel = _relative(path, project_root)
        role = _classify_role(rel)
        weight = ROLE_WEIGHT.get(role, 0)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        scored.append((weight, size, path, role))

    # 先按权重降序；同权重内按大小降序（大文件更值得讲）
    scored.sort(key=lambda t: (-t[0], -t[1]))
    picked: list[dict[str, Any]] = []
    for _weight, size, path, role in scored[:max_files]:
        text = _read_text_safely(path)
        loc = _line_count(text)
        # 摘录：前 60 行足以让 LLM 判断作用
        excerpt = "\n".join(text.splitlines()[:60])
        picked.append({
            "path": _relative(path, project_root),
            "role": role,
            "loc": loc,
            "size_bytes": size,
            "excerpt": excerpt,
        })
    return picked


def scan_project(
    project_root: Path,
    output_dir: Path,
    logger: PipelineLogger,
    *,
    max_key_files: int = 12,
) -> dict[str, Any]:
    """扫描目标项目，写入 output_dir/project_meta.json，并返回同一份 dict。"""
    project_root = Path(project_root).resolve()
    if not project_root.exists():
        raise SystemExit(f"目标项目路径不存在: {project_root}")
    if not project_root.is_dir():
        raise SystemExit(f"目标项目路径不是目录: {project_root}")

    logger.info("step:project_scan.start", root=str(project_root))
    t0 = time.time()

    # ---- package.json ----
    pkg_path = project_root / "package.json"
    if not pkg_path.exists():
        raise SystemExit(f"未找到 package.json: {pkg_path}（暂只支持 Node 前端项目）")
    try:
        pkg_raw = json.loads(pkg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"package.json 解析失败: {exc}") from exc

    project_name = str(pkg_raw.get("name") or project_root.name).strip() or project_root.name
    version = str(pkg_raw.get("version") or "").strip()
    description = str(pkg_raw.get("description") or "").strip()
    scripts = pkg_raw.get("scripts") or {}
    deps = pkg_raw.get("dependencies") or {}
    dev_deps = pkg_raw.get("devDependencies") or {}

    framework = _detect_framework(pkg_raw)
    dev_port = _detect_dev_port(project_root, framework)
    dev_command = _detect_dev_command(pkg_raw)

    # ---- README.md ----
    readme_text = ""
    for name in ("README.md", "readme.md", "README.MD", "Readme.md"):
        p = project_root / name
        if p.exists():
            readme_text = _read_text_safely(p, max_bytes=8000)
            break

    # ---- 路由 ----
    routes = _scan_routes(project_root)

    # ---- 关键文件 ----
    all_files = _walk_source_files(project_root)
    key_files = _pick_key_files(all_files, project_root, max_files=max_key_files)

    meta = {
        "project_name": safe_slug(project_name, fallback="frontend_project"),
        "raw_name": project_name,
        "version": version,
        "description": description,
        "root": str(project_root).replace("\\", "/"),
        "framework": framework,
        "dev_command": dev_command,
        "dev_port": dev_port,
        "package_scripts": scripts,
        "dependencies_top": list(deps.keys())[:20],
        "dev_dependencies_top": list(dev_deps.keys())[:20],
        "readme_md": readme_text,
        "routes": routes,
        "source_file_count": len(all_files),
        "key_files": key_files,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "project_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "step:project_scan.done",
        elapsed_s=round(time.time() - t0, 2),
        framework=framework,
        dev_port=dev_port,
        routes=len(routes),
        key_files=len(key_files),
    )
    return meta
