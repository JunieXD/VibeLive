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
  await page.getByRole('button', { name: '选择来源', exact: true }).click()
  await page.locator('.source-option').first().waitFor()
  const sourceCount = await page.locator('.source-option').count()
  if (sourceCount < 1) {
    throw new Error('Desktop source IPC returned no sources.')
  }

  console.log(
    `Monorepo desktop smoke passed: control, audience, settings, preload IPC, and ${sourceCount} desktop sources rendered.`
  )
  console.log(`Screenshot: ${resolve(artifactDirectory, 'control-console.png')}`)
} finally {
  await electronApp.close()
}
