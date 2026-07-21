"""Composer @ / mentions：校验、路径映射与 prompt 注入块。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Optional

from agent.backends.mount_paths import (
    AGENT_MEMORY_AGENTS_FILE,
    AGENT_MEMORY_USER_FILE,
    AGENT_PERSONAL_SKILLS_ROUTE,
)
from agent.skills_filter import _package_has_skill_md
from config.extensions_paths import skills_root
from config.user_data_paths import (
    get_session_attachments_dir,
    get_session_uploads_dir,
    get_user_agents_md_path,
    get_user_profile_md_path,
    get_user_root,
    get_user_skills_dir,
    get_workspace_dir,
)
from constants.code_enum import IntentEnum
from exceptions.exception import ServiceException
from schemas.qa_vo import MentionItem

MentionType = Literal["skill", "file", "folder", "subagent"]

_SUBAGENTS_BY_QA: dict[str, frozenset[str]] = {
    IntentEnum.SUPER_AGENT_QA.value[0]: frozenset({"task-worker"}),
    IntentEnum.FAULT_OPERATION_QA.value[0]: frozenset({"general-purpose"}),
}

_ALLOWED_TYPES_BY_QA: dict[str, frozenset[MentionType]] = {
    IntentEnum.SUPER_AGENT_QA.value[0]: frozenset({"skill", "file", "folder", "subagent"}),
    IntentEnum.FAULT_OPERATION_QA.value[0]: frozenset({"file", "folder", "subagent"}),
}

_USER_ROOT_FILES = frozenset({"AGENTS.md", "USER.md"})


@dataclass
class ResolvedMentions:
    """校验通过后的 mentions 结果。"""

    items: list[MentionItem] = field(default_factory=list)
    prompt_block: str = ""
    skill_ids: list[str] = field(default_factory=list)
    persistence: list[dict[str, Any]] = field(default_factory=list)


def parse_mention_items(raw: Any) -> Optional[list[MentionItem]]:
    """将请求体 mentions 解析为 MentionItem 列表；None/缺省表示未传。"""
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ServiceException(message="mentions 必须为数组")
    items: list[MentionItem] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ServiceException(message=f"mentions[{i}] 必须为对象")
        try:
            items.append(MentionItem.model_validate(entry))
        except Exception as exc:  # noqa: BLE001 — 统一为业务错误
            raise ServiceException(message=f"mentions[{i}] 非法: {exc}") from exc
    return items


class MentionResolveService:
    @classmethod
    def resolve(
        cls,
        *,
        mentions: Optional[Iterable[MentionItem] | list[dict[str, Any]]],
        qa_type: str,
        user_id: str,
        session_id: str,
    ) -> ResolvedMentions:
        if mentions is None:
            return ResolvedMentions()

        if isinstance(mentions, list) and mentions and isinstance(mentions[0], dict):
            items = parse_mention_items(mentions) or []
        else:
            items = list(mentions)  # type: ignore[arg-type]

        if not items:
            return ResolvedMentions()

        allowed = _ALLOWED_TYPES_BY_QA.get(qa_type)
        if allowed is None:
            raise ServiceException(message=f"当前问答类型不支持 mentions: {qa_type}")

        if not session_id and any(m.type in ("file", "folder") for m in items):
            raise ServiceException(message="file/folder mention 需要有效 session_id")

        skill_ids: list[str] = []
        lines: list[str] = ["<user_mentions>", "用户在本轮通过 @ / 显式引用了以下上下文，请优先处理："]
        persistence: list[dict[str, Any]] = []

        for idx, item in enumerate(items):
            mtype = item.type
            if mtype not in allowed:
                if mtype == "skill" and qa_type == IntentEnum.FAULT_OPERATION_QA.value[0]:
                    raise ServiceException(message="故障运维会话不支持 skill mention")
                raise ServiceException(message=f"当前问答类型不支持 mention type={mtype}")

            if mtype == "skill":
                sid, source, line, persisted = cls._resolve_skill(item, user_id)
                skill_ids.append(sid)
                lines.append(f"- {line}")
                persistence.append(persisted)
            elif mtype in ("file", "folder"):
                line, persisted = cls._resolve_path_item(
                    item,
                    user_id=user_id,
                    session_id=session_id,
                    is_folder=(mtype == "folder"),
                )
                lines.append(f"- {line}")
                persistence.append(persisted)
            elif mtype == "subagent":
                line, persisted = cls._resolve_subagent(item, qa_type)
                lines.append(f"- {line}")
                persistence.append(persisted)
            else:
                raise ServiceException(message=f"mentions[{idx}] 未知 type")

        lines.append("</user_mentions>")
        # 去重 skill_ids 保序
        seen: set[str] = set()
        unique_skills: list[str] = []
        for s in skill_ids:
            if s not in seen:
                seen.add(s)
                unique_skills.append(s)

        return ResolvedMentions(
            items=list(items),
            prompt_block="\n".join(lines),
            skill_ids=unique_skills,
            persistence=persistence,
        )

    @classmethod
    def _resolve_skill(
        cls,
        item: MentionItem,
        user_id: str,
    ) -> tuple[str, str, str, dict[str, Any]]:
        sid = (item.id or "").strip()
        if not sid:
            raise ServiceException(message="skill mention 缺少 id")
        if "/" in sid or ".." in sid or sid.startswith("."):
            raise ServiceException(message=f"非法 skill id: {sid}")

        platform_ok = _package_has_skill_md(skills_root(), sid)
        user_ok = _package_has_skill_md(get_user_skills_dir(user_id), sid)
        source = (item.source or "").strip()
        if source == "platform":
            if not platform_ok:
                raise ServiceException(message=f"平台 Skill 不存在: {sid}")
        elif source == "user":
            if not user_ok:
                raise ServiceException(message=f"用户 Skill 不存在: {sid}")
        else:
            if platform_ok:
                source = "platform"
            elif user_ok:
                source = "user"
            else:
                raise ServiceException(message=f"Skill 不存在: {sid}")

        if source == "platform":
            skill_path = f"/skills/public/{sid}/SKILL.md"
        else:
            skill_path = f"{AGENT_PERSONAL_SKILLS_ROUTE.rstrip('/')}/{sid}/SKILL.md"

        line = (
            f"skill `{sid}`（{source}）：用户点名该 Skill，请先 `read_file` "
            f"`{skill_path}`（建议 limit=1000）再按其协议执行"
        )
        return sid, source, line, {
            "type": "skill",
            "id": sid,
            "source": source,
            "virtual_path": skill_path,
        }

    @classmethod
    def _resolve_subagent(
        cls,
        item: MentionItem,
        qa_type: str,
    ) -> tuple[str, dict[str, Any]]:
        sid = (item.id or "").strip()
        if not sid:
            raise ServiceException(message="subagent mention 缺少 id")
        allowed = _SUBAGENTS_BY_QA.get(qa_type, frozenset())
        if sid not in allowed:
            raise ServiceException(message=f"未知或不适用的 subagent: {sid}")
        line = (
            f"subagent `{sid}`：若任务适合委派，优先使用 `task` 且 "
            f"`subagent_type={sid}`；简单一两步仍应自行完成，勿机械强制委派"
        )
        return line, {"type": "subagent", "id": sid}

    @classmethod
    def _normalize_rel_path(cls, rel_path: str, session_id: str) -> str:
        norm = rel_path.strip().replace("\\", "/")
        if norm.startswith("users/"):
            # 前端偶发带 users/{uid}/ 前缀时剥掉至相对用户根
            parts = norm.split("/")
            if len(parts) >= 3:
                norm = "/".join(parts[2:])
        if not norm or ".." in norm.split("/"):
            raise ServiceException(message="非法路径")
        if norm in _USER_ROOT_FILES:
            return norm
        if norm.startswith("skills/"):
            return norm
        session_prefix = f"sessions/{session_id}/"
        if norm.startswith(session_prefix):
            tail = norm[len(session_prefix):]
            if tail.startswith(("workspace/", "uploads/", "attachments/")):
                return norm
            raise ServiceException(message="非法路径")
        if norm.startswith(("workspace/", "uploads/", "attachments/")):
            return f"{session_prefix}{norm}"
        raise ServiceException(message="非法路径")

    @classmethod
    def _to_agent_virtual_path(
        cls,
        rel_norm: str,
        session_id: str,
    ) -> str:
        if rel_norm == "AGENTS.md":
            return AGENT_MEMORY_AGENTS_FILE
        if rel_norm == "USER.md":
            return AGENT_MEMORY_USER_FILE
        if rel_norm.startswith("skills/"):
            rest = rel_norm[len("skills/"):]
            return f"{AGENT_PERSONAL_SKILLS_ROUTE.rstrip('/')}/{rest}"
        session_prefix = f"sessions/{session_id}/"
        if rel_norm.startswith(session_prefix):
            tail = rel_norm[len(session_prefix):]
            if tail.startswith("workspace/"):
                return "/" + tail[len("workspace/"):]
            if tail.startswith("uploads/"):
                return f"/uploads/{tail[len('uploads/'):]}"
            if tail.startswith("attachments/"):
                return f"/attachments/{tail[len('attachments/'):]}"
        raise ServiceException(message=f"无法映射虚拟路径: {rel_norm}")

    @classmethod
    def _host_path(cls, rel_norm: str, user_id: str, session_id: str) -> str:
        if rel_norm == "AGENTS.md":
            return str(get_user_agents_md_path(user_id))
        if rel_norm == "USER.md":
            return str(get_user_profile_md_path(user_id))
        if rel_norm.startswith("skills/"):
            root = str(get_user_skills_dir(user_id))
            return os.path.join(root, rel_norm[len("skills/"):])
        session_prefix = f"sessions/{session_id}/"
        if not rel_norm.startswith(session_prefix):
            raise ServiceException(message="非法路径")
        tail = rel_norm[len(session_prefix):]
        if tail.startswith("workspace/"):
            return os.path.join(str(get_workspace_dir(user_id, session_id)), tail[len("workspace/"):])
        if tail.startswith("uploads/"):
            return os.path.join(str(get_session_uploads_dir(user_id, session_id)), tail[len("uploads/"):])
        if tail.startswith("attachments/"):
            return os.path.join(
                str(get_session_attachments_dir(user_id, session_id)),
                tail[len("attachments/"):],
            )
        raise ServiceException(message="非法路径")

    @classmethod
    def _resolve_path_item(
        cls,
        item: MentionItem,
        *,
        user_id: str,
        session_id: str,
        is_folder: bool,
    ) -> tuple[str, dict[str, Any]]:
        raw_path = (item.path or item.virtual_path or "").strip()
        if not raw_path:
            raise ServiceException(message="file/folder mention 缺少 path")
        rel_norm = cls._normalize_rel_path(raw_path, session_id)
        host = cls._host_path(rel_norm, user_id, session_id)
        # 防穿越：host 必须在对应用户数据根下
        user_root = os.path.abspath(str(get_user_root(user_id)))
        host_abs = os.path.abspath(host)
        if host_abs != user_root and not host_abs.startswith(user_root + os.sep):
            raise ServiceException(message="路径越权")

        if is_folder:
            if not os.path.isdir(host_abs):
                raise ServiceException(message=f"目录不存在: {rel_norm}")
        else:
            if not os.path.isfile(host_abs):
                raise ServiceException(message=f"文件不存在: {rel_norm}")

        virtual = (item.virtual_path or "").strip() or cls._to_agent_virtual_path(
            rel_norm,
            session_id,
        )
        kind = "folder" if is_folder else "file"
        action = (
            "请列出/浏览该目录下相关文件后再继续"
            if is_folder
            else "请先通过工具读取该路径（不要假设已内联全文）"
        )
        line = f"{kind} `{virtual}`（{rel_norm}）：{action}"
        return line, {
            "type": kind,
            "path": rel_norm,
            "virtual_path": virtual,
        }
