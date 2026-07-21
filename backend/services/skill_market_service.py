"""
Skills 市场业务：browse / search / detail / install（skills.sh）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

from concurrent.futures import ThreadPoolExecutor

from common.logging import logger
from config.env import SkillsMarketConfig
from config.user_data_paths import get_user_skills_dir
from exceptions.exception import ConflictException, ServiceException
from schemas.skill_vo import (
    SkillMarketDetailResponse,
    SkillMarketItem,
    SkillMarketListResponse,
)
from services.skill_fs_service import SkillFsService
from services.skills_sh_client import (
    LeaderboardSort,
    SkillsShClient,
    SkillsShSearchHit,
    market_url_for,
    validate_skill_id,
    validate_source,
)

InstallMatch = Literal["none", "exact", "name_conflict"]


class SkillMarketService:
    @classmethod
    def browse(
        cls,
        user_id: str | int,
        *,
        sort: LeaderboardSort = "trending",
        limit: int = 40,
    ) -> SkillMarketListResponse:
        """榜单浏览。"""
        hits = SkillsShClient.fetch_leaderboard(sort, limit=limit)
        items = [cls._to_item(h) for h in hits]
        cls._annotate_install_status(items, user_id)
        return SkillMarketListResponse(items=items, query="")

    @classmethod
    def search(
        cls, user_id: str | int, query: str, *, limit: int = 20,
    ) -> SkillMarketListResponse:
        hits = SkillsShClient.search(query, limit=limit)
        items = [cls._to_item(h) for h in hits]
        cls._annotate_install_status(items, user_id)
        return SkillMarketListResponse(
            items=items,
            query=(query or "").strip(),
        )

    @classmethod
    def detail(
        cls, user_id: str | int, source: str, skill_id: str,
    ) -> SkillMarketDetailResponse:
        source = validate_source(source)
        skill_id = validate_skill_id(skill_id)
        item = SkillMarketItem(
            id=f"{source}/{skill_id}",
            skill_id=skill_id,
            name=skill_id,
            source=source,
            installs=0,
            market_url=market_url_for(f"{source}/{skill_id}"),
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            preview_fut = pool.submit(SkillsShClient.fetch_skill_preview, source, skill_id)
            search_fut = pool.submit(SkillsShClient.search, skill_id, limit=10)
            preview = preview_fut.result()
            try:
                for hit in search_fut.result():
                    if hit.source == source and hit.skill_id == skill_id:
                        item = cls._to_item(hit)
                        break
            except ServiceException:
                pass
            if preview.display_name and item.name == skill_id:
                item = item.model_copy(update={"name": preview.display_name})
        cls._annotate_install_status([item], user_id)
        return SkillMarketDetailResponse(
            item=item,
            skill_md=preview.skill_md,
            skill_md_path=preview.skill_md_relpath,
        )

    @classmethod
    def install(
        cls,
        user_id: str | int,
        source: str,
        skill_id: str,
        *,
        overwrite: bool = False,
    ) -> str:
        source = validate_source(source)
        skill_id = validate_skill_id(skill_id)
        if SkillFsService.user_package_exists(skill_id, user_id) and not overwrite:
            raise ConflictException(message=f'技能「{skill_id}」已存在，请设置 overwrite 覆盖')

        bundle = SkillsShClient.download_skill_bundle(source, skill_id)
        try:
            ok, msg = SkillFsService.install_package_dir(
                str(bundle.skill_dir),
                skill_id,
                user_id,
                overwrite=overwrite,
            )
            if not ok:
                if '已存在' in msg:
                    raise ConflictException(message=msg)
                raise ServiceException(message=msg)
            cls._write_origin(
                user_id,
                skill_id,
                source=source,
                skill_full_id=f"{source}/{skill_id}",
                skill_md_path=bundle.skill_md_relpath,
            )
            logger.info(f"skills market installed: user={user_id} {source}/{skill_id}")
            return msg
        finally:
            SkillsShClient.cleanup_bundle(bundle)

    @classmethod
    def _annotate_install_status(
        cls,
        items: list[SkillMarketItem],
        user_id: str | int,
    ) -> None:
        local = cls._scan_local_packages(user_id)
        for item in items:
            match = cls._match_local(item.source, item.skill_id, local)
            item.install_match = match
            item.installed = match == "exact"

    @classmethod
    def _match_local(
        cls,
        source: str,
        skill_id: str,
        local: dict[str, dict[str, str | None]],
    ) -> InstallMatch:
        info = local.get(skill_id)
        if info is None:
            return "none"
        origin_source = info.get("source")
        origin_skill = info.get("skillId")
        if origin_source == source and (origin_skill in (None, skill_id)):
            return "exact"
        return "name_conflict"

    @classmethod
    def _scan_local_packages(
        cls, user_id: str | int,
    ) -> dict[str, dict[str, str | None]]:
        """skill_id → {source, skillId}；无 origin 时 source/skillId 为 None。"""
        root = get_user_skills_dir(user_id)
        result: dict[str, dict[str, str | None]] = {}
        if not root.is_dir():
            return result
        try:
            names = list(root.iterdir())
        except OSError as exc:
            logger.warning(f"scan user skills failed: {exc}")
            return result
        for entry in names:
            if not entry.is_dir() or entry.name.startswith(".") or entry.is_symlink():
                continue
            origin = cls._read_origin(entry)
            if origin is None:
                result[entry.name] = {"source": None, "skillId": None}
            else:
                result[entry.name] = {
                    "source": origin.get("source"),
                    "skillId": origin.get("skillId") or entry.name,
                }
        return result

    @classmethod
    def _read_origin(cls, package_dir: Path) -> dict | None:
        path = package_dir / ".skills-sh" / "origin.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        source = data.get("source")
        skill_id = data.get("skillId")
        return {
            "source": str(source).strip() if source else None,
            "skillId": str(skill_id).strip() if skill_id else None,
        }

    @classmethod
    def _write_origin(
        cls,
        user_id: str | int,
        skill_id: str,
        *,
        source: str,
        skill_full_id: str,
        skill_md_path: str,
    ) -> None:
        root = Path(SkillFsService.get_user_root_path(user_id)) / skill_id
        meta_dir = root / ".skills-sh"
        try:
            meta_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "registry": SkillsMarketConfig.base_url.rstrip("/"),
                "id": skill_full_id,
                "source": source,
                "skillId": skill_id,
                "skillMdPath": skill_md_path,
                "installedAt": int(time.time() * 1000),
            }
            (meta_dir / "origin.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(f"write skills-sh origin failed: {exc}")

    @classmethod
    def _to_item(cls, hit: SkillsShSearchHit) -> SkillMarketItem:
        return SkillMarketItem(
            id=hit.id,
            skill_id=hit.skill_id,
            name=hit.name,
            source=hit.source,
            installs=hit.installs,
            market_url=market_url_for(hit.id),
        )
