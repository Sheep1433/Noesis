import Router from '@/router'
import { LOGIN_ROUTE_PATH } from '@/router/routes'
import { useUserStore } from '@/store/business/userStore'

export const REFRESH_TOKEN_HEADER = 'X-Refresh-Token'
export const STOP_TOKEN_HEADER = 'X-Stop-Token'

export class UnauthorizedError extends Error {
  constructor(message = '登录已过期，请重新登录') {
    super(message)
    this.name = 'UnauthorizedError'
  }
}

let redirectingToLogin = false

export function isUnauthorizedError(err: unknown): boolean {
  return err instanceof UnauthorizedError
}

export function applyRefreshToken(response: Response): void {
  const token = response.headers.get(REFRESH_TOKEN_HEADER)
  if (token) {
    useUserStore().setToken(token)
  }
}

export function handleUnauthorized(message?: string): never {
  const userStore = useUserStore()
  userStore.logoutLocal()
  if (!redirectingToLogin) {
    redirectingToLogin = true
    const msg = message ?? '登录已过期，请重新登录'
    window.$ModalMessage?.error(msg)
    void Router.replace(LOGIN_ROUTE_PATH).finally(() => {
      redirectingToLogin = false
    })
  }
  throw new UnauthorizedError(message)
}

export function getAuthHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = useUserStore().getUserToken()
  return {
    Authorization: `Bearer ${token ?? ''}`,
    ...extra,
  }
}

function mergeAuthHeaders(input: RequestInfo | URL, init?: RequestInit): HeadersInit {
  const headers = input instanceof Request ? new Headers(input.headers) : new Headers()
  for (const [key, value] of Object.entries(getAuthHeaders())) {
    headers.set(key, value)
  }
  if (init?.headers) {
    new Headers(init.headers).forEach((value, key) => headers.set(key, value))
  }
  return headers
}

export async function authFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const res = await fetch(input, {
    ...init,
    credentials: 'include',
    headers: mergeAuthHeaders(input, init),
  })
  applyRefreshToken(res)
  if (res.status === 401) {
    let msg: string | undefined
    try {
      const json = await res.clone().json() as { msg?: string, message?: string }
      msg = json.msg ?? json.message
    } catch {
      // ignore parse errors
    }
    handleUnauthorized(msg)
  }
  return res
}

export async function parseAuthJson<T>(res: Response): Promise<T> {
  const json = await res.json() as { code?: number, msg?: string, message?: string, data?: T }
  if (json.code === 401) {
    handleUnauthorized(json.msg ?? json.message)
  }
  if (json.code !== 200) {
    throw new Error(json.msg ?? json.message ?? `API error: ${json.code}`)
  }
  return json.data as T
}
