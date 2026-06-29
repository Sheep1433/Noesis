"""初始 schema + 演示账号

Revision ID: 202606290001
Revises:
Create Date: 2026-06-29

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "202606290001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# bcrypt hash for password "123456"
_DEMO_ADMIN_PASSWORD_HASH = (
    "$2b$12$aZVFQokpDhbSA7/jjo573OAQ.CV11QmpT8kBLHL0lmEyBKsyMQhAa"
)


def upgrade() -> None:
    op.create_table(
        "t_user",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="用户ID"),
        sa.Column("username", sa.String(length=200), nullable=True, comment="用户名称"),
        sa.Column("password", sa.String(length=300), nullable=True, comment="密码"),
        sa.Column("mobile", sa.String(length=100), nullable=True, comment="手机号"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="修改时间"),
        sa.PrimaryKeyConstraint("id"),
        comment="用户表",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )

    op.create_table(
        "t_chat_session",
        sa.Column("id", sa.String(length=36), nullable=False, comment="UUID 主键"),
        sa.Column("parent_id", sa.String(length=36), nullable=True, comment="父会话 ID（subagent 场景）"),
        sa.Column("user_id", sa.String(length=36), nullable=False, comment="用户 ID（冗余，便于查询）"),
        sa.Column("title", sa.String(length=500), server_default="新对话", nullable=False, comment="会话标题"),
        sa.Column("extra", sa.JSON(), nullable=True, comment="JSON: {user_id, model, ...}"),
        sa.Column("created_at", sa.BigInteger(), nullable=False, comment="创建时间戳（Unix 毫秒）"),
        sa.Column("updated_at", sa.BigInteger(), nullable=False, comment="更新时间戳（Unix 毫秒）"),
        sa.Column("deleted_at", sa.BigInteger(), nullable=True, comment="软删时间戳（毫秒），NULL=未删除"),
        sa.ForeignKeyConstraint(["parent_id"], ["t_chat_session.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        comment="会话表 v2.1",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_session_parent", "t_chat_session", ["parent_id"], unique=False)
    op.create_index("idx_session_updated", "t_chat_session", ["updated_at"], unique=False)
    op.create_index("idx_session_user", "t_chat_session", ["user_id"], unique=False)

    op.create_table(
        "t_chat_message",
        sa.Column("id", sa.String(length=36), nullable=False, comment="UUID 主键"),
        sa.Column("session_id", sa.String(length=36), nullable=False, comment="所属会话 ID"),
        sa.Column("parent_id", sa.String(length=36), nullable=True, comment="父消息 ID"),
        sa.Column("user_id", sa.String(length=36), nullable=False, comment="用户 ID（冗余，便于查询）"),
        sa.Column("role", sa.Text(), nullable=False, comment="角色: user | assistant"),
        sa.Column("content", sa.JSON(), nullable=True, comment="消息内容，JSON multipart 格式"),
        sa.Column("extra", sa.JSON(), nullable=True, comment="JSON: model, tokens, finish_reason, error"),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="completed",
            nullable=False,
            comment="状态: completed | partial | error | streaming",
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False, comment="创建时间戳（Unix 毫秒）"),
        sa.Column("deleted_at", sa.BigInteger(), nullable=True, comment="软删时间戳（NULL=未删除）"),
        sa.ForeignKeyConstraint(["parent_id"], ["t_chat_message.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["t_chat_session.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="消息表 v2.1",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_message_session", "t_chat_message", ["session_id", "created_at"], unique=False)
    op.create_index("idx_message_parent", "t_chat_message", ["parent_id"], unique=False)

    op.create_table(
        "t_chat_attachment",
        sa.Column("id", sa.String(length=36), nullable=False, comment="UUID attachment_id"),
        sa.Column("session_id", sa.String(length=36), nullable=False, comment="所属会话"),
        sa.Column("user_id", sa.String(length=36), nullable=False, comment="用户 ID"),
        sa.Column("file_name", sa.String(length=500), nullable=False, comment="原始文件名"),
        sa.Column("kind", sa.String(length=20), nullable=False, comment="document | image"),
        sa.Column("original_path", sa.String(length=1000), nullable=False, comment="原文件相对路径"),
        sa.Column("markdown_path", sa.String(length=1000), nullable=True, comment="解析后 Markdown 相对路径"),
        sa.Column("mime_type", sa.String(length=100), nullable=True, comment="MIME 类型"),
        sa.Column("virtual_path", sa.String(length=1000), nullable=False, comment="Agent 工具逻辑路径"),
        sa.Column("char_count", sa.Integer(), server_default="0", nullable=False, comment="Markdown 字符数"),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="uploaded",
            nullable=False,
            comment="uploaded | parsed | failed",
        ),
        sa.Column("preview_base64", sa.Text(), nullable=True, comment="图片缩略图 base64（可选）"),
        sa.Column("created_at", sa.BigInteger(), nullable=False, comment="创建时间戳（毫秒）"),
        sa.Column("expires_at", sa.BigInteger(), nullable=False, comment="过期时间戳（毫秒）"),
        sa.ForeignKeyConstraint(["session_id"], ["t_chat_session.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="聊天会话附件表",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_attachment_session", "t_chat_attachment", ["session_id", "created_at"], unique=False)
    op.create_index("idx_attachment_expires", "t_chat_attachment", ["expires_at"], unique=False)

    op.execute(
        sa.text(
            """
            INSERT INTO t_user (id, username, password, mobile, create_time, update_time)
            VALUES (1, 'admin', :password, NULL, NOW(), NOW())
            """
        ).bindparams(password=_DEMO_ADMIN_PASSWORD_HASH)
    )


def downgrade() -> None:
    op.drop_index("idx_attachment_expires", table_name="t_chat_attachment")
    op.drop_index("idx_attachment_session", table_name="t_chat_attachment")
    op.drop_table("t_chat_attachment")
    op.drop_index("idx_message_parent", table_name="t_chat_message")
    op.drop_index("idx_message_session", table_name="t_chat_message")
    op.drop_table("t_chat_message")
    op.drop_index("idx_session_user", table_name="t_chat_session")
    op.drop_index("idx_session_updated", table_name="t_chat_session")
    op.drop_index("idx_session_parent", table_name="t_chat_session")
    op.drop_table("t_chat_session")
    op.drop_table("t_user")
