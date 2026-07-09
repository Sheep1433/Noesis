/** 支持内联预览的纯文本 / 代码扩展名 */
export const TEXT_PREVIEW_EXTENSIONS = [
  '.md',
  '.markdown',
  '.txt',
  '.json',
  '.yaml',
  '.yml',
  '.csv',
  '.ts',
  '.tsx',
  '.js',
  '.jsx',
  '.py',
  '.html',
  '.css',
  '.xml',
  '.log',
  '.sh',
  '.toml',
  '.ini',
  '.sql',
] as const

/** 支持内联预览的图片扩展名 */
export const IMAGE_PREVIEW_EXTENSIONS = [
  '.png',
  '.jpg',
  '.jpeg',
  '.gif',
  '.webp',
  '.svg',
] as const

export type FilePreviewKind = 'text' | 'image' | 'unsupported'

const EXT_TO_CODE_LANGUAGE: Record<string, string> = {
  '.md': 'markdown',
  '.markdown': 'markdown',
  '.json': 'json',
  '.yaml': 'yaml',
  '.yml': 'yaml',
  '.csv': 'csv',
  '.ts': 'typescript',
  '.tsx': 'typescript',
  '.js': 'javascript',
  '.jsx': 'javascript',
  '.py': 'python',
  '.html': 'html',
  '.css': 'css',
  '.xml': 'xml',
  '.sh': 'shell',
  '.sql': 'sql',
  '.toml': 'toml',
  '.ini': 'ini',
}

export function getPathExtension(path: string): string {
  const lower = path.toLowerCase()
  const idx = lower.lastIndexOf('.')
  return idx >= 0 ? lower.slice(idx) : ''
}

export function isTextPreviewPath(path: string): boolean {
  const ext = getPathExtension(path)
  return (TEXT_PREVIEW_EXTENSIONS as readonly string[]).includes(ext)
}

export function isImagePreviewPath(path: string): boolean {
  const ext = getPathExtension(path)
  return (IMAGE_PREVIEW_EXTENSIONS as readonly string[]).includes(ext)
}

export function isMarkdownPreviewPath(path: string): boolean {
  const ext = getPathExtension(path)
  return ext === '.md' || ext === '.markdown'
}

export function isInlinePreviewPath(path: string): boolean {
  return isTextPreviewPath(path) || isImagePreviewPath(path)
}

export function getFilePreviewKind(path: string): FilePreviewKind {
  if (isTextPreviewPath(path)) {
    return 'text'
  }
  if (isImagePreviewPath(path)) {
    return 'image'
  }
  return 'unsupported'
}

export function getCodeLanguage(path: string): string {
  return EXT_TO_CODE_LANGUAGE[getPathExtension(path)] ?? 'plaintext'
}

/** 拆分 Markdown 文件头部的 YAML frontmatter（`---` 包裹）与正文 */
export function splitYamlFrontmatter(content: string): { frontmatter: string | null; body: string } {
  const normalized = content.replace(/^\uFEFF/, '')
  const match = normalized.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/)
  if (!match) {
    return { frontmatter: null, body: content }
  }
  return { frontmatter: match[1], body: normalized.slice(match[0].length) }
}

const FILE_TYPE_ICON_MAP: Record<string, string> = {
  xlsx: 'i-vscode-icons:file-type-excel2',
  xls: 'i-vscode-icons:file-type-excel2',
  csv: 'i-vscode-icons:file-type-excel2',
  docx: 'i-vscode-icons:file-type-word',
  doc: 'i-vscode-icons:file-type-word',
  pdf: 'i-vscode-icons:file-type-pdf2',
  pptx: 'i-vscode-icons:file-type-powerpoint',
  ppt: 'i-vscode-icons:file-type-powerpoint',
  md: 'i-vscode-icons:file-type-markdown',
  markdown: 'i-vscode-icons:file-type-markdown',
}

export function getFileBaseName(path: string): string {
  if (!path) {
    return ''
  }
  return path.split('/').pop() || path
}

/** 列表卡片用：按文件名返回 UnoCSS 图标类名 */
export function getFileTypeIconClass(fileName: string): string {
  const base = getFileBaseName(fileName)
  const extension = base.split('.').pop()?.toLowerCase() || ''
  return FILE_TYPE_ICON_MAP[extension] || 'i-material-symbols:file-open-outline'
}

/** 附件 kind 或文件名判断是否为图片 */
export function isImageAttachment(
  kind: string | undefined,
  fileName: string,
): boolean {
  return kind === 'image' || isImagePreviewPath(fileName)
}

/** 本地 File 对象判断是否为图片（上传队列用） */
export function isImageUploadFile(file: File): boolean {
  return file.type.startsWith('image/') || isImagePreviewPath(file.name)
}

/** 附件缩略图 data URL（preview_base64 为 JPEG） */
export function attachmentPreviewDataUrl(previewBase64: string): string {
  return `data:image/jpeg;base64,${previewBase64}`
}
