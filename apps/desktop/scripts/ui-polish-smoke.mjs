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
  await page.getByRole('button', { name: '房间互动', exact: true }).click()
  assert.equal(
    await page.getByLabel('发送房间消息', { exact: true }).getAttribute('placeholder'),
    '配置供应商后可与 AI 观众互动'
  )
  assert.equal(
    (await page.locator('body').innerText()).includes('Provider'),
    false,
    'English Provider copy is still visible in the interaction view.'
  )
  await page.getByRole('button', { name: '直播控制台', exact: true }).click()

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
      stagePanel: bounds('.stage-panel'),
      commandMeter: bounds('.command-meter'),
      rightRail: bounds('.right-rail'),
      viewerPanel: bounds('.viewer-panel'),
      viewerList: bounds('.viewer-list'),
      mixerPanel: bounds('.mixer-panel'),
      mixerScroll: bounds('.mixer-scroll'),
      mixerScrollTop,
      mixerRows: document.querySelectorAll('.mixer-row').length,
      commandButtons: [...document.querySelectorAll('.command-button')].map((button) => ({
        clientWidth: button.clientWidth,
        scrollWidth: button.scrollWidth,
        clientHeight: button.clientHeight,
        scrollHeight: button.scrollHeight
      })),
      deviceButtons: [...document.querySelectorAll('.device-control .ghost-button')].map(
        (button) => ({
          clientWidth: button.clientWidth,
          scrollWidth: button.scrollWidth,
          clientHeight: button.clientHeight,
          scrollHeight: button.scrollHeight
        })
      )
    }
  })

  assert.ok(layout, 'Live dashboard layout was not available.')
  assert.equal(layout.documentOverflow, false, 'Live dashboard overflowed horizontally.')
  assert.ok(layout.stagePanel && layout.commandMeter && layout.rightRail && layout.viewerPanel && layout.viewerList)
  assert.ok(layout.mixerPanel && layout.mixerScroll)
  assert.ok(
    layout.commandMeter.left >= layout.stagePanel.left &&
      layout.commandMeter.right <= layout.stagePanel.right,
    'The microphone status is clipped outside the live stage at 1120x720.'
  )
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
  assert.equal(layout.mixerRows, 8)
  assert.equal(layout.commandButtons.length, 3)
  assert.ok(
    layout.commandButtons.every(
      (button) =>
        button.scrollWidth <= button.clientWidth + 1 &&
        button.scrollHeight <= button.clientHeight + 1
    ),
    'A live command button wraps or clips its label at 1120x720.'
  )
  assert.equal(layout.deviceButtons.length, 3)
  assert.ok(
    layout.deviceButtons.every(
      (button) =>
        button.scrollWidth <= button.clientWidth + 1 &&
        button.scrollHeight <= button.clientHeight + 1
    ),
    'A live device button wraps or clips its label at 1120x720.'
  )
  assert.ok(
    layout.mixerScroll.scrollHeight > layout.mixerScroll.clientHeight &&
      layout.mixerScrollTop > 0,
    'The mixer card does not expose its scrollable status rows.'
  )

  const commandTooltipCases = [
    ['暂停', '暂停 AI 观察和麦克风，画面预览会继续保留。'],
    ['清屏', '清空房间互动记录，以及屏幕弹幕和悬浮互动窗中的内容。'],
    ['显示', '按设置打开已启用的弹幕输出。']
  ]
  for (const [buttonName, tooltipText] of commandTooltipCases) {
    await page.getByRole('button', { name: buttonName, exact: true }).hover()
    const tooltip = page.getByRole('tooltip').filter({ hasText: tooltipText })
    await tooltip.waitFor()
    assert.equal((await tooltip.innerText()).trim(), tooltipText)
    const tooltipBounds = await tooltip.evaluate((element) => {
      const stage = document.querySelector('.stage-panel')
      if (!(stage instanceof HTMLElement)) return null
      const tooltipRect = element.getBoundingClientRect()
      const stageRect = stage.getBoundingClientRect()
      return {
        tooltip: {
          top: tooltipRect.top,
          right: tooltipRect.right,
          bottom: tooltipRect.bottom,
          left: tooltipRect.left
        },
        stage: {
          top: stageRect.top,
          right: stageRect.right,
          bottom: stageRect.bottom,
          left: stageRect.left
        }
      }
    })
    assert.ok(tooltipBounds, `Tooltip bounds were unavailable for ${buttonName}.`)
    assert.ok(
      tooltipBounds.tooltip.left >= tooltipBounds.stage.left &&
        tooltipBounds.tooltip.right <= tooltipBounds.stage.right &&
        tooltipBounds.tooltip.top >= tooltipBounds.stage.top &&
        tooltipBounds.tooltip.bottom <= tooltipBounds.stage.bottom,
      `Tooltip escaped the live stage for ${buttonName}.`
    )
  }
  await page.screenshot({
    path: resolve(artifactDirectory, 'live-ui-polish-command-tooltip-dark.png')
  })
  await page.mouse.move(4, 4)
  await page.waitForTimeout(150)

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

  await page.getByRole('button', { name: '观众配置', exact: true }).click()
  await page.getByRole('button', { name: '模式参数', exact: true }).click()
  const modeParameters = page.getByRole('dialog', { name: '模式参数', exact: true })
  await modeParameters.waitFor()
  const algorithmMode = modeParameters.getByRole('combobox', {
    name: '算法模式',
    exact: true
  })
  assert.equal(await algorithmMode.getAttribute('data-value'), 'per_viewer')
  await algorithmMode.click()
  await page
    .getByRole('listbox', { name: '算法模式', exact: true })
    .getByRole('option', { name: '30 秒窗口聚合', exact: true })
    .click()
  assert.equal(await algorithmMode.getAttribute('data-value'), 'window_batch')
  assert.equal(
    await modeParameters.getByRole('combobox', { name: '视觉输入', exact: true }).isDisabled(),
    true
  )
  assert.equal(
    await modeParameters.getByRole('combobox', { name: '帧选择策略', exact: true }).isDisabled(),
    true
  )
  const frameCount = modeParameters.locator('label').filter({ hasText: '帧数' }).locator('input')
  const frameWindow = modeParameters
    .locator('label')
    .filter({ hasText: '窗口 ms' })
    .locator('input')
  assert.equal(await frameCount.inputValue(), '5')
  assert.equal(await frameWindow.inputValue(), '30000')
  assert.equal(await frameCount.isDisabled(), true)
  assert.equal(await frameWindow.isDisabled(), true)
  await page.screenshot({
    path: resolve(artifactDirectory, 'audience-window-batch-settings-light.png')
  })

  console.log(
    JSON.stringify({
      layout,
      menuBounds,
      screenshots: [
      resolve(artifactDirectory, 'live-ui-polish-1120-dark.png'),
      resolve(artifactDirectory, 'live-ui-polish-command-tooltip-dark.png'),
      resolve(artifactDirectory, 'live-ui-polish-dropdown-dark.png'),
      resolve(artifactDirectory, 'live-ui-polish-dropdown-light.png'),
      resolve(artifactDirectory, 'audience-window-batch-settings-light.png')
      ]
    })
  )
} finally {
  await electronApp.close()
}
