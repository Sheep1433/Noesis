<script lang="ts" setup>
import type { TreeRenderProps } from 'naive-ui/es/tree/src/interface'
import type { SkillFsTreeNode } from '@/api/skills'

defineProps<{
  treeData: SkillFsTreeNode[]
  selectedKeys: string[]
  contextMenuShow: boolean
  contextMenuX: number
  contextMenuY: number
  contextMenuOptions: Array<{ label: string, key: string }>
  renderTreeLabel: (props: TreeRenderProps) => unknown
  nodeProps: (props: { option: SkillFsTreeNode }) => Record<string, unknown>
}>()

const emit = defineEmits<{
  updateSelectedKeys: [keys: Array<string | number>]
  contextMenuSelect: [key: string]
  contextMenuClose: []
  openZipModal: []
}>()
</script>

<template>
  <div class="skills-tree-panel">
    <div class="sider-header">
      <n-text strong>技能目录</n-text>
      <n-text depth="3" class="sider-hint">
        点击文件预览；个人技能顶层目录可右键删除
      </n-text>
    </div>
    <div v-if="treeData.length > 0" class="tree-wrap">
      <n-dropdown
        trigger="manual"
        placement="bottom-start"
        :show="contextMenuShow"
        :x="contextMenuX"
        :y="contextMenuY"
        :options="contextMenuOptions"
        @select="emit('contextMenuSelect', $event)"
        @clickoutside="emit('contextMenuClose')"
      />
      <n-tree
        :data="treeData"
        :selected-keys="selectedKeys"
        :render-label="renderTreeLabel"
        :node-props="nodeProps"
        block-line
        show-line
        selectable
        @update:selected-keys="emit('updateSelectedKeys', $event)"
      />
    </div>
    <div v-else class="tree-empty">
      <n-empty description="暂无技能" size="small">
        <template #extra>
          <n-button size="small" type="primary" @click="emit('openZipModal')">
            上传第一个技能
          </n-button>
        </template>
      </n-empty>
    </div>
  </div>
</template>

<style scoped>
.skills-tree-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.sider-header {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 14px 16px 10px;
  border-bottom: 1px solid var(--n-border-color);
  flex-shrink: 0;
}

.sider-hint {
  font-size: 12px;
}

.tree-wrap {
  flex: 1;
  min-height: 0;
  padding: 8px 10px 12px;
  overflow: auto;
}

.tree-empty {
  padding: 32px 16px;
}
</style>
