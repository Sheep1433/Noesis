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
    expandedKeys.value = collectExpandableKeys(nodes, 2)
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
</template>

<style scoped>
.workspace-file-tree {
  font-size: 12px;
  line-height: 1.4;
  user-select: none;
}
</style>
