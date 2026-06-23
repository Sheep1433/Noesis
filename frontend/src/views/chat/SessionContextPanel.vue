<script setup lang="ts">
import type { ChatAttachmentResponse, SessionContextResponse, SessionFsTreeNode } from '@/api/chat'
import { Refresh } from '@vicons/ionicons-v5'
import {
  NButton,
  NCode,
  NEmpty,
  NIcon,
  NList,
  NListItem,
  NSpin,
  NTabPane,
  NTabs,
  NText,
  NTree,
  useMessage,
} from 'naive-ui'
import { ref, watch } from 'vue'
import { getSessionContext, getWorkspaceFile } from '@/api/chat'

const props = defineProps<{
  sessionId: string
}>()

const message = useMessage()
const loading = ref(false)
const context = ref<SessionContextResponse | null>(null)
const selectedKeys = ref<string[]>([])
const previewPath = ref('')
const previewContent = ref('')
const previewLoading = ref(false)
const activeTab = ref('workspace')

async function reload() {
  if (!props.sessionId) {
    context.value = null
    return
  }
  loading.value = true
  previewPath.value = ''
  previewContent.value = ''
  selectedKeys.value = []
  try {
    context.value = await getSessionContext(props.sessionId)
  } catch (e: unknown) {
    context.value = null
    const err = e as Error
    message.error(err.message || '加载会话上下文失败')
  } finally {
    loading.value = false
  }
}

function isLeafKey(key: string, nodes: SessionFsTreeNode[] | undefined): boolean | null {
  if (!nodes) {
    return null
  }
  for (const n of nodes) {
    if (n.key === key) {
      return n.isLeaf
    }
    const sub = isLeafKey(key, n.children)
    if (sub !== null) {
      return sub
    }
  }
  return null
}

async function onUpdateSelectedKeys(keys: Array<string | number>) {
  const raw = keys[0]
  const key = raw == null ? '' : String(raw)
  selectedKeys.value = key ? [key] : []

  if (!key || !props.sessionId) {
    previewPath.value = ''
    previewContent.value = ''
    return
  }

  if (isLeafKey(key, context.value?.workspace) !== true) {
    previewPath.value = ''
    previewContent.value = ''
    return
  }

  previewLoading.value = true
  previewPath.value = key
  previewContent.value = ''
  try {
    const res = await getWorkspaceFile(props.sessionId, key)
    previewContent.value = res.content
  } catch (e: unknown) {
    const err = e as Error
    message.error(err.message || '读取失败')
    previewPath.value = ''
  } finally {
    previewLoading.value = false
  }
}

function openAttachment(item: ChatAttachmentResponse) {
  if (item.artifact_url) {
    window.open(item.artifact_url, '_blank', 'noopener')
  }
}

watch(
  () => props.sessionId,
  () => {
    void reload()
  },
  { immediate: true },
)

defineExpose({ reload })
</script>

<template>
  <div class="session-context-panel">
    <div class="panel-head">
      <span class="panel-title">会话上下文</span>
      <n-button quaternary size="small" :loading="loading" @click="reload">
        <template #icon>
          <n-icon><Refresh /></n-icon>
        </template>
      </n-button>
    </div>

    <n-spin :show="loading">
      <n-tabs v-model:value="activeTab" type="line" size="small" class="panel-tabs">
        <n-tab-pane name="workspace" tab="产物">
          <div v-if="context?.workspace?.length" class="tree-wrap">
            <n-tree
              :data="context.workspace"
              :selected-keys="selectedKeys"
              block-line
              show-line
              selectable
              @update:selected-keys="onUpdateSelectedKeys"
            />
          </div>
          <n-empty
            v-else
            size="small"
            description="本会话尚无 Agent 产物"
          />
          <div v-if="previewPath" class="preview-block">
            <n-text depth="3" class="preview-label">
              {{ previewPath }}
            </n-text>
            <n-spin v-if="previewLoading" size="small" />
            <n-code
              v-else
              :code="previewContent"
              language="markdown"
              word-wrap
              class="preview-code"
            />
          </div>
        </n-tab-pane>
        <n-tab-pane name="attachments" tab="附件">
          <n-list v-if="context?.attachments?.length" clickable>
            <n-list-item
              v-for="item in context.attachments"
              :key="item.attachment_id"
              @click="openAttachment(item)"
            >
              <div class="attach-row">
                <span class="attach-name">{{ item.file_name }}</span>
                <n-text depth="3" class="attach-kind">
                  {{ item.kind }}
                </n-text>
              </div>
            </n-list-item>
          </n-list>
          <n-empty v-else size="small" description="暂无附件" />
        </n-tab-pane>
      </n-tabs>
    </n-spin>
  </div>
</template>

<style scoped>
.session-context-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 12px;
  box-sizing: border-box;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  flex-shrink: 0;
}

.panel-title {
  font-weight: 600;
  font-size: 14px;
}

.panel-tabs {
  flex: 1;
  min-height: 0;
}

.tree-wrap {
  max-height: 240px;
  overflow: auto;
  margin-bottom: 8px;
}

.preview-block {
  border-top: 1px solid var(--n-border-color);
  padding-top: 8px;
}

.preview-label {
  display: block;
  margin-bottom: 6px;
  word-break: break-all;
  font-size: 12px;
}

.preview-code {
  max-height: 200px;
  overflow: auto;
  font-size: 12px;
}

.attach-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
}

.attach-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.attach-kind {
  flex-shrink: 0;
  font-size: 12px;
}
</style>
