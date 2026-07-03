<script setup lang="ts">
import { NButton, NButtonGroup, NCode, NSpin } from 'naive-ui'
import { computed, ref, watch } from 'vue'
import MarkdownInstance from '@/components/MarkdownPreview/plugins/markdown'
import { getCodeLanguage, isImagePreviewPath, isMarkdownPreviewPath } from '@/utils/filePreview'
import 'highlight.js/styles/atom-one-dark-reasonable.css'

type MarkdownViewMode = 'preview' | 'source'

const props = withDefaults(defineProps<{
  path: string
  content?: string
  imageSrc?: string
  loading?: boolean
  density?: 'compact' | 'comfortable'
  showPath?: boolean
  fillHeight?: boolean
}>(), {
  content: '',
  imageSrc: '',
  loading: false,
  density: 'comfortable',
  showPath: true,
  fillHeight: false,
})

const viewMode = ref<MarkdownViewMode>('preview')

watch(
  () => props.path,
  () => {
    viewMode.value = 'preview'
  },
)

const isImage = computed(() => isImagePreviewPath(props.path) && !!props.imageSrc)
const isMarkdown = computed(() => isMarkdownPreviewPath(props.path))
const codeLanguage = computed(() => getCodeLanguage(props.path))
const renderedMarkdown = computed(() => MarkdownInstance.render(props.content))
const showMarkdownSource = computed(() => isMarkdown.value && viewMode.value === 'source')
const showMarkdownPreview = computed(() => isMarkdown.value && viewMode.value === 'preview')
</script>

<template>
  <div
    class="file-preview"
    :class="[
      `file-preview--${density}`,
      { 'file-preview--fill': fillHeight },
    ]"
  >
    <div v-if="(showPath && path) || isMarkdown" class="file-preview__header">
      <div v-if="showPath && path" class="file-preview__path" :title="path">
        {{ path }}
      </div>
      <n-button-group v-if="isMarkdown" size="tiny" class="file-preview__mode-toggle">
        <n-button
          :type="viewMode === 'preview' ? 'primary' : 'default'"
          :ghost="viewMode !== 'preview'"
          @click="viewMode = 'preview'"
        >
          预览
        </n-button>
        <n-button
          :type="viewMode === 'source' ? 'primary' : 'default'"
          :ghost="viewMode !== 'source'"
          @click="viewMode = 'source'"
        >
          源码
        </n-button>
      </n-button-group>
    </div>
    <n-spin v-if="loading" size="small" class="file-preview__spin" />
    <div v-else-if="isImage" class="file-preview__image-wrap">
      <img :src="imageSrc" :alt="path" class="file-preview__image">
    </div>
    <div
      v-else-if="showMarkdownPreview"
      class="file-preview__markdown markdown-wrapper markdown-wrapper--file-preview"
      v-html="renderedMarkdown"
    ></div>
    <n-code
      v-else-if="showMarkdownSource || !isMarkdown"
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

.file-preview--fill {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.file-preview--compact {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgb(0 0 0 / 6%);
}

.file-preview--fill.file-preview--compact {
  margin-top: 0;
  padding-top: 0;
  border-top: none;
}

.file-preview__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-shrink: 0;
  padding: 6px 8px 4px;
}

.file-preview__path {
  flex: 1;
  min-width: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  color: #64748b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-preview--comfortable .file-preview__header {
  padding: 0 0 8px;
}

.file-preview--comfortable .file-preview__path {
  font-size: 12px;
  color: var(--n-text-color-3);
}

.file-preview__mode-toggle {
  flex-shrink: 0;
}

.file-preview__spin {
  display: flex;
  justify-content: center;
  padding: 12px;
}

.file-preview__code {
  overflow: auto;
}

.file-preview--fill .file-preview__code,
.file-preview--fill .file-preview__markdown,
.file-preview--fill .file-preview__image-wrap {
  flex: 1;
  min-height: 0;
}

.file-preview--compact .file-preview__code {
  max-height: min(40vh, 320px);
}

.file-preview--compact.file-preview--fill .file-preview__code {
  max-height: none;
}

.file-preview--comfortable .file-preview__code {
  max-height: calc(100vh - 280px);
}

.file-preview__markdown {
  overflow: auto;
  padding: 8px 12px;
}

.file-preview--compact .file-preview__markdown {
  max-height: min(40vh, 320px);
}

.file-preview--compact.file-preview--fill .file-preview__markdown {
  max-height: none;
}

.file-preview__image-wrap {
  padding: 8px;
  overflow: auto;
}

.file-preview--compact .file-preview__image-wrap {
  max-height: min(40vh, 320px);
}

.file-preview--compact.file-preview--fill .file-preview__image-wrap {
  max-height: none;
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

<style lang="scss">
.markdown-wrapper.markdown-wrapper--file-preview {
  margin: 0;
  padding: 8px 12px;
  border-radius: 0;
  background: transparent;
  font-size: 13px;
  line-height: 1.65;
  color: var(--noesis-color-text-table);

  h1 { font-size: 1.35em; }
  h2 { font-size: 1.15em; padding-bottom: 0.25em; border-bottom: 1px solid var(--noesis-markdown-heading-border); }
  h3 { font-size: 1.05em; }

  h1, h2, h3, h4, h5, h6 {
    margin-top: 14px;
    margin-bottom: 8px;
    line-height: 1.3;
  }

  p {
    margin: 8px 0;
    line-height: 1.65;
  }

  ul, ol {
    padding-left: 1.4em;
    margin: 8px 0;
  }

  li {
    line-height: 1.6;
    margin: 4px 0;
  }

  blockquote {
    padding: 8px 10px;
    margin: 10px 0;
    border-left: 3px solid var(--noesis-color-border);
    background-color: var(--noesis-color-bg-hover);
    color: var(--noesis-color-text-secondary);

    & > p { margin: 0; }
  }

  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 12px;
  }

  th, td {
    border: 1px solid var(--noesis-color-border);
    padding: 6px;
    text-align: left;
  }

  th { background-color: var(--noesis-color-bg-muted); }

  img {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 8px 0;
  }

  code {
    font-size: 0.9em;
    padding: 1px 4px;
    border-radius: 3px;
    background: var(--noesis-color-bg-muted);
  }

  pre {
    margin: 10px 0;
    overflow: auto;
    border-radius: 6px;
  }
}
</style>
