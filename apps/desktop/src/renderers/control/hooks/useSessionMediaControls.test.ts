import { describe, expect, it } from 'vitest'
import type { BackendSessionSnapshot } from '../../../shared/contracts'
import { canStopBackendSession } from './useSessionMediaControls'

function backendSession(
  state: BackendSessionSnapshot['state'],
  sessionId: string | null
): BackendSessionSnapshot {
  return {
    sessionId,
    state,
    startedAtMs: sessionId ? 1 : null,
    updatedAtMs: 2,
    revision: 1
  }
}

describe('backend session ownership recovery', () => {
  it('recognizes a Session that became active after the desktop start request failed', () => {
    expect(canStopBackendSession(backendSession('running', 'session-1'))).toBe(true)
  })

  it('does not attempt to stop an idle backend without a Session', () => {
    expect(canStopBackendSession(backendSession('idle', null))).toBe(false)
  })

  it('does not issue a duplicate stop while the backend is already stopping', () => {
    expect(canStopBackendSession(backendSession('stopping', 'session-1'))).toBe(false)
  })
})
