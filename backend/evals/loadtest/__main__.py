"""负载测试入口：打印 Locust 运行说明。"""

from __future__ import annotations

LOCUSTFILE = "evals/loadtest/locustfile.py"


def main() -> int:
    print("Noesis 深度研究负载测试（Locust）\n")
    print("前置：后端已启动（默认 http://127.0.0.1:8089）")
    print("依赖：uv sync --extra loadtest\n")
    print("Web UI：")
    print(f"  uv run locust -f {LOCUSTFILE} --host=http://127.0.0.1:8089\n")
    print("无 UI（1 并发示例）：")
    print(
        f"  uv run locust -f {LOCUSTFILE} --host=http://127.0.0.1:8089 "
        "--headless -u 1 -r 1 --run-time 30m --only-summary\n"
    )
    print("查询集：evals/loadtest/data/queries.jsonl")
    print("详见 backend/evals/README.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
