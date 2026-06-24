<script setup lang="ts">
import type { PropType } from 'vue'
import {
  getFileBaseName,
  getFileTypeIconClass,
  isImageAttachment,
} from '@/utils/filePreview'

const props = defineProps({
  file: {
    type: Object as PropType<{
      file_name?: string
      kind?: 'document' | 'image'
      artifact_url?: string | null
      preview_base64?: string | null
      source_file_key: string
      parse_file_key: string
      file_size: string
    }>,
    required: true,
  },
})

const displayName = computed(() => {
  return props.file.file_name || getFileBaseName(props.file.source_file_key)
})

const isImage = computed(() => {
  return isImageAttachment(props.file.kind, displayName.value)
})

const imageSrc = computed(() => {
  if (props.file.artifact_url) {
    return props.file.artifact_url
  }
  if (props.file.preview_base64) {
    return `data:image/png;base64,${props.file.preview_base64}`
  }
  return ''
})

const iconClass = computed(() => {
  return getFileTypeIconClass(props.file.source_file_key || displayName.value)
})
</script>

<template>
  <div
    class="relative w-180 px-16 py-5 b b-solid b-bgcolor rounded-8 transition-all-300 bg-white h-45"
    flex="~ gap-5 items-center"
  >
    <div class="size-30 ml--8">
      <img
        v-if="isImage && imageSrc"
        :src="imageSrc"
        class="size-full object-contain rounded-4"
        alt=""
      >
      <div
        v-else
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
        {{ displayName }}
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
