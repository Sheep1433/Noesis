## 1. 后端：工具供料去噪

- [x] 1.1 `web_search_tool.py` `_normalize_web_result` 去除 `citable: True` 注入；`test_web_search_tool.py` 同步断言（输出不含 citable、URL 非法结果仍被整条丢弃）
- [x] 1.2 `kb_search_tool.py` `_format_hits`：去除 `citable` 字段；`citation_ref` 仅在 `hit.citable` 时输出；`test_kb_search_tool.py` 同步（versioned 命中含 citation_ref 无 citable、legacy_unversioned 命中无 citation_ref）
- [x] 1.3 `message_builder.py` 删除 `raw.get("citable", True)` 过滤（保留 isinstance 防御）；`test_message_builder.py` 验证无身份 KB 命中走 `invalid_evidence_envelope` 拒收、合法命中登记不受影响

## 2. 前端：引用判定收紧

- [x] 2.1 `researchArcs.ts`：`collectCitationSignals` 只保留完整标记 exactKeys（删 `canonicalUrlsInText`、`BARE_CITATION_RE`、`hostOfCanonicalWebKey`、bareHints）；`entryIsCited` 退化为精确查找；`attributionUnavailable` 语义改为「无完整标记」；相关注释按现态重写（不留兜底通道描述残留）
- [x] 2.2 `ArcPanelData` 新增 `writtenFilePaths`（弧内 write_file/edit_file 的 `input.file_path` 提取，含后写覆盖语义由消费方处理）；`computeArcPanels` 填充
- [x] 2.3 `researchArcs.test.ts`：改写三通道用例为单通道（完整标记命中计入引用；裸 URL / 残缺标记归其他检索来源；无完整标记降级「共检索 N」；writtenFilePaths 提取）

## 3. 前端：文件预览编号与 KB 点击

- [x] 3.1 `FilePreview/index.vue`：新增可选 `citationIndex` prop 并传入 `MarkdownInstance.render` env；新增 KB 徽章点击路由（`data-kb-ref` → KnowledgeBaseDetail，与 MarkdownPreview 同逻辑）
- [x] 3.2 `SessionContextPanel.vue`：新增可选 `citationIndex` prop 透传 FilePreview
- [x] 3.3 `chat.vue`：由 `arcPanels` 构建归一化路径（去前导 `/`）→ 弧 CitationIndex 映射（后写覆盖先写），按 `previewPath` 传给 SessionContextPanel；无归属弧不传
- [x] 3.4 `filePreview.test.ts`：有 citationIndex 时渲染编号上标、无时保持无编号「·」上标；KB 徽章渲染 `data-kb-ref`

## 4. 验证与沉淀

- [x] 4.1 后端：`uv run pytest tests/api_contract -q` + 相关单测（message_builder / kb / web_search / bridge contract）
- [x] 4.2 前端：`pnpm vitest run`（相关文件）+ 按影响范围 `pnpm lint`
- [x] 4.3 新增决策记录 `docs/decisions/implemented/`（引用判定收紧 + citable 内化 + 文件预览编号；含被否方案：保留裸 URL 兜底、服务端算法归因、仅模型侧隐藏字段）
- [x] 4.4 `python3 scripts/verify-md-links.py` 与 `verify-decision-format.py` 过闸；openspec status 全 green
