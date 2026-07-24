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
  await page.screenshot({ path: resolve(artifactDirectory, 'audience-declutter.png') })

  await page.locator('[data-audience-persona-open]').first().click()
  await page.getByRole('dialog', { name: /编辑/ }).waitFor()
  await page.waitForTimeout(300)
  await page.screenshot({ path: resolve(artifactDirectory, 'audience-persona-drawer.png') })
  await page.keyboard.press('Escape')

  await page.getByRole('button', { name: '模式参数', exact: true }).click()
  await page.waitForTimeout(300)
  await page.screenshot({ path: resolve(artifactDirectory, 'audience-param-popover.png') })
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
      )
    }
  })
  if (
    viewerLayout.documentOverflow ||
    viewerLayout.workspaceOverflow ||
    !viewerLayout.viewerWithinWorkspace
  ) {
    throw new Error(`Viewer management layout escaped at 1120x720: ${JSON.stringify(viewerLayout)}`)
  }
  await page.screenshot({ path: resolve(artifactDirectory, 'viewer-management-idle-1120.png') })

  console.log('done')
} finally {
  await electronApp.close()
}
