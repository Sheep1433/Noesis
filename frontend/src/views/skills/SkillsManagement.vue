<script setup lang="ts">
import type { SkillFsTreeNode, SkillFsTreeResponse } from '@/api/skills'
import { CloudUpload, FolderOpen, Refresh } from '@vicons/ionicons-v5'
import {
  NAlert,
  NButton,
  NCode,
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
  NText,
  NTree,
  NUpload,
  useMessage,
} from 'naive-ui'
import { computed, onMounted, ref } from 'vue'
import { getSkillsFsFile, getSkillsFsTree, uploadSkillsFsZip } from '@/api/skills'

const message = useMessage()

const loading = ref(true)
const error = ref<string | null>(null)
const treePayload = ref<SkillFsTreeResponse | null>(null)
const treeData = computed(() => treePayload.value?.tree ?? [])

const selectedKeys = ref<string[]>([])
const previewLoading = ref(false)
const previewPath = ref('')
const previewContent = ref('')

const showZipModal = ref(false)
const zipFile = ref<File | null>(null)
const zipLoading = ref(false)

onMounted(async () => {
  await loadTree()
})

async function loadTree() {
  loading.value = true
  error.value = null
  previewPath.value = ''
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

async function onUpdateSelectedKeys(keys: Array<string | number>) {
  const raw = keys[0]
  const key = raw == null ? '' : String(raw)
  selectedKeys.value = key ? [key] : []

  if (!key) {
    previewPath.value = ''
    previewContent.value = ''
    return
  }

  const leaf = isLeafKey(key, treeData.value)
  if (leaf !== true) {
    previewPath.value = ''
    previewContent.value = ''
    return
  }

  previewLoading.value = true
  previewPath.value = key
  previewContent.value = ''
  try {
    const res = await getSkillsFsFile(key)
    previewContent.value = res.content
  } catch (e: any) {
    message.error(e.message || '读取失败')
    previewPath.value = ''
  } finally {
    previewLoading.value = false
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
    <div class="toolbar">
      <div class="toolbar-title">
        <n-icon :component="FolderOpen" size="22" class="toolbar-icon" />
        <span>Skills 文件目录</span>
      </div>
      <n-space>
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
          上传 skill
        </n-button>
      </n-space>
    </div>

    <n-alert v-if="treePayload" type="info" class="hint-alert" :show-icon="false">
      <div class="hint-lines">
        <div>
          <n-text depth="3">展示的是服务器上配置的 Skills 根目录（默认仓库内 </n-text>
          <n-text code>extensions/skills</n-text>
          <n-text depth="3">）。新增或修改 skill 请在该目录下操作，或点击「上传 skill」将 ZIP 解压到该根目录；修改后点击「刷新」。</n-text>
        </div>
        <div class="root-path">
          <n-text depth="3">当前根路径：</n-text>
          <n-text code>{{ treePayload.root_path }}</n-text>
        </div>
      </div>
    </n-alert>

    <n-alert
      v-if="treePayload && !treePayload.root_exists"
      type="warning"
      title="目录尚未创建"
      class="missing-alert"
    >
      请在服务器上创建该路径，或点击「上传 skill」上传 ZIP（会在根路径下解压，不存在时会尝试创建根目录）。
    </n-alert>

    <div v-if="loading" class="loading">
      <n-spin size="large" />
      <span>加载目录...</span>
    </div>

    <div v-else-if="error" class="error-wrap">
      <n-alert type="error" :title="error" />
    </div>

    <n-layout v-else has-sider class="fs-layout" bordered>
      <n-layout-sider content-style="padding: 12px;" :width="340" show-trigger collapse-mode="width">
        <div v-if="treeData.length > 0" class="tree-wrap">
          <n-tree
            :data="treeData"
            :selected-keys="selectedKeys"
            block-line
            show-line
            selectable
            @update:selected-keys="onUpdateSelectedKeys"
          />
        </div>
        <n-empty v-else description="目录为空" />
      </n-layout-sider>
      <n-layout-content content-style="padding: 16px;" :native-scrollbar="false">
        <div v-if="previewLoading" class="preview-loading">
          <n-spin size="medium" />
          <span>读取文件...</span>
        </div>
        <template v-else-if="previewPath">
          <div class="preview-header">
            <n-text depth="3">文件：</n-text>
            <n-text code>{{ previewPath }}</n-text>
          </div>
          <n-code
            :code="previewContent"
            language="markdown"
            show-line-numbers
            word-wrap
            class="preview-code"
          />
        </template>
        <n-empty v-else description="在左侧树中点击文件即可预览" />
      </n-layout-content>
    </n-layout>

    <n-modal
      v-model:show="showZipModal"
      preset="card"
      title="上传 skill"
      style="width: 480px"
    >
      <n-form label-placement="top">
        <n-form-item label="ZIP 文件（解压到当前配置的 Skills 根目录，包内目录结构保留）" required>
          <n-upload accept=".zip" :max="1" @change="handleZipFileChange">
            <n-button>选择 ZIP</n-button>
          </n-upload>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showZipModal = false">
            取消
          </n-button>
          <n-button type="primary" :loading="zipLoading" @click="confirmZipUpload">
            解压上传
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
  padding: 16px 20px;
  box-sizing: border-box;
  gap: 12px;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}

.toolbar-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 18px;
  font-weight: 600;
}

.toolbar-icon {
  color: var(--n-primary-color);
}

.hint-alert,
.missing-alert {
  flex-shrink: 0;
}

.hint-lines {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.root-path {
  word-break: break-all;
}

.loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 48px;
  color: #666;
}

.error-wrap {
  max-width: 480px;
}

.fs-layout {
  flex: 1;
  min-height: 0;
  background: var(--n-color);
  border-radius: 8px;
}

.tree-wrap {
  max-height: calc(100vh - 280px);
  overflow: auto;
}

.preview-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #666;
}

.preview-header {
  margin-bottom: 12px;
  word-break: break-all;
}

.preview-code {
  max-height: calc(100vh - 260px);
  overflow: auto;
}
</style>
