/** 与后端 CHAT_ATTACHMENT_REF 一致 */
import { KB_FILE_DICT_REF } from '@/config/knowledge'

export const CHAT_ATTACHMENT_REF = '__CHAT_ATTACHMENT__'

export { KB_FILE_DICT_REF }

export function chatAttachmentRef(attachmentId: string): string {
  return `${CHAT_ATTACHMENT_REF}:${attachmentId}`
}

export function buildFileDict(
  files: Array<{ file_name: string, attachment_id: string }>,
): Record<string, string> | undefined {
  if (!files.length) {
    return undefined
  }
  const dict: Record<string, string> = {}
  for (const f of files) {
    if (!f.file_name || !f.attachment_id) {
      continue
    }
    if (f.attachment_id === KB_FILE_DICT_REF) {
      dict[f.file_name] = KB_FILE_DICT_REF
    } else {
      dict[f.file_name] = chatAttachmentRef(f.attachment_id)
    }
  }
  return Object.keys(dict).length ? dict : undefined
}
