"""md 文件记忆层文件服务：索引、条目、journal 的唯一读写通道。

设计要点（openspec: md-memory-layer design §2）：
- ``MEMORY.md`` 索引：五类分组、一行一条（``[标签] 一句话 → type/slug.md``），
  行数 + 字节双保险预算；损坏行跳过，可从条目目录重建。
- 条目文件：一条一文件，含正文、Why、适用条件、来源（可多条）、更新时间。
- journal：按日情景日志，只追加，永不改写。
- 所有写入原子（tmp + os.replace），写入前重读（不基于陈旧内存覆盖）。
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from noesis.config.user_data_paths import get_user_root
from noesis.services.memory.types import MEMORY_TYPES, TYPE_LABELS, validate_memory_type

_INDEX_HEADER = "<!-- Noesis 记忆索引：引擎维护，可手动编辑；一行一条 -->\n\n"
_INDEX_LINE_RE = re.compile(
    r"^-\s*\[(?P<label>[^\]]+)\]\s*(?P<desc>.*?)\s*→\s*(?P<type>preference|goal|decision|experience|gotcha)/(?P<slug>[A-Za-z0-9_-]+)\.md\s*$"
)
_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")

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
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


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
        """索引损坏/缺失时从条目目录重建（spec: 索引可从条目目录重建）。"""
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
                        label=front.get("label") or path.stem,
                        description=front.get("description") or "",
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
        label: str,
        body: str,
        why: str,
        applicability: str,
        sources: list[str],
        updated_at: str,
    ) -> str:
        lines = [f"# {label}", "", body.strip()]
        if why.strip():
            lines += ["", "**Why**", why.strip()]
        if applicability.strip():
            lines += ["", "**适用条件**", applicability.strip()]
        lines += ["", "**来源**"]
        lines += [f"- {source}" for source in sources] or ["-（无）"]
        lines += ["", f"**更新时间** {updated_at}", ""]
        return "\n".join(lines)

    @staticmethod
    def read_entry_file(path: Path) -> dict[str, object]:
        """解析条目文件为语义字段；供索引重建与合并使用（宽容解析）。"""
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        label = ""
        if text.startswith("# "):
            label = text.splitlines()[0][2:].strip()
        sections: dict[str, str] = {}
        current: str | None = None
        buffer: list[str] = []
        for line in text.splitlines()[1:]:
            if line.strip() in {"**Why**", "**适用条件**", "**来源**"} or line.startswith("**更新时间**"):
                if current:
                    sections[current] = "\n".join(buffer).strip()
                current = line.strip().strip("*")
                if current.startswith("更新时间"):
                    sections["更新时间"] = line.replace("**更新时间**", "").strip()
                    current = None
                buffer = []
            elif line.startswith("# ") and not label:
                label = line[2:].strip()
            elif current:
                buffer.append(line)
        if current:
            sections[current] = "\n".join(buffer).strip()
        body = text.split("**Why**")[0].split("**适用条件**")[0].split("**来源**")[0]
        body = "\n".join(body.splitlines()[1:]).strip()
        sources = [
            line.lstrip("- ").strip()
            for line in sections.get("来源", "").splitlines()
            if line.strip().startswith("-")
        ]
        description = " ".join(body.split())[:60]
        return {
            "label": label,
            "body": body,
            "why": sections.get("Why", ""),
            "applicability": sections.get("适用条件", ""),
            "sources": sources,
            "updated_at": sections.get("更新时间", ""),
            "description": description,
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
        sources: list[str] | None = None,
        slug: str | None = None,
        max_entry_chars: int = 4000,
    ) -> IndexEntry:
        """新建或更新条目并同步索引行。

        更新路径基于写入前重读的当前文件内容合并（追加来源、保留用户
        手工补充的字段），再整体原子写回；不存在则按唯一 slug 新建。
        """
        validate_memory_type(memory_type)
        cls.ensure_layout(user_id)
        label = label.strip()[:80]
        body = body.strip()[:max_entry_chars]
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

        if existing:
            merged_sources = list(
                dict.fromkeys([*existing.get("sources", []), *sources])
            )
            merged_why = why or str(existing.get("why") or "")
            merged_applicability = applicability or str(
                existing.get("applicability") or ""
            )
        else:
            merged_sources = sources
            merged_why = why
            merged_applicability = applicability

        content = cls._render_entry(
            label=label,
            body=body,
            why=str(merged_why or ""),
            applicability=str(merged_applicability or ""),
            sources=merged_sources or ["（无）"],
            updated_at=_now_date(),
        )
        _atomic_write(cls.entry_path(user_id, memory_type, target_slug), content)

        entry = IndexEntry(
            memory_type=memory_type,
            slug=target_slug,
            label=label,
            description=" ".join(body.split())[:60],
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
    def _sync_index_line(cls, user_id: str | int, entry: IndexEntry) -> None:
        """索引行一致性：同一 type/slug 只保留最新一行。"""
        state = cls.read_index(user_id)
        entries = [
            e for e in state.entries if not (e.memory_type == entry.memory_type and e.slug == entry.slug)
        ]
        entries.append(entry)
        cls.write_index(user_id, entries)

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
        cls, user_id: str | int, *, session_id: str, text: str, day: str | None = None
    ) -> Path:
        """情景日志只追加（spec: journal 只追加永不改写）。"""
        cls.ensure_layout(user_id)
        path = cls.journal_path(user_id, day)
        stamp = datetime.now(timezone.utc).strftime("%H:%M")
        block = f"\n## {stamp} · 会话 {str(session_id)[:8]}\n\n{text.strip()}\n"
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


__all__ = ["IndexEntry", "IndexState", "MemoryStore", "today_str"]
