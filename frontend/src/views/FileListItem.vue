<!-- src/components/FileListItem.vue -->
<script setup lang="ts">
import type { PropType } from 'vue'

// 定义 props
const props = defineProps({
  file: {
    type: Object as PropType<{ source_file_key: string, parse_file_key: string, file_size: string }>,
    required: true,
  },
})

// 提取文件名
const getFileName = (fileKey: string) => {
  if (!fileKey) {
    return ''
  }
  return fileKey.split('/').pop() || fileKey
}

// 计算文件名
const fileName = computed(() => getFileName(props.file.source_file_key))

// 获取文件图标类名
const getFileIconClass = (fileKey: string) => {
  if (!fileKey) {
    return ''
  }
  const name = getFileName(fileKey)
  const extension = name.split('.').pop()?.toLowerCase() || ''

  const fileTypeIconMap: Record<string, string> = {
    xlsx: 'i-vscode-icons:file-type-excel2',
    xls: 'i-vscode-icons:file-type-excel2',
    csv: 'i-vscode-icons:file-type-excel2',
    docx: 'i-vscode-icons:file-type-word',
    doc: 'i-vscode-icons:file-type-word',
    pdf: 'i-vscode-icons:file-type-pdf2',
    pptx: 'i-vscode-icons:file-type-powerpoint',
    ppt: 'i-vscode-icons:file-type-powerpoint',
  }

  return fileTypeIconMap[extension] || 'i-material-symbols:file-open-outline'
}

// 计算图标类名
const iconClass = computed(() => getFileIconClass(props.file.source_file_key))
</script>

<template>
  <div
    class="relative w-180 px-16 py-5 b b-solid b-bgcolor rounded-8 transition-all-300 bg-white h-45"
    flex="~ gap-5 items-center"
  >
    <div class="size-30 ml--8">
      <div
        :class="[
          iconClass,
          'size-full opacity-80',
        ]"
      ></div>
    </div>
    <div
      flex="1 ~ col gap-2"
      class="min-w-0 text-13 overflow-x-hidden"
    >
      <n-ellipsis
        :style="{
          'font-weight': '500',
          'font-size': '14px',
          'font-family': `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif, 'Apple Color Emoji', 'Segoe UI Emoji'`,
        }"
      >
        {{ fileName }}
      </n-ellipsis>

      <div
        flex="~ gap-3 items-center"
        class="text-[#999]"
      >
        <span class="text-11">{{ props.file.file_size }}</span>
      </div>
    </div>
  </div>
</template>
