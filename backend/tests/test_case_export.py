"""测试用例 Markdown 导出。"""

from agent.case_generate.case_coordinator import CaseCoordinator, export_cases
from api.chat_api import _attachment_content_disposition


def test_export_cases_markdown_contains_fields():
    md = export_cases(
        [
            {
                "case_id": "TC-001",
                "point_name": "上传成功",
                "point_level": "P0",
                "point_type": "functional",
                "scene_name": "文件上传",
                "preconditions": ["已登录"],
                "test_steps": ["选择文件", "点击上传"],
                "expected_results": ["上传成功"],
            }
        ],
        query="登录模块",
    )
    assert "# 测试用例报告" in md
    assert "登录模块" in md
    assert "上传成功" in md
    assert "文件上传" in md
    assert "1. 选择文件" in md


def test_coordinator_get_export_markdown_from_cache():
    coord = CaseCoordinator()
    thread_id = "sess-export-1"
    coord._finished_results[thread_id] = {
        "test_cases": [{"point_name": "点A", "test_steps": ["步骤"]}],
        "query": "需求A",
    }
    md = coord.get_export_markdown(thread_id)
    assert md is not None
    assert "点A" in md
    assert "需求A" in md


def test_coordinator_get_export_markdown_prefers_request_body():
    coord = CaseCoordinator()
    thread_id = "sess-export-2"
    coord._finished_results[thread_id] = {
        "test_cases": [{"point_name": "缓存点"}],
        "query": "缓存",
    }
    md = coord.get_export_markdown(
        thread_id,
        test_cases=[{"point_name": "请求点", "test_steps": ["s"]}],
        query="请求",
    )
    assert md is not None
    assert "请求点" in md
    assert "缓存点" not in md


def test_coordinator_get_export_markdown_empty_returns_none():
    coord = CaseCoordinator()
    assert coord.get_export_markdown("missing") is None


def test_attachment_disposition_ascii_only_in_filename_param():
    disp = _attachment_content_disposition("测试用例-登录.md")
    assert "filename=test-cases-export.md" in disp
    assert "filename*=" in disp
    assert "%E6%B5%8B" in disp or "UTF-8" in disp
    assert "测试用例" not in disp.split("filename=")[1].split(";")[0]
