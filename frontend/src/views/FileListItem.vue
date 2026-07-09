<script setup lang="ts">
import type { PropType } from 'vue'
import type { AttachmentImageSources } from '@/utils/attachmentImage'
import { onBeforeUnmount, watch } from 'vue'
import {
  loadAttachmentImageSources,
  revokeAttachmentImageSources,
} from '@/utils/attachmentImage'
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
      source_file_key?: string
      parse_file_key?: string
      file_size?: string
    }>,
    required: true,
  },
})

const displayName = computed(() => {
  return props.file.file_name || getFileBaseName(props.file.source_file_key || '')
})

const isImage = computed(() => {
  return isImageAttachment(props.file.kind, displayName.value)
})

const imageLoading = ref(false)
const imageSources = ref<AttachmentImageSources | null>(null)

async function resolveImageSources() {
  revokeAttachmentImageSources(imageSources.value)
  imageSources.value = null

  if (!isImage.value) {
    imageLoading.value = false
    return
  }

  imageLoading.value = true
  try {
    imageSources.value = await loadAttachmentImageSources(props.file)
  } finally {
    imageLoading.value = false
  }
}

watch(
  () => [props.file.preview_base64, props.file.artifact_url, props.file.kind, displayName.value],
  () => {
    void resolveImageSources()
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  revokeAttachmentImageSources(imageSources.value)
})

const iconClass = computed(() => {
  return getFileTypeIconClass(props.file.source_file_key || displayName.value)
})
</script>

<template>
  <!-- 图片：仅缩略图，不展示文件名 -->
  <div
    v-if="isImage"
    class="relative size-96 shrink-0 rounded-8 overflow-hidden bg-[#f5f5f5] b b-solid b-bgcolor"
  >
    <div
      v-if="imageLoading"
      class="size-full flex items-center justify-center"
    >
      <n-spin size="small" />
    </div>
    <n-image
      v-else-if="imageSources"
      width="96"
      height="96"
      :src="imageSources.thumb"
      :preview-src="imageSources.full"
      object-fit="cover"
      class="size-full cursor-zoom-in"
      :alt="displayName"
      :show-toolbar="true"
    />
    <div
      v-else
      class="size-full flex items-center justify-center text-[#bbb]"
    >
      <span class="i-mdi:image-off-outline text-24"></span>
    </div>
  </div>

  <!-- 文档：保留文件名卡片 -->
  <div
    v-else
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
        {{ displayName }}
      </n-ellipsis>

      <div
        v-if="props.file.file_size"
        flex="~ gap-3 items-center"
        class="text-[#999]"
      >
        <span class="text-11">{{ props.file.file_size }}</span>
      </div>
    </div>
  </div>
</template>
