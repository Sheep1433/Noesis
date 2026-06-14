<script lang="tsx" setup>
import type { ScrollbarInst, UploadCustomRequestOptions } from 'naive-ui'
import type { TcScene, TcTestCase, CaseGenStatus } from '@/views/TestAssistant/scenesToMarkmap'
import { Transformer } from 'markmap-lib'
import { Toolbar } from 'markmap-toolbar'
import { Markmap } from 'markmap-view'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { createSession, exportTestCaseMarkdown, getSessionMessages } from '@/api/chat'
import type { TestCaseExportCaseItem } from '@/api/chat'
import { query_user_qa_record } from '@/api'
import { getCollections, uploadDocument } from '@/api/knowledgeBase'
import { KB_FILE_DICT_REF, TEST_CASE_UPLOAD_COLLECTION } from '@/config/knowledge'
import { normalizeApiContent } from '@/views/chat/messageParts'
import { useSSEStream } from '@/views/chat/useSSEStream'
import {
  computeSceneCaseProgress,
  countTestPoints,
  MARKMAP_WELCOME,
  sceneProgressLabel,
  sceneProgressStatus,
  scenesToMarkmap,
} from '@/views/TestAssistant/scenesToMarkmap'

/** 需求文档：Word（.docx）与 Markdown（.md / .markdown） */
const TEST_REQUIREMENT_EXT = ['.docx', '.md', '.markdown'] as const

function isTestRequirementFilename(filename: string): boolean {
  const n = filename.trim().toLowerCase()
  if (!n) {
    return true
  }
  return TEST_REQUIREMENT_EXT.some((ext) => n.endsWith(ext))
}

const transformer = new Transformer()
const initValue = ref(MARKMAP_WELCOME)
const mm = ref()
const svgRef = ref()

const update = () => {
  if (!mm.value) {
    return
  }
  const { root } = transformer.transform(initValue.value)
  mm.value.setData(root)
  mm.value.fit()
}

/** 须始终在 DOM 中（勿放在 n-spin 内），供 Markmap 工具栏 append */
const markmapHostRef = ref<HTMLElement | null>(null)

interface RequirementItem {
  /** 列表 key；历史项与 sessionId 相同，本页新上传未建会话时为临时 uuid */
  id: string
  title: string
  /** Qdrant payload.file_name，与 file_dict 的 key 一致 */
  kbFileName: string
  kbCollection: string
  /** 仅旧会话回放可能带内联正文；新上传走 KB_FILE_DICT_REF */
  markdown?: string
  sessionId?: string
}

const requirementList = ref<RequirementItem[]>([])
const selectedReqId = ref<string | null>(null)
const sessionsListLoading = ref(false)

function extractMessagePlainText(content: unknown): string {
  if (typeof content === 'string') {
    try {
      return extractMessagePlainText(JSON.parse(content))
    } catch {
      return content
    }
  }
  if (!content || typeof content !== 'object') {
    return ''
  }
  const normalized = normalizeApiContent(content)
  return normalized.parts
    .filter((p) => p.type === 'text')
    .map((p) => p.content || '')
    .join('\n')
    .trim()
}

function linkListItemToSession(
  localId: string,
  sessionId: string,
  title: string,
  kbFileName: string,
  kbCollection: string,
) {
  const idx = requirementList.value.findIndex((r) => r.id === localId || r.sessionId === sessionId)
  const entry: RequirementItem = {
    id: sessionId,
    sessionId,
    title: title.trim() || '新对话',
    kbFileName,
    kbCollection,
  }
  if (idx >= 0) {
    requirementList.value[idx] = entry
  } else {
    requirementList.value.unshift(entry)
  }
  selectedReqId.value = sessionId
}

async function loadDesignSessionHistory() {
  sessionsListLoading.value = true
  try {
    const res = await query_user_qa_record(1, 500, '', '')
    if (!res.ok) {
      return
    }
    const data = await res.json()
    const records = (data?.data?.records ?? []) as Array<{
      session_id: string
      title?: string
      qa_type?: string
    }>
    const fromServer: RequirementItem[] = records
      .filter((r) => r.qa_type === 'TEST_CASE_QA' && r.session_id)
      .map((r) => ({
        id: r.session_id,
        sessionId: r.session_id,
        title: (r.title || '').trim() || '新对话',
        kbFileName: '',
        kbCollection: TEST_CASE_UPLOAD_COLLECTION,
      }))
    const localDrafts = requirementList.value.filter((x) => !x.sessionId)
    const serverIds = new Set(fromServer.map((x) => x.id))
    requirementList.value = [
      ...localDrafts.filter((d) => !serverIds.has(d.id)),
      ...fromServer,
    ]
  } catch {
    // 静默：列表为空时仍可使用上传流程
  } finally {
    sessionsListLoading.value = false
  }
}

async function restoreDesignSession(sessionId: string, item: RequirementItem) {
  tcSessionId.value = sessionId
  chatMessages.value = []
  tcScenes.value = []
  selectedPointNames.value = []
  tcCasesByPoint.value = {}
  caseGenStatus.value = {}
  caseGenErrors.value = {}
  tcPhase.value = 'idle'
  tcPhaseRailIndex.value = -1
  tcStatusText.value = ''
  setMindmapMarkdown(MARKMAP_WELCOME)

  const { messages } = await getSessionMessages(sessionId, { limit: 200 })
  let userCount = 0
  let assistantCount = 0
  for (const msg of messages) {
    const text = extractMessagePlainText(msg.content)
    if (msg.role === 'user') {
      userCount += 1
      if (text) {
        appendChat('user', text)
      }
      const fd = msg.extra?.file_dict as Record<string, string> | undefined
      if (fd && typeof fd === 'object') {
        const first = Object.entries(fd).find(([, v]) => typeof v === 'string')
        if (first) {
          const [fn, body] = first
          const row = requirementList.value.find((r) => r.id === item.id)
          if (body === KB_FILE_DICT_REF || body.length <= 800) {
            item.kbFileName = fn
            if (row) {
              row.kbFileName = fn
            }
          } else {
            item.markdown = body
            item.kbFileName = fn
            if (row) {
              row.markdown = body
              row.kbFileName = fn
            }
          }
        }
      }
    } else if (text) {
      assistantCount += 1
      appendChat('assistant', text)
    }
  }
  if (assistantCount >= 2 || (userCount >= 1 && assistantCount >= 1 && messages.length >= 3)) {
    tcPhase.value = 'done'
  }
}

async function selectRequirement(item: RequirementItem) {
  if (portalBusy.value) {
    window.$ModalMessage?.warning?.('当前有任务进行中，请稍后再切换')
    return
  }
  selectedReqId.value = item.id
  if (item.sessionId) {
    try {
      await restoreDesignSession(item.sessionId, item)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '加载会话失败'
      window.$ModalMessage?.error?.(msg)
    }
  }
}

interface ChatMessage {
  id: string
  role: 'assistant' | 'user' | 'system'
  text: string
}

const chatMessages = ref<ChatMessage[]>([])
const chatScrollRef = ref<ScrollbarInst | null>(null)

function appendChat(role: ChatMessage['role'], text: string) {
  chatMessages.value.push({
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
    role,
    text,
  })
  nextTick(() => {
    chatScrollRef.value?.scrollTo({ top: 1e9, behavior: 'smooth' })
  })
}

const loading = ref(false)

// ---------- 测试用例生成（Word/Markdown → 场景/测试点 → 采纳 → resume）----------
type TcWorkflowPhase =
  | 'idle'
  | 'parsing_doc'
  | 'parse_done'
  | 'gen_scenes'
  | 'pick'
  | 'gen_cases'
  | 'done'

const tcSessionId = ref<string | null>(null)
const tcScenes = ref<TcScene[]>([])
const selectedPointNames = ref<string[]>([])
const tcPhase = ref<TcWorkflowPhase>('idle')
const tcStatusText = ref('')
const tcCasesByPoint = ref<Record<string, TcTestCase>>({})
const caseGenStatus = ref<Record<string, CaseGenStatus>>({})
const caseGenErrors = ref<Record<string, string>>({})
const lastUploadedFileName = ref('需求文档')
const exportLoading = ref(false)

const canExportCases = computed(() => {
  if (tcPhase.value !== 'done') {
    return false
  }
  return Object.keys(tcCasesByPoint.value).some(
    (pn) => caseGenStatus.value[pn] === 'done',
  )
})

function buildPointToSceneName(): Map<string, string> {
  const map = new Map<string, string>()
  for (const sc of tcScenes.value) {
    const sceneName = (sc.scene_name || '').trim()
    for (const tp of sc.test_points || []) {
      const pn = (tp.point_name || '').trim()
      if (pn) {
        map.set(pn, sceneName)
      }
    }
  }
  return map
}

function buildExportTestCases(): TestCaseExportCaseItem[] {
  const sceneMap = buildPointToSceneName()
  const items: TestCaseExportCaseItem[] = []
  for (const [pn, tc] of Object.entries(tcCasesByPoint.value)) {
    if (caseGenStatus.value[pn] !== 'done') {
      continue
    }
    items.push({
      case_id: tc.case_id,
      point_name: (tc.point_name || pn).trim(),
      point_level: tc.point_level,
      point_type: tc.point_type,
      scene_name: tc.scene_name || sceneMap.get(pn) || '',
      preconditions: tc.preconditions || [],
      test_steps: tc.test_steps || [],
      expected_results: tc.expected_results || [],
    })
  }
  return items
}

function buildExportQuery(): string {
  const item = requirementList.value.find(
    (r) => r.id === selectedReqId.value || r.sessionId === tcSessionId.value,
  )
  return (
    inputTextString.value.trim()
    || item?.title?.trim()
    || lastUploadedFileName.value
    || ''
  )
}

async function handleExportCases() {
  if (!tcSessionId.value) {
    window.$ModalMessage?.warning?.('请先完成测试用例生成')
    return
  }
  const test_cases = buildExportTestCases()
  if (!test_cases.length) {
    window.$ModalMessage?.warning?.('暂无可导出的测试用例')
    return
  }
  exportLoading.value = true
  try {
    await exportTestCaseMarkdown(tcSessionId.value, {
      test_cases,
      query: buildExportQuery(),
    })
    window.$ModalMessage?.success?.('已导出 Markdown 报告')
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : '导出失败'
    window.$ModalMessage?.error?.(msg)
  } finally {
    exportLoading.value = false
  }
}

function setMindmapMarkdown(md: string) {
  initValue.value = md
  update()
}

function refreshMindmapFromScenes(scenes: TcScene[], selected?: string[] | null) {
  setMindmapMarkdown(scenesToMarkmap(
    scenes,
    selected,
    tcCasesByPoint.value,
    caseGenStatus.value,
    caseGenErrors.value,
  ))
}

function initCaseGenStatus(pointNames: string[], initial: CaseGenStatus = 'pending') {
  const next: Record<string, CaseGenStatus> = {}
  for (const pn of pointNames) {
    next[pn] = initial
  }
  caseGenStatus.value = next
}

function refreshMindmapNow() {
  refreshMindmapFromScenes(tcScenes.value, selectedPointNames.value.length ? selectedPointNames.value : null)
}

const sceneCaseProgressItems = computed(() => {
  if (!selectedPointNames.value.length) {
    return []
  }
  return computeSceneCaseProgress(
    tcScenes.value,
    selectedPointNames.value,
    caseGenStatus.value,
  ).map((progress) => ({
    ...progress,
    status: sceneProgressStatus(progress),
    label: sceneProgressLabel(progress),
  }))
})

const caseProgressSummary = computed(() => {
  const items = sceneCaseProgressItems.value
  const total = items.reduce((n, i) => n + i.total, 0)
  const done = items.reduce((n, i) => n + i.done, 0)
  const generating = items.filter((i) => i.status === 'generating').length
  const failed = items.reduce((n, i) => n + i.failed, 0)
  return { total, done, generating, failed, sceneCount: items.length }
})

const TC_PHASE_STEPS = [
  { id: 'parse_requirements', label: '解析需求' },
  { id: 'generate_test_points', label: '生成测试点' },
  { id: 'await_user_confirm', label: '待确认' },
  { id: 'parallel_generate_cases', label: '并行生成' },
] as const

function phaseIdToRailIndex(phaseId: string): number {
  const idx = TC_PHASE_STEPS.findIndex((s) => s.id === phaseId)
  return idx
}

function tcPhaseChipClass(stepIndex: number): string {
  if (tcPhaseRailIndex.value === stepIndex) {
    return 'bg-#e8ecff text-#3b5bdb font-600'
  }
  if (tcPhaseRailIndex.value > stepIndex) {
    return 'text-#78849e'
  }
  return 'text-#aab2c9'
}

/** -1：未收到阶段 SSE；0–3：当前高亮步骤 */
const tcPhaseRailIndex = ref(-1)

const sse = useSSEStream({
  onCustomEvent(type, data) {
    if (type === 'phase-start') {
      const pid = typeof data.phase_id === 'string' ? data.phase_id : ''
      const idx = phaseIdToRailIndex(pid)
      if (idx >= 0) {
        tcPhaseRailIndex.value = idx
      }
      const ttl = typeof data.title === 'string' ? data.title.trim() : ''
      if (ttl) {
        tcStatusText.value = ttl
      }
    }
    if (type === 'phase-delta' && typeof data.text_delta === 'string' && data.text_delta.trim()) {
      tcStatusText.value = data.text_delta.trim()
    }
    if (type === 'phase-end') {
      const ok = data.ok !== false
      if (!ok && typeof data.phase_id === 'string') {
        tcStatusText.value = `阶段中断：${data.phase_id}`
      }
    }
    if (type === 'scenario-start') {
      tcPhase.value = 'gen_scenes'
      tcStatusText.value = String((data as { message?: string }).message || '正在生成测试场景与测试点…')
      appendChat('assistant', tcStatusText.value)
    }
    if (type === 'testpoints-confirm-required') {
      const scenes = (data as { scenes?: TcScene[] }).scenes
      tcScenes.value = Array.isArray(scenes) ? scenes : []
      const pointCount = countTestPoints(tcScenes.value)
      loading.value = false
      if (pointCount === 0) {
        tcPhase.value = 'idle'
        tcPhaseRailIndex.value = -1
        setMindmapMarkdown(MARKMAP_WELCOME)
        window.$ModalMessage?.error?.('未解析到可勾选的测试点，请重试或检查模型输出。')
        appendChat(
          'assistant',
          '场景与测试点生成未返回有效 JSON（需包含 scenes[].test_points[].point_name）。请重新上传或发送。',
        )
        return
      }
      refreshMindmapFromScenes(tcScenes.value)
      tcPhase.value = 'pick'
      selectedPointNames.value = []
      tcStatusText.value = '请勾选要采纳的测试点，确认后生成具体用例'
      appendChat(
        'assistant',
        `已生成 ${pointCount} 个测试点，请在下方列表中勾选要采纳的项，确认后生成具体用例。`,
      )
      nextTick(() => {
        chatScrollRef.value?.scrollTo({ top: 1e9, behavior: 'smooth' })
      })
    }
    if (type === 'scene-cases') {
      const sceneName = typeof data.sceneName === 'string' ? data.sceneName.trim() : ''
      const err = typeof data.error === 'string' ? data.error : ''
      const pointNames = Array.isArray(data.pointNames)
        ? data.pointNames.map((n) => String(n).trim()).filter(Boolean)
        : []
      const rawCases = Array.isArray(data.cases) ? data.cases : []
      const nextStatus = { ...caseGenStatus.value }
      const nextCases = { ...tcCasesByPoint.value }
      const nextErrors = { ...caseGenErrors.value }

      if (rawCases.length) {
        for (const rawCase of rawCases) {
          if (!rawCase || typeof rawCase !== 'object') {
            continue
          }
          const pn = typeof (rawCase as TcTestCase).point_name === 'string'
            ? (rawCase as TcTestCase).point_name.trim()
            : ''
          if (!pn) {
            continue
          }
          nextCases[pn] = rawCase as TcTestCase
          nextStatus[pn] = 'done'
        }
      }

      if (err) {
        const failedNames = pointNames.length
          ? pointNames
          : Object.keys(nextStatus).filter((pn) => nextStatus[pn] === 'generating')
        for (const pn of failedNames) {
          if (nextStatus[pn] !== 'done') {
            nextStatus[pn] = 'error'
            nextErrors[pn] = err
          }
        }
      } else if (!rawCases.length && pointNames.length) {
        for (const pn of pointNames) {
          if (nextStatus[pn] !== 'done') {
            nextStatus[pn] = 'error'
            nextErrors[pn] = sceneName ? `场景「${sceneName}」未返回用例` : '未返回用例'
          }
        }
      }

      tcCasesByPoint.value = nextCases
      caseGenStatus.value = nextStatus
      caseGenErrors.value = nextErrors
      refreshMindmapNow()
    }
  },
  onFinish() {
    loading.value = false
    if (tcPhase.value === 'gen_cases') {
      tcPhase.value = 'done'
      tcPhaseRailIndex.value = TC_PHASE_STEPS.length - 1
      const { done, total, failed } = caseProgressSummary.value
      tcStatusText.value = '测试用例已生成'
      refreshMindmapNow()
      if (total > 0) {
        const failHint = failed ? `，${failed} 条失败` : ''
        const failDetails = failed
          ? Object.entries(caseGenErrors.value)
            .filter(([pn]) => caseGenStatus.value[pn] === 'error')
            .map(([pn, msg]) => `「${pn}」：${msg}`)
            .join('；')
          : ''
        appendChat(
          'assistant',
          failDetails
            ? `已并行生成 ${done}/${total} 条测试用例${failHint}。失败详情：${failDetails}。其余用例见左侧脑图。`
            : `已并行生成 ${done}/${total} 条测试用例${failHint}，详情见左侧脑图各测试点下的子节点。`,
        )
      } else {
        appendChat('assistant', '测试用例生成已完成。')
      }
    }
    void loadDesignSessionHistory()
  },
  onError(msg) {
    appendChat('assistant', `请求失败：${msg}`)
    window.$ModalMessage?.error?.(msg)
    loading.value = false
    tcCasesByPoint.value = {}
    caseGenStatus.value = {}
  caseGenErrors.value = {}
    tcPhase.value = 'idle'
    tcPhaseRailIndex.value = -1
    if (tcScenes.value.length) {
      refreshMindmapFromScenes(tcScenes.value)
    } else {
      setMindmapMarkdown(MARKMAP_WELCOME)
    }
  },
})

const portalBusy = computed(() => loading.value || sse.isLoading.value)

const inputTextString = ref('')

const sendButtonDisabled = computed(
  () =>
    portalBusy.value
    || tcPhase.value === 'pick'
    || (tcPhase.value !== 'parse_done' && !inputTextString.value.trim()),
)

const composerInputStyle = {
  '--n-border': 'none',
  '--n-border-hover': 'none',
  '--n-border-focus': 'none',
  '--n-box-shadow-focus': 'none',
  '--n-border-radius': '15px',
  'font-family': `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif, 'Apple Color Emoji', 'Segoe UI Emoji'`,
  'font-size': '16px',
  'line-height': '1.5',
} as const

function buildFileDictFromSelectedRequirement(): Record<string, string> | null {
  const id = selectedReqId.value
  if (!id) {
    return null
  }
  const item = requirementList.value.find((r) => r.id === id)
  if (!item) {
    return null
  }
  const fileName = (item.kbFileName || item.title).trim()
  if (!fileName) {
    return null
  }
  if (item.markdown?.trim()) {
    return { [fileName]: item.markdown }
  }
  return { [fileName]: KB_FILE_DICT_REF }
}

async function handleSendChat() {
  if (portalBusy.value) {
    return
  }
  if (tcPhase.value === 'pick') {
    window.$ModalMessage?.warning?.('请先完成下方测试点勾选与确认生成')
    return
  }
  const text = inputTextString.value.trim()
  if (tcPhase.value === 'parse_done') {
    if (!text) {
      await startGenerateScenes()
      return
    }
  } else if (!text) {
    window.$ModalMessage?.warning?.('请输入内容后再发送')
    return
  } else if (sendButtonDisabled.value) {
    return
  }
  const fileDict = buildFileDictFromSelectedRequirement()
  if (!fileDict) {
    appendChat(
      'assistant',
      '发送前请先在左侧「测试设计」选中一条会话或本页已上传的需求；若还没有，请先上传 .docx / .md / .markdown。',
    )
    return
  }
  loading.value = true
  tcPhase.value = 'gen_scenes'
  tcPhaseRailIndex.value = -1
  tcScenes.value = []
  selectedPointNames.value = []
  tcCasesByPoint.value = {}
  caseGenStatus.value = {}
  caseGenErrors.value = {}
  setMindmapMarkdown('# 测试场景与测试点\n\n正在生成测试场景与测试点…')
  appendChat('user', text)
  inputTextString.value = ''
  try {
    let sessionId = tcSessionId.value
    if (!sessionId) {
      const session = await createSession({
        title: `测试用例-${text}`.slice(0, 80),
        extra: { qa_type: 'TEST_CASE_QA' },
      })
      sessionId = session.id
      tcSessionId.value = sessionId
      const localId = selectedReqId.value || sessionId
      const fdItem = requirementList.value.find((r) => r.id === (selectedReqId.value || sessionId))
      linkListItemToSession(
        localId,
        sessionId,
        session.title || `测试用例-${text}`.slice(0, 80),
        fdItem?.kbFileName || Object.keys(fileDict)[0] || '',
        fdItem?.kbCollection || targetCollection.value,
      )
    }
    await sse.sendMessage(sessionId, text, {
      qa_type: 'TEST_CASE_QA',
      file_dict: fileDict,
    })
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : '发送失败'
    appendChat('assistant', `发送失败：${msg}`)
    window.$ModalMessage?.error?.(msg)
    tcPhase.value = 'idle'
    tcStatusText.value = ''
    tcPhaseRailIndex.value = -1
    loading.value = false
  }
}

interface ScenePickGroup {
  sceneName: string
  sceneDescription: string
  points: { value: string, label: string }[]
}

/** 勾选面板：按场景分组（目录式层级） */
const scenePickGroups = computed<ScenePickGroup[]>(() => {
  const groups: ScenePickGroup[] = []
  for (const sc of tcScenes.value) {
    const sceneName = (sc.scene_name || '').trim()
    if (!sceneName) {
      continue
    }
    const points = (sc.test_points || [])
      .filter((tp) => tp.point_name?.trim())
      .map((tp) => {
        const level = (tp.point_level || '').trim()
        return {
          value: tp.point_name.trim(),
          label: level ? `${tp.point_name} [${level}]` : tp.point_name,
        }
      })
    if (!points.length) {
      continue
    }
    groups.push({
      sceneName,
      sceneDescription: (sc.scene_description || '').trim(),
      points,
    })
  }
  return groups
})

const pickPointCount = computed(() => countTestPoints(tcScenes.value))

const allPickPointValues = computed(() =>
  scenePickGroups.value.flatMap((group) => group.points.map((pt) => pt.value)),
)

function selectAllPickPoints() {
  selectedPointNames.value = [...new Set(allPickPointValues.value)]
}

function clearAllPickPoints() {
  selectedPointNames.value = []
}

function scenePointValues(group: ScenePickGroup): string[] {
  return group.points.map((pt) => pt.value)
}

function isSceneAllSelected(group: ScenePickGroup): boolean {
  const values = scenePointValues(group)
  return values.length > 0 && values.every((v) => selectedPointNames.value.includes(v))
}

function isSceneIndeterminate(group: ScenePickGroup): boolean {
  const values = scenePointValues(group)
  const selectedCount = values.filter((v) => selectedPointNames.value.includes(v)).length
  return selectedCount > 0 && selectedCount < values.length
}

function toggleSceneGroup(group: ScenePickGroup, checked: boolean) {
  const values = scenePointValues(group)
  if (checked) {
    selectedPointNames.value = [...new Set([...selectedPointNames.value, ...values])]
    return
  }
  const remove = new Set(values)
  selectedPointNames.value = selectedPointNames.value.filter((v) => !remove.has(v))
}

async function startGenerateScenes() {
  if (portalBusy.value) {
    return
  }
  if (tcPhase.value !== 'parse_done') {
    window.$ModalMessage?.warning?.('请先完成文档解析，或等待当前流程结束')
    return
  }
  const item = requirementList.value.find((r) => r.id === selectedReqId.value)
  const kbName = (item?.kbFileName || lastUploadedFileName.value).trim()
  if (!kbName) {
    window.$ModalMessage?.warning?.('请先上传需求文档')
    return
  }
  await runTestCaseAfterUpload(kbName)
}

async function runTestCaseAfterUpload(fileLabel: string) {
  const kbName = fileLabel.trim() || '需求文档'
  try {
    loading.value = true
    tcPhase.value = 'gen_scenes'
    tcPhaseRailIndex.value = -1
    tcStatusText.value = '正在生成测试场景与测试点…'
    tcScenes.value = []
    selectedPointNames.value = []
    tcCasesByPoint.value = {}
    caseGenStatus.value = {}
  caseGenErrors.value = {}
    setMindmapMarkdown('# 测试场景与测试点\n\n正在生成测试场景与测试点…')
    const session = await createSession({
      title: `测试用例-${kbName}`.slice(0, 80),
      extra: { qa_type: 'TEST_CASE_QA' },
    })
    tcSessionId.value = session.id
    linkListItemToSession(
      selectedReqId.value || session.id,
      session.id,
      session.title || `测试用例-${kbName}`.slice(0, 80),
      kbName,
      targetCollection.value,
    )
    appendChat('assistant', '文档已就绪，正在从知识库加载并生成测试场景与测试点…')
    await sse.sendMessage(
      tcSessionId.value,
      '请根据已上传的需求文档生成测试场景与测试点。',
      {
        qa_type: 'TEST_CASE_QA',
        file_dict: { [kbName]: KB_FILE_DICT_REF },
      },
    )
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : '测试用例生成失败'
    appendChat('assistant', `生成失败：${msg}`)
    window.$ModalMessage?.error?.(msg)
    tcPhase.value = 'idle'
    tcStatusText.value = ''
    tcPhaseRailIndex.value = -1
    loading.value = false
  }
}

async function submitConfirmedCases() {
  if (!tcSessionId.value) {
    return
  }
  if (!selectedPointNames.value.length) {
    appendChat('assistant', '请至少勾选一个测试点后再确认生成。')
    return
  }
  initCaseGenStatus(selectedPointNames.value, 'generating')
  tcCasesByPoint.value = {}
  caseGenErrors.value = {}
  refreshMindmapFromScenes(tcScenes.value, selectedPointNames.value)
  tcPhase.value = 'gen_cases'
  tcStatusText.value = '正在并行生成测试用例…'
  loading.value = true
  appendChat(
    'assistant',
    `已采纳 ${selectedPointNames.value.length} 个测试点，脑图已更新；正在并行生成具体用例（完成后同步至脑图子节点）…`,
  )
  try {
    await sse.resumeTestCase(tcSessionId.value, selectedPointNames.value)
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : '用例生成失败'
    appendChat('assistant', `生成失败：${msg}`)
    window.$ModalMessage?.error?.(msg)
    loading.value = false
    caseGenStatus.value = {}
  caseGenErrors.value = {}
  }
}

const targetCollection = ref(TEST_CASE_UPLOAD_COLLECTION)

async function loadKbCollections() {
  try {
    const list = await getCollections()
    const names = list.map((c) => c.name)
    if (!names.length) {
      window.$ModalMessage?.warning?.('知识库中暂无 Collection，请先在「知识库」页创建后再上传文档')
      return
    }
    if (!names.includes(targetCollection.value)) {
      targetCollection.value = names[0]
    }
  } catch {
    // 静默：上传时由接口错误提示
  }
}

async function handleTestCaseKbUpload(options: UploadCustomRequestOptions) {
  const { file, onFinish, onError } = options
  if (portalBusy.value || tcPhase.value === 'pick') {
    window.$ModalMessage?.warning?.('当前有任务进行中或处于测试点确认阶段，请稍后再上传')
    onError()
    return
  }
  const f = file.file
  if (!f) {
    onError()
    return
  }
  const name = f.name || ''
  if (name && !isTestRequirementFilename(name)) {
    window.$ModalMessage?.warning?.('请上传 Word（.docx）或 Markdown（.md / .markdown）需求文档')
    onError()
    return
  }
  if (name) {
    lastUploadedFileName.value = name
  }
  if (!targetCollection.value) {
    window.$ModalMessage?.error?.('未配置目标知识库 Collection')
    onError()
    return
  }

  loading.value = true
  tcPhase.value = 'parsing_doc'
  tcPhaseRailIndex.value = 0
  setMindmapMarkdown('# 测试场景与测试点\n\n正在解析文档…')
  appendChat('user', `已选择上传：${name || '未命名'}`)
  appendChat('assistant', '正在解析文档…')
  try {
    const res = await uploadDocument(targetCollection.value, f)
    const isDuplicate = res.message === '文档已存在，无需重复上传'
    const md = (res.extracted_markdown || '').trim()
    if (!md && !isDuplicate) {
      window.$ModalMessage?.error?.('文档解析结果为空，请检查文件或后端日志')
      tcPhase.value = 'idle'
      setMindmapMarkdown(MARKMAP_WELCOME)
      onError()
      return
    }
    const kbName = name || '需求文档'
    const rid = globalThis.crypto?.randomUUID?.() ?? String(Date.now())
    requirementList.value.unshift({
      id: rid,
      title: kbName,
      kbFileName: kbName,
      kbCollection: targetCollection.value,
    })
    selectedReqId.value = rid
    tcPhase.value = 'parse_done'
    tcPhaseRailIndex.value = 0
    loading.value = false
    setMindmapMarkdown(
      '# 测试场景与测试点\n\n文档已解析完成。请在对话区点击「开始生成测试场景与测试点」，或在下方输入补充说明后发送。',
    )
    if (isDuplicate) {
      window.$ModalMessage?.warning?.('文档已在知识库中，将使用已有内容；请手动开始生成')
      appendChat(
        'assistant',
        `文档已在知识库「${targetCollection.value}」中，无需重复入库。请点击下方「开始生成测试场景与测试点」，或在输入框补充说明后发送。`,
      )
    } else {
      window.$ModalMessage?.success?.('文档解析成功')
      appendChat(
        'assistant',
        `文档解析成功，已写入知识库「${targetCollection.value}」。请点击下方「开始生成测试场景与测试点」，或在输入框补充说明后发送。`,
      )
    }
    nextTick(() => {
      chatScrollRef.value?.scrollTo({ top: 1e9, behavior: 'smooth' })
    })
    onFinish()
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : '上传或解析失败'
    window.$ModalMessage?.error?.(msg)
    tcPhase.value = 'idle'
    tcPhaseRailIndex.value = -1
    setMindmapMarkdown(MARKMAP_WELCOME)
    appendChat('assistant', `解析失败：${msg}`)
    loading.value = false
    onError()
  }
}


function initMarkmap() {
  const svg = svgRef.value
  const host = markmapHostRef.value
  if (!svg || !host || mm.value) {
    return
  }

  mm.value = Markmap.create(svg, {
    autoFit: true,
    duration: 500,
    embedGlobalCSS: true,
    fitRatio: 1,
    lineWidth: (_node) => 1,
    maxInitialScale: 2,
    maxWidth: 800,
    nodeMinHeight: 20,
    paddingX: 20,
    pan: true,
    scrollForPan: true,
    spacingHorizontal: 30,
    spacingVertical: 20,
    toggleRecursively: true,
    zoom: true,
  })

  const { el } = Toolbar.create(mm.value)
  el.style.position = 'absolute'
  el.style.bottom = '1.5rem'
  el.style.right = '1rem'
  el.style.alignItems = 'center'
  el.style.display = 'flex'
  el.style.flexDirection = 'row'
  el.style.width = '120px'
  el.style.justifyContent = 'space-between'

  host.append(el)
  update()
}

onMounted(() => {
  void nextTick(() => {
    initMarkmap()
    void Promise.all([loadKbCollections(), loadDesignSessionHistory()]).finally(() => {
      appendChat(
        'assistant',
        '你好，我是测试用例助手。左侧为历史测试设计会话；上传新文档后将依次：解析 → 生成场景与测试点 → 勾选采纳 → 生成用例。',
      )
    })
  })
})

onBeforeUnmount(() => {
  if (mm.value && typeof mm.value.destroy === 'function') {
    mm.value.destroy()
  }
  mm.value = undefined
})
</script>

<template>
  <div
    class="tc-root"
    flex="~"
    h="98vh"
    mt="10px"
    mx="5px"
    rounded="10"
    overflow="hidden"
    bg-white
    b="1 solid #e8eaf3"
  >
    <!-- 左：测试设计会话列表 -->
    <aside
      flex="~ col shrink-0"
      min-h-0
      h-full
      w="272px"
      min-w-0
      b-r="1 solid #e8eaf3"
      bg="#fafbff"
    >
      <div class="tc-panel-title">
        测试设计
      </div>
      <n-spin :show="sessionsListLoading" class="flex-1 min-h-0" content-class="h-full">
        <n-scrollbar class="h-full min-h-0">
          <div v-if="!requirementList.length && !sessionsListLoading" class="tc-empty-hint">
            暂无历史记录。上传 .docx / .md / .markdown 开始新的测试设计；完成后会出现在此列表。
          </div>
          <div
            v-for="item in requirementList"
            :key="item.id"
            class="tc-req-item"
            :class="{ 'tc-req-item--active': selectedReqId === item.id }"
            @click="selectRequirement(item)"
          >
            {{ item.title }}
          </div>
        </n-scrollbar>
      </n-spin>
    </aside>

    <!-- 中：脑图 -->
    <main
      flex="1 ~ col"
      min-w-0
      min-h-0
      bg="#f6f7fb"
      relative
    >
      <div
        v-if="canExportCases"
        class="tc-mindmap-toolbar"
      >
        <n-button
          type="primary"
          color="#5c7cfa"
          :loading="exportLoading"
          :disabled="portalBusy"
          @click="handleExportCases"
        >
          <template #icon>
            <span class="i-mingcute:download-2-line text-16" />
          </template>
          导出 Markdown
        </n-button>
        <span class="tc-mindmap-toolbar__hint">下载本次已生成的测试用例报告</span>
      </div>
      <div ref="markmapHostRef" class="tc-markmap-host relative flex-1 min-h-0 w-full min-w-0">
        <n-spin
          class="absolute inset-0 z-1"
          content-class="h-full w-full"
          :show="portalBusy"
          :rotate="false"
          :style="{ '--n-opacity-spinning': '0' }"
        >
          <div class="tc-markmap-wrap relative h-full w-full min-h-0">
            <svg ref="svgRef" class="h-full w-full" />
          </div>
        </n-spin>
      </div>
    </main>

    <!-- 右：对话与交互（样式对齐主对话页 chat.vue） -->
    <aside
      class="tc-right-aside"
      flex="~ col shrink-0"
      min-h-0
      h-full
      w="400px"
      min-w-0
      b-l="1 solid #e8eaf3"
    >
      <div class="tc-panel-title">
        测试助手
      </div>
      <div
        v-if="tcPhaseRailIndex >= 0 || tcPhase !== 'idle'"
        class="tc-phase-rail mx-16px mb-8px mt-4px flex flex-wrap items-center gap-6px text-11px lh-tight select-none md:text-12px"
      >
        <template v-for="(s, i) in TC_PHASE_STEPS" :key="s.id">
          <span
            class="rounded-full px-8px py-2px transition-colors"
            :class="tcPhaseChipClass(i)"
          >{{ s.label }}</span>
          <span v-if="i < TC_PHASE_STEPS.length - 1" class="text-#d2d9e9">→</span>
        </template>
      </div>
      <n-scrollbar ref="chatScrollRef" class="tc-chat-scroll flex-1 min-h-0" trigger="none">
        <div class="tc-chat-pad">
          <div
            v-for="m in chatMessages"
            :key="m.id"
            class="tc-chat-row"
          >
            <div v-if="m.role === 'user'" class="tc-user-msg">
              <n-tag
                size="large"
                :bordered="false"
                :round="true"
                class="tc-user-tag"
                :color="{
                  color: '#e0dfff',
                  borderColor: '#e0dfff',
                }"
              >
                <template #avatar>
                  <div class="size-25 i-my-svg:user-avatar"></div>
                </template>
                {{ m.text }}
              </n-tag>
            </div>
            <div v-else class="tc-assistant-msg">
              <div class="tc-assistant-card">
                {{ m.text }}
              </div>
            </div>
          </div>
          <div
            v-if="tcPhase === 'gen_cases' && sceneCaseProgressItems.length"
            class="tc-chat-row"
          >
            <div class="tc-assistant-msg">
              <div class="tc-assistant-card tc-assistant-card--streaming">
                <p class="tc-case-progress-title">
                  并行生成进度（{{ caseProgressSummary.done }}/{{ caseProgressSummary.total }} 用例，
                  {{ caseProgressSummary.sceneCount }} 个场景）
                  <span v-if="caseProgressSummary.generating">
                    · {{ caseProgressSummary.generating }} 个场景进行中
                  </span>
                </p>
                <ul class="tc-case-progress-list">
                  <li
                    v-for="item in sceneCaseProgressItems"
                    :key="item.sceneName"
                    class="tc-case-progress-item"
                    :class="`tc-case-progress-item--${item.status}`"
                  >
                    <span class="tc-case-progress-dot" />
                    <span class="tc-case-progress-name">{{ item.sceneName }}</span>
                    <span class="tc-case-progress-state">{{ item.label }}</span>
                  </li>
                </ul>
              </div>
            </div>
          </div>
          <div
            v-if="tcPhase === 'parse_done'"
            class="tc-chat-row"
          >
            <div class="tc-assistant-msg">
              <div class="tc-assistant-card tc-assistant-card--action">
                <p class="tc-action-lead">
                  文档已就绪，请确认后开始生成测试场景与测试点。
                </p>
                <n-button
                  type="primary"
                  color="#5c7cfa"
                  strong
                  block
                  class="rounded-20"
                  :disabled="portalBusy"
                  @click="startGenerateScenes"
                >
                  开始生成测试场景与测试点
                </n-button>
              </div>
            </div>
          </div>
          <div
            v-if="tcPhase === 'pick'"
            class="tc-chat-row"
          >
            <div class="tc-assistant-msg">
              <div class="tc-assistant-card tc-assistant-card--pick">
                <p class="tc-pick-lead">
                  已生成 {{ pickPointCount }} 个测试点，请多选勾选要采纳的项
                </p>
                <div class="tc-pick-toolbar">
                  <span class="tc-pick-count">
                    已选 {{ selectedPointNames.length }} / {{ pickPointCount }}
                  </span>
                  <div class="tc-pick-actions">
                    <n-button
                      size="tiny"
                      quaternary
                      type="primary"
                      :disabled="!pickPointCount"
                      @click="selectAllPickPoints"
                    >
                      全选
                    </n-button>
                    <n-button
                      size="tiny"
                      quaternary
                      :disabled="!selectedPointNames.length"
                      @click="clearAllPickPoints"
                    >
                      全不选
                    </n-button>
                  </div>
                </div>
                <div class="tc-scene-tree">
                  <section
                    v-for="group in scenePickGroups"
                    :key="group.sceneName"
                    class="tc-scene-group"
                  >
                    <n-checkbox
                      class="tc-scene-group__title-check"
                      :checked="isSceneAllSelected(group)"
                      :indeterminate="isSceneIndeterminate(group)"
                      @update:checked="(checked: boolean) => toggleSceneGroup(group, checked)"
                    >
                      {{ group.sceneName }}
                    </n-checkbox>
                    <p
                      v-if="group.sceneDescription"
                      class="tc-scene-group__desc"
                    >
                      {{ group.sceneDescription }}
                    </p>
                    <n-checkbox-group
                      v-model:value="selectedPointNames"
                      class="tc-scene-group__points-wrap"
                    >
                      <div class="tc-scene-group__points">
                        <n-checkbox
                          v-for="pt in group.points"
                          :key="`${group.sceneName}::${pt.value}`"
                          :value="pt.value"
                          :label="pt.label"
                          class="tc-scene-point"
                        />
                      </div>
                    </n-checkbox-group>
                  </section>
                </div>
                <n-popconfirm
                  positive-text="确认生成"
                  negative-text="取消"
                  @positive-click="submitConfirmedCases"
                >
                  <template #trigger>
                    <n-button
                      type="primary"
                      color="#5c7cfa"
                      strong
                      block
                      class="mt-12 rounded-20"
                      :disabled="!selectedPointNames.length"
                    >
                      确认并生成测试用例
                    </n-button>
                  </template>
                  将基于已勾选的 {{ selectedPointNames.length }} 个测试点生成具体用例，是否继续？
                </n-popconfirm>
              </div>
            </div>
          </div>
        </div>
      </n-scrollbar>

      <div class="tc-input-bar">
        <n-space vertical class="w-full min-w-0">
          <div
            class="tc-composer-box relative b b-solid b-primary bg-white rounded-10px p-12"
          >
            <n-input
              v-model:value="inputTextString"
              type="textarea"
              class="textarea-resize-none w-full border-none text-15"
              :style="composerInputStyle"
              :placeholder="tcPhase === 'parse_done' ? '可补充说明后发送，或点击上方按钮开始生成' : '点击左侧图标开始上传'"
              :disabled="portalBusy || tcPhase === 'pick' || tcPhase === 'parsing_doc'"
              :autosize="{ minRows: 1, maxRows: 10 }"
            >
              <template #prefix>
                <n-upload
                  :show-file-list="false"
                  accept=".docx,.md,.markdown"
                  :custom-request="handleTestCaseKbUpload"
                  :disabled="portalBusy || tcPhase === 'pick' || tcPhase === 'parsing_doc'"
                >
                  <div
                    flex="~ items-center justify-center"
                    class="rounded-50% p-7 transition-all-300 bg-primary/1 hover:bg-primary/5"
                    b="~ solid primary/20"
                  >
                    <div class="text-20 i-uil:upload cursor-pointer"></div>
                  </div>
                </n-upload>
              </template>
            </n-input>
            <n-float-button
              position="absolute"
              type="primary"
              color
              bottom="10px"
              right="8px"
              class="text-20"
              :disabled="sendButtonDisabled"
              @click.stop="handleSendChat"
            >
              <div
                class="flex items-center justify-center i-mingcute:send-fill text-20 cursor-pointer transition-colors duration-300 hover:c-primary/80"
              ></div>
            </n-float-button>
          </div>
        </n-space>
      </div>
    </aside>
  </div>
</template>

<style lang="scss" scoped>
.tc-root {
  min-height: 0;
}

.tc-panel-title {
  flex-shrink: 0;
  padding: 14px 16px;
  font-size: 15px;
  font-weight: 600;
  color: #26244c;
  border-bottom: 1px solid #e8eaf3;
  background: #fff;
}

.tc-mindmap-toolbar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  background: #fff;
  border-bottom: 1px solid #e8eaf3;
  box-shadow: 0 1px 0 rgb(92 124 250 / 6%);
}

.tc-mindmap-toolbar__hint {
  font-size: 12px;
  color: #78849e;
}

.tc-right-aside {
  background: #f6f7fb;
}

.tc-chat-scroll {
  background: #f6f7fb;
}

.tc-chat-pad {
  padding: 16px 8px 20px;
  background: #f6f7fb;
  min-height: 100%;
}

.tc-chat-row {
  margin-bottom: 16px;

  &:last-child {
    margin-bottom: 4px;
  }
}

.tc-user-msg {
  display: flex;
  justify-content: flex-end;
  width: 100%;
  padding: 0 4px;
}

.tc-user-tag {
  max-width: 90%;
  font-size: 14px;
  font-weight: 400;
  font-family:
    -apple-system,
    blinkmacsystemfont,
    'Segoe UI',
    roboto,
    'Helvetica Neue',
    arial,
    'Noto Sans',
    sans-serif,
    'Apple Color Emoji',
    'Segoe UI Emoji';
  color: #26244c;

  :deep(.n-tag__content) {
    max-width: 300px;
    text-align: left;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
  }
}

.tc-assistant-msg {
  display: flex;
  justify-content: flex-start;
  width: 100%;
  padding: 0 2px;
}

.tc-assistant-card--streaming {
  max-height: 40vh;
  overflow: auto;
}

.tc-case-progress-title {
  margin: 0 0 10px;
  font-size: 13px;
  font-weight: 600;
  color: #363457;
}

.tc-case-progress-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tc-case-progress-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  line-height: 1.4;
  color: #5c6370;
}

.tc-case-progress-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #c5cad6;
  flex-shrink: 0;
}

.tc-case-progress-item--generating .tc-case-progress-dot {
  background: #5c7cfa;
  animation: tc-pulse 1s ease-in-out infinite;
}

.tc-case-progress-item--done .tc-case-progress-dot {
  background: #51cf66;
}

.tc-case-progress-item--error .tc-case-progress-dot {
  background: #ff6b6b;
}

.tc-case-progress-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tc-case-progress-state {
  flex-shrink: 0;
  color: #78849e;
}

@keyframes tc-pulse {
  0%,
  100% {
    opacity: 1;
  }

  50% {
    opacity: 0.45;
  }
}

.tc-assistant-card--pick {
  max-height: 48vh;
  display: flex;
  flex-direction: column;
}

.tc-assistant-card--action {
  display: flex;
  flex-direction: column;
}

.tc-action-lead {
  margin: 0 0 12px;
  font-size: 14px;
  line-height: 1.5;
  color: #363457;
}

.tc-pick-lead {
  margin: 0 0 8px;
  font-size: 14px;
  line-height: 1.5;
  color: #363457;
}

.tc-pick-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
  padding-bottom: 10px;
  border-bottom: 1px solid #eef0f6;
}

.tc-pick-count {
  font-size: 12px;
  color: #78849e;
}

.tc-pick-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.tc-scene-tree {
  flex: 1;
  min-height: 0;
  max-height: 32vh;
  overflow: auto;
  margin-bottom: 4px;
}

.tc-scene-group {
  margin-bottom: 14px;
  padding-bottom: 12px;
  border-bottom: 1px solid #eef0f6;

  &:last-child {
    margin-bottom: 0;
    padding-bottom: 0;
    border-bottom: none;
  }
}

.tc-scene-group__points-wrap {
  width: 100%;
}

.tc-scene-group__title-check {
  margin-bottom: 4px;

  :deep(.n-checkbox__label) {
    font-size: 14px;
    font-weight: 600;
    color: #26244c;
    line-height: 1.4;
  }
}

.tc-scene-group__desc {
  margin: 0 0 8px;
  padding-left: 0;
  font-size: 12px;
  color: #78849e;
  line-height: 1.45;
}

.tc-scene-group__points {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding-left: 14px;
  border-left: 2px solid #e0e4f4;
}

.tc-scene-point {
  margin: 0;
}

.tc-assistant-card {
  width: 92%;
  margin-left: 4%;
  margin-right: 4%;
  background: #fff;
  border: 1px solid #e8eaf0;
  border-radius: 16px;
  box-shadow: 0 1px 2px rgb(0 0 0 / 4%);
  padding: 12px 14px;
  font-size: 15px;
  line-height: 1.5;
  color: #363457;
  font-family:
    -apple-system,
    blinkmacsystemfont,
    'Segoe UI',
    roboto,
    'Helvetica Neue',
    arial,
    'Noto Sans',
    sans-serif;
  white-space: pre-wrap;
  word-break: break-word;
}

.tc-input-bar {
  flex-shrink: 0;
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  padding: 12px 12px 16px;
  background: #f6f7fb;
}

.tc-composer-box {
  :deep(.n-input) {
    background: transparent;
  }

  :deep(.n-input-wrapper) {
    padding: 0 !important;
    border: none !important;
    box-shadow: none !important;
    background: transparent !important;
  }

  :deep(.n-input__border),
  :deep(.n-input__state-border) {
    display: none !important;
  }

  :deep(.n-input__textarea-el),
  :deep(.n-input__input-el) {
    border: none !important;
    box-shadow: none !important;
  }
}

.tc-markmap-host {
  min-height: 0;
}

.tc-markmap-wrap {
  background: #f6f7fb;
}

.tc-empty-hint {
  padding: 16px;
  font-size: 13px;
  color: #888;
  line-height: 1.5;
}

.tc-req-item {
  margin: 0 10px 6px;
  padding: 10px 12px;
  font-size: 13px;
  color: #363457;
  border-radius: 8px;
  cursor: pointer;
  border: 1px solid transparent;
  transition:
    background 0.15s,
    border-color 0.15s;

  &:hover {
    background: #eef0ff;
  }

  &--active {
    background: #e8ecff;
    border-color: #c5cdff;
    font-weight: 600;
  }
}

:deep(.mm-toolbar-brand) {
  display: none !important;
}

:deep(.mm-toolbar-item:hover) {
  background-color: #f5f5f5;
}

:deep(.mm-toolbar-item:active) {
  background-color: #e0e0e0;
}
</style>
