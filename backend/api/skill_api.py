"""
Skill API — 仅 Skills 文件目录（磁盘），不再使用 MySQL 用户 Skill 表
"""
import os
import tempfile

from fastapi import APIRouter, HTTPException, UploadFile, File, Depends

from schemas.login_vo import CurrentUser
from schemas.skill_vo import (
    SkillFsTreeResponse,
    SkillFsFileContent,
)
from services.skill_fs_service import SkillFsService, max_zip_bytes
from services.user_service import UserService
from utils.response_util import ResponseUtil

skill_router = APIRouter(prefix='/api/skills', tags=['Skill 模块'])


@skill_router.get('/fs/tree', response_model=SkillFsTreeResponse)
async def get_skills_fs_tree(
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """
    列出配置的 Skills 文件目录（默认仓库 backend/skills）树形结构
    """
    _ = current_user
    return SkillFsService.get_tree()


@skill_router.get('/fs/file', response_model=SkillFsFileContent)
async def get_skills_fs_file(
    path: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """
    读取 Skills 目录下文本文件内容（相对根路径）
    """
    _ = current_user
    rel = path.strip()
    ok, err, content = SkillFsService.read_file(rel)
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    return SkillFsFileContent(path=rel, content=content)


@skill_router.post('/fs/upload-zip')
async def upload_skills_fs_zip(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """
    上传 skill：将 ZIP 解压到当前 Skills 根目录（不新建额外子目录名；包内若有子目录则原样落盘）。
    """
    _ = current_user
    zip_path = None
    try:
        raw = await file.read()
        if len(raw) > max_zip_bytes():
            raise HTTPException(status_code=400, detail='文件大小超过 10MB 限制')
        suffix = os.path.splitext(file.filename)[1] if file.filename else '.zip'
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(raw)
            zip_path = tmp.name

        ok, msg = SkillFsService.extract_zip_to_root(zip_path)
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
        return ResponseUtil.success(msg=msg)
    finally:
        if zip_path and os.path.exists(zip_path):
            os.unlink(zip_path)
