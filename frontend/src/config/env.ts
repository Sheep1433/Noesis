/** Langfuse Web UI 根地址（与后端 LANGFUSE_BASE_URL 可相同）。设置后助手工具栏显示「观测」入口。 */
export const langfuseUiOrigin = String(import.meta.env.VITE_LANGFUSE_UI_ORIGIN ?? '').replace(/\/$/, '')
