"""Shared typed citation prompt contract."""

CITATION_EXTENSION = """<citations>
知识库搜索、web_search 与 web_fetch 结果中的 evidence_id 只用于 typed citation binding。正文不得输出 evidence_id、[[source:...]]、[ID:n]、文件名角标或其它引用 marker。
若运行时要求 structured answer，严格返回 segments[{text,cited_evidence_ids}]：每段只绑定直接支持它的 evidence_id；无依据时使用空数组。不得引用工具结果之外的 evidence_id。
</citations>"""
