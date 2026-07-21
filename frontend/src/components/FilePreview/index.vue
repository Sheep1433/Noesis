<script setup lang="ts">
import { CreateOutline, DownloadOutline } from '@vicons/ionicons-v5'
import { NButton, NButtonGroup, NCode, NIcon, NInput, NSpin } from 'naive-ui'
import { computed, ref, watch } from 'vue'
import MarkdownInstance from '@/components/MarkdownPreview/plugins/markdown'
import { useMermaidRender } from '@/hooks/useMermaidRender'
import { downloadFile } from '@/utils/download'
import { getCodeLanguage, getFileBaseName, isImagePreviewPath, isMarkdownPreviewPath, splitYamlFrontmatter } from '@/utils/filePreview'

type MarkdownViewMode = 'preview' | 'source'

const props = withDefaults(defineProps<{
  path: string
  content?: string
  imageSrc?: string
  loading?: boolean
  density?: 'compact' | 'comfortable'
  showPath?: boolean
  fillHeight?: boolean
  editable?: boolean
  saving?: boolean
}>(), {
  content: '',
  imageSrc: '',
  loading: false,
  density: 'comfortable',
  showPath: true,
  fillHeight: false,
  editable: false,
  saving: false,
})

const emit = defineEmits<{
  save: [content: string]
}>()

const viewMode = ref<MarkdownViewMode>('preview')
const isEditing = ref(false)
const draftContent = ref('')
const markdownPreviewRef = ref<HTMLElement | null>(null)

watch(
  () => props.path,
  () => {
    viewMode.value = 'preview'
    isEditing.value = false
    draftContent.value = ''
  },
)

watch(
  () => props.content,
  (value) => {
    if (!isEditing.value) {
      draftContent.value = value
    }
  },
  { immediate: true },
)

const isImage = computed(() => isImagePreviewPath(props.path) && !!props.imageSrc)
const isMarkdown = computed(() => isMarkdownPreviewPath(props.path))
const codeLanguage = computed(() => getCodeLanguage(props.path))
const displayContent = computed(() => (isEditing.value ? draftContent.value : props.content))
const markdownParts = computed(() => splitYamlFrontmatter(displayContent.value))
const renderedMarkdown = computed(() => MarkdownInstance.render(markdownParts.value.body))
const showMarkdownSource = computed(() => isMarkdown.value && viewMode.value === 'source' && !isEditing.value)
const showMarkdownPreview = computed(() => isMarkdown.value && viewMode.value === 'preview' && !isEditing.value)
const canEdit = computed(() => props.editable && !isImage.value && !props.loading)
const canDownload = computed(() => !props.loading && (!!props.content || !!props.imageSrc))
const mermaidSource = computed(() => (showMarkdownPreview.value ? renderedMarkdown.value : ''))
const mermaidEnabled = computed(() => showMarkdownPreview.value && !!mermaidSource.value)

useMermaidRender(markdownPreviewRef, mermaidSource, mermaidEnabled)

function startEdit() {
  draftContent.value = props.content
  isEditing.value = true
  viewMode.value = 'source'
}

function cancelEdit() {
  draftContent.value = props.content
  isEditing.value = false
  if (isMarkdown.value) {
    viewMode.value = 'preview'
  }
}

function saveEdit() {
  emit('save', draftContent.value)
}

watch(
  () => props.saving,
  (saving, wasSaving) => {
    if (wasSaving && !saving && isEditing.value) {
      isEditing.value = false
      if (isMarkdown.value) {
        viewMode.value = 'preview'
      }
    }
  },
)

async function downloadCurrentFile() {
  const filename = getFileBaseName(props.path) || 'download'
  if (isImage.value && props.imageSrc) {
    const res = await fetch(props.imageSrc)
    const blob = await res.blob()
    downloadFile(blob, filename)
    return
  }
  downloadFile(props.content, filename, 'text/plain;charset=utf-8')
}
</script>

<template>
  <div
    class="file-preview"
    :class="[
      `file-preview--${density}`,
      { 'file-preview--fill': fillHeight },
    ]"
  >
    <div v-if="(showPath && path) || isMarkdown || canDownload || canEdit" class="file-preview__header">
      <div v-if="showPath && path" class="file-preview__path" :title="path">
        {{ path }}
      </div>
      <div class="file-preview__actions">
        <n-button-group v-if="isMarkdown && !isEditing" size="tiny" class="file-preview__mode-toggle">
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
        <n-button
          v-if="canDownload"
          quaternary
          size="tiny"
          title="下载"
          @click="downloadCurrentFile"
        >
          <template #icon>
            <n-icon size="16"><DownloadOutline /></n-icon>
          </template>
        </n-button>
        <n-button
          v-if="canEdit && !isEditing"
          quaternary
          size="tiny"
          title="编辑"
          @click="startEdit"
        >
          <template #icon>
            <n-icon size="16"><CreateOutline /></n-icon>
          </template>
        </n-button>
        <template v-if="isEditing">
          <n-button size="tiny" :loading="saving" type="primary" @click="saveEdit">
            保存
          </n-button>
          <n-button size="tiny" :disabled="saving" @click="cancelEdit">
            取消
          </n-button>
        </template>
      </div>
    </div>
    <n-spin v-if="loading" size="small" class="file-preview__spin" />
    <div v-else-if="isImage" class="file-preview__image-wrap">
      <img :src="imageSrc" :alt="path" class="file-preview__image">
    </div>
    <div
      v-else-if="showMarkdownPreview"
      ref="markdownPreviewRef"
      class="file-preview__markdown markdown-wrapper markdown-wrapper--file-preview"
    >
      <pre
        v-if="markdownParts.frontmatter"
        class="file-preview__frontmatter"
      >{{ markdownParts.frontmatter }}</pre>
      <div v-html="renderedMarkdown"></div>
    </div>
    <n-input
      v-else-if="isEditing"
      v-model:value="draftContent"
      type="textarea"
      :autosize="{ minRows: 12, maxRows: 40 }"
      class="file-preview__editor"
      placeholder="编辑文件内容…"
    />
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
  color: var(--noesis-color-text-secondary);
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

.file-preview__actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.file-preview__mode-toggle {
  flex-shrink: 0;
}

.file-preview__spin {
  display: flex;
  justify-content: center;
  padding: 12px;
}

.file-preview__code,
.file-preview__editor {
  overflow: auto;
}

.file-preview--fill .file-preview__code,
.file-preview--fill .file-preview__markdown,
.file-preview--fill .file-preview__image-wrap,
.file-preview--fill .file-preview__editor {
  flex: 1;
  min-height: 0;
}

.file-preview--compact .file-preview__code,
.file-preview--compact .file-preview__editor {
  max-height: min(40vh, 320px);
}

.file-preview--compact.file-preview--fill .file-preview__code,
.file-preview--compact.file-preview--fill .file-preview__editor {
  max-height: none;
}

.file-preview--comfortable .file-preview__code,
.file-preview--comfortable .file-preview__editor {
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

.file-preview--comfortable.file-preview--fill .file-preview__code,
.file-preview--comfortable.file-preview--fill .file-preview__editor,
.file-preview--comfortable.file-preview--fill .file-preview__markdown,
.file-preview--comfortable.file-preview--fill .file-preview__image-wrap {
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

.file-preview__editor :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
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

  .mermaid {
    margin: 12px 0;
    overflow-x: auto;
  }
}

.file-preview__frontmatter {
  margin: 0 0 12px;
  padding: 8px 10px;
  border-radius: 6px;
  border: 1px solid var(--noesis-color-border);
  background: var(--noesis-color-bg-muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  line-height: 1.5;
  color: var(--noesis-color-text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
