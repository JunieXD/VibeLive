import { mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright-core'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const artifactDirectory = resolve(root, 'artifacts')
await mkdir(artifactDirectory, { recursive: true })
const smokeUserDataDirectory = resolve(artifactDirectory, 'smoke-user-data')
await rm(smokeUserDataDirectory, {
  recursive: true,
  force: true,
  maxRetries: 5,
  retryDelay: 200
})
await mkdir(smokeUserDataDirectory, { recursive: true })
const { ELECTRON_RUN_AS_NODE: _electronRunAsNode, ...electronEnvironment } = process.env

const electronApp = await electron.launch({
  args: ['.', `--user-data-dir=${smokeUserDataDirectory}`, '--use-fake-device-for-media-stream'],
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
  const modeSelect = page.getByLabel('观众模式')
  if ((await modeSelect.locator('option').count()) !== 6) {
    throw new Error('Expected six built-in audience modes.')
  }
  if ((await page.locator('.aw-persona-row').count()) !== 32) {
    throw new Error('Expected the complete 32-persona catalog.')
  }
  await modeSelect.selectOption('room-6657')
  await page.waitForFunction(
    () => document.querySelector('.aw-mode-copy strong')?.textContent === '6657 玩机器风格'
  )
  const modeRanges = await page.locator('.aw-range-control input').evaluateAll((inputs) =>
    inputs.map((input) => input.value)
  )
  if (modeRanges.join(',') !== '6,10,20,28') {
    throw new Error(`Unexpected 6657 activity ranges: ${modeRanges.join(',')}`)
  }

  await page.getByRole('button', { name: '成长梗库', exact: true }).click()
  await page.getByRole('button', { name: '手动新增梗', exact: true }).click()
  await page.locator('.aw-meme-form textarea').first().fill('烟火味这下有说法了')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await page.waitForTimeout(700)
  await page.getByText('烟火味这下有说法了', { exact: true }).first().waitFor()

  await electronApp.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()
      .find((window) => window.webContents.getURL().includes('/control/'))
      ?.setSize(1120, 720)
  })
  await page.waitForTimeout(150)
  const audienceOverflow = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth > window.innerWidth,
    workspace:
      (document.querySelector('.audience-workspace')?.scrollWidth ?? 0) >
      (document.querySelector('.audience-workspace')?.clientWidth ?? 0)
  }))
  if (audienceOverflow.document || audienceOverflow.workspace) {
    throw new Error(`Audience workspace overflowed at 1120x720: ${JSON.stringify(audienceOverflow)}`)
  }
  await page.screenshot({
    path: resolve(artifactDirectory, 'audience-workspace-1120.png')
  })
  await page.getByRole('button', { name: '人格阵容', exact: true }).click()
  const personaLayout = await page.evaluate(() => {
    const metrics = (selector) => {
      const element = document.querySelector(selector)
      return element
        ? {
            clientHeight: element.clientHeight,
            scrollHeight: element.scrollHeight,
            clientWidth: element.clientWidth,
            scrollWidth: element.scrollWidth
          }
        : null
    }
    return {
      viewportHeight: window.innerHeight,
      document: metrics('html'),
      workspace: metrics('.workspace'),
      audience: metrics('.audience-workspace'),
      layout: metrics('.aw-persona-layout'),
      editor: metrics('.aw-editor')
    }
  })
  if (
    !personaLayout.workspace ||
    !personaLayout.audience ||
    !personaLayout.layout ||
    !personaLayout.editor ||
    personaLayout.workspace.scrollHeight > personaLayout.workspace.clientHeight + 1 ||
    personaLayout.audience.scrollHeight > personaLayout.audience.clientHeight + 1 ||
    personaLayout.layout.scrollHeight > personaLayout.layout.clientHeight + 1 ||
    personaLayout.editor.scrollHeight > personaLayout.editor.clientHeight + 1
  ) {
    throw new Error(`Persona workspace escaped its viewport: ${JSON.stringify(personaLayout)}`)
  }
  await page.screenshot({
    path: resolve(artifactDirectory, 'audience-personas-1120.png')
  })
  await electronApp.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()
      .find((window) => window.webContents.getURL().includes('/control/'))
      ?.setSize(1440, 900)
  })

  await page.reload()
  await page.getByRole('heading', { name: '直播控制台', exact: true }).waitFor()
  await page.getByRole('button', { name: /AI 观众/ }).click()
  await page.waitForFunction(() => {
    const select = document.querySelector('select')
    return select instanceof HTMLSelectElement && select.value === 'room-6657'
  })
  await page.getByRole('button', { name: '成长梗库', exact: true }).click()
  await page.getByText('烟火味这下有说法了', { exact: true }).first().waitFor()

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
    throw new Error('Camera permission was granted before the explicit camera enable action.')
  }

  await page.evaluate(() => {
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices)
    globalThis.__advxSmokeOriginalCameraGetUserMedia = original
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      const stream = await original(constraints)
      if (constraints && typeof constraints === 'object' && constraints.video) {
        globalThis.__advxSmokePendingCameraTrack = stream.getVideoTracks()[0]
        await new Promise((resolvePendingCapture) => {
          globalThis.__advxSmokeReleaseCameraCapture = resolvePendingCapture
        })
      }
      return stream
    }
  })
  await page.getByRole('button', { name: '开启摄像头', exact: true }).click()
  await page.waitForFunction(() => globalThis.__advxSmokeReleaseCameraCapture)
  await electronApp.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()
      .find((window) => window.webContents.getURL().includes('/control/'))
      ?.webContents.send('session:emergency-stop')
  })
  await page.evaluate(() => {
    navigator.mediaDevices.getUserMedia = globalThis.__advxSmokeOriginalCameraGetUserMedia
    globalThis.__advxSmokeReleaseCameraCapture?.()
  })
  await page.waitForFunction(
    () => globalThis.__advxSmokePendingCameraTrack?.readyState === 'ended'
  )
  if ((await page.locator('.camera-video').count()) !== 0) {
    throw new Error('A camera stream was published after emergency stop invalidated its request.')
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
  await page.locator('.source-option').first().waitFor()
  const switchSourceCount = await page.locator('.source-option').count()
  await page.locator('.source-option').nth(switchSourceCount > 1 ? 1 : 0).click()
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
      const audioTrack = stream.getAudioTracks()[0]
      if (audioTrack) globalThis.__advxSmokeMicrophoneTrack = audioTrack
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

  await page.getByRole('button', { name: '开启摄像头', exact: true }).click()
  await page.waitForFunction(
    () =>
      document.querySelector('.segmented-control button.active')?.textContent?.trim() ===
        '画中画' && document.querySelectorAll('.video-stage video').length === 2
  )
  const cameraDevices = await page.getByLabel('摄像头').locator('option').count()
  if (cameraDevices < 1) {
    throw new Error('Camera device enumeration returned no entries after explicit permission.')
  }
  await page.locator('.camera-video').evaluate((video) => {
    globalThis.__advxSmokeCameraTrack = video.srcObject?.getVideoTracks()[0]
  })
  if ((await page.evaluate(() => globalThis.__advxSmokeCameraTrack?.readyState)) !== 'live') {
    throw new Error('Expected a live camera track after the explicit enable action.')
  }

  await page.getByRole('button', { name: '屏幕', exact: true }).click()
  await page.waitForFunction(
    () =>
      document.querySelector('.segmented-control button.active')?.textContent?.trim() ===
        '屏幕' && document.querySelectorAll('.video-stage video').length === 1
  )
  await page.waitForFunction(() => globalThis.__advxSmokeCameraTrack?.readyState === 'ended')

  await page.getByRole('button', { name: '摄像头', exact: true }).click()
  await page.waitForFunction(
    () =>
      document.querySelector('.segmented-control button.active')?.textContent?.trim() ===
        '摄像头' && document.querySelectorAll('.video-stage video').length === 1
  )
  await page.locator('.camera-video').evaluate((video) => {
    globalThis.__advxSmokeCameraTrack = video.srcObject?.getVideoTracks()[0]
  })

  await page.getByRole('button', { name: '画中画', exact: true }).click()
  await page.waitForFunction(
    () =>
      document.querySelector('.segmented-control button.active')?.textContent?.trim() ===
        '画中画' && document.querySelectorAll('.video-stage video').length === 2
  )
  await page.locator('.screen-video').evaluate((video) => {
    globalThis.__advxSmokeDisplayTrack = video.srcObject?.getVideoTracks()[0]
  })
  await page.locator('.camera-video').evaluate((video) => {
    globalThis.__advxSmokeCameraTrack = video.srcObject?.getVideoTracks()[0]
  })

  await page.getByLabel('画中画位置').selectOption('top-left')
  await page.getByLabel('画中画尺寸').selectOption('large')
  await page.getByLabel('镜像').check()
  const pipLayout = await page.evaluate(() => {
    const stage = document.querySelector('.video-stage')?.getBoundingClientRect()
    const pip = document.querySelector('.camera-pip')?.getBoundingClientRect()
    if (!stage || !pip) return null
    return {
      leftRatio: (pip.left - stage.left) / stage.width,
      topRatio: (pip.top - stage.top) / stage.height,
      widthRatio: pip.width / stage.width
    }
  })
  if (
    !pipLayout ||
    pipLayout.leftRatio > 0.1 ||
    pipLayout.topRatio > 0.1 ||
    pipLayout.widthRatio < 0.32
  ) {
    throw new Error(`Unexpected top-left large picture-in-picture layout: ${JSON.stringify(pipLayout)}`)
  }

  await page.getByRole('button', { name: '开始直播', exact: true }).click()
  await page.waitForFunction(() => document.body.textContent?.includes('直播中'))
  await page.waitForFunction(
    () => {
      const valueFor = (label) => {
        const row = [...document.querySelectorAll('.mixer-row')].find((candidate) =>
          candidate.querySelector('span')?.textContent?.includes(label)
        )
        return row?.querySelector('strong')?.textContent?.trim() ?? ''
      }
      return (
        valueFor('图像适配器') === '等待后端接入' &&
        valueFor('最近批次') !== '--:--:--' &&
        valueFor('合成压缩').includes('KB')
      )
    },
    undefined,
    { timeout: 15_000 }
  )
  const compressionText = await page
    .locator('.mixer-row')
    .filter({ hasText: '合成压缩' })
    .locator('strong')
    .textContent()
  const compressedKilobytes = Number(compressionText?.match(/([\d.]+) KB/)?.[1] ?? 0)
  if (compressedKilobytes <= 0) {
    throw new Error(`Composite JPEG size was not reported: ${compressionText}`)
  }
  await page.screenshot({
    path: resolve(artifactDirectory, 'views-camera-pip.png'),
    fullPage: true
  })
  await electronApp.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()
      .find((window) => window.webContents.getURL().includes('/control/'))
      ?.setSize(1120, 720)
  })
  await page.waitForTimeout(180)
  const compactVisualLayout = await page.evaluate(() => {
    const rectangle = (selector) => document.querySelector(selector)?.getBoundingClientRect()
    const toolbar = rectangle('.visual-toolbar')
    const stage = rectangle('.video-stage')
    const command = rectangle('.command-bar')
    const pip = rectangle('.camera-pip')
    return {
      documentOverflow: document.documentElement.scrollWidth > window.innerWidth,
      ordered:
        Boolean(toolbar && stage && command) &&
        toolbar.bottom <= stage.top + 1 &&
        stage.bottom <= command.top + 1,
      pipContained:
        Boolean(stage && pip) &&
        pip.left >= stage.left &&
        pip.top >= stage.top &&
        pip.right <= stage.right &&
        pip.bottom <= stage.bottom
    }
  })
  if (
    compactVisualLayout.documentOverflow ||
    !compactVisualLayout.ordered ||
    !compactVisualLayout.pipContained
  ) {
    throw new Error(
      `Visual controls overlapped at 1120x720: ${JSON.stringify(compactVisualLayout)}`
    )
  }
  await page.screenshot({
    path: resolve(artifactDirectory, 'views-camera-pip-1120.png'),
    fullPage: true
  })
  await electronApp.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()
      .find((window) => window.webContents.getURL().includes('/control/'))
      ?.setSize(1440, 900)
  })
  await page.waitForTimeout(180)

  await page.locator('.screen-video').evaluate((video) => {
    const track = video.srcObject?.getVideoTracks()[0]
    track?.stop()
    track?.dispatchEvent(new Event('ended'))
  })
  await page.waitForFunction(
    () =>
      document.querySelector('.segmented-control button.active')?.textContent?.trim() ===
        '摄像头' && document.querySelectorAll('.video-stage video').length === 1
  )
  await page.getByRole('button', { name: '画中画', exact: true }).click()
  await page.waitForFunction(() => document.querySelectorAll('.video-stage video').length === 2)
  await page.locator('.screen-video').evaluate((video) => {
    globalThis.__advxSmokeDisplayTrack = video.srcObject?.getVideoTracks()[0]
  })

  await page.locator('.camera-video').evaluate((video) => {
    const track = video.srcObject?.getVideoTracks()[0]
    track?.stop()
    track?.dispatchEvent(new Event('ended'))
  })
  await page.waitForFunction(
    () =>
      document.querySelector('.segmented-control button.active')?.textContent?.trim() ===
        '屏幕' && document.querySelectorAll('.video-stage video').length === 1
  )
  await page.getByRole('button', { name: '开启摄像头', exact: true }).click()
  await page.waitForFunction(() => document.querySelectorAll('.video-stage video').length === 2)
  await page.locator('.camera-video').evaluate((video) => {
    globalThis.__advxSmokeCameraTrack = video.srcObject?.getVideoTracks()[0]
  })

  await page.getByPlaceholder('说点什么，AI 观众会回应你').fill('这下真有说法了')
  await page.getByRole('button', { name: '发送' }).click()
  await page.waitForTimeout(100)
  if (
    await page
      .locator('.chat-item.audience')
      .filter({ hasText: '这下真有说法了' })
      .count()
  ) {
    throw new Error('A MemeCandidate bypassed the audience barrage pipeline.')
  }

  await page.getByRole('button', { name: /AI 观众/ }).click()
  await page.getByRole('heading', { name: 'AI 观众', exact: true }).waitFor()
  const liveEditPolicy = {
    modeLocked: await page.getByLabel('观众模式').isDisabled(),
    duplicateLocked: await page.getByRole('button', { name: '复制为自定义模式' }).isDisabled(),
    personaSaveLocked: await page.getByRole('button', { name: '保存覆盖' }).isDisabled(),
    activityEditable: await page.locator('.aw-range-control input').first().isEnabled(),
    participationEditable: await page.locator('.aw-persona-row .aw-switch input').first().isEnabled()
  }
  if (
    !liveEditPolicy.modeLocked ||
    !liveEditPolicy.duplicateLocked ||
    !liveEditPolicy.personaSaveLocked ||
    !liveEditPolicy.activityEditable ||
    !liveEditPolicy.participationEditable
  ) {
    throw new Error(`Unexpected live audience edit policy: ${JSON.stringify(liveEditPolicy)}`)
  }
  await page.getByRole('button', { name: '成长梗库', exact: true }).click()
  if (await page.getByRole('button', { name: '手动新增梗' }).isDisabled()) {
    throw new Error('The active mode meme library should remain editable while live.')
  }
  await page.getByText('这下真有说法了', { exact: true }).first().click()
  await page.getByRole('button', { name: '撤销自动梗' }).click()
  await page.getByText('这下真有说法了', { exact: true }).waitFor({ state: 'detached' })
  await page.getByRole('button', { name: '直播控制台', exact: true }).click()

  await page.getByRole('button', { name: '暂停', exact: true }).click()
  await page.waitForFunction(() => document.body.textContent?.includes('屏幕 已暂停'))
  if ((await page.locator('video').count()) !== 0) {
    throw new Error('A visual preview remained live after pause.')
  }
  const pausedDisplayTrackState = await page.evaluate(
    () => globalThis.__advxSmokeDisplayTrack?.readyState
  )
  if (pausedDisplayTrackState !== 'ended') {
    throw new Error(`Display track was not released on pause: ${pausedDisplayTrackState}.`)
  }
  const pausedCameraTrackState = await page.evaluate(
    () => globalThis.__advxSmokeCameraTrack?.readyState
  )
  if (pausedCameraTrackState !== 'ended') {
    throw new Error(`Camera track was not released on pause: ${pausedCameraTrackState}.`)
  }
  const pausedMicrophoneTrackState = await page.evaluate(
    () => globalThis.__advxSmokeMicrophoneTrack?.readyState
  )
  if (pausedMicrophoneTrackState !== 'ended') {
    throw new Error(`Microphone track was not released on pause: ${pausedMicrophoneTrackState}.`)
  }

  await page.getByRole('button', { name: '恢复', exact: true }).click()
  await page.waitForFunction(() => document.querySelectorAll('.video-stage video').length === 2)
  await page.waitForFunction(() => document.body.textContent?.includes('直播中'))
  await page.locator('.screen-video').evaluate((video) => {
    globalThis.__advxSmokeDisplayTrack = video.srcObject?.getVideoTracks()[0]
  })
  await page.locator('.camera-video').evaluate((video) => {
    globalThis.__advxSmokeCameraTrack = video.srcObject?.getVideoTracks()[0]
  })
  await page.waitForFunction(() => globalThis.__advxSmokeMicrophoneTrack?.readyState === 'live')

  await page.getByRole('button', { name: '关闭摄像头', exact: true }).click()
  await page.waitForFunction(() => globalThis.__advxSmokeCameraTrack?.readyState === 'ended')
  await page.waitForFunction(
    () =>
      document.querySelector('.segmented-control button.active')?.textContent?.trim() ===
      '屏幕'
  )
  await page.getByRole('button', { name: '开启摄像头', exact: true }).click()
  await page.waitForFunction(() => document.querySelectorAll('.video-stage video').length === 2)
  await page.locator('.camera-video').evaluate((video) => {
    globalThis.__advxSmokeCameraTrack = video.srcObject?.getVideoTracks()[0]
  })

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
  const stoppedCameraTrackState = await page.evaluate(
    () => globalThis.__advxSmokeCameraTrack?.readyState
  )
  if (stoppedCameraTrackState !== 'ended') {
    throw new Error(`Camera track was not released on stop: ${stoppedCameraTrackState}.`)
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

  await page.reload()
  await page.getByRole('heading', { name: '直播控制台', exact: true }).waitFor()
  if (
    (await page.getByLabel('画中画位置').inputValue()) !== 'top-left' ||
    (await page.getByLabel('画中画尺寸').inputValue()) !== 'large' ||
    !(await page.getByLabel('镜像').isChecked())
  ) {
    throw new Error('Versioned visual settings were not restored after reload.')
  }
  await page.getByRole('button', { name: '开启摄像头', exact: true }).waitFor()
  if ((await page.locator('.video-stage video').count()) !== 0) {
    throw new Error('Camera or display tracks were restored from disk after reload.')
  }
  const postGrantCameraPermission = await page.evaluate(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true })
      stream.getTracks().forEach((track) => track.stop())
      return 'granted'
    } catch (error) {
      return error instanceof DOMException ? error.name : 'rejected'
    }
  })
  if (postGrantCameraPermission === 'granted') {
    throw new Error('Camera permission remained open without a fresh single-use authorization.')
  }

  await page.getByRole('button', { name: /AI 观众/ }).click()
  await page.getByLabel('观众模式').selectOption('room-6657')
  await page.locator('.aw-range-control input').first().fill('7')

  console.log(
    `Monorepo desktop smoke passed: ${sourceCount} sources, six audience modes, 32 personas, live edit policy, meme candidate ingestion/undo, camera denied before explicit enable, ${cameraDevices} camera entries, three visual modes, ${compressedKilobytes} KB composite JPEG, versioned settings restore, microphone meter peak ${microphonePeak}%, and complete pause/stop track cleanup.`
  )
  console.log(`Screenshot: ${resolve(artifactDirectory, 'control-console.png')}`)
  console.log(`Camera picture-in-picture: ${resolve(artifactDirectory, 'views-camera-pip.png')}`)
  console.log(
    `Compact camera picture-in-picture: ${resolve(artifactDirectory, 'views-camera-pip-1120.png')}`
  )
} finally {
  await electronApp.close()
}

const audienceWorkspaceFile = resolve(smokeUserDataDirectory, 'audience-workspace.json')
const flushedWorkspace = JSON.parse(await readFile(audienceWorkspaceFile, 'utf8'))
const flushed6657 = flushedWorkspace.modeState.modes.find((mode) => mode.id === 'room-6657')
if (flushed6657?.baseActivity?.[0] !== 7) {
  throw new Error('The close handshake did not flush the latest audience workspace edit.')
}

const personaDocument = await readFile(
  resolve(
    smokeUserDataDirectory,
    'audience-modes',
    'room-6657',
    'personas',
    'instigator',
    'personality.md'
  ),
  'utf8'
)
if (!personaDocument.includes('串子哥')) {
  throw new Error('The mode-specific personality.md file was not materialized.')
}
if (!personaDocument.includes('"version": 1')) {
  throw new Error('The materialized personality.md file has no supported format version.')
}

const rejectedWorkspace = `${JSON.stringify({ version: 2, future: true }, null, 2)}\n`
await writeFile(audienceWorkspaceFile, rejectedWorkspace, 'utf8')
const recoveryApp = await electron.launch({
  args: ['.', `--user-data-dir=${smokeUserDataDirectory}`, '--use-fake-device-for-media-stream'],
  cwd: root,
  env: {
    ...electronEnvironment,
    ELECTRON_DISABLE_SECURITY_WARNINGS: 'true'
  }
})
try {
  const recoveryPage = await recoveryApp.firstWindow()
  await recoveryPage.getByRole('heading', { name: '直播控制台', exact: true }).waitFor()
  await recoveryPage.getByRole('button', { name: /AI 观众/ }).click()
  await recoveryPage.getByText('本地配置已保护', { exact: true }).waitFor()
  await recoveryPage.waitForTimeout(700)
  if ((await readFile(audienceWorkspaceFile, 'utf8')) !== rejectedWorkspace) {
    throw new Error('A rejected audience workspace was overwritten by automatic persistence.')
  }
} finally {
  await recoveryApp.close()
}
if ((await readFile(audienceWorkspaceFile, 'utf8')) !== rejectedWorkspace) {
  throw new Error('A rejected audience workspace was overwritten during application close.')
}
const rejectedCopies = (await readdir(smokeUserDataDirectory)).filter((name) =>
  /^audience-workspace\.rejected-[a-f0-9]{12}\.json$/.test(name)
)
if (rejectedCopies.length !== 1) {
  throw new Error(`Expected one content-addressed rejected workspace copy, found ${rejectedCopies.length}.`)
}
console.log(`Audience screenshot: ${resolve(artifactDirectory, 'audience-workspace-1120.png')}`)
console.log(`Persona screenshot: ${resolve(artifactDirectory, 'audience-personas-1120.png')}`)
