# /compact 手动压缩从未真正跑通：build_compaction_middleware 键名不匹配 TypeError

> 状态：✅ 已修复（2026-09-07，session-history-search 实机冒烟中发现并同提交修复）

## 现象

`/compact` 命令（`compact_session` → `build_compaction_middleware`）每次调用即抛
`TypeError: CompactionMiddleware.__init__() got an unexpected keyword argument 'compaction_thresholds'`，
被命令边界捕获后对用户表现为「压缩对话失败，请稍后重试」。手动压缩功能自引入以来从未成功执行过。

## 根因

`factory._compaction_deps` 返回的键名对齐 `NoesisStackDeps`（`compaction_thresholds` /
`compaction_keep_messages`），而 `build_compaction_middleware` 用 `CompactionMiddleware(backend=..., **deps)`
直构中间件——构造参数名是 `thresholds` / `keep_messages`。stack 路径
（`build_noesis_stack`）逐字段显式映射所以正常；只有 `/compact` 宿主 seam 直传 `**deps`。

## 为什么长期未发现

`test_compaction_service.py` 对 `build_compaction_middleware` 打了 fake（只断言 kwargs），
构造错误被 mock 挡住；真实构造从未被任何测试或线上路径覆盖（auto 压缩走 stack 路径不受影响）。

## 修复

- `factory.py`：`build_compaction_middleware` 显式映射 deps 键 → 构造参数名。
- 回归：`test_compaction_service.py::test_build_compaction_middleware_constructs_for_real`
  不再 fake builder，真实构造（只桩 `get_llm`）。
- 实机验证：session-history-search 冒烟脚本全链路通过（`/compact` completed、
  `compaction_cutoff_seq` 落库、压缩后 `search_history` 找回细节）。
