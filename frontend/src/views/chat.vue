<script lang="tsx" setup>
import type { InputInst, UploadFileInfo } from 'naive-ui'
import type { ComposerMention, MentionCandidate } from '@/hooks/useMentionCatalog'
import type { ChatAttachmentItem } from '@/store/business'
import type { ChatModeQaType } from '@/utils/qaType'
import type { MessageContentV1, UiPart } from '@/views/chat/messageParts'
import { ensureSession, getSession, updateSessionMeta, updateSessionTitle } from '@/api/chat'
import AssistantReplyToolbar from '@/components/AssistantReplyToolbar/index.vue'
import ChatComposerToolbar from '@/components/Chat/ChatComposerToolbar.vue'
import ChatModeSelector from '@/components/Chat/ChatModeSelector.vue'
import MentionPicker from '@/components/Chat/MentionPicker.vue'
import CitationSources from '@/components/CitationSources/index.vue'
import ContextWindowIndicator from '@/components/ContextWindowIndicator/index.vue'
import HitlComposerPanel from '@/components/HitlComposerPanel/index.vue'
import ReasoningBlock from '@/components/ReasoningBlock/index.vue'
import ResizeDivider from '@/components/ResizeDivider.vue'
import SubagentCollapse from '@/components/SubagentCollapse/index.vue'
import TodoList from '@/components/TodoList/index.vue'
import ToolCallCollapse from '@/components/ToolCallCollapse/index.vue'
import { langfuseUiOrigin } from '@/config'
import { buildFileDict } from '@/config/chat'
import { composerPlaceholder, supportsAtMentions, supportsSlashSkills } from '@/config/subagents'
import { cssVar, themeColors, themeCssVar } from '@/config/theme'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import {
  candidateToMention,
  ensureMentionCatalog,
  formatMentionToken,
  invalidateMentionContextCache,
  mentionToPayload,
} from '@/hooks/useMentionCatalog'
import { usePaneResize } from '@/hooks/usePaneResize'
import { useResponsiveDrawerWidth } from '@/hooks/useResponsiveDrawerWidth'
import { loadSessionMessages } from '@/store/business/initChatHistory'
import { isUnauthorizedError } from '@/utils/authHttp'
import { copyToClipboard } from '@/utils/copy'
import { buildDisplayParts } from '@/utils/groupAssistantParts'
import { parseWriteTodosInput, shouldApplyWriteTodos } from '@/utils/parseWriteTodosInput'
import { isChatModeChange, qaTypeLabel } from '@/utils/qaType'
import { ensureVisionModelForImageUpload } from '@/utils/visionModel'
import ChatHistoryPanel from '@/views/chat/ChatHistoryPanel.vue'
import {
  pendingHitlForSession,
  setPendingHitlForSession,
  shouldDisableHitlComposer,
  shouldShowRunContinuation,
} from '@/views/chat/hitlUiState'
import {
  appendReasoningDelta,
  appendRetrievalPart,
  appendStreamFailureNotice,
  appendTextDelta,
  appendTextDeltaWithRedactedThinking,
  appendUserStopNotice,
  applyHitlPendingParts,
  applyToolOutput,
  assistantPartsStillStreaming,
  completeLastReasoningPart,
  createRedactedThinkingStreamCtx,
  emptyMessageContent,
  extractLastTopLevelText,
  flushRedactedThinkingStreamCtx,
  formatUsageSummary,
  hasValidContextWindow,
  hasValidUsage,
  markStreamingPartsComplete,
  normalizeApiContent,
  shortenChatErrorToast,
  shouldShowAssistantToolFailureBlocker,
  syncLegacyFieldsFromParts,
  upsertToolInputPart,
} from '@/views/chat/messageParts'
import SessionContextPanel from '@/views/chat/SessionContextPanel.vue'
import { useSSEStream } from '@/views/chat/useSSEStream'
import DefaultPage from './DefaultPage.vue'
import FileListItem from './FileListItem.vue'
import FileUploadManager from './FileUploadManager.vue'
import SuggestedView from './SuggestedPage.vue'
import TableModal from './TableModal.vue'

const sessionFilesPanelRef = ref<InstanceType<typeof SessionContextPanel> | null>(null)
function retrievedResults(parts: UiPart[]) {
  return parts
    .filter((part) => part.type === 'retrieval')
    .flatMap((part) => part.type === 'retrieval' ? part.results : [])
}

type CitationSourcesHandle = InstanceType<typeof CitationSources>
const citationSourcesRefs = new Map<string, CitationSourcesHandle>()

function citationSourcesKey(item: { message_id?: string, chat_id: string }, index: number): string {
  return item.message_id || `${item.chat_id}:${index}`
}

function setCitationSourcesRef(key: string, component: unknown) {
  if (component) {
    citationSourcesRefs.set(key, component as CitationSourcesHandle)
  } else {
    citationSourcesRefs.delete(key)
  }
}

function openCitationSource(key: string, number: number) {
  citationSourcesRefs.get(key)?.open(number)
}
/** 会话上下文侧栏（产物/附件）是否展开，默认关闭 */
const sessionFilesPanelOpen = ref(false)

/** 是否显示欢迎/默认页；作为对话首页的轻量引导 */
const showDefaultPage = ref(true)

function reloadSessionFilesPanel() {
  invalidateMentionContextCache(uuids.value[qa_type.value] || undefined)
  if (!sessionFilesPanelOpen.value) {
    return
  }
  sessionFilesPanelRef.value?.reload()
}

function toggleSessionFilesPanel() {
  sessionFilesPanelOpen.value = !sessionFilesPanelOpen.value
  if (sessionFilesPanelOpen.value) {
    nextTick(() => reloadSessionFilesPanel())
  }
}

watch(showDefaultPage, (isDefault) => {
  if (isDefault) {
    sessionFilesPanelOpen.value = false
  }
})

// 全局存储
const businessStore = useBusinessStore()
const router = useRouter()
const route = useRoute()
const naivePresetColors = useNaivePresetColors()

// 是否是刚登录到系统 批量渲染对话记录
const isInit = ref(false)

// 是否查看历史消息标识
const isView = ref(false)

// 使用 onMounted 生命周期钩子加载历史对话
// 新增：加载历史对话的状态
const isLoadingHistory = ref(false)

/** 程序化改 URL 时跳过路由 watch 的二次恢复，避免重复拉消息 */
const suppressRouteSessionSync = ref(false)

function routeSessionId(): string {
  const raw = route.params.sessionId
  return typeof raw === 'string' ? raw.trim() : ''
}

function isComposingRouteName(name: unknown): boolean {
  return name === 'ChatIndex' || name === 'ChatNew' || name === 'ChatRoot'
}

async function replaceChatSessionUrl(sessionId: string) {
  if (!sessionId) {
    return
  }
  if (route.name === 'ChatSession' && routeSessionId() === sessionId) {
    return
  }
  suppressRouteSessionSync.value = true
  try {
    await router.replace({ name: 'ChatSession', params: { sessionId } })
  } finally {
    suppressRouteSessionSync.value = false
  }
}

async function navigateToComposingUrl(replace = false) {
  const query = { ...route.query, qa_type: qa_type.value }
  if (
    isComposingRouteName(route.name)
    && !routeSessionId()
    && route.query.qa_type === qa_type.value
  ) {
    return
  }
  suppressRouteSessionSync.value = true
  try {
    const nav = replace ? router.replace : router.push
    await nav({ name: 'ChatNew', query })
  } finally {
    suppressRouteSessionSync.value = false
  }
}

async function restoreActiveSessionFromRoute(sessionId: string) {
  // 切换会话只释放浏览器 subscription，不停止服务端 Run。必须先隔离旧流，
  // 否则旧会话的 snapshot/delta 会写入新会话页面。
  sseStream.detachSubscription()
  try {
    const session = await getSession(sessionId)
    const qt = String(session.extra?.qa_type ?? '').trim() || 'COMMON_QA'
    if (qt === 'TEST_CASE_QA') {
      await router.replace({ name: 'TestCaseGenerate' })
      return
    }

    showDefaultPage.value = false
    isInit.value = false
    isView.value = true
    currentIndex.value = sessionId
    clearComposerQueue()
    businessStore.todos = []

    qa_type.value = qt
    businessStore.update_qa_type(qt)
    uuids.value[qt] = sessionId
    sessionMaterialized.value = true

    await loadSessionMessages(sessionId, conversationItems, currentRenderIndex)
    const hasUserMessage = conversationItems.value.some((item) => item.role === 'user')
    if (!hasUserMessage) {
      window.$ModalMessage.info('该会话尚无消息，已回到新对话')
      resetComposingSurface()
      await navigateToComposingUrl(true)
      return
    }
    await loadSessionContext(sessionId)
    reloadSessionFilesPanel()
    await scrollToLatestMessage(false)
    // Run 订阅可能持续数分钟；页面与历史列表恢复不应等待整轮生成结束。
    void sseStream.resumeActiveRun(sessionId)
  } catch (error) {
    console.error('恢复会话失败:', error)
    window.$ModalMessage.warning('会话不存在或无权访问，已回到新对话')
    resetComposingSurface()
    await navigateToComposingUrl(true)
  }
}

function resetComposingSurface() {
  sseStream.detachSubscription()
  sessionContext.value = null
  showDefaultPage.value = true
  isInit.value = true
  isView.value = false
  conversationItems.value = []
  stylizingLoading.value = false
  suggested_array.value = []
  currentIndex.value = null
  businessStore.todos = []
  clearComposerQueue()
  inputTextString.value = ''
  uuids.value[qa_type.value] = uuidv4()
  sessionMaterialized.value = false
  selectedKbCollections.value = []
  kbSearchEnabled.value = true
  selectedModelId.value = ''
  selectedMcpServers.value = []
  selectedSkills.value = []
  skillsAllEnabled.value = true
}

// 使用 onMounted 生命周期钩子加载历史对话
onBeforeMount(async () => {
  try {
    if (businessStore.qa_type === 'TEST_CASE_QA') {
      businessStore.update_qa_type('COMMON_QA')
    }
    applyWelcomeRouteQaType()
    isLoadingHistory.value = true
    isInit.value = true
    await fetchConversationHistory(isInit, conversationItems, tableData, currentRenderIndex, null, '')

    const sid = routeSessionId()
    if (sid) {
      await restoreActiveSessionFromRoute(sid)
    }
  } catch (error) {
    console.error('加载历史对话失败:', error)
    window.$ModalMessage.error('加载历史对话失败，请重试')
  } finally {
    isLoadingHistory.value = false
  }
})

watch(
  () => [route.name, routeSessionId()] as const,
  async ([name, sid], [prevName, prevSid]) => {
    if (suppressRouteSessionSync.value) {
      return
    }
    if (name === 'ChatSession' && sid && sid !== prevSid) {
      if (uuids.value[qa_type.value] === sid && !showDefaultPage.value && conversationItems.value.length > 0) {
        return
      }
      await restoreActiveSessionFromRoute(sid)
      return
    }
    if (
      isComposingRouteName(name)
      && (prevName === 'ChatSession' || Boolean(prevSid))
      && !showDefaultPage.value
    ) {
      resetComposingSurface()
    }
  },
)

// 管理对话
const isModalOpen = ref(false)
function openModal() {
  isModalOpen.value = true
}
/** 关闭管理弹窗：只刷新左侧列表；保留当前对话面。若当前会话已被删除则回 composing。 */
function handleModalClose(value: boolean) {
  isModalOpen.value = value
  if (value) {
    return
  }
  void refreshSidebarAfterManageClose()
}

async function refreshSidebarAfterManageClose() {
  const activeSessionId = sessionMaterialized.value
    ? String(uuids.value[qa_type.value] || '')
    : ''
  const wasShowingChat = !showDefaultPage.value

  await fetchConversationHistory(
    isInit,
    conversationItems,
    tableData,
    currentRenderIndex,
    null,
    '',
    archiveView.value ? 'only' : 'exclude',
  )

  if (!wasShowingChat || !activeSessionId) {
    return
  }
  const stillExists = tableData.value.some(
    (item) => item.chat_id === activeSessionId || item.uuid === activeSessionId,
  )
  if (!stillExists) {
    resetComposingSurface()
    void navigateToComposingUrl(true)
  }
}

// 新建对话
function newChat(targetQaType: ChatModeQaType = qa_type.value as ChatModeQaType) {
  backgroundColorVariable.value = cssVar(themeCssVar.bgElevated)

  if (isChatModeChange(qa_type.value, targetQaType)) {
    activateChatMode(targetQaType, '')
    historyDrawerOpen.value = false
    return
  }

  if (showDefaultPage.value && isComposingRouteName(route.name) && !routeSessionId()) {
    window.$ModalMessage.success(`已经是最新对话`)
    return
  }
  resetComposingSurface()
  void navigateToComposingUrl(false)
}

function changeChatMode(targetQaType: ChatModeQaType) {
  if (!isChatModeChange(qa_type.value, targetQaType)) {
    return
  }
  activateChatMode(targetQaType, '')
}

/**
 * 默认大模型（已移除，使用 useChat）
 */
// currentChatId 已移除，使用 useChat 管理 sessionId


// 对话等待提示词图标
const stylizingLoading = ref(false)

type PendingHitlState = {
  session_id: string
  run_id: string
  interrupt_id: string
  kind: string
  action_requests: Array<{ tool_call_id?: string, name?: string, args?: Record<string, unknown>, description?: string }>
  review_configs: unknown[]
  expires_at: number
  submitting: boolean
}
const pendingHitlBySession = ref<Record<string, PendingHitlState>>({})
const pendingHitl = computed<PendingHitlState | null>({
  get: () => {
    const sessionId = uuids.value[qa_type.value]
    return pendingHitlForSession(pendingHitlBySession.value, sessionId)
  },
  set: (value) => {
    const sessionId = value?.session_id || uuids.value[qa_type.value]
    if (!sessionId) {
      return
    }
    pendingHitlBySession.value = setPendingHitlForSession(
      pendingHitlBySession.value,
      sessionId,
      value,
    )
  },
})

// 输入字符串
const inputTextString = ref('')
const refInputTextString = ref<InputInst | null>()
const composerMentions = ref<ComposerMention[]>([])
const mentionPickerRef = ref<InstanceType<typeof MentionPicker> | null>(null)
const mentionPickerOpen = ref(false)
const mentionPickerQuery = ref('')
const mentionPickerCandidates = ref<MentionCandidate[]>([])
const mentionPickerLoading = ref(false)
const mentionTriggerIndex = ref(-1)
const mentionTriggerChar = ref<'/' | '@' | ''>('')

interface FileUploadRef {
  pendingUploadFileInfoList: UploadFileInfo[] | null | undefined
  options?: any[]
  reset?: () => void
  enqueueFiles?: (files: File[] | FileList) => void
  uploadAllPendingFiles?: () => Promise<ChatAttachmentItem[]>
  clearQueue?: () => void
}
const fileUploadRef = ref<FileUploadRef | null>(null)
const pendingUploadFileInfoList = ref([])

// 输出字符串 Reader 流（已移除，使用 useChat）

// markdown对象（已移除）

// 主内容区域
const messagesContainer = ref<HTMLElement | null>(null)

// 读取失败
const onFailedReader = (index: number) => {
  stylizingLoading.value = false
  if (index > 0 && conversationItems.value[index - 1]?.role === 'user') {
    contentLoadingStates.value[index - 1] = false
  }
  window.$ModalMessage.error('请求失败，请重试')
  setTimeout(() => {
    if (refInputTextString.value) {
      refInputTextString.value.select()
    }
  })
}

/** 仅聚焦输入框；全局加载态由 SSE onFinish/onError 与 stopChatStream 控制，避免 Markdown 片段挂载误触结束 */
const onCompletedReader = (_index: number) => {
  setTimeout(() => {
    if (refInputTextString.value) {
      refInputTextString.value.select()
    }
  })
}

function isLastAssistantMessage(index: number): boolean {
  for (let i = conversationItems.value.length - 1; i >= 0; i--) {
    if (conversationItems.value[i]?.role === 'assistant') {
      return i === index
    }
  }
  return false
}

/** 助手回复卡片内流式指示：整轮 SSE 未结束前保持（含多步工具间隙） */
function showAssistantReplyLoading(index: number, role: string): boolean {
  return role === 'assistant' && isLastAssistantMessage(index) && stylizingLoading.value
}

// 当前索引位置
const currentRenderIndex = ref(0)

const onRecycleQa = async (index: number) => {
  // 设置当前选中的问答类型
  const item = conversationItems.value[index - 1]
  activateChatMode(item.qa_type, item.chat_id)


  // 清空推荐列表
  suggested_array.value = []
  // 发送问题重新生成
  handleCreateStylized(item.question, item.file_key)
  scrollToBottom()
}

// 开始输出时隐藏加载提示
const onBeginRead = async (index: number) => {
  // 设置最上面的滚动提示图标隐藏
  contentLoadingStates.value[currentRenderIndex.value - 1] = false
}

// 复制用户消息原文
const handleCopyUserText = async (text: string) => {
  if (!text.trim()) {
    window.$ModalMessage.destroyAll()
    window.$ModalMessage.warning('暂无可复制内容')
    return
  }
  try {
    await copyToClipboard(text)
    window.$ModalMessage.destroyAll()
    window.$ModalMessage.success('已复制')
  } catch {
    window.$ModalMessage.destroyAll()
    window.$ModalMessage.error('复制失败')
  }
}

// 侧边栏对话历史
interface TableItem {
  uuid: string
  key: string
  chat_id: string
  qa_type: string
  pinned?: boolean
  archived?: boolean
}

function sessionQaIconClass(qt: string) {
  switch (qt) {
    case 'SUPER_AGENT_QA':
    case 'DEEP_RESEARCH_QA':
      return 'i-hugeicons:search-01'
    case 'FAULT_OPERATION_QA':
      return 'i-hugeicons:settings-01'
    case 'TEST_CASE_QA':
      return 'i-hugeicons:note-edit'
    default:
      return 'i-hugeicons:ai-chat-02'
  }
}

function sessionQaIconColor(qt: string) {
  switch (qt) {
    case 'FAULT_OPERATION_QA':
      return themeColors.qaFault
    case 'TEST_CASE_QA':
      return themeColors.qaTest
    default:
      return naivePresetColors.value.primary
  }
}

function sessionQaTooltip(qt: string) {
  return qaTypeLabel(qt)
}

const historySidebarColumns = computed(() => [
  {
    key: 'key',
    align: 'left' as const,
    ellipsis: { tooltip: false },
    render(row: TableItem) {
      const children: any[] = [
        h('div', {
          class: ['size-18px shrink-0 inline-flex items-center justify-center', sessionQaIconClass(row.qa_type)],
          style: { color: sessionQaIconColor(row.qa_type) },
          title: sessionQaTooltip(row.qa_type),
        }),
        h('span', { class: 'truncate flex-1 min-w-0' }, row.key),
      ]
      if (row.pinned) {
        children.push(h('div', {
          class: 'size-14px shrink-0 inline-flex items-center justify-center i-hugeicons:pin-02',
          style: { color: cssVar(themeCssVar.primaryTextSoft) },
          title: '已置顶',
        }))
      }
      return h(
        'div',
        { class: 'flex items-center gap-8px min-w-0 pr-4px' },
        children,
      )
    },
  },
])

const tableData = ref<TableItem[]>([])

// 保存对话历史记录
const conversationItems = ref<
  Array<{
    uuid: string
    chat_id: string
    qa_type: string
    question: string
    role: 'user' | 'assistant'
    content: string
    reasoning?: string
    file_key: ChatAttachmentItem[]
    mentions?: ComposerMention[]
    tool_calls?: any[]
    messageContent?: MessageContentV1
    msg_metadata?: any
    reader?: ReadableStreamDefaultReader | null
    parent_id?: string | null
    message_id?: string
    /** 与后端 Langfuse metadata.langfuse_session_id 一致（chat_id） */
    langfuse_session_id?: string
  }>
>([])

function patchLastAssistantParts(mut: (parts: UiPart[]) => UiPart[]) {
  const lastAssistantIndex = conversationItems.value.findLastIndex((item) => item.role === 'assistant')
  patchAssistantPartsAt(lastAssistantIndex, mut)
}

function patchAssistantPartsAt(index: number, mut: (parts: UiPart[]) => UiPart[]) {
  const lastAssistantIndex = index
  if (lastAssistantIndex === -1) {
    return
  }
  const prev = conversationItems.value[lastAssistantIndex]
  const base = prev.messageContent?.version === 1 ? prev.messageContent : emptyMessageContent()
  const newParts = mut([...base.parts])
  const { content, reasoning } = syncLegacyFieldsFromParts(newParts)
  const updated = {
    ...prev,
    messageContent: { version: 1 as const, parts: newParts },
    content,
    reasoning,
  }
  conversationItems.value = [
    ...conversationItems.value.slice(0, lastAssistantIndex),
    updated,
    ...conversationItems.value.slice(lastAssistantIndex + 1),
  ]
}

// 强制依赖追踪 - 使用 watchEffect + ref
const conversationItemsSnapshot = ref([])

// 监听 conversationItems 变化并更新 snapshot
watchEffect(() => {
  const items = conversationItems.value
  conversationItemsSnapshot.value = items.slice()
})

// 添加 watch 验证 conversationItems 变化
watch(() => conversationItems.value.length, (newLen) => {
  // debug: length changed
})

// 这里控制内容加载状态
const contentLoadingStates = ref(
  conversationItemsSnapshot.value.map(() => false),
)

/** 解析正文流内 `<think>…</think>`，跨 chunk 缓冲标签片段 */
const redactedThinkingStreamCtx = createRedactedThinkingStreamCtx()
/** 本轮已收到后端 reasoning-* 时，不再对 text-delta 做标签拆分 */
const nativeReasoningSeen = ref(false)

// 改为对象存储不同问答类型的uuid
const uuids = ref<Record<string, string>>({})

const sessionContext = ref<import('@/api/chat').ContextSnapshot | null>(null)
const selectedKbCollections = ref<string[]>([])
const kbSearchEnabled = ref(true)
const selectedModelId = ref('')
const selectedMcpServers = ref<string[]>([])
const selectedSkills = ref<string[]>([])
const skillsAllEnabled = ref(true)
/** 当前内存 session_id 是否已物化（历史点入或发送 ensure 成功） */
const sessionMaterialized = ref(false)

function normalizeIdList(raw: unknown): string[] {
  if (!Array.isArray(raw)) {
    return []
  }
  const seen = new Set<string>()
  const out: string[] = []
  for (const item of raw) {
    const name = String(item ?? '').trim()
    if (!name || seen.has(name)) {
      continue
    }
    seen.add(name)
    out.push(name)
  }
  return out
}

async function onChatImageUploaded() {
  if (!usesSessionAttachmentUpload(qa_type.value)) {
    return
  }
  const sessionId = uuids.value[qa_type.value] ?? ''
  if (!sessionId) {
    return
  }
  await ensureVisionModelForImageUpload({
    sessionId,
    selectedModelId,
    persistSessionExtra: sessionMaterialized.value,
  })
}

function buildComposingSessionExtra(): Record<string, unknown> {
  const extra: Record<string, unknown> = {
    qa_type: qa_type.value,
  }
  if (qa_type.value === 'COMMON_QA' || qa_type.value === 'SUPER_AGENT_QA') {
    extra.kb_collections = selectedKbCollections.value
    extra.kb_search_enabled = kbSearchEnabled.value
  }
  if (qa_type.value !== 'TEST_CASE_QA' && selectedModelId.value) {
    extra.model_id = selectedModelId.value
  }
  if (qa_type.value !== 'TEST_CASE_QA') {
    extra.mcp_servers = selectedMcpServers.value
  }
  if (qa_type.value === 'SUPER_AGENT_QA' && !skillsAllEnabled.value) {
    extra.enabled_skills = selectedSkills.value
  }
  return extra
}

function normalizeKbCollections(raw: unknown): string[] {
  return normalizeIdList(raw)
}

const showContextIndicator = computed(
  () => qa_type.value !== 'TEST_CASE_QA' && hasValidContextWindow(sessionContext.value),
)

async function loadSessionContext(sessionId: string) {
  if (!sessionId || qa_type.value === 'TEST_CASE_QA') {
    sessionContext.value = null
    return
  }
  try {
    const session = await getSession(sessionId)
    const raw = session.extra?.context
    sessionContext.value = hasValidContextWindow(raw) ? raw : null
    selectedKbCollections.value = normalizeKbCollections(session.extra?.kb_collections)
    kbSearchEnabled.value = session.extra?.kb_search_enabled !== false
    const storedModelId = String(session.extra?.model_id ?? '').trim()
    if (storedModelId) {
      selectedModelId.value = storedModelId
    }
    if (Object.prototype.hasOwnProperty.call(session.extra ?? {}, 'mcp_servers')) {
      selectedMcpServers.value = normalizeIdList(session.extra?.mcp_servers)
    } else {
      selectedMcpServers.value = []
    }
    if (Object.prototype.hasOwnProperty.call(session.extra ?? {}, 'enabled_skills')) {
      selectedSkills.value = normalizeIdList(session.extra?.enabled_skills)
      skillsAllEnabled.value = false
    } else {
      selectedSkills.value = []
      skillsAllEnabled.value = true
    }
    sessionMaterialized.value = true
  } catch {
    sessionContext.value = null
    selectedKbCollections.value = []
    kbSearchEnabled.value = true
    selectedModelId.value = ''
    selectedMcpServers.value = []
    selectedSkills.value = []
    skillsAllEnabled.value = true
    sessionMaterialized.value = false
  }
}

let lastRunStatusNotice = ''
const reconnectAvailable = ref(false)

// SSE：依赖 conversationItems / uuids / qa_type，须放在其后
const sseStream = useSSEStream({
  onRunStatus: (status, message) => {
    if (status === lastRunStatusNotice) {
      return
    }
    lastRunStatusNotice = status
    if (status === 'retrying') {
      window.$ModalMessage.info(message || '连接中断，正在重试')
    } else if (status === 'interrupted') {
      window.$ModalMessage.warning(message || '服务中断，本轮生成未完成')
    } else if (status === 'disconnected') {
      reconnectAvailable.value = true
    } else if (status === 'running') {
      reconnectAvailable.value = false
      lastRunStatusNotice = ''
      pendingHitl.value = null
    }
  },
  onSnapshot: (snapshot) => {
    reconnectAvailable.value = false
    stylizingLoading.value = shouldShowRunContinuation(snapshot.status)
    if (snapshot.status !== 'hitl_pending') {
      pendingHitlBySession.value = setPendingHitlForSession(
        pendingHitlBySession.value,
        snapshot.session_id,
        null,
      )
    }
    const normalized = normalizeApiContent(snapshot.content)
    let lastIdx = conversationItems.value.findIndex(
      (item) => item.role === 'assistant' && item.message_id === snapshot.assistant_message_id,
    )
    if (lastIdx < 0) {
      lastIdx = conversationItems.value.findLastIndex(
        (item) => item.role === 'assistant'
          && item.chat_id === snapshot.session_id
          && !item.message_id,
      )
    }
    patchAssistantPartsAt(lastIdx, () => normalized.parts)
    if (lastIdx >= 0) {
      conversationItems.value[lastIdx] = {
        ...conversationItems.value[lastIdx],
        message_id: snapshot.assistant_message_id,
      }
    }
  },
  onMessageStart: (data) => {
    nativeReasoningSeen.value = false
    Object.assign(redactedThinkingStreamCtx, createRedactedThinkingStreamCtx())
    const aid = String(data.assistant_message_id ?? '')
    const lfRaw = data.langfuse_session_id
    const lf = typeof lfRaw === 'string' && lfRaw.trim() ? lfRaw.trim() : ''
    const lastIdx = conversationItems.value.findLastIndex((item) => item.role === 'assistant')
    if (lastIdx === -1) {
      return
    }
    const cur = conversationItems.value[lastIdx]
    conversationItems.value[lastIdx] = {
      ...cur,
      ...(aid ? { message_id: aid } : {}),
      ...(lf ? { langfuse_session_id: lf } : {}),
    }
  },
  onTextDelta: (text, parent_task_call_id) =>
    patchLastAssistantParts((parts) =>
      nativeReasoningSeen.value
        ? appendTextDelta(parts, text, parent_task_call_id)
        : appendTextDeltaWithRedactedThinking(parts, text, redactedThinkingStreamCtx, parent_task_call_id),
    ),
  onRetrievalResults: (part) => {
    patchLastAssistantParts((parts) => appendRetrievalPart(parts, part))
  },
  onReasoningStart: () => {
    nativeReasoningSeen.value = true
  },
  onReasoningDelta: (delta, parent_task_call_id) => {
    nativeReasoningSeen.value = true
    patchLastAssistantParts((parts) => appendReasoningDelta(parts, delta, parent_task_call_id))
  },
  onReasoningEnd: () => {
    patchLastAssistantParts((parts) => completeLastReasoningPart(parts))
  },
  onToolCall: (name, args, tool_call_id, parent_task_call_id) => {
    patchLastAssistantParts((parts) =>
      upsertToolInputPart(parts, tool_call_id, name, args, parent_task_call_id),
    )
    if (shouldApplyWriteTodos(name, args)) {
      const parsed = parseWriteTodosInput(args)
      if (parsed !== null) {
        businessStore.update_todos(parsed)
      }
    }
  },
  onToolResult: (tool_call_id, payload) => {
    patchLastAssistantParts((parts) => applyToolOutput(parts, tool_call_id, payload))
  },
  onCustomEvent: (eventType, data) => {
    if (eventType !== 'hitl-required') {
      return
    }
    const interrupt_id = String(data.interrupt_id ?? '')
    const kind = String(data.kind ?? 'approval')
    const action_requests = Array.isArray(data.action_requests)
      ? (data.action_requests as Array<{ tool_call_id?: string, name?: string, args?: Record<string, unknown> }>)
      : []
    const session_id = String(data.session_id ?? '')
    const run_id = String(data.run_id ?? '')
    if (!session_id || !run_id) {
      return
    }
    pendingHitl.value = {
      session_id,
      run_id,
      interrupt_id,
      kind,
      action_requests,
      review_configs: Array.isArray(data.review_configs) ? data.review_configs : [],
      expires_at: Number(data.expires_at ?? 0),
      submitting: false,
    }
    stylizingLoading.value = false
    patchLastAssistantParts((parts) =>
      applyHitlPendingParts(parts, { interrupt_id, kind, action_requests }),
    )
  },
  onFinish: (detail) => {
    stylizingLoading.value = false
    patchLastAssistantParts((parts) => flushRedactedThinkingStreamCtx(parts, redactedThinkingStreamCtx))
    const lastIdx = conversationItems.value.findLastIndex((item) => item.role === 'assistant')
    if (lastIdx !== -1) {
      const prev = conversationItems.value[lastIdx]
      if (prev.messageContent?.version === 1) {
        let parts = prev.messageContent.parts
        if (detail?.finish_reason === 'stopped') {
          parts = appendUserStopNotice(parts)
        } else if (detail?.finish_reason !== 'hitl_pending') {
          parts = markStreamingPartsComplete(parts)
          pendingHitl.value = null
        }
        const { content, reasoning } = syncLegacyFieldsFromParts(parts)
        conversationItems.value = [
          ...conversationItems.value.slice(0, lastIdx),
          { ...prev, messageContent: { version: 1, parts }, content, reasoning },
          ...conversationItems.value.slice(lastIdx + 1),
        ]
      }
    }
    const lastUserIdx = conversationItems.value.findLastIndex((item) => item.role === 'user')
    if (lastUserIdx !== -1) {
      contentLoadingStates.value[lastUserIdx] = false
    }
    onCompletedReader(conversationItems.value.length - 1)
    scrollToBottom()
    void loadSessionContext(getChatSessionId())
    reloadSessionFilesPanel()
  },
  onTitleUpdate: (title: string) => {
    const currentUuid = uuids.value[qa_type.value]
    if (currentUuid && tableData.value.length > 0) {
      const sessionIndex = tableData.value.findIndex((s) => s.chat_id === currentUuid)
      if (sessionIndex !== -1) {
        const row = tableData.value[sessionIndex]
        const currentKey = (row.key || '').trim()
        // 会话标题仅在首条消息时确定，后续轮次不再覆盖
        if (currentKey && currentKey !== '新对话') {
          return
        }
        tableData.value[sessionIndex].key = title
      } else {
        tableData.value.unshift({
          uuid: currentUuid,
          key: title,
          chat_id: currentUuid,
          qa_type: qa_type.value,
        })
      }
    }
  },
  onUsageUpdate: (usage) => {
    const lastIdx = conversationItems.value.findLastIndex((item) => item.role === 'assistant')
    if (lastIdx === -1) {
      return
    }
    const prev = conversationItems.value[lastIdx]
    conversationItems.value[lastIdx] = {
      ...prev,
      msg_metadata: { ...(prev.msg_metadata || {}), usage },
    }
  },
  onContextUpdate: (context) => {
    sessionContext.value = context
  },
  onError: (msg) => {
    stylizingLoading.value = false
    patchLastAssistantParts((parts) => flushRedactedThinkingStreamCtx(parts, redactedThinkingStreamCtx))
    const lastAssistantIdx = conversationItems.value.findLastIndex((item) => item.role === 'assistant')
    if (lastAssistantIdx !== -1) {
      const prev = conversationItems.value[lastAssistantIdx]
      if (prev.messageContent?.version === 1) {
        const parts = appendStreamFailureNotice(prev.messageContent.parts, msg)
        const { content, reasoning } = syncLegacyFieldsFromParts(parts)
        conversationItems.value = [
          ...conversationItems.value.slice(0, lastAssistantIdx),
          { ...prev, messageContent: { version: 1, parts }, content, reasoning },
          ...conversationItems.value.slice(lastAssistantIdx + 1),
        ]
      }
    }
    const lastUserIdx = conversationItems.value.findLastIndex((item) => item.role === 'user')
    if (lastUserIdx !== -1) {
      contentLoadingStates.value[lastUserIdx] = false
    }
    window.$ModalMessage.error(shortenChatErrorToast(msg || '请求失败'))
    onCompletedReader(conversationItems.value.length - 1)
    scrollToBottom()
  },
})

/** 顶层 ref 供模板自动解包；嵌在 sseStream 对象里的 isLoading 不会解包，会导致选择器一直 disabled */
const sseIsLoading = sseStream.isLoading
const hitlComposerDisabled = computed(() =>
  shouldDisableHitlComposer(pendingHitl.value, sseIsLoading.value),
)

async function submitHitlFromPanel(payload: {
  decisions: Array<{ type: string, message?: string }>
  grant_scope?: 'once' | 'session' | null
}) {
  const pending = pendingHitl.value
  if (!pending || pending.submitting) {
    return
  }
  pending.submitting = true
  const sessionId = pending.session_id
  try {
    await sseStream.resumeHitl(sessionId, {
      interrupt_id: pending.interrupt_id,
      decisions: payload.decisions,
      grant_scope: payload.grant_scope,
    })
  } finally {
    const current = pendingHitlBySession.value[sessionId]
    if (current?.interrupt_id === pending.interrupt_id) {
      pendingHitlBySession.value = {
        ...pendingHitlBySession.value,
        [sessionId]: { ...current, submitting: false },
      }
    }
  }
}

async function stopChatStream() {
  try {
    await sseStream.stopCurrentRun()
  } catch (err) {
    // 401 已由 authHttp 统一跳转登录；其余错误仍等待 SSE finish/stopped
    if (!isUnauthorizedError(err)) {
      window.$ModalMessage.warning('停止请求失败，请稍后重试或重新登录')
    }
  }
}

// 校验文件上传状态和业务处理逻辑
const getChatSessionId = () => {
  if (!uuids.value[qa_type.value]) {
    uuids.value[qa_type.value] = uuidv4()
  }
  return uuids.value[qa_type.value]
}

async function reconnectCurrentRun() {
  const sessionId = uuids.value[qa_type.value]
  if (!sessionId) {
    return
  }
  reconnectAvailable.value = false
  await sseStream.resumeActiveRun(sessionId)
}

const checkAllFilesUploaded = () => {
  const pendingFiles = fileUploadRef.value?.pendingUploadFileInfoList || []

  if (qa_type.value === 'FAULT_OPERATION_QA' && pendingFiles.length > 0) {
    window.$ModalMessage.warning('故障排查暂不支持文件上传')
    return false
  }

  if (qa_type.value === 'COMMON_QA') {
    return true
  }

  for (const file of pendingFiles) {
    if (file.status !== 'finished') {
      window.$ModalMessage.warning('存在未完成上传或解析失败的文件，请检查后重试')
      return false
    }
  }
  return true
}

const uploadingOnSend = ref(false)

const sendDisabled = computed(() => {
  if (uploadingOnSend.value) {
    return true
  }
  const pendingCount = pendingUploadFileInfoList.value?.length ?? 0
  if (qa_type.value === 'FAULT_OPERATION_QA' && pendingCount > 0) {
    return true
  }
  return !inputTextString.value.trim()
})

function clearComposerQueue() {
  pendingUploadFileInfoList.value = []
  businessStore.clear_file_list()
  fileUploadRef.value?.clearQueue?.()
}

async function resolveAttachmentsForSend(): Promise<{
  upload_file_key: ChatAttachmentItem[]
  file_dict: Record<string, string> | undefined
}> {
  const pendingCount = fileUploadRef.value?.pendingUploadFileInfoList?.length ?? 0
  if (!pendingCount) {
    return { upload_file_key: [], file_dict: undefined }
  }

  if (usesSessionAttachmentUpload(qa_type.value)) {
    uploadingOnSend.value = true
    try {
      // session 须已由发送编排 ensure；此处只串行 upload
      const uploaded = await fileUploadRef.value!.uploadAllPendingFiles!()
      return {
        upload_file_key: uploaded,
        file_dict: buildFileDict(uploaded),
      }
    } finally {
      uploadingOnSend.value = false
    }
  }

  if (!checkAllFilesUploaded()) {
    throw new Error('pending_upload')
  }
  const upload_file_key = [...businessStore.file_list]
  return {
    upload_file_key,
    file_dict: buildFileDict(upload_file_key),
  }
}

function usesSessionAttachmentUpload(mode: string): boolean {
  return mode === 'COMMON_QA' || mode === 'SUPER_AGENT_QA' || mode === 'DEEP_RESEARCH_QA'
}

/** FAULT 禁止附件：不得用 kb 即时上传；与 chat 延时队列共用组件但入口已关 */
const composerUploadMode = computed(() =>
  usesSessionAttachmentUpload(qa_type.value) || qa_type.value === 'FAULT_OPERATION_QA'
    ? 'chat'
    : 'kb',
)


// 提交对话
const handleCreateStylized = async (send_text = '', file_key = []) => {
  // 设置背景颜色
  backgroundColorVariable.value = cssVar(themeCssVar.bg)

  // 滚动到底部
  scrollToBottom()

  // 设置初始化数据标识为false
  isInit.value = false

  // 设置查看历史消息标识为false
  isView.value = false

  // 清空推荐列表
  suggested_array.value = []

  // 若正在加载，则点击后恢复初始状态
  if (stylizingLoading.value) {
    void stopChatStream()
    return
  }

  const textContent = inputTextString.value
    ? inputTextString.value
    : send_text

  if (!textContent.trim()) {
    if (refInputTextString.value && !send_text) {
      inputTextString.value = ''
      refInputTextString.value?.select()
    }
    return
  }

  // 发送才物化：merge COMPOSING overlay → session.extra
  const sessionIdForSend = getChatSessionId()
  try {
    await ensureSession(sessionIdForSend, { extra: buildComposingSessionExtra() })
    sessionMaterialized.value = true
    void replaceChatSessionUrl(sessionIdForSend)
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error)
    window.$ModalMessage.error(`创建会话失败: ${msg}`)
    return
  }

  let upload_file_key: ChatAttachmentItem[] = []
  let file_dict: Record<string, string> | undefined

  try {
    const attachmentResult = await resolveAttachmentsForSend()
    upload_file_key = attachmentResult.upload_file_key
    file_dict = attachmentResult.file_dict
  } catch (error) {
    if (error instanceof Error && error.message !== 'pending_upload') {
      window.$ModalMessage.error(`附件上传失败: ${error.message}`)
    }
    return
  }

  if (file_key.length > 0) {
    upload_file_key = file_key as ChatAttachmentItem[]
    file_dict = buildFileDict(upload_file_key)
  }

  if (showDefaultPage.value) {
    // 新建对话 时输入新问题 清空历史数据
    conversationItems.value = []
    showDefaultPage.value = false
  }

  // 自定义id
  const uuid_str = uuidv4()
  // 加入对话历史用于左边表格渲染
  const newItem = {
    uuid: uuid_str,
    key: inputTextString.value ? inputTextString.value : send_text,
    chat_id: uuids.value[qa_type.value],
    qa_type: qa_type.value,
  }

  // 如果有相同的chat_id 则不添加 使用 unshift 方法将新元素添加到数组的最前面
  const hasSameChatId = tableData.value.some((item) => item.chat_id === uuids.value[qa_type.value])
  if (!hasSameChatId) {
    tableData.value.unshift(newItem)
  }

  // 新一轮用户提问：清空上一轮的 Todo 面板（流结束后保留，便于对照报告）
  businessStore.todos = []

  // 调用大模型后台服务接口
  stylizingLoading.value = true
  inputTextString.value = ''

  if (!uuids.value[qa_type.value]) {
    uuids.value[qa_type.value] = uuidv4()
  }

  // 存储该轮用户对话消息
  if (textContent) {
    conversationItems.value.push({
      uuid: uuid_str,
      chat_id: uuids.value[qa_type.value],
      qa_type: qa_type.value,
      question: textContent,
      content: '',
      file_key: upload_file_key,
      mentions: [...composerMentions.value],
      role: 'user',
    })
    // 更新 currentRenderIndex 以包含新添加的项
    currentRenderIndex.value = conversationItems.value.length - 1

    // 清空文件上传列表
    pendingUploadFileInfoList.value = []
    businessStore.clear_file_list()
  }

  // 存储该轮AI回复的消息（初始为空）
  nativeReasoningSeen.value = false
  Object.assign(redactedThinkingStreamCtx, createRedactedThinkingStreamCtx())
  conversationItems.value.push({
    uuid: uuid_str,
    chat_id: uuids.value[qa_type.value],
    qa_type: qa_type.value,
    question: textContent,
    content: '',
    file_key: [],
    role: 'assistant',
    messageContent: emptyMessageContent(),
  })

  // 更新 currentRenderIndex 以包含新添加的项
  currentRenderIndex.value = conversationItems.value.length - 1

  // 使用 useSSEStream composable
  const streamExtra: Record<string, unknown> = {
    qa_type: qa_type.value,
    file_dict,
  }
  if (qa_type.value === 'COMMON_QA' || qa_type.value === 'SUPER_AGENT_QA') {
    streamExtra.kb_collections = selectedKbCollections.value
    streamExtra.kb_search_enabled = kbSearchEnabled.value
  }
  if (qa_type.value !== 'TEST_CASE_QA' && selectedModelId.value) {
    streamExtra.model_id = selectedModelId.value
  }
  if (qa_type.value !== 'TEST_CASE_QA' && selectedMcpServers.value.length > 0) {
    streamExtra.mcp_servers = selectedMcpServers.value
  }
  if (qa_type.value === 'SUPER_AGENT_QA' && !skillsAllEnabled.value) {
    streamExtra.enabled_skills = selectedSkills.value
  }
  if (composerMentions.value.length > 0) {
    streamExtra.mentions = composerMentions.value.map(mentionToPayload)
  }
  await sseStream.sendMessage(
    uuids.value[qa_type.value],
    textContent,
    streamExtra,
  )

  composerMentions.value = []
  closeMentionPicker()

  // 滚动到底部
  scrollToBottom()
}

// 滚动到底部（流式对话中自动跟随；查看历史时默认不滚动，避免干扰阅读）
const scrollToBottom = () => {
  if (isView.value === false) {
    void scrollToLatestMessage()
  }
}

/** 强制滚到最新消息（切换历史会话等场景） */
async function scrollToLatestMessage(smooth = false) {
  await nextTick()
  await nextTick()
  requestAnimationFrame(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTo({
        top: messagesContainer.value.scrollHeight,
        behavior: smooth ? 'smooth' : 'auto',
      })
      window.setTimeout(() => {
        showScrollToBottom.value = false
      }, 350)
    }
  })
}

const placeholder = computed(() => {
  return composerPlaceholder(qa_type.value, uploadingOnSend.value)
})

function getComposerTextarea(): HTMLTextAreaElement | null {
  const inst = refInputTextString.value as InputInst & { textareaElRef?: HTMLTextAreaElement } | null
  if (inst?.textareaElRef) {
    return inst.textareaElRef
  }
  const root = (inst as unknown as { $el?: HTMLElement })?.$el
  return root?.querySelector?.('textarea') ?? null
}

function closeMentionPicker() {
  mentionPickerOpen.value = false
  mentionPickerQuery.value = ''
  mentionTriggerIndex.value = -1
  mentionTriggerChar.value = ''
}

async function syncMentionPickerFromInput() {
  const ta = getComposerTextarea()
  const text = inputTextString.value
  const pos = ta?.selectionStart ?? text.length
  const before = text.slice(0, pos)
  // / 仅行首；@ 允许空白边界（行首或空格/制表后）
  const slashMatch = before.match(/(^|\n)\/(\S*)$/)
  const atMatch = before.match(/(^|\s)@(\S*)$/)
  const match = slashMatch
    ? { trigger: '/' as const, query: slashMatch[2] || '', prefix: slashMatch[1] }
    : atMatch
      ? { trigger: '@' as const, query: atMatch[2] || '', prefix: atMatch[1] }
      : null
  if (!match) {
    closeMentionPicker()
    return
  }
  const { trigger, query } = match
  if (trigger === '/' && !supportsSlashSkills(qa_type.value)) {
    closeMentionPicker()
    return
  }
  if (trigger === '@' && !supportsAtMentions(qa_type.value)) {
    closeMentionPicker()
    return
  }
  const triggerAt = before.length - 1 - query.length
  const needLoad = !mentionPickerOpen.value
    || mentionTriggerChar.value !== trigger
    || mentionTriggerIndex.value !== triggerAt
  mentionTriggerChar.value = trigger
  mentionTriggerIndex.value = triggerAt
  mentionPickerQuery.value = query
  mentionPickerOpen.value = true
  if (needLoad) {
    mentionPickerLoading.value = true
    try {
      mentionPickerCandidates.value = await ensureMentionCatalog({
        qaType: qa_type.value,
        sessionId: uuids.value[qa_type.value] || '',
        mode: trigger === '/' ? 'slash' : 'at',
      })
    } finally {
      mentionPickerLoading.value = false
    }
  }
}

function onMentionSelect(item: MentionCandidate) {
  const mention = candidateToMention(item)
  const token = formatMentionToken(mention)
  const existingKey = `${mention.type}:${mention.id || mention.path}`
  if (!composerMentions.value.some((m) => `${m.type}:${m.id || m.path}` === existingKey)) {
    composerMentions.value = [...composerMentions.value, mention]
  }
  const ta = getComposerTextarea()
  const text = inputTextString.value
  const pos = ta?.selectionStart ?? text.length
  const start = mentionTriggerIndex.value
  if (start >= 0) {
    const insert = `${token} `
    inputTextString.value = `${text.slice(0, start)}${insert}${text.slice(pos)}`
    const caret = start + insert.length
    nextTick(() => {
      ta?.focus()
      ta?.setSelectionRange(caret, caret)
    })
  } else {
    nextTick(() => ta?.focus())
  }
  closeMentionPicker()
}

function onComposerKeydown(e: KeyboardEvent) {
  if (mentionPickerOpen.value && mentionPickerRef.value) {
    const key = e.key
    if (key === 'ArrowDown' || key === 'ArrowUp' || key === 'Escape' || key === 'Tab'
      || (key === 'Enter' && !e.shiftKey)) {
      mentionPickerRef.value.onKeydown(e)
      if (e.defaultPrevented) {
        return
      }
    }
  }
  if (e.key !== 'Enter' || e.shiftKey || e.isComposing) {
    return
  }
  e.preventDefault()
  if (!stylizingLoading.value && sendDisabled.value) {
    return
  }
  void handleCreateStylized()
}

watch(inputTextString, () => {
  // 删掉输入框中的 token 时，同步丢掉结构化 mentions
  if (composerMentions.value.length) {
    const text = inputTextString.value
    composerMentions.value = composerMentions.value.filter((m) => text.includes(formatMentionToken(m)))
  }
  void syncMentionPickerFromInput()
})

const generateRandomSuffix = function () {
  return Math.floor(Math.random() * 10000) // 生成0到9999之间的随机整数
}

// 重置状态
const handleResetState = () => {
  inputTextString.value = ''
  composerMentions.value = []
  closeMentionPicker()
  clearComposerQueue()

  stylizingLoading.value = false
  nextTick(() => {
    refInputTextString.value?.select()
  })
}
handleResetState()


// 下面方法用于左侧对话列表点击 右侧内容滚动
// 用于存储每个 MarkdownPreview 容器的引用
// const markdownPreviews = ref<Array<HTMLElement | null>>([]) // 初始化为空数组
const markdownPreviews = ref<Map<string, HTMLElement | null>>(new Map())


// 会话列表右键菜单
const sessionContextMenuShow = ref(false)
const sessionContextMenuX = ref(0)
const sessionContextMenuY = ref(0)
const sessionContextMenuTarget = ref<TableItem | null>(null)
/** 归档视图：仅展示已归档会话；此时不展示「新建对话」，右键不展示「置顶」 */
const archiveView = ref(false)

const sessionContextMenuOptions = computed(() => {
  const target = sessionContextMenuTarget.value
  const opts: Array<{ label: string, key: string }> = [
    { label: '修改标题', key: 'rename' },
  ]
  if (target && !archiveView.value) {
    opts.push({ label: target.pinned ? '取消置顶' : '置顶', key: target.pinned ? 'unpin' : 'pin' })
  }
  if (target) {
    opts.push({ label: target.archived ? '取消归档' : '归档', key: target.archived ? 'unarchive' : 'archive' })
  }
  return opts
})

function closeSessionContextMenu() {
  sessionContextMenuShow.value = false
  sessionContextMenuTarget.value = null
}

async function refreshSessionList() {
  await fetchConversationHistory(
    isInit,
    conversationItems,
    tableData,
    currentRenderIndex,
    null,
    searchText.value,
    archiveView.value ? 'only' : 'exclude',
  )
}

function sortTableDataPinnedFirst(items: TableItem[]): TableItem[] {
  return [...items].sort((a, b) => {
    const pa = a.pinned ? 1 : 0
    const pb = b.pinned ? 1 : 0
    if (pa !== pb) {
      return pb - pa
    }
    return 0
  })
}

async function toggleSessionMeta(row: TableItem, patch: { pinned?: boolean, archived?: boolean }) {
  try {
    await updateSessionMeta(row.chat_id, patch)
    // 本地即时更新 + 重排
    const idx = tableData.value.findIndex((s) => s.chat_id === row.chat_id)
    if (idx !== -1) {
      const updated = { ...tableData.value[idx], ...patch }
      const next = [...tableData.value]
      next[idx] = updated
      tableData.value = sortTableDataPinnedFirst(next)
    }
    // 归档 / 取消归档会改变列表成员，直接重拉
    if (patch.archived !== undefined) {
      await refreshSessionList()
    }
    window.$ModalMessage.destroyAll()
    window.$ModalMessage.success('已更新', { duration: 1200 })
  } catch (error) {
    const msg = error instanceof Error ? error.message : '操作失败'
    window.$ModalMessage.error(msg)
  }
}

function handleSessionContextMenuSelect(key: string) {
  const target = sessionContextMenuTarget.value
  closeSessionContextMenu()
  if (!target) {
    return
  }
  if (key === 'rename') {
    openRenameSessionModal(target)
    return
  }
  if (key === 'pin') {
    void toggleSessionMeta(target, { pinned: true })
    return
  }
  if (key === 'unpin') {
    void toggleSessionMeta(target, { pinned: false })
    return
  }
  if (key === 'archive') {
    void toggleSessionMeta(target, { archived: true })
    return
  }
  if (key === 'unarchive') {
    void toggleSessionMeta(target, { archived: false })
  }
}

const renameSessionModal = reactive({
  show: false,
  loading: false,
  sessionId: '',
  title: '',
  originalTitle: '',
})

function openRenameSessionModal(row: TableItem) {
  renameSessionModal.sessionId = row.chat_id
  renameSessionModal.title = row.key || ''
  renameSessionModal.originalTitle = row.key || ''
  renameSessionModal.show = true
}

async function submitRenameSession() {
  const title = renameSessionModal.title.trim()
  if (!title) {
    window.$ModalMessage.warning('标题不能为空')
    return false
  }
  if (title === renameSessionModal.originalTitle) {
    renameSessionModal.show = false
    return true
  }
  renameSessionModal.loading = true
  try {
    await updateSessionTitle(renameSessionModal.sessionId, { title })
    const sessionIndex = tableData.value.findIndex((s) => s.chat_id === renameSessionModal.sessionId)
    if (sessionIndex !== -1) {
      tableData.value[sessionIndex].key = title
    }
    window.$ModalMessage.success('标题已更新')
    renameSessionModal.show = false
    return true
  } catch (error) {
    const msg = error instanceof Error ? error.message : '更新标题失败'
    window.$ModalMessage.error(msg)
    return false
  } finally {
    renameSessionModal.loading = false
  }
}

// 表格行点击事件
const currentIndex = ref<string | null>(null)
const rowProps = (row: TableItem) => {
  return {
    class: [
      'cursor-pointer select-none',
      currentIndex.value === row.uuid && 'selected-row',
    ].join(' '),
    onContextmenu: (e: MouseEvent) => {
      e.preventDefault()
      e.stopPropagation()
      sessionContextMenuTarget.value = row
      sessionContextMenuX.value = e.clientX
      sessionContextMenuY.value = e.clientY
      sessionContextMenuShow.value = true
    },
    onClick: async () => {
      sseStream.detachSubscription()
      backgroundColorVariable.value = cssVar(themeCssVar.bg)

      currentIndex.value = row.uuid
      suggested_array.value = []
      businessStore.todos = []
      clearComposerQueue()

      isInit.value = false
      isView.value = true

      // 先关闭默认页面（如果还没关闭）
      if (showDefaultPage.value) {
        showDefaultPage.value = false
      }

      // 先切换当前 session 身份，再异步加载历史；审批面板必须立即随 session 隔离，
      // 不能在网络请求期间继续显示上一会话的 pending HITL。
      activateChatMode(row.qa_type, row.chat_id, true)

      // 这里根据chat_id 过滤同一轮对话数据
      await fetchConversationHistory(
        isInit,
        conversationItems,
        tableData,
        currentRenderIndex,
        row,
        '',
      )

      await replaceChatSessionUrl(row.chat_id)
      await scrollToLatestMessage(true)
      if (isMobile.value) {
        historyDrawerOpen.value = false
      }
    },
  }
}

// 递归查找最底层的元素
const findDeepestElement = (element: HTMLElement): HTMLElement => {
  if (element.children.length === 0) {
    return element
  }
  return findDeepestElement(element.lastElementChild as HTMLElement)
}

// 设置 markdownPreviews 数组中的元素
const setMarkdownPreview = (uuid: string, role: string, el: any) => {
  if (role === 'user') {
    if (el && el instanceof HTMLElement) {
      // 查找最下面的元素
      const deepestElement = findDeepestElement(el)
      markdownPreviews.value.set(uuid, deepestElement)
    }
  }
}

// 滚动到指定位置的方法
const scrollToItem = async (uuid: string) => {
  // 等待 DOM 更新完成
  await nextTick()
  await nextTick()

  const element = markdownPreviews.value.get(uuid)

  if (element && element instanceof HTMLElement) {
    try {
      // 强制重排，确保元素位置和尺寸正确
      void element.offsetWidth
      element.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
        inline: 'nearest',
      })
    } catch (error) {
      console.error('滚动到指定元素时出错:', error)
    }
  }
}

// 默认选中的对话类型
const qa_type = ref('COMMON_QA')

function ensureActiveSessionId() {
  const qt = qa_type.value
  if (!uuids.value[qt]) {
    uuids.value[qt] = uuidv4()
  }
}

watch(qa_type, ensureActiveSessionId, { immediate: true })
/** 从历史会话进入时只同步模式与会话标识，不清空已加载的消息。 */
const activateChatMode = (
  targetQaType: string,
  sessionId: string,
  fromHistorySelection = false,
) => {
  businessStore.todos = []

  // 切换到不同问答类型时，清空聊天记录（顶栏切换）；历史会话点入时跳过，否则会覆盖刚加载的 messages
  if (qa_type.value !== targetQaType) {
    suggested_array.value = []
    if (!fromHistorySelection) {
      conversationItems.value = []
      showDefaultPage.value = true
      currentIndex.value = null
      clearComposerQueue()
    }
  }

  qa_type.value = targetQaType
  businessStore.update_qa_type(targetQaType)

  // 切换类型时生成新uuid
  if (sessionId) {
    uuids.value[targetQaType] = sessionId
    sessionMaterialized.value = true
    void loadSessionContext(sessionId)
    reloadSessionFilesPanel()
  } else {
    uuids.value[targetQaType] = uuidv4()
    sessionMaterialized.value = false
    sessionContext.value = null
    selectedKbCollections.value = []
    kbSearchEnabled.value = true
    selectedModelId.value = ''
    selectedMcpServers.value = []
    selectedSkills.value = []
    skillsAllEnabled.value = true
    if (!fromHistorySelection) {
      void navigateToComposingUrl(true)
    }
  }

  // 测试用例生成在独立页面（TestAssistant），不在对话页内完成
  if (targetQaType === 'TEST_CASE_QA' && route.name !== 'TestCaseGenerate') {
    router.push({ name: 'TestCaseGenerate' })
  }
}

const WELCOME_QA_TYPES = ['COMMON_QA', 'SUPER_AGENT_QA', 'FAULT_OPERATION_QA'] as const

/** 从 URL 同步问答类型（不触发清空逻辑，避免首屏与历史加载打架） */
function applyWelcomeRouteQaType() {
  const q = route.query.qa_type
  if (typeof q !== 'string' || !(WELCOME_QA_TYPES as readonly string[]).includes(q)) {
    return
  }
  if (qa_type.value !== q) {
    qa_type.value = q
    businessStore.update_qa_type(q)
    if (!uuids.value[q]) {
      uuids.value[q] = uuidv4()
    }
  }
}

// 获取建议问题
const suggested_array = ref([])
// const query_dify_suggested = async () => {
//   if (!isInit.value) {
//     const res = await GlobalAPI.dify_suggested(uuids.value[qa_type.value])
//     const json = await res.json()
//     if (json?.data?.data !== undefined) {
//       suggested_array.value = json.data.data
//     }
//   }

//   // 滚动到底部
//   scrollToBottom()
// }
// 建议问题点击事件
const onSuggested = (index: number) => {
  handleCreateStylized(suggested_array.value[index])
}

// 侧边表格滚动条数 动态显示隐藏设置
const scrollableContainer = useTemplateRef('scrollableContainer')

const showScrollbar = () => {
  if (
    scrollableContainer.value
    && scrollableContainer.value.$el
    && scrollableContainer.value.$el.firstElementChild
  ) {
    scrollableContainer.value.$el.firstElementChild.style.overflowY = 'auto'
  }
}

const hideScrollbar = () => {
  if (
    scrollableContainer.value
    && scrollableContainer.value.$el
    && scrollableContainer.value.$el.firstElementChild
  ) {
    scrollableContainer.value.$el.firstElementChild.style.overflowY
            = 'hidden'
  }
}

const searchText = ref('')
const isFocusSearchChat = ref(false)
const onFocusSearchChat = () => {
  if (!showDefaultPage.value) {
    newChat()
  }
  isFocusSearchChat.value = true
  nextTick(() => {
    chatHistoryPanelRef.value?.focusSearch()
  })
}
const onBlurSearchChat = () => {
  if (searchText.value) {
    return
  }
  isFocusSearchChat.value = false
}

// 在script部分添加搜索处理函数
const handleSearch = () => {
  tableData.value = []
  fetchConversationHistory(
    isInit,
    conversationItems,
    tableData,
    currentRenderIndex,
    null,
    searchText.value,
    archiveView.value ? 'only' : 'exclude',
  )
}

const handleClear = () => {
  if (!showDefaultPage.value) {
    newChat()
  }
}

const collapsed = useLocalStorage(
  'collapsed-chat-menu',
  ref(false),
)

const { size: historySiderWidth, startResize: startHistorySiderResize } = usePaneResize({
  storageKey: 'noesis.chat.historySiderWidth',
  defaultSize: 260,
  min: 200,
  max: 420,
})

const { size: sessionPanelWidth, startResize: startSessionPanelResize } = usePaneResize({
  storageKey: 'noesis.chat.sessionPanelWidth',
  defaultSize: 420,
  min: 280,
  max: 720,
  invertDelta: true,
})

const { isMobile } = useBreakpoint()
const { drawerWidth: historyDrawerWidth } = useResponsiveDrawerWidth({ max: 560, mobileRatio: 0.8 })
const { drawerWidth: sessionDrawerWidth } = useResponsiveDrawerWidth({ max: 480, mobileRatio: 0.9 })
const historyDrawerOpen = ref(false)
const chatHistoryPanelRef = ref<InstanceType<typeof ChatHistoryPanel> | null>(null)

// 切换归档视图时重拉列表
watch(archiveView, () => {
  void refreshSessionList()
})

watch(isMobile, (mobile) => {
  if (mobile) {
    collapsed.value = true
    historyDrawerOpen.value = false
  }
})

watch(historyDrawerOpen, (open) => {
  if (open && isMobile.value) {
    sessionFilesPanelOpen.value = false
  }
})

watch(sessionFilesPanelOpen, (open) => {
  if (open && isMobile.value) {
    historyDrawerOpen.value = false
  }
})

function openHistoryDrawer() {
  historyDrawerOpen.value = true
}

function closeHistoryDrawer() {
  historyDrawerOpen.value = false
}

// 背景颜色 默认页面和内容页面动态调整
const backgroundColorVariable = ref(cssVar(themeCssVar.bgElevated))


// 添加一键滚动到底部功能的相关代码
const showScrollToBottom = ref(false)
const scrollThreshold = 1000 // 滚动超过100px时显示按钮

// 用户点击图标滚动到底部
const clickScrollToBottom = () => {
  void scrollToLatestMessage()
}

// ======新增：检查是否需要显示滚动到底部按钮==========//
const checkScrollPosition = () => {
  if (messagesContainer.value) {
    const { scrollTop, scrollHeight, clientHeight } = messagesContainer.value
    const isAtBottom = scrollTop + clientHeight >= scrollHeight - 10 // 10px的容差
    showScrollToBottom.value = !isAtBottom && scrollTop > scrollThreshold
  }
}
// 新增：监听滚动事件
const handleScroll = () => {
  checkScrollPosition()
}

// 在 onMounted 或 onBeforeMount 中添加事件监听
onMounted(() => {
  if (messagesContainer.value) {
    messagesContainer.value.addEventListener('scroll', handleScroll)
  }
})

// 在组件卸载前移除事件监听
onBeforeUnmount(() => {
  if (messagesContainer.value) {
    messagesContainer.value.removeEventListener('scroll', handleScroll)
  }
})

// ============================== 文件上传 ============================//
const composerDragOver = ref(false)

function isFileDragEvent(e: DragEvent): boolean {
  return !!e.dataTransfer?.types.includes('Files')
}

/** 阻止浏览器对文件拖放的默认行为（打开新标签 / 直接下载） */
function onPageDragOver(e: DragEvent) {
  if (!isFileDragEvent(e)) {
    return
  }
  e.preventDefault()
  if (e.dataTransfer) {
    e.dataTransfer.dropEffect = 'copy'
  }
}

function onPageDrop(e: DragEvent) {
  if (!isFileDragEvent(e)) {
    return
  }
  e.preventDefault()
}

function canUploadComposerFiles(): boolean {
  if (qa_type.value === 'FAULT_OPERATION_QA') {
    window.$ModalMessage.warning('故障排查暂不支持文件上传')
    return false
  }
  return true
}

function onComposerDragEnter(e: DragEvent) {
  if (!e.dataTransfer?.types.includes('Files')) {
    return
  }
  e.preventDefault()
  composerDragOver.value = true
}

function onComposerDragOver(e: DragEvent) {
  if (!e.dataTransfer?.types.includes('Files')) {
    return
  }
  e.preventDefault()
  e.dataTransfer.dropEffect = 'copy'
  composerDragOver.value = true
}

function onComposerDragLeave(e: DragEvent) {
  const related = e.relatedTarget as Node | null
  const current = e.currentTarget as HTMLElement
  if (related && current.contains(related)) {
    return
  }
  composerDragOver.value = false
}

function onComposerDrop(e: DragEvent) {
  e.preventDefault()
  e.stopPropagation()
  composerDragOver.value = false
  if (!canUploadComposerFiles()) {
    return
  }
  const files = e.dataTransfer?.files
  if (!files?.length) {
    return
  }
  fileUploadRef.value?.enqueueFiles?.(files)
}

function onComposerPaste(e: ClipboardEvent) {
  const items = e.clipboardData?.items
  if (!items?.length) {
    return
  }

  const imageFiles: File[] = []
  for (const item of items) {
    if (item.kind === 'file' && item.type.startsWith('image/')) {
      const file = item.getAsFile()
      if (file) {
        imageFiles.push(file)
      }
    }
  }
  if (!imageFiles.length) {
    return
  }

  e.preventDefault()
  if (!canUploadComposerFiles()) {
    return
  }
  fileUploadRef.value?.enqueueFiles?.(imageFiles)
}
</script>

<template>
  <div
    class="chat-page flex justify-between items-center h-full"
    :class="{ 'chat-page--mobile': isMobile }"
    @dragover="onPageDragOver"
    @drop="onPageDrop"
  >
    <n-layout
      ref="scrollableContainer"
      class="custom-layout h-full"
      has-sider
      :native-scrollbar="true"
      @mouseenter="showScrollbar"
      @mouseleave="hideScrollbar"
    >
      <n-layout-sider
        v-if="!isMobile"
        v-model:collapsed="collapsed"
        class="chat-history-sider"
        collapse-mode="width"
        :collapsed-width="0"
        :width="historySiderWidth"
        :show-collapsed-content="false"
        show-trigger="arrow-circle"
        bordered
      >
        <ChatHistoryPanel
          ref="chatHistoryPanelRef"
          v-model:search-text="searchText"
          v-model:archive-view="archiveView"
          :stylizing-loading="stylizingLoading"
          :is-focus-search-chat="isFocusSearchChat"
          :is-loading-history="isLoadingHistory"
          :table-data="tableData"
          :history-sidebar-columns="historySidebarColumns"
          :session-context-menu-show="sessionContextMenuShow"
          :session-context-menu-x="sessionContextMenuX"
          :session-context-menu-y="sessionContextMenuY"
          :session-context-menu-options="sessionContextMenuOptions"
          :row-props="rowProps"
          :current-qa-type="qa_type"
          @newChat="newChat"
          @focusSearch="onFocusSearchChat"
          @blurSearch="onBlurSearchChat"
          @search="handleSearch"
          @clear="handleClear"
          @openModal="openModal"
          @contextMenuSelect="handleSessionContextMenuSelect"
          @contextMenuClose="closeSessionContextMenu"
        />
        <ResizeDivider
          v-if="!collapsed"
          @resize-start="startHistorySiderResize"
        />
      </n-layout-sider>
      <n-layout-content class="content" :style="{ backgroundColor: backgroundColorVariable }">
        <div class="chat-main-layout h-full flex min-w-0">
          <div class="chat-main-inner flex-1 min-w-0 min-h-0 flex flex-col">
            <!-- 内容区域 -->
            <div
              flex="~ 1 col"
              min-w-0
              h-full
            >
              <div flex="~ justify-between items-center" class="chat-top-bar">
                <button
                  v-if="isMobile"
                  type="button"
                  class="history-drawer-toggle"
                  aria-label="打开对话历史"
                  @click="openHistoryDrawer"
                >
                  <span class="i-hugeicons:menu-02" aria-hidden="true"></span>
                </button>
                <NavigationNavBar
                  v-if="!isMobile"
                  class="flex-1 min-w-0"
                  :background-color="backgroundColorVariable"
                />
                <div class="chat-top-bar__mode">
                  <ChatModeSelector
                    :qa-type="qa_type"
                    :disabled="sseIsLoading"
                    @select="changeChatMode"
                  />
                </div>
                <button
                  v-if="!isMobile && !showDefaultPage && uuids[qa_type]"
                  type="button"
                  class="session-files-toggle"
                  :class="{ 'session-files-toggle--open': sessionFilesPanelOpen }"
                  :title="sessionFilesPanelOpen ? '收起文件区' : '展开文件区'"
                  :aria-label="sessionFilesPanelOpen ? '收起文件区' : '展开文件区'"
                  @click="toggleSessionFilesPanel"
                >
                  <span
                    class="session-files-toggle__icon"
                    :class="sessionFilesPanelOpen ? 'i-carbon:side-panel-close' : 'i-carbon:side-panel-open'"
                  ></span>
                </button>
              </div>

              <!-- 这里循环渲染即可实现多轮对话 -->
              <div
                ref="messagesContainer"
                flex="1 ~ col"
                min-h-0
                pb-20
                class="scrollable-container"
                :style="{ backgroundColor: backgroundColorVariable }"
                @scroll="handleScroll"
              >
                <!-- 默认对话页面 -->
                <transition name="fade">
                  <div v-if="showDefaultPage" class="default-page-slot">
                    <DefaultPage :qa-type="qa_type" />
                  </div>
                </transition>

                <template
                  v-if="!showDefaultPage"
                >
                  <div
                    v-for="(item, index) in conversationItemsSnapshot"
                    :key="`${item.uuid}-${index}`"
                    class="mb-4"
                  >
                    <div v-if="item.role === 'user'" class="chat-user-message-row flex flex-col items-end space-y-2 w-full">
                      <!-- 用户消息 -->
                      <div
                        class="chat-user-message"
                        :style="{
                          'margin-left': `10%`,
                          'margin-right': `10%`,
                          'padding': `15px`,
                          'border-radius': `5px`,
                          'text-align': `center`,
                          'max-width': '80%', // 控制宽度避免撑满
                        }"
                      >
                        <n-space>
                          <n-tag
                            class="chat-user-message__tag"
                            size="large"
                            :bordered="false"
                            :round="true"
                            :style="{
                              'fontSize': '16px',
                              'fontFamily': `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif, 'Apple Color Emoji', 'Segoe UI Emoji'`,
                              'fontWeight': '400',
                              'color': cssVar(themeCssVar.textNav),
                              'max-width': '600px',
                              'text-align': 'left',
                              'padding': '5px 18px',
                              'height': 'auto',
                              'line-height': 1.5,
                              'word-wrap': 'break-word',
                              'word-break': 'break-all',
                              'white-space': 'pre-wrap',
                              'overflow': 'visible',
                            }"
                            :color="{
                              color: naivePresetColors.primaryBorderSoft,
                              borderColor: naivePresetColors.primaryBorderSoft,
                            }"
                          >
                            <template #avatar>
                              <div class="size-25 text-primary i-my-svg:user-avatar"></div>
                            </template>
                            {{ item.question }}
                          </n-tag>
                        </n-space>
                      </div>

                      <!-- 用户消息复制按钮（hover 显隐） -->
                      <div
                        class="chat-user-message-actions"
                        style="margin-left: 10%; margin-right: 10%; width: 80%;"
                      >
                        <button
                          type="button"
                          class="chat-user-copy-btn"
                          title="复制"
                          aria-label="复制该消息"
                          @click="handleCopyUserText(item.question || '')"
                        >
                          <span class="i-hugeicons:copy-01" aria-hidden="true"></span>
                        </button>
                      </div>

                      <!-- 用户上传的文件列表 -->
                      <div
                        v-if="item.file_key && item.file_key.length > 0"
                        class="upload-wrapper-list flex flex-wrap gap-10 items-center pb-5"
                        style="margin-left: 10%; margin-right: 10.5%; width: 80%; justify-content: flex-end;"
                      >
                        <FileListItem
                          v-for="(file, fileIndex) in item.file_key"
                          :key="fileIndex"
                          :file="file"
                        />
                      </div>

                      <!-- 加载动画：紧跟在消息下方，但对齐到左边 -->
                      <div
                        v-if="contentLoadingStates[index]"
                        class="i-svg-spinners:bars-scale"
                        :style="{
                          'width': `24px`,
                          'height': `24px`,
                          'color': cssVar(themeCssVar.primaryTextSoft),
                          'border-left-color': cssVar(themeCssVar.primaryTextSoft),
                          'animation': `spin 1s linear infinite`,
                          'margin-top': '10px',
                          'align-self': 'flex-start', // 让此元素在交叉轴（水平轴）上靠左对齐
                          'margin-left': '12%', // 与上面的消息保持一致的缩进
                        }"
                      ></div>
                    </div>

                    <div v-if="item.role === 'assistant'">
                      <template v-if="item.messageContent?.version === 1">
                        <div class="assistant-unified-card">
                          <template
                            v-for="(entry, pi) in buildDisplayParts(item.messageContent.parts)"
                            :key="entry.kind === 'subagent'
                              ? (entry.part.tool_call_id ?? entry.part.id ?? pi)
                              : (entry.part.id ?? pi)"
                          >
                            <ReasoningBlock
                              v-if="entry.kind === 'part' && entry.part.type === 'reasoning' && (entry.part.content || entry.part.status === 'streaming')"
                              :reasoning="entry.part.content"
                              :defaultOpen="false"
                              :streaming="entry.part.status === 'streaming'"
                              appearance="light"
                            />
                            <SubagentCollapse
                              v-else-if="entry.kind === 'subagent'"
                              appearance="light"
                              :input="entry.part.input"
                              :output="entry.part.output"
                              :status="entry.part.status"
                              :state="entry.part.state"
                              :error="entry.part.error"
                              :duration_ms="entry.part.duration_ms"
                              :child-parts="entry.childParts"
                            />
                            <template v-else-if="entry.kind === 'part' && entry.part.type === 'tool'">
                              <ToolCallCollapse
                                appearance="light"
                                :name="entry.part.name"
                                :arguments="entry.part.input"
                                :result="entry.part.output"
                                :error="entry.part.error"
                                :status="entry.part.status"
                                :state="entry.part.state"
                                :error-category="entry.part.errorCategory"
                                :exit_code="entry.part.exit_code"
                                :truncated="entry.part.truncated"
                                :duration_ms="entry.part.duration_ms"
                              />
                            </template>
                            <MarkdownPreview
                              v-else-if="entry.kind === 'part' && entry.part.type === 'text'"
                              :content="entry.part.content || ''"
                              :retrieval-results="retrievedResults(item.messageContent.parts)"
                              :references-complete="entry.part.status !== 'streaming'"
                              :toolCalls="null"
                              :msgMetadata="item.msg_metadata"
                              :isInit="isInit"
                              :isView="isView"
                              :show-action-bar="false"
                              variant="segment"
                              :qa-type="item.qa_type || 'COMMON_QA'"
                              :parentScollBottomMethod="scrollToBottom"
                              @failed="() => onFailedReader(index)"
                              @recycleQa="() => onRecycleQa(index)"
                              @citation-click="(number) => openCitationSource(citationSourcesKey(item, index), number)"
                            />
                          </template>
                          <div
                            v-if="shouldShowAssistantToolFailureBlocker(item.messageContent.parts, showAssistantReplyLoading(index, item.role))"
                            class="assistant-tool-failure-blocker"
                            role="status"
                          >
                            <span class="assistant-tool-failure-blocker__icon" aria-hidden="true">!</span>
                            <span>本轮未完成</span>
                            <n-button
                              text
                              size="tiny"
                              @click="onRecycleQa(index)"
                            >
                              重新执行
                            </n-button>
                          </div>
                          <AssistantStreamingIndicator
                            v-if="showAssistantReplyLoading(index, item.role)"
                            section
                            :divided="buildDisplayParts(item.messageContent.parts).length > 0"
                            :label="buildDisplayParts(item.messageContent.parts).length > 0 ? '正在继续生成' : '正在生成'"
                          />
                          <AssistantReplyToolbar
                            v-if="item.messageContent.parts.length > 0 && !assistantPartsStillStreaming(item.messageContent.parts)"
                            :qa-type="item.qa_type || 'COMMON_QA'"
                            :copy-text="extractLastTopLevelText(item.messageContent.parts)"
                            :usage-text="hasValidUsage(item.msg_metadata?.usage) ? formatUsageSummary(item.msg_metadata!.usage!) : ''"
                            :attribution="item.msg_metadata?.usage?.attribution"
                            :langfuse_session_id="item.langfuse_session_id"
                            :langfuse-ui-origin="langfuseUiOrigin"
                            @recycle-qa="() => onRecycleQa(index)"
                          >
                            <template #meta>
                              <CitationSources
                                v-if="retrievedResults(item.messageContent.parts).length"
                                :ref="(component) => setCitationSourcesRef(citationSourcesKey(item, index), component)"
                                :content="extractLastTopLevelText(item.messageContent.parts)"
                                :results="retrievedResults(item.messageContent.parts)"
                              />
                            </template>
                          </AssistantReplyToolbar>
                        </div>
                      </template>
                      <template v-else>
                        <ReasoningBlock
                          v-if="item.reasoning"
                          :reasoning="item.reasoning"
                          :defaultOpen="false"
                          appearance="light"
                        />
                        <MarkdownPreview
                          :content="item.content || ''"
                          :toolCalls="item.tool_calls"
                          :msgMetadata="item.msg_metadata"
                          :isInit="isInit"
                          :isView="isView"
                          :qa-type="item.qa_type || 'COMMON_QA'"
                          :parentScollBottomMethod="scrollToBottom"
                          @failed="() => onFailedReader(index)"
                          @completed="() => onCompletedReader(index)"
                          @recycleQa="() => onRecycleQa(index)"
                          @beginRead="() => onBeginRead(index)"
                        />
                        <AssistantStreamingIndicator
                          v-if="showAssistantReplyLoading(index, item.role)"
                        />
                      </template>
                    </div>
                  </div>
                </template>

                <div
                  v-if="!isInit && !stylizingLoading"
                  class="w-70% ml-11% mt-[-20] bg-bgcolor"
                >
                  <SuggestedView
                    :labels="suggested_array"
                    @suggested="onSuggested"
                  />
                </div>
              </div>

              <div
                v-show="showScrollToBottom"
                class="scroll-to-bottom-btn"
                @click="clickScrollToBottom"
              >
                <div class="i-mingcute:arrow-down-fill"></div>
              </div>

              <div
                :style="{ backgroundColor: backgroundColorVariable }"
                class="items-center shrink-0 chat-input-footer-bar"
              >
                <div class="flex-1 w-full p-1em chat-input-footer">
                  <n-space
                    vertical
                    class="chat-content-gutter"
                  >
                    <n-alert
                      v-if="reconnectAvailable"
                      type="warning"
                      title="连接已中断"
                    >
                      已生成的内容不会丢失，可以重新连接继续查看。
                      <template #action>
                        <n-button
                          size="small"
                          @click="reconnectCurrentRun"
                        >
                          重新连接
                        </n-button>
                      </template>
                    </n-alert>
                    <!-- HITL 优先占 Todo 槽位；无 pending 时显示 Todo -->
                    <HitlComposerPanel
                      v-if="pendingHitl"
                      :kind="pendingHitl.kind"
                      :action-requests="pendingHitl.action_requests"
                      :disabled="hitlComposerDisabled"
                      @submit="submitHitlFromPanel"
                    />
                    <TodoList
                      v-else
                      :todos="businessStore.todos"
                    />
                    <div
                      :class="[
                        'chat-composer relative b b-solid p-12',
                        composerDragOver && 'chat-composer--dragover',
                      ]"
                      @dragenter="onComposerDragEnter"
                      @dragover="onComposerDragOver"
                      @dragleave="onComposerDragLeave"
                      @drop="onComposerDrop"
                    >
                      <div
                        v-if="composerDragOver"
                        class="chat-composer-drop-hint"
                      >
                        松开鼠标上传文件
                      </div>

                      <FileUploadManager
                        ref="fileUploadRef"
                        v-model="pendingUploadFileInfoList"
                        :upload-mode="composerUploadMode"
                        :get-session-id="getChatSessionId"
                        @chatImageUploaded="onChatImageUploaded"
                      />

                      <MentionPicker
                        ref="mentionPickerRef"
                        :open="mentionPickerOpen"
                        :query="mentionPickerQuery"
                        :candidates="mentionPickerCandidates"
                        :loading="mentionPickerLoading"
                        @select="onMentionSelect"
                        @close="closeMentionPicker"
                      />

                      <n-input
                        ref="refInputTextString"
                        v-model:value="inputTextString"
                        type="textarea"
                        class="textarea-resize-none w-full text-15 [&_.n-input\_\_border]:hidden [&_.n-input\_\_state-border]:hidden [&_.n-input-wrapper]:p-0!"
                        :style="{
                          '--n-border-radius': '15px',
                          'font-family': `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif, 'Apple Color Emoji', 'Segoe UI Emoji'`,
                          'font-size': '16px',
                          'line-height': '1.5',
                        }"
                        :placeholder="placeholder"
                        :autosize="{
                          minRows: 1,
                          maxRows: 10,
                        }"
                        @paste="onComposerPaste"
                        @keydown="onComposerKeydown"
                      />

                      <ChatComposerToolbar
                        v-model:model-id="selectedModelId"
                        v-model:kb-collections="selectedKbCollections"
                        v-model:kb-search-enabled="kbSearchEnabled"
                        v-model:mcp-servers="selectedMcpServers"
                        v-model:enabled-skills="selectedSkills"
                        v-model:skills-all-enabled="skillsAllEnabled"
                        :qa-type="qa_type"
                        :session-id="uuids[qa_type] ?? ''"
                        :persist-session-extra="sessionMaterialized"
                        :disabled="sseIsLoading"
                        :file-upload-ref="fileUploadRef"
                        :show-session-files="isMobile && !showDefaultPage && !!uuids[qa_type]"
                        @open-session-files="toggleSessionFilesPanel"
                      >
                        <template #right>
                          <ContextWindowIndicator
                            v-if="showContextIndicator"
                            class="shrink-0"
                            :context="sessionContext!"
                          />

                          <div class="chat-send-btn-wrap shrink-0">
                            <n-tooltip
                              :disabled="!stylizingLoading"
                              placement="top"
                            >
                              <template #trigger>
                                <n-float-button
                                  position="relative"
                                  :width="36"
                                  :height="36"
                                  :disabled="!stylizingLoading && sendDisabled"
                                  :type="stylizingLoading ? 'primary' : 'default'"
                                  color
                                  :class="[
                                    'chat-send-btn',
                                    stylizingLoading && 'chat-send-btn--stop',
                                  ]"
                                  @click.stop="handleCreateStylized()"
                                >
                                  <span
                                    v-if="stylizingLoading"
                                    class="chat-stop-icon"
                                    aria-label="停止生成"
                                  ></span>
                                  <div
                                    v-else
                                    class="flex items-center justify-center i-mingcute:send-fill text-20 cursor-pointer transition-colors duration-300 hover:c-primary/80"
                                  ></div>
                                </n-float-button>
                              </template>
                              停止生成
                            </n-tooltip>
                          </div>
                        </template>
                      </ChatComposerToolbar>
                    </div>
                  </n-space>
                </div>
              </div>
            </div>
          </div>
          <aside
            v-if="sessionFilesPanelOpen && !showDefaultPage && uuids[qa_type] && !isMobile"
            class="session-context-aside"
            :style="{
              backgroundColor: backgroundColorVariable,
              width: `${sessionPanelWidth}px`,
            }"
          >
            <ResizeDivider
              side="left"
              @resize-start="startSessionPanelResize"
            />
            <SessionContextPanel
              ref="sessionFilesPanelRef"
              :session-id="uuids[qa_type] || ''"
              :background-color="backgroundColorVariable"
            />
          </aside>
        </div>
      </n-layout-content>
    </n-layout>

    <!-- 移动端：对话历史抽屉 -->
    <n-drawer
      v-if="isMobile"
      v-model:show="historyDrawerOpen"
      placement="left"
      :width="historyDrawerWidth"
      :block-scroll="true"
    >
      <n-drawer-content
        title="对话历史"
        closable
        body-content-style="padding: 0; height: 100%;"
        @close="closeHistoryDrawer"
      >
        <ChatHistoryPanel
          ref="chatHistoryPanelRef"
          v-model:search-text="searchText"
          show-account-actions
          :stylizing-loading="stylizingLoading"
          :is-focus-search-chat="isFocusSearchChat"
          :is-loading-history="isLoadingHistory"
          :table-data="tableData"
          :history-sidebar-columns="historySidebarColumns"
          :session-context-menu-show="sessionContextMenuShow"
          :session-context-menu-x="sessionContextMenuX"
          :session-context-menu-y="sessionContextMenuY"
          :session-context-menu-options="sessionContextMenuOptions"
          :row-props="rowProps"
          :current-qa-type="qa_type"
          @newChat="newChat"
          @focusSearch="onFocusSearchChat"
          @blurSearch="onBlurSearchChat"
          @search="handleSearch"
          @clear="handleClear"
          @openModal="openModal"
          @contextMenuSelect="handleSessionContextMenuSelect"
          @contextMenuClose="closeSessionContextMenu"
        />
      </n-drawer-content>
    </n-drawer>

    <!-- 移动端：会话文件抽屉 -->
    <n-drawer
      v-if="isMobile"
      :show="sessionFilesPanelOpen && !showDefaultPage && !!uuids[qa_type]"
      placement="right"
      :width="sessionDrawerWidth"
      :block-scroll="true"
      @update:show="sessionFilesPanelOpen = $event"
    >
      <n-drawer-content
        title="会话文件"
        closable
        body-content-style="padding: 0; height: 100%;"
      >
        <SessionContextPanel
          ref="sessionFilesPanelRef"
          :session-id="uuids[qa_type] || ''"
          :background-color="backgroundColorVariable"
        />
      </n-drawer-content>
    </n-drawer>

    <n-modal
      v-model:show="renameSessionModal.show"
      preset="dialog"
      title="修改标题"
      positive-text="确定"
      negative-text="取消"
      :loading="renameSessionModal.loading"
      :mask-closable="false"
      @positive-click="submitRenameSession"
    >
      <n-input
        v-model:value="renameSessionModal.title"
        placeholder="请输入会话标题"
        :maxlength="255"
        clearable
        @keyup.enter="submitRenameSession"
      />
    </n-modal>
    <TableModal
      :show="isModalOpen"
      @update:show="handleModalClose"
    />
  </div>
</template>

<style lang="scss" scoped>
.assistant-tool-failure-blocker {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 7px 2px 3px;
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
  line-height: 1.4;
}

.assistant-tool-failure-blocker__icon {
  display: inline-flex;
  flex: 0 0 16px;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  color: var(--noesis-color-warning);
  font-size: 11px;
  font-weight: 700;
  border: 1px solid currentColor;
  border-radius: 50%;
}

.chat-composer--dragover {
  border-color: var(--noesis-color-primary);
  background: var(--noesis-color-primary-bg-subtle);
}

.chat-composer-drop-hint {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--noesis-radius-md);
  background: color-mix(in srgb, var(--noesis-color-bg-elevated) 92%, transparent);
  font-size: 14px;
  color: var(--noesis-color-primary);
  pointer-events: none;
}

.chat-composer-row {
  min-height: 36px;
}

.chat-send-btn-wrap {
  z-index: 1;
  display: flex;
  align-items: center;
}

.chat-send-btn-wrap :deep(.n-float-button) {
  position: relative !important;
  inset: auto !important;
}

.chat-send-btn--stop {
  box-shadow: 0 0 0 2px var(--noesis-color-primary-ring);
}

.chat-stop-icon {
  display: block;
  width: 12px;
  height: 12px;
  background-color: var(--noesis-color-bg-elevated);
  border-radius: 2px;
}

.chat-history-sider {
  position: relative;
}

/* 聊天记录侧栏折叠钮 — 使用 Naive 右缘定位，仅对齐主题色 */
.chat-history-sider :deep(.n-layout-toggle-button) {
  border-color: var(--noesis-color-border);
  background: var(--noesis-color-bg-elevated);
  box-shadow: var(--noesis-shadow-sm);
  color: var(--noesis-color-text-secondary);
}

.chat-history-sider :deep(.n-layout-toggle-button:hover) {
  color: var(--noesis-color-primary);
  border-color: var(--noesis-color-primary-muted);
  background: var(--noesis-color-primary-bg-subtle);
}

.scrollable-container {
  overflow-y: auto;
  height: 100%;
  padding-bottom: 20px;
  background-color: var(--noesis-color-bg);
}

/* 滚动条整体部分 */

::-webkit-scrollbar {
  width: 4px; /* 竖向滚动条宽度 */
  height: 4px; /* 横向滚动条高度 */
}

/* 滚动条的轨道 */

::-webkit-scrollbar-track {
  background: var(--noesis-scrollbar-track);
}

::-webkit-scrollbar-thumb {
  background: var(--noesis-scrollbar-thumb);
  border-radius: var(--noesis-radius-md);
}

::-webkit-scrollbar-thumb:hover {
  background: var(--noesis-scrollbar-thumb);
}

.default-page-slot {
  height: auto;
  flex-shrink: 0;
  align-self: flex-start;
  width: 100%;
}

.default-page {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100vh;
  background-color: var(--noesis-color-bg);
}

/* 新建对话框的淡入淡出动画样式 */

.fade-enter-active {
  transition: opacity 1s; /* 出现时较慢 */
}

.fade-leave-active {
  transition: opacity 0s; /* 隐藏时较快 */
}

.fade-enter, .fade-leave-to /* .fade-leave-active in <2.1.8 */ {
  opacity: 0;
}

@keyframes spin {

  0% {
    transform: rotate(0deg);
  }

  100% {
    transform: rotate(360deg);
  }
}

.custom-layout {
  border-top-left-radius: var(--noesis-chat-layout-radius);
  background-color: var(--noesis-color-bg-elevated);
}

.header,
.footer {
  background-color: var(--noesis-color-bg-elevated);
}

.content {
  border-right: 1px solid var(--noesis-color-bg);
}

.chat-main-layout {
  background-color: v-bind(backgroundColorVariable);
}

.session-context-aside {
  position: relative;
  flex-shrink: 0;
  min-height: 0;
  border-left: 1px solid var(--noesis-color-border-aside);
  overflow: hidden;
}

/* 文件区折叠钮 — 顶栏右上角，尺寸与左侧 Naive layout-toggle-button 对齐 */
.session-files-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  margin: 0;
  padding: 0;
  border: 1px solid var(--noesis-color-border);
  border-radius: 50%;
  background: var(--noesis-color-bg-elevated);
  box-shadow: var(--noesis-shadow-sm);
  color: var(--noesis-color-text-secondary);
  cursor: pointer;
  transition:
    color 0.15s ease,
    border-color 0.15s ease,
    background-color 0.15s ease;
}

.session-files-toggle:hover,
.session-files-toggle--open {
  color: var(--noesis-color-primary);
  border-color: var(--noesis-color-primary-muted);
  background: var(--noesis-color-primary-bg-subtle);
}

.session-files-toggle__icon {
  display: inline-block;
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}

.footer {
  border-bottom-left-radius: 10px;
}

.icon-button {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border-radius: var(--noesis-radius-round);
  border: 1px solid var(--noesis-color-border);
  background-color: var(--noesis-color-bg-elevated);
  cursor: pointer;
  transition: background-color 0.3s;
  position: relative;
}

.icon-button.selected {
  border: 1px solid var(--noesis-color-primary-muted);
}

.icon-button:hover {
  border: 1px solid var(--noesis-color-primary-muted);
}

.scroll-to-bottom-btn {
  position: absolute;
  bottom: 145px;
  left: 50%;
  transform: translateX(-50%);
  width: 30px;
  height: 30px;
  border-radius: var(--noesis-radius-round);
  background-color: var(--noesis-color-bg-elevated);
  box-shadow: var(--noesis-shadow-float);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  z-index: 100;
  transition: all 0.3s ease;
  border: 1px solid var(--noesis-color-border);
  backdrop-filter: blur(5px);
}

.scroll-to-bottom-btn:hover {
  background-color: var(--noesis-color-bg);
  transform: translateX(-50%) scale(1.1);
  box-shadow: 0 6px 20px rgb(0 0 0 / 25%);
}

.scroll-to-bottom-btn::before {
  content: "";
  position: absolute;
  width: 200%;
  height: 200%;
  top: -50%;
  left: -50%;
  border-radius: 50%;
  animation: pulse 2s infinite;
}

@keyframes pulse {

  0% {
    transform: scale(0.5);
    opacity: 0;
  }

  50% {
    transform: scale(1);
    opacity: 0.2;
  }

  100% {
    transform: scale(1.5);
    opacity: 0;
  }
}

.upload-wrapper-list {
  --at-apply: flex flex-wrap gap-10 items-center;
  --at-apply: pb-12;
}

.chat-input-footer {
  flex-shrink: 0;
}

.chat-user-message-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: -4px;
  margin-bottom: 4px;
}

.chat-user-copy-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: none;
  border-radius: var(--noesis-radius-md);
  background: transparent;
  color: var(--noesis-color-text-hint);
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s ease, color 0.15s ease, background-color 0.15s ease;
}

.chat-user-copy-btn span {
  display: inline-block;
  width: 16px;
  height: 16px;
  font-size: 16px;
  line-height: 1;
}

.chat-user-message-row:hover .chat-user-copy-btn,
.chat-user-copy-btn:focus-visible {
  opacity: 1;
}

.chat-user-copy-btn:hover {
  color: var(--noesis-color-primary);
  background: var(--noesis-color-primary-bg-subtle);
}

.assistant-unified-card {
  width: 80%;
  margin-left: 10%;
  margin-right: 10%;
  background: var(--noesis-color-bg-elevated);
  border: 1px solid var(--noesis-color-border-subtle);
  border-radius: 16px;
  overflow: visible;
  box-shadow: var(--noesis-shadow-sm);
}

.chat-top-bar {
  flex-shrink: 0;
  gap: 8px;
  padding-right: 12px;
  padding-left: 8px;
}

.chat-top-bar__mode {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
}

.history-drawer-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  margin: 0;
  padding: 0;
  border: 1px solid var(--noesis-color-border);
  border-radius: 50%;
  background: var(--noesis-color-bg-elevated);
  color: var(--noesis-color-text-secondary);
  cursor: pointer;
  transition:
    color 0.15s ease,
    border-color 0.15s ease,
    background-color 0.15s ease;
}

.history-drawer-toggle:hover {
  color: var(--noesis-color-primary);
  border-color: var(--noesis-color-primary-muted);
  background: var(--noesis-color-primary-bg-subtle);
}

.history-drawer-toggle span {
  display: inline-block;
  width: 16px;
  height: 16px;
}

.chat-page {
  height: 100%;
  min-height: 0;
}

.chat-page--mobile .custom-layout {
  border-radius: 0;
}

.chat-content-gutter {
  margin-left: var(--noesis-content-gutter-desktop);
  margin-right: var(--noesis-content-gutter-desktop);
}

@media (max-width: 1024px) {
  .chat-content-gutter {
    margin-left: var(--noesis-content-gutter-mobile);
    margin-right: var(--noesis-content-gutter-mobile);
  }

  .assistant-unified-card {
    width: 100%;
    margin-left: 0;
    margin-right: 0;
  }

  .custom-layout {
    border-top-left-radius: var(--noesis-shell-radius-mobile);
  }

  .scroll-to-bottom-btn {
    bottom: 120px;
  }

  .default-page-slot {
    min-height: 0;
  }

  .default-page {
    height: auto;
    min-height: 0;
  }
}

@media (max-width: 768px) {
  .chat-top-bar {
    min-height: 48px;
    padding: 7px 8px;
    border-bottom: 1px solid var(--noesis-color-border-subtle);
  }

  .chat-top-bar__mode {
    flex: 1;
    padding-right: 32px;
  }

  .chat-input-footer {
    padding: 8px !important;
  }

  .chat-content-gutter {
    margin-right: 0;
    margin-left: 0;
  }

  .chat-user-message {
    max-width: calc(100% - 8px) !important;
    margin: 0 !important;
    padding: 6px !important;
  }

  .chat-user-message__tag {
    max-width: 100% !important;
    padding: 4px 12px !important;
    font-size: 15px !important;
    line-height: 1.55 !important;
  }

}
</style>
