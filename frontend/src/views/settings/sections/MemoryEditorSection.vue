<script setup lang="ts">
import { NButton, NInput, useMessage } from 'naive-ui'
import { onMounted, ref, watch } from 'vue'
import { getUserMemoryFile, putUserMemoryFile } from '@/api/settings'
import FilePreview from '@/components/FilePreview/index.vue'

const props = defineProps<{
  file: 'USER.md' | 'AGENTS.md'
  title: string
  description: string
}>()

const message = useMessage()
const content = ref('')
const draft = ref('')
const updatedAt = ref<string | undefined>()
const saving = ref(false)
const loading = ref(false)
const editing = ref(false)

async function load() {
  loading.value = true
  try {
    const data = await getUserMemoryFile(props.file)
    content.value = data?.content ?? ''
    draft.value = content.value
    updatedAt.value = data?.updated_at
    editing.value = !content.value.trim()
  } catch (e) {
    message.error(e instanceof Error ? e.message : '加载失败')
  } finally {
    loading.value = false
  }
}

function startEdit() {
  draft.value = content.value
  editing.value = true
}

function cancelEdit() {
  draft.value = content.value
  editing.value = false
}

async function save() {
  saving.value = true
  try {
    const data = await putUserMemoryFile(props.file, draft.value)
    content.value = draft.value
    updatedAt.value = data?.updated_at
    editing.value = false
    message.success('已保存')
  } catch (e) {
    message.error(e instanceof Error ? e.message : '保存失败')
  } finally {
    saving.value = false
  }
}

watch(() => props.file, () => {
  void load()
})

onMounted(() => {
  void load()
})
</script>

<template>
  <section class="pane">
    <h2>{{ title }}</h2>
    <p class="hint">
      {{ description }}
    </p>
    <p v-if="updatedAt" class="meta">
      最近修改：{{ updatedAt }}
    </p>

    <div class="editor-area">
      <n-input
        v-if="editing"
        v-model:value="draft"
        type="textarea"
        :autosize="{ minRows: 16, maxRows: 40 }"
        :disabled="loading || saving"
        placeholder="使用 Markdown 编写…"
      />
      <FilePreview
        v-else
        :path="file"
        :content="content"
        :loading="loading"
        :show-path="false"
        :show-toolbar="false"
        density="comfortable"
      />
    </div>

    <div class="pane-footer">
      <template v-if="editing">
        <n-button :disabled="saving" @click="cancelEdit">
          取消
        </n-button>
        <n-button type="primary" :loading="saving" @click="save">
          保存
        </n-button>
      </template>
      <n-button
        v-else
        type="primary"
        ghost
        :disabled="loading"
        @click="startEdit"
      >
        编辑
      </n-button>
    </div>
  </section>
</template>

<style scoped>
.pane h2 {
  margin: 0 0 8px;
  font-size: 18px;
  color: var(--noesis-color-text-heading);
}

.hint,
.meta {
  margin: 0 0 12px;
  color: var(--noesis-color-text-secondary);
  font-size: 13px;
}

.editor-area {
  max-width: 720px;
}

.pane-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
  max-width: 720px;
}
</style>
