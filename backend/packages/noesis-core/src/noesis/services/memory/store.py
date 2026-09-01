"""md 文件记忆层文件服务：索引、条目、journal 的唯一读写通道。

设计要点（openspec: agent-memory-cortex）：
- ``MEMORY.md`` 索引：五类分组、一行一条（``[标签] 一句话 → type/slug.md``），
  行数 + 字节双保险预算；损坏行跳过，可从条目目录重建（frontmatter 投影）。
- 条目文件：一条一文件，YAML frontmatter 承载结构化元数据（字段集冻结为
  type/label/description/tags/created/updated/sources），正文保留结论、Why、
  适用条件散文节；frontmatter 解析失败退化为散文容错（存量/手写条目）。
- journal：按日情景日志，只追加，永不改写（含抽取决策块与整理快照块）。
- 所有写入原子（tmp + os.replace），写入前重读（不基于陈旧内存覆盖）。
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

from noesis.config.user_data_paths import get_user_root
from noesis.services.memory.types import MEMORY_TYPES, TYPE_LABELS, validate_memory_type

_INDEX_HEADER = "<!-- Noesis 记忆索引：引擎维护，可手动编辑；一行一条 -->\n\n"
_INDEX_LINE_RE = re.compile(
    r"^-\s*\[(?P<label>[^\]]+)\]\s*(?P<desc>.*?)\s*→\s*(?P<type>preference|goal|decision|experience|gotcha)/(?P<slug>[A-Za-z0-9_-]+)\.md\s*$"
)
_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(?P<block>.*?)\n---[ \t]*\n?", re.DOTALL)

# frontmatter 字段集冻结（openspec: agent-memory-cortex）：新增字段需新变更提案
FRONTMATTER_FIELDS: tuple[str, ...] = (
    "type",
    "label",
    "description",
    "tags",
    "created",
    "updated",
    "sources",
)
_DESCRIPTION_MAX_CHARS = 160
# description 回退截断（无 frontmatter 的存量/手写条目，索引行沿用旧语义）
_DESCRIPTION_FALLBACK_CHARS = 60

JOURNAL_DIR = "journal"


@dataclass(frozen=True)
class IndexEntry:
    memory_type: str
    slug: str
    label: str
    description: str

    @property
    def rel_path(self) -> str:
        return f"{self.memory_type}/{self.slug}.md"


@dataclass
class IndexState:
    entries: list[IndexEntry] = field(default_factory=list)
    corrupt_lines: int = 0
    over_budget: bool = False

    def by_type(self, memory_type: str) -> list[IndexEntry]:
        return [entry for entry in self.entries if entry.memory_type == memory_type]


def _now_date() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _yaml_scalar(value: object) -> str:
    """YAML 标量 → 文本（date/datetime 由解析器产生，统一转 ISO 文本）。"""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _meta_str(meta: dict[str, object], key: str) -> str:
    value = meta.get(key)
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return _yaml_scalar(value).strip()


def _meta_str_list(meta: dict[str, object], key: str) -> list[str]:
    value = meta.get(key)
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = [_yaml_scalar(item).strip() for item in value]
    else:
        text = _yaml_scalar(value).strip()
        items = [text] if text else []
    return [item for item in items if item]


def _split_frontmatter(text: str) -> tuple[dict[str, object] | None, str]:
    """拆出 YAML frontmatter；缺失或解析失败返回 (None, 原文) 走散文容错。"""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None, text
    try:
        raw = yaml.safe_load(match.group("block"))
    except yaml.YAMLError:
        return None, text
    if raw is None:
        return {}, text[match.end():]
    if not isinstance(raw, dict):
        return None, text
    return raw, text[match.end():]


def _dump_frontmatter(meta: dict[str, object]) -> str:
    """按冻结字段序渲染 frontmatter 块（含首尾 ``---`` 围栏与结尾换行）。"""
    ordered: dict[str, object] = {}
    for key in FRONTMATTER_FIELDS:
        value = meta.get(key)
        if value in (None, "", []):
            continue
        ordered[key] = value
    body = yaml.safe_dump(
        ordered,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=4096,
    ).rstrip("\n")
    return f"---\n{body}\n---\n"


def _parse_prose(text: str) -> dict[str, str]:
    """散文节解析（宽容）：``# 标签`` + 正文 + Why/适用条件/来源/更新时间。"""
    lines = text.splitlines()
    label = ""
    head = 0
    while head < len(lines) and not lines[head].strip():
        head += 1
    if head < len(lines) and lines[head].startswith("# "):
        label = lines[head][2:].strip()
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in lines[1:]:
        stripped = line.strip()
        if stripped in {"**Why**", "**适用条件**", "**来源**"}:
            if current:
                sections[current] = "\n".join(buffer).strip()
            current = stripped.strip("*")
            buffer = []
        elif stripped.startswith("**更新时间**"):
            if current:
                sections[current] = "\n".join(buffer).strip()
            sections["更新时间"] = stripped.replace("**更新时间**", "").strip()
            current = None
            buffer = []
        elif line.startswith("# ") and not label:
            label = line[2:].strip()
        elif current:
            buffer.append(line)
    if current:
        sections[current] = "\n".join(buffer).strip()
    body = text
    for marker in ("**Why**", "**适用条件**", "**来源**", "**更新时间**"):
        body = body.split(marker)[0]
    body_lines = body.splitlines()
    while body_lines and not body_lines[0].strip():
        body_lines = body_lines[1:]
    if body_lines and body_lines[0].startswith("# "):
        body_lines = body_lines[1:]
    return {
        "label": label,
        "body": "\n".join(body_lines).strip(),
        "why": sections.get("Why", ""),
        "applicability": sections.get("适用条件", ""),
        "sources_text": sections.get("来源", ""),
        "updated_text": sections.get("更新时间", ""),
    }


class MemoryStore:
    """用户 memory/ 目录的读写门面；所有路径经 user_id 校验防穿越。"""

    @staticmethod
    def memory_root(user_id: str | int) -> Path:
        return get_user_root(user_id) / "memory"

    @classmethod
    def ensure_layout(cls, user_id: str | int) -> Path:
        root = cls.memory_root(user_id)
        root.mkdir(parents=True, exist_ok=True)
        for memory_type in MEMORY_TYPES:
            (root / memory_type).mkdir(exist_ok=True)
        (root / JOURNAL_DIR).mkdir(exist_ok=True)
        index = root / "MEMORY.md"
        if not index.is_file():
            _atomic_write(index, _INDEX_HEADER + cls._render_index([]))
        return root

    # ----- 索引 -----

    @staticmethod
    def index_path(user_id: str | int) -> Path:
        return MemoryStore.memory_root(user_id) / "MEMORY.md"

    @classmethod
    def _render_index(cls, entries: list[IndexEntry]) -> str:
        lines: list[str] = []
        for memory_type in MEMORY_TYPES:
            lines.append(f"## {TYPE_LABELS[memory_type]}")
            grouped = [e for e in entries if e.memory_type == memory_type]
            for entry in grouped:
                lines.append(
                    f"- [{entry.label}] {entry.description} → {entry.rel_path}"
                )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    @classmethod
    def read_index(cls, user_id: str | int, *, max_lines: int = 200, max_bytes: int = 25_600) -> IndexState:
        cls.ensure_layout(user_id)
        path = cls.index_path(user_id)
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        entries: list[IndexEntry] = []
        corrupt = 0
        seen: set[str] = set()
        for line in text.splitlines():
            if not line.startswith("- "):
                continue
            match = _INDEX_LINE_RE.match(line)
            if not match:
                corrupt += 1
                continue
            key = f"{match['type']}/{match['slug']}"
            if key in seen:
                corrupt += 1
                continue
            seen.add(key)
            entries.append(
                IndexEntry(
                    memory_type=match["type"],
                    slug=match["slug"],
                    label=match["label"],
                    description=match["desc"],
                )
            )
        return IndexState(
            entries=entries,
            corrupt_lines=corrupt,
            over_budget=len(text.splitlines()) > max_lines
            or len(text.encode("utf-8")) > max_bytes,
        )

    @classmethod
    def write_index(cls, user_id: str | int, entries: list[IndexEntry]) -> None:
        """整索引重写：排序确定性（类型顺序 + slug）。"""
        cls.ensure_layout(user_id)
        ordered = sorted(
            entries, key=lambda e: (MEMORY_TYPES.index(e.memory_type), e.slug)
        )
        _atomic_write(cls.index_path(user_id), _INDEX_HEADER + cls._render_index(ordered))

    @classmethod
    def rebuild_index(cls, user_id: str | int) -> IndexState:
        """索引损坏/缺失时从条目目录重建（frontmatter label/description 机械投影）。"""
        cls.ensure_layout(user_id)
        entries: list[IndexEntry] = []
        for memory_type in MEMORY_TYPES:
            for path in sorted((cls.memory_root(user_id) / memory_type).glob("*.md")):
                if not _SLUG_RE.match(path.stem):
                    continue
                front = cls.read_entry_file(path)
                entries.append(
                    IndexEntry(
                        memory_type=memory_type,
                        slug=path.stem,
                        label=str(front.get("label") or path.stem),
                        description=str(front.get("description") or ""),
                    )
                )
        cls.write_index(user_id, entries)
        return cls.read_index(user_id)

    # ----- 条目文件 -----

    @classmethod
    def entry_path(cls, user_id: str | int, memory_type: str, slug: str) -> Path:
        validate_memory_type(memory_type)
        if not _SLUG_RE.match(slug or ""):
            raise ValueError(f"非法 slug: {slug!r}，仅允许 [A-Za-z0-9_-]")
        return cls.memory_root(user_id) / memory_type / f"{slug}.md"

    @staticmethod
    def _render_entry(
        *,
        memory_type: str,
        label: str,
        description: str,
        body: str,
        why: str,
        applicability: str,
        tags: list[str],
        created: str,
        updated: str,
        sources: list[str],
    ) -> str:
        front = _dump_frontmatter(
            {
                "type": memory_type,
                "label": label,
                "description": description,
                "tags": tags,
                "created": created,
                "updated": updated,
                "sources": sources,
            }
        )
        lines = [f"# {label}", "", body.strip()]
        if why.strip():
            lines += ["", "**Why**", why.strip()]
        if applicability.strip():
            lines += ["", "**适用条件**", applicability.strip()]
        return front + "\n" + "\n".join(lines) + "\n"

    @staticmethod
    def read_entry_file(path: Path) -> dict[str, object]:
        """解析条目文件为语义字段；frontmatter 权威、散文 fallback（宽容解析）。

        type 取自所在目录（frontmatter 与目录不一致是治理信号，由整理任务归位）；
        description 无 frontmatter 时回退正文截断。
        """
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        directory_type = path.parent.name
        meta, prose_text = _split_frontmatter(text)
        if meta is not None:
            prose = _parse_prose(prose_text)
            return {
                "memory_type": directory_type,
                "label": _meta_str(meta, "label") or prose["label"],
                "description": _meta_str(meta, "description"),
                "tags": _meta_str_list(meta, "tags"),
                "created": _meta_str(meta, "created"),
                "updated_at": _meta_str(meta, "updated"),
                "body": prose["body"],
                "why": prose["why"],
                "applicability": prose["applicability"],
                "sources": _meta_str_list(meta, "sources"),
            }
        prose = _parse_prose(text)
        sources = [
            line.lstrip("- ").strip()
            for line in prose["sources_text"].splitlines()
            if line.strip().startswith("-")
        ]
        description = " ".join(prose["body"].split())[:_DESCRIPTION_FALLBACK_CHARS]
        return {
            "memory_type": directory_type,
            "label": prose["label"],
            "description": description,
            "tags": [],
            "created": "",
            "updated_at": prose["updated_text"],
            "body": prose["body"],
            "why": prose["why"],
            "applicability": prose["applicability"],
            "sources": sources,
        }

    @classmethod
    def read_entry(cls, user_id: str | int, memory_type: str, slug: str) -> dict[str, object] | None:
        path = cls.entry_path(user_id, memory_type, slug)
        if not path.is_file():
            return None
        return cls.read_entry_file(path)

    @classmethod
    def unique_slug(cls, user_id: str | int, memory_type: str, base: str) -> str:
        """slug 撞名追加序号（spec: 撞名追加序号）；中文标签回退为短哈希。"""
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", base.strip().lower()).strip("-")[:48]
        if not slug or not slug[0].isalpha():
            digest = hashlib.sha256(base.strip().encode("utf-8")).hexdigest()[:8]
            slug = f"m-{digest}"
        candidate = slug
        serial = 2
        while cls.entry_path(user_id, memory_type, candidate).exists():
            candidate = f"{slug}-{serial}"
            serial += 1
        return candidate

    @classmethod
    def upsert_entry(
        cls,
        user_id: str | int,
        *,
        memory_type: str,
        label: str,
        body: str,
        why: str = "",
        applicability: str = "",
        description: str = "",
        tags: list[str] | None = None,
        created: str = "",
        sources: list[str] | None = None,
        slug: str | None = None,
        max_entry_chars: int = 4000,
    ) -> IndexEntry:
        """新建或更新条目并同步索引行（索引行 = frontmatter 的投影）。

        更新路径基于写入前重读的当前文件内容合并：追加来源、维护 updated、
        保留用户手工字段；description 未提供新值时保留既有值（防清空权威
        字段，存量条目回退正文截断）。frontmatter 与索引行在同一次调用中
        生成，不存在只写其一的中间态。
        """
        validate_memory_type(memory_type)
        cls.ensure_layout(user_id)
        label = label.strip()[:80]
        body = body.strip()[:max_entry_chars]
        description = description.strip()[:_DESCRIPTION_MAX_CHARS]
        sources = [s.strip() for s in (sources or []) if s.strip()]

        target_slug = slug
        existing: dict[str, object] | None = None
        if target_slug:
            existing = cls.read_entry(user_id, memory_type, target_slug)
        if existing is None:
            # 轻量合并：同类型同名（label）视为同一记忆，更新而非新建
            target_slug = cls.slug_of(user_id, memory_type, label)
            if target_slug:
                existing = cls.read_entry(user_id, memory_type, target_slug)
        if target_slug is None:
            target_slug = cls.unique_slug(user_id, memory_type, slug or label)

        today = _now_date()
        if existing:
            merged_sources = list(
                dict.fromkeys([*existing.get("sources", []), *sources])
            )
            merged_why = why or str(existing.get("why") or "")
            merged_applicability = applicability or str(
                existing.get("applicability") or ""
            )
            merged_description = description or str(existing.get("description") or "")
            merged_tags = list(tags or []) or list(existing.get("tags") or [])
            # 存量散文条目无 created：以散文更新时间近似首次沉淀日期
            merged_created = (
                created
                or str(existing.get("created") or "")
                or str(existing.get("updated_at") or "")
                or today
            )
        else:
            merged_sources = sources
            merged_why = why
            merged_applicability = applicability
            merged_description = description
            merged_tags = list(tags or [])
            merged_created = created or today
        if not merged_description:
            merged_description = " ".join(body.split())[:_DESCRIPTION_FALLBACK_CHARS]

        content = cls._render_entry(
            memory_type=memory_type,
            label=label,
            description=merged_description,
            body=body,
            why=str(merged_why or ""),
            applicability=str(merged_applicability or ""),
            tags=merged_tags,
            created=merged_created,
            updated=today,
            sources=merged_sources,
        )
        _atomic_write(cls.entry_path(user_id, memory_type, target_slug), content)

        entry = IndexEntry(
            memory_type=memory_type,
            slug=target_slug,
            label=label,
            description=merged_description,
        )
        cls._sync_index_line(user_id, entry)
        return entry

    @classmethod
    def slug_of(cls, user_id: str | int, memory_type: str, label: str) -> str | None:
        for entry in cls.read_index(user_id).by_type(memory_type):
            if entry.label == label:
                return entry.slug
        return None

    @classmethod
    def sync_index_line(cls, user_id: str | int, entry: IndexEntry) -> None:
        """公开入口：条目文件变更后维护索引行一致性。"""
        cls._sync_index_line(user_id, entry)

    @classmethod
    def _sync_index_line(cls, user_id: str | int, entry: IndexEntry) -> None:
        """索引行一致性：同一 type/slug 只保留最新一行。"""
        state = cls.read_index(user_id)
        entries = [
            e for e in state.entries if not (e.memory_type == entry.memory_type and e.slug == entry.slug)
        ]
        entries.append(entry)
        cls.write_index(user_id, entries)

    @classmethod
    def align_frontmatter_type(cls, user_id: str | int, memory_type: str, slug: str) -> str | None:
        """frontmatter type 与所在目录不一致时以目录为准改写（不移动文件）。

        返回改写前的 frontmatter type；无 frontmatter、一致或 YAML 损坏
        返回 None（损坏走散文容错，不在此修复）。
        """
        path = cls.entry_path(user_id, memory_type, slug)
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8")
        meta, prose = _split_frontmatter(text)
        if meta is None:
            return None
        current = _meta_str(meta, "type")
        if not current or current == memory_type:
            return None
        meta["type"] = memory_type
        _atomic_write(path, _dump_frontmatter(meta) + prose)
        return current

    @classmethod
    def remove_entry(cls, user_id: str | int, memory_type: str, slug: str) -> bool:
        """淘汰：删条目文件与索引行（journal 永在，不在此处理）。"""
        path = cls.entry_path(user_id, memory_type, slug)
        removed = path.is_file()
        if removed:
            path.unlink()
        state = cls.read_index(user_id)
        entries = [
            e for e in state.entries if not (e.memory_type == memory_type and e.slug == slug)
        ]
        cls.write_index(user_id, entries)
        return removed

    # ----- journal -----

    @classmethod
    def journal_path(cls, user_id: str | int, day: str | None = None) -> Path:
        return cls.memory_root(user_id) / JOURNAL_DIR / f"{day or _now_date()}.md"

    @classmethod
    def append_journal(
        cls,
        user_id: str | int,
        *,
        session_id: str | None,
        text: str,
        day: str | None = None,
        label: str = "",
    ) -> Path:
        """情景日志只追加（spec: journal 只追加永不改写）。

        label 为块头标记后缀（如「抽取决策」「整理快照 · 原条目 …」）；
        session_id 为空时不带会话段（整理任务不隶属于任何会话）。
        """
        cls.ensure_layout(user_id)
        path = cls.journal_path(user_id, day)
        stamp = datetime.now().astimezone().strftime("%H:%M")
        header = f"## {stamp}"
        if session_id:
            header += f" · 会话 {str(session_id)[:8]}"
        if label:
            header += f"（{label}）"
        block = f"\n{header}\n\n{text.strip()}\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(block)
        return path

    # ----- 检索（grep） -----

    @classmethod
    def search(
        cls,
        user_id: str | int,
        query: str,
        *,
        memory_types: tuple[str, ...] = (),
        limit: int = 5,
    ) -> list[dict[str, object]]:
        """关键词字面匹配（grep 语义）：索引行 + 条目/journal 原文命中。"""
        query = query.strip()
        if not query:
            return []
        types = memory_types or MEMORY_TYPES
        for memory_type in types:
            validate_memory_type(memory_type)
        results: list[dict[str, object]] = []
        root = cls.memory_root(user_id)
        keywords = [token for token in re.split(r"\s+", query) if token]
        for memory_type in types:
            for path in sorted((root / memory_type).glob("*.md")):
                text = path.read_text(encoding="utf-8") if path.is_file() else ""
                if not text:
                    continue
                if any(keyword.casefold() in text.casefold() for keyword in keywords):
                    results.append(
                        {
                            "memory_type": memory_type,
                            "slug": path.stem,
                            "rel_path": f"{memory_type}/{path.stem}.md",
                            "content": text,
                        }
                    )
                    if len(results) >= limit:
                        return results
        return results


def today_str() -> str:
    return date.today().isoformat()


__all__ = [
    "FRONTMATTER_FIELDS",
    "IndexEntry",
    "IndexState",
    "MemoryStore",
    "today_str",
]
