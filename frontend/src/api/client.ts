/**
 * LangGraph Client API
 * 基于 @langchain/langgraph-sdk 封装
 */
import { Client as LangGraphClient } from '@langchain/langgraph-sdk/client'

// LangGraph Server 地址
const LANGGRAPH_BASE_URL = import.meta.env.VITE_LANGGRAPH_API_URL || '/api'

let _client: LangGraphClient | null = null

/**
 * 获取 LangGraph Client 单例
 */
export function getLangGraphClient(): LangGraphClient {
  if (!_client) {
    _client = new LangGraphClient({
      apiUrl: LANGGRAPH_BASE_URL,
    })
  }
  return _client
}

/**
 * 搜索会话列表
 */
export async function searchThreads(params?: {
  limit?: number
  offset?: number
  sortBy?: 'updated_at' | 'created_at'
  sortOrder?: 'asc' | 'desc'
}) {
  const client = getLangGraphClient()
  return client.threads.search({
    limit: params?.limit ?? 50,
    offset: params?.offset ?? 0,
    sortBy: params?.sortBy ?? 'updated_at',
    sortOrder: params?.sortOrder ?? 'desc',
    select: ['thread_id', 'updated_at', 'values'],
  })
}

/**
 * 创建新会话
 */
export async function createThread(values?: Record<string, unknown>) {
  const client = getLangGraphClient()
  return client.threads.create({
    values: values ?? {},
  })
}

/**
 * 获取会话详情
 */
export async function getThread(threadId: string) {
  const client = getLangGraphClient()
  return client.threads.get(threadId)
}

/**
 * 删除会话
 */
export async function deleteThread(threadId: string) {
  const client = getLangGraphClient()
  return client.threads.delete(threadId)
}

/**
 * 更新会话状态
 */
export async function updateThreadState(
  threadId: string,
  values: Record<string, unknown>,
) {
  const client = getLangGraphClient()
  return client.threads.updateState(threadId, { values })
}

/**
 * 发送消息并流式响应
 */
export async function* streamMessage(
  threadId: string | null,
  assistantId: string,
  input: {
    messages: Array<{
      type: 'human' | 'ai' | 'system' | 'tool'
      content: string | Array<{ type: 'text', text: string }>
      additional_kwargs?: Record<string, unknown>
    }>
  },
  config?: {
    recursion_limit?: number
    configurable?: Record<string, unknown>
  },
) {
  const client = getLangGraphClient()

  // 如果没有 threadId，先创建会话
  let actualThreadId = threadId
  if (!actualThreadId) {
    const thread = await client.threads.create()
    actualThreadId = thread.thread_id
  }

  const run = client.runs.stream(
    actualThreadId,
    assistantId,
    {
      input,
      streamMode: 'values',
      config: {
        recursion_limit: config?.recursion_limit ?? 200,
        ...config?.configurable,
      },
    },
  )

  for await (const chunk of run) {
    yield chunk
  }
}
