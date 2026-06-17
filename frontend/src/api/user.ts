import {
  applyRefreshToken,
  authFetch,
  getAuthHeaders,
} from '@/utils/authHttp'

/** 用户登录（表单登录，不经 Bearer 鉴权） */
export async function login(username: string, password: string) {
  const url = new URL(`${location.origin}/api/user/login`)
  const formData = new URLSearchParams()
  formData.append('username', username)
  formData.append('password', password)

  const res = await fetch(url, {
    mode: 'cors',
    credentials: 'include',
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: formData,
  })
  applyRefreshToken(res)
  return res
}

/** 用户注册 */
export async function register(username: string, password: string, mobile?: string) {
  const url = new URL(`${location.origin}/api/user/register`)
  const res = await fetch(url, {
    mode: 'cors',
    credentials: 'include',
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      username,
      password,
      mobile: mobile || null,
    }),
  })
  return res
}

/** 分页查询用户对话历史（侧边栏 / 管理弹窗） */
export async function query_user_qa_record(
  page: number,
  limit: number,
  search_text: string | null,
  chat_id: string | null,
) {
  const url = new URL(`${location.origin}/api/user/query_user_record`)
  const req = new Request(url, {
    mode: 'cors',
    credentials: 'include',
    method: 'POST',
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

/** 批量删除对话历史 */
export async function delete_user_record(ids: string[]) {
  const url = new URL(`${location.origin}/api/chat/sessions/batch-delete`)
  const req = new Request(url, {
    mode: 'cors',
    credentials: 'include',
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeaders(),
    },
    body: JSON.stringify({ session_ids: ids }),
  })
  return authFetch(req)
}
