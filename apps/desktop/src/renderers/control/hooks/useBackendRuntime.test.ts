import { describe, expect, it } from 'vitest'
import type { BackendRuntimeStatus } from '../../../shared/contracts'
import {
  createDisconnectedBackendStatus,
  getBackendNotice
} from './useBackendRuntime'

function status(
  connection: BackendRuntimeStatus['connection'],
  startupError: string | null = null
): BackendRuntimeStatus {
  return {
    ...createDisconnectedBackendStatus(42),
    connection,
    startupError
  }
}

describe('backend runtime state helpers', () => {
  it('creates a stable disconnected fallback after an initial IPC failure', () => {
    expect(createDisconnectedBackendStatus(42)).toMatchObject({
      connection: 'disconnected',
      providersConfigured: false,
      session: { state: 'idle', updatedAtMs: 42, revision: 0 }
    })
  })

  it('uses the actionable startup error for a failed backend', () => {
    expect(getBackendNotice(status('failed', 'Python runtime missing'))).toEqual({
      title: '本地服务启动失败',
      detail: 'Python runtime missing'
    })
  })

  it('hides the notice once the backend is connected', () => {
    expect(getBackendNotice(status('connected'))).toBeNull()
  })
})
