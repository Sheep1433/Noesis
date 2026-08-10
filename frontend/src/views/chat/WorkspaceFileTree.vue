<script setup lang="ts">
import type { SessionFsTreeNode } from '@/api/chat'
import { ref, watch } from 'vue'
import WorkspaceFileTreeNode from './WorkspaceFileTreeNode.vue'

const props = defineProps<{
  nodes: SessionFsTreeNode[]
  selectedKey?: string
}>()

const emit = defineEmits<{
  select: [key: string]
}>()

const expandedKeys = ref<string[]>([])

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
</script>

<template>
  <div v-if="nodes.length" class="workspace-file-tree">
    <p class="workspace-file-tree__hint">点击文件即可预览内容</p>
    <div class="workspace-file-tree__body">
      <WorkspaceFileTreeNode
        v-for="node in nodes"
        :key="node.key"
        :node="node"
        :depth="0"
        :selected-key="selectedKey"
        :is-expanded="isExpanded"
        :toggle-expand="toggleExpand"
        :on-row-click="onRowClick"
      />
    </div>
  </div>
</template>

<style scoped>
.workspace-file-tree {
  font-size: 12px;
  line-height: 1.4;
  user-select: none;
}

.workspace-file-tree__hint {
  position: sticky;
  left: 0;
  z-index: 1;
  margin: 0;
  padding: 0 8px 6px;
  font-size: 11px;
  color: var(--noesis-color-text-tertiary, #94a3b8);
  background: var(--panel-bg, transparent);
}

.workspace-file-tree__body {
  min-width: 100%;
  width: max-content;
}
</style>
