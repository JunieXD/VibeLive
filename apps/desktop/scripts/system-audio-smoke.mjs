import assert from 'node:assert/strict'
import { mkdir, rm } from 'node:fs/promises'
import { createServer } from 'node:net'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright-core'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const artifactDirectory = resolve(root, 'artifacts', 'system-audio-smoke')
const userDataDirectory = resolve(artifactDirectory, 'user-data')
await rm(artifactDirectory, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
await mkdir(userDataDirectory, { recursive: true })

const backendPort = await new Promise((resolvePort, reject) => {
  const server = createServer()
  server.unref()
  server.once('error', reject)
  server.listen(0, '127.0.0.1', () => {
    const address = server.address()
    assert.ok(address && typeof address === 'object')
    server.close((error) => (error ? reject(error) : resolvePort(address.port)))
  })
})

const { ELECTRON_RUN_AS_NODE: _electronRunAsNode, ...electronEnvironment } = process.env
const electronApp = await electron.launch({
  args: [
    '.',
    `--user-data-dir=${userDataDirectory}`,
    '--use-fake-device-for-media-stream'
  ],
  cwd: root,
  env: {
    ...electronEnvironment,
    ADVX_BACKEND_EXTERNAL: '0',
    ADVX_BACKEND_URL: `http://127.0.0.1:${backendPort}`,
    ELECTRON_DISABLE_SECURITY_WARNINGS: 'true'
  }
})

try {
  const page = await electronApp.firstWindow()
  await page.getByRole('heading', { name: '直播控制台', exact: true }).waitFor()
  await page.waitForFunction(() => document.body.textContent?.includes('后端 · 已连接'))
  await page.locator('.screen-video').waitFor({ timeout: 45_000 })

  const microphoneSelect = page.getByRole('combobox', { name: '麦克风', exact: true })
  const microphoneToggle = page.locator('#microphone-toggle')
  const systemAudioToggle = page.locator('#system-audio-toggle')
  await microphoneSelect.waitFor()
  await microphoneToggle.waitFor()
  await systemAudioToggle.waitFor()
  assert.equal(await microphoneToggle.getAttribute('aria-checked'), 'true')
  assert.equal(await systemAudioToggle.getAttribute('aria-checked'), 'true')
  await page.waitForFunction(() => {
    const toggle = document.querySelector('#microphone-toggle')
    return toggle instanceof HTMLButtonElement && !toggle.disabled
  })
  await microphoneToggle.click()
  assert.equal(await microphoneToggle.getAttribute('aria-checked'), 'false')
  assert.equal(
    JSON.parse(
      (await page.evaluate(() => localStorage.getItem('advx.audio-settings'))) ?? 'null'
    )?.microphoneEnabled,
    false
  )
  await page.waitForFunction(() => {
    const toggle = document.querySelector('#microphone-toggle')
    return toggle instanceof HTMLButtonElement && !toggle.disabled
  })
  await microphoneToggle.click()
  assert.equal(await microphoneToggle.getAttribute('aria-checked'), 'true')
  await page.waitForFunction(() => {
    const raw = localStorage.getItem('advx.audio-settings')
    if (!raw) return false
    const settings = JSON.parse(raw)
    return settings.version === 2 && Boolean(settings.selectedMicrophoneId)
  })
  await page.getByText('系统声音 · 推荐', { exact: true }).waitFor()
  if (process.platform !== 'win32') {
    assert.equal(await systemAudioToggle.isDisabled(), true)
    await page.getByText('仅 Windows 支持', { exact: true }).waitFor()
    console.log('System audio smoke passed: unsupported platform is explicitly disabled.')
    process.exitCode = 0
  } else {
    await page.waitForFunction(() => {
      const toggle = document.querySelector('#system-audio-toggle')
      return toggle instanceof HTMLButtonElement && !toggle.disabled
    })
    await systemAudioToggle.click()
    assert.equal(await systemAudioToggle.getAttribute('aria-checked'), 'false')
    assert.equal(
      JSON.parse(
        (await page.evaluate(() => localStorage.getItem('advx.audio-settings'))) ?? 'null'
      )?.systemAudioEnabled,
      false
    )
    await systemAudioToggle.click()
    assert.equal(await systemAudioToggle.getAttribute('aria-checked'), 'true')

    await page.getByRole('button', { name: '设置', exact: true }).click()
    await page.getByLabel('服务地址', { exact: true }).fill('https://smoke.example/v1')
    await page.getByLabel('模型名称', { exact: true }).fill('smoke-model')
    await page.getByLabel('模型 API Key', { exact: true }).fill('smoke-model-key')
    await page.getByLabel('StepFun ASR API Key', { exact: true }).fill('smoke-asr-key')
    await page.getByRole('button', { name: '保存连接', exact: true }).click()
    await page.getByText(/模型与语音识别配置已安全保存/).waitFor()
    await page.getByRole('button', { name: '直播控制台', exact: true }).click()

    await electronApp.evaluate(({ BrowserWindow, ipcMain }) => {
      const channels = [
        'backend:get-status',
        'backend:restart',
        'backend:audience-query',
        'backend:session-start',
        'backend:session-pause',
        'backend:session-resume',
        'backend:session-stop',
        'backend:submit-text',
        'backend:submit-audio',
        'backend:submit-frame'
      ]
      channels.forEach((channel) => ipcMain.removeHandler(channel))
      ipcMain.removeAllListeners('backend:voice-activity')
      ipcMain.on('backend:voice-activity', () => undefined)

      let state = 'idle'
      let sessionId = null
      let startedAtMs = null
      let revision = 0
      const snapshot = () => ({
        sessionId,
        state,
        startedAtMs,
        updatedAtMs: Date.now(),
        revision
      })
      const status = () => ({
        connection: 'connected',
        providersConfigured: true,
        startupError: null,
        recoverableRuntimeSessionId: null,
        session: snapshot()
      })
      const publish = () => {
        BrowserWindow.getAllWindows()
          .find((window) => window.webContents.getURL().includes('/control/'))
          ?.webContents.send('backend:status', status())
      }
      const transition = (nextState) => {
        state = nextState
        revision += 1
        if (nextState === 'running' && sessionId === null) {
          sessionId = 'system-audio-smoke-session'
          startedAtMs = Date.now()
        }
        if (nextState === 'idle') {
          sessionId = null
          startedAtMs = null
        }
        publish()
        return snapshot()
      }

      ipcMain.handle('backend:get-status', status)
      ipcMain.handle('backend:restart', status)
      ipcMain.handle('backend:audience-query', (_event, requestedSessionId) => ({
        session_id: requestedSessionId,
        room_id: 'system-audio-smoke-room',
        audience_epoch: 1,
        population_revision: 1,
        target_concurrent_viewers: 0,
        active_count: 0,
        viewers: []
      }))
      ipcMain.handle('backend:session-start', () => transition('running'))
      ipcMain.handle('backend:session-pause', () => transition('paused'))
      ipcMain.handle('backend:session-resume', () => transition('running'))
      ipcMain.handle('backend:session-stop', () => transition('idle'))
      ipcMain.handle('backend:submit-text', () => undefined)
      ipcMain.handle('backend:submit-audio', () => undefined)
      ipcMain.handle('backend:submit-frame', () => undefined)
      publish()
    })
    await page.waitForFunction(() => document.body.textContent?.includes('后端 · 已连接'))

    await page.evaluate(() => {
      const original = navigator.mediaDevices.getDisplayMedia.bind(navigator.mediaDevices)
      const originalUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices)
      globalThis.__advxSystemAudioTracks = []
      globalThis.__advxLoopbackRequests = 0
      globalThis.__advxMicrophoneTracks = []
      globalThis.__advxMicrophoneRequests = 0
      navigator.mediaDevices.getDisplayMedia = async (constraints) => {
        const stream = await original(constraints)
        if (constraints?.audio) {
          globalThis.__advxLoopbackRequests += 1
          const track = stream.getAudioTracks()[0]
          if (track) globalThis.__advxSystemAudioTracks.push(track)
        }
        return stream
      }
      navigator.mediaDevices.getUserMedia = async (constraints) => {
        const stream = await originalUserMedia(constraints)
        if (constraints?.audio && !constraints?.video) {
          globalThis.__advxMicrophoneRequests += 1
          const track = stream.getAudioTracks()[0]
          if (track) globalThis.__advxMicrophoneTracks.push(track)
        }
        return stream
      }
    })

    await page.getByRole('button', { name: '开始直播', exact: true }).click()
    await page.waitForFunction(() => document.body.textContent?.includes('直播中'))
    await page.waitForFunction(
      () =>
        globalThis.__advxMicrophoneRequests > 0 &&
        globalThis.__advxMicrophoneTracks.at(-1)?.readyState === 'live'
    )
    await page.waitForFunction(
      () =>
        globalThis.__advxLoopbackRequests > 0 &&
        globalThis.__advxSystemAudioTracks.at(-1)?.readyState === 'live'
    )
    await page.waitForFunction(() => document.body.textContent?.includes('麦克风 正常'))
    await page.waitForFunction(() => document.body.textContent?.includes('系统声音 正常'))
    assert.equal(await microphoneSelect.isDisabled(), false)

    await electronApp.evaluate(({ BrowserWindow }) => {
      const controlWindow = BrowserWindow.getAllWindows().find((window) =>
        window.webContents.getURL().includes('/control/')
      )
      const base = {
        startedAtMs: 100,
        endedAtMs: 200,
        utteranceId: 'same-provider-id',
        revision: 1
      }
      controlWindow?.webContents.send('backend:transcript', {
        ...base,
        source: 'microphone',
        text: '主播部分转写',
        final: false,
        utteranceId: null
      })
      controlWindow?.webContents.send('backend:transcript', {
        ...base,
        source: 'system_audio',
        text: '队友部分转写',
        final: false,
        utteranceId: null
      })
    })
    await page.getByText('主播部分转写', { exact: true }).waitFor()
    await page.getByText('队友部分转写', { exact: true }).waitFor()
    assert.equal(await page.locator('.chat-item.transcript').count(), 0)

    await electronApp.evaluate(({ BrowserWindow }) => {
      const controlWindow = BrowserWindow.getAllWindows().find((window) =>
        window.webContents.getURL().includes('/control/')
      )
      const base = {
        final: true,
        startedAtMs: 100,
        endedAtMs: 200,
        utteranceId: 'same-provider-id',
        revision: 1
      }
      controlWindow?.webContents.send('backend:transcript', {
        ...base,
        source: 'microphone',
        text: '主播最终转写'
      })
      controlWindow?.webContents.send('backend:transcript', {
        ...base,
        source: 'system_audio',
        text: '队友最终转写'
      })
    })
    await page
      .locator('.chat-item.transcript')
      .filter({ hasText: '主播最终转写' })
      .getByText('麦克风（主播）', { exact: true })
      .waitFor()
    await page
      .locator('.chat-item.transcript')
      .filter({ hasText: '队友最终转写' })
      .getByText('系统声音', { exact: true })
      .waitFor()
    assert.equal(await page.locator('.chat-item.transcript').count(), 2)

    const activeTrackIndex = await page.evaluate(
      () => globalThis.__advxSystemAudioTracks.length - 1
    )
    await systemAudioToggle.click()
    await page.waitForFunction(
      (index) => globalThis.__advxSystemAudioTracks[index]?.readyState === 'ended',
      activeTrackIndex
    )
    await page.waitForFunction(() => document.body.textContent?.includes('系统声音 已关闭'))
    await systemAudioToggle.click()
    await page.waitForFunction(
      (previousCount) =>
        globalThis.__advxSystemAudioTracks.length > previousCount &&
        globalThis.__advxSystemAudioTracks.at(-1)?.readyState === 'live',
      activeTrackIndex + 1
    )

    const activeMicrophoneTrackIndex = await page.evaluate(
      () => globalThis.__advxMicrophoneTracks.length - 1
    )
    await microphoneToggle.click()
    await page.waitForFunction(
      (index) => globalThis.__advxMicrophoneTracks[index]?.readyState === 'ended',
      activeMicrophoneTrackIndex
    )
    await page.waitForFunction(() => document.body.textContent?.includes('麦克风 已关闭'))
    assert.equal(await microphoneSelect.isDisabled(), false)
    await page.screenshot({
      path: resolve(artifactDirectory, 'microphone-disabled.png'),
      fullPage: true
    })
    await microphoneToggle.click()
    await page.waitForFunction(
      (previousCount) =>
        globalThis.__advxMicrophoneTracks.length > previousCount &&
        globalThis.__advxMicrophoneTracks.at(-1)?.readyState === 'live',
      activeMicrophoneTrackIndex + 1
    )

    const beforePauseTrackIndex = await page.evaluate(
      () => globalThis.__advxSystemAudioTracks.length - 1
    )
    const beforePauseMicrophoneTrackIndex = await page.evaluate(
      () => globalThis.__advxMicrophoneTracks.length - 1
    )
    await page.getByRole('button', { name: '暂停', exact: true }).click()
    await page.waitForFunction(
      (index) => globalThis.__advxMicrophoneTracks[index]?.readyState === 'ended',
      beforePauseMicrophoneTrackIndex
    )
    await page.waitForFunction(
      (index) => globalThis.__advxSystemAudioTracks[index]?.readyState === 'ended',
      beforePauseTrackIndex
    )
    await page.waitForFunction(() => document.body.textContent?.includes('麦克风 已暂停'))
    await page.waitForFunction(() => document.body.textContent?.includes('系统声音 已暂停'))

    await page.getByRole('button', { name: '恢复', exact: true }).click()
    await page.waitForFunction(
      (previousCount) =>
        globalThis.__advxMicrophoneTracks.length > previousCount &&
        globalThis.__advxMicrophoneTracks.at(-1)?.readyState === 'live',
      beforePauseMicrophoneTrackIndex + 1
    )
    await page.waitForFunction(
      (previousCount) =>
        globalThis.__advxSystemAudioTracks.length > previousCount &&
        globalThis.__advxSystemAudioTracks.at(-1)?.readyState === 'live',
      beforePauseTrackIndex + 1
    )
    await page.waitForFunction(() => document.body.textContent?.includes('麦克风 正常'))
    await page.waitForFunction(() => document.body.textContent?.includes('系统声音 正常'))

    const beforeStopTrackIndex = await page.evaluate(
      () => globalThis.__advxSystemAudioTracks.length - 1
    )
    const beforeStopMicrophoneTrackIndex = await page.evaluate(
      () => globalThis.__advxMicrophoneTracks.length - 1
    )
    await page.getByRole('button', { name: '结束直播', exact: true }).click()
    await page.waitForFunction(() => document.body.textContent?.includes('未开播'))
    await page.waitForFunction(
      (index) => globalThis.__advxMicrophoneTracks[index]?.readyState === 'ended',
      beforeStopMicrophoneTrackIndex
    )
    await page.waitForFunction(
      (index) => globalThis.__advxSystemAudioTracks[index]?.readyState === 'ended',
      beforeStopTrackIndex
    )

    await page.screenshot({
      path: resolve(artifactDirectory, 'system-audio-asr.png'),
      fullPage: true
    })
    console.log(
      `System audio smoke passed: ${await page.evaluate(
        () => globalThis.__advxLoopbackRequests
      )} loopback captures and ${await page.evaluate(
        () => globalThis.__advxMicrophoneRequests
      )} microphone captures, independent transcript labels, toggles, pause/resume, and stop cleanup.`
    )
    console.log(`Screenshot: ${resolve(artifactDirectory, 'system-audio-asr.png')}`)
    console.log(`Screenshot: ${resolve(artifactDirectory, 'microphone-disabled.png')}`)
  }
} finally {
  await electronApp.close()
}
