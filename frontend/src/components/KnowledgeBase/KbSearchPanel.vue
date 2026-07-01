<script setup lang="ts">
import type { KbSearchMode } from '@/api/knowledgeBase'
import { Search } from '@vicons/ionicons-v5'
import {
  NButton,
  NCollapse,
  NCollapseItem,
  NFormItem,
  NIcon,
  NInput,
  NInputNumber,
  NRadioButton,
  NRadioGroup,
  NSwitch,
} from 'naive-ui'

const props = defineProps<{
  loading?: boolean
  defaultLimit?: number
  defaultRecallTopK?: number
  defaultUseReranker?: boolean
  defaultRrfK?: number
}>()

const emit = defineEmits<{
  search: []
}>()

const query = defineModel<string>('query', { default: '' })
const searchMode = defineModel<KbSearchMode>('searchMode', { default: 'hybrid' })
const finalTopKOverride = defineModel<number | null>('finalTopKOverride', { default: null })
const recallTopKOverride = defineModel<number | null>('recallTopKOverride', { default: null })
const useRerankerOverride = defineModel<boolean | null>('useRerankerOverride', { default: null })
const scoreThresholdOverride = defineModel<number | null>('scoreThresholdOverride', { default: null })
const rrfKOverride = defineModel<number | null>('rrfKOverride', { default: null })
const filterFileName = defineModel<string>('filterFileName', { default: '' })

function onSearch() {
  emit('search')
}
</script>

<template>
  <div class="kb-search-panel">
    <n-input
      v-model:value="query"
      type="textarea"
      placeholder="输入问题，测试本知识库的 Hybrid 检索与 Rerank 效果…"
      :autosize="{ minRows: 3, maxRows: 6 }"
      @keydown.enter.exact.prevent="onSearch"
    />
    <div class="toolbar">
      <n-radio-group v-model:value="searchMode" size="small">
        <n-radio-button value="vector">
          语义
        </n-radio-button>
        <n-radio-button value="bm25">
          关键词
        </n-radio-button>
        <n-radio-button value="hybrid">
          混合
        </n-radio-button>
      </n-radio-group>
      <n-button
        type="primary"
        :loading="loading"
        :disabled="!query.trim()"
        @click="onSearch"
      >
        <template #icon>
          <n-icon><Search /></n-icon>
        </template>
        检索
      </n-button>
    </div>
    <n-collapse>
      <n-collapse-item title="高级参数（单次调试覆盖）" name="advanced">
        <div class="advanced-grid">
          <n-form-item label="final_top_k" :show-feedback="false">
            <n-input-number
              v-model:value="finalTopKOverride"
              :min="1"
              :max="50"
              clearable
              :placeholder="`默认 ${props.defaultLimit ?? 10}`"
              style="width: 100%"
            />
          </n-form-item>
          <n-form-item label="recall_top_k" :show-feedback="false">
            <n-input-number
              v-model:value="recallTopKOverride"
              :min="1"
              :max="200"
              clearable
              :placeholder="`默认 ${props.defaultRecallTopK ?? 50}`"
              style="width: 100%"
            />
          </n-form-item>
          <n-form-item label="rrf_k" :show-feedback="false">
            <n-input-number
              v-model:value="rrfKOverride"
              :min="1"
              :max="500"
              clearable
              :placeholder="`默认 ${props.defaultRrfK ?? 60}`"
              style="width: 100%"
            />
          </n-form-item>
          <n-form-item label="score_threshold" :show-feedback="false">
            <n-input-number
              v-model:value="scoreThresholdOverride"
              :min="0"
              :max="1"
              :step="0.01"
              clearable
              placeholder="使用集合默认"
              style="width: 100%"
            />
          </n-form-item>
          <n-form-item label="use_reranker" :show-feedback="false">
            <n-switch
              :value="useRerankerOverride ?? props.defaultUseReranker ?? true"
              @update:value="useRerankerOverride = $event"
            />
          </n-form-item>
          <n-form-item label="限定文档 file_name" :show-feedback="false">
            <n-input
              v-model:value="filterFileName"
              clearable
              placeholder="精确匹配文件名"
            />
          </n-form-item>
        </div>
      </n-collapse-item>
    </n-collapse>
  </div>
</template>

<style scoped>
.kb-search-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.advanced-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 16px;
}

@media (max-width: 900px) {
  .advanced-grid {
    grid-template-columns: 1fr;
  }
}
</style>
