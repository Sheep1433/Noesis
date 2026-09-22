"""leader 晋升对账顺序约束（server/bootstrap/leader_runtime.py）。

顺序是安全不变式：claimed 命令重置先于命令消费者启动（换主窗口
排队任务的追加消息不被误翻转）；queued 重建先于 dispatcher.start。
改步骤清单（增删/重排）本测试同步红——顺序变化的评审入口。
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

from server.bootstrap import leader_runtime
from server.bootstrap.leader_runtime import RECONCILE_ORDER, _reconcile_steps


def test_reconcile_order_matches_step_factory() -> None:
    """RECONCILE_ORDER（推导值）与步骤工厂产出一致——单一事实源。"""
    steps = _reconcile_steps(MagicMock(), token_term=1)
    assert [name for name, _ in steps] == RECONCILE_ORDER


def test_consumer_start_follows_reconcile_loop() -> None:
    """consumer.start 的调用出现在对账 for 循环之后（源级顺序断言，
    回归入口：有人把 start 挪到循环内/前时本测试红）。"""
    src = inspect.getsource(leader_runtime)
    loop_pos = src.index("for name, step in")
    start_pos = src.index("consumer.start()")
    assert loop_pos < start_pos, "consumer.start 必须在对账循环之后"
