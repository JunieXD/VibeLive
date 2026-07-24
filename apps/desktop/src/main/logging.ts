import { app, crashReporter, ipcMain } from 'electron'
import log from 'electron-log/main'
import { mkdirSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import { join } from 'node:path'
import { redactLogData } from './logging-redaction'

const MAX_LOG_SIZE_BYTES = 5 * 1024 * 1024

let initialized = false

const fallbackConsoleError = console.error.bind(console)
const applicationRunId = randomUUID()
export const logger = log.scope('app')
export const backendLogger = log.scope('backend')
export const auditLogger = log.scope('action')

export function initializeLogging(): void {
  if (initialized) return
  try {
    initializeLoggingInternal()
  } catch (error) {
    try {
      log.transports.file.level = false
    } catch {
      // The original console remains available when electron-log cannot initialize.
    }
    fallbackConsoleError('[logging] initialization failed; continuing without file logging', error)
  } finally {
    initialized = true
  }
}

function initializeLoggingInternal(): void {
  let crashDumpsDirectory: string | undefined
  let logPath: string | undefined
  let loggingReady = false
  try {
    const userDataDirectory = app.getPath('userData')
    const logsDirectory = join(userDataDirectory, 'logs')
    crashDumpsDirectory = join(userDataDirectory, 'crash-dumps')
    logPath = join(logsDirectory, 'advx.log')
    mkdirSync(logsDirectory, { recursive: true })

    log.variables.sessionId = applicationRunId
    log.transports.file.resolvePathFn = () => logPath!
    log.transports.file.maxSize = MAX_LOG_SIZE_BYTES
    log.transports.file.level = 'debug'
    log.transports.file.format =
      '[{iso}] [{level}] [run={sessionId}] [{processType}]{scope} {text}'
    log.transports.console.level = app.isPackaged ? 'warn' : 'debug'
    log.hooks.push((message) => ({
      ...message,
      data: redactLogData(message.data)
    }))

    log.initialize({ spyRendererConsole: true })
    log.errorHandler.startCatching({ showDialog: false })
    log.eventLogger.startLogging({ level: 'warn', scope: 'electron' })
    Object.assign(console, log.functions)
    ipcMain.on('logging:action', (_event, payload: unknown) => {
      const action = parseActionLog(payload)
      if (!action) {
        logger.warn('action.invalid-log-payload')
        return
      }
      const details = {
        channel: action.channel,
        durationMs: action.durationMs,
        error: action.error
      }
      if (action.stage === 'failed') auditLogger.error('action.failed', details)
      else auditLogger.info(`action.${action.stage}`, details)
    })
    loggingReady = true
  } catch (error) {
    try {
      log.transports.file.level = false
    } catch {
      // The original console remains available when electron-log cannot initialize.
    }
    fallbackConsoleError('[logging] file logging disabled; continuing with console output', error)
  }

  try {
    crashDumpsDirectory ??= join(app.getPath('userData'), 'crash-dumps')
    mkdirSync(crashDumpsDirectory, { recursive: true })
    app.setPath('crashDumps', crashDumpsDirectory)
    crashReporter.start({
      uploadToServer: false,
      globalExtra: {
        app_version: app.getVersion(),
        session_id: applicationRunId
      }
    })
  } catch (error) {
    if (loggingReady) logger.warn('crash-reporter.start.failed', error)
    else fallbackConsoleError('[logging] local crash reporter unavailable', error)
  }

  if (loggingReady) {
    logger.info('app.start', {
      appVersion: app.getVersion(),
      crashDumpsDirectory,
      logPath,
      packaged: app.isPackaged,
      platform: process.platform,
      sessionId: applicationRunId
    })
  }
}

type ActionLog = {
  channel: string
  durationMs?: number
  error?: string
  stage: 'started' | 'completed' | 'failed'
}

function parseActionLog(value: unknown): ActionLog | null {
  if (!value || typeof value !== 'object') return null
  const candidate = value as Partial<ActionLog>
  if (
    typeof candidate.channel !== 'string' ||
    !/^[a-z0-9:-]{1,80}$/.test(candidate.channel) ||
    (candidate.stage !== 'started' &&
      candidate.stage !== 'completed' &&
      candidate.stage !== 'failed')
  ) {
    return null
  }
  if (
    candidate.durationMs !== undefined &&
    (typeof candidate.durationMs !== 'number' ||
      !Number.isFinite(candidate.durationMs) ||
      candidate.durationMs < 0)
  ) {
    return null
  }
  if (candidate.error !== undefined && typeof candidate.error !== 'string') return null
  return {
    channel: candidate.channel,
    durationMs: candidate.durationMs,
    error: candidate.error?.slice(0, 500),
    stage: candidate.stage
  }
}
