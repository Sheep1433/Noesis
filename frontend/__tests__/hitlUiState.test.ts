import { describe, expect, it } from 'vitest'
import {
  pendingHitlForSession,
  setPendingHitlForSession,
  shouldDisableHitlComposer,
  shouldShowRunContinuation,
} from '@/views/chat/hitlUiState'

describe('hitl composer state', () => {
  it('keeps approval controls enabled while the run subscription is active', () => {
    expect(shouldDisableHitlComposer({ submitting: false }, true)).toBe(false)
  })

  it('disables approval controls while a decision is being submitted', () => {
    expect(shouldDisableHitlComposer({ submitting: true }, true)).toBe(true)
  })

  it('shows only the approval bound to the active session', () => {
    const pendingBySession = {
      'session-1': { interrupt_id: 'interrupt-1' },
      'session-2': { interrupt_id: 'interrupt-2' },
    }

    expect(pendingHitlForSession(pendingBySession, 'session-1')).toEqual({
      interrupt_id: 'interrupt-1',
    })
    expect(pendingHitlForSession(pendingBySession, 'session-3')).toBeNull()
  })

  it('clears one session without removing approvals from other sessions', () => {
    const pendingBySession = {
      'session-1': { interrupt_id: 'interrupt-1' },
      'session-2': { interrupt_id: 'interrupt-2' },
    }

    const next = setPendingHitlForSession(pendingBySession, 'session-1', null)

    expect(next['session-1']).toBeUndefined()
    expect(next['session-2']).toEqual({ interrupt_id: 'interrupt-2' })
  })

  it('shows continuation while a resumed run is active', () => {
    expect(shouldShowRunContinuation('queued')).toBe(true)
    expect(shouldShowRunContinuation('running')).toBe(true)
    expect(shouldShowRunContinuation('retrying')).toBe(true)
    expect(shouldShowRunContinuation('hitl_pending')).toBe(false)
    expect(shouldShowRunContinuation('completed')).toBe(false)
    expect(shouldShowRunContinuation('error')).toBe(false)
  })
})
