/** 知识库 UI 通用格式化 */
import type { ChunkLocator } from '@/api/knowledgeBase'

const FILE_TYPE_LABEL: Record<string, string> = {
  pdf: 'PDF',
  docx: 'Word',
  doc: 'Word',
  xlsx: 'Excel',
  xls: 'Excel',
  csv: 'CSV',
  pptx: 'PPT',
  ppt: 'PPT',
  md: 'Markdown',
  markdown: 'Markdown',
  txt: '文本',
}

export function fileExtension(fileName: string): string {
  const idx = fileName.lastIndexOf('.')
  if (idx < 0) {
    return ''
  }
  return fileName.slice(idx + 1).toLowerCase()
}

export function fileTypeLabel(fileName: string): string {
  const ext = fileExtension(fileName)
  return FILE_TYPE_LABEL[ext] || (ext ? ext.toUpperCase() : '文件')
}

export function formatKbDate(dateStr: string | null | undefined): string {
  if (!dateStr) {
    return '—'
  }
  try {
    return new Date(dateStr).toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return dateStr
  }
}

export function shortHash(hash: string | null | undefined, len = 8): string {
  if (!hash) {
    return '—'
  }
  return hash.length > len * 2 ? `${hash.slice(0, len)}…` : hash
}

const ELEMENT_TYPE_LABEL: Record<string, string> = {
  text: '正文',
  title: '标题',
  table: '表格',
  image: '图片',
}

export function chunkElementLabel(type: string | null | undefined): string {
  return type ? ELEMENT_TYPE_LABEL[type] || type : '未分类'
}

export function formatChunkLocator(locator: ChunkLocator | null | undefined): string {
  if (!locator) {
    return '未提供来源位置'
  }
  if (locator.type === 'page') {
    const start = locator.page_start
    const end = locator.page_end
    if (start != null && end != null && end !== start) {
      return `第 ${start}–${end} 页`
    }
    return start != null ? `第 ${start} 页` : '页面位置'
  }
  if (locator.type === 'header') {
    return Array.isArray(locator.path) && locator.path.length
      ? locator.path.join(' / ')
      : '标题位置'
  }
  if (locator.type === 'char') {
    return locator.start != null && locator.end != null
      ? `字符 ${locator.start}–${locator.end}`
      : '字符位置'
  }
  if (locator.type === 'bbox') {
    return '版面坐标'
  }
  return locator.type
}

/** 知识库上传支持的文档格式（界面展示用） */
export const SUPPORTED_DOC_FORMATS = [
  { ext: 'PDF', desc: '版式/OCR 解析' },
  { ext: 'Word', desc: 'DOCX / DOC' },
  { ext: 'Excel', desc: 'XLSX / XLS / CSV' },
  { ext: 'PPT', desc: 'PPTX / PPT' },
  { ext: 'Markdown', desc: 'MD / TXT' },
] as const
