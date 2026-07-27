import json
from types import SimpleNamespace

from evals.agent.harbor.noesis_runner import HarborRunCollector, write_run_artifacts


def test_harbor_collector_writes_common_manifest_and_trajectory(tmp_path):
    collector = HarborRunCollector(instruction="do it", model_name="model")
    collector.add_user_step()
    collector.consume(
        {
            "event": "on_tool_start",
            "name": "execute",
            "run_id": "1",
            "data": {"input": {"command": "pwd"}},
        }
    )
    collector.consume({"event": "on_tool_end", "name": "execute", "run_id": "1", "data": {"output": "ok"}})
    collector.consume({"event": "on_chat_model_stream", "data": {"chunk": SimpleNamespace(content="done")}})
    collector.consume({"type": "__tw_finish__", "finish_reason": "stop"})

    write_run_artifacts(logs_dir=tmp_path, session_id="sid", collector=collector)
    summary = json.loads((tmp_path / "noesis.txt").read_text(encoding="utf-8"))
    trajectory = json.loads((tmp_path / "trajectory.json").read_text(encoding="utf-8"))
    assert summary["schema_version"] == "noesis-eval-run/v1"
    assert summary["completed"] is True
    assert summary["final_text"] == "done"
    assert summary["tool_stats"] == {"execute": 1}
    assert trajectory["schema_version"] == "ATIF-v1.7"
