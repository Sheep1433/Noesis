export interface HitlSubmissionState {
  submitting: boolean
}

export function pendingHitlForSession<T>(
  pendingBySession: Record<string, T>,
  sessionId: string | undefined,
): T | null {
  return sessionId ? pendingBySession[sessionId] ?? null : null
}

export function setPendingHitlForSession<T>(
  pendingBySession: Record<string, T>,
  sessionId: string,
  pending: T | null,
): Record<string, T> {
  const next = { ...pendingBySession }
  if (pending) {
    next[sessionId] = pending
  } else {
    delete next[sessionId]
  }
  return next
}

export function shouldShowRunContinuation(status: string): boolean {
  return status === 'queued' || status === 'running' || status === 'retrying'
}

/**
 * HITL pauses the agent while the SSE subscription remains active. The active
 * subscription must not disable the approval controls; only an in-flight
 * submission should prevent another click.
 */
export function shouldDisableHitlComposer(
  pending: HitlSubmissionState | null,
  _streamIsLoading: boolean,
): boolean {
  return Boolean(pending?.submitting)
}
