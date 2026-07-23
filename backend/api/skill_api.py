"""
Skill API — Skills 文件目录（平台 + 用户）+ skills.sh 市场
"""
import os
import tempfile
from typing import Literal

from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, Query
from fastapi.responses import Response

from schemas.login_vo import CurrentUser
from schemas.skill_vo import (
    SkillFsFileContent,
    SkillSource,
    SkillMarketInstallRequest,
)
from services.skill_fs_service import SkillFsService, max_zip_bytes
from services.skill_market_service import SkillMarketService
from services.user_service import UserService
from common.http.response import ResponseUtil

skill_router = APIRouter(prefix='/api/skills', tags=['Skill 模块'])


@skill_router.get('/fs/tree')
async def get_skills_fs_tree(
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """列出当前用户可用的 Skills（平台预置 + 个人上传）。"""
    return ResponseUtil.success(
        data=SkillFsService.get_tree(current_user.user_id).model_dump(),
    )


@skill_router.get('/fs/file')
async def get_skills_fs_file(
    path: str,
    source: SkillSource = 'platform',
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """
    读取 Skills 目录下文本文件；source=platform|user
    """
    rel = path.strip()
    ok, err, content = SkillFsService.read_file(
        rel, source=source, user_id=current_user.user_id,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    return ResponseUtil.success(
        data=SkillFsFileContent(
            rel_path=rel,
            filename=os.path.basename(rel) or rel,
            source=source,
            content=content,
        ).model_dump(),
    )


@skill_router.post('/fs/upload-zip')
async def upload_skills_fs_zip(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """上传个人技能：将 ZIP 解压到当前登录用户的私有目录。"""
    zip_path = None
    try:
        raw = await file.read()
        if len(raw) > max_zip_bytes():
            raise HTTPException(status_code=400, detail='文件大小超过 10MB 限制')
        suffix = os.path.splitext(file.filename)[1] if file.filename else '.zip'
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(raw)
            zip_path = tmp.name

        ok, msg = SkillFsService.extract_zip_to_user_dir(zip_path, current_user.user_id)
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
        return ResponseUtil.success(msg=msg)
    finally:
        if zip_path and os.path.exists(zip_path):
            os.unlink(zip_path)


@skill_router.delete('/fs/package')
async def delete_user_skill_package(
    path: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """删除当前用户上传的顶层技能目录。"""
    ok, msg = SkillFsService.delete_user_skill_package(path, current_user.user_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return ResponseUtil.success(msg=msg)


@skill_router.get('/fs/package/archive')
async def download_skill_package_archive(
    path: str,
    source: SkillSource = 'user',
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """下载顶层技能目录 ZIP（平台预置只读，个人技能可备份）。"""
    ok, err, data, filename = SkillFsService.build_package_zip(
        path,
        source=source,
        user_id=current_user.user_id,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    return Response(
        content=data,
        media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@skill_router.get('/market/browse')
async def market_browse(
    sort: Literal['all_time', 'trending'] = Query(
        'trending',
        description='all_time=累计安装；trending=24h 趋势',
    ),
    limit: int = Query(20, ge=1, le=50, description='每页条数'),
    offset: int = Query(0, ge=0, description='偏移量'),
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """skills.sh Leaderboard（/ 或 /trending）。"""
    return ResponseUtil.success(
        data=SkillMarketService.browse(
            current_user.user_id, sort=sort, limit=limit, offset=offset,
        ).model_dump(),
    )


@skill_router.get('/market/search')
async def market_search(
    q: str = Query(..., min_length=2, description='搜索词'),
    limit: int = Query(20, ge=1, le=50, description='每页条数'),
    offset: int = Query(0, ge=0, description='偏移量'),
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """搜索 skills.sh 目录。"""
    return ResponseUtil.success(
        data=SkillMarketService.search(
            current_user.user_id, q, limit=limit, offset=offset,
        ).model_dump(),
    )


@skill_router.get('/market/detail')
async def market_detail(
    source: str = Query(..., description='GitHub owner/repo'),
    skill_id: str = Query(..., description='技能包名'),
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """拉取技能详情（含 SKILL.md）。"""
    return ResponseUtil.success(
        data=SkillMarketService.detail(current_user.user_id, source, skill_id).model_dump(),
    )


@skill_router.post('/market/install')
async def market_install(
    body: SkillMarketInstallRequest,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """从 skills.sh / GitHub 安装到个人 skills。"""
    msg = SkillMarketService.install(
        current_user.user_id,
        body.source,
        body.skill_id,
        overwrite=body.overwrite,
    )
    return ResponseUtil.success(msg=msg)
