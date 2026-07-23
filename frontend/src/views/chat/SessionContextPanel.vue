<script setup lang="ts">
import type { SessionContextResponse } from '@/api/chat'
import { Refresh } from '@vicons/ionicons-v5'
import {
  NButton,
  NIcon,
  NSpin,
  useMessage,
} from 'naive-ui'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { getSessionContext, getWorkspaceFile, saveWorkspaceFile } from '@/api/chat'
import FilePreview from '@/components/FilePreview/index.vue'
import ResizeDivider from '@/components/ResizeDivider.vue'
import { invalidateMentionContextCache } from '@/hooks/useMentionCatalog'
import { usePaneResize } from '@/hooks/usePaneResize'
import { authFetch } from '@/utils/authHttp'
import { getFilePreviewKind } from '@/utils/filePreview'
import WorkspaceFileTree from '@/views/chat/WorkspaceFileTree.vue'

const props = defineProps<{
  sessionId: string
  backgroundColor?: string
}>()

const message = useMessage()
const router = useRouter()
const loading = ref(false)
const context = ref<SessionContextResponse | null>(null)
const selectedKey = ref('')
const previewPath = ref('')
const previewContent = ref('')
const previewImageSrc = ref('')
const previewLoading = ref(false)
const previewSaving = ref(false)

const previewEditable = computed(() => {
  if (!previewPath.value) {
    return false
  }
  return getFilePreviewKind(previewPath.value) === 'text'
})

const settingsDeepLink = computed(() => {
  const base = previewPath.value.replace(/^\/+/, '')
  if (base === 'USER.md') {
    return { name: 'Settings' as const, query: { s: 'profile' } }
  }
  if (base === 'AGENTS.md') {
    return { name: 'Settings' as const, query: { s: 'memory' } }
  }
  return null
})

const { size: treeWidth, startResize: startTreeResize } = usePaneResize({
  storageKey: 'noesis.chat.sessionTreeWidth',
  defaultSize: 132,
  min: 100,
  max: 360,
})

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

function sessionArtifactRelPath(key: string, sessionId: string): string | null {
  const prefix = `sessions/${sessionId}/`
  if (!key.startsWith(prefix)) {
    if (key.startsWith('uploads/') || key.startsWith('attachments/')) {
      return key
    }
    return null
  }
  const rel = key.slice(prefix.length)
  if (rel.startsWith('uploads/') || rel.startsWith('attachments/')) {
    return rel
  }
  return null
}

function isSessionArtifactKey(key: string, sessionId: string): boolean {
  return sessionArtifactRelPath(key, sessionId) != null
}

function openArtifact(path: string) {
  const rel = sessionArtifactRelPath(path, props.sessionId) ?? path
  const url = `${location.origin}/api/chat/sessions/${encodeURIComponent(props.sessionId)}/artifacts/${rel}`
  window.open(url, '_blank', 'noopener')
}

async function loadArtifactImage(path: string) {
  const rel = sessionArtifactRelPath(path, props.sessionId) ?? path
  const url = `${location.origin}/api/chat/sessions/${encodeURIComponent(props.sessionId)}/artifacts/${rel}`
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
  invalidateMentionContextCache(props.sessionId)
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
  const isUploadOrAttach = isSessionArtifactKey(key, props.sessionId)

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

async function onSaveFile(content: string) {
  if (!props.sessionId || !previewPath.value) {
    return
  }
  previewSaving.value = true
  try {
    const res = await saveWorkspaceFile(props.sessionId, previewPath.value, content)
    previewContent.value = res.content
    message.success('已保存')
  } catch (e: unknown) {
    const err = e as Error
    message.error(err.message || '保存失败')
  } finally {
    previewSaving.value = false
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
      <n-button
        v-if="settingsDeepLink"
        quaternary
        size="tiny"
        title="在设置中打开"
        @click="router.push(settingsDeepLink)"
      >
        在设置中打开
      </n-button>
      <n-button quaternary size="tiny" :loading="loading" title="Refresh" @click="reload">
        <template #icon>
          <n-icon size="16"><Refresh /></n-icon>
        </template>
      </n-button>
    </div>

    <n-spin :show="loading" class="panel-body">
      <div class="panel-split">
        <aside
          class="panel-tree"
          :style="{ width: `${treeWidth}px` }"
        >
          <WorkspaceFileTree
            v-if="context?.tree?.length"
            :nodes="context.tree"
            :selected-key="selectedKey"
            :session-id="sessionId"
            @select="onSelectFile"
          />
          <div v-else class="panel-empty-hint">
            暂无文件
          </div>
          <ResizeDivider @resize-start="startTreeResize" />
        </aside>

        <section class="panel-preview">
          <FilePreview
            v-if="previewPath"
            :path="previewPath"
            :content="previewContent"
            :image-src="previewImageSrc"
            :loading="previewLoading"
            :editable="previewEditable"
            :saving="previewSaving"
            density="compact"
            fill-height
            class="session-file-preview"
            @save="onSaveFile"
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
  gap: 4px;
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
  position: relative;
  flex: 0 0 auto;
  min-width: 0;
  overflow: auto;
  padding: 4px 0 8px;
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
