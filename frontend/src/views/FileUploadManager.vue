<script lang="tsx" setup>
import type { UploadFileInfo } from 'naive-ui'
import type { PropType } from 'vue'
import { deleteSessionAttachment, uploadSessionAttachment } from '@/api/chat'

const props = defineProps({
  /** kb：知识库 tmp；chat：会话附件 API（COMMON_QA） */
  uploadMode: {
    type: String as PropType<'kb' | 'chat'>,
    default: 'kb',
  },
  /** chat 模式上传前获取/创建 session_id */
  getSessionId: {
    type: Function as PropType<() => string>,
    default: undefined,
  },
})

// 全局存储
const businessStore = useBusinessStore()

// 在您的项目中添加类型扩展
interface ExtendedUploadFileInfo extends UploadFileInfo {
  error?: Error
  attachmentId?: string
}

// 定义模型，用于双向绑定上传文件列表
const pendingUploadFileInfoList = defineModel<ExtendedUploadFileInfo[]>({ default: () => [] })

const imageAccept = 'image/jpeg,image/png,image/webp,image/gif'

const uploadChatAttachment = async (fileInfo: ExtendedUploadFileInfo) => {
  const sessionId = props.getSessionId?.()
  if (!sessionId) {
    throw new Error('请先开始对话后再上传附件')
  }
  if (!fileInfo.file) {
    throw new Error('文件无效')
  }
  const data = await uploadSessionAttachment(sessionId, fileInfo.file)
  fileInfo.attachmentId = data.attachment_id
  businessStore.add_file({
    file_name: data.file_name,
    attachment_id: data.attachment_id,
    kind: data.kind as 'document' | 'image',
    virtual_path: data.virtual_path,
    preview_base64: null,
    artifact_url: data.artifact_url,
    source_file_key: data.file_name,
    parse_file_key: data.attachment_id,
  })
  if (data.parse_error) {
    window.$ModalMessage.warning(data.parse_error)
  }
}

const uploadKbDocument = async (fileInfo: ExtendedUploadFileInfo) => {
  const formData = new FormData()
  if (fileInfo.file) {
    formData.append('file', fileInfo.file)
  }
  const response = await fetch('/api/knowledge_base/collections/tmp/upload', {
    method: 'POST',
    body: formData,
  })
  const result = await response.json()
  if (result.success !== true) {
    throw new Error(result.message || '上传失败')
  }
  businessStore.add_file({
    file_name: result.file_name || fileInfo.name || 'file',
    attachment_id: result.file_name || '',
    kind: 'document',
    source_file_key: result.file_name,
    parse_file_key: result.file_name,
    file_size: '',
  })
  if (result.message === '文档已存在，无需重复上传') {
    window.$ModalMessage.warning('文档已存在，无需重复上传')
  }
}

// 处理文件上传的函数
const handleFileUpload = async (fileInfo: ExtendedUploadFileInfo) => {
  try {
    if (props.uploadMode === 'chat') {
      await uploadChatAttachment(fileInfo)
    } else {
      await uploadKbDocument(fileInfo)
    }
    const index = pendingUploadFileInfoList.value.findIndex((f) => f.id === fileInfo.id)
    if (index !== -1) {
      pendingUploadFileInfoList.value[index].status = 'finished'
      pendingUploadFileInfoList.value[index].percentage = 100
    }
    window.$ModalMessage.success('文件上传成功')
  } catch (error) {
    const index = pendingUploadFileInfoList.value.findIndex((f) => f.id === fileInfo.id)
    if (index !== -1) {
      pendingUploadFileInfoList.value[index].status = 'error'
      pendingUploadFileInfoList.value[index].error = error instanceof Error ? error : new Error(String(error))
    }
    window.$ModalMessage.error(`文件上传失败: ${error instanceof Error ? error.message : String(error)}`)
  }
}

const handleRemove = async (index: number) => {
  const fileInfo = pendingUploadFileInfoList.value[index]
  if (
    props.uploadMode === 'chat'
    && fileInfo?.status === 'finished'
    && fileInfo.attachmentId
    && props.getSessionId
  ) {
    try {
      await deleteSessionAttachment(props.getSessionId(), fileInfo.attachmentId)
    } catch {
      // 删除失败仍从 UI 移除
    }
    businessStore.remove_file(fileInfo.attachmentId)
  } else if (fileInfo?.status === 'finished') {
    const fileData = businessStore.file_list[index]
    if (fileData?.attachment_id) {
      businessStore.remove_file(fileData.attachment_id)
    } else if (fileData?.source_file_key) {
      businessStore.remove_file(fileData.source_file_key)
    }
  }
  pendingUploadFileInfoList.value.splice(index, 1)
}

// 上传附件 下拉菜单的选项
const options = computed(() => {
  const items = [
    {
      key: 'document',
      type: 'render',
      render() {
        return (
          <n-upload
            accept=".doc,.docx,.ppt,.pptx,.pdf,.txt,.xlsx,.csv,.md"
            default-upload={false}
            show-file-list={false}
            multiple={false}
            onChange={(res) => {
              pendingUploadFileInfoList.value.push(res.file)
              handleFileUpload(res.file)
            }}
          >
            <div class="px-4">
              <div
                flex="~ items-center gap-4"
                class="cursor-pointer px-12 py-4 hover:bg-primary/10 transition-all-300"
              >
                <span class="i-material-symbols:file-open-outline text-16" />
                <span>上传文档</span>
              </div>
            </div>
          </n-upload>
        )
      },
    },
  ]

  if (props.uploadMode === 'chat') {
    items.push({
      key: 'image',
      type: 'render',
      render() {
        return (
          <n-upload
            accept={imageAccept}
            default-upload={false}
            show-file-list={false}
            multiple={false}
            onChange={(res) => {
              pendingUploadFileInfoList.value.push(res.file)
              handleFileUpload(res.file)
            }}
          >
            <div class="px-4">
              <div
                flex="~ items-center gap-4"
                class="cursor-pointer px-12 py-4 hover:bg-primary/10 transition-all-300"
              >
                <span class="i-mdi:file-image-outline text-16" />
                <span>上传图片</span>
              </div>
            </div>
          </n-upload>
        )
      },
    })
  } else {
    items.push({
      key: 'image',
      type: 'render',
      render() {
        return (
          <div onClick={(e) => {
            e.stopPropagation()
            window.$ModalMessage.info('当前模式暂不支持图片上传')
          }}
          >
            <div class="px-4">
              <div
                flex="~ items-center gap-4"
                class="cursor-pointer px-12 py-4 hover:bg-primary/10 transition-all-300"
              >
                <span class="i-mdi:file-image-outline text-16" />
                <span>上传图片</span>
              </div>
            </div>
          </div>
        )
      },
    })
  }

  return items
})

const UploadWrapperItem = defineComponent({
  name: 'UploadWrapperItem',
  props: {
    fileInfo: {
      type: Object as PropType<UploadFileInfo>,
      default: () => null,
    },
  },
  emits: ['remove'],
  setup(props, { emit }) {
    const statusList = ref([
      { status: 'parsing', text: '解析中...', icon: 'i-svg-spinners:6-dots-rotate' },
      { status: 'failed', text: '解析失败', icon: 'i-carbon:error c-red' },
      { status: 'success', text: '解析完成', icon: 'i-carbon:checkmark' },
    ])

    const _status = computed(() => {
      if (props.fileInfo.status === 'finished') {
        if ((props.fileInfo as ExtendedUploadFileInfo).percentage === 100 && !(props.fileInfo as ExtendedUploadFileInfo).error) {
          return 'success'
        } else if ((props.fileInfo as ExtendedUploadFileInfo).error) {
          return 'failed'
        }
        return 'parsing'
      } else if (props.fileInfo.status === 'error') {
        return 'failed'
      }
      return 'parsing'
    })

    const isImage = computed(() => props.fileInfo.type?.includes('image'))
    const fileName = computed(() => props.fileInfo.name || '')
    const previewImageUrl = ref('')

    watchEffect(() => {
      const file = props.fileInfo.file
      if (file && isImage.value) {
        previewImageUrl.value = URL.createObjectURL(file)
      }
    })

    const currentStatus = computed(() => statusList.value.find((item) => item.status === _status.value))

    const fileTypeIconMap = ref({
      xlsx: 'i-vscode-icons:file-type-excel2',
      xls: 'i-vscode-icons:file-type-excel2',
      csv: 'i-vscode-icons:file-type-excel2',
      docx: 'i-vscode-icons:file-type-word',
      doc: 'i-vscode-icons:file-type-word',
      pdf: 'i-vscode-icons:file-type-pdf2',
      pptx: 'i-vscode-icons:file-type-powerpoint',
      ppt: 'i-vscode-icons:file-type-powerpoint',
      md: 'i-vscode-icons:file-type-markdown',
    })

    const fileIcon = computed(() => {
      const fileExtension = fileName.value.split('.').pop()?.toLowerCase()
      return fileTypeIconMap.value[fileExtension as keyof typeof fileTypeIconMap.value]
    })

    return {
      isImage,
      previewImageUrl,
      fileName,
      currentStatus,
      fileIcon,
      removeFile: () => emit('remove'),
    }
  },
  render() {
    return (
      <div
        class="relative w-200 px-16 py-5 b b-solid b-bgcolor rounded-8 group transition-all-300"
        flex="~ gap-5 items-center"
      >
        <div class="absolute z-1 top--9 right--9 group-hover:opacity-100 opacity-0 transition-all-300">
          <div
            class="text-20 c-info cursor-pointer i-famicons:remove-circle-outline transition-all-300 hover:c-primary"
            onClick={this.removeFile}
          >
          </div>
        </div>
        <div class="size-30">
          {
            this.isImage
              ? (
                  <img src={this.previewImageUrl} class="size-full object-contain" />
                )
              : (
                  <div class={[this.fileIcon, 'size-full opacity-80']}></div>
                )
          }
        </div>
        <div flex="1 ~ col gap-2" class="min-w-0 text-13 overflow-x-hidden">
          <n-ellipsis tooltip>
            {{
              default: () => this.fileName,
              tooltip: () => this.fileName,
            }}
          </n-ellipsis>
          <div flex="~ gap-3 items-center" class="text-[#999]">
            <span class={['text-12', this.currentStatus?.icon]}></span>
            <span class="text-11">{this.currentStatus?.text}</span>
          </div>
        </div>
      </div>
    )
  },
})

defineExpose({
  options,
  pendingUploadFileInfoList,
  UploadWrapperItem,
})
</script>

<template>
  <div>
    <div
      v-if="pendingUploadFileInfoList.length"
      class="upload-wrapper-list"
    >
      <UploadWrapperItem
        v-for="(pendingUploadFileInfo, index) in pendingUploadFileInfoList"
        :key="pendingUploadFileInfo.id"
        :file-info="pendingUploadFileInfo"
        @remove="() => handleRemove(index)"
      />
    </div>
  </div>
</template>

<style lang="scss" scoped>
.upload-wrapper-list {
  --at-apply: flex flex-wrap gap-10 items-center;
  --at-apply: pb-12;
}
</style>
