/**
 * 测试助手：需求文档须写入「需求向量库」collection（与后端 REQUIREMENT_DOCS_COLLECTION / 索引召回一致）。
 * 默认 `requirement_docs`；可用 `VITE_TEST_CASE_UPLOAD_COLLECTION` 覆盖上传目标。
 * 参考用例库集合 `test_case_docs` 由运维自行入库，不在此常量内。
 */
export const TEST_CASE_UPLOAD_COLLECTION = (
  import.meta.env.VITE_TEST_CASE_UPLOAD_COLLECTION as string | undefined
)?.trim() || 'requirement_docs'

/** 与后端 TEST_CASE_KB_FILE_DICT_REF 一致：file_dict 值为该常量时从知识库按 key(file_name) 拉整篇 */
export const KB_FILE_DICT_REF = '__FROM_KB__'