<script setup lang="ts">
import { NCode, NSpin } from 'naive-ui'
import { computed } from 'vue'
import { getCodeLanguage, isImagePreviewPath } from '@/utils/filePreview'

const props = withDefaults(defineProps<{
  path: string
  content?: string
  imageSrc?: string
  loading?: boolean
  density?: 'compact' | 'comfortable'
  showPath?: boolean
}>(), {
  content: '',
  imageSrc: '',
  loading: false,
  density: 'comfortable',
  showPath: true,
})

const isImage = computed(() => isImagePreviewPath(props.path) && !!props.imageSrc)
const codeLanguage = computed(() => getCodeLanguage(props.path))
</script>

<template>
  <div class="file-preview" :class="[`file-preview--${density}`]">
    <div v-if="showPath && path" class="file-preview__path" :title="path">
      {{ path }}
    </div>
    <n-spin v-if="loading" size="small" class="file-preview__spin" />
    <div v-else-if="isImage" class="file-preview__image-wrap">
      <img :src="imageSrc" :alt="path" class="file-preview__image">
    </div>
    <n-code
      v-else
      :code="content"
      :language="codeLanguage"
      show-line-numbers
      word-wrap
      class="file-preview__code"
    />
  </div>
</template>

<style scoped>
.file-preview {
  min-width: 0;
}

.file-preview--compact {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgb(0 0 0 / 6%);
}

.file-preview__path {
  padding: 0 8px 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  color: #64748b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-preview--comfortable .file-preview__path {
  padding: 0 0 8px;
  font-size: 12px;
  color: var(--n-text-color-3);
}

.file-preview__spin {
  display: flex;
  justify-content: center;
  padding: 12px;
}

.file-preview__code {
  overflow: auto;
}

.file-preview--compact .file-preview__code {
  max-height: min(40vh, 320px);
}

.file-preview--comfortable .file-preview__code {
  max-height: calc(100vh - 280px);
}

.file-preview__image-wrap {
  padding: 8px;
  overflow: auto;
}

.file-preview--compact .file-preview__image-wrap {
  max-height: min(40vh, 320px);
}

.file-preview--comfortable .file-preview__image-wrap {
  max-height: calc(100vh - 280px);
}

.file-preview__image {
  display: block;
  max-width: 100%;
  height: auto;
  border-radius: 6px;
  border: 1px solid rgb(0 0 0 / 5%);
}
</style>
