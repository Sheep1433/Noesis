<script lang="ts" setup>
import type FileUploadManager from '@/views/FileUploadManager.vue'
import ModelSelector from '@/components/Chat/ModelSelector.vue'
import KbScopeSelector from '@/components/KnowledgeBase/KbScopeSelector.vue'

const props = defineProps<{
  qaType: string
  sessionId: string
  disabled?: boolean
  fileUploadRef?: InstanceType<typeof FileUploadManager> | null
}>()

const selectedModelId = defineModel<string>('modelId', { default: '' })
const selectedKbCollections = defineModel<string[]>('kbCollections', { default: () => [] })

const showKbScope = computed(() => props.qaType === 'COMMON_QA')
const showModelSelector = computed(() => props.qaType !== 'TEST_CASE_QA')
const plusOpen = ref(false)

const uploadOptions = computed(() => props.fileUploadRef?.options ?? [])
</script>

<template>
  <div class="composer-toolbar">
    <div class="composer-toolbar__left">
      <n-popover
        v-model:show="plusOpen"
        trigger="click"
        placement="top-start"
        :disabled="disabled"
        class="composer-plus-popover"
      >
        <template #trigger>
          <button
            type="button"
            class="composer-plus-btn"
            :disabled="disabled"
            aria-label="添加附件或设置知识库"
          >
            <span class="i-carbon:add text-18"></span>
          </button>
        </template>

        <div class="composer-plus-panel">
          <div
            v-for="item in uploadOptions"
            :key="String(item.key)"
            class="composer-plus-panel__item"
          >
            <component
              :is="{ render: item.render }"
              v-if="item.type === 'render' && item.render"
            />
          </div>

          <template v-if="showKbScope">
            <div class="composer-plus-panel__divider"></div>
            <KbScopeSelector
              v-model="selectedKbCollections"
              :session-id="sessionId"
              :disabled="disabled"
              embedded
            />
          </template>
        </div>
      </n-popover>

      <ModelSelector
        v-if="showModelSelector"
        v-model="selectedModelId"
        :session-id="sessionId"
        :disabled="disabled"
      />
    </div>

    <div class="composer-toolbar__right">
      <slot name="right"></slot>
    </div>
  </div>
</template>

<style scoped>
.composer-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 8px;
  min-height: 32px;
}

.composer-toolbar__left,
.composer-toolbar__right {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.composer-toolbar__right {
  margin-left: auto;
  flex-shrink: 0;
}

.composer-plus-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--noesis-text-secondary, #6b7280);
  cursor: pointer;
  transition: background-color 0.2s ease, color 0.2s ease;
}

.composer-plus-btn:hover:not(:disabled) {
  background: var(--noesis-color-primary-bg-subtle, rgb(0 0 0 / 4%));
  color: var(--noesis-text-primary, #111);
}

.composer-plus-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.composer-plus-panel {
  min-width: 200px;
  padding: 4px 0;
}

.composer-plus-panel__divider {
  height: 1px;
  margin: 6px 12px;
  background: var(--noesis-border-subtle, rgb(0 0 0 / 8%));
}

.composer-plus-panel__item :deep(.px-4) {
  padding-left: 0;
  padding-right: 0;
}
</style>
