/**
 * Business Store
 *
 * 流式对话由 chat 页 useSSEStream 管理。
 */
import { defineStore } from 'pinia'

export interface ChatAttachmentItem {
  file_name: string
  attachment_id: string
  kind: 'document' | 'image'
  virtual_path?: string
  preview_base64?: string | null
  artifact_url?: string | null
  /** 兼容历史 FileListItem 展示 */
  source_file_key?: string
  parse_file_key?: string
  file_size?: string
}

export interface BusinessState {
  qa_type: any
  task_id: any
  file_list: ChatAttachmentItem[]
  todos: Array<{ content: string, status: 'pending' | 'in_progress' | 'completed' }>
}

export const useBusinessStore = defineStore('business-store', {
  state: (): BusinessState => {
    return {
      qa_type: 'COMMON_QA',
      file_list: [],
      task_id: '',
      todos: [],
    }
  },
  actions: {
    update_qa_type(qa_type) {
      this.qa_type = qa_type
    },
    add_file(file_item: ChatAttachmentItem) {
      this.file_list.push(file_item)
    },
    clear_file_list() {
      this.file_list = []
    },
    remove_file(idOrKey: string) {
      const index = this.file_list.findIndex(
        (file) => file.attachment_id === idOrKey || file.source_file_key === idOrKey,
      )
      if (index !== -1) {
        this.file_list.splice(index, 1)
      }
    },
    update_task_id(task_id) {
      this.task_id = task_id
    },
    clear_task_id() {
      this.task_id = ''
    },
    update_todos(todos: Array<{ content: string, status: 'pending' | 'in_progress' | 'completed' }>) {
      this.todos = todos
    },
  },
})
