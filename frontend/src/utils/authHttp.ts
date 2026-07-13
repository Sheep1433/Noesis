import Router from '@/router'
import { LOGIN_ROUTE_PATH } from '@/router/routes'
import { useUserStore } from '@/store/business/userStore'

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
export function handleUnauthorized(message?: string): never {
  const store = useUserStore()
  store.logoutLocal()
  if (!redirectingToLogin) {
    redirectingToLogin = true
    window.$ModalMessage?.error(message ?? '登录已过期，请重新登录')
    void Router.replace(LOGIN_ROUTE_PATH).finally(() => {
      redirectingToLogin = false
    })
  }
  throw new UnauthorizedError(message)
}
export function getAuthHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const csrf = useUserStore().csrfToken
  return { ...(csrf ? { 'X-CSRF-Token': csrf } : {}), ...extra }
}
export async function authFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase()
  const headers = new Headers(input instanceof Request ? input.headers : undefined)
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && useUserStore().csrfToken) {
    headers.set('X-CSRF-Token', useUserStore().csrfToken)
  }
  if (init?.headers) {
    new Headers(init.headers).forEach((v, k) => headers.set(k, v))
  }
  const res = await fetch(input, { ...init, credentials: 'include', headers })
  if (res.status === 401) {
    handleUnauthorized()
  }
  return res
}
export async function parseAuthJson<T>(res: Response): Promise<T> {
  const json = await res.json() as { code?: number, msg?: string, data?: T }
  if (res.status === 401 || json.code === 401) {
    handleUnauthorized(json.msg)
  }
  if (json.code !== 200) {
    throw new Error(json.msg ?? `API error: ${json.code}`)
  }
  return json.data as T
}
