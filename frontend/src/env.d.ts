/// <reference types="vite/client" />

interface ImportMetaEnv extends Readonly<Record<string, string | undefined>> {
  readonly VITE_BASE_API?: string
  readonly VITE_LANGFUSE_UI_ORIGIN?: string
  readonly VITE_ROUTER_MODE?: string
  readonly VITE_TEST_CASE_UPLOAD_COLLECTION?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
