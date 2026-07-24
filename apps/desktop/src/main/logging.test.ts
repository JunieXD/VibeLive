import { afterEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => {
  const scopedLogger = {
    debug: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warn: vi.fn()
  }
  return {
    crashReporterStart: vi.fn(),
    log: {
      errorHandler: { startCatching: vi.fn() },
      eventLogger: { startLogging: vi.fn() },
      functions: {},
      hooks: [] as Array<(message: unknown) => unknown>,
      initialize: vi.fn(),
      scope: vi.fn(() => scopedLogger),
      transports: {
        console: { level: 'debug' as string | false },
        file: {
          format: '',
          level: 'debug' as string | false,
          maxSize: 0,
          resolvePathFn: () => ''
        }
      },
      variables: {} as Record<string, unknown>
    },
    mkdirSync: vi.fn(() => {
      throw new Error('read-only user data')
    })
  }
})

vi.mock('electron', () => ({
  app: {
    getPath: vi.fn(() => 'Z:\\read-only-user-data'),
    getVersion: vi.fn(() => '0.1.0'),
    isPackaged: false,
    setPath: vi.fn()
  },
  crashReporter: {
    start: mocks.crashReporterStart
  },
  ipcMain: {
    on: vi.fn()
  }
}))

vi.mock('electron-log/main', () => ({
  default: mocks.log
}))

vi.mock('node:fs', () => ({
  mkdirSync: mocks.mkdirSync
}))

describe('logging initialization', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('continues without file logging when the user data directory is unavailable', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const { initializeLogging } = await import('./logging')

    expect(() => initializeLogging()).not.toThrow()
    expect(mocks.mkdirSync).toHaveBeenCalled()
    expect(mocks.log.transports.file.level).toBe(false)
    expect(mocks.crashReporterStart).not.toHaveBeenCalled()
  })
})
