<script setup lang="ts">
import type { SessionContextResponse } from '@/api/chat'
import { Refresh } from '@vicons/ionicons-v5'
import {
  NButton,
  NIcon,
  NSpin,
  useMessage,
} from 'naive-ui'
import { onBeforeUnmount, ref, watch } from 'vue'
import { getSessionContext, getWorkspaceFile } from '@/api/chat'
import FilePreview from '@/components/FilePreview/index.vue'
import { authFetch } from '@/utils/authHttp'
import { getFilePreviewKind } from '@/utils/filePreview'
import WorkspaceFileTree from '@/views/chat/WorkspaceFileTree.vue'

const props = defineProps<{
  sessionId: string
  backgroundColor?: string
}>()

const message = useMessage()
const loading = ref(false)
const context = ref<SessionContextResponse | null>(null)
const selectedKey = ref('')
const previewPath = ref('')
const previewContent = ref('')
const previewImageSrc = ref('')
const previewLoading = ref(false)

function revokePreviewImage() {
  if (previewImageSrc.value) {
    URL.revokeObjectURL(previewImageSrc.value)
    previewImageSrc.value = ''
  }
}

function clearPreview() {
  previewPath.value = ''
  previewContent.value = ''
  revokePreviewImage()
}

function openArtifact(path: string) {
  const url = `${location.origin}/api/chat/sessions/${encodeURIComponent(props.sessionId)}/artifacts/${path}`
  window.open(url, '_blank', 'noopener')
}

async function loadArtifactImage(path: string) {
  const url = `${location.origin}/api/chat/sessions/${encodeURIComponent(props.sessionId)}/artifacts/${path}`
  const res = await authFetch(url)
  if (!res.ok) {
    throw new Error(`Failed to load image: ${res.status}`)
  }
  const blob = await res.blob()
  return URL.createObjectURL(blob)
}

async function reload() {
  if (!props.sessionId) {
    context.value = null
    return
  }
  loading.value = true
  clearPreview()
  selectedKey.value = ''
  try {
    context.value = await getSessionContext(props.sessionId)
  } catch (e: unknown) {
    context.value = null
    const err = e as Error
    if (!err.message?.includes('404')) {
      message.error(err.message || 'Failed to load directory')
    }
  } finally {
    loading.value = false
  }
}

async function onSelectFile(key: string) {
  if (!props.sessionId) {
    return
  }
  selectedKey.value = key
  revokePreviewImage()
  previewContent.value = ''

  const kind = getFilePreviewKind(key)
  const isUploadOrAttach = key.startsWith('uploads/') || key.startsWith('attachments/')

  if (kind === 'unsupported') {
    clearPreview()
    if (isUploadOrAttach) {
      openArtifact(key)
      return
    }
    selectedKey.value = ''
    return
  }

  previewLoading.value = true
  previewPath.value = key

  try {
    if (kind === 'image') {
      previewImageSrc.value = await loadArtifactImage(key)
      return
    }

    const res = await getWorkspaceFile(props.sessionId, key)
    previewContent.value = res.content
  } catch (e: unknown) {
    const err = e as Error
    if (isUploadOrAttach) {
      openArtifact(key)
      clearPreview()
      selectedKey.value = ''
      return
    }
    message.error(err.message || 'Failed to read file')
    clearPreview()
    selectedKey.value = ''
  } finally {
    previewLoading.value = false
  }
}

watch(
  () => props.sessionId,
  () => {
    void reload()
  },
)

onBeforeUnmount(() => {
  revokePreviewImage()
})

defineExpose({ reload })
</script>

<template>
  <div
    class="session-files-panel"
    :style="backgroundColor ? { backgroundColor, '--panel-bg': backgroundColor } : undefined"
  >
    <div class="panel-toolbar">
      <n-button quaternary size="tiny" :loading="loading" title="Refresh" @click="reload">
        <template #icon>
          <n-icon size="16"><Refresh /></n-icon>
        </template>
      </n-button>
    </div>

    <n-spin :show="loading" class="panel-body">
      <div class="panel-split">
        <aside class="panel-tree">
          <WorkspaceFileTree
            v-if="context?.tree?.length"
            :nodes="context.tree"
            :selected-key="selectedKey"
            @select="onSelectFile"
          />
          <div v-else class="panel-empty-hint">
            暂无文件
          </div>
        </aside>

        <section class="panel-preview">
          <FilePreview
            v-if="previewPath"
            :path="previewPath"
            :content="previewContent"
            :image-src="previewImageSrc"
            :loading="previewLoading"
            density="compact"
            fill-height
            class="session-file-preview"
          />
          <div v-else class="panel-empty-hint panel-empty-hint--preview">
            选择文件以查看内容
          </div>
        </section>
      </div>
    </n-spin>
  </div>
</template>

<style scoped>
.session-files-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  box-sizing: border-box;
  background-color: var(--panel-bg, transparent);
}

.panel-toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-shrink: 0;
  padding: 4px 6px 0;
}

.panel-body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  padding: 0;
  background-color: inherit;
}

.panel-body :deep(.n-spin-container) {
  height: 100%;
  background-color: inherit;
}

.panel-body :deep(.n-spin-content) {
  height: 100%;
  background-color: inherit;
}

.panel-split {
  display: flex;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}

.panel-tree {
  flex: 0 0 132px;
  min-width: 120px;
  overflow: auto;
  padding: 4px 0 8px;
  border-right: 1px solid var(--noesis-color-border-aside);
}

.panel-preview {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.panel-empty-hint {
  padding: 12px 8px;
  font-size: 12px;
  color: var(--noesis-color-text-tertiary, #94a3b8);
  text-align: center;
}

.panel-empty-hint--preview {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 16px;
}

.session-file-preview {
  flex: 1;
  min-height: 0;
}

.session-file-preview :deep(.n-code) {
  background-color: rgb(255 255 255 / 45%) !important;
}

.session-file-preview :deep(.n-code .n-code__line) {
  background-color: transparent !important;
}
</style>
