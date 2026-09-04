# chat 页流式热路径每 delta 全量重建：标签页渲染进程 11 天膨胀至 2.4GB

> 状态：✅ 已修复
> 发现日期：2026-09-04
> 环境：本地 dev（M4 / 16GB / Edge 151），长期挂着的 chat 标签页（同渲染进程存活 10 天 21 小时）

## 现象

- chat 标签页渲染进程 footprint 达 **2443M**，常驻仅 ~110M（其余在压缩器/swap），且随流式使用持续增长（前一天 2236M → 次日 2443M）；**切到后台仍在涨**（SSE 不因切后台停流）
- **刷新页面不回落**：reload 复用同一渲染进程，V8 释放的堆页以 purgeable 形式留在进程内，只有关标签页才真正释放
- 同会话全量历史重新渲染仅 ~110M 常驻——膨胀量与历史体量无关，与**流式过程**相关
- 机器级后果：16GB 物理内存仅剩 ~100M、压缩器 ~5G、swap 用满，换页风暴导致整机卡顿

## 发现路径

1. 整机卡顿 → 资源排查定位到浏览器 footprint ~3.2G 为应用侧大头
2. Edge 无调试端口，用两个间接信号归因「渲染进程 ↔ 标签」：切换标签观察 RSS 回升（压缩页被解压）、逐标签后台 reload 观察 CPU 峰值（内存信号在压缩环境下有噪音，CPU 归因最可靠）
3. CPU 归因实锤：chat 页 reload 时其渲染进程 395% CPU；该进程 11 天未关、2.4G footprint，占 Edge 总占用 ~85%（此前按「切到 GitHub 标签时它涨了 75MB」误判为 GitHub 页，实际是 chat 页 SSE 在后台持续累积，与切换哪个标签无关）
4. 刷新对照实验排除「历史体量」假设：全量历史重渲染 ~110M ≪ 2.4G → 锁定流式路径
5. 代码走读：useSSEStream.ts 帧即分发无积压 → chat.vue `patchAssistantPartsAt` / messageParts.ts `appendTextDelta` → 定位每 delta 全量重建链

## 根因

每个 text/reasoning delta（长 run 内可达数万次）触发以下全量重建：

| # | 位置 | 开销 | 修复 |
|---|------|------|------|
| ① | `messageParts.ts` `appendTextDelta`：`parts.map(p => ({...p}))` | 每 delta 克隆全部 part 对象 | ✅ copy-on-write，只新建命中 part |
| ② | `messageParts.ts` `syncLegacyFieldsFromParts`：拼接全部 text part | 每 delta 重建整条消息 content 字符串 | ✅ 摊薄：随 ③ 每 flush 一次（~10Hz） |
| ③ | `chat.vue` `patchAssistantPartsAt`：重建整个消息数组 | 每 delta 一次全链 patch | ✅ 摊薄：delta 经 streamDeltaBatcher 批量应用 |
| ④ | `chat.vue` watchEffect：`conversationItemsSnapshot = items.slice()` | 每 delta 再全量拷贝一次 | ✅ 随 ③ 摊薄（每 flush 指针级拷贝） |
| ⑤ | `chat.vue` `conversationItems` 为深层响应 ref | 每个新建对象递归 reactive 代理 | ✅ 随 ①+③ 摊薄（每 flush 仅 3 个新对象） |
| ⑥ | `MarkdownPreview/index.vue` `renderedMarkdown` computed | 每 token 全量重新解析 markdown | ✅ 随 ③ 摊薄（每 flush 一次） |

规模测算：最终 S 字符的消息经 D 个 delta 流完，仅 ② 就拷贝 S×D/2 字符，⑥ 再解析 S×D/2——单条长消息（SuperAgent 深度研究 run，parts 上百）的临时垃圾即 GB 级。V8 堆随分配速率扩张，后台标签 GC 惰性、释放页不归还 → footprint 单调上涨。

已排除项：会话切换会重置数组，排除跨会话累积；SSE 传输层无原始帧缓存，排除积压；`createFrameHandlerTable` 的 Map 按 run reset，排除工具元数据累积。

## 修复实现（2026-09-04）

方案与被否备选见决策记录 [2026-09-04-流式热路径delta批量应用](../decisions/implemented/2026-09-04-流式热路径delta批量应用.md)。

- **delta 批量应用**：新增 `frontend/src/views/chat/streamDeltaBatcher.ts`——text/reasoning delta 进缓冲（同签名连续 delta 分桶），100ms 定时 / 128KB 阈值触发单次 parts patch；结构性帧回调（snapshot / message-start / tool / retrieval / reasoning-end / hitl / finish / error）顶部先 flush，会话重置点 clear，卸载 dispose。`<think>` 拆分开关在 push 时捕获，flush 不读共享标志。
- **reducer copy-on-write**：`appendTextDelta` / `appendReasoningDelta` 只新建命中 part，其余复用引用（Vue 代理缓存使未变更 part 的代理身份稳定，子组件不再随每 delta 重渲染）。
- **等价性回归测试**：`__tests__/streamDeltaBatcher.test.ts`（合并/顺序/定时/阈值/clear/dispose）；`__tests__/messageParts.test.ts` 新增 COW 身份保持断言与「合并应用 ≡ 逐条应用」等价性（含 `<think>` 标签任意切分点）。

效果量化方式（使用侧验证）：关标签重开取干净渲染进程，跑固定几轮对话对比 footprint 增速（修复前实测几轮对话常驻 +~120MB）。

## 状态流转

- 2026-09-04 🆕 新增：整机资源排查 → 标签归因 → 刷新对照 → 代码走读，定位流式热路径全量重建
- 2026-09-04 ✅ 已修复：delta 批量应用 + reducer copy-on-write，整链开销按 flush 频率（~10Hz）计价；等价性测试钉住合并语义
