import assert from 'node:assert/strict'
import { once } from 'node:events'
import { mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:net'
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

function reservePort() {
  return new Promise((resolvePort, reject) => {
    const server = createServer()
    server.unref()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      assert.ok(address && typeof address === 'object')
      server.close((error) => (error ? reject(error) : resolvePort(address.port)))
    })
  })
}

const backendPort = await reservePort()
const backendEnvironment = {
  ADVX_BACKEND_EXTERNAL: '0',
  ADVX_BACKEND_URL: `http://127.0.0.1:${backendPort}`
}

function launchApp() {
  return electron.launch({
    args: [
      '.',
      `--user-data-dir=${smokeUserDataDirectory}`,
      '--use-fake-device-for-media-stream'
    ],
    cwd: root,
    env: {
      ...electronEnvironment,
      ...backendEnvironment,
      ELECTRON_DISABLE_SECURITY_WARNINGS: 'true'
    }
  })
}

async function closeControlWindowAndWaitForExit(electronApp) {
  const electronProcess = electronApp.process()
  const exited =
    electronProcess.exitCode === null ? once(electronProcess, 'exit') : Promise.resolve()
  await electronApp.evaluate(({ BrowserWindow }) => {
    const controlWindow = BrowserWindow.getAllWindows().find((window) =>
      window.webContents.getURL().includes('/control/')
    )
    if (!controlWindow) throw new Error('Control window was not found during shutdown smoke.')
    controlWindow.close()
  })

  let timeout
  try {
    await Promise.race([
      exited,
      new Promise((_, reject) => {
        timeout = setTimeout(
          () => reject(new Error('Electron did not exit after its control window closed.')),
          8_000
        )
      })
    ])
  } finally {
    clearTimeout(timeout)
  }
}

async function setRange(page, label, value) {
  const input = page.getByLabel(label, { exact: true })
  await input.fill(String(value))
  await input.evaluate((element) => {
    element.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

let electronApp = await launchApp()
let proof

try {
  const page = await electronApp.firstWindow()
  await page.waitForSelector('h1')

  const title = await page.locator('h1').textContent()
  assert.equal(title?.trim(), '直播控制台', `Unexpected initial view: ${title}`)

  await page.screenshot({
    path: resolve(artifactDirectory, 'control-console.png'),
    fullPage: true
  })

  await page.waitForFunction(() => document.body.textContent?.includes('后端 · 已连接'))
  await page.getByRole('button', { name: '设置', exact: true }).click()
  await page.getByLabel('服务地址', { exact: true }).fill('https://smoke.example/v1')
  await page.getByLabel('模型名称', { exact: true }).fill('smoke-model')
  await page.getByLabel('模型 API Key', { exact: true }).fill('smoke-model-key')
  await page.getByLabel('StepFun ASR API Key', { exact: true }).fill('smoke-asr-key')
  await page.getByRole('button', { name: '保存连接', exact: true }).click()
  await page.getByText('模型与语音识别配置已安全保存并接入后端', { exact: true }).waitFor()

  const modelApiKeyInput = page.getByLabel(/模型 API Key/)
  const asrApiKeyInput = page.getByLabel(/StepFun ASR API Key/)
  assert.equal(await modelApiKeyInput.inputValue(), '')
  assert.equal(await asrApiKeyInput.inputValue(), '')
  assert.match((await modelApiKeyInput.getAttribute('placeholder')) ?? '', /已保存/)
  assert.match((await asrApiKeyInput.getAttribute('placeholder')) ?? '', /已保存/)
  assert.equal(await page.getByText('已安全保存', { exact: true }).count(), 2)

  const saveChangesButton = page.getByRole('button', { name: '保存更改', exact: true })
  assert.equal(await saveChangesButton.isEnabled(), true)
  await saveChangesButton.click()
  await page.getByText('模型与语音识别配置已安全保存并接入后端', { exact: true }).waitFor()
  await page.screenshot({
    path: resolve(artifactDirectory, 'model-config-saved.png'),
    fullPage: true
  })

  await page.getByRole('button', { name: /AI 观众/ }).click()
  await page.getByRole('heading', { name: 'AI 观众', exact: true }).waitFor()
  const modeSelect = page.getByLabel('观众模式')
  if ((await modeSelect.locator('option').count()) !== 6) {
    throw new Error('Expected six built-in audience modes.')
  }
  if ((await page.locator('[data-audience-persona-row]').count()) !== 32) {
    throw new Error('Expected the complete 32-persona catalog.')
  }
  await modeSelect.selectOption('room-6657')
  await page.waitForFunction(
    () =>
      document.querySelector('[data-audience-mode-copy] strong')?.textContent ===
      '6657 玩机器风格'
  )
  const modeRanges = await page.locator('[data-audience-range] input').evaluateAll((inputs) =>
    inputs.map((input) => input.value)
  )
  if (modeRanges.join(',') !== '6,10,20,28') {
    throw new Error(`Unexpected 6657 activity ranges: ${modeRanges.join(',')}`)
  }

  await page.getByRole('button', { name: '成长梗库', exact: true }).click()
  await page.getByText(
    '开始直播并连接后端后，可管理当前房间的长期记忆与梗库。',
    { exact: true }
  ).waitFor()
  assert.equal(
    await page.getByRole('button', { name: '手动新增梗' }).count(),
    0,
    'The removed local meme editor leaked back into the control surface.'
  )

  await electronApp.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()
      .find((window) => window.webContents.getURL().includes('/control/'))
      ?.setSize(1120, 720)
  })
  await page.waitForTimeout(150)
  const audienceOverflow = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth > window.innerWidth,
    workspaceMetrics: (() => {
      const workspace = document.querySelector('[data-audience-workspace]')
      return workspace
        ? {
            clientWidth: workspace.clientWidth,
            scrollWidth: workspace.scrollWidth,
            offsetWidth: workspace.offsetWidth
          }
        : null
    })(),
    workspace:
      (document.querySelector('[data-audience-workspace]')?.scrollWidth ?? 0) >
      (document.querySelector('[data-audience-workspace]')?.clientWidth ?? 0),
    offenders: [...document.querySelectorAll('[data-audience-workspace] *')]
      .filter((element) => {
        const workspace = document.querySelector('[data-audience-workspace]')?.getBoundingClientRect()
        const bounds = element.getBoundingClientRect()
        return element.scrollWidth > element.clientWidth + 1 || Boolean(workspace && bounds.right > workspace.right + 1)
      })
      .slice(0, 8)
      .map((element) => ({
        tag: element.tagName,
        className: element.className,
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
        right: Math.round(element.getBoundingClientRect().right)
      }))
  }))
  if (audienceOverflow.document || audienceOverflow.workspace) {
    throw new Error(`Audience workspace overflowed at 1120x720: ${JSON.stringify(audienceOverflow)}`)
  }
  await page.screenshot({
    path: resolve(artifactDirectory, 'audience-workspace-1120.png')
  })
  await page.getByRole('button', { name: '人格阵容', exact: true }).click()
  await page.locator('[data-audience-persona-open]').first().click()
  await page.getByRole('dialog', { name: /编辑/ }).waitFor()
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
      workspace: metrics('[data-control-workspace]'),
      audience: metrics('[data-audience-workspace]'),
      layout: metrics('[data-audience-persona-layout]'),
      editor: metrics('[data-audience-persona-editor]')
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
  assert.equal(await page.getByRole('button', { name: '手动新增梗' }).count(), 0)

  await page.getByRole('button', { name: '设置', exact: true }).click()
  await page.getByRole('heading', { name: '弹幕覆盖层', exact: true }).waitFor()
  await page.getByLabel('弹幕目标', { exact: true }).waitFor()
  assert.equal(await page.getByLabel('点击穿透', { exact: true }).count(), 0)
  const controlAlwaysOnTop = await electronApp.evaluate(({ BrowserWindow }) => {
    const controlWindow = BrowserWindow.getAllWindows().find((window) =>
      window.webContents.getURL().includes('/control/')
    )
    if (!controlWindow) throw new Error('Control window is missing.')
    return controlWindow.isAlwaysOnTop()
  })
  assert.equal(controlAlwaysOnTop, false, 'Overlay settings must not elevate the control window.')

  const defaultOverlaySettings = await page.evaluate(() => window.advx.getOverlaySettings())
  assert.deepEqual(
    {
      fontSizePx: defaultOverlaySettings.fontSizePx,
      fontFamily: defaultOverlaySettings.fontFamily,
      bold: defaultOverlaySettings.bold,
      outlineWidthPx: defaultOverlaySettings.outlineWidthPx,
      speed: defaultOverlaySettings.speed,
      opacity: defaultOverlaySettings.opacity,
      density: defaultOverlaySettings.density,
      region: defaultOverlaySettings.region
    },
    {
      fontSizePx: 25,
      fontFamily: 'bilibili',
      bold: true,
      outlineWidthPx: 1,
      speed: 75,
      opacity: 80,
      density: 6,
      region: { topPercent: 0, bottomPercent: 50 }
    }
  )

  const targetOptions = await page.getByLabel('弹幕目标', { exact: true }).locator('option').count()
  assert.ok(targetOptions >= 1, 'Overlay target IPC returned no displays.')
  if (targetOptions > 1) {
    const targetSelect = page.getByLabel('弹幕目标', { exact: true })
    const currentTargetId = await targetSelect.inputValue()
    const alternateTargetId = Number(
      (
        await targetSelect.locator('option').evaluateAll((options) =>
          options.map((option) => option.value)
        )
      ).find((value) => value !== currentTargetId)
    )
    await targetSelect.selectOption(String(alternateTargetId))
    await page.waitForFunction(
      async (targetDisplayId) =>
        (await window.advx.getOverlaySettings()).targetDisplayId === targetDisplayId,
      alternateTargetId
    )
  }

  await page.getByLabel('弹幕字体', { exact: true }).selectOption('system')
  await page.getByLabel('粗体', { exact: true }).uncheck()
  await setRange(page, '字号', 30)
  await setRange(page, '描边粗细', 2)
  await setRange(page, '移动速度', 100)
  await setRange(page, '透明度', 55)
  await setRange(page, '密度', 3)
  await setRange(page, '显示区域顶部', 20)
  await setRange(page, '显示区域底部', 60)
  await page.getByText('已同步', { exact: true }).waitFor()

  const configuredSettings = await page.evaluate(() => window.advx.getOverlaySettings())
  assert.deepEqual(
    {
      fontSizePx: configuredSettings.fontSizePx,
      fontFamily: configuredSettings.fontFamily,
      bold: configuredSettings.bold,
      outlineWidthPx: configuredSettings.outlineWidthPx,
      speed: configuredSettings.speed,
      opacity: configuredSettings.opacity,
      density: configuredSettings.density,
      region: configuredSettings.region
    },
    {
      fontSizePx: 30,
      fontFamily: 'system',
      bold: false,
      outlineWidthPx: 2,
      speed: 100,
      opacity: 55,
      density: 3,
      region: { topPercent: 20, bottomPercent: 60 }
    }
  )

  await page.screenshot({
    path: resolve(artifactDirectory, 'overlay-settings.png'),
    fullPage: true
  })
  await electronApp.evaluate(({ BrowserWindow }) => {
    const controlWindow = BrowserWindow.getAllWindows().find((window) =>
      window.webContents.getURL().replaceAll('\\', '/').endsWith('/control/index.html')
    )
    controlWindow?.setSize(1_120, 720)
  })
  await page.waitForTimeout(250)
  const compactOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  )
  assert.ok(compactOverflow <= 1, 'Overlay settings overflowed the minimum desktop width.')
  await page.screenshot({
    path: resolve(artifactDirectory, 'overlay-settings-compact.png'),
    fullPage: true
  })

  await page.getByRole('button', { name: '直播控制台', exact: true }).click()
  await page.getByRole('button', { name: '更换来源', exact: true }).waitFor()
  await page.locator('.screen-video').waitFor()
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

  await page.getByRole('button', { name: '更换来源', exact: true }).click()
  await page.locator('[data-source-option]').first().waitFor()
  const sourceCount = await page.locator('[data-source-option]').count()
  assert.ok(sourceCount >= 1, 'Desktop source IPC returned no sources.')
  await page.getByTitle('关闭').click()
  await page.getByRole('button', { name: '设置', exact: true }).click()

  await page.evaluate(async () => {
    await window.advx.showOverlay()
    for (let index = 0; index < 8; index += 1) {
      await window.advx.pushBarrage({
        barrageId: `smoke-${index}`,
        audienceId: `audience-${index}`,
        audienceName: `测试观众 ${index + 1}`,
        text: `Overlay 参数验证弹幕 ${index + 1}`,
        color: index % 2 === 0 ? '#a8f53a' : '#65d6b9',
        createdAt: Date.now(),
        mode: 'scroll'
      })
    }
  })

  let overlayPage = electronApp
    .windows()
    .find((candidate) => candidate.url().replaceAll('\\', '/').endsWith('/overlay/index.html'))
  overlayPage ??= await electronApp.waitForEvent('window', {
    predicate: (candidate) =>
      candidate.url().replaceAll('\\', '/').endsWith('/overlay/index.html')
  })
  await overlayPage.locator('.overlay-barrage').first().waitFor()

  const rendered = await overlayPage.evaluate(() => {
    const root = document.querySelector('.overlay-root')
    const items = [...document.querySelectorAll('.overlay-barrage')]
    const first = items[0]
    if (!(root instanceof HTMLElement) || !(first instanceof HTMLElement)) return null
    const rootStyle = getComputedStyle(root)
    const itemStyle = getComputedStyle(first)
    return {
      count: items.length,
      rootHeight: root.getBoundingClientRect().height,
      fontSize: itemStyle.fontSize,
      opacity: itemStyle.opacity,
      animationDuration: itemStyle.animationDuration,
      backgroundColor: itemStyle.backgroundColor,
      borderLeftWidth: itemStyle.borderLeftWidth,
      borderRadius: itemStyle.borderRadius,
      boxShadow: itemStyle.boxShadow,
      color: itemStyle.color,
      fontFamily: itemStyle.fontFamily,
      fontWeight: itemStyle.fontWeight,
      lineHeight: itemStyle.lineHeight,
      padding: itemStyle.padding,
      textShadow: itemStyle.textShadow,
      textContent: first.textContent,
      identityNodeCount: document.querySelectorAll('.overlay-name, .ai-watermark, img').length,
      regionTop: rootStyle.getPropertyValue('--overlay-region-top').trim(),
      regionBottom: rootStyle.getPropertyValue('--overlay-region-bottom').trim(),
      itemRects: items.map((item) => {
        const rect = item.getBoundingClientRect()
        return { top: rect.top, bottom: rect.bottom }
      })
    }
  })
  assert.ok(rendered, 'Overlay styles were not readable.')
  assert.equal(rendered.count, 3, 'Density did not cap the visible barrage count.')
  assert.equal(rendered.fontSize, '30px')
  assert.equal(rendered.opacity, '0.55')
  assert.equal(rendered.animationDuration, '5s')
  assert.equal(rendered.backgroundColor, 'rgba(0, 0, 0, 0)')
  assert.equal(rendered.borderLeftWidth, '0px')
  assert.equal(rendered.borderRadius, '0px')
  assert.equal(rendered.boxShadow, 'none')
  assert.equal(rendered.color, 'rgb(255, 255, 255)')
  assert.match(rendered.fontFamily, /Segoe UI/)
  assert.equal(rendered.fontWeight, '400')
  assert.equal(rendered.lineHeight, '33.75px')
  assert.equal(rendered.padding, '0px')
  assert.match(rendered.textShadow, /rgb\(0, 0, 0\)/)
  assert.match(rendered.textShadow, /2px/)
  assert.equal(rendered.textContent, 'Overlay 参数验证弹幕 6')
  assert.equal(rendered.identityNodeCount, 0)
  assert.equal(rendered.regionTop, '20%')
  assert.equal(rendered.regionBottom, '60%')
  const sortedRects = [...rendered.itemRects].sort((left, right) => left.top - right.top)
  for (let index = 1; index < sortedRects.length; index += 1) {
    assert.ok(
      sortedRects[index - 1].bottom <= sortedRects[index].top + 1,
      'Barrage lanes overlapped vertically.'
    )
  }
  assert.ok(
    sortedRects.every(
      (rect) =>
        rect.top >= rendered.rootHeight * 0.2 - 1 &&
        rect.bottom <= rendered.rootHeight * 0.6 + 1
    ),
    'A barrage escaped the configured display region.'
  )

  const boundsProof = await electronApp.evaluate(
    ({ BrowserWindow, screen }, targetDisplayId) => {
      const overlayWindow = BrowserWindow.getAllWindows().find((window) =>
        window.webContents.getURL().replaceAll('\\', '/').endsWith('/overlay/index.html')
      )
      const target = screen.getAllDisplays().find((display) => display.id === targetDisplayId)
      return {
        overlay: overlayWindow?.getBounds(),
        target: target?.bounds
      }
    },
    configuredSettings.targetDisplayId
  )
  assert.ok(boundsProof.overlay && boundsProof.target, 'Overlay target bounds were unavailable.')
  if (process.env.ADVX_SMOKE_SKIP_DISPLAY_BOUNDS !== '1') {
    for (const key of ['x', 'y', 'width', 'height']) {
      assert.ok(
        Math.abs(boundsProof.overlay[key] - boundsProof.target[key]) <= 1,
        `Overlay ${key} did not match its target within one DIP.`
      )
    }
  }

  await page.evaluate(() => window.advx.clearOverlay())
  await page.getByRole('button', { name: '直播控制台', exact: true }).click()
  await page.getByRole('button', { name: '更换来源', exact: true }).click()
  await page.locator('[data-source-option]').first().waitFor()

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
  await page.locator('[data-source-option]').first().click()
  await page.getByRole('button', { name: '使用此来源', exact: true }).click()
  await page.waitForFunction(() => globalThis.__advxSmokeReleaseDisplayCapture)
  await electronApp.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()
      .find((window) => window.webContents.getURL().includes('/control/'))
      ?.webContents.send('session:emergency-stop')
  })
  await page.waitForFunction(() => {
    const button = [...document.querySelectorAll('button')].find(
      (candidate) => candidate.textContent?.trim() === '更换来源'
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
  if ((await page.locator('.video-stage video').count()) !== 0) {
    throw new Error('A display stream was published after emergency stop invalidated its request.')
  }

  await page.getByRole('button', { name: '更换来源', exact: true }).click()
  await page.locator('[data-source-option]').first().click()
  await page.getByRole('button', { name: '使用此来源', exact: true }).click()
  try {
    await page.locator('.screen-video').waitFor({ timeout: 15_000 })
  } catch (error) {
    console.error(`Control surface after display capture failure:\n${await page.locator('body').innerText()}`)
    throw error
  }
  const displayTrackState = await page.locator('.screen-video').evaluate((video) => {
    const stream = video.srcObject
    return stream instanceof MediaStream ? stream.getVideoTracks()[0]?.readyState : undefined
  })
  if (displayTrackState !== 'live') {
    throw new Error(`Expected a live display track, received ${displayTrackState ?? 'none'}.`)
  }
  await page.locator('.screen-video').evaluate((video) => {
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
  await page.locator('[data-source-option]').first().waitFor()
  const switchSourceCount = await page.locator('[data-source-option]').count()
  await page.locator('[data-source-option]').nth(switchSourceCount > 1 ? 1 : 0).click()
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
  await page.locator('.video-stage video').waitFor({ state: 'detached' })
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

  await electronApp.evaluate(({ BrowserWindow, ipcMain }) => {
    const channels = [
      'backend:get-status',
      'backend:restart',
      'backend:session-start',
      'backend:session-pause',
      'backend:session-resume',
      'backend:session-stop',
      'backend:submit-text',
      'backend:submit-audio',
      'backend:submit-frame'
    ]
    channels.forEach((channel) => ipcMain.removeHandler(channel))

    let state = 'idle'
    let sessionId = null
    let startedAtMs = null
    let revision = 0
    let connection = 'failed'
    let startupError = 'Smoke 模拟的后端启动失败'
    const sessionSnapshot = () => ({
      sessionId,
      state,
      startedAtMs,
      updatedAtMs: Date.now(),
      revision
    })
    const runtimeStatus = () => ({
      connection,
      providersConfigured: connection === 'connected',
      startupError,
      recoverableRuntimeSessionId: null,
      session: sessionSnapshot()
    })
    const publishStatus = () => {
      BrowserWindow.getAllWindows()
        .find((window) => window.webContents.getURL().includes('/control/'))
        ?.webContents.send('backend:status', runtimeStatus())
    }
    const transition = (nextState) => {
      state = nextState
      revision += 1
      if (nextState === 'running' && sessionId === null) {
        sessionId = 'smoke-session'
        startedAtMs = Date.now()
      }
      if (nextState === 'idle') {
        sessionId = null
        startedAtMs = null
      }
      publishStatus()
      return sessionSnapshot()
    }

    ipcMain.handle('backend:get-status', runtimeStatus)
    ipcMain.handle('backend:restart', () => {
      connection = 'connected'
      startupError = null
      publishStatus()
      return runtimeStatus()
    })
    ipcMain.handle('backend:session-start', (_event, workspace, clientRequestId) => {
      if (!workspace || typeof workspace !== 'object') {
        throw new Error('Smoke expected the current workspace-first session start signature.')
      }
      if (typeof clientRequestId !== 'string' || !clientRequestId.trim()) {
        throw new Error('Smoke expected a non-empty client request ID when starting a session.')
      }
      return transition('running')
    })
    ipcMain.handle('backend:session-pause', () => transition('paused'))
    ipcMain.handle('backend:session-resume', () => transition('running'))
    ipcMain.handle('backend:session-stop', () => transition('idle'))
    ipcMain.handle('backend:submit-text', () => undefined)
    ipcMain.handle('backend:submit-audio', () => undefined)
    ipcMain.handle('backend:submit-frame', () => undefined)
    publishStatus()
  })
  await page.getByText('本地服务启动失败', { exact: true }).waitFor()
  await page.getByRole('button', { name: '重试', exact: true }).click()
  await page.waitForFunction(() => document.body.textContent?.includes('后端 · 已连接'))

  await page.getByRole('button', { name: '开始直播', exact: true }).click()
  await page.waitForFunction(() => document.body.textContent?.includes('直播中'))
  const smokeBarrageText = 'Smoke test-only 实时弹幕'
  // Test-only renderer event: this proves UI fan-out only. runtime-smoke owns backend linkage proof.
  await electronApp.evaluate(({ BrowserWindow }, event) => {
    BrowserWindow.getAllWindows()
      .find((window) => window.webContents.getURL().includes('/control/'))
      ?.webContents.send('backend:barrage', event)
  }, {
    barrageId: 'smoke-test-only-runtime-barrage',
    audienceId: 'smoke-viewer-1',
    audienceName: 'Smoke 观众',
    text: smokeBarrageText,
    createdAt: Date.now(),
    roomId: 'smoke-room',
    sessionId: 'smoke-session',
    audienceEpoch: 1,
    observationId: 'smoke-observation-1',
    generationRequestId: 'smoke-generation-1',
    viewerInstanceId: 'smoke-viewer-1',
    personaId: 'reaction_qmark',
    viewerSequence: 1,
    reactionType: 'smoke',
    evidenceRefs: [],
    expiresAt: Date.now() + 10_000
  })
  await page.getByText(smokeBarrageText, { exact: true }).waitFor()
  await overlayPage.getByText(smokeBarrageText, { exact: true }).waitFor()
  await page.waitForFunction(
    () => {
      const valueFor = (label) => {
        const row = [...document.querySelectorAll('.mixer-row')].find((candidate) =>
          candidate.querySelector('span')?.textContent?.includes(label)
        )
        return row?.querySelector('strong')?.textContent?.trim() ?? ''
      }
      return (
        valueFor('图像适配器') === '已就绪' &&
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
  await page.locator('[data-audience-persona-open]').first().click()
  await page.getByRole('dialog', { name: /编辑/ }).waitFor()
  const liveEditPolicy = {
    modeEditable: await page.getByLabel('观众模式').isEnabled(),
    duplicateEnabled: await page.getByRole('button', { name: '复制为自定义模式' }).isEnabled(),
    personaSaveEnabled: await page.getByRole('button', { name: '保存覆盖' }).isEnabled(),
    activityEditable: await page.locator('[data-audience-range] input').first().isEnabled(),
    participationEditable: await page
      .locator('[data-audience-persona-row] [data-audience-participation] input')
      .first()
      .isEnabled()
  }
  if (
    !liveEditPolicy.modeEditable ||
    !liveEditPolicy.duplicateEnabled ||
    !liveEditPolicy.personaSaveEnabled ||
    !liveEditPolicy.activityEditable ||
    !liveEditPolicy.participationEditable
  ) {
    throw new Error(`Unexpected live audience edit policy: ${JSON.stringify(liveEditPolicy)}`)
  }
  await page.getByRole('button', { name: '直播控制台', exact: true }).click()

  await page.getByRole('button', { name: '暂停', exact: true }).click()
  await page.waitForFunction(() => document.body.textContent?.includes('观察已暂停'))
  await page.waitForFunction(
    () =>
      document.body.textContent?.includes('屏幕 预览中') &&
      document.body.textContent?.includes('摄像头 预览中')
  )
  if ((await page.locator('.video-stage video').count()) !== 2) {
    throw new Error('Visual preview was not kept live while observation was paused.')
  }
  const pausedDisplayTrackState = await page.evaluate(
    () => globalThis.__advxSmokeDisplayTrack?.readyState
  )
  if (pausedDisplayTrackState !== 'live') {
    throw new Error(`Display track stopped on pause: ${pausedDisplayTrackState}.`)
  }
  const pausedCameraTrackState = await page.evaluate(
    () => globalThis.__advxSmokeCameraTrack?.readyState
  )
  if (pausedCameraTrackState !== 'live') {
    throw new Error(`Camera track stopped on pause: ${pausedCameraTrackState}.`)
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
  try {
    await page.waitForFunction(() => {
      const screen = document.querySelector('.screen-video')
      const camera = document.querySelector('.camera-video')
      return (
        screen instanceof HTMLVideoElement &&
        camera instanceof HTMLVideoElement &&
        screen.srcObject?.getVideoTracks()[0]?.readyState === 'live' &&
        camera.srcObject?.getVideoTracks()[0]?.readyState === 'live'
      )
    })
  } catch (error) {
    const resumeDiagnostics = await page.evaluate(() => {
      const describe = (selector) => {
        const video = document.querySelector(selector)
        return video instanceof HTMLVideoElement
          ? {
              hasSource: video.srcObject instanceof MediaStream,
              tracks: video.srcObject
                ? video.srcObject.getTracks().map((track) => ({
                    kind: track.kind,
                    readyState: track.readyState
                  }))
                : []
            }
          : null
      }
      return {
        screen: describe('.screen-video'),
        camera: describe('.camera-video'),
        text: document.body.textContent
      }
    })
    console.error(`Resume media diagnostics: ${JSON.stringify(resumeDiagnostics)}`)
    throw error
  }
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
  await page.waitForFunction(
    () =>
      document.querySelector('.camera-video')?.srcObject?.getVideoTracks()[0]?.readyState ===
      'live'
  )
  await page.locator('.camera-video').evaluate((video) => {
    globalThis.__advxSmokeCameraTrack = video.srcObject?.getVideoTracks()[0]
  })

  await page.getByRole('button', { name: '结束直播', exact: true }).click()
  await page.waitForFunction(() => document.body.textContent?.includes('未开播'))
  await page.waitForFunction(
    () =>
      document.body.textContent?.includes('屏幕 预览中') &&
      document.body.textContent?.includes('摄像头 预览中')
  )
  if ((await page.locator('.video-stage video').count()) !== 2) {
    throw new Error('Visual preview did not remain mounted after stop.')
  }
  const stoppedDisplayTrackState = await page.evaluate(
    () => globalThis.__advxSmokeDisplayTrack?.readyState
  )
  if (stoppedDisplayTrackState !== 'live') {
    throw new Error(`Display track stopped with the live session: ${stoppedDisplayTrackState}.`)
  }
  const stoppedCameraTrackState = await page.evaluate(
    () => globalThis.__advxSmokeCameraTrack?.readyState
  )
  if (stoppedCameraTrackState !== 'live') {
    throw new Error(`Camera track stopped with the live session: ${stoppedCameraTrackState}.`)
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
  await page.getByRole('button', { name: '更换来源', exact: true }).waitFor()
  await page.locator('.screen-video').waitFor()
  if (
    (await page.locator('.screen-video').count()) !== 1 ||
    (await page.locator('.camera-video').count()) !== 0
  ) {
    throw new Error('Reload did not restore the default live desktop-only preview.')
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
  await page.getByRole('button', { name: '模式参数', exact: true }).click()
  await page.locator('[data-audience-range] input').first().fill('7')

  console.log(
    `Monorepo desktop smoke passed: default live desktop capture, ${sourceCount} sources, background preview continuity, six audience modes, 32 personas, live edit policy, Shared Brain-only meme controls, camera denied before explicit enable, ${cameraDevices} camera entries, three visual modes, ${compressedKilobytes} KB composite JPEG, versioned settings restore, microphone meter peak ${microphonePeak}%, continuous visual preview across pause/stop, and microphone cleanup.`
  )
  console.log(`Screenshot: ${resolve(artifactDirectory, 'control-console.png')}`)
  console.log(`Saved model credentials: ${resolve(artifactDirectory, 'model-config-saved.png')}`)
  console.log(`Camera picture-in-picture: ${resolve(artifactDirectory, 'views-camera-pip.png')}`)
  console.log(
    `Compact camera picture-in-picture: ${resolve(artifactDirectory, 'views-camera-pip-1120.png')}`
  )

  await page.getByRole('button', { name: '设置', exact: true }).click()
  await page.evaluate(async () => {
    await window.advx.showOverlay()
    for (let index = 0; index < 8; index += 1) {
      await window.advx.pushBarrage({
        barrageId: `overlay-proof-${index}`,
        audienceId: `audience-${index}`,
        audienceName: `测试观众 ${index + 1}`,
        text: `Overlay 渲染验证弹幕 ${index + 1}`,
        color: index % 2 === 0 ? '#a8f53a' : '#65d6b9',
        createdAt: Date.now()
      })
    }
  })
  await overlayPage.locator('.overlay-barrage').first().waitFor()
  await overlayPage.waitForTimeout(1_300)
  await overlayPage.screenshot({
    path: resolve(artifactDirectory, 'overlay-renderer.png'),
    omitBackground: false
  })

  await page.evaluate(() => window.advx.clearOverlay())
  await overlayPage.locator('.overlay-barrage').first().waitFor({ state: 'detached' })
  await page.waitForTimeout(250)
  assert.equal(await overlayPage.locator('.overlay-barrage').count(), 0)

  proof = {
    targetOptions,
    sourceCount,
    defaultSettings: defaultOverlaySettings,
    settings: configuredSettings,
    rendered,
    bounds: boundsProof,
    clearCount: 0
  }

  await closeControlWindowAndWaitForExit(electronApp)
  electronApp = await launchApp()
  const restartedPage = await electronApp.firstWindow()
  await restartedPage.waitForSelector('h1')
  const restoredSettings = await restartedPage.evaluate(() => window.advx.getOverlaySettings())
  assert.deepEqual(restoredSettings, configuredSettings, 'Overlay settings were not persisted.')
  proof.restoredSettings = restoredSettings

  const defaultPreviewSettings = {
    ...defaultOverlaySettings,
    targetDisplayId: restoredSettings.targetDisplayId
  }
  await restartedPage.evaluate(
    (settings) => window.advx.setOverlaySettings(settings),
    defaultPreviewSettings
  )
  await restartedPage.reload()
  await restartedPage.getByRole('heading', { name: '直播控制台', exact: true }).waitFor()
  await restartedPage.getByRole('button', { name: '设置', exact: true }).click()
  await restartedPage.getByLabel('弹幕字体', { exact: true }).waitFor()
  assert.equal(
    await restartedPage.getByLabel('弹幕字体', { exact: true }).inputValue(),
    'bilibili'
  )
  assert.equal(await restartedPage.getByLabel('粗体', { exact: true }).isChecked(), true)
  assert.equal(
    await restartedPage.getByLabel('描边粗细', { exact: true }).inputValue(),
    '1'
  )

  await restartedPage.getByRole('button', { name: '滚动', exact: true }).click()
  let restartedOverlayPage = electronApp
    .windows()
    .find((candidate) => candidate.url().replaceAll('\\', '/').endsWith('/overlay/index.html'))
  restartedOverlayPage ??= await electronApp.waitForEvent('window', {
    predicate: (candidate) =>
      candidate.url().replaceAll('\\', '/').endsWith('/overlay/index.html')
  })
  await restartedOverlayPage.locator('.overlay-barrage--scroll').waitFor()
  await restartedPage.getByRole('button', { name: '顶端', exact: true }).click()
  await restartedPage.getByRole('button', { name: '底端', exact: true }).click()
  await restartedOverlayPage.waitForFunction(
    () => document.querySelectorAll('.overlay-barrage').length === 3
  )
  const previewControlModes = await restartedOverlayPage.evaluate(() => ({
    scroll: document.querySelectorAll('.overlay-barrage--scroll').length,
    top: document.querySelectorAll('.overlay-barrage--top').length,
    bottom: document.querySelectorAll('.overlay-barrage--bottom').length
  }))
  assert.deepEqual(previewControlModes, { scroll: 1, top: 1, bottom: 1 })

  await restartedPage.evaluate(() => window.advx.clearOverlay())
  await restartedOverlayPage.locator('.overlay-barrage').first().waitFor({ state: 'detached' })
  const mockBarrageEvents = [
    ['mock-scroll-1', '这波操作有点东西', 'scroll'],
    ['mock-scroll-2', '前方高能，请注意', 'scroll'],
    ['mock-scroll-3', '画面很清楚，继续冲', 'scroll'],
    ['mock-top-1', '顶端固定：本场最佳', 'top'],
    ['mock-top-2', '顶端固定：名场面预定', 'top'],
    ['mock-bottom-1', '底端固定：感谢观看', 'bottom']
  ].map(([barrageId, text, mode], index) => ({
    barrageId,
    audienceId: `mock-audience-${index}`,
    audienceName: `模式观众 ${index + 1}`,
    text,
    color: index % 2 === 0 ? '#a8f53a' : '#65d6b9',
    mode,
    createdAt: Date.now() + index
  }))
  await restartedPage.evaluate(async (events) => {
    await window.advx.showOverlay()
    for (const event of events) {
      await window.advx.pushBarrage(event)
    }
  }, mockBarrageEvents)
  await restartedOverlayPage.waitForFunction(
    () => document.querySelectorAll('.overlay-barrage').length === 6
  )
  await restartedOverlayPage.waitForTimeout(1_200)
  const modeMock = await restartedOverlayPage.evaluate(() => {
    const root = document.querySelector('.overlay-root')
    const scroll = document.querySelector('.overlay-barrage--scroll')
    const top = document.querySelector('.overlay-barrage--top')
    const bottom = document.querySelector('.overlay-barrage--bottom')
    if (
      !(root instanceof HTMLElement) ||
      !(scroll instanceof HTMLElement) ||
      !(top instanceof HTMLElement) ||
      !(bottom instanceof HTMLElement)
    ) {
      return null
    }
    const rootRect = root.getBoundingClientRect()
    const scrollStyle = getComputedStyle(scroll)
    const topStyle = getComputedStyle(top)
    const bottomStyle = getComputedStyle(bottom)
    return {
      count: document.querySelectorAll('.overlay-barrage').length,
      modes: {
        scroll: document.querySelectorAll('.overlay-barrage--scroll').length,
        top: document.querySelectorAll('.overlay-barrage--top').length,
        bottom: document.querySelectorAll('.overlay-barrage--bottom').length
      },
      fontSize: scrollStyle.fontSize,
      fontFamily: scrollStyle.fontFamily,
      fontWeight: scrollStyle.fontWeight,
      opacity: scrollStyle.opacity,
      textShadow: scrollStyle.textShadow,
      durations: {
        scroll: scrollStyle.animationDuration,
        top: topStyle.animationDuration,
        bottom: bottomStyle.animationDuration
      },
      fixedRects: [top, bottom].map((item) => {
        const rect = item.getBoundingClientRect()
        return { top: rect.top, bottom: rect.bottom }
      }),
      rootHeight: rootRect.height,
      identityNodeCount: document.querySelectorAll('.overlay-name, .ai-watermark, img').length
    }
  })
  assert.ok(modeMock, 'Three-mode mock styles were not readable.')
  assert.equal(modeMock.count, 6)
  assert.deepEqual(modeMock.modes, { scroll: 3, top: 2, bottom: 1 })
  assert.equal(modeMock.fontSize, '25px')
  assert.match(modeMock.fontFamily, /SimHei/)
  assert.ok(['700', 'bold'].includes(modeMock.fontWeight))
  assert.equal(modeMock.opacity, '0.8')
  assert.match(modeMock.textShadow, /1px/)
  assert.deepEqual(modeMock.durations, {
    scroll: '8.438s',
    top: '4s',
    bottom: '4s'
  })
  assert.equal(modeMock.identityNodeCount, 0)
  assert.ok(
    modeMock.fixedRects.every(
      (rect) => rect.top >= -1 && rect.bottom <= modeMock.rootHeight * 0.5 + 1
    ),
    'A fixed barrage escaped the default top-half display region.'
  )
  await restartedOverlayPage.evaluate(() => {
    const root = document.querySelector('.overlay-root')
    if (root instanceof HTMLElement) root.style.background = '#687583'
  })
  await restartedOverlayPage.screenshot({
    path: resolve(artifactDirectory, 'overlay-modes-mock.png'),
    omitBackground: false
  })
  proof.previewControls = previewControlModes
  proof.modeMock = modeMock

  await writeFile(
    resolve(artifactDirectory, 'overlay-smoke-proof.json'),
    JSON.stringify(proof, null, 2),
    'utf8'
  )

  console.log(
    `Desktop Overlay smoke passed: ${targetOptions} target(s), ${sourceCount} capture source(s), three barrage modes, font styling, collision-free density, bounds, clear, persistence, and main-window quit.`
  )
  console.log(`Settings screenshot: ${resolve(artifactDirectory, 'overlay-settings.png')}`)
  console.log(`Overlay screenshot: ${resolve(artifactDirectory, 'overlay-renderer.png')}`)
  console.log(`Three-mode mock: ${resolve(artifactDirectory, 'overlay-modes-mock.png')}`)
  console.log(`Proof: ${resolve(artifactDirectory, 'overlay-smoke-proof.json')}`)
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
if (!personaDocument.includes('"document_version": 2')) {
  throw new Error('The materialized personality.md file has no supported format version.')
}

const rejectedWorkspace = `${JSON.stringify({ version: 2, future: true }, null, 2)}\n`
await writeFile(audienceWorkspaceFile, rejectedWorkspace, 'utf8')
const recoveryApp = await electron.launch({
  args: ['.', `--user-data-dir=${smokeUserDataDirectory}`, '--use-fake-device-for-media-stream'],
  cwd: root,
  env: {
    ...electronEnvironment,
    ...backendEnvironment,
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
