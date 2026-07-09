import { authFetch } from '@/utils/authHttp'
import { attachmentPreviewDataUrl } from '@/utils/filePreview'

export interface AttachmentImageSources {
  thumb: string
  full: string
  /** 是否需在组件卸载时 revoke full（blob URL） */
  revokeFull: boolean
}

function toAbsoluteArtifactUrl(artifactUrl: string): string {
  return artifactUrl.startsWith('http')
    ? artifactUrl
    : `${location.origin}${artifactUrl}`
}

export async function loadAttachmentImageSources(file: {
  preview_base64?: string | null
  artifact_url?: string | null
}): Promise<AttachmentImageSources | null> {
  const thumb = file.preview_base64
    ? attachmentPreviewDataUrl(file.preview_base64)
    : ''

  if (file.artifact_url) {
    try {
      const res = await authFetch(toAbsoluteArtifactUrl(file.artifact_url))
      if (res.ok) {
        const blob = await res.blob()
        const full = URL.createObjectURL(blob)
        return {
          thumb: thumb || full,
          full,
          revokeFull: true,
        }
      }
    } catch {
      // fall through
    }
  }

  if (thumb) {
    return { thumb, full: thumb, revokeFull: false }
  }

  return null
}

export function revokeAttachmentImageSources(sources: AttachmentImageSources | null) {
  if (sources?.revokeFull && sources.full.startsWith('blob:')) {
    URL.revokeObjectURL(sources.full)
  }
}
