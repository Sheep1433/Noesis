"""Shared prompt contract for source citations in normal text responses."""

CITATION_EXTENSION = """<citations>
当回答使用知识库搜索、web_search 或 web_fetch 返回的事实时，在对应事实之后紧邻添加来源引用。

- 网页来源格式：[citation:来源标题](原始URL)
- 知识库来源格式：[citation:文件名](kb:Collection名/文件名)
- 标题、文件名、Collection 和 URL 必须逐字复制工具结果提供的值，不得改写或编造
- 工具结果没有提供来源时不添加引用，明确说明依据不足
- 不要在回答末尾添加「### 参考资料」章节，来源列表由系统自动渲染
- 不得在正文中输出 evidence_id、document_id、segment_id 或 JSON 结构
</citations>"""
