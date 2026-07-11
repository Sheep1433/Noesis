<script setup lang="ts">
import type { SkillFsTreeNode, SkillFsTreeResponse, SkillSource } from '@/api/skills'
import { CloudUpload, DocumentText, FolderOpen, LockClosed, Person, Refresh, Server } from '@vicons/ionicons-v5'
import {
  NButton,
  NDrawer,
  NDrawerContent,
  NEmpty,
  NForm,
  NFormItem,
  NIcon,
  NLayout,
  NLayoutContent,
  NLayoutSider,
  NModal,
  NSpace,
  NSpin,
  NTag,
  NText,
  NUpload,
  useDialog,
  useMessage,
} from 'naive-ui'
import { computed, h, onMounted, ref } from 'vue'
import {
  deleteUserSkillPackage,
  getSkillsFsFile,
  getSkillsFsTree,
  isDeletableUserSkillPackage,
  parseSourceFromKey,
  uploadSkillsFsZip,
} from '@/api/skills'
import FilePreview from '@/components/FilePreview/index.vue'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { useResponsiveDrawerWidth } from '@/hooks/useResponsiveDrawerWidth'
import { isTextPreviewPath } from '@/utils/filePreview'
import SkillsTreePanel from '@/views/skills/SkillsTreePanel.vue'

const message = useMessage()
const dialog = useDialog()
const { isMobile } = useBreakpoint()
const { drawerWidth: treeDrawerWidth } = useResponsiveDrawerWidth({ max: 320 })
const treeDrawerOpen = ref(false)
const modalWidth = computed(() => (isMobile.value ? 'min(480px, calc(100vw - 32px))' : '480px'))

const SOURCE_LABEL: Record<SkillSource, string> = {
  platform: '平台预置',
  user: '个人技能',
}

const loading = ref(true)
const error = ref<string | null>(null)
const treePayload = ref<SkillFsTreeResponse | null>(null)
const treeData = computed(() => treePayload.value?.tree ?? [])

const selectedKeys = ref<string[]>([])
const previewLoading = ref(false)
const previewMeta = ref<{
  relPath: string
  filename: string
  source: SkillSource
} | null>(null)
const previewContent = ref('')

const showZipModal = ref(false)
const zipFile = ref<File | null>(null)
const zipLoading = ref(false)

const contextMenuShow = ref(false)
const contextMenuX = ref(0)
const contextMenuY = ref(0)
const contextMenuTarget = ref<SkillFsTreeNode | null>(null)

const contextMenuOptions = [
  { label: '删除技能', key: 'delete' },
]

const showEmptyHint = computed(() => {
  const userNode = treeData.value.find((n) => n.key === 'user:')
  return !userNode?.children?.length
})

onMounted(async () => {
  await loadTree()
})

async function loadTree() {
  loading.value = true
  error.value = null
  previewMeta.value = null
  previewContent.value = ''
  selectedKeys.value = []
  try {
    treePayload.value = await getSkillsFsTree()
  } catch (e: any) {
    error.value = e.message || '加载失败'
    treePayload.value = null
  } finally {
    loading.value = false
  }
}

function isLeafKey(key: string, nodes: SkillFsTreeNode[] | undefined): boolean | null {
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

function renderTreeLabel({ option }: { option: SkillFsTreeNode }) {
  const isRoot = option.key === 'platform:' || option.key === 'user:'
  if (!isRoot) {
    return option.label
  }
  const isPlatform = option.key === 'platform:'
  return h('span', { class: 'tree-root-label' }, [
    h(NIcon, {
      component: isPlatform ? Server : Person,
      size: 14,
      class: 'tree-root-icon',
    }),
    option.label,
  ])
}

function nodeProps({ option }: { option: SkillFsTreeNode }) {
  return {
    onContextmenu(e: MouseEvent) {
      if (!isDeletableUserSkillPackage(option)) {
        return
      }
      e.preventDefault()
      e.stopPropagation()
      contextMenuTarget.value = option
      contextMenuX.value = e.clientX
      contextMenuY.value = e.clientY
      contextMenuShow.value = true
    },
  }
}

function closeContextMenu() {
  contextMenuShow.value = false
  contextMenuTarget.value = null
}

function handleContextMenuSelect(key: string) {
  const target = contextMenuTarget.value
  closeContextMenu()
  if (key !== 'delete' || !target) {
    return
  }
  confirmDeleteSkillPackage(target)
}

function confirmDeleteSkillPackage(node: SkillFsTreeNode) {
  const { path } = parseSourceFromKey(node.key)
  dialog.warning({
    title: '删除技能',
    content: `确定删除个人技能「${node.label}」吗？该目录下的全部文件将被永久移除，且无法恢复。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: () => executeDeleteSkillPackage(path, node.key),
  })
}

async function executeDeleteSkillPackage(packageName: string, nodeKey: string): Promise<boolean> {
  try {
    const r = await deleteUserSkillPackage(packageName)
    if (r.success) {
      message.success(r.message)
      if (selectedKeys.value[0]?.startsWith(`${nodeKey}/`) || selectedKeys.value[0] === nodeKey) {
        selectedKeys.value = []
        previewMeta.value = null
        previewContent.value = ''
      }
      await loadTree()
      return true
    }
    message.error(r.message || '删除失败')
    return false
  } catch (e: any) {
    message.error(e.message || '删除失败')
    return false
  }
}

async function onUpdateSelectedKeys(keys: Array<string | number>) {
  const raw = keys[0]
  const key = raw == null ? '' : String(raw)
  selectedKeys.value = key ? [key] : []

  if (!key) {
    previewMeta.value = null
    previewContent.value = ''
    return
  }

  const leaf = isLeafKey(key, treeData.value)
  if (leaf !== true) {
    previewMeta.value = null
    previewContent.value = ''
    return
  }

  const { source, path } = parseSourceFromKey(key)
  if (!isTextPreviewPath(path)) {
    previewMeta.value = null
    previewContent.value = ''
    message.info('该文件类型暂不支持预览')
    return
  }

  previewLoading.value = true
  previewMeta.value = null
  previewContent.value = ''
  try {
    const res = await getSkillsFsFile(path, source)
    previewMeta.value = {
      relPath: res.rel_path,
      filename: res.filename,
      source: res.source,
    }
    previewContent.value = res.content
  } catch (e: any) {
    message.error(e.message || '读取失败')
  } finally {
    previewLoading.value = false
    if (isMobile.value && previewMeta.value) {
      treeDrawerOpen.value = false
    }
  }
}

function openZipModal() {
  zipFile.value = null
  showZipModal.value = true
}

function handleZipFileChange(options: { file: { file?: File | null } }) {
  const f = options.file.file
  zipFile.value = f ?? null
}

async function confirmZipUpload() {
  if (!zipFile.value) {
    message.warning('请选择 ZIP 文件')
    return
  }
  zipLoading.value = true
  try {
    const r = await uploadSkillsFsZip(zipFile.value)
    if (r.success) {
      message.success(r.message)
      showZipModal.value = false
      await loadTree()
    } else {
      message.error(r.message || '上传失败')
    }
  } catch (e: any) {
    message.error(e.message || '上传失败')
  } finally {
    zipLoading.value = false
  }
}
</script>

<template>
  <div class="skills-management">
    <header class="page-header">
      <div class="page-header-main">
        <div class="page-title-row">
          <n-icon :component="FolderOpen" size="24" class="page-title-icon" />
          <div>
            <h1 class="page-title">技能中心</h1>
            <p class="page-subtitle">
              管理 Agent 可调用的能力包。平台预置技能只读可用；个人技能支持上传 ZIP，右键顶层技能目录可删除。
            </p>
          </div>
        </div>
      </div>
      <n-space class="page-header-actions">
        <n-button @click="loadTree">
          <template #icon>
            <n-icon><Refresh /></n-icon>
          </template>
          刷新
        </n-button>
        <n-button type="primary" @click="openZipModal">
          <template #icon>
            <n-icon><CloudUpload /></n-icon>
          </template>
          上传技能
        </n-button>
      </n-space>
    </header>

    <div v-if="loading" class="loading">
      <n-spin size="large" />
      <span>加载技能目录...</span>
    </div>

    <div v-else-if="error" class="error-wrap">
      <n-empty :description="error">
        <template #extra>
          <n-button @click="loadTree">重试</n-button>
        </template>
      </n-empty>
    </div>

    <n-layout v-else has-sider class="fs-layout" :class="{ 'fs-layout--mobile': isMobile }" bordered>
      <n-layout-sider
        v-if="!isMobile"
        content-style="padding: 0;"
        :width="320"
        show-trigger
        collapse-mode="width"
        bordered
      >
        <SkillsTreePanel
          :tree-data="treeData"
          :selected-keys="selectedKeys"
          :context-menu-show="contextMenuShow"
          :context-menu-x="contextMenuX"
          :context-menu-y="contextMenuY"
          :context-menu-options="contextMenuOptions"
          :render-tree-label="renderTreeLabel"
          :node-props="nodeProps"
          @update-selected-keys="onUpdateSelectedKeys"
          @context-menu-select="handleContextMenuSelect"
          @context-menu-close="closeContextMenu"
          @open-zip-modal="openZipModal"
        />
      </n-layout-sider>

      <n-layout-content content-style="padding: 0;" :native-scrollbar="false">
        <div v-if="isMobile" class="mobile-tree-bar">
          <n-button size="small" quaternary @click="treeDrawerOpen = true">
            <template #icon>
              <n-icon><FolderOpen /></n-icon>
            </template>
            技能目录
          </n-button>
          <n-text v-if="previewMeta" depth="3" class="mobile-preview-hint">
            {{ previewMeta.filename }}
          </n-text>
        </div>
        <div v-if="previewLoading" class="preview-state">
          <n-spin size="medium" />
          <span>读取文件...</span>
        </div>

        <template v-else-if="previewMeta">
          <div class="preview-header">
            <div class="preview-title-row">
              <n-icon :component="DocumentText" size="18" class="preview-file-icon" />
              <span class="preview-filename">{{ previewMeta.filename }}</span>
              <n-tag
                size="small"
                :type="previewMeta.source === 'platform' ? 'info' : 'success'"
                :bordered="false"
              >
                {{ SOURCE_LABEL[previewMeta.source] }}
              </n-tag>
            </div>
            <n-text depth="3" class="preview-path">{{ previewMeta.relPath }}</n-text>
          </div>
          <div class="preview-body">
            <FilePreview
              :path="previewMeta.relPath"
              :content="previewContent"
              :show-path="false"
              density="comfortable"
            />
          </div>
        </template>

        <div v-else class="preview-state">
          <n-empty :description="isMobile ? '点击「技能目录」选择文件预览' : '在左侧选择文件以预览'">
            <template v-if="showEmptyHint" #extra>
              <n-text depth="3">您还没有个人技能，可点击右上角「上传技能」添加</n-text>
            </template>
          </n-empty>
        </div>
      </n-layout-content>
    </n-layout>

    <n-drawer
      v-if="isMobile"
      v-model:show="treeDrawerOpen"
      placement="left"
      :width="treeDrawerWidth"
      :trap-focus="false"
      :block-scroll="true"
    >
      <n-drawer-content
        title="技能目录"
        closable
        body-content-style="padding: 0; height: 100%;"
      >
        <SkillsTreePanel
          :tree-data="treeData"
          :selected-keys="selectedKeys"
          :context-menu-show="contextMenuShow"
          :context-menu-x="contextMenuX"
          :context-menu-y="contextMenuY"
          :context-menu-options="contextMenuOptions"
          :render-tree-label="renderTreeLabel"
          :node-props="nodeProps"
          @update-selected-keys="onUpdateSelectedKeys"
          @context-menu-select="handleContextMenuSelect"
          @context-menu-close="closeContextMenu"
          @open-zip-modal="openZipModal"
        />
      </n-drawer-content>
    </n-drawer>

    <n-modal
      v-model:show="showZipModal"
      preset="card"
      title="上传个人技能"
      :style="{ width: modalWidth }"
    >
      <p class="upload-desc">
        选择包含技能包的 ZIP 文件，系统将解压到您的个人技能库。请保持包内目录结构完整（通常包含 <code>SKILL.md</code>）。
      </p>
      <n-form label-placement="top">
        <n-form-item label="ZIP 文件" required>
          <n-upload accept=".zip" :max="1" @change="handleZipFileChange">
            <n-button>选择文件</n-button>
          </n-upload>
        </n-form-item>
      </n-form>
      <div class="upload-tips">
        <n-icon :component="LockClosed" size="14" />
        <span>个人技能仅对您可见，不会影响平台预置技能</span>
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showZipModal = false">
            取消
          </n-button>
          <n-button type="primary" :loading="zipLoading" @click="confirmZipUpload">
            上传并解压
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.skills-management {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: var(--noesis-shell-padding-desktop) 24px;
  box-sizing: border-box;
  gap: 16px;
}

.fs-layout--mobile {
  border: 1px solid var(--n-border-color);
}

.mobile-tree-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--n-border-color);
  flex-shrink: 0;
}

.mobile-preview-hint {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.page-header-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

@media (max-width: 1024px) {
  .skills-management {
    padding: 12px var(--noesis-content-gutter-mobile);
    gap: 12px;
  }

  .page-header {
    flex-direction: column;
    align-items: stretch;
  }

  .page-subtitle {
    max-width: none;
  }

  .tree-wrap {
    max-height: none;
  }

  .preview-body {
    max-height: calc(100dvh - 280px);
  }
}

@media (max-width: 768px) {
  .page-header-actions :deep(.n-button span:not(.n-button__icon)) {
    display: none;
  }

  .page-header-actions :deep(.n-button) {
    padding: 0 10px;
  }
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-shrink: 0;
  gap: 16px;
}

.page-title-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.page-title-icon {
  color: var(--n-primary-color);
  margin-top: 2px;
  flex-shrink: 0;
}

.page-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  line-height: 1.3;
}

.page-subtitle {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--n-text-color-3);
  line-height: 1.5;
  max-width: 560px;
}

.loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 48px;
  color: var(--n-text-color-3);
}

.error-wrap {
  padding: 40px 0;
}

.fs-layout {
  flex: 1;
  min-height: 0;
  border-radius: 10px;
  overflow: hidden;
}

.sider-header {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 14px 16px 10px;
  border-bottom: 1px solid var(--n-border-color);
}

.sider-hint {
  font-size: 12px;
}

.tree-wrap {
  padding: 8px 10px 12px;
  max-height: calc(100vh - 260px);
  overflow: auto;
}

.tree-empty {
  padding: 32px 16px;
}

.tree-root-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}

.tree-root-icon {
  opacity: 0.75;
}

.preview-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  height: 100%;
  min-height: 280px;
  padding: 24px;
  color: var(--n-text-color-3);
}

.preview-header {
  padding: 14px 18px;
  border-bottom: 1px solid var(--n-border-color);
  flex-shrink: 0;
}

.preview-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.preview-file-icon {
  color: var(--n-text-color-3);
}

.preview-filename {
  font-size: 15px;
  font-weight: 600;
}

.preview-path {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.preview-body {
  padding: 12px 16px 16px;
  overflow: auto;
}

.upload-desc {
  margin: 0 0 16px;
  font-size: 13px;
  color: var(--n-text-color-2);
  line-height: 1.6;
}

.upload-desc code {
  font-size: 12px;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--n-code-color);
}

.upload-tips {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  font-size: 12px;
  color: var(--n-text-color-3);
}
</style>
