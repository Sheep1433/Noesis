import {
  applyRefreshToken,
  authFetch,
  getAuthHeaders,
} from '@/utils/authHttp'

/**
 * 用户登录
 * @param username
 * @param password
 * @returns
 */
export async function login(username, password) {
  const url = new URL(`${location.origin}/api/user/login`)

  const formData = new URLSearchParams()
  formData.append('username', username)
  formData.append('password', password)

  const req = new Request(url, {
    mode: 'cors',
    credentials: 'include',
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: formData,
  })

  const res = await fetch(req)
  applyRefreshToken(res)
  return res
}

/**
 * 查询用户对话历史
 */
export async function query_user_qa_record(page, limit, search_text, chat_id) {
  const url = new URL(`${location.origin}/api/user/query_user_record`)
  const req = new Request(url, {
    mode: 'cors',
    credentials: 'include',
    method: 'post',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeaders(),
    },
    body: JSON.stringify({
      page,
      limit,
      search_text,
      chat_id,
    }),
  })
  return authFetch(req)
}

/**
 * 获取会话消息历史
 */
export async function get_session_messages(session_id: string) {
  const url = new URL(`${location.origin}/api/chat/sessions/${session_id}/messages`)
  const req = new Request(url, {
    mode: 'cors',
    credentials: 'include',
    method: 'get',
    headers: getAuthHeaders(),
  })
  return authFetch(req)
}

/**
 * 获取会话列表
 */
export async function get_chat_sessions() {
  const url = new URL(`${location.origin}/api/chat/sessions`)
  const req = new Request(url, {
    mode: 'cors',
    credentials: 'include',
    method: 'get',
    headers: getAuthHeaders(),
  })
  return authFetch(req)
}

/**
 * 删除对话历史记录
 */
export async function delete_user_record(ids) {
  const url = new URL(`${location.origin}/api/chat/sessions/batch-delete`)
  const req = new Request(url, {
    mode: 'cors',
    credentials: 'include',
    method: 'post',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeaders(),
    },
    body: JSON.stringify({ session_ids: ids }),
  })
  return authFetch(req)
}

/**
 * 用户反馈（暂时禁用）
 */
export async function fead_back(session_id, rating) {
  return { ok: true }
}
