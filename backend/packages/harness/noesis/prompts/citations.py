"""Shared prompt contract for source citations in normal text responses."""

CITATION_EXTENSION = """<citations>
当回答使用知识库搜索、web_search 或 web_fetch 返回的事实时，必须在对应事实之后紧邻添加来源引用，而不是只在答案末尾罗列本轮检索结果。

- 网页来源使用普通 Markdown 链接：`[来源标题](原始 URL)`。
- 知识库来源使用简短编号 `[1]`、`[2]`，并在回答末尾添加 `### 参考资料`，按相同编号列出文件名、Collection 和可用的章节或页码信息。
- 同一来源在全文复用同一编号或链接；只引用实际支持当前陈述的来源。
- 不得编造来源、标题、URL、文件名或定位信息。工具结果没有提供来源时，不添加引用并明确说明依据不足。
- 不得在用户可见正文中输出 evidence_id、document_id、segment_id、`[[source:...]]` 或 JSON citation 结构。
- 直接输出正常 Markdown 回答，不要把最终回答包装成 JSON，也不要调用用于提交答案的虚拟工具。
</citations>"""
