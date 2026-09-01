# 决策：HTML 沙箱渲染：锚点导航白屏 + Vue SFC 字面 script 坑

状态：implemented
日期：2026-08-25
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **症状**：iframe `sandbox="allow-scripts"` 渲染生成的 HTML 报告，点击页内锚点（如「今日总结」）白屏。
- **根因**：无 `allow-same-origin` 时点击 `#锚点` 被浏览器判为导航（目标 `about:srcdoc#summary`），opaque origin 导航被拦截 → iframe 空白。
- **解法**（`6749b492`）：srcdoc 注入约 10 行垫片脚本，捕获 `a[href^="#"]` 点击 → `preventDefault()` → `scrollIntoView({behavior:'smooth'})`。
- **附带坑**：Vue SFC 解析器按原文扫描标签块，script 模板字符串和注释里出现字面 `<script>`/`</script>` 都会提前闭合块导致编译失败，用字符串拼接规避。
