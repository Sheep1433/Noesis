# RunSubscription.close() 对 frozen 字段赋值：SSE 订阅队列静默泄漏

> 状态：✅ 已修复（2026-08-27，`c9a3bb01`）
> 发现日期：2026-08-27
> 环境：本地 dev，SUPER_AGENT_QA 流式会话（日志报 `FrozenInstanceError: cannot assign to field 'closer'`）

## 现象

运行日志每次 SSE 流关闭都报 `dataclasses.FrozenInstanceError: cannot assign to field 'closer'`，异常发生在 generator 清理阶段被事件循环吞掉——**流表面仍「正常结束」，无功能故障可见**。

## 根因

`RunSubscription` 是 `@dataclass(frozen=True)`，`f3553a42`（P1 共享状态机）给它加的幂等 `close()` 写了：

```python
closer, self.closer = self.closer, None   # 对冻结字段赋值 → 每次必抛
```

异常发生在赋值语句本身，**closer 里的退订逻辑一次都没执行过**。`close()` 是 SSE 流 `finally` 的退订入口（主 run 流 + dispatcher 唤醒订阅两处调用），意味着：

- 每次流正常结束、客户端断开、断线重连旧流销毁，都泄漏一个订阅队列（挂在 run 的 `subscribers` 里，直到 run 终态回收）
- 多 Tab、频繁刷新、移动端切前后台场景累积；泄漏队列持续吃事件（有界队列丢事件转 SlowSubscriber，指标噪音）

## 修复

```python
closer = self.closer
object.__setattr__(self, "closer", None)   # frozen 下合法消费一次性标记
if closer is not None:
    ...
```

保持 frozen 语义与幂等不变。回归测试断言：close 后订阅确实从 `handle.subscribers` 移除、重复 close 是 no-op。

## 教训（可复用）

- **静态审查盲区**：两轮静态审查都没抓到——单测只断言 message 文本不断言异常**类型**，两种异常的 toast 文案一样但 HTTP 语义完全不同；这例是异常被事件循环吞掉后「表面正常」，更难发现
- frozen dataclass 上做「一次性消费」模式必须用 `object.__setattr__`，普通赋值在 frozen 下必抛

## 状态流转

- 2026-08-27 🆕 新增：日志报错定位，根因为幂等 close 与 frozen 冲突
- 2026-08-27 ✅ 已修复：`c9a3bb01`，全量 1212 测试通过
