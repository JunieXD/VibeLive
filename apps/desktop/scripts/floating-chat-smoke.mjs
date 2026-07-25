import assert from 'node:assert/strict'
import { mkdir, rm } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright-core'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const artifactDirectory = resolve(root, 'artifacts')
const userDataDirectory = resolve(artifactDirectory, 'floating-chat-smoke-user-data')

async function waitForDisplayModeEnabled(page, expectedMode, enabled = true) {
  const deadline = Date.now() + 5_000
  let actualModes = []

  while (Date.now() < deadline) {
    actualModes = await page.evaluate(
      async () => (await window.advx.getOverlaySettings()).displayModes
    )
    if (actualModes.includes(expectedMode) === enabled) return
    await page.waitForTimeout(100)
  }

  assert.equal(actualModes.includes(expectedMode), enabled)
}

await mkdir(artifactDirectory, { recursive: true })
await rm(userDataDirectory, {
  recursive: true,
  force: true,
  maxRetries: 5,
  retryDelay: 200
})
await mkdir(userDataDirectory, { recursive: true })

const { ELECTRON_RUN_AS_NODE: _electronRunAsNode, ...electronEnvironment } = process.env
const electronApp = await electron.launch({
  args: ['.', `--user-data-dir=${userDataDirectory}`],
  cwd: root,
  env: {
    ...electronEnvironment,
    ADVX_BACKEND_EXTERNAL: '1',
    ADVX_BACKEND_URL: 'http://127.0.0.1:65534',
    ELECTRON_DISABLE_SECURITY_WARNINGS: 'true'
  }
})
const rendererDiagnostics = []
electronApp.on('window', (page) => {
  page.on('console', (message) => {
    rendererDiagnostics.push(`console:${message.type()}:${message.text()}`)
  })
  page.on('pageerror', (error) => {
    rendererDiagnostics.push(`pageerror:${error.message}`)
  })
})

try {
  const controlPage = await electronApp.firstWindow()
  await controlPage.getByRole('heading', { name: '直播控制台', exact: true }).waitFor()
  await controlPage.getByRole('button', { name: '设置', exact: true }).click()
  const floatingChatToggle = controlPage.getByLabel('互动悬浮窗', { exact: true })
  await floatingChatToggle.waitFor()
  await floatingChatToggle.check()
  await waitForDisplayModeEnabled(controlPage, 'floating')
  const settingsScreenshot = resolve(artifactDirectory, 'floating-chat-settings.png')
  await controlPage.screenshot({
    path: settingsScreenshot,
    fullPage: true
  })

  await controlPage.evaluate(async () => {
    const visible = await window.advx.showOverlay()
    if (!visible) throw new Error('Floating chat output did not become visible.')
    const settings = await window.advx.getOverlaySettings()
    if (!settings.displayModes.includes('floating')) {
      throw new Error(`Floating chat output was not enabled: ${settings.displayModes}`)
    }
    const messages = [
      ['羊-有毒的', '这么帅', '#65c9e5'],
      ['羊-有毒的', '坐在牛客坐牢', '#65c9e5'],
      ['前排观众', '开个签到题就不会了', '#78bfa4'],
      ['路过观众', 'wa 了3发了', '#d7a45c']
    ]
    for (const [index, message] of messages.entries()) {
      const dispatched = await window.advx.pushBarrage({
        barrageId: `floating-smoke-${index}`,
        audienceId: `audience-${index === 1 ? 0 : index}`,
        audienceName: message[0],
        text: message[1],
        color: message[2],
        createdAt: Date.now() + index,
        mode: 'scroll'
      })
      if (!dispatched) throw new Error(`Floating barrage ${index} was not dispatched.`)
    }
  })

  let floatingPage = electronApp
    .windows()
    .find((candidate) =>
      candidate.url().replaceAll('\\', '/').endsWith('/floating-chat/index.html')
    )
  floatingPage ??= await electronApp.waitForEvent('window', {
    predicate: (candidate) =>
      candidate.url().replaceAll('\\', '/').endsWith('/floating-chat/index.html')
  })
  try {
    await floatingPage.getByText('wa 了3发了', { exact: true }).waitFor({
      timeout: 5_000
    })
  } catch (error) {
    const diagnostic = await floatingPage.evaluate(() => ({
      bodyText: document.body.innerText,
      floatingApiAvailable: typeof window.advxFloatingChat === 'object',
      messageRows: document.querySelectorAll('.message-row').length
    }))
    const windowDiagnostic = await electronApp.evaluate(({ BrowserWindow }) => {
      const floatingWindow = BrowserWindow.getAllWindows().find((window) =>
        window.webContents.getURL().replaceAll('\\', '/').endsWith('/floating-chat/index.html')
      )
      return floatingWindow
        ? {
            url: floatingWindow.webContents.getURL(),
            loading: floatingWindow.webContents.isLoadingMainFrame(),
            crashed: floatingWindow.webContents.isCrashed()
          }
        : null
    })
    throw new Error(
      `Floating chat did not receive barrage events: ${JSON.stringify({
        ...diagnostic,
        pageUrl: floatingPage.url(),
        windowDiagnostic,
        rendererDiagnostics
      })}`,
      { cause: error }
    )
  }

  const proof = await floatingPage.evaluate(() => {
    const shell = document.querySelector('.floating-chat-shell')
    const list = document.querySelector('.message-list')
    const composer = document.querySelector('.composer')
    if (
      !(shell instanceof HTMLElement) ||
      !(list instanceof HTMLElement) ||
      !(composer instanceof HTMLElement)
    ) {
      return null
    }
    const shellRect = shell.getBoundingClientRect()
    const listRect = list.getBoundingClientRect()
    const composerRect = composer.getBoundingClientRect()
    return {
      title: document.querySelector('.titlebar-brand strong')?.textContent?.trim(),
      rows: document.querySelectorAll('.message-row').length,
      controls: document.querySelectorAll('.window-actions button').length,
      audienceCount: document.querySelector('.interaction-summary span')?.textContent?.trim(),
      horizontalOverflow:
        document.documentElement.scrollWidth - document.documentElement.clientWidth,
      shellWidth: shellRect.width,
      shellHeight: shellRect.height,
      listHeight: listRect.height,
      composerBelowList: composerRect.top >= listRect.bottom - 1
    }
  })
  assert.ok(proof, 'Floating chat renderer did not expose its expected layout.')
  assert.equal(proof.title, '直播互动')
  assert.equal(proof.rows, 4)
  assert.equal(proof.controls, 2)
  assert.equal(proof.audienceCount, '3')
  assert.ok(proof.horizontalOverflow <= 1, 'Floating chat overflowed horizontally.')
  assert.ok(proof.shellWidth >= 340)
  assert.ok(proof.shellHeight >= 500)
  assert.ok(proof.listHeight > 200)
  assert.equal(proof.composerBelowList, true)

  const defaultScreenshot = resolve(artifactDirectory, 'floating-chat-window.png')
  await floatingPage.screenshot({ path: defaultScreenshot })

  await electronApp.evaluate(({ BrowserWindow }) => {
    const floatingWindow = BrowserWindow.getAllWindows().find((window) =>
      window.webContents.getURL().replaceAll('\\', '/').endsWith('/floating-chat/index.html')
    )
    if (!floatingWindow) throw new Error('Floating chat window is missing.')
    floatingWindow.setSize(340, 500)
  })
  await floatingPage.waitForTimeout(200)
  const compactProof = await floatingPage.evaluate(() => ({
    horizontalOverflow:
      document.documentElement.scrollWidth - document.documentElement.clientWidth,
    messageListHeight:
      document.querySelector('.message-list')?.getBoundingClientRect().height ?? 0,
    composerHeight:
      document.querySelector('.composer')?.getBoundingClientRect().height ?? 0
  }))
  assert.ok(compactProof.horizontalOverflow <= 1)
  assert.ok(compactProof.messageListHeight > 100)
  assert.ok(compactProof.composerHeight > 0)

  const compactScreenshot = resolve(artifactDirectory, 'floating-chat-window-compact.png')
  await floatingPage.screenshot({ path: compactScreenshot })

  await floatingPage.getByTitle('关闭互动窗').click()
  await electronApp.evaluate(async ({ BrowserWindow }) => {
    const floatingWindow = BrowserWindow.getAllWindows().find((window) =>
      window.webContents.getURL().replaceAll('\\', '/').endsWith('/floating-chat/index.html')
    )
    if (!floatingWindow) throw new Error('Floating chat window is missing.')
    for (let attempt = 0; attempt < 20 && floatingWindow.isVisible(); attempt += 1) {
      await new Promise((resolveWait) => setTimeout(resolveWait, 25))
    }
    if (floatingWindow.isVisible()) throw new Error('Floating chat window did not hide.')
  })

  await floatingChatToggle.uncheck()
  await waitForDisplayModeEnabled(controlPage, 'floating', false)
  await waitForDisplayModeEnabled(controlPage, 'overlay')
  await controlPage.evaluate(async () => {
    await window.advx.showOverlay()
    const dispatched = await window.advx.pushBarrage({
      barrageId: 'overlay-regression-smoke',
      audienceId: 'overlay-regression',
      audienceName: '覆盖层回归',
      text: '屏幕弹幕仍可显示',
      createdAt: Date.now(),
      mode: 'scroll'
    })
    if (!dispatched) throw new Error('Overlay regression barrage was not dispatched.')
  })
  let overlayPage = electronApp
    .windows()
    .find((candidate) =>
      candidate.url().replaceAll('\\', '/').endsWith('/overlay/index.html')
    )
  overlayPage ??= await electronApp.waitForEvent('window', {
    predicate: (candidate) =>
      candidate.url().replaceAll('\\', '/').endsWith('/overlay/index.html')
  })
  await overlayPage.getByText('屏幕弹幕仍可显示', { exact: true }).waitFor()
  assert.equal(
    await overlayPage.evaluate(() => typeof window.advxOverlay),
    'object'
  )
  await controlPage.evaluate(() => window.advx.hideOverlay())

  console.log(`Floating chat screenshot: ${defaultScreenshot}`)
  console.log(`Compact screenshot: ${compactScreenshot}`)
  console.log(`Settings screenshot: ${settingsScreenshot}`)
} finally {
  await electronApp.evaluate(({ app }) => app.exit(0)).catch(() => undefined)
  await electronApp.close().catch(() => undefined)
}
