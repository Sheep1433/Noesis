const defaultHost = {
  hostname: 'localhost',
  /** REST API 前缀：开发经 Vite proxy `/api` → 127.0.0.1:8089；生产默认同源 */
  baseApi: import.meta.env.VITE_BASE_API || '/api',
}

const hostList = [defaultHost]

/**
 * 获取当前页面的 API baseURL
 */
export const currentHost =
  hostList.find((item) => window.location.hostname === item.hostname) ?? defaultHost
