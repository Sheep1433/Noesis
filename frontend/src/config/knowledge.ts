/**
 * 聊天页知识库即时上传的目标 collection。
 * 默认 `requirement_docs`；可用 `VITE_KB_UPLOAD_COLLECTION` 覆盖上传目标。
 */
export const KB_UPLOAD_COLLECTION = (
  import.meta.env.VITE_KB_UPLOAD_COLLECTION as string | undefined
)?.trim() || 'requirement_docs'

/** file_dict 值为该常量时表示文档在知识库中，按 key(file_name) 整篇引用 */
export const KB_FILE_DICT_REF = '__FROM_KB__'
