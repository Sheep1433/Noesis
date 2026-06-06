import * as GlobalAPI from '@/api'
import { appendStreamFailureNotice, normalizeApiContent, syncLegacyFieldsFromParts } from '@/views/chat/messageParts'

const userStore = useUserStore()
const router = useRouter()

interface TableItem {
  uuid: string
  key: string
  chat_id: string
  qa_type: string
}

/**
 * 从消息 content 字段提取文本内容
 * content 可能是字符串、{parts: []} 对象或 {text: string} 对象
 * 对于 tool 类型消息，提取 tool 名和 output 内容
 */
function extractContent(content: any, role?: string): string {
  if (typeof content === 'string') {
    // 尝试解析 JSON 字符串（可能是 {"parts": [...]} 格式）
    try {
      content = JSON.parse(content)
    } catch {
      // 非 JSON 字符串，原样返回
      return content
    }
  }

  if (content === null || content === undefined) {
    return ''
  }

  // 递归提取 parts 数组中的文本
  if (Array.isArray(content.parts)) {
    return content.parts.map((part: any) => extractContent(part, role)).join('')
  }

  // 处理嵌套对象：{text: string} 或 {parts: []}
  if (typeof content === 'object') {
    // 先处理 tool 类型消息
    if (content.type === 'tool') {
      return '' // tool 调用不拼入 content，由 tool_calls / parts 单独展示
    }
    // 只提取 text 类型内容，不显示 reasoning 思考内容
    if (content.type === 'text') {
      return typeof content.content === 'string' ? content.content : ''
    }
    if (content.text && typeof content.text === 'string') {
      return content.text
    }
    if (Array.isArray(content.parts)) {
      return content.parts.map((part: any) => extractContent(part, role)).join('')
    }
    // 其他对象，尝试提取所有字符串属性（排除 tool 类型对象）
    const texts: string[] = []
    for (const key of Object.keys(content)) {
      const val = content[key]
      if (typeof val === 'string') {
        texts.push(val)
      } else if (typeof val === 'object' && val !== null) {
        // 递归时排除 tool 类型，避免将 output 拼入 content
        if (val.type !== 'tool') {
          texts.push(extractContent(val, role))
        }
      }
    }
    return texts.join('')
  }

  // 无法解析，返回空字符串（不显示 [object Object]）
  return ''
}

/**
 * 提取工具调用
 * tool 存储在 content.parts 中，类型为 'tool'
 */
function extractToolCalls(content: any): any[] | undefined {
  if (typeof content === 'string') {
    try {
      content = JSON.parse(content)
    } catch {
      return undefined
    }
  }
  if (!content || typeof content !== 'object') {
    return undefined
  }
  const parts = content.parts
  if (!Array.isArray(parts)) {
    return undefined
  }
  const toolParts = parts.filter((part: any) => part.type === 'tool')
  if (toolParts.length === 0) {
    return undefined
  }
  return toolParts.map((p: any) => ({
    name: p.name || p.tool || '',
    arguments: p.arguments || p.input || {},
    // tool output 已包含在 content.text 中，此处不重复提取 result
    result: '',
  }))
}

// 请求接口查询对话历史记录
export const fetchConversationHistory = async function fetchConversationHistory(
  isInit: Ref<boolean>,
  conversationItems: Ref<
    Array<{
      chat_id: string
      qa_type: string
      question: string
      file_key: {
        source_file_key: string
        parse_file_key: string
        file_size: string
      }[]
      role: 'user' | 'assistant'
      reader: ReadableStreamDefaultReader | null
      reasoning?: string
      msg_metadata?: any
      parent_id?: string | null
      message_id?: string
    }>
  >,
  tableData: Ref<TableItem[]>,
  currentRenderIndex: Ref<number>,
  row,
  searchText: string,
) {
  try {
    const res = await GlobalAPI.query_user_qa_record(1, 999999, searchText, row?.chat_id)
    if (res.status === 401) {
      userStore.logout()
      setTimeout(() => {
        router.replace('/login')
      }, 500)
    } else if (res.ok) {
      const data = await res.json()
      if (data && Array.isArray(data.data?.records)) {
        const records = data.data.records

        // 初始化左侧会话列表数据
        if (isInit.value) {
          tableData.value = records.map((chat: any) => ({
            uuid: chat.session_id,
            key: chat.title?.trim() || '新对话',
            chat_id: chat.session_id,
            qa_type: chat.qa_type || 'COMMON_QA',
          }))
        }

        // 用户点击了某个会话，加载该会话的完整消息历史
        if (row?.chat_id && !isInit.value) {
          await loadSessionMessages(row.chat_id, conversationItems, currentRenderIndex)
        } else {
          // 初始化时或搜索时不加载 conversationItems
          conversationItems.value = []
          currentRenderIndex.value = 0
        }
      }
    } else {
      // debug: request failed
    }
  } catch (error) {
    // debug: error occurred
  }
}

/**
 * 加载指定会话的完整消息历史
 */
async function loadSessionMessages(
  sessionId: string,
  conversationItems: Ref<any[]>,
  currentRenderIndex: Ref<number>,
) {
  try {
    const res = await GlobalAPI.get_session_messages(sessionId)
    if (res.ok) {
      const data = await res.json()
      if (data?.data?.messages) {
        const messages = data.data.messages

        // 将消息历史转换为 conversationItems 格式
        let lastUserQaType = 'COMMON_QA'
        const items = messages.map((msg: any, index: number) => {
          // 根据 role 决定是用户消息还是助手消息
          if (msg.role === 'user') {
            const userQaType = msg.extra?.qa_type || msg.msg_metadata?.qa_type || 'COMMON_QA'
            lastUserQaType = userQaType
            // 将 file_dict 转换为 file_key 格式
            const fileDict = msg.extra?.file_dict || msg.msg_metadata?.file_dict || {}
            const fileKey = Object.values(fileDict).map((value: any) => ({
              source_file_key: value,
              parse_file_key: value,
              file_size: '',
            }))
            return {
              uuid: msg.id || `user-${index}`,
              chat_id: sessionId,
              qa_type: userQaType,
              question: extractContent(msg.content, msg.role),
              file_key: fileKey,
              role: 'user' as const,
              reader: null,
              parent_id: msg.parent_id,
              message_id: msg.id,
            }
          } else {
            // assistant 消息（tool 调用结果已合并到 content.parts 中）
            const assistantQaType = msg.extra?.qa_type || lastUserQaType || 'COMMON_QA'
            let messageContent = normalizeApiContent(msg.content)
            const errMsg = typeof msg.extra?.error_message === 'string' ? msg.extra.error_message : ''
            const finishReason = msg.extra?.finish_reason
            const partsText = messageContent.parts.map((p) => String((p as { content?: string }).content ?? '')).join('\n')
            const noticeMissing = finishReason === 'error'
              && errMsg
              && !partsText.includes('已达到最大处理步数')
              && !partsText.includes('后续内容未能继续生成')
              && !partsText.includes('后续内容未能生成')
            if (noticeMissing) {
              const parts = appendStreamFailureNotice(messageContent.parts, errMsg)
              messageContent = { version: 1, parts }
            }
            const { content, reasoning } = syncLegacyFieldsFromParts(messageContent.parts)
            return {
              uuid: msg.id || `assistant-${index}`,
              chat_id: sessionId,
              qa_type: assistantQaType,
              question: '',
              content,
              file_key: [],
              role: 'assistant' as const,
              reader: null,
              messageContent,
              tool_calls: extractToolCalls(msg.content),
              reasoning,
              msg_metadata: msg.extra,
              parent_id: msg.parent_id,
              message_id: msg.id,
            }
          }
        })

        conversationItems.value.splice(0, conversationItems.value.length, ...items)
        currentRenderIndex.value = items.length > 0 ? items.length - 1 : 0
      }
    } else {
      // debug: failed to load session messages
    }
  } catch (error) {
    // debug: error loading session messages
  }
}
