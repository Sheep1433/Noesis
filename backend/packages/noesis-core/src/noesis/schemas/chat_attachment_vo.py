from typing import List, Optional

from pydantic import BaseModel, Field


class AttachmentResponse(BaseModel):
    attachment_id: str = Field(description='附件 UUID')
    file_name: str = Field(description='原始文件名')
    kind: str = Field(description='document | image')
    mime_type: Optional[str] = Field(default=None, description='MIME 类型')
    status: str = Field(description='uploaded | parsed | failed')
    char_count: int = Field(default=0, description='Markdown 字符数')
    preview: Optional[str] = Field(default=None, description='正文预览或图片说明')
    virtual_path: str = Field(description='Agent 工具逻辑路径')
    artifact_url: Optional[str] = Field(default=None, description='预览 URL')
    preview_base64: Optional[str] = Field(default=None, description='图片缩略图 base64（仅 image）')
    parse_error: Optional[str] = Field(default=None, description='解析失败原因（若有）')


class AttachmentListResponse(BaseModel):
    attachments: List[AttachmentResponse] = Field(description='附件列表')
    total: int = Field(description='数量')
