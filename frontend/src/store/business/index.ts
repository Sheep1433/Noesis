/**
 * Business Store
 *
 * 流式对话由 chat 页 useSSEStream 管理。
 */
import { defineStore } from 'pinia'

export interface BusinessState {
  qa_type: any
  task_id: any
  file_list: {
    source_file_key: string
    parse_file_key: string
    file_size: string
  }[]
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
    add_file(file_url: any) {
      this.file_list.push(file_url)
    },
    clear_file_list() {
      this.file_list = []
    },
    remove_file(source_file_key: string) {
      const index = this.file_list.findIndex(
        (file) => file.source_file_key === source_file_key,
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
