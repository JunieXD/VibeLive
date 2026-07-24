import assert from 'node:assert/strict'
import { mkdir, rm } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright-core'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const artifactDirectory = resolve(root, 'artifacts')
const userDataDirectory = resolve(artifactDirectory, 'ui-polish-user-data')
await rm(userDataDirectory, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
await mkdir(userDataDirectory, { recursive: true })

const { ELECTRON_RUN_AS_NODE: _drop, ...electronEnvironment } = process.env
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

try {
  const page = await electronApp.firstWindow()
  await page.getByRole('heading', { name: '直播控制台', exact: true }).waitFor()
  await page.setViewportSize({ width: 1120, height: 720 })
  await page.waitForTimeout(250)

  const liveCopy = await page.locator('body').innerText()
  assert.equal(liveCopy.includes('Provider'), false, 'English Provider copy is still visible.')
  assert.equal(
    liveCopy.includes('配置并接入供应商后才会发送画面、文字和转写'),
    false,
    'The removed idle supplier disclosure is still visible.'
  )
  assert.equal(
    await page.locator('.composer input').getAttribute('placeholder'),
    '配置供应商后可与 AI 观众互动'
  )

  const layout = await page.evaluate(() => {
    const bounds = (selector) => {
      const element = document.querySelector(selector)
      if (!(element instanceof HTMLElement)) return null
      const rect = element.getBoundingClientRect()
      return {
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        left: rect.left,
        clientHeight: element.clientHeight,
        scrollHeight: element.scrollHeight
      }
    }
    const mixerScroll = document.querySelector('.mixer-scroll')
    if (!(mixerScroll instanceof HTMLElement)) return null
    mixerScroll.scrollTop = mixerScroll.scrollHeight
    const mixerScrollTop = mixerScroll.scrollTop
    mixerScroll.scrollTop = 0
    return {
      viewport: { width: window.innerWidth, height: window.innerHeight },
      documentOverflow: document.documentElement.scrollWidth > window.innerWidth,
      rightRail: bounds('.right-rail'),
      viewerPanel: bounds('.viewer-panel'),
      viewerList: bounds('.viewer-list'),
      mixerPanel: bounds('.mixer-panel'),
      mixerScroll: bounds('.mixer-scroll'),
      mixerScrollTop,
      mixerRows: document.querySelectorAll('.mixer-row').length
    }
  })

  assert.ok(layout, 'Live dashboard layout was not available.')
  assert.equal(layout.documentOverflow, false, 'Live dashboard overflowed horizontally.')
  assert.ok(layout.rightRail && layout.viewerPanel && layout.viewerList)
  assert.ok(layout.mixerPanel && layout.mixerScroll)
  assert.ok(
    layout.rightRail.scrollHeight <= layout.rightRail.clientHeight + 1,
    'The live right rail requires a second outer scrollbar at 1120x720.'
  )
  assert.ok(
    layout.viewerPanel.bottom <= layout.rightRail.bottom + 1 &&
      layout.viewerList.clientHeight > 0,
    'The live viewer panel is clipped.'
  )
  assert.ok(
    layout.mixerPanel.bottom <= layout.rightRail.bottom + 1 &&
      layout.mixerScroll.clientHeight > 0,
    'The mixer card is clipped.'
  )
  assert.equal(layout.mixerRows, 7)
  assert.ok(
    layout.mixerScroll.scrollHeight > layout.mixerScroll.clientHeight &&
      layout.mixerScrollTop > 0,
    'The mixer card does not expose its scrollable status rows.'
  )

  await page.screenshot({
    path: resolve(artifactDirectory, 'live-ui-polish-1120-dark.png')
  })

  const sampleDropdown = page.getByRole('combobox', {
    name: '视觉采样频率',
    exact: true
  })
  await sampleDropdown.click()
  const sampleListbox = page.getByRole('listbox', {
    name: '视觉采样频率',
    exact: true
  })
  await sampleListbox.waitFor()
  assert.equal(await sampleListbox.getByRole('option').count(), 4)
  const menuBounds = await sampleListbox.evaluate((element) => {
    const rect = element.getBoundingClientRect()
    return {
      top: rect.top,
      right: rect.right,
      bottom: rect.bottom,
      left: rect.left
    }
  })
  assert.ok(
    menuBounds.top >= 0 &&
      menuBounds.left >= 0 &&
      menuBounds.right <= 1120 &&
      menuBounds.bottom <= 720,
    `Custom dropdown escaped the viewport: ${JSON.stringify(menuBounds)}`
  )
  await page.screenshot({
    path: resolve(artifactDirectory, 'live-ui-polish-dropdown-dark.png')
  })

  await sampleDropdown.press('End')
  await sampleDropdown.press('Enter')
  assert.equal(await sampleDropdown.getAttribute('data-value'), '500')
  await sampleDropdown.press('Home')
  await sampleDropdown.press('Enter')
  assert.equal(await sampleDropdown.getAttribute('data-value'), '5000')

  const darkTheme = await page.evaluate(() => ({
    theme: document.documentElement.dataset.theme,
    control: getComputedStyle(document.documentElement).getPropertyValue('--control-bg'),
    popover: getComputedStyle(document.documentElement).getPropertyValue('--popover-bg')
  }))
  await page.getByRole('button', { name: '切换到浅色模式', exact: true }).click()
  const lightTheme = await page.evaluate(() => ({
    theme: document.documentElement.dataset.theme,
    control: getComputedStyle(document.documentElement).getPropertyValue('--control-bg'),
    popover: getComputedStyle(document.documentElement).getPropertyValue('--popover-bg')
  }))
  assert.equal(darkTheme.theme, 'dark')
  assert.equal(lightTheme.theme, 'light')
  assert.notEqual(lightTheme.control, darkTheme.control)
  assert.notEqual(lightTheme.popover, darkTheme.popover)

  await sampleDropdown.click()
  await sampleListbox.waitFor()
  await page.screenshot({
    path: resolve(artifactDirectory, 'live-ui-polish-dropdown-light.png')
  })
  await sampleDropdown.press('Escape')

  console.log(
    JSON.stringify({
      layout,
      menuBounds,
      screenshots: [
        resolve(artifactDirectory, 'live-ui-polish-1120-dark.png'),
        resolve(artifactDirectory, 'live-ui-polish-dropdown-dark.png'),
        resolve(artifactDirectory, 'live-ui-polish-dropdown-light.png')
      ]
    })
  )
} finally {
  await electronApp.close()
}
