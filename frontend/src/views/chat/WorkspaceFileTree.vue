<script setup lang="ts">
import type { DropdownOption } from 'naive-ui'
import type { SessionFsTreeNode } from '@/api/chat'
import { NDropdown, useMessage } from 'naive-ui'
import { nextTick, ref, watch } from 'vue'
import { downloadWorkspaceArchive } from '@/api/chat'
import WorkspaceFileTreeNode from './WorkspaceFileTreeNode.vue'

const props = defineProps<{
  nodes: SessionFsTreeNode[]
  selectedKey?: string
  sessionId: string
}>()

const emit = defineEmits<{
  select: [key: string]
}>()

const message = useMessage()
const expandedKeys = ref<string[]>([])
const contextMenuShow = ref(false)
const contextMenuX = ref(0)
const contextMenuY = ref(0)
const contextMenuTarget = ref<SessionFsTreeNode | null>(null)
const archiveDownloading = ref(false)

const contextMenuOptions: DropdownOption[] = [
  { label: '下载', key: 'download' },
]

watch(
  () => props.nodes,
  (nodes) => {
    expandedKeys.value = collectExpandableKeys(nodes, 3)
  },
  { immediate: true, deep: true },
)

function collectExpandableKeys(nodes: SessionFsTreeNode[], maxDepth: number, depth = 0): string[] {
  if (depth >= maxDepth) {
    return []
  }
  const keys: string[] = []
  for (const node of nodes) {
    if (!node.isLeaf) {
      keys.push(node.key)
      if (node.children?.length) {
        keys.push(...collectExpandableKeys(node.children, maxDepth, depth + 1))
      }
    }
  }
  return keys
}

function isExpanded(key: string) {
  return expandedKeys.value.includes(key)
}

function toggleExpand(key: string) {
  if (expandedKeys.value.includes(key)) {
    expandedKeys.value = expandedKeys.value.filter((k) => k !== key)
  } else {
    expandedKeys.value = [...expandedKeys.value, key]
  }
}

function onRowClick(node: SessionFsTreeNode) {
  if (node.isLeaf) {
    emit('select', node.key)
    return
  }
  toggleExpand(node.key)
}

function canArchiveDownload(key: string): boolean {
  return !!key && !/^users\/[^/]+$/.test(key)
}

function onNodeContextMenu(node: SessionFsTreeNode, event: MouseEvent) {
  if (!canArchiveDownload(node.key)) {
    return
  }
  event.preventDefault()
  event.stopPropagation()
  contextMenuShow.value = false
  contextMenuTarget.value = node
  contextMenuX.value = event.clientX
  contextMenuY.value = event.clientY
  void nextTick(() => {
    contextMenuShow.value = true
  })
}

function closeContextMenu() {
  contextMenuShow.value = false
  contextMenuTarget.value = null
}

async function handleContextMenuSelect(key: string) {
  const target = contextMenuTarget.value
  closeContextMenu()
  if (key !== 'download' || !target || !props.sessionId || archiveDownloading.value) {
    return
  }
  archiveDownloading.value = true
  try {
    await downloadWorkspaceArchive(props.sessionId, target.key)
    message.success('已开始下载')
  } catch (e: unknown) {
    const err = e as Error
    message.error(err.message || '下载失败')
  } finally {
    archiveDownloading.value = false
  }
}
</script>

<template>
  <div v-if="nodes.length" class="workspace-file-tree">
    <p class="workspace-file-tree__hint">右键或 Control+点按可下载</p>
    <n-dropdown
      trigger="manual"
      placement="bottom-start"
      to="body"
      :show="contextMenuShow"
      :x="contextMenuX"
      :y="contextMenuY"
      :options="contextMenuOptions"
      @select="handleContextMenuSelect"
      @clickoutside="closeContextMenu"
    />
    <WorkspaceFileTreeNode
      v-for="node in nodes"
      :key="node.key"
      :node="node"
      :depth="0"
      :selected-key="selectedKey"
      :is-expanded="isExpanded"
      :toggle-expand="toggleExpand"
      :on-row-click="onRowClick"
      :on-context-menu="onNodeContextMenu"
    />
  </div>
</template>

<style scoped>
.workspace-file-tree {
  font-size: 12px;
  line-height: 1.4;
  user-select: none;
}

.workspace-file-tree__hint {
  margin: 0;
  padding: 0 8px 6px;
  font-size: 11px;
  color: var(--noesis-color-text-tertiary, #94a3b8);
}
</style>
