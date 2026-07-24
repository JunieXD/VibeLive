import assert from 'node:assert/strict'
import { execFile, spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdir, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:net'
import { dirname, resolve } from 'node:path'
import { promisify } from 'node:util'
import { fileURLToPath } from 'node:url'
import electronPath from 'electron'
import { _electron as electron } from 'playwright-core'

const execFileAsync = promisify(execFile)
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const artifactDirectory = resolve(root, 'artifacts', 'tray-smoke')
const userDataDirectory = resolve(artifactDirectory, 'user-data')
await rm(artifactDirectory, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
await mkdir(userDataDirectory, { recursive: true })

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

async function waitFor(check, message, timeoutMs = 8_000) {
  const deadline = Date.now() + timeoutMs
  let lastError
  while (Date.now() < deadline) {
    try {
      const value = await check()
      if (value) return value
    } catch (error) {
      lastError = error
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 100))
  }
  throw new Error(`${message}${lastError ? `: ${lastError.message}` : ''}`)
}

async function descendantProcessIds(parentProcessId) {
  if (process.platform !== 'win32') return []
  const script = `
$all = @(Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId)
$parents = @([uint32]${parentProcessId})
$descendants = @()
while ($parents.Count -gt 0) {
  $children = @($all | Where-Object { $parents -contains $_.ParentProcessId })
  if ($children.Count -eq 0) { break }
  $descendants += $children
  $parents = @($children | ForEach-Object { [uint32]$_.ProcessId })
}
@($descendants | ForEach-Object { [uint32]$_.ProcessId }) | ConvertTo-Json -Compress
`
  const { stdout } = await execFileAsync('powershell.exe', [
    '-NoProfile',
    '-NonInteractive',
    '-Command',
    script
  ])
  const trimmed = stdout.trim()
  if (!trimmed) return []
  const parsed = JSON.parse(trimmed)
  return Array.isArray(parsed) ? parsed : [parsed]
}

async function runningProcessIds(processIds) {
  if (process.platform !== 'win32' || processIds.length === 0) return []
  const script = `
$ids = @(${processIds.join(',')})
@($ids | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue }) |
  ConvertTo-Json -Compress
`
  const { stdout } = await execFileAsync('powershell.exe', [
    '-NoProfile',
    '-NonInteractive',
    '-Command',
    script
  ])
  const trimmed = stdout.trim()
  if (!trimmed) return []
  const parsed = JSON.parse(trimmed)
  return Array.isArray(parsed) ? parsed : [parsed]
}

const backendPort = await reservePort()
const appEnvironment = {
  ...electronEnvironment,
  ADVX_BACKEND_EXTERNAL: '0',
  ADVX_BACKEND_URL: `http://127.0.0.1:${backendPort}`,
  ADVX_TRAY_SMOKE: '1',
  ELECTRON_DISABLE_SECURITY_WARNINGS: 'true'
}
let electronApp
let electronProcess
let proof

try {
  electronApp = await electron.launch({
    args: ['.', `--user-data-dir=${userDataDirectory}`],
    cwd: root,
    env: appEnvironment
  })
  electronProcess = electronApp.process()
  const page = await electronApp.firstWindow()
  await page.getByRole('heading', { name: '直播控制台', exact: true }).waitFor()
  await page.waitForFunction(() => document.body.textContent?.includes('后端 · 已连接'))

  const trayState = await waitFor(
    () =>
      electronApp.evaluate(() => {
        const handle = globalThis.__advxTraySmoke
        if (!handle) return null
        return {
          destroyed: handle.tray.isDestroyed(),
          openLabel: handle.menu.getMenuItemById('open-advx-live')?.label,
          quitLabel: handle.menu.getMenuItemById(handle.quitMenuItemId)?.label
        }
      }),
    'The real Electron tray was not created'
  )
  assert.deepEqual(trayState, {
    destroyed: false,
    openLabel: '打开 ADVX Live',
    quitLabel: '彻底退出'
  })

  await electronApp.evaluate(({ BrowserWindow }) => {
    const controlWindow = BrowserWindow.getAllWindows().find((window) =>
      window.webContents.getURL().includes('/control/')
    )
    if (!controlWindow) throw new Error('Control window is missing.')
    controlWindow.hide()
    globalThis.__advxTraySmoke?.tray.emit('click')
  })
  await waitFor(
    () =>
      electronApp.evaluate(({ BrowserWindow }) => {
        const controlWindow = BrowserWindow.getAllWindows().find((window) =>
          window.webContents.getURL().includes('/control/')
        )
        return Boolean(controlWindow?.isVisible() && !controlWindow.isMinimized())
      }),
    'Clicking the tray icon did not restore the control window'
  )

  await electronApp.evaluate(({ BrowserWindow }) => {
    const controlWindow = BrowserWindow.getAllWindows().find((window) =>
      window.webContents.getURL().includes('/control/')
    )
    controlWindow?.hide()
  })
  const secondInstance = spawn(
    electronPath,
    ['.', `--user-data-dir=${userDataDirectory}`],
    {
      cwd: root,
      env: appEnvironment,
      stdio: 'ignore',
      windowsHide: true
    }
  )
  const [secondExitCode] = await Promise.race([
    once(secondInstance, 'exit'),
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error('Second ADVX Live instance did not exit.')), 8_000)
    )
  ])
  assert.equal(secondExitCode, 0)
  await waitFor(
    () =>
      electronApp.evaluate(({ BrowserWindow }) => {
        const controlWindow = BrowserWindow.getAllWindows().find((window) =>
          window.webContents.getURL().includes('/control/')
        )
        return Boolean(controlWindow?.isVisible())
      }),
    'The existing instance was not shown after a duplicate launch'
  )

  const childProcessIds = await descendantProcessIds(electronProcess.pid)
  assert.ok(childProcessIds.length > 0, 'No managed Electron/backend child processes were found.')
  const exited =
    electronProcess.exitCode === null ? once(electronProcess, 'exit') : Promise.resolve()
  await electronApp.evaluate(({ BrowserWindow }) => {
    const controlWindow = BrowserWindow.getAllWindows().find((window) =>
      window.webContents.getURL().includes('/control/')
    )
    if (!controlWindow) throw new Error('Control window is missing before shutdown.')
    controlWindow.close()
  })
  await Promise.race([
    exited,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error('ADVX Live did not exit after the window closed.')), 10_000)
    )
  ])
  await new Promise((resolveWait) => setTimeout(resolveWait, 300))
  const leakedProcessIds = await runningProcessIds(childProcessIds)
  assert.deepEqual(
    leakedProcessIds,
    [],
    `ADVX Live left child processes running: ${leakedProcessIds.join(', ')}`
  )

  proof = {
    trayState,
    duplicateInstanceExitCode: secondExitCode,
    managedChildProcessIds: childProcessIds,
    leakedProcessIds
  }
  await writeFile(
    resolve(artifactDirectory, 'tray-smoke-proof.json'),
    JSON.stringify(proof, null, 2),
    'utf8'
  )
  console.log(
    `Tray smoke passed: real tray created, click restored the window, duplicate launch exited, window close exited the app, and ${childProcessIds.length} child process(es) were cleaned up.`
  )
  console.log(`Proof: ${resolve(artifactDirectory, 'tray-smoke-proof.json')}`)
} finally {
  if (electronApp && electronProcess?.exitCode === null) {
    await electronApp.close().catch(() => undefined)
  }
}
