import { mkdir } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright-core'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const artifactDirectory = resolve(root, 'artifacts')
await mkdir(artifactDirectory, { recursive: true })
const { ELECTRON_RUN_AS_NODE: _electronRunAsNode, ...electronEnvironment } = process.env

const electronApp = await electron.launch({
  args: ['.'],
  cwd: root,
  env: {
    ...electronEnvironment,
    ELECTRON_DISABLE_SECURITY_WARNINGS: 'true'
  }
})
try {
  const page = await electronApp.firstWindow()
  await page.waitForSelector('h1')

  const title = await page.locator('h1').textContent()
  if (title?.trim() !== '直播控制台') {
    throw new Error(`Unexpected initial view: ${title}`)
  }

  await page.screenshot({
    path: resolve(artifactDirectory, 'control-console.png'),
    fullPage: true
  })

  await page.getByRole('button', { name: /AI 观众/ }).click()
  await page.getByRole('heading', { name: 'AI 观众', exact: true }).waitFor()

  await page.getByRole('button', { name: '设置', exact: true }).click()
  await page.getByRole('heading', { name: '设置', exact: true }).waitFor()

  await page.getByRole('button', { name: '直播控制台', exact: true }).click()
  const cameraPermission = await page.evaluate(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true })
      stream.getTracks().forEach((track) => track.stop())
      return 'granted'
    } catch (error) {
      return error instanceof DOMException ? error.name : 'rejected'
    }
  })
  if (cameraPermission === 'granted') {
    throw new Error('Camera permission was granted even though the app only requests microphones.')
  }

  await page.getByRole('button', { name: '选择来源', exact: true }).click()
  await page.locator('.source-option').first().waitFor()
  const sourceCount = await page.locator('.source-option').count()
  if (sourceCount < 1) {
    throw new Error('Desktop source IPC returned no sources.')
  }

  await page.evaluate(() => {
    const original = navigator.mediaDevices.getDisplayMedia.bind(navigator.mediaDevices)
    globalThis.__advxSmokeOriginalGetDisplayMedia = original
    navigator.mediaDevices.getDisplayMedia = async (constraints) => {
      const stream = await original(constraints)
      globalThis.__advxSmokePendingDisplayTrack = stream.getVideoTracks()[0]
      await new Promise((resolvePendingCapture) => {
        globalThis.__advxSmokeReleaseDisplayCapture = resolvePendingCapture
      })
      return stream
    }
  })
  await page.locator('.source-option').first().click()
  await page.getByRole('button', { name: '使用此来源', exact: true }).click()
  await page.waitForFunction(() => globalThis.__advxSmokeReleaseDisplayCapture)
  await electronApp.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()
      .find((window) => window.webContents.getURL().includes('/control/'))
      ?.webContents.send('session:emergency-stop')
  })
  await page.waitForFunction(() => {
    const button = [...document.querySelectorAll('button')].find(
      (candidate) => candidate.textContent?.trim() === '选择来源'
    )
    return button instanceof HTMLButtonElement && !button.disabled
  })
  await page.evaluate(() => {
    navigator.mediaDevices.getDisplayMedia = globalThis.__advxSmokeOriginalGetDisplayMedia
    globalThis.__advxSmokeReleaseDisplayCapture?.()
  })
  await page.waitForFunction(
    () => globalThis.__advxSmokePendingDisplayTrack?.readyState === 'ended'
  )
  if ((await page.locator('video').count()) !== 0) {
    throw new Error('A display stream was published after emergency stop invalidated its request.')
  }

  await page.getByRole('button', { name: '选择来源', exact: true }).click()
  await page.locator('.source-option').first().click()
  await page.getByRole('button', { name: '使用此来源', exact: true }).click()
  try {
    await page.locator('video').waitFor({ timeout: 15_000 })
  } catch (error) {
    console.error(`Control surface after display capture failure:\n${await page.locator('body').innerText()}`)
    throw error
  }
  const displayTrackState = await page.locator('video').evaluate((video) => {
    const stream = video.srcObject
    return stream instanceof MediaStream ? stream.getVideoTracks()[0]?.readyState : undefined
  })
  if (displayTrackState !== 'live') {
    throw new Error(`Expected a live display track, received ${displayTrackState ?? 'none'}.`)
  }
  await page.locator('video').evaluate((video) => {
    globalThis.__advxSmokeDisplayTrack = video.srcObject?.getVideoTracks()[0]
  })
  const selectedSourceName = await page.locator('.stage-source .panel-subtitle').textContent()
  await page.evaluate(() => {
    const original = navigator.mediaDevices.getDisplayMedia.bind(navigator.mediaDevices)
    globalThis.__advxSmokeOriginalGetDisplayMedia = original
    navigator.mediaDevices.getDisplayMedia = async () => {
      throw new DOMException('Simulated source switch failure.', 'NotAllowedError')
    }
  })
  await page.getByRole('button', { name: '更换来源', exact: true }).click()
  await page.locator('.source-option').nth(sourceCount > 1 ? 1 : 0).click()
  await page.getByRole('button', { name: '使用此来源', exact: true }).click()
  await page.waitForFunction(() => document.body.textContent?.includes('录屏权限被拒绝'))
  const sourceNameAfterFailedSwitch = await page
    .locator('.stage-source .panel-subtitle')
    .textContent()
  if (sourceNameAfterFailedSwitch !== selectedSourceName) {
    throw new Error('Failed source switch changed the selected source shown in the UI.')
  }
  const displayStateAfterFailedSwitch = await page.evaluate(
    () => globalThis.__advxSmokeDisplayTrack?.readyState
  )
  if (displayStateAfterFailedSwitch !== 'live') {
    throw new Error('Failed source switch stopped the previously active display stream.')
  }
  await page.evaluate(() => {
    navigator.mediaDevices.getDisplayMedia = globalThis.__advxSmokeOriginalGetDisplayMedia
  })

  await page.evaluate(() => {
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices)
    globalThis.__advxSmokeOriginalGetUserMedia = original
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      const stream = await original(constraints)
      globalThis.__advxSmokePendingMicrophoneTrack = stream.getAudioTracks()[0]
      await new Promise((resolvePendingCapture) => {
        globalThis.__advxSmokeReleaseMicrophoneCapture = resolvePendingCapture
      })
      return stream
    }
  })
  await page.getByRole('button', { name: '授权并检测', exact: true }).click()
  await page.waitForFunction(() => globalThis.__advxSmokeReleaseMicrophoneCapture)
  await electronApp.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()
      .find((window) => window.webContents.getURL().includes('/control/'))
      ?.webContents.send('session:emergency-stop')
  })
  await page.locator('video').waitFor({ state: 'detached' })
  await page.waitForFunction(() => {
    const button = [...document.querySelectorAll('button')].find(
      (candidate) => candidate.textContent?.trim() === '授权并检测'
    )
    return button instanceof HTMLButtonElement && !button.disabled
  })
  await page.evaluate(() => {
    const original = globalThis.__advxSmokeOriginalGetUserMedia
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      const stream = await original(constraints)
      globalThis.__advxSmokeMicrophoneTrack = stream.getAudioTracks()[0]
      return stream
    }
    globalThis.__advxSmokeReleaseMicrophoneCapture?.()
  })
  await page.waitForFunction(
    () => globalThis.__advxSmokePendingMicrophoneTrack?.readyState === 'ended'
  )

  await page.getByRole('button', { name: '授权并检测', exact: true }).click()
  await page.waitForFunction(() => document.body.textContent?.includes('麦克风 正常'))
  const microphoneTrackState = await page.evaluate(
    () => globalThis.__advxSmokeMicrophoneTrack?.readyState
  )
  if (microphoneTrackState !== 'live') {
    throw new Error(`Expected a live microphone track, received ${microphoneTrackState ?? 'none'}.`)
  }
  let microphonePeak = 0
  for (let sample = 0; sample < 6; sample += 1) {
    const label = await page.locator('.command-meter').getAttribute('aria-label')
    microphonePeak = Math.max(microphonePeak, Number(label?.match(/(\d+)%/)?.[1] ?? 0))
    await page.waitForTimeout(120)
  }

  await page.getByRole('button', { name: '开始直播', exact: true }).click()
  await page.waitForFunction(() => document.body.textContent?.includes('直播中'))

  await page.getByRole('button', { name: '暂停', exact: true }).click()
  await page.waitForFunction(() => document.body.textContent?.includes('画面 已暂停'))
  if ((await page.locator('video').count()) !== 0) {
    throw new Error('Display preview remained live after pause.')
  }
  const pausedDisplayTrackState = await page.evaluate(
    () => globalThis.__advxSmokeDisplayTrack?.readyState
  )
  if (pausedDisplayTrackState !== 'ended') {
    throw new Error(`Display track was not released on pause: ${pausedDisplayTrackState}.`)
  }
  const pausedMicrophoneTrackState = await page.evaluate(
    () => globalThis.__advxSmokeMicrophoneTrack?.readyState
  )
  if (pausedMicrophoneTrackState !== 'ended') {
    throw new Error(`Microphone track was not released on pause: ${pausedMicrophoneTrackState}.`)
  }

  await page.getByRole('button', { name: '恢复', exact: true }).click()
  await page.locator('video').waitFor()
  await page.waitForFunction(() => document.body.textContent?.includes('直播中'))
  await page.locator('video').evaluate((video) => {
    globalThis.__advxSmokeDisplayTrack = video.srcObject?.getVideoTracks()[0]
  })
  await page.waitForFunction(() => globalThis.__advxSmokeMicrophoneTrack?.readyState === 'live')

  await page.getByRole('button', { name: '结束直播', exact: true }).click()
  await page.waitForFunction(() => document.body.textContent?.includes('未开播'))
  if ((await page.locator('video').count()) !== 0) {
    throw new Error('Display preview remained live after stop.')
  }
  const stoppedDisplayTrackState = await page.evaluate(
    () => globalThis.__advxSmokeDisplayTrack?.readyState
  )
  if (stoppedDisplayTrackState !== 'ended') {
    throw new Error(`Display track was not released on stop: ${stoppedDisplayTrackState}.`)
  }
  const stoppedMicrophoneTrackState = await page.evaluate(
    () => globalThis.__advxSmokeMicrophoneTrack?.readyState
  )
  if (stoppedMicrophoneTrackState !== 'ended') {
    throw new Error(`Microphone track was not released on stop: ${stoppedMicrophoneTrackState}.`)
  }
  if (await page.locator('body').getByText('麦克风 正常', { exact: true }).count()) {
    throw new Error('Microphone remained active after stop.')
  }

  console.log(
    `Monorepo desktop smoke passed: ${sourceCount} sources, camera denied, pending capture invalidation, live display preview, microphone meter peak ${microphonePeak}%, pause/resume cleanup, and stop cleanup.`
  )
  console.log(`Screenshot: ${resolve(artifactDirectory, 'control-console.png')}`)
} finally {
  await electronApp.close()
}
