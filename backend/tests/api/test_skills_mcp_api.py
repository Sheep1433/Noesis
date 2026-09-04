"""Skill 文件系统与 MCP 管理接口用例（integration）。

此前两模块零覆盖。Skill 市场四个端点依赖 skills.sh / GitHub 外网，不在
本地 happy path 范围（见文件尾注释）；MCP probe / tools 通过本地起一个
真实 FastMCP streamable-http server 走完整握手，不依赖外网。

前置与运行：

    cd backend && uv run app.py
    uv run pytest tests/api/test_skills_mcp_api.py -m integration
"""

from __future__ import annotations

import io
import json
import socket
import threading
import time
import zipfile

import pytest

pytestmark = [pytest.mark.integration]

_SKILL_NAME = "api-test-skill"

_SKILL_MD = """---
name: api-test-skill
description: 接口验证用临时技能（上传后即删除）
---

# api-test-skill

接口集成验证用占位内容。
"""


def _build_skill_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(f"{_SKILL_NAME}/SKILL.md", _SKILL_MD)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def local_mcp_server():
    """本地 FastMCP streamable-http server（/mcp，含 ping 工具）。"""
    import uvicorn
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("api-test-mcp")

    @mcp.tool()
    def ping() -> str:
        """健康探测工具。"""
        return "pong"

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    config = uvicorn.Config(
        mcp.streamable_http_app(), host="127.0.0.1", port=port, log_level="error"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.1)
    assert server.started, "本地 MCP server 未能在时限内启动"
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5.0)


# ----- Skill 文件系统 -----


def test_skill_fs_packages_and_tree(auth_client) -> None:
    resp = auth_client.get("/api/skills/fs/packages")
    resp.raise_for_status()
    packages = resp.json()["data"]
    assert isinstance(packages, list)

    resp = auth_client.get("/api/skills/fs/tree")
    resp.raise_for_status()
    tree = resp.json()["data"]
    assert "platform" in tree and "user" in tree


def test_skill_upload_read_archive_delete(auth_client) -> None:
    """上传 zip→包列表可见→读文件→打包下载→卸载→消失。"""
    resp = auth_client.post(
        "/api/skills/fs/upload-zip",
        files={"file": ("api-test-skill.zip", _build_skill_zip(), "application/zip")},
    )
    resp.raise_for_status()
    try:
        resp = auth_client.get("/api/skills/fs/packages")
        resp.raise_for_status()
        assert any(
            p.get("name") == _SKILL_NAME for p in resp.json()["data"]
        ), "上传的技能包应出现在包列表"

        resp = auth_client.get(
            "/api/skills/fs/file",
            params={"path": f"{_SKILL_NAME}/SKILL.md", "source": "user"},
        )
        resp.raise_for_status()
        assert "接口集成验证用占位内容" in resp.json()["data"]["content"]

        resp = auth_client.get(
            "/api/skills/fs/package/archive",
            params={"path": _SKILL_NAME, "source": "user"},
        )
        resp.raise_for_status()
        assert resp.headers["content-type"].startswith("application/zip")
        assert zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
    finally:
        auth_client.delete("/api/skills/fs/package", params={"path": _SKILL_NAME})

    resp = auth_client.get("/api/skills/fs/packages")
    resp.raise_for_status()
    assert all(p.get("name") != _SKILL_NAME for p in resp.json()["data"])


# ----- MCP 管理面 -----


def test_mcp_servers_list_and_status(auth_client) -> None:
    resp = auth_client.get("/api/mcp/servers", params={"scope": "all"})
    resp.raise_for_status()
    assert isinstance(resp.json()["data"]["servers"], list)

    # probe=false：纯本地读取，不做网络握手
    resp = auth_client.get("/api/mcp/servers/status", params={"probe": "false"})
    resp.raise_for_status()
    for server in resp.json()["data"]["servers"]:
        assert server["status"] == "unknown"


def test_mcp_config_roundtrip(auth_client) -> None:
    """读取（不存在则种模板）→保存改写→恢复原文。"""
    resp = auth_client.get("/api/mcp/config")
    resp.raise_for_status()
    original = resp.json()["data"]
    try:
        config = json.loads(original["content"])
        config.setdefault("mcpServers", {})["api-test-entry"] = {
            "transport": "streamable_http",
            "url": "http://127.0.0.1:9/mcp",
            "display_name": "接口验证条目",
        }
        resp = auth_client.put(
            "/api/mcp/config", json={"content": json.dumps(config, ensure_ascii=False)}
        )
        resp.raise_for_status()
        assert "api-test-entry" in resp.json()["data"]["content"]
    finally:
        auth_client.put("/api/mcp/config", json={"content": original["content"]})


def test_mcp_server_lifecycle_probe_and_tools(
    auth_client, local_mcp_server
) -> None:
    """server 增改/启停/删除 + 对本地真实 MCP server 的 probe 与 tools 全绿。"""
    server_id = "api-test-mcp-server"
    try:
        resp = auth_client.put(
            f"/api/mcp/servers/{server_id}",
            json={
                "transport": "streamable_http",
                "url": local_mcp_server,
                "display_name": "接口验证 MCP",
                "enabled": True,
            },
        )
        resp.raise_for_status()

        resp = auth_client.post(f"/api/mcp/servers/{server_id}/probe")
        resp.raise_for_status()
        probe = resp.json()["data"]
        assert probe["ok"] is True, f"本地 MCP probe 应成功: {probe}"
        assert probe["tool_count"] >= 1

        resp = auth_client.get(
            f"/api/mcp/servers/{server_id}/tools", params={"refresh": "false"}
        )
        resp.raise_for_status()
        tool_names = {t["name"] for t in resp.json()["data"]["tools"]}
        assert "ping" in tool_names

        resp = auth_client.post(f"/api/mcp/servers/{server_id}/disable")
        resp.raise_for_status()
        assert resp.json()["data"]["enabled"] is False

        resp = auth_client.post(f"/api/mcp/servers/{server_id}/enable")
        resp.raise_for_status()
        assert resp.json()["data"]["enabled"] is True
    finally:
        auth_client.delete(f"/api/mcp/servers/{server_id}")

    resp = auth_client.get("/api/mcp/servers", params={"scope": "all"})
    resp.raise_for_status()
    assert all(
        s["id"] != server_id for s in resp.json()["data"]["servers"]
    ), "删除后 server 不应再出现在目录"
