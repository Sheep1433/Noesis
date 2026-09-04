# Tasks

## 1. 预算中间件 append-only 化

- [x] 1.1 删除批次合计逻辑（`aggregate_max_chars` 参数与 `_project_tool_messages` 的 force 替换路径；`_replace_message` 的 `force` 参数随之退役）
- [x] 1.2 删除参数卸载滑动窗口（`argument_keep_recent_messages`），大参数入口即定型；参数梗概 head 与结果梗概同额（2000），结果梗概尾 1000
- [x] 1.3 投影幂等：参数替换文本前缀哨兵（`_ARG_REPLACEMENT_PREFIX`）防止二次替换；单测钉死「同一历史两次投影逐字段一致」不变量
- [x] 1.4 回归测试更新：批次合计测试改为「并行结果整体放行」；窗口测试改为「新旧参数无差别替换」

## 2. read_file 源头封顶

- [x] 2.1 `agents/tools/read_file_bound.py`：原地包装 FilesystemMiddleware 的 read_file（与 execute 后台化替换同模式），超限截断 + 行号续读提示，schema 与工具身份不变
- [x] 2.2 stack 装配接线（主/子 Agent 共用，位于 `filesystem_middleware_hook` 之前）；新配置 `runtime.read_file_max_chars`（默认 20,000）走全链（yaml_config / env / 五个 yaml）
- [x] 2.3 单测：超限截断、下限放行、异步路径、身份保持、缺工具静默跳过

## 3. Runtime Context 冻结块

- [x] 3.1 `DynamicContextMiddleware` 重写：日期粒度头部冻结块（private state 持久化、messages[0] 投影）、跨日尾部纠正、附件集合变化尾部声明、无 provider 清理路径
- [x] 3.2 `insert_late_context`（`late_context.py`）退役删除；factory 默认 provider 改日期粒度（去 session_id、去时间戳）；`__init__` 导出更新
- [x] 3.3 单测：冻结块跨 run 一致、跨日尾部纠正、附件声明一次、子 Agent 继承语义（private state 键聚合）、渲染布局

## 4. web_fetch 单份正文与头尾截断

- [x] 4.1 删除输出 JSON 顶层 `content` 双份存储（消费方只读 `results`）；`fetch_max_chars` 4096 → 16000（五个 yaml + 代码默认值）
- [x] 4.2 单测更新为单份正文断言
- [x] 4.3 超限页头 75% + 尾 25% 截断（行边界对齐）；provider 层（tavily/local_fetch）去掉预先硬截断，全文上抛工具层统一处理
- [x] 4.4 全文落盘 agent backend（`/web_pages/`，2MB 上限，尽力而为）；页脚给出保存路径 + 精确续读 offset（read_file offset 为 0 起始行号）；backend 不可用（COMMON_QA）退化纯截断
- [x] 4.5 `build_web_search_tools(backend=)` 注入：Super Agent 传入 backend，子 Agent 经共享工具列表继承；单测覆盖截断/落盘/退化路径

## 5. 工具失败通道单源化

- [x] 5.1 `ToolFailure` 重构：`message_for_llm`/`message_for_user` 双文案 → 单份 `text` 短文案（≤600 字符）；pydantic ValidationError 结构化提取（`file_path: Field required（传入字段：…）` 一行，不再灌 str(exc) 全文 dump）；可重试类统一后缀「，可稍后重试」（不可重试不带，防模型盲目重试）；UNKNOWN 纯文本兜底固定「执行失败」不透传任意文本
- [x] 5.2 `build_error_tool_message` 正文 = `Error: <短文案>`（与 deepagents 错误文本前缀契约一致）；errorCategory/retryable 保留为 additional_kwargs metadata（统计 / SUBAGENT_FAILED 判定 / 预算中间件保语义）
- [x] 5.3 bridge：errorCategory 优先取 ToolMessage metadata（middleware 权威标记），无 metadata 才文本分类；入库 output = ToolMessage 正文逐字（错误返回型原文即权威文案）；SSE error 字段 = 同一段短文案
- [x] 5.4 前端 `ToolCallCollapse`：删本地 categoryCopy 分类文案表（曾与后端表漂移、展开后细节丢失）；折叠失败行 = 通用「执行失败」，展开态「状态」区显示后端短文案
- [x] 5.5 分类器降级为打标器：`_suggestion_for_category`、`USER_TOOL_ERROR_MESSAGES`、Suggestion 行全部删除

## 6. 规格与验证

- [x] 6.1 openspec 变更四件套（proposal / design 含备选方案与语义消费者 / agent-runtime delta / tasks）
- [x] 6.2 决策记录（含被否方案）落 `docs/decisions/implemented/`
- [x] 6.3 全量后端回归绿（1375）；前端 lint / 224 单测 / build 绿；`verify-md-links` / `verify-decision-format` 通过
- [ ] 6.4 真实场景人工验收：深度研究任务中 read_file 超限显示续读提示、并行搜索结果不再被摘要替换、多步 run 缓存命中率向结构值收敛（用户执行）
