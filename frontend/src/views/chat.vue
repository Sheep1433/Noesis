<script lang="tsx" setup>
import type { InputInst, UploadFileInfo } from 'naive-ui'
import type { ChatAttachmentItem } from '@/store/business'
import type { MessageContentV1, UiPart } from '@/views/chat/messageParts'
import { ensureSession, getSession, stopChat, updateSessionTitle } from '@/api/chat'
import AssistantReplyToolbar from '@/components/AssistantReplyToolbar/index.vue'
import ChatComposerToolbar from '@/components/Chat/ChatComposerToolbar.vue'
import ContextWindowIndicator from '@/components/ContextWindowIndicator/index.vue'
import ReasoningBlock from '@/components/ReasoningBlock/index.vue'
import ResizeDivider from '@/components/ResizeDivider.vue'
import SubagentCollapse from '@/components/SubagentCollapse/index.vue'
import TodoList from '@/components/TodoList/index.vue'
import ToolCallCollapse from '@/components/ToolCallCollapse/index.vue'
import { langfuseUiOrigin } from '@/config'
import { buildFileDict } from '@/config/chat'
import { cssVar, themeColors, themeCssVar } from '@/config/theme'
import { usePaneResize } from '@/hooks/usePaneResize'
import { isUnauthorizedError } from '@/utils/authHttp'
import { buildDisplayParts } from '@/utils/groupAssistantParts'
import { parseWriteTodosInput, shouldApplyWriteTodos } from '@/utils/parseWriteTodosInput'
import { qaTypeLabel } from '@/utils/qaType'
import { ensureVisionModelForImageUpload } from '@/utils/visionModel'
import {
  appendReasoningDelta,
  appendStreamFailureNotice,
  appendTextDelta,
  appendTextDeltaWithRedactedThinking,
  appendUserStopNotice,
  applyToolOutput,
  assistantPartsStillStreaming,
  completeLastReasoningPart,
  createRedactedThinkingStreamCtx,
  emptyMessageContent,
  flushRedactedThinkingStreamCtx,
  formatUsageSummary,
  hasValidContextWindow,
  hasValidUsage,
  markStreamingPartsComplete,
  shortenChatErrorToast,
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
/** 会话上下文侧栏（产物/附件）是否展开，默认关闭 */
const sessionFilesPanelOpen = ref(false)

/** 是否显示欢迎/默认页（未进入具体会话对话流） */
const showDefaultPage = ref(true)

function reloadSessionFilesPanel() {
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

// 使用 onMounted 生命周期钩子加载历史对话
onBeforeMount(() => {
  try {
    if (businessStore.qa_type === 'TEST_CASE_QA') {
      businessStore.update_qa_type('COMMON_QA')
    }
    applyWelcomeRouteQaType()
    // 开始加载历史对话
    isLoadingHistory.value = true
    isInit.value = true
    fetchConversationHistory(isInit, conversationItems, tableData, currentRenderIndex, null, '')
  } catch (error) {
    console.error('加载历史对话失败:', error)
    window.$ModalMessage.error('加载历史对话失败，请重试')
  } finally {
    // 加载完成
    isLoadingHistory.value = false
  }
})

// 管理对话
const isModalOpen = ref(false)
function openModal() {
  isModalOpen.value = true
}
// 模态框关闭
function handleModalClose(value) {
  isModalOpen.value = value
  isInit.value = true
  // 重新加载对话记录
  fetchConversationHistory(
    isInit,
    conversationItems,
    tableData,
    currentRenderIndex,
    null,
    '',
  )
  showDefaultPage.value = true
}

// 新建对话
function newChat() {
  sessionContext.value = null
  backgroundColorVariable.value = cssVar(themeCssVar.bgElevated)

  if (showDefaultPage.value) {
    window.$ModalMessage.success(`已经是最新对话`)
    return
  }
  showDefaultPage.value = true
  isInit.value = true
  conversationItems.value = []
  stylizingLoading.value = false
  suggested_array.value = []

  // 清除表格选中状态
  currentIndex.value = null

  // 清理 Todo 列表（PRD：仅在当前会话生效，会话结束后不持久化）
  businessStore.todos = []

  clearComposerQueue()
  inputTextString.value = ''

  // 新增：生成当前问答类型的新uuid
  uuids.value[qa_type.value] = uuidv4()
}

/**
 * 默认大模型（已移除，使用 useChat）
 */
// currentChatId 已移除，使用 useChat 管理 sessionId


// 对话等待提示词图标
const stylizingLoading = ref(false)

// 输入字符串
const inputTextString = ref('')
const refInputTextString = ref<InputInst | null>()

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
  onAqtiveChange(item.qa_type, item.chat_id)


  // 清空推荐列表
  suggested_array.value = []
  // 发送问题重新生成
  handleCreateStylized(item.question, item.file_key)
  scrollToBottom()
}

// 赞（后端反馈接口未接入，仅本地提示）
const onPraiseFeadBack = (_index: number) => {
  window.$ModalMessage.destroyAll()
  window.$ModalMessage.success('感谢反馈', { duration: 1500 })
}

// 开始输出时隐藏加载提示
const onBeginRead = async (index: number) => {
  // 设置最上面的滚动提示图标隐藏
  contentLoadingStates.value[currentRenderIndex.value - 1] = false
}

// 踩（后端反馈接口未接入，仅本地提示）
const onBelittleFeedback = (_index: number) => {
  window.$ModalMessage.destroyAll()
  window.$ModalMessage.success('感谢反馈', { duration: 1500 })
}

// 侧边栏对话历史
interface TableItem {
  uuid: string
  key: string
  chat_id: string
  qa_type: string
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
      return h(
        'div',
        { class: 'flex items-center gap-8px min-w-0 pr-4px' },
        [
          h('div', {
            class: ['size-18px shrink-0 inline-flex items-center justify-center', sessionQaIconClass(row.qa_type)],
            style: { color: sessionQaIconColor(row.qa_type) },
            title: sessionQaTooltip(row.qa_type),
          }),
          h('span', { class: 'truncate flex-1 min-w-0' }, row.key),
        ],
      )
    },
  },
])

const tableData = ref<TableItem[]>([])
const tableRef = ref(null)

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

const sessionContext = ref<import('@/views/chat/messageParts').ContextWindowSnapshot | null>(null)
const selectedKbCollections = ref<string[]>([])
const selectedModelId = ref('')

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
  })
}

function normalizeKbCollections(raw: unknown): string[] {
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
    const storedModelId = String(session.extra?.model_id ?? '').trim()
    selectedModelId.value = storedModelId
  } catch {
    sessionContext.value = null
    selectedKbCollections.value = []
    selectedModelId.value = ''
  }
}

// SSE：依赖 conversationItems / uuids / qa_type，须放在其后
const sseStream = useSSEStream({
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
  onFinish: (detail) => {
    stylizingLoading.value = false
    patchLastAssistantParts((parts) => flushRedactedThinkingStreamCtx(parts, redactedThinkingStreamCtx))
    const lastIdx = conversationItems.value.findLastIndex((item) => item.role === 'assistant')
    if (lastIdx !== -1) {
      const prev = conversationItems.value[lastIdx]
      if (prev.messageContent?.version === 1) {
        const parts = detail?.finish_reason === 'stopped'
          ? appendUserStopNotice(prev.messageContent.parts)
          : markStreamingPartsComplete(prev.messageContent.parts)
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

async function stopChatStream() {
  const sessionId = getChatSessionId()
  const stopToken = sseStream.getStopToken()
  sseStream.abortStream()
  try {
    await stopChat(sessionId, qa_type.value, stopToken)
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

const checkAllFilesUploaded = () => {
  const pendingFiles = fileUploadRef.value?.pendingUploadFileInfoList || []

  if (qa_type.value === 'FAULT_OPERATION_QA' && pendingFiles.length > 0) {
    window.$ModalMessage.warning('故障运维暂不支持文件上传')
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
      const sessionId = getChatSessionId()
      await ensureSession(sessionId, { extra: { qa_type: qa_type.value } })
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
  if (qa_type.value === 'COMMON_QA') {
    streamExtra.kb_collections = selectedKbCollections.value
  }
  if (qa_type.value !== 'TEST_CASE_QA' && selectedModelId.value) {
    streamExtra.model_id = selectedModelId.value
  }
  await sseStream.sendMessage(
    uuids.value[qa_type.value],
    textContent,
    streamExtra,
  )

  // 滚动到底部
  scrollToBottom()
}

// 滚动到底部
const scrollToBottom = () => {
  if (isView.value === false) {
    nextTick(() => {
      if (messagesContainer.value) {
        messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
      }
    })
  }
}

const placeholder = computed(() => {
  if (uploadingOnSend.value) {
    return '附件上传中...'
  }
  return '输入任意问题...'
})

function onComposerKeydown(e: KeyboardEvent) {
  if (e.key !== 'Enter' || e.shiftKey || e.isComposing) {
    return
  }
  e.preventDefault()
  if (!stylizingLoading.value && sendDisabled.value) {
    return
  }
  void handleCreateStylized()
}

const generateRandomSuffix = function () {
  return Math.floor(Math.random() * 10000) // 生成0到9999之间的随机整数
}

// 重置状态
const handleResetState = () => {
  inputTextString.value = ''
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
const sessionContextMenuOptions = [
  { label: '修改标题', key: 'rename' },
]

function closeSessionContextMenu() {
  sessionContextMenuShow.value = false
  sessionContextMenuTarget.value = null
}

function handleSessionContextMenuSelect(key: string) {
  const target = sessionContextMenuTarget.value
  closeSessionContextMenu()
  if (key !== 'rename' || !target) {
    return
  }
  openRenameSessionModal(target)
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

      // 这里根据chat_id 过滤同一轮对话数据
      await fetchConversationHistory(
        isInit,
        conversationItems,
        tableData,
        currentRenderIndex,
        row,
        '',
      )

      // 与顶栏切换不同：从历史会话点入时已拉取 messages，不能再因 qa_type 不一致而清空列表并打开默认页
      onAqtiveChange(row.qa_type, row.chat_id, true)
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
/**
 * @param fromHistorySelection 为 true 时表示从左侧历史会话点入：已加载该会话 messages，仅同步 qa_type / uuid，不得清空 conversationItems 或切回默认页
 */
const onAqtiveChange = (val, chat_id, fromHistorySelection = false) => {
  businessStore.todos = []

  // 切换到不同问答类型时，清空聊天记录（顶栏切换）；历史会话点入时跳过，否则会覆盖刚加载的 messages
  if (qa_type.value !== val) {
    suggested_array.value = []
    if (!fromHistorySelection) {
      conversationItems.value = []
      showDefaultPage.value = true
      currentIndex.value = null
      clearComposerQueue()
    }
  }

  qa_type.value = val
  businessStore.update_qa_type(val)

  // 切换类型时生成新uuid
  if (chat_id) {
    uuids.value[val] = chat_id
    void loadSessionContext(chat_id)
    reloadSessionFilesPanel()
  } else {
    uuids.value[val] = uuidv4()
    sessionContext.value = null
    selectedKbCollections.value = []
    selectedModelId.value = ''
  }

  // 测试用例生成在独立页面（TestAssistant），不在对话页内完成
  if (val === 'TEST_CASE_QA' && route.name !== 'TestCaseGenerate') {
    router.push({ name: 'TestCaseGenerate' })
  }
}

const WELCOME_QA_TYPES = ['COMMON_QA', 'SUPER_AGENT_QA', 'FAULT_OPERATION_QA', 'TEST_CASE_QA'] as const

/** 从 URL 同步问答类型（不触发清空逻辑，避免首屏与历史加载打架） */
function applyWelcomeRouteQaType() {
  const q = route.query.qa_type
  if (typeof q !== 'string' || !(WELCOME_QA_TYPES as readonly string[]).includes(q)) {
    return
  }
  // 测试用例在独立路由完成；从 URL 同步 TEST_CASE_QA 会误触发跳转，导致无法停留在对话页
  if (q === 'TEST_CASE_QA') {
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
const searchChatRef = useTemplateRef('searchChatRef')
const isFocusSearchChat = ref(false)
const onFocusSearchChat = () => {
  if (!showDefaultPage.value) {
    newChat()
  }
  isFocusSearchChat.value = true
  nextTick(() => {
    searchChatRef.value?.focus()
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

// 背景颜色 默认页面和内容页面动态调整
const backgroundColorVariable = ref(cssVar(themeCssVar.bgElevated))


// 添加一键滚动到底部功能的相关代码
const showScrollToBottom = ref(false)
const scrollThreshold = 1000 // 滚动超过100px时显示按钮

// 用户点击图标滚动到底部
const clickScrollToBottom = () => {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
      showScrollToBottom.value = false // 滚动到底部后隐藏按钮
    }
  })
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

function canUploadComposerFiles(): boolean {
  if (qa_type.value === 'FAULT_OPERATION_QA') {
    window.$ModalMessage.warning('故障运维暂不支持文件上传')
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
    class="flex justify-between items-center h-full"
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
        v-model:collapsed="collapsed"
        class="chat-history-sider"
        collapse-mode="width"
        :collapsed-width="0"
        :width="historySiderWidth"
        :show-collapsed-content="false"
        show-trigger="arrow-circle"
        bordered
      >
        <div
          h-full
          class="content"
          flex="~ col"
        >
          <div class="sidebar-header-toolbar header p-20">
            <div
              class="create-chat-box"
              :class="{
                hide: isFocusSearchChat,
              }"
            >
              <n-button
                type="primary"
                icon-placement="left"
                strong
                class="create-chat"
                :disabled="stylizingLoading"
                @click="newChat"
              >
                <template #icon>
                  <n-icon>
                    <div class="i-hugeicons:add-01"></div>
                  </n-icon>
                </template>
                新建对话
              </n-button>
            </div>
            <button
              v-if="!isFocusSearchChat"
              type="button"
              class="search-chat-trigger"
              aria-label="搜索对话"
              @click="onFocusSearchChat"
            >
              <span class="search-chat-trigger__icon i-hugeicons:search-01" aria-hidden="true"></span>
            </button>
            <n-input
              v-else
              ref="searchChatRef"
              v-model:value="searchText"
              placeholder="搜索"
              class="search-chat-input"
              clearable
              @blur="onBlurSearchChat()"
              @input="handleSearch()"
              @keyup.enter="handleSearch()"
              @clear="handleClear()"
            >
              <template #prefix>
                <span class="search-chat-input__icon i-hugeicons:search-01" aria-hidden="true"></span>
              </template>
            </n-input>
          </div>
          <div flex="1 ~ col" class="scrollable-table-container">
            <n-dropdown
              trigger="manual"
              placement="bottom-start"
              :show="sessionContextMenuShow"
              :x="sessionContextMenuX"
              :y="sessionContextMenuY"
              :options="sessionContextMenuOptions"
              @select="handleSessionContextMenuSelect"
              @clickoutside="closeSessionContextMenu"
            />
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
            <n-data-table
              ref="tableRef"
              class="custom-table"
              :style="{
                'font-size': `14px`,
                '--n-td-color': cssVar(themeCssVar.bgElevated),
                'font-family': `-apple-system, BlinkMacSystemFont,'Segoe UI', Roboto, 'Helvetica Neue', Arial,sans-serif`,
              }"
              size="small"
              :bordered="false"
              :bottom-bordered="false"
              :single-line="false"
              :columns="historySidebarColumns"
              :data="tableData"
              :loading="isLoadingHistory"
              :row-props="rowProps"
            />
          </div>
          <div
            class="footer"
            style="flex-shrink: 0"
          >
            <n-divider
              style="width: calc(100% - 60px); margin-left: 25px; margin-right: 35px;

--n-color: var(--noesis-color-bg-muted);"
            />
            <n-button
              quaternary
              icon-placement="left"
              type="primary"
              strong
              :style="{
                'width': `200px`,
                'height': `38px`,
                'margin-left': `20px`,
                'margin-bottom': `10px`,
                'align-self': `center`,
                'text-align': `center`,
                'font-family': `-apple-system, BlinkMacSystemFont,
            'Segoe UI', Roboto, 'Helvetica Neue', Arial,
            sans-serif`,
                'font-size': `14px`,
              }"
              @click="openModal"
            >
              <template #icon>
                <n-icon>
                  <div class="i-hugeicons:voice-id"></div>
                </n-icon>
              </template>
              管理对话
            </n-button>
          </div>
        </div>
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
                <NavigationNavBar
                  class="flex-1 min-w-0"
                  :background-color="backgroundColorVariable"
                />
                <button
                  v-if="!showDefaultPage && uuids[qa_type]"
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
                  <div v-if="showDefaultPage">
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
                    <div v-if="item.role === 'user'" class="flex flex-col items-end space-y-2 w-full">
                      <!-- 用户消息 -->
                      <div
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
                              :error="entry.part.error"
                              :duration_ms="entry.part.duration_ms"
                              :child-parts="entry.childParts"
                            />
                            <ToolCallCollapse
                              v-else-if="entry.kind === 'part' && entry.part.type === 'tool'"
                              appearance="light"
                              :name="entry.part.name"
                              :arguments="entry.part.input"
                              :result="entry.part.output"
                              :error="entry.part.error"
                              :status="entry.part.status"
                              :duration_ms="entry.part.duration_ms"
                            />
                            <MarkdownPreview
                              v-else-if="entry.kind === 'part' && entry.part.type === 'text'"
                              :content="entry.part.content || ''"
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
                              @praiseFeadBack="() => onPraiseFeadBack(index)"
                              @belittleFeedback="() => onBelittleFeedback(index)"
                            />
                          </template>
                          <AssistantStreamingIndicator
                            v-if="showAssistantReplyLoading(index, item.role)"
                            section
                            :divided="buildDisplayParts(item.messageContent.parts).length > 0"
                            :label="buildDisplayParts(item.messageContent.parts).length > 0 ? '正在继续生成' : '正在生成'"
                          />
                          <div
                            v-if="hasValidUsage(item.msg_metadata?.usage)"
                            class="assistant-usage-summary"
                          >
                            {{ formatUsageSummary(item.msg_metadata!.usage!) }}
                          </div>
                          <AssistantReplyToolbar
                            v-if="item.messageContent.parts.length > 0 && !assistantPartsStillStreaming(item.messageContent.parts)"
                            :qa-type="item.qa_type || 'COMMON_QA'"
                            :copy-text="[item.reasoning, item.content].filter((s) => s && String(s).trim()).join('\n\n')"
                            :langfuse_session_id="item.langfuse_session_id"
                            :langfuse-ui-origin="langfuseUiOrigin"
                            @praise-fead-back="() => onPraiseFeadBack(index)"
                            @belittle-feedback="() => onBelittleFeedback(index)"
                            @recycle-qa="() => onRecycleQa(index)"
                          />
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
                          @praiseFeadBack="() => onPraiseFeadBack(index)"
                          @belittleFeedback="() => onBelittleFeedback(index)"
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
                    class="mx-10%"
                  >
                    <!-- 文档流内、与输入区同宽，避免 absolute 遮挡消息区 -->
                    <TodoList
                      :todos="businessStore.todos"
                    />
                    <div
                      flex="~ gap-10"
                      class="h-40"
                    >
                      <n-button
                        type="default"
                        :class="[
                          qa_type === 'COMMON_QA' && 'active-tab',
                          'rounded-100 w-120 h-36 p-15 text-13 text-tab',
                        ]"
                        @click="onAqtiveChange('COMMON_QA', '')"
                      >
                        <template #icon>
                          <n-icon size="16">
                            <svg
                              t="1742194713465"
                              class="icon"
                              viewBox="0 0 1024 1024"
                              version="1.1"
                              xmlns="http://www.w3.org/2000/svg"
                              p-id="8188"
                              width="60"
                              height="60"
                            >
                              <path
                                d="M80.867881 469.76534l0.916659 0.916659 79.711097-79.711097L160.655367 389.901467a162.210364 162.210364 0 0 1 229.164631-229.164631l236.345122 236.345122L706.028994 317.332667l-236.345123-236.803452a275.112139 275.112139 0 0 0-388.81599 389.27432z m472.690245-388.81599l-0.916658 0.916659 79.711097 79.711097 0.916659-0.916658A162.210364 162.210364 0 0 1 862.663019 389.901467l-236.345122 236.345122 79.711097 79.711098 236.803452-236.345123a275.112139 275.112139 0 0 0-389.27432-388.81599z m-84.027031 861.506236l0.916659-0.916659-79.711098-79.711097-0.916658 0.916658a162.210364 162.210364 0 0 1-229.164631-229.431989l236.345122-236.345123L317.251198 317.332667l-236.803452 236.345122a275.112139 275.112139 0 0 0 389.27432 388.815991z m99.801197-372.736272a81.811773 81.811773 0 0 0 21.197728-78.794439 80.895115 80.895115 0 0 0-57.59671-57.596711 81.620803 81.620803 0 1 0 36.398982 136.352956z m373.156407-15.659583l-0.916659-0.916659-79.711097 79.711097 0.916658 0.916659a162.248559 162.248559 0 0 1-229.431989 229.431989L396.885907 626.704918 317.251198 706.568792l236.345122 236.803452A275.073945 275.073945 0 0 0 942.374117 554.136119z"
                                fill="#297CE9"
                                p-id="8189"
                              />
                            </svg>
                          </n-icon>
                        </template>
                        智能问答
                      </n-button>
                      <n-button
                        type="default"
                        :class="[
                          qa_type === 'SUPER_AGENT_QA' && 'active-tab',
                          'rounded-100 w-120 h-36 p-15 text-13 text-tab',
                        ]"
                        @click="onAqtiveChange('SUPER_AGENT_QA', '')"
                      >
                        <template #icon>
                          <n-icon size="18">
                            <svg
                              t="1732528323504"
                              class="icon"
                              viewBox="0 0 1024 1024"
                              version="1.1"
                              xmlns="http://www.w3.org/2000/svg"
                              p-id="41739"
                              width="64"
                              height="64"
                            >
                              <path
                                d="M96 896c-8 0-15.5-3.1-21.2-8.8C69.1 881.6 66 874 66 866V445c0-5.5 4.5-10 10-10s10 4.5 10 10v421c0 2.7 1 5.2 2.9 7.1 1.9 1.9 4.4 2.9 7.1 2.9h612c5.5 0 10 4.5 10 10s-4.5 10-10 10H96z m748 0v-20c2.7 0 5.2-1 7.1-2.9 1.9-1.9 2.9-4.4 2.9-7.1v-80c0-5.5 4.5-10 10-10s10 4.5 10 10v80c0 8-3.1 15.5-8.8 21.2-5.6 5.7-13.2 8.8-21.2 8.8z m20-450c-5.5 0-10-4.5-10-10V126c0-5.5-4.5-10-10-10H96c-5.5 0-10 4.5-10 10v193c0 5.5-4.5 10-10 10s-10-4.5-10-10V126c0-16.5 13.4-30 30-30h748c16.5 0 30 13.4 30 30v310c0 5.5-4.5 10-10 10z"
                                fill="#222222"
                                p-id="41740"
                              />
                              <path
                                d="M781 886m-16 0a16 16 0 1 0 32 0 16 16 0 1 0-32 0Z"
                                fill="#222222"
                                p-id="41741"
                              />
                              <path
                                d="M76 383m-16 0a16 16 0 1 0 32 0 16 16 0 1 0-32 0Z"
                                fill="#222222"
                                p-id="41742"
                              />
                              <path
                                d="M84 226h775v20H84zM750 826c-57.2 0-110.9-22.3-151.3-62.7C558.3 722.9 536 669.2 536 612s22.3-110.9 62.7-151.3C639.1 420.3 692.8 398 750 398s110.9 22.3 151.3 62.7C941.7 501.1 964 554.8 964 612s-22.3 110.9-62.7 151.3C860.9 803.7 807.2 826 750 826z m0-408c-107 0-194 87-194 194s87 194 194 194 194-87 194-194-87-194-194-194z"
                                fill="#222222"
                                p-id="41743"
                              />
                              <path
                                d="M901.7 753.2c-1 0-2.1-0.2-3.1-0.5-4.1-1.3-6.9-5.2-6.9-9.5V478.8c0-4.3 2.8-8.2 6.9-9.5 4.1-1.3 8.6 0.1 11.2 3.6 24.9 34 51.4 75.6 51.4 139.1 0 62-22.3 97.3-51.4 137.1-1.9 2.7-4.9 4.1-8.1 4.1z m10.1-241.9v200c17.9-28 29.5-56.4 29.5-99.3-0.1-40.2-11-70.5-29.5-100.7z"
                                fill="#222222"
                                p-id="41744"
                              />
                              <path
                                d="M859 788l93 130"
                                fill="#358AFE"
                                p-id="41745"
                              />
                              <path
                                d="M952 928c-3.1 0-6.2-1.5-8.1-4.2l-93-130c-3.2-4.5-2.2-10.7 2.3-14 4.5-3.2 10.7-2.2 14 2.3l93 130c3.2 4.5 2.2 10.7-2.3 14-1.8 1.3-3.9 1.9-5.9 1.9zM482.4 468.4H171.6c-8.8 0-16-7.2-16-16v-89.8c0-8.8 7.2-16 16-16h310.8c8.8 0 16 7.2 16 16v89.8c0 8.8-7.2 16-16 16z m-306.8-20h302.8v-81.8H175.6v81.8z m306.8-81.8zM384 580H165c-5.5 0-10-4.5-10-10s4.5-10 10-10h219c5.5 0 10 4.5 10 10s-4.5 10-10 10zM455 690H165c-5.5 0-10-4.5-10-10s4.5-10 10-10h290c5.5 0 10 4.5 10 10s-4.5 10-10 10zM525 800H165c-5.5 0-10-4.5-10-10s4.5-10 10-10h360c5.5 0 10 4.5 10 10s-4.5 10-10 10zM183 146c15.5 0 28 12.5 28 28s-12.5 28-28 28-28-12.5-28-28 12.5-28 28-28z m94 0c15.5 0 28 12.5 28 28s-12.5 28-28 28-28-12.5-28-28 12.5-28 28-28z m94 0c15.5 0 28 12.5 28 28s-12.5 28-28 28-28-12.5-28-28 12.5-28 28-28z"
                                fill="#222222"
                                p-id="41746"
                              />
                            </svg>
                          </n-icon>
                        </template>
                        智能体
                      </n-button>
                      <n-button
                        type="default"
                        :class="[
                          qa_type === 'FAULT_OPERATION_QA' && 'active-tab',
                          'rounded-100 w-120 h-36 p-15 text-13 text-tab',
                        ]"
                        @click="onAqtiveChange('FAULT_OPERATION_QA', '')"
                      >
                        <template #icon>
                          <n-icon size="18">
                            <svg
                              t="1743292000000"
                              class="icon"
                              viewBox="0 0 1024 1024"
                              version="1.1"
                              xmlns="http://www.w3.org/2000/svg"
                              p-id="8849"
                              width="64"
                              height="64"
                            >
                              <path
                                d="M512 160c-35.3 0-64 28.7-64 64v32c0 35.3 28.7 64 64 64s64-28.7 64-64v-32c0-35.3-28.7-64-64-64z"
                                fill="#222222"
                                p-id="8850"
                              />
                              <path
                                d="M480 384h64v320h-64z"
                                fill="#222222"
                                p-id="8851"
                              />
                              <path
                                d="M448 704h128v32c0 17.7-14.3 32-32 32H480c-17.7 0-32-14.3-32-32v-32z"
                                fill="#222222"
                                p-id="8852"
                              />
                              <path
                                d="M416 80c-22.1 0-40 17.9-40 40v24c0 22.1 17.9 40 40 40s40-17.9 40-40v-24c0-22.1-17.9-40-40-40z"
                                fill="#222222"
                                p-id="8853"
                              />
                              <path
                                d="M608 80c-22.1 0-40 17.9-40 40v24c0 22.1 17.9 40 40 40s40-17.9 40-40v-24c0-22.1-17.9-40-40-40z"
                                fill="#222222"
                                p-id="8854"
                              />
                              <path
                                d="M448 144c-22.1 0-40 17.9-40 40v24c0 22.1 17.9 40 40 40h128c22.1 0 40-17.9 40-40v-24c0-22.1-17.9-40-40-40H448z"
                                fill="#222222"
                                p-id="8855"
                              />
                              <path
                                d="M848 512c0-141.4-114.6-256-256-256S336 370.6 336 512c0 44.2 11.2 85.8 31.1 122.9L224 768h576l-143.1-133.1C880.8 597.8 896 556.2 896 512zM592 768H432l144-160h144l-128 160z"
                                fill="#222222"
                                p-id="8856"
                              />
                              <path
                                d="M512 320c-105.6 0-192 86.4-192 192s86.4 192 192 192 192-86.4 192-192-86.4-192-192-192z"
                                fill="#222222"
                                p-id="8857"
                              />
                            </svg>
                          </n-icon>
                        </template>
                        故障运维
                      </n-button>
                      <n-button
                        type="default"
                        :class="[
                          qa_type === 'TEST_CASE_QA' && 'active-tab',
                          'rounded-100 w-120 h-36 p-15 text-13 text-tab',
                        ]"
                        @click="onAqtiveChange('TEST_CASE_QA', '')"
                      >
                        <template #icon>
                          <n-icon size="18">
                            <svg
                              t="1743292000001"
                              class="icon"
                              viewBox="0 0 1024 1024"
                              version="1.1"
                              xmlns="http://www.w3.org/2000/svg"
                              p-id="88501"
                              width="64"
                              height="64"
                            >
                              <path
                                d="M896 128H128c-35.2 0-64 28.8-64 64v640c0 35.2 28.8 64 64 64h768c35.2 0 64-28.8 64-64V192c0-35.2-28.8-64-64-64z"
                                fill="none"
                                stroke="#222222"
                                stroke-width="32"
                                p-id="88502"
                              />
                              <path
                                d="M320 320h384M320 448h384M320 576h256"
                                fill="none"
                                stroke="#222222"
                                stroke-width="24"
                                stroke-linecap="round"
                                p-id="88503"
                              />
                              <path
                                d="M704 640l-96 96-32-32-64 64"
                                fill="none"
                                stroke="#4CAF50"
                                stroke-width="24"
                                stroke-linecap="round"
                                stroke-linejoin="round"
                                p-id="88504"
                              />
                            </svg>
                          </n-icon>
                        </template>
                        测试用例
                      </n-button>
                    </div>
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
                        :upload-mode="usesSessionAttachmentUpload(qa_type) ? 'chat' : 'kb'"
                        :get-session-id="getChatSessionId"
                        @chatImageUploaded="onChatImageUploaded"
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
                        :qa-type="qa_type"
                        :session-id="uuids[qa_type] ?? ''"
                        :disabled="sseIsLoading"
                        :file-upload-ref="fileUploadRef"
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
            v-if="sessionFilesPanelOpen && !showDefaultPage && uuids[qa_type]"
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
    <TableModal
      :show="isModalOpen"
      @update:show="handleModalClose"
    />
  </div>
</template>

<style lang="scss" scoped>
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

.create-chat-box {
  flex: 1;
  min-width: 0;
  overflow: visible;
  transition: flex 0.25s ease, opacity 0.25s ease, margin 0.25s ease;

  &.hide {
    flex: 0 0 0;
    width: 0;
    margin: 0;
    opacity: 0;
    overflow: hidden;
    pointer-events: none;
  }
}

.create-chat {
  width: 100%;
  height: 40px;
  text-align: center;
  font-family: inherit;
  font-weight: 500;
  font-size: 14px;
  border-radius: var(--noesis-radius-pill);

  &:deep(.n-button__border),
  &:deep(.n-button__state-border) {
    border-radius: inherit !important;
  }
}

.sidebar-header-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
  position: sticky;
  top: 0;
  z-index: 1;
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

.search-chat-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  margin: 0;
  padding: 0;
  border: 1px solid var(--noesis-color-border, #e8eaf3);
  border-radius: var(--noesis-radius-round);
  background: var(--noesis-color-bg-elevated, #fff);
  color: var(--noesis-color-text-muted, #64748b);
  cursor: pointer;
  transition: border-color 0.2s ease, color 0.2s ease, background-color 0.2s ease;
}

.search-chat-trigger:hover {
  color: var(--noesis-color-primary, #5c7cfa);
  border-color: var(--noesis-color-primary-muted, #a48ef4);
  background: var(--noesis-color-primary-bg-subtle, rgb(92 124 250 / 4%));
}

.search-chat-trigger__icon {
  display: inline-block;
  width: 18px;
  height: 18px;
  font-size: 18px;
  line-height: 1;
  color: var(--noesis-color-text-secondary);
  flex-shrink: 0;
}

.search-chat-input {
  flex: 1;
  min-width: 0;
}

.search-chat-input :deep(.n-input-wrapper) {
  height: 36px;
  border-radius: var(--noesis-radius-pill);
}

.search-chat-input__icon {
  display: inline-block;
  width: 16px;
  height: 16px;
  font-size: 16px;
  color: var(--noesis-color-text-secondary);
  flex-shrink: 0;
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

:deep(.custom-table .n-data-table-thead) {
  display: none;
}

:deep(.custom-table .n-data-table-table) {
  border-collapse: collapse;
}

:deep(.custom-table .n-data-table-th),
:deep(.custom-table .n-data-table-td) {
  border: none;
}

:deep(.custom-table td) {
  color: var(--noesis-color-text, #1a1d33);
  padding: 12px 16px;
  background-color: var(--noesis-color-bg-elevated, #fff);
  transition: background-color 0.3s cubic-bezier(0.4, 0, 0.2, 1);

  /* 优化后的系统字体栈：优先使用系统原生字体 */

  font-family:
    /* macOS */ -apple-system,
    /* Windows */ BlinkMacSystemFont,
    /* 通用系统UI */ 'Segoe UI',
    /* 开源跨平台 */ Roboto,
    /* Linux */ Oxygen, Ubuntu, Cantarell,
    /* fallback */ 'Open Sans', 'Helvetica Neue', Arial,
    /* 终极兜底 */ sans-serif,
    /* 现代浏览器推荐 */ system-ui,
    /* 苹果新字体支持 */ "SF Pro Text";

  /* 可选：基础字体大小与行高，提升可读性 */

  font-size: 14px;

  /* 优化字体渲染 */

  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizelegibility;
}

:deep(.custom-table .selected-row td) {
  color: var(--noesis-color-primary) !important;
  font-weight: bold;
  padding: 12px 30px !important;
  background: var(--noesis-chat-selected-row-bg);
  transform: scale(1.001);
  transition: all 0.3s ease;
}

.default-page {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100vh;
  background-color: var(--noesis-color-bg);
}

.active-tab,
:deep(.n-button.active-tab) {
  background: var(--noesis-chat-tab-active-bg) !important;
  border-color: var(--noesis-chat-tab-active-border, var(--noesis-color-primary)) !important;
  color: var(--noesis-chat-tab-active-color, var(--noesis-color-primary)) !important;
  box-shadow: var(--noesis-chat-tab-active-shadow, none);
  font-weight: 600;
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


/** 自定义对话历史表格滚动条样式 */

.scrollable-table-container {
  overflow-y: hidden;
  height: 100%;
  background-color: var(--noesis-color-bg-elevated);
  transition: background-color 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.scrollable-table-container:hover {
  overflow-y: auto; /* 鼠标悬停时显示滚动条 */
}

/* 隐藏滚动条轨道 */

.scrollable-table-container::-webkit-scrollbar {
  width: 5px; /* 滚动条宽度 */
}

.scrollable-table-container::-webkit-scrollbar-track {
  background: transparent; /* 滚动条轨道背景透明 */
}

.scrollable-table-container::-webkit-scrollbar-thumb {
  background-color: var(--noesis-scrollbar-thumb-muted);
  border-radius: 4px;
}

/* 一键到底部按钮样式，底部居中显示 */

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

.assistant-usage-summary {
  padding: 4px 16px 8px;
  font-size: 11px;
  line-height: 1.4;
  color: var(--noesis-color-text-hint);
  font-family: ui-monospace, 'SF Mono', Monaco, Consolas, monospace;
  letter-spacing: 0.02em;
}

.chat-top-bar {
  flex-shrink: 0;
  gap: 8px;
  padding-right: 12px;
}
</style>
