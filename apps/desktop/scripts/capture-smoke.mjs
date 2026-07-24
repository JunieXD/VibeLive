import assert from 'node:assert/strict'
import { mkdir, rm } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright-core'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const artifactDirectory = resolve(root, 'artifacts')
const userDataDirectory = resolve(artifactDirectory, 'capture-smoke-user-data')
await rm(userDataDirectory, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
await mkdir(userDataDirectory, { recursive: true })

const { ELECTRON_RUN_AS_NODE: _electronRunAsNode, ...electronEnvironment } = process.env
const electronApp = await electron.launch({
  args: ['.', `--user-data-dir=${userDataDirectory}`],
  cwd: root,
  env: {
    ...electronEnvironment,
    ADVX_BACKEND_EXTERNAL: '1',
    ADVX_BACKEND_URL: 'http://127.0.0.1:1',
    ELECTRON_DISABLE_SECURITY_WARNINGS: 'true'
  }
})

async function previewSnapshot(page) {
  return page.locator('.screen-video').evaluate((video) => {
    const track = video.srcObject?.getVideoTracks()[0]
    const crop = globalThis.__advxCaptureSmokeCrop
    const canvas = document.createElement('canvas')
    canvas.width = 32
    canvas.height = 32
    const context = canvas.getContext('2d', { willReadFrequently: true })
    let frameHash = null
    let averageColor = null
    if (context && video.videoWidth > 0 && video.videoHeight > 0) {
      const sourceX = crop ? crop.x * video.videoWidth : 0
      const sourceY = crop ? crop.y * video.videoHeight : 0
      const sourceWidth = crop ? crop.width * video.videoWidth : video.videoWidth
      const sourceHeight = crop ? crop.height * video.videoHeight : video.videoHeight
      context.drawImage(
        video,
        sourceX,
        sourceY,
        sourceWidth,
        sourceHeight,
        0,
        0,
        canvas.width,
        canvas.height
      )
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data
      let hash = 2166136261
      let red = 0
      let green = 0
      let blue = 0
      for (let index = 0; index < pixels.length; index += 4) {
        red += pixels[index]
        green += pixels[index + 1]
        blue += pixels[index + 2]
        hash ^= pixels[index]
        hash = Math.imul(hash, 16777619)
        hash ^= pixels[index + 1]
        hash = Math.imul(hash, 16777619)
        hash ^= pixels[index + 2]
        hash = Math.imul(hash, 16777619)
      }
      const pixelCount = pixels.length / 4
      frameHash = hash >>> 0
      averageColor = {
        red: Math.round(red / pixelCount),
        green: Math.round(green / pixelCount),
        blue: Math.round(blue / pixelCount)
      }
    }
    return {
      currentTime: video.currentTime,
      averageColor,
      frameHash,
      trackState: track?.readyState ?? null,
      sameTrack: track === globalThis.__advxCaptureSmokeTrack
    }
  })
}

try {
  const page = await electronApp.firstWindow()
  await page.getByRole('heading', { name: '直播控制台', exact: true }).waitFor()
  await page.getByRole('button', { name: '更换来源', exact: true }).waitFor()
  await page.locator('.screen-video').waitFor()

  const defaultSource = await page.evaluate(async () => {
    const sources = await window.advx.listDesktopSources()
    const video = document.querySelector('.screen-video')
    const track =
      video instanceof HTMLVideoElement ? video.srcObject?.getVideoTracks()[0] : undefined
    globalThis.__advxCaptureSmokeTrack = track
    return {
      expectedName: sources.find((source) => source.kind === 'screen')?.name ?? null,
      selectedName: document
        .querySelector('.stage-source .panel-subtitle')
        ?.textContent?.trim(),
      trackState: track?.readyState ?? null
    }
  })
  assert.ok(defaultSource.expectedName, 'No desktop screen source was available.')
  assert.equal(defaultSource.selectedName, defaultSource.expectedName)
  assert.equal(defaultSource.trackState, 'live')

  const backgroundThrottling = await electronApp.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()
      .find((window) => window.webContents.getURL().includes('/control/'))
      ?.webContents.getBackgroundThrottling()
  )
  assert.equal(backgroundThrottling, false)

  await page.getByRole('button', { name: '开始直播', exact: true }).click()
  await page.waitForFunction(() => document.body.textContent?.includes('直播中'))
  await page.getByRole('button', { name: '暂停', exact: true }).click()
  await page.waitForFunction(() => document.body.textContent?.includes('观察已暂停'))

  const pausedStart = await previewSnapshot(page)
  await page.waitForTimeout(750)
  const pausedEnd = await previewSnapshot(page)
  assert.equal(pausedEnd.trackState, 'live')
  assert.equal(pausedEnd.sameTrack, true)
  assert.ok(
    pausedEnd.currentTime > pausedStart.currentTime + 0.05,
    `Preview froze while paused: ${pausedStart.currentTime} -> ${pausedEnd.currentTime}`
  )

  await page.getByRole('button', { name: '恢复', exact: true }).click()
  await page.waitForFunction(() => document.body.textContent?.includes('直播中'))
  assert.equal((await previewSnapshot(page)).sameTrack, true)

  await page.getByRole('button', { name: '结束直播', exact: true }).click()
  await page.waitForFunction(() => document.body.textContent?.includes('未开播'))
  const stoppedStart = await previewSnapshot(page)
  await page.waitForTimeout(750)
  const stoppedEnd = await previewSnapshot(page)
  assert.equal(stoppedEnd.trackState, 'live')
  assert.equal(stoppedEnd.sameTrack, true)
  assert.ok(
    stoppedEnd.currentTime > stoppedStart.currentTime + 0.05,
    `Preview froze after stop: ${stoppedStart.currentTime} -> ${stoppedEnd.currentTime}`
  )

  const markerCrop = await electronApp.evaluate(async ({ BrowserWindow, screen }) => {
    const display = screen.getPrimaryDisplay()
    const size = Math.min(360, display.workArea.width, display.workArea.height)
    const bounds = {
      x: display.workArea.x + 24,
      y: display.workArea.y + 24,
      width: size,
      height: size
    }
    const marker = new BrowserWindow({
      ...bounds,
      alwaysOnTop: true,
      backgroundColor: '#ff0000',
      focusable: false,
      frame: false,
      show: false,
      skipTaskbar: true
    })
    await marker.loadURL(
      'data:text/html,<body style="margin:0;background:%23ff0000;width:100vw;height:100vh"></body>'
    )
    marker.setAlwaysOnTop(true, 'screen-saver')
    marker.showInactive()
    globalThis.__advxCaptureSmokeMarker = marker
    return {
      x: (bounds.x - display.bounds.x) / display.bounds.width,
      y: (bounds.y - display.bounds.y) / display.bounds.height,
      width: bounds.width / display.bounds.width,
      height: bounds.height / display.bounds.height
    }
  })
  await page.evaluate((crop) => {
    globalThis.__advxCaptureSmokeCrop = crop
  }, markerCrop)
  await electronApp.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()
      .find((window) => window.webContents.getURL().includes('/control/'))
      ?.minimize()
  })
  await page.waitForTimeout(750)
  const minimizedRed = await previewSnapshot(page)
  await electronApp.evaluate(async () => {
    await globalThis.__advxCaptureSmokeMarker?.webContents.executeJavaScript(
      "document.body.style.background = '#00ff00'"
    )
  })
  await page.waitForTimeout(750)
  const minimizedGreen = await previewSnapshot(page)
  await electronApp.evaluate(({ BrowserWindow }) => {
    globalThis.__advxCaptureSmokeMarker?.destroy()
    delete globalThis.__advxCaptureSmokeMarker
    const controlWindow = BrowserWindow.getAllWindows().find((window) =>
      window.webContents.getURL().includes('/control/')
    )
    controlWindow?.restore()
    controlWindow?.show()
  })
  assert.equal(minimizedGreen.trackState, 'live')
  assert.equal(minimizedGreen.sameTrack, true)
  assert.notEqual(
    minimizedGreen.frameHash,
    minimizedRed.frameHash,
    'Captured desktop pixels did not change with the visible test marker.'
  )
  assert.ok(
    minimizedRed.averageColor?.red > (minimizedRed.averageColor?.green ?? 255) + 80,
    `Expected a red captured marker, received ${JSON.stringify(minimizedRed.averageColor)}.`
  )
  assert.ok(
    minimizedGreen.averageColor?.green > (minimizedGreen.averageColor?.red ?? 255) + 80,
    `Expected a green captured marker, received ${JSON.stringify(minimizedGreen.averageColor)}.`
  )

  const screenshotPath = resolve(artifactDirectory, 'capture-continuity.png')
  await page.screenshot({ path: screenshotPath, fullPage: true })

  await page.getByRole('button', { name: '开始直播', exact: true }).click()
  await page.waitForFunction(() => document.body.textContent?.includes('直播中'))
  await electronApp.evaluate(({ ipcMain }) => {
    ipcMain.removeHandler('backend:get-status')
    ipcMain.handle('backend:get-status', async () => {
      await new Promise((resolveStatus) => setTimeout(resolveStatus, 500))
      return {
        connection: 'failed',
        providersConfigured: false,
        startupError: 'Capture smoke offline backend.',
        recoverableRuntimeSessionId: null,
        session: {
          sessionId: null,
          state: 'idle',
          startedAtMs: null,
          updatedAtMs: Date.now(),
          revision: 0
        }
      }
    })
  })
  await page.getByRole('button', { name: '结束直播', exact: true }).click()
  await page.waitForFunction(() => document.body.textContent?.includes('停止中'))
  await page.evaluate(() => {
    const track = globalThis.__advxCaptureSmokeTrack
    track?.stop()
    track?.dispatchEvent(new Event('ended'))
  })
  await page.waitForFunction(() => document.body.textContent?.includes('未开播'), undefined, {
    timeout: 5_000
  })

  console.log(
    `Capture smoke passed: default desktop selected, one track survived pause/stop, minimized capture observed deterministic pixel changes, and track loss did not stall session stop.`
  )
  console.log(`Screenshot: ${screenshotPath}`)
} finally {
  await electronApp.close()
}
