<script lang="ts" setup>
import AssistantReplyToolbar from '@/components/AssistantReplyToolbar/index.vue'
import SubagentCollapse from '@/components/SubagentCollapse/index.vue'
import ToolCallCollapse from '@/components/ToolCallCollapse/index.vue'
import { TASK_TOOL_NAME } from '@/utils/parseTaskTool'
import { shouldRenderToolCallCollapse } from '@/utils/parseWriteTodosInput'
import MarkdownInstance from './plugins/markdown'

// code高亮语法样式
import 'highlight.js/styles/atom-one-dark-reasonable.css'

interface Props {
  isInit?: boolean
  isView?: boolean
  content?: string
  toolCalls?: any[] | null
  msgMetadata?: any
  /** 为 false 时不展示底部问答类型与操作按钮（由父级统一气泡聚合展示） */
  showActionBar?: boolean
  /** full：独立气泡；segment：嵌入助手统一卡片内的正文片段 */
  variant?: 'full' | 'segment'
  /** 底部工具栏左侧问答类型角标 */
  qaType?: string
}

interface Emits {
  (e: 'completed'): void
  (e: 'failed', error: any): void
  (e: 'recycleQa'): void
  (e: 'praiseFeadBack'): void
  (e: 'belittleFeedback'): void
}

const props = withDefaults(defineProps<Props>(), {
  isInit: false,
  content: '',
  toolCalls: null,
  msgMetadata: null,
  showActionBar: true,
  variant: 'full',
  qaType: 'COMMON_QA',
})

const emit = defineEmits<Emits>()

const isCompleted = ref(false)
const refWrapperContent = ref<HTMLElement>()

const displayText = ref('')

watch(
  () => props.content,
  (newContent) => {
    if (newContent) {
      displayText.value = newContent
      isCompleted.value = true
    }
  },
  { immediate: true },
)

const renderedMarkdown = computed(() => {
  return MarkdownInstance.render(displayText.value)
})

const renderedContent = computed(() => {
  return `${renderedMarkdown.value}`
})

const onCompleted = () => {
  isCompleted.value = true
  emit('completed')
}

const praiseFeedback = () => emit('praiseFeadBack')
const belittleFeedback = () => emit('belittleFeedback')
const handleRecycleAquestion = () => emit('recycleQa')

onMounted(() => {
  // segment 由父级 SSE 控制整轮加载态；挂载即有 content 不代表流结束
  if (props.variant === 'full' && props.content) {
    onCompleted()
  }
})
</script>

<template>
  <n-spin
    relative
    flex="1 ~"
    min-h-0
    w-full
    h-full
    content-class="w-full h-full flex"
    :show="false"
    :rotate="false"
    :class="[
      variant === 'full' ? 'bg-bgcolor' : 'bg-transparent',
    ]"
    :style="{ '--n-opacity-spinning': '0.3' }"
  >
    <template #icon>
      <div class="i-svg-spinners:3-dots-rotate"></div>
    </template>
    <div flex="1 ~" min-w-0 min-h-0>
      <div
        text-16
        class="w-full h-full overflow-hidden"
        :class="[!displayText && 'flex items-center justify-center']"
      >
        <div
          v-if="displayText"
          ref="refWrapperContent"
          text-16
          class="w-full h-full overflow-y-auto"
          :class="variant === 'segment' ? 'px-15px py-2' : 'p-15px'"
        >
          <div class="markdown-wrapper" :class="{ 'markdown-wrapper--segment': variant === 'segment' }" v-html="renderedContent"></div>

          <div
            v-if="showActionBar && isCompleted"
            :style="variant === 'full'
              ? { 'width': '80%', 'margin-left': '10%', 'margin-right': '10%' }
              : {}"
          >
            <AssistantReplyToolbar
              :qa-type="qaType"
              :copy-text="displayText"
              @praise-fead-back="praiseFeedback"
              @belittle-feedback="belittleFeedback"
              @recycle-qa="handleRecycleAquestion"
            />
          </div>

          <div v-if="isCompleted && toolCalls && toolCalls.length > 0" class="tool-calls-wrapper">
            <template v-for="(call, index) in toolCalls" :key="call.tool_call_id || index">
              <SubagentCollapse
                v-if="call.name === TASK_TOOL_NAME"
                appearance="light"
                :input="call.arguments ?? {}"
                :output="call.result ?? ''"
                :status="call.status || 'success'"
                :defaultOpen="false"
              />
              <ToolCallCollapse
                v-else-if="shouldRenderToolCallCollapse(call.name, call.arguments)"
                :name="call.name"
                :arguments="call.arguments"
                :result="call.result"
                :status="call.status || 'success'"
                :defaultOpen="false"
              />
            </template>
          </div>
        </div>
      </div>
    </div>
  </n-spin>
</template>

<style lang="scss">
.markdown-wrapper {
  margin-left: 10%;
  margin-right: 10%;
  background-color: var(--noesis-color-bg-elevated);
  padding: 1px 18px;
  border-top-right-radius: 16px;
  border-top-left-radius: 16px;
  color: var(--noesis-color-text-table);
  font-family:
    -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell,
    'Open Sans', 'Helvetica Neue', Arial, sans-serif, system-ui, "SF Pro Text";
  font-size: 16px;
  line-height: 1.7;
  font-weight: 400;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizelegibility;

  h1 { font-size: 2em; }
  h2 { font-size: 1.5em; padding-bottom: 0.3em; border-bottom: 1px solid var(--noesis-markdown-heading-border); }
  h3 { font-size: 1.25em; }
  h4 { font-size: 1em; }
  h5 { font-size: 0.875em; }
  h6 { font-size: 0.85em; }

  h1, h2, h3, h4, h5, h6 {
    margin: 0 auto;
    line-height: 1.25;
    margin-top: 20px;
    margin-bottom: 15px;
  }

  ul, ol {
    padding-left: 1.5em;
    line-height: 0.8;
  }

  ul, li, ol {
    list-style-position: outside;
    white-space: normal;
  }

  li { line-height: 2; }
  ol ol { padding-left: 20px; }
  ul ul { padding-left: 20px; }
  hr { margin: 16px 0; }

  a {
    color: $color-default;
    font-weight: bolder;
    text-decoration: underline;
    padding: 0 3px;
    display: block;
  }

  p {
    line-height: 2;
    margin: 10px 16px;
    & > code {
      background-color: transparent;
      white-space: pre;
      padding: 2px 4px;
      border-radius: 4px;
      font-size: 0.9em;
    }
    img { display: inline-block; }
  }

  li > p { line-height: 2; }

  blockquote {
    padding: 10px;
    margin: 20px 0;
    border-left: 5px solid var(--noesis-color-border);
    background-color: var(--noesis-color-bg-hover);
    color: var(--noesis-color-text-secondary);
    & > p { margin: 0; }
  }

  table {
    border-collapse: collapse;
    width: 100%;
  }

  th, td {
    border: 1px solid var(--noesis-color-bg);
    padding: 8px;
    text-align: left;
  }

  th { background-color: var(--noesis-color-bg-muted); }

  img {
    width: 95%;
    height: auto;
    object-fit: cover;
    display: block;
    margin: 0;
  }

  .active-tab {
    background: var(--noesis-chat-tab-active-bg);
    border-color: var(--noesis-color-primary);
    color: var(--noesis-color-primary);
  }
}

.markdown-wrapper.markdown-wrapper--segment {
  margin-left: 0;
  margin-right: 0;
  width: 100%;
  border-radius: 0;
  box-sizing: border-box;
}

.tool-calls-wrapper {
  width: 80%;
  margin-left: 10%;
  margin-right: 10%;
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
</style>
