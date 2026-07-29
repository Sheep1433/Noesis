# feishu HITL 审批令牌无过期清理

- 状态：✅ 已修复
- 严重度：低
- 发现时间：2026-07-29
- 位置：`backend/noesis_server/services/channels/feishu_runtime.py:45`（`_hitl_prompts`）

## 现象

`_hitl_prompts` 字典用于暂存飞书审批卡片的 token → 上下文映射。token 在 `_deliver_after_result` 中写入（`expires_at = time.monotonic() + 86400`，24h），在 `_handle_card` 中被点击时 `pop` 移除。

但过期条目只在读取时被跳过（`prompt.expires_at < time.monotonic()` 即 `return`），**从不主动移除**，且该字典没有 `max_items` 上限（对比 `EventDeduplicator` 有 `max_items=4096` 与淘汰循环）。

## 影响

用户发起敏感操作后若长期不点击审批卡片，条目在 24h 后只会失效，但原实现不会删除，因此会一直残留到进程退出。高频使用飞书通道且审批经常搁置的部署下，该字典会持续增长，属于缓慢内存泄漏；24h 内的突发审批量也没有容量约束。

## 根因

设计上依赖「用户点击 → pop」作为唯一回收路径，未覆盖「过期未点击」与「进程长期运行」两条路径。

## 修复

- 卡片回调读取到过期 token 时立即删除，且不执行审批；
- `_supervisor_loop` 每 30s 清扫从未点击的过期 token；
- 写入前清扫并将缓存限制为 4096 条，超限时优先删除最早过期的条目；
- 飞书运行时停止时清空缓存，避免重启后保留无效上下文。

## 验证

- `test_card_callback_evicts_expired_hitl_prompt`：过期 token 被拒绝且移除；
- `test_hitl_prompt_store_evicts_expired_and_bounds_capacity`：未点击条目可清扫且缓存有硬上限；
- `test_card_callback_rejects_other_open_id`：无权限点击仍不能消费有效 token。
