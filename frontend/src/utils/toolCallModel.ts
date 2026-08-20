/**
 * 工具调用展示模型：variant 分类 + 一行摘要推导。
 *
 * 按工具名映射到 variant，
 * 每个 variant 有专属标题、summary 取参优先级。纯函数，无副作用。
 */

/** 工具调用行 variant，决定标题/图标/展开卡片类型。 */
export type ToolRowVariant = 'search' | 'read' | 'bash' | 'write' | 'edit' | 'todo' | 'others'

/** 各 variant 的固定标题（header 左侧）。 */
export const VARIANT_TITLES: Record<ToolRowVariant, string> = {
  search: '搜索',
  read: '读取',
  bash: 'Bash',
  write: '写入',
  edit: '编辑',
  todo: '待办',
  others: '工具',
}

/** 工具名专属标题（优先于 variant 标题，避免歧义：grep/glob/web_search 区分）。 */
const TOOL_TITLES: Record<string, string> = {
  grep: '代码搜索',
  glob: '文件查找',
  grep_attachment: '附件搜索',
  web_search: '网页搜索',
  web_fetch: '网页抓取',
  search_knowledge_base: '知识库搜索',
  search_memory: '记忆搜索',
  read_file: '读取文件',
  read_attachment: '读取附件',
  get_knowledge_document: '读取文档',
  write_file: '写入文件',
  edit_file: '编辑文件',
  execute: 'Bash',
}

/** 取工具的展示标题：优先专属标题，否则 variant 标题。 */
export function toolTitle(toolName: string, variant: ToolRowVariant): string {
  return TOOL_TITLES[toolName] ?? VARIANT_TITLES[variant]
}

/** 工具名 → variant 映射。未命中归 others。 */
const TOOL_VARIANTS: Record<string, ToolRowVariant> = {
  // deepagents FilesystemMiddleware
  execute: 'bash',
  read_file: 'read',
  write_file: 'write',
  edit_file: 'edit',
  glob: 'search',
  grep: 'search',
  ls: 'read',
  // Noesis 自有工具
  read_attachment: 'read',
  grep_attachment: 'search',
  web_search: 'search',
  web_fetch: 'read',
  search_knowledge_base: 'search',
  list_knowledge_bases: 'others',
  get_knowledge_document: 'read',
  search_memory: 'search',
  get_memory_source: 'others',
  ask_user: 'others',
  write_todos: 'todo',
  // 子代理 / 元工具
  task: 'others',
  tool_search: 'others',
}

/** 各 variant 的 summary 取参字段优先级（从前往后取第一个非空字符串）。 */
const SUMMARY_KEYS: Record<ToolRowVariant, readonly string[]> = {
  // bash 优先取 description（为将来加意图字段预留），其次 command
  bash: ['description', 'command', 'cmd', 'shell', 'script'],
  read: ['path', 'file_path', 'url'],
  search: ['query', 'pattern', 'url'],
  write: ['path', 'file_path'],
  edit: ['path', 'file_path'],
  todo: [],
  others: [],
}

/** 一行摘要的截断上限。 */
const HEADER_SUMMARY_MAX = 240

/** 把任意字符串压成一行并截断。 */
function truncateOneLine(s: string, max: number = HEADER_SUMMARY_MAX): string {
  const t = s.replace(/\s+/g, ' ').trim()
  if (t.length <= max) {
    return t
  }
  return `${t.slice(0, max - 1)}…`
}

/** 取 args 第一行（多行命令/路径只展示首行）。 */
function firstLine(text: string): string {
  const nl = text.indexOf('\n')
  return nl === -1 ? text : text.slice(0, nl)
}

/** 按优先级 keys 从 args 取第一个非空字符串。 */
function pickString(args: Record<string, unknown>, keys: readonly string[]): string | undefined {
  for (const key of keys) {
    const v = args[key]
    if (typeof v === 'string' && v !== '') {
      return v
    }
  }
  return undefined
}

/** 解析工具参数；非 JSON 字符串时回退到 undefined。 */
function parseArgs(argsRaw: unknown): Record<string, unknown> | undefined {
  if (argsRaw == null || argsRaw === '') {
    return undefined
  }
  if (typeof argsRaw === 'object' && !Array.isArray(argsRaw)) {
    return argsRaw as Record<string, unknown>
  }
  if (typeof argsRaw === 'string') {
    try {
      const parsed = JSON.parse(argsRaw)
      if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>
      }
    } catch {
      return undefined
    }
  }
  return undefined
}

/** 按工具名分类到 variant。 */
export function classifyTool(toolName: string): ToolRowVariant {
  return TOOL_VARIANTS[toolName] ?? 'others'
}

/**
 * 推导收起态的一行摘要。
 *
 * 按 variant 的 SUMMARY_KEYS 优先级从 input 取字段；取不到则回退到 input 任意
 * 字符串值；再取不到回退到原始 input 字符串。bash 类优先取 description（意图字段），
 * 当前 Noesis 无该字段会自动落到 command。
 */
export function deriveSummary(variant: ToolRowVariant, input: unknown): string {
  const parsed = parseArgs(input)
  if (parsed === undefined) {
    // 非 JSON 对象：字符串取首行，否则空
    if (typeof input === 'string') {
      return truncateOneLine(firstLine(input))
    }
    return ''
  }
  const keys = SUMMARY_KEYS[variant]
  const picked = pickString(parsed, keys)
  if (picked !== undefined) {
    return truncateOneLine(firstLine(picked))
  }
  // others 或命中字段全空：取任意字符串字段
  for (const v of Object.values(parsed)) {
    if (typeof v === 'string' && v !== '') {
      return truncateOneLine(firstLine(v))
    }
  }
  return ''
}
