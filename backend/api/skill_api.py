"""
Skill API — Skills 文件目录（平台 + 用户）
"""
import os
import tempfile

from fastapi import APIRouter, HTTPException, UploadFile, File, Depends

from schemas.login_vo import CurrentUser
from schemas.skill_vo import (
    SkillFsTreeResponse,
    SkillFsFileContent,
    SkillSource,
)
from services.skill_fs_service import SkillFsService, max_zip_bytes
from services.user_service import UserService
from common.http.response import ResponseUtil

skill_router = APIRouter(prefix='/api/skills', tags=['Skill 模块'])


@skill_router.get('/fs/tree', response_model=SkillFsTreeResponse)
async def get_skills_fs_tree(
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """
    列出当前用户可用的 Skills（平台 extensions/skills + 用户目录）
    """
    return SkillFsService.get_tree(current_user.user_id)


@skill_router.get('/fs/file', response_model=SkillFsFileContent)
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
    display = rel
    if not display.startswith(('platform:', 'user:')):
        display = f'{source}:{rel}'
    return SkillFsFileContent(path=display, source=source, content=content)


@skill_router.post('/fs/upload-zip')
async def upload_skills_fs_zip(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """
    上传 skill：将 ZIP 解压到当前登录用户的 Skills 目录（.data/users/{user_id}/skills/）。
    """
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
