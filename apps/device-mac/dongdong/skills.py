"""只发现本地 SKILL.md；目录名是稳定 ID，正文在使用时按需读取。"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import config, work_gate

log = logging.getLogger(__name__)
_LIMIT = 32 * 1024


class SkillIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)


def _path(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("技能目录名只允许英文字母、数字、下划线和连字符")
    try:
        root = (config.HOME / "skills").resolve()
        path = (root / name / "SKILL.md").resolve()
    except RuntimeError as exc:
        raise ValueError(f"技能 {name} 的符号链接存在循环") from exc
    if not path.is_relative_to(root):
        raise ValueError("技能文件不能通过符号链接指向本地 skills 目录之外")
    if not path.is_file():
        raise FileNotFoundError(f"找不到技能 {name} 的 SKILL.md")
    return path


def _content(name: str) -> str:
    with _path(name).open("rb") as source:
        data = source.read(_LIMIT + 1)
    if len(data) > _LIMIT:
        raise ValueError(f"技能 {name} 超过 32KB，请缩短 SKILL.md 后重试")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"技能 {name} 的 SKILL.md 必须使用 UTF-8 编码") from exc


def list_skills(cfg: dict) -> list[dict]:
    root = config.HOME / "skills"
    if not root.is_dir():
        return []
    result = []
    for directory in sorted(root.iterdir()):
        try:
            text = _content(directory.name)
            body = text
            description = ""
            lines = text.splitlines()
            if lines and lines[0].strip() == "---":
                end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
                if end is not None:
                    metadata = yaml.safe_load("\n".join(lines[1:end]))
                    if isinstance(metadata, dict) and isinstance(metadata.get("description"), str):
                        description = metadata["description"].strip()
                    body = "\n".join(lines[end + 1 :])
            if not description:
                description = body.strip().split("\n\n", 1)[0].strip()
            result.append(
                {
                    "name": directory.name,
                    "description": description,
                    "enabled": directory.name not in cfg.get("disabled_skills", []),
                }
            )
        except (OSError, ValueError, yaml.YAMLError) as exc:
            log.warning("跳过不可用技能 %s: %s", directory.name, exc)
    return result


def read_skill(name: str, cfg: dict) -> str:
    _path(name)
    if name in cfg.get("disabled_skills", []):
        raise ValueError(f"技能 {name} 已禁用，请先启用再读取")
    return _content(name)


def router(cfg: dict) -> APIRouter:
    routes = APIRouter(prefix="/api/skills")

    @routes.get("")
    async def listing():
        return {"skills": list_skills(cfg)}

    @routes.get("/{name}")
    async def detail(name: str):
        try:
            return {"name": name, "content": read_skill(name, cfg)}
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @routes.put("/{name}")
    async def update(name: str, body: SkillIn):
        if not work_gate.acquire("skills-config"):
            raise HTTPException(409, "任务运行中，请结束后再修改技能；修改对下一任务生效")
        previous = cfg.get("disabled_skills", [])
        try:
            _content(name)
            disabled = set(previous)
            disabled.discard(name) if body.enabled else disabled.add(name)
            cfg["disabled_skills"] = sorted(disabled)
            config.save(cfg)
            return {"name": name, "enabled": body.enabled}
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ValueError, OSError) as exc:
            cfg["disabled_skills"] = previous
            raise HTTPException(400, str(exc)) from exc
        finally:
            work_gate.release("skills-config")

    return routes
