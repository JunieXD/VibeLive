import { mkdir, rm } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright-core'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const artifactDirectory = resolve(root, 'artifacts')
const userDataDirectory = resolve(artifactDirectory, 'audience-shot-user-data')
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
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.getByRole('button', { name: /观众配置/ }).click()
  await page.getByRole('heading', { name: '观众配置', exact: true }).waitFor()
  await page.waitForTimeout(600)
  const audienceTypography = await page.evaluate(() => {
    const workspace = document.querySelector('[data-audience-workspace]')
    const findExactText = (text) =>
      Array.from(document.querySelectorAll('*')).find(
        (element) => element.children.length === 0 && element.textContent?.trim() === text
      )
    const findExactButton = (text) =>
      Array.from(document.querySelectorAll('button')).find(
        (element) => element.textContent?.trim() === text
      )
    const runtimeTitle = findExactText('Runtime 未启动')
    const applyButton = findExactButton('应用到当前会话')
    const modeLabel = findExactText('观众模式')
    const parameterButton = findExactButton('参数')
    return {
      workspaceRadius: workspace ? Number.parseFloat(getComputedStyle(workspace).borderRadius) : 0,
      runtimeTitleSize: runtimeTitle
        ? Number.parseFloat(getComputedStyle(runtimeTitle).fontSize)
        : 0,
      applyButtonSize: applyButton
        ? Number.parseFloat(getComputedStyle(applyButton).fontSize)
        : 0,
      modeLabelSize: modeLabel
        ? Number.parseFloat(getComputedStyle(modeLabel).fontSize)
        : 0,
      parameterRadius: parameterButton
        ? Number.parseFloat(getComputedStyle(parameterButton).borderRadius)
        : 0
    }
  })
  if (
    audienceTypography.workspaceRadius < 8 ||
    audienceTypography.runtimeTitleSize < 14 ||
    audienceTypography.applyButtonSize < 12 ||
    audienceTypography.modeLabelSize < 12 ||
    audienceTypography.parameterRadius < 8
  ) {
    throw new Error(
      `Audience typography or radius scale regressed: ${JSON.stringify(audienceTypography)}`
    )
  }
  await page.screenshot({ path: resolve(artifactDirectory, 'audience-declutter.png') })

  await page.locator('[data-audience-persona-open]').first().click()
  await page.getByRole('dialog', { name: /编辑/ }).waitFor()
  await page.waitForTimeout(300)
  await page.screenshot({ path: resolve(artifactDirectory, 'audience-persona-drawer.png') })
  await page.keyboard.press('Escape')

  await page.getByRole('button', { name: '模式参数', exact: true }).click()
  await page.waitForTimeout(300)
  await page.screenshot({ path: resolve(artifactDirectory, 'audience-param-popover.png') })
  const ambienceDropdown = page.getByRole('combobox', { name: '冷场策略', exact: true })
  await ambienceDropdown.click()
  const ambienceListbox = page.getByRole('listbox', { name: '冷场策略', exact: true })
  await ambienceListbox.waitFor()
  await page.screenshot({ path: resolve(artifactDirectory, 'audience-param-dropdown.png') })
  await ambienceListbox.getByRole('option', { name: '持续暖场', exact: true }).click()
  if (
    (await ambienceDropdown.getAttribute('data-value')) !== 'continuous' ||
    !(await page.getByRole('dialog', { name: '模式参数', exact: true }).isVisible())
  ) {
    throw new Error('Nested mode dropdown closed its parent popover or lost the selection.')
  }
  await ambienceDropdown.click()
  await ambienceListbox.waitFor()
  await page.keyboard.press('Escape')
  if (
    (await ambienceDropdown.getAttribute('aria-expanded')) !== 'false' ||
    (await ambienceListbox.isVisible()) ||
    !(await page.getByRole('dialog', { name: '模式参数', exact: true }).isVisible()) ||
    !(await ambienceDropdown.evaluate((element) => document.activeElement === element))
  ) {
    throw new Error('Nested mode dropdown Escape handling closed its parent or lost focus.')
  }
  await page.keyboard.press('Escape')

  await page.getByRole('button', { name: '更多操作', exact: true }).click()
  await page.waitForTimeout(300)
  await page.screenshot({ path: resolve(artifactDirectory, 'audience-more-menu.png') })
  await page.keyboard.press('Escape')

  await page.setViewportSize({ width: 1120, height: 720 })
  await page.waitForTimeout(300)
  const overflow = await page.evaluate(() => {
    const workspace = document.querySelector('[data-audience-workspace]')
    return {
      document: document.documentElement.scrollWidth > window.innerWidth,
      workspace: workspace ? workspace.scrollWidth > workspace.clientWidth : true
    }
  })
  if (overflow.document || overflow.workspace) {
    throw new Error(`Audience workspace overflowed at 1120x720: ${JSON.stringify(overflow)}`)
  }
  await page.screenshot({ path: resolve(artifactDirectory, 'audience-declutter-1120.png') })

  await page.locator('[data-audience-persona-open]').first().click()
  await page.getByRole('dialog', { name: /编辑/ }).waitFor()
  const drawer = await page.evaluate(() => {
    const workspaceElement = document.querySelector('[data-audience-workspace]')
    const layerElement = document.querySelector('[data-audience-editor-layer]')
    const panelElement = document.querySelector('[data-audience-editor-layer] [role="dialog"]')
    const workspace = workspaceElement?.getBoundingClientRect()
    const layer = layerElement?.getBoundingClientRect()
    const panel = panelElement?.getBoundingClientRect()
    return workspace && layer && panel && panelElement
      ? {
          workspaceLeft: workspace.left,
          workspaceRight: workspace.right,
          workspaceTop: workspace.top,
          workspaceBottom: workspace.bottom,
          layerTop: layer.top,
          layerBottom: layer.bottom,
          panelLeft: panel.left,
          panelRight: panel.right,
          panelTop: panel.top,
          panelBottom: panel.bottom,
          viewportWidth: window.innerWidth,
          viewportHeight: window.innerHeight,
          panelStyle: {
            height: getComputedStyle(panelElement).height,
            minHeight: getComputedStyle(panelElement).minHeight,
            overflow: getComputedStyle(panelElement).overflow,
            position: getComputedStyle(panelElement).position
          }
        }
      : null
  })
  if (
    !drawer ||
    drawer.panelLeft < 0 ||
    drawer.panelRight > drawer.viewportWidth ||
    drawer.panelTop < 0 ||
    drawer.panelBottom > drawer.viewportHeight
  ) {
    throw new Error(`Audience drawer escaped the workspace: ${JSON.stringify(drawer)}`)
  }
  await page.screenshot({ path: resolve(artifactDirectory, 'audience-persona-drawer-1120.png') })
  await page.keyboard.press('Escape')

  await page.setViewportSize({ width: 1440, height: 900 })
  await page.getByRole('button', { name: /直播观众/ }).click()
  await page.getByRole('heading', { name: '直播观众', exact: true }).waitFor()
  await page.waitForTimeout(300)
  await page.screenshot({ path: resolve(artifactDirectory, 'viewer-management-idle.png') })

  await page.setViewportSize({ width: 1120, height: 720 })
  await page.waitForTimeout(300)
  const viewerLayout = await page.evaluate(() => {
    const workspace = document.querySelector('[data-control-workspace]')
    const viewerManagement = document.querySelector('.viewer-management')
    const workspaceBounds = workspace?.getBoundingClientRect()
    const viewerBounds = viewerManagement?.getBoundingClientRect()
    const childBounds = viewerManagement
      ? Array.from(viewerManagement.children).map((element) => element.getBoundingClientRect())
      : []
    const contentBounds = childBounds.length > 0
      ? {
          left: Math.min(...childBounds.map((bounds) => bounds.left)),
          right: Math.max(...childBounds.map((bounds) => bounds.right)),
          top: Math.min(...childBounds.map((bounds) => bounds.top)),
          bottom: Math.max(...childBounds.map((bounds) => bounds.bottom))
        }
      : null
    return {
      documentOverflow: document.documentElement.scrollWidth > window.innerWidth,
      workspaceOverflow: workspace ? workspace.scrollWidth > workspace.clientWidth : true,
      viewerWithinWorkspace: Boolean(
        workspaceBounds &&
        viewerBounds &&
        viewerBounds.left >= workspaceBounds.left &&
        viewerBounds.right <= workspaceBounds.right &&
        viewerBounds.top >= workspaceBounds.top &&
        viewerBounds.bottom <= workspaceBounds.bottom
      ),
      emptyStateCentered: Boolean(
        viewerBounds &&
        contentBounds &&
        Math.abs((contentBounds.left + contentBounds.right) / 2 - (viewerBounds.left + viewerBounds.right) / 2) <= 2 &&
        Math.abs((contentBounds.top + contentBounds.bottom) / 2 - (viewerBounds.top + viewerBounds.bottom) / 2) <= 2
      ),
      viewerRadius: viewerManagement
        ? Number.parseFloat(getComputedStyle(viewerManagement).borderRadius)
        : 0
    }
  })
  if (
    viewerLayout.documentOverflow ||
    viewerLayout.workspaceOverflow ||
    !viewerLayout.viewerWithinWorkspace ||
    !viewerLayout.emptyStateCentered ||
    viewerLayout.viewerRadius < 8
  ) {
    throw new Error(`Viewer management layout escaped at 1120x720: ${JSON.stringify(viewerLayout)}`)
  }
  await page.screenshot({ path: resolve(artifactDirectory, 'viewer-management-idle-1120.png') })

  console.log('done')
} finally {
  await electronApp.close()
}
