<script setup lang="ts">
import type { SessionFsTreeNode } from '@/api/chat'
import { computed, onBeforeUnmount } from 'vue'

const props = defineProps<{
  node: SessionFsTreeNode
  depth: number
  selectedKey?: string
  canDownload: (key: string) => boolean
  isExpanded: (key: string) => boolean
  toggleExpand: (key: string) => void
  onRowClick: (node: SessionFsTreeNode) => void
  onContextMenu: (node: SessionFsTreeNode, event: MouseEvent) => void
  onDownload: (node: SessionFsTreeNode) => void
}>()

const LONG_PRESS_MS = 480
const MOVE_CANCEL_PX = 10

const isFolder = computed(() => !props.node.isLeaf)
const expanded = computed(() => isFolder.value && props.isExpanded(props.node.key))
const selected = computed(() => props.selectedKey === props.node.key)
const downloadable = computed(() => props.canDownload(props.node.key))

let longPressTimer: ReturnType<typeof setTimeout> | null = null
let touchStartX = 0
let touchStartY = 0
let longPressFired = false

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

function clearLongPress() {
  if (longPressTimer != null) {
    clearTimeout(longPressTimer)
    longPressTimer = null
  }
}

function onChevronClick(e: Event) {
  e.stopPropagation()
  props.toggleExpand(props.node.key)
}

function handleContextMenu(event: MouseEvent) {
  props.onContextMenu(props.node, event)
}

function handleRowClick() {
  if (longPressFired) {
    longPressFired = false
    return
  }
  props.onRowClick(props.node)
}

function handleDownloadClick(event: Event) {
  event.stopPropagation()
  props.onDownload(props.node)
}

function openMenuAt(clientX: number, clientY: number) {
  const event = new MouseEvent('contextmenu', {
    bubbles: true,
    cancelable: true,
    clientX,
    clientY,
  })
  props.onContextMenu(props.node, event)
}

function onTouchStart(event: TouchEvent) {
  if (!downloadable.value || event.touches.length !== 1) {
    return
  }
  const touch = event.touches[0]
  touchStartX = touch.clientX
  touchStartY = touch.clientY
  longPressFired = false
  clearLongPress()
  longPressTimer = setTimeout(() => {
    longPressTimer = null
    longPressFired = true
    openMenuAt(touch.clientX, touch.clientY)
  }, LONG_PRESS_MS)
}

function onTouchMove(event: TouchEvent) {
  if (longPressTimer == null || event.touches.length !== 1) {
    return
  }
  const touch = event.touches[0]
  if (
    Math.abs(touch.clientX - touchStartX) > MOVE_CANCEL_PX
    || Math.abs(touch.clientY - touchStartY) > MOVE_CANCEL_PX
  ) {
    clearLongPress()
  }
}

function onTouchEnd() {
  clearLongPress()
}

onBeforeUnmount(() => {
  clearLongPress()
})
</script>

<template>
  <div class="tree-node">
    <div
      class="tree-row"
      :class="{
        'tree-row--selected': selected,
        'tree-row--folder': isFolder,
        'tree-row--downloadable': downloadable,
      }"
      :style="{ paddingLeft: `${8 + depth * 14}px` }"
      @click="handleRowClick"
      @contextmenu="handleContextMenu"
      @touchstart.passive="onTouchStart"
      @touchmove.passive="onTouchMove"
      @touchend="onTouchEnd"
      @touchcancel="onTouchEnd"
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
      <button
        v-if="downloadable"
        type="button"
        class="tree-download"
        title="下载"
        aria-label="下载"
        @click="handleDownloadClick"
      >
        <span class="i-mingcute:download-2-line tree-download__icon"></span>
      </button>
    </div>
    <template v-if="expanded && node.children?.length">
      <WorkspaceFileTreeNode
        v-for="child in node.children"
        :key="child.key"
        :node="child"
        :depth="depth + 1"
        :selected-key="selectedKey"
        :can-download="canDownload"
        :is-expanded="isExpanded"
        :toggle-expand="toggleExpand"
        :on-row-click="onRowClick"
        :on-context-menu="onContextMenu"
        :on-download="onDownload"
      />
    </template>
  </div>
</template>

<style scoped>
.tree-node {
  min-width: 100%;
  width: max-content;
}

.tree-row {
  display: flex;
  align-items: center;
  gap: 4px;
  box-sizing: border-box;
  width: max-content;
  min-width: 100%;
  min-height: 26px;
  padding-right: 4px;
  border-radius: 4px;
  cursor: pointer;
  color: #3f3f46;
  -webkit-touch-callout: none;
  touch-action: manipulation;
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
  flex: 0 0 auto;
  white-space: nowrap;
}

.tree-download {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  margin-left: 2px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: #94a3b8;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.12s ease, background 0.12s ease, color 0.12s ease;
}

.tree-row:hover .tree-download,
.tree-row--selected .tree-download,
.tree-download:focus-visible {
  opacity: 1;
}

@media (hover: none) {
  .tree-download {
    opacity: 0.85;
  }
}

.tree-download:hover,
.tree-download:focus-visible {
  background: rgb(0 0 0 / 6%);
  color: #64748b;
}

.tree-download__icon {
  width: 14px;
  height: 14px;
}
</style>
