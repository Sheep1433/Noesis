/**
 * 用户登录
 * @param username
 * @param password
 * @returns
 */
export async function login(username, password) {
  const url = new URL(`${location.origin}/api/user/login`)

  // 构造表单数据
  const formData = new URLSearchParams()
  formData.append('username', username)
  formData.append('password', password)

  const req = new Request(url, {
    mode: 'cors',
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded', // 改为表单类型
    },
    body: formData, // 直接传入 URLSearchParams 对象
  })

  return fetch(req)
}

/**
 * 查询用户对话历史
 * @param page
 * @param limit
 * @returns
 */
export async function query_user_qa_record(page, limit, search_text, chat_id) {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  const url = new URL(`${location.origin}/api/user/query_user_record`)
  const req = new Request(url, {
    mode: 'cors',
    method: 'post',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`, // 添加 token 到头部
    },
    body: JSON.stringify({
      page,
      limit,
      search_text,
      chat_id,
    }),
  })
  return fetch(req)
}

/**
 * 获取会话消息历史
 * @param session_id 会话ID
 * @returns 消息列表
 */
export async function get_session_messages(session_id: string) {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  const url = new URL(`${location.origin}/api/chat/sessions/${session_id}/messages`)
  const req = new Request(url, {
    mode: 'cors',
    method: 'get',
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })
  return fetch(req)
}

/**
 * 获取会话列表
 * @returns 会话列表
 */
export async function get_chat_sessions() {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  const url = new URL(`${location.origin}/api/chat/sessions`)
  const req = new Request(url, {
    mode: 'cors',
    method: 'get',
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })
  return fetch(req)
}

/**
 * 删除对话历史记录
 * @param page
 * @param limit
 * @returns
 */
export async function delete_user_record(ids) {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  const url = new URL(`${location.origin}/api/chat/sessions/batch-delete`)
  const req = new Request(url, {
    mode: 'cors',
    method: 'post',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`, // 添加 token 到头部
    },
    body: JSON.stringify({ session_ids: ids }),
  })
  return fetch(req)
}

/**
 * 用户反馈（暂时禁用）
 */
export async function fead_back(session_id, rating) {
  return { ok: true }
}
