<script setup lang="ts">
import type { SessionFsTreeNode } from '@/api/chat'
import { computed } from 'vue'

const props = defineProps<{
  node: SessionFsTreeNode
  depth: number
  selectedKey?: string
  isExpanded: (key: string) => boolean
  toggleExpand: (key: string) => void
  onRowClick: (node: SessionFsTreeNode) => void
  onContextMenu: (node: SessionFsTreeNode, event: MouseEvent) => void
}>()

const isFolder = computed(() => !props.node.isLeaf)
const expanded = computed(() => isFolder.value && props.isExpanded(props.node.key))
const selected = computed(() => props.selectedKey === props.node.key)

function fileIcon(name: string) {
  const lower = name.toLowerCase()
  if (lower.endsWith('.md')) {
    return 'i-carbon:document'
  }
  if (lower.endsWith('.json') || lower.endsWith('.yaml') || lower.endsWith('.yml')) {
    return 'i-carbon:code'
  }
  return 'i-carbon:document-blank'
}

function onChevronClick(e: Event) {
  e.stopPropagation()
  props.toggleExpand(props.node.key)
}

function handleContextMenu(event: MouseEvent) {
  props.onContextMenu(props.node, event)
}
</script>

<template>
  <div class="tree-node">
    <div
      class="tree-row"
      :class="{
        'tree-row--selected': selected,
        'tree-row--folder': isFolder,
      }"
      :style="{ paddingLeft: `${8 + depth * 14}px` }"
      @click="onRowClick(node)"
      @contextmenu="handleContextMenu"
    >
      <span
        class="tree-chevron"
        :class="{
          'tree-chevron--open': expanded,
          'tree-chevron--hidden': !isFolder,
        }"
        @click="isFolder ? onChevronClick($event) : undefined"
      >›</span>
      <span
        class="tree-icon"
        :class="isFolder ? 'i-carbon:folder' : fileIcon(node.label)"
      ></span>
      <span class="tree-label" :title="node.label">{{ node.label }}</span>
    </div>
    <template v-if="expanded && node.children?.length">
      <WorkspaceFileTreeNode
        v-for="child in node.children"
        :key="child.key"
        :node="child"
        :depth="depth + 1"
        :selected-key="selectedKey"
        :is-expanded="isExpanded"
        :toggle-expand="toggleExpand"
        :on-row-click="onRowClick"
        :on-context-menu="onContextMenu"
      />
    </template>
  </div>
</template>

<style scoped>
.tree-row {
  display: flex;
  align-items: center;
  gap: 4px;
  min-height: 26px;
  padding-right: 8px;
  border-radius: 4px;
  cursor: pointer;
  color: #3f3f46;
}

.tree-row:hover {
  background: rgb(0 0 0 / 3%);
}

.tree-row--selected {
  background: rgb(0 0 0 / 5%);
  color: #334155;
}

.tree-chevron {
  flex-shrink: 0;
  width: 14px;
  font-size: 14px;
  line-height: 1;
  color: #a1a1aa;
  transition: transform 0.15s ease;
}

.tree-chevron--open {
  transform: rotate(90deg);
}

.tree-chevron--hidden {
  visibility: hidden;
}

.tree-icon {
  flex-shrink: 0;
  width: 14px;
  height: 14px;
  color: #71717a;
}

.tree-row--selected .tree-icon {
  color: #64748b;
}

.tree-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
