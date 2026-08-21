<script setup lang="ts">
import type { SearchResult, ShardDetail as ShardDetailType } from '@/api/knowledgeBase'
import { NDrawer, NDrawerContent } from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { getShardDetail } from '@/api/knowledgeBase'
import ChunkDetailPanel from '@/components/KnowledgeBase/ChunkDetailPanel.vue'
import { useBreakpoint } from '@/hooks/useBreakpoint'

const props = defineProps<{
  show: boolean
  collectionName: string
  shardId: string
  searchContext?: SearchResult | null
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
}>()

const { isMobile } = useBreakpoint()
const { width: windowWidth } = useWindowSize()
const drawerWidth = computed(() => isMobile.value ? windowWidth.value : Math.min(720, windowWidth.value - 48))
const loading = ref(false)
const error = ref<string | null>(null)
const detail = ref<ShardDetailType | null>(null)

watch(
  () => [props.show, props.collectionName, props.shardId],
  async ([show, collectionName, shardId]) => {
    if (!show || !collectionName || !shardId) {
      return
    }
    loading.value = true
    error.value = null
    try {
      detail.value = await getShardDetail(collectionName, shardId)
    } catch (e: any) {
      error.value = e.message || '分块详情加载失败'
      detail.value = null
    } finally {
      loading.value = false
    }
  },
)
</script>

<template>
  <n-drawer
    :show="show"
    :width="drawerWidth"
    class="search-chunk-drawer-shell"
    :class="{ 'search-chunk-drawer-shell--mobile': isMobile }"
    :placement="isMobile ? 'bottom' : 'right'"
    :height="isMobile ? 'min(82vh, 720px)' : undefined"
    @update:show="emit('update:show', $event)"
  >
    <n-drawer-content title="分块详情" closable class="search-chunk-drawer">
      <ChunkDetailPanel
        :detail="detail"
        :loading="loading"
        :error="error"
        :search-context="searchContext"
      />
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.search-chunk-drawer :deep(.n-drawer-body-content-wrapper) {
  padding: 0;
  overflow: hidden;
}

.search-chunk-drawer-shell--mobile :deep(.n-drawer-content) {
  border-radius: 16px 16px 0 0;
}
</style>
