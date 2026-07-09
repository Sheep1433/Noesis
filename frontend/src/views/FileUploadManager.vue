<script lang="tsx" setup>
import type { UploadFileInfo } from 'naive-ui'
import type { PropType } from 'vue'
import type { ChatAttachmentItem } from '@/store/business'
import { deleteSessionAttachment, uploadSessionAttachment } from '@/api/chat'
import { uploadDocument } from '@/api/knowledgeBase'
import { KB_FILE_DICT_REF, TEST_CASE_UPLOAD_COLLECTION } from '@/config/knowledge'
import { CHAT_MAX_FILES_PER_MESSAGE } from '@/config/chat'
import { getFileTypeIconClass, isImagePreviewPath, isImageUploadFile } from '@/utils/filePreview'

const props = defineProps({
  /** kb：测试用例等场景写入 requirement_docs；chat：会话附件 API */
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

const emit = defineEmits<{
  chatImageUploaded: []
}>()

// 全局存储
const businessStore = useBusinessStore()

// 在您的项目中添加类型扩展
interface ExtendedUploadFileInfo extends UploadFileInfo {
  error?: Error
  attachmentId?: string
}

const deferUpload = computed(() => props.uploadMode === 'chat')

// 定义模型，用于双向绑定上传文件列表
const pendingUploadFileInfoList = defineModel<ExtendedUploadFileInfo[]>({ default: () => [] })

const imageAccept = 'image/jpeg,image/png,image/webp,image/gif'
const documentExtensions = ['doc', 'docx', 'ppt', 'pptx', 'pdf', 'txt', 'xlsx', 'csv', 'md']

function isDocumentFile(file: File): boolean {
  const ext = file.name.split('.').pop()?.toLowerCase()
  return !!ext && documentExtensions.includes(ext)
}

function isImageFile(file: File): boolean {
  return isImageUploadFile(file)
}

function createUploadFileInfo(file: File): ExtendedUploadFileInfo {
  const name = file.name.trim()
    || (isImageFile(file) ? `paste-${Date.now()}.png` : `file-${Date.now()}`)
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
    name,
    status: 'pending',
    file,
    type: file.type,
    percentage: 0,
  }
}

function enqueueFiles(raw: File[] | FileList) {
  const files = Array.from(raw)
  if (!files.length) {
    return
  }

  if (props.uploadMode === 'chat') {
    const remaining = CHAT_MAX_FILES_PER_MESSAGE - pendingUploadFileInfoList.value.length
    if (remaining <= 0) {
      window.$ModalMessage.warning(`单条消息最多 ${CHAT_MAX_FILES_PER_MESSAGE} 个附件`)
      return
    }
    if (files.length > remaining) {
      window.$ModalMessage.warning(
        `单条消息最多 ${CHAT_MAX_FILES_PER_MESSAGE} 个附件，已忽略超出部分`,
      )
    }
  }

  const filesToAdd = props.uploadMode === 'chat' ? files.slice(0, Math.max(0, CHAT_MAX_FILES_PER_MESSAGE - pendingUploadFileInfoList.value.length)) : files

  for (const file of filesToAdd) {
    const doc = isDocumentFile(file)
    const img = isImageFile(file)
    if (!doc && !img) {
      window.$ModalMessage.warning(`不支持的文件类型：${file.name || file.type || '未知文件'}`)
      continue
    }
    if (img && props.uploadMode !== 'chat') {
      window.$ModalMessage.info('当前模式暂不支持图片上传')
      continue
    }

    const fileInfo = createUploadFileInfo(file)
    pendingUploadFileInfoList.value.push(fileInfo)
    if (deferUpload.value) {
      continue
    }
    handleFileUpload(fileInfo)
  }
}

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
    preview_base64: data.preview_base64 ?? null,
    artifact_url: data.artifact_url ?? null,
    source_file_key: data.file_name,
    parse_file_key: data.attachment_id,
    file_size: '',
  })
  if (data.parse_error) {
    window.$ModalMessage.warning(data.parse_error)
  }
  if (data.kind === 'image') {
    emit('chatImageUploaded')
  }
}

const uploadKbDocument = async (fileInfo: ExtendedUploadFileInfo) => {
  if (!fileInfo.file) {
    throw new Error('文件无效')
  }
  const result = await uploadDocument(TEST_CASE_UPLOAD_COLLECTION, fileInfo.file)
  if (!result.success) {
    throw new Error(result.message || '上传失败')
  }
  const fileName = result.file_name || fileInfo.name || 'file'
  businessStore.add_file({
    file_name: fileName,
    attachment_id: KB_FILE_DICT_REF,
    kind: 'document',
    source_file_key: fileName,
    parse_file_key: fileName,
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
    if (!deferUpload.value) {
      window.$ModalMessage.success('文件上传成功')
    }
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

async function uploadAllPendingFiles(): Promise<ChatAttachmentItem[]> {
  if (!deferUpload.value) {
    throw new Error('uploadAllPendingFiles 仅用于 chat 模式')
  }
  const sessionId = props.getSessionId?.()
  if (!sessionId) {
    throw new Error('会话无效')
  }
  businessStore.clear_file_list()
  const uploaded: ChatAttachmentItem[] = []

  for (const fileInfo of [...pendingUploadFileInfoList.value]) {
    if (!fileInfo.file) {
      continue
    }
    fileInfo.status = 'uploading'
    try {
      await uploadChatAttachment(fileInfo)
      const index = pendingUploadFileInfoList.value.findIndex((f) => f.id === fileInfo.id)
      if (index !== -1) {
        pendingUploadFileInfoList.value[index].status = 'finished'
        pendingUploadFileInfoList.value[index].percentage = 100
      }
      const last = businessStore.file_list[businessStore.file_list.length - 1]
      if (last) {
        uploaded.push(last)
      }
    } catch (error) {
      const index = pendingUploadFileInfoList.value.findIndex((f) => f.id === fileInfo.id)
      if (index !== -1) {
        pendingUploadFileInfoList.value[index].status = 'error'
        pendingUploadFileInfoList.value[index].error = error instanceof Error ? error : new Error(String(error))
      }
      throw error
    }
  }
  return uploaded
}

function clearQueue() {
  pendingUploadFileInfoList.value = []
  businessStore.clear_file_list()
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
            multiple
            onChange={(res) => {
              if (res.file.file) {
                enqueueFiles([res.file.file])
              }
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
            multiple
            onChange={(res) => {
              if (res.file.file) {
                enqueueFiles([res.file.file])
              }
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
    deferUpload: {
      type: Boolean,
      default: false,
    },
  },
  emits: ['remove'],
  setup(props, { emit }) {
    const statusList = ref([
      { status: 'queued', text: '待发送', icon: 'i-carbon:document-blank' },
      { status: 'parsing', text: '上传中...', icon: 'i-svg-spinners:6-dots-rotate' },
      { status: 'failed', text: '失败', icon: 'i-carbon:error c-red' },
      { status: 'success', text: '已就绪', icon: 'i-carbon:checkmark' },
    ])

    const _status = computed(() => {
      if (props.fileInfo.status === 'uploading') {
        return 'parsing'
      }
      if (props.deferUpload && props.fileInfo.status === 'pending') {
        return 'queued'
      }
      if (props.fileInfo.status === 'finished') {
        if ((props.fileInfo as ExtendedUploadFileInfo).percentage === 100 && !(props.fileInfo as ExtendedUploadFileInfo).error) {
          return props.deferUpload ? 'success' : 'success'
        } else if ((props.fileInfo as ExtendedUploadFileInfo).error) {
          return 'failed'
        }
        return 'parsing'
      } else if (props.fileInfo.status === 'error') {
        return 'failed'
      }
      return props.deferUpload ? 'queued' : 'parsing'
    })

    const isImage = computed(() => {
      const file = props.fileInfo.file
      if (file) {
        return isImageUploadFile(file)
      }
      return isImagePreviewPath(props.fileInfo.name || '')
    })
    const fileName = computed(() => props.fileInfo.name || '')
    const previewImageUrl = ref('')

    watchEffect((onCleanup) => {
      const file = props.fileInfo.file
      if (file && isImageUploadFile(file)) {
        const url = URL.createObjectURL(file)
        previewImageUrl.value = url
        onCleanup(() => URL.revokeObjectURL(url))
      } else {
        previewImageUrl.value = ''
      }
    })

    const currentStatus = computed(() => statusList.value.find((item) => item.status === _status.value))

    const showStatus = computed(() => {
      const status = _status.value
      if (status === 'failed' || status === 'parsing') {
        return true
      }
      // chat 模式 pending / 已就绪不展示状态行，避免「待发送」误解
      if (props.deferUpload) {
        return false
      }
      return status === 'success'
    })

    const deferTooltip = computed(() => {
      if (!props.deferUpload || _status.value !== 'queued') {
        return ''
      }
      return '发送消息时会上传'
    })

    const fileIcon = computed(() => getFileTypeIconClass(fileName.value))

    const removeButton = () => (
      <div class="absolute z-2 top--6 right--6 group-hover:opacity-100 opacity-0 transition-all-300">
        <div
          class="text-18 c-info cursor-pointer i-famicons:remove-circle-outline transition-all-300 hover:c-primary bg-white rounded-50%"
          onClick={(e: Event) => {
            e.stopPropagation()
            emit('remove')
          }}
        >
        </div>
      </div>
    )

    return {
      isImage,
      previewImageUrl,
      fileName,
      currentStatus,
      showStatus,
      deferTooltip,
      fileIcon,
      removeFile: () => emit('remove'),
      removeButton,
    }
  },
  render() {
    if (this.isImage && this.previewImageUrl) {
      const imageChip = (
        <div
          class={[
            'relative size-52 shrink-0 b b-solid b-bgcolor rounded-8 overflow-hidden group transition-all-300',
            this.currentStatus?.status === 'failed' ? 'b-red-400' : '',
            this.currentStatus?.status === 'parsing' ? 'opacity-70' : '',
          ]}
        >
          {this.removeButton()}
          <n-image
            width={52}
            height={52}
            src={this.previewImageUrl}
            previewSrc={this.previewImageUrl}
            objectFit="cover"
            class="size-full cursor-pointer"
            alt={this.fileName}
          />
          {this.currentStatus?.status === 'parsing'
            ? (
                <div class="absolute inset-0 flex items-center justify-center bg-black/20 pointer-events-none">
                  <span class="text-16 c-white i-svg-spinners:6-dots-rotate"></span>
                </div>
              )
            : null}
        </div>
      )

      if (this.deferTooltip) {
        return (
          <n-tooltip trigger="hover">
            {{
              trigger: () => imageChip,
              default: () => this.deferTooltip,
            }}
          </n-tooltip>
        )
      }
      return imageChip
    }

    const docChip = (
      <div
        class={[
          'relative w-200 px-16 py-5 b b-solid b-bgcolor rounded-8 group transition-all-300',
          this.currentStatus?.status === 'failed' ? 'b-red-400' : '',
        ]}
        flex="~ gap-5 items-center"
      >
        {this.removeButton()}
        <div class="size-40 shrink-0">
          <div class={[this.fileIcon, 'size-full opacity-80']}></div>
        </div>
        <div flex="1 ~ col gap-2" class="min-w-0 text-13 overflow-x-hidden">
          <n-ellipsis tooltip>
            {{
              default: () => this.fileName,
              tooltip: () => this.deferTooltip || this.fileName,
            }}
          </n-ellipsis>
          {this.showStatus
            ? (
                <div flex="~ gap-3 items-center" class="text-[#999]">
                  <span class={['text-12', this.currentStatus?.icon]}></span>
                  <span class="text-11">{this.currentStatus?.text}</span>
                </div>
              )
            : null}
        </div>
      </div>
    )

    return docChip
  },
})

defineExpose({
  options,
  pendingUploadFileInfoList,
  UploadWrapperItem,
  enqueueFiles,
  uploadAllPendingFiles,
  clearQueue,
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
        :defer-upload="deferUpload"
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
