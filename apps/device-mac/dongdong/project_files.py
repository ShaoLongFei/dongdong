"""项目工作区的只读文件浏览 API。"""

from __future__ import annotations

import mimetypes
from pathlib import Path, PureWindowsPath

from fastapi import APIRouter, HTTPException, Query

_MAX_CONTENT = 200 * 1024
_SKIP = {".git", ".venv", "node_modules", "__pycache__"}


def _root(state, project_id: str) -> Path:
    try:
        root = state.project_workspace(project_id)
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(404, "项目不存在") from None
    if not root.is_dir():
        raise HTTPException(404, "项目目录不存在")
    return root


def _safe_path(root: Path, requested: str) -> Path:
    if (
        not requested
        or "\\" in requested
        or Path(requested).is_absolute()
        or PureWindowsPath(requested).is_absolute()
        or PureWindowsPath(requested).drive
    ):
        raise HTTPException(404, "文件路径不存在")
    try:
        relative = Path(requested)
        if any(part in {"", "."} for part in relative.parts):
            relative = Path(*[part for part in relative.parts if part not in {"", "."}])
        if any(part == ".." for part in relative.parts):
            raise HTTPException(404, "文件路径不存在")
        target = root.joinpath(relative)
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise HTTPException(404, "文件路径不存在")
        target.relative_to(root)
        if not target.exists():
            raise HTTPException(404, "文件路径不存在")
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(404, "文件路径不存在") from None
    return target


def _entry(root: Path, path: Path) -> dict:
    info = path.stat()
    return {
        "name": path.name,
        "type": "dir" if path.is_dir() else "file",
        "size": info.st_size,
        "mtime": info.st_mtime,
        "relative_path": path.relative_to(root).as_posix(),
    }


def router(state) -> APIRouter:
    routes = APIRouter(prefix="/api/projects")

    @routes.get("/{project_id}/files")
    async def list_files(
        project_id: str,
        path: str = Query(default="."),
        limit: int = Query(default=200),
    ):
        root = _root(state, project_id)
        directory = _safe_path(root, path)
        if not directory.is_dir():
            raise HTTPException(404, "目录不存在")
        rows = []
        try:
            children = sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name))
            for child in children:
                if (
                    child.name in _SKIP
                    or child.is_symlink()
                    or not (child.is_dir() or child.is_file())
                ):
                    continue
                rows.append(_entry(root, child))
        except OSError:
            raise HTTPException(404, "目录不存在") from None
        return {"files": rows[: max(1, min(limit, 200))], "path": path}

    @routes.get("/{project_id}/files/content")
    async def file_content(project_id: str, path: str = Query(...)):
        root = _root(state, project_id)
        target = _safe_path(root, path)
        if not target.is_file():
            raise HTTPException(404, "文件不存在")
        try:
            size = target.stat().st_size
            if size > _MAX_CONTENT:
                raise HTTPException(413, "文件过大")
            data = target.read_bytes()
        except HTTPException:
            raise
        except OSError:
            raise HTTPException(404, "文件不存在") from None
        if b"\x00" in data:
            raise HTTPException(415, "仅支持文本文件")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(415, "仅支持 UTF-8 文本文件") from None
        return {
            "text": text,
            "mime": mimetypes.guess_type(target.name)[0] or "text/plain",
            "size": size,
            "relative_path": target.relative_to(root).as_posix(),
        }

    return routes
