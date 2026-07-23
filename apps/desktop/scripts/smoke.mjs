import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { mkdir, rm, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { promisify } from 'node:util'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright-core'

const execFileAsync = promisify(execFile)
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const artifactDirectory = resolve(root, 'artifacts')
const userDataDirectory = resolve(artifactDirectory, 'smoke-user-data')
await mkdir(artifactDirectory, { recursive: true })
await rm(userDataDirectory, { recursive: true, force: true })

const { ELECTRON_RUN_AS_NODE: _electronRunAsNode, ...electronEnvironment } = process.env

function launchApp() {
  return electron.launch({
    args: ['.', `--user-data-dir=${userDataDirectory}`],
    cwd: root,
    env: {
      ...electronEnvironment,
      ELECTRON_DISABLE_SECURITY_WARNINGS: 'true'
    }
  })
}

async function setRange(page, label, value) {
  const input = page.getByLabel(label, { exact: true })
  await input.fill(String(value))
  await input.evaluate((element) => {
    element.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

async function windowAtScreenPoint(point) {
  const script = `
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class AdvxWindowHitTest {
  [StructLayout(LayoutKind.Sequential)]
  public struct Point { public int X; public int Y; }
  [StructLayout(LayoutKind.Sequential)]
  public struct Rect { public int Left; public int Top; public int Right; public int Bottom; }
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(Point point);
  [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(
    IntPtr hwnd, out uint processId
  );
  [DllImport("user32.dll", CharSet = CharSet.Unicode)]
  public static extern int GetWindowText(IntPtr hwnd, System.Text.StringBuilder text, int count);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)]
  public static extern int GetClassName(IntPtr hwnd, System.Text.StringBuilder text, int count);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
}
'@
[AdvxWindowHitTest]::SetProcessDPIAware() | Out-Null
$point = New-Object AdvxWindowHitTest+Point
$point.X = ${point.x}
$point.Y = ${point.y}
$hit = [AdvxWindowHitTest]::WindowFromPoint($point)
$root = [AdvxWindowHitTest]::GetAncestor($hit, 2)
$processId = [uint32]0
[AdvxWindowHitTest]::GetWindowThreadProcessId($root, [ref]$processId) | Out-Null
$title = New-Object System.Text.StringBuilder 256
[AdvxWindowHitTest]::GetWindowText($root, $title, $title.Capacity) | Out-Null
$className = New-Object System.Text.StringBuilder 256
[AdvxWindowHitTest]::GetClassName($root, $className, $className.Capacity) | Out-Null
$rect = New-Object AdvxWindowHitTest+Rect
[AdvxWindowHitTest]::GetWindowRect($root, [ref]$rect) | Out-Null
@{
  handle = $root.ToInt64().ToString()
  processId = $processId
  title = $title.ToString()
  className = $className.ToString()
  rect = @{ left = $rect.Left; top = $rect.Top; right = $rect.Right; bottom = $rect.Bottom }
} | ConvertTo-Json -Compress
`
  const { stdout } = await execFileAsync('powershell.exe', [
    '-NoProfile',
    '-NonInteractive',
    '-Command',
    script
  ])
  return JSON.parse(stdout.trim())
}

async function clickScreenPoint(point) {
  const script = `
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class AdvxNativeMouse {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")]
  public static extern void mouse_event(
    uint flags, uint dx, uint dy, uint data, UIntPtr extraInfo
  );
}
'@
[AdvxNativeMouse]::SetProcessDPIAware() | Out-Null
[AdvxNativeMouse]::SetCursorPos(${point.x}, ${point.y}) | Out-Null
Start-Sleep -Milliseconds 80
[AdvxNativeMouse]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 40
[AdvxNativeMouse]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
`
  await execFileAsync('powershell.exe', [
    '-NoProfile',
    '-NonInteractive',
    '-Command',
    script
  ])
}

async function windowRectForHandle(handle) {
  const script = `
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class AdvxWindowRect {
  [StructLayout(LayoutKind.Sequential)]
  public struct Rect { public int Left; public int Top; public int Right; public int Bottom; }
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
}
'@
[AdvxWindowRect]::SetProcessDPIAware() | Out-Null
$rect = New-Object AdvxWindowRect+Rect
$handle = [IntPtr]([Int64]::Parse('${handle}'))
[AdvxWindowRect]::GetWindowRect($handle, [ref]$rect) | Out-Null
@{ left = $rect.Left; top = $rect.Top; right = $rect.Right; bottom = $rect.Bottom } |
  ConvertTo-Json -Compress
`
  const { stdout } = await execFileAsync('powershell.exe', [
    '-NoProfile',
    '-NonInteractive',
    '-Command',
    script
  ])
  return JSON.parse(stdout.trim())
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

  await page.getByRole('button', { name: /AI 观众/ }).click()
  await page.getByRole('heading', { name: 'AI 观众', exact: true }).waitFor()

  await page.getByRole('button', { name: '设置', exact: true }).click()
  await page.getByRole('heading', { name: '弹幕覆盖层', exact: true }).waitFor()
  await page.getByLabel('弹幕目标', { exact: true }).waitFor()

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

  await setRange(page, '字号', 30)
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
      speed: configuredSettings.speed,
      opacity: configuredSettings.opacity,
      density: configuredSettings.density,
      region: configuredSettings.region,
      clickThrough: configuredSettings.clickThrough
    },
    {
      fontSizePx: 30,
      speed: 100,
      opacity: 55,
      density: 3,
      region: { topPercent: 20, bottomPercent: 60 },
      clickThrough: true
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
  assert.ok(sourceCount >= 1, 'Desktop source IPC returned no sources.')
  await page.getByTitle('关闭').click()
  await page.getByRole('button', { name: '设置', exact: true }).click()
  await page.getByLabel('点击穿透', { exact: true }).waitFor()

  await page.evaluate(async () => {
    await window.advx.showOverlay()
    for (let index = 0; index < 8; index += 1) {
      await window.advx.pushBarrage({
        barrageId: `smoke-${index}`,
        audienceId: `audience-${index}`,
        audienceName: `测试观众 ${index + 1}`,
        text: `Overlay 参数验证弹幕 ${index + 1}`,
        color: index % 2 === 0 ? '#a8f53a' : '#65d6b9',
        createdAt: Date.now()
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
  for (const key of ['x', 'y', 'width', 'height']) {
    assert.ok(
      Math.abs(boundsProof.overlay[key] - boundsProof.target[key]) <= 1,
      `Overlay ${key} did not match its target within one DIP.`
    )
  }

  await page.evaluate(() => window.advx.clearOverlay())
  await page.getByRole('button', { name: '直播控制台', exact: true }).click()
  await page.getByRole('button', { name: '选择来源', exact: true }).click()
  await page.locator('.source-option').first().waitFor()

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

  await page.getByRole('button', { name: '设置', exact: true }).click()
  await page.getByLabel('点击穿透', { exact: true }).waitFor()
  await page.evaluate(async () => {
    await window.advx.showOverlay()
    for (let index = 0; index < 8; index += 1) {
      await window.advx.pushBarrage({
        barrageId: `click-proof-${index}`,
        audienceId: `audience-${index}`,
        audienceName: `测试观众 ${index + 1}`,
        text: `Overlay 点击穿透验证弹幕 ${index + 1}`,
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

  let clickThroughProof = { skipped: process.platform !== 'win32' }
  if (process.platform === 'win32') {
    const clickThroughToggle = page.getByLabel('点击穿透', { exact: true })
    await clickThroughToggle.scrollIntoViewIfNeeded()

    await electronApp.evaluate(({ BrowserWindow }) => {
      const overlayWindow = BrowserWindow.getAllWindows().find((window) =>
        window.webContents.getURL().replaceAll('\\', '/').endsWith('/overlay/index.html')
      )
      if (!overlayWindow) throw new Error('Overlay window is missing.')
      const original = overlayWindow.setIgnoreMouseEvents.bind(overlayWindow)
      globalThis.__advxIgnoreMouseCalls = []
      overlayWindow.setIgnoreMouseEvents = (ignore, options) => {
        globalThis.__advxIgnoreMouseCalls.push(ignore)
        return original(ignore, options)
      }
    })

    await page.evaluate(() => {
      const probe = document.createElement('button')
      probe.id = 'overlay-click-probe'
      probe.type = 'button'
      probe.textContent = 'click probe'
      probe.style.cssText =
        'position:fixed;left:80px;top:120px;width:140px;height:48px;z-index:2147483647;'
      document.body.append(probe)
    })
    await overlayPage.evaluate(() => {
      const probe = document.createElement('div')
      probe.id = 'overlay-native-hit-probe'
      probe.style.cssText =
        'position:fixed;right:40px;top:80px;width:120px;height:60px;background:#16191d;' +
        'opacity:.2;pointer-events:auto;'
      document.body.append(probe)
    })

    const probeBox = await page.locator('#overlay-click-probe').boundingBox()
    assert.ok(probeBox, 'Could not locate the click-through probe.')
    const toggleBox = await clickThroughToggle.boundingBox()
    assert.ok(toggleBox, 'Could not locate the click-through toggle.')
    const overlayProbeBox = await overlayPage.locator('#overlay-native-hit-probe').boundingBox()
    assert.ok(overlayProbeBox, 'Could not locate the Overlay native hit-test probe.')
    await electronApp.evaluate(
      ({ BrowserWindow, screen }, { targetDisplayId }) => {
        const controlWindow = BrowserWindow.getAllWindows().find((window) =>
          window.webContents.getURL().replaceAll('\\', '/').endsWith('/control/index.html')
        )
        const overlayWindow = BrowserWindow.getAllWindows().find((window) =>
          window.webContents.getURL().replaceAll('\\', '/').endsWith('/overlay/index.html')
        )
        if (!controlWindow) throw new Error('Control window is missing.')
        if (!overlayWindow) throw new Error('Overlay window is missing.')
        const targetDisplay =
          screen.getAllDisplays().find((display) => display.id === targetDisplayId) ??
          screen.getPrimaryDisplay()
        controlWindow.setBounds({
          x: targetDisplay.workArea.x + 24,
          y: targetDisplay.workArea.y + 24,
          width: Math.min(1_120, targetDisplay.workArea.width),
          height: Math.min(720, targetDisplay.workArea.height)
        })
        controlWindow.setAlwaysOnTop(true, 'pop-up-menu')
        controlWindow.show()
        controlWindow.focus()
        controlWindow.moveTop()
        overlayWindow.moveTop()
      },
      {
        targetDisplayId: configuredSettings.targetDisplayId
      }
    )

    const nativeHandles = await electronApp.evaluate(({ BrowserWindow }) => {
      const windows = BrowserWindow.getAllWindows()
      const controlWindow = windows.find((window) =>
        window.webContents.getURL().replaceAll('\\', '/').endsWith('/control/index.html')
      )
      const overlayWindow = windows.find((window) =>
        window.webContents.getURL().replaceAll('\\', '/').endsWith('/overlay/index.html')
      )
      if (!controlWindow || !overlayWindow) throw new Error('Smoke windows are missing.')
      return {
        control: controlWindow.getNativeWindowHandle().readBigUInt64LE().toString(),
        overlay: overlayWindow.getNativeWindowHandle().readBigUInt64LE().toString()
      }
    })
    const [controlNativeRect, overlayNativeRect] = await Promise.all([
      windowRectForHandle(nativeHandles.control),
      windowRectForHandle(nativeHandles.overlay)
    ])
    const overlayViewport = await overlayPage.evaluate(() => ({
      width: window.innerWidth,
      height: window.innerHeight
    }))
    const controlViewport = await page.evaluate(() => ({
      width: window.innerWidth,
      height: window.innerHeight
    }))
    const scaleX =
      (overlayNativeRect.right - overlayNativeRect.left) / overlayViewport.width
    const scaleY =
      (overlayNativeRect.bottom - overlayNativeRect.top) / overlayViewport.height
    const controlContentOffset = {
      x:
        (controlNativeRect.right -
          controlNativeRect.left -
          controlViewport.width * scaleX) /
        2,
      y:
        (controlNativeRect.bottom -
          controlNativeRect.top -
          controlViewport.height * scaleY) /
        2
    }
    const toControlNativePoint = (box) => ({
      x: Math.round(
        controlNativeRect.left +
          controlContentOffset.x +
          (box.x + box.width / 2) * scaleX
      ),
      y: Math.round(
        controlNativeRect.top +
          controlContentOffset.y +
          (box.y + box.height / 2) * scaleY
      )
    })
    const overlayOnlyPoint = {
      x: Math.round(
        overlayNativeRect.left +
          (overlayProbeBox.x + overlayProbeBox.width / 2) *
            scaleX
      ),
      y: Math.round(
        overlayNativeRect.top +
          (overlayProbeBox.y + overlayProbeBox.height / 2) *
            scaleY
      )
    }
    assert.ok(
      overlayOnlyPoint.x < controlNativeRect.left ||
        overlayOnlyPoint.x >= controlNativeRect.right ||
        overlayOnlyPoint.y < controlNativeRect.top ||
        overlayOnlyPoint.y >= controlNativeRect.bottom,
      'The Overlay native hit-test probe is covered by the control window.'
    )
    const hitTestPoints = {
      probe: toControlNativePoint(probeBox),
      toggle: toControlNativePoint(toggleBox),
      overlayOnly: overlayOnlyPoint
    }
    const passThroughOwner = await windowAtScreenPoint(hitTestPoints.probe)
    assert.equal(
      passThroughOwner.handle,
      nativeHandles.control,
      `The control window did not own the hit-test point with click-through enabled: ${JSON.stringify({ nativeHandles, passThroughOwner, hitTestPoints })}`
    )

    await clickThroughToggle.uncheck()
    await page.waitForFunction(async () => !(await window.advx.getOverlaySettings()).clickThrough)
    const blockedOwner = await windowAtScreenPoint(hitTestPoints.overlayOnly)
    assert.equal(
      blockedOwner.handle,
      nativeHandles.overlay,
      `The overlay did not own its painted hit-test probe with click-through disabled: ${JSON.stringify({ blockedOwner, hitTestPoints, controlNativeRect, overlayNativeRect, overlayViewport, overlayProbeBox })}`
    )

    const recoveryControlOwner = await windowAtScreenPoint(hitTestPoints.toggle)
    assert.equal(
      recoveryControlOwner.handle,
      nativeHandles.control,
      `The control window was not accessible above the blocking Overlay: ${JSON.stringify({ recoveryControlOwner, hitTestPoints, controlNativeRect, overlayNativeRect, toggleBox })}`
    )
    await clickScreenPoint(hitTestPoints.toggle)
    await page.waitForFunction(async () => (await window.advx.getOverlaySettings()).clickThrough)
    const restoredOwner = await windowAtScreenPoint(hitTestPoints.probe)
    assert.equal(
      restoredOwner.handle,
      nativeHandles.control,
      'The underlying window did not regain hit-test ownership after native recovery.'
    )
    const restoredOverlayPointOwner = await windowAtScreenPoint(hitTestPoints.overlayOnly)
    assert.notEqual(
      restoredOverlayPointOwner.handle,
      nativeHandles.overlay,
      'The Overlay still owned hit tests after native recovery.'
    )
    const ignoreMouseCalls = await electronApp.evaluate(
      () => globalThis.__advxIgnoreMouseCalls ?? []
    )
    assert.deepEqual(
      ignoreMouseCalls.slice(-2),
      [false, true],
      'Click-through changes did not reach BrowserWindow.setIgnoreMouseEvents.'
    )
    clickThroughProof = {
      skipped: false,
      nativeHandles,
      nativeRects: {
        control: controlNativeRect,
        overlay: overlayNativeRect
      },
      hitTestPoints,
      passThroughOwner,
      blockedOwner,
      recoveryControlOwner,
      restoredOwner,
      restoredOverlayPointOwner,
      ignoreMouseCalls
    }
  }

  await page.evaluate(() => window.advx.clearOverlay())
  await overlayPage.locator('.overlay-barrage').first().waitFor({ state: 'detached' })
  await page.waitForTimeout(250)
  assert.equal(await overlayPage.locator('.overlay-barrage').count(), 0)

  proof = {
    targetOptions,
    sourceCount,
    settings: configuredSettings,
    rendered,
    bounds: boundsProof,
    clickThrough: clickThroughProof,
    clearCount: 0
  }

  await electronApp.close()
  electronApp = await launchApp()
  const restartedPage = await electronApp.firstWindow()
  await restartedPage.waitForSelector('h1')
  const restoredSettings = await restartedPage.evaluate(() => window.advx.getOverlaySettings())
  assert.deepEqual(restoredSettings, configuredSettings, 'Overlay settings were not persisted.')
  proof.restoredSettings = restoredSettings

  await writeFile(
    resolve(artifactDirectory, 'overlay-smoke-proof.json'),
    JSON.stringify(proof, null, 2),
    'utf8'
  )

  console.log(
    `Desktop Overlay smoke passed: ${targetOptions} target(s), ${sourceCount} capture source(s), live styles, collision-free density, bounds, clear, persistence, and ${clickThroughProof.skipped ? 'API-only' : 'real Windows'} click-through.`
  )
  console.log(`Settings screenshot: ${resolve(artifactDirectory, 'overlay-settings.png')}`)
  console.log(`Overlay screenshot: ${resolve(artifactDirectory, 'overlay-renderer.png')}`)
  console.log(`Proof: ${resolve(artifactDirectory, 'overlay-smoke-proof.json')}`)
} finally {
  await electronApp.close()
}
