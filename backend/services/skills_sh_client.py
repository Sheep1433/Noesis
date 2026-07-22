"""
skills.sh 市场客户端：搜索发现 + 从 GitHub 拉取 skill 包。
"""
from __future__ import annotations

import io
import json
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal
from urllib.parse import quote, urlparse

import httpx

from common.logging import logger
from config.env import SkillsMarketConfig
from exceptions.exception import NotFoundException, ServiceException, ServiceWarning

_SOURCE_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ALLOWED_HOSTS = frozenset(
    {
        "skills.sh",
        "www.skills.sh",
        "github.com",
        "codeload.github.com",
        "raw.githubusercontent.com",
        "api.github.com",
    },
)

_SKILL_MD_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.S)

_SKILLS_SH_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
_HTTP_RETRIES = 3
_HTTP_RETRY_BASE_SLEEP = 0.5

_search_cache: dict[str, tuple[float, list["SkillsShSearchHit"]]] = {}
# sort → (monotonic_ts, hits)
_leaderboard_cache: dict[str, tuple[float, list["SkillsShSearchHit"]]] = {}
# source/skill_id → (monotonic_ts, preview)
_preview_cache: dict[str, tuple[float, "SkillsShPreview"]] = {}

LeaderboardSort = Literal["all_time", "trending"]

_LEADERBOARD_PATHS: dict[str, str] = {
    "all_time": "/",
    "trending": "/trending",
}

# 行：href + h3 名称 + Installs 列（最后一个 text-foreground mono）
_LEADERBOARD_ROW_RE = re.compile(
    r'href="/(?P<owner>[^"/]+)/(?P<repo>[^"/]+)/(?P<skill>[^"/]+)"[^>]*>'
    r"[\s\S]*?<h3[^>]*>(?P<name>[^<]+)</h3>"
    r'[\s\S]*?<span class="font-mono text-sm text-foreground">(?P<installs>[^<]+)</span>',
)


def parse_installs_text(raw: str) -> int:
    """把 skills.sh 展示值（如 2.6M / 687.4K）转为整数。"""
    s = (raw or "").strip().upper().replace(",", "")
    m = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*([KMB])?", s)
    if not m:
        try:
            return max(0, int(float(s)))
        except ValueError:
            return 0
    n = float(m.group(1))
    suf = m.group(2)
    if suf == "K":
        n *= 1_000
    elif suf == "M":
        n *= 1_000_000
    elif suf == "B":
        n *= 1_000_000_000
    return int(n)


@dataclass(frozen=True)
class SkillsShSearchHit:
    id: str
    skill_id: str
    name: str
    source: str
    installs: int


@dataclass(frozen=True)
class SkillsShBundle:
    source: str
    skill_id: str
    skill_dir: Path
    skill_md_relpath: str
    cleanup_root: Path


@dataclass(frozen=True)
class SkillsShPreview:
    skill_md: str
    skill_md_relpath: str
    skill_dir_relpath: str
    tree: list
    display_name: str | None = None


def validate_source(source: str) -> str:
    s = (source or "").strip().strip("/")
    if not _SOURCE_RE.fullmatch(s):
        raise ServiceException(message="非法 source，应为 owner/repo")
    return s


def validate_skill_id(skill_id: str) -> str:
    s = (skill_id or "").strip()
    if not _SKILL_ID_RE.fullmatch(s):
        raise ServiceException(message="非法 skill_id")
    return s


def market_url_for(skill_full_id: str, base_url: str | None = None) -> str:
    base = (base_url or SkillsMarketConfig.base_url).rstrip("/")
    rel = (skill_full_id or "").strip().strip("/")
    return f"{base}/{rel}" if rel else base


class SkillsShClient:
    """skills.sh Search API + Leaderboard + Download API 详情 + GitHub archive 安装。"""

    @classmethod
    def _stale_cache_max_seconds(cls) -> int:
        return max(
            SkillsMarketConfig.cache_ttl_seconds,
            SkillsMarketConfig.preview_cache_ttl_seconds,
        )

    @classmethod
    def _cache_fresh(cls, entry: tuple[float, object] | None, ttl: int) -> object | None:
        if not entry or ttl <= 0:
            return None
        ts, value = entry
        if time.monotonic() - ts < ttl:
            return value
        return None

    @classmethod
    def _cache_stale(cls, entry: tuple[float, object] | None) -> object | None:
        if not entry:
            return None
        ts, value = entry
        if time.monotonic() - ts < cls._stale_cache_max_seconds():
            return value
        return None

    @classmethod
    def _request_skills_sh(
        cls,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        accept: str = "application/json",
    ) -> httpx.Response:
        cls._assert_allowed_url(url)
        timeout = float(SkillsMarketConfig.search_timeout_seconds)
        headers = {**_SKILLS_SH_REQUEST_HEADERS, "Accept": accept}
        last_error: Exception | None = None
        for attempt in range(_HTTP_RETRIES):
            try:
                with httpx.Client(
                    timeout=timeout,
                    follow_redirects=True,
                    http2=False,
                ) as client:
                    return client.request(method, url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning(
                    f"skills.sh HTTP error {method} {url}"
                    f" attempt={attempt + 1}/{_HTTP_RETRIES}: {exc}",
                )
                if attempt + 1 < _HTTP_RETRIES:
                    time.sleep(_HTTP_RETRY_BASE_SLEEP * (2 ** attempt))
        assert last_error is not None
        raise last_error

    @classmethod
    def fetch_leaderboard(
        cls,
        sort: LeaderboardSort = "all_time",
        *,
        limit: int = 40,
    ) -> list[SkillsShSearchHit]:
        """抓取 skills.sh Leaderboard 页面。"""
        if sort not in _LEADERBOARD_PATHS:
            raise ServiceException(message="非法排序，仅支持 all_time / trending")
        limit = max(1, min(int(limit), 100))
        ttl = SkillsMarketConfig.cache_ttl_seconds
        now = time.monotonic()
        cached = _leaderboard_cache.get(sort)
        fresh = cls._cache_fresh(cached, ttl)
        if fresh is not None:
            return list(fresh)[:limit]  # type: ignore[arg-type]

        base = SkillsMarketConfig.base_url.rstrip("/")
        path = _LEADERBOARD_PATHS[sort]
        url = f"{base}{path}" if path != "/" else f"{base}/"
        label = "All Time" if sort == "all_time" else "Trending"
        try:
            resp = cls._request_skills_sh(
                "GET", url, accept="text/html,application/xhtml+xml",
            )
            cls._raise_for_status(resp, f"skills.sh {label} 榜加载失败")
            html = resp.text
            hits = cls._parse_leaderboard_html(html)
            if not hits:
                raise ServiceException(message=f"未能解析 skills.sh {label} 榜")
            if ttl > 0:
                _leaderboard_cache[sort] = (now, hits)
            return list(hits[:limit])
        except (httpx.HTTPError, ServiceException) as exc:
            stale = cls._cache_stale(cached)
            if stale is not None:
                logger.warning(
                    f"skills.sh leaderboard fetch failed sort={sort}, using stale cache: {exc}",
                )
                return list(stale)[:limit]  # type: ignore[arg-type]
            if isinstance(exc, httpx.HTTPError):
                raise ServiceException(message=f"skills.sh {label} 榜加载失败: {exc}") from exc
            raise

    @classmethod
    def fetch_trending(cls, *, limit: int = 40) -> list[SkillsShSearchHit]:
        """兼容旧调用：等价于 fetch_leaderboard(trending)。"""
        return cls.fetch_leaderboard("trending", limit=limit)

    @classmethod
    def _parse_leaderboard_html(cls, html: str) -> list[SkillsShSearchHit]:
        hits: list[SkillsShSearchHit] = []
        seen: set[str] = set()
        for m in _LEADERBOARD_ROW_RE.finditer(html or ""):
            owner = m.group("owner")
            repo = m.group("repo")
            skill = m.group("skill")
            if owner == "site":
                continue
            source = f"{owner}/{repo}"
            try:
                source = validate_source(source)
                skill = validate_skill_id(skill)
            except ServiceException:
                continue
            full_id = f"{source}/{skill}"
            if full_id in seen:
                continue
            seen.add(full_id)
            name = (m.group("name") or skill).strip() or skill
            hits.append(
                SkillsShSearchHit(
                    id=full_id,
                    skill_id=skill,
                    name=name,
                    source=source,
                    installs=parse_installs_text(m.group("installs")),
                ),
            )
        return hits

    @classmethod
    def search(cls, query: str, *, limit: int = 20) -> list[SkillsShSearchHit]:
        q = (query or "").strip()
        if len(q) < 2:
            raise ServiceException(message="搜索词至少 2 个字符")
        limit = max(1, min(int(limit), 50))
        cache_key = f"{q}|{limit}"
        ttl = SkillsMarketConfig.cache_ttl_seconds
        now = time.monotonic()
        cached = _search_cache.get(cache_key)
        fresh = cls._cache_fresh(cached, ttl)
        if fresh is not None:
            return list(fresh)  # type: ignore[arg-type]

        base = SkillsMarketConfig.base_url.rstrip("/")
        url = f"{base}/api/search"
        try:
            resp = cls._request_skills_sh(
                "GET", url, params={"q": q, "limit": str(limit)},
            )
            cls._raise_for_status(resp, "skills.sh 搜索失败")
            data = resp.json()
            hits = cls._parse_search_payload(data)
            if ttl > 0:
                _search_cache[cache_key] = (now, hits)
            return hits
        except (httpx.HTTPError, ServiceException) as exc:
            stale = cls._cache_stale(cached)
            if stale is not None:
                logger.warning(
                    f"skills.sh search failed q={q!r}, using stale cache: {exc}",
                )
                return list(stale)  # type: ignore[arg-type]
            if isinstance(exc, httpx.HTTPError):
                raise ServiceException(message=f"skills.sh 搜索失败: {exc}") from exc
            raise

    @classmethod
    def _parse_search_payload(cls, data: object) -> list[SkillsShSearchHit]:
        if not isinstance(data, dict):
            return []
        raw_skills = data.get("skills") or []
        if not isinstance(raw_skills, list):
            return []
        hits: list[SkillsShSearchHit] = []
        for item in raw_skills:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "").strip()
            skill_id = str(item.get("skillId") or item.get("name") or "").strip()
            full_id = str(item.get("id") or "").strip()
            if not full_id and source and skill_id:
                full_id = f"{source}/{skill_id}"
            if not source or not skill_id:
                continue
            try:
                source = validate_source(source)
                skill_id = validate_skill_id(skill_id)
            except ServiceException:
                continue
            installs = item.get("installs") or 0
            try:
                installs_i = int(installs)
            except (TypeError, ValueError):
                installs_i = 0
            hits.append(
                SkillsShSearchHit(
                    id=full_id or f"{source}/{skill_id}",
                    skill_id=skill_id,
                    name=str(item.get("name") or skill_id),
                    source=source,
                    installs=max(0, installs_i),
                ),
            )
        hits.sort(key=lambda h: h.installs, reverse=True)
        return hits

    @classmethod
    def resolve_featured(
        cls,
        featured: Iterable[tuple[str, str, str]],
    ) -> list[SkillsShSearchHit]:
        """用 search 补 installs（并行）；失败则退回静态条目。"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        featured_list = list(featured)
        if not featured_list:
            return []

        def enrich_one(entry: tuple[int, tuple[str, str, str]]) -> tuple[int, SkillsShSearchHit]:
            idx, (full_id, source, skill_id) = entry
            try:
                source = validate_source(source)
                skill_id = validate_skill_id(skill_id)
            except ServiceException:
                return idx, SkillsShSearchHit(
                    id=full_id or f"{source}/{skill_id}",
                    skill_id=skill_id or "unknown",
                    name=skill_id or "unknown",
                    source=source or "unknown/unknown",
                    installs=0,
                )
            exact: SkillsShSearchHit | None = None
            soft: SkillsShSearchHit | None = None
            try:
                for candidate in cls.search(skill_id, limit=10):
                    if candidate.source == source and candidate.skill_id == skill_id:
                        exact = candidate
                        break
                    if soft is None and candidate.skill_id == skill_id:
                        soft = candidate
            except ServiceException as exc:
                logger.info(f"featured enrich skipped for {source}/{skill_id}: {exc}")
            hit = exact or soft
            if hit is None:
                hit = SkillsShSearchHit(
                    id=full_id or f"{source}/{skill_id}",
                    skill_id=skill_id,
                    name=skill_id,
                    source=source,
                    installs=0,
                )
            else:
                # 保持配置里的 source / id，仅借用 installs / name
                hit = SkillsShSearchHit(
                    id=full_id or f"{source}/{skill_id}",
                    skill_id=skill_id,
                    name=hit.name or skill_id,
                    source=source,
                    installs=hit.installs,
                )
            return idx, hit

        indexed = list(enumerate(featured_list))
        results: list[SkillsShSearchHit | None] = [None] * len(featured_list)
        workers = min(8, max(1, len(featured_list)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(enrich_one, item) for item in indexed]
            for fut in as_completed(futures):
                idx, hit = fut.result()
                results[idx] = hit
        return [h for h in results if h is not None]

    @classmethod
    def download_skill_bundle(cls, source: str, skill_id: str) -> SkillsShBundle:
        source = validate_source(source)
        skill_id = validate_skill_id(skill_id)
        owner, repo = source.split("/", 1)
        archive_bytes, ref = cls._download_github_archive(owner, repo)
        return cls._extract_skill_from_archive(
            archive_bytes,
            source=source,
            skill_id=skill_id,
            ref=ref,
        )

    @classmethod
    def fetch_skill_preview(cls, source: str, skill_id: str) -> SkillsShPreview:
        """详情预览：skills.sh /api/download 取 SKILL.md 原文，安装仍走 GitHub。"""
        source = validate_source(source)
        skill_id = validate_skill_id(skill_id)
        cache_key = f"{source}/{skill_id}"
        ttl = SkillsMarketConfig.preview_cache_ttl_seconds
        now = time.monotonic()
        cached = _preview_cache.get(cache_key)
        fresh = cls._cache_fresh(cached, ttl)
        if fresh is not None:
            return fresh  # type: ignore[return-value]

        try:
            skill_md, skill_md_relpath, display_name = cls._fetch_skill_md_from_download(
                source, skill_id,
            )
        except (httpx.HTTPError, ServiceWarning, NotFoundException) as exc:
            stale = cls._cache_stale(cached)
            if stale is not None and not isinstance(exc, NotFoundException):
                logger.warning(
                    f"skills.sh preview failed {cache_key}, using stale cache: {exc}",
                )
                return stale  # type: ignore[return-value]
            raise
        if not skill_md.strip():
            raise ServiceWarning(message="skills.sh 未返回 SKILL.md 正文")
        preview = SkillsShPreview(
            skill_md=skill_md,
            skill_md_relpath=skill_md_relpath,
            skill_dir_relpath="",
            tree=[],
            display_name=display_name,
        )
        if ttl > 0:
            _preview_cache[cache_key] = (now, preview)
        return preview

    @classmethod
    def _download_api_url(cls, source: str, skill_id: str) -> str:
        base = SkillsMarketConfig.base_url.rstrip("/")
        return f"{base}/api/download/{source}/{skill_id}"

    @classmethod
    def _fetch_skill_md_from_download(
        cls, source: str, skill_id: str,
    ) -> tuple[str, str, str | None]:
        url = cls._download_api_url(source, skill_id)
        resp = cls._request_skills_sh("GET", url)
        if resp.status_code == 404:
            raise NotFoundException(message=f"skills.sh 未找到技能「{source}/{skill_id}」")
        cls._raise_for_status(resp, "skills.sh 下载接口失败")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ServiceWarning(message="skills.sh 下载接口返回非 JSON") from exc
        skill_md, skill_md_relpath = cls._pick_skill_md_from_download(data.get("files") or [])
        display_name = cls._parse_skill_md_display_name(skill_md) if skill_md else None
        return skill_md, skill_md_relpath, display_name

    @classmethod
    def _pick_skill_md_from_download(cls, files: Iterable) -> tuple[str, str]:
        candidates: list[tuple[str, str]] = []
        for item in files:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").replace("\\", "/").strip()
            if path != "SKILL.md" and not path.endswith("/SKILL.md"):
                continue
            contents = str(item.get("contents") or "")
            if contents.strip():
                candidates.append((path, contents))
        if not candidates:
            return "", "SKILL.md"
        path, contents = sorted(candidates, key=lambda pair: (pair[0].count("/"), len(pair[0])))[0]
        return contents, path if path else "SKILL.md"

    @classmethod
    def _parse_skill_md_display_name(cls, skill_md: str) -> str | None:
        match = _SKILL_MD_FRONTMATTER_RE.match(skill_md or "")
        if not match:
            return None
        for line in match.group(1).splitlines():
            stripped = line.strip()
            if stripped.startswith("name:"):
                name = stripped.split(":", 1)[1].strip().strip("'\"")
                return name or None
        return None

    @classmethod
    def fetch_skill_md(cls, source: str, skill_id: str) -> tuple[str, str]:
        """返回 (SKILL.md 正文, 仓内相对路径)。"""
        preview = cls.fetch_skill_preview(source, skill_id)
        return preview.skill_md, preview.skill_md_relpath

    @classmethod
    def cleanup_bundle(cls, bundle: SkillsShBundle) -> None:
        import shutil

        root = bundle.cleanup_root
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)

    @classmethod
    def _download_github_archive(cls, owner: str, repo: str) -> tuple[bytes, str]:
        timeout = float(SkillsMarketConfig.github_timeout_seconds)
        max_bytes = SkillsMarketConfig.max_archive_bytes
        last_error: Exception | None = None
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            for ref in ("main", "master"):
                url = (
                    f"https://codeload.github.com/{quote(owner)}/{quote(repo)}"
                    f"/zip/refs/heads/{quote(ref)}"
                )
                cls._assert_allowed_url(url)
                for attempt in range(2):
                    try:
                        with client.stream("GET", url) as resp:
                            if resp.status_code == 404:
                                break
                            cls._raise_for_status(resp, "GitHub 下载失败")
                            chunks: list[bytes] = []
                            total = 0
                            for chunk in resp.iter_bytes():
                                total += len(chunk)
                                if total > max_bytes:
                                    raise ServiceException(
                                        message=(
                                            f"仓库压缩包过大（>{max_bytes // (1024 * 1024)}MB）"
                                        ),
                                    )
                                chunks.append(chunk)
                            return b"".join(chunks), ref
                    except ServiceException:
                        raise
                    except httpx.HTTPError as exc:
                        last_error = exc
                        logger.warning(
                            f"GitHub archive download failed {owner}/{repo}@{ref}"
                            f" attempt={attempt + 1}: {exc}",
                        )
                        if attempt == 0:
                            time.sleep(0.5)
                            continue
        if last_error is not None:
            raise ServiceWarning(message=f"GitHub 下载失败，请稍后重试: {last_error}") from last_error
        raise NotFoundException(message=f"未找到仓库 {owner}/{repo}")

    @classmethod
    def _extract_skill_from_archive(
        cls,
        archive_bytes: bytes,
        *,
        source: str,
        skill_id: str,
        ref: str,
    ) -> SkillsShBundle:
        import tempfile
        import shutil

        tmp_root = Path(tempfile.mkdtemp(prefix="skills-sh-"))
        try:
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
                for info in zf.infolist():
                    name = info.filename.replace("\\", "/")
                    if name.startswith("/") or ".." in name.split("/"):
                        raise ServiceException(message="GitHub 压缩包含非法路径")
                zf.extractall(tmp_root)
            top_dirs = [p for p in tmp_root.iterdir() if p.is_dir()]
            if len(top_dirs) != 1:
                raise ServiceException(message="无法解析 GitHub 压缩包结构")
            repo_root = top_dirs[0]
            skill_dir = cls.find_skill_dir(repo_root, skill_id)
            if skill_dir is None:
                raise NotFoundException(
                    message=f"仓库 {source} 中未找到技能「{skill_id}」",
                )
            rel = skill_dir.relative_to(repo_root).as_posix()
            skill_md_rel = f"{rel}/SKILL.md" if rel != "." else "SKILL.md"
            logger.info(
                f"skills.sh bundle ready: {source}/{skill_id} ref={ref} path={skill_md_rel}",
            )
            return SkillsShBundle(
                source=source,
                skill_id=skill_id,
                skill_dir=skill_dir,
                skill_md_relpath=skill_md_rel,
                cleanup_root=tmp_root,
            )
        except Exception:
            shutil.rmtree(tmp_root, ignore_errors=True)
            raise

    @classmethod
    def find_skill_dir(cls, repo_root: Path, skill_id: str) -> Path | None:
        candidates = [
            repo_root / "skills" / skill_id,
            repo_root / skill_id,
            repo_root,
        ]
        for path in candidates:
            if (path / "SKILL.md").is_file():
                return path

        skills_root = repo_root / "skills"
        if skills_root.is_dir():
            for child in skills_root.iterdir():
                if not child.is_dir():
                    continue
                if child.name == skill_id and (child / "SKILL.md").is_file():
                    return child
                nested = child / skill_id
                if (nested / "SKILL.md").is_file():
                    return nested

        matches: list[Path] = []
        skip_dirs = {".git", "node_modules", "__pycache__", ".github"}
        for skill_md in repo_root.rglob("SKILL.md"):
            parent = skill_md.parent
            if parent.name != skill_id:
                continue
            if any(part in skip_dirs for part in parent.relative_to(repo_root).parts):
                continue
            matches.append(parent)
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]

        def rank(path: Path) -> tuple[int, int, str]:
            rel = path.relative_to(repo_root).as_posix()
            under_skills = rel.startswith("skills/") or "/skills/" in rel
            return (len(rel.split("/")), 0 if under_skills else 1, rel)

        return sorted(matches, key=rank)[0]

    @classmethod
    def _assert_allowed_url(cls, url: str) -> None:
        host = (urlparse(url).hostname or "").lower()
        if host not in _ALLOWED_HOSTS:
            raise ServiceException(message=f"拒绝访问非白名单主机: {host}")

    @classmethod
    def _raise_for_status(cls, resp: httpx.Response, prefix: str) -> None:
        if resp.status_code == 429:
            retry = resp.headers.get("Retry-After", "")
            raise ServiceException(
                message=f"{prefix}：请求过于频繁" + (f"（Retry-After={retry}）" if retry else ""),
            )
        if resp.is_error:
            raise ServiceException(message=f"{prefix}（HTTP {resp.status_code}）")
