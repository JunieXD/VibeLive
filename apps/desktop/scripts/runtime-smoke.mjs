import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron } from 'playwright-core'

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = resolve(desktopRoot, '..', '..')
const artifactDirectory = resolve(desktopRoot, 'artifacts', 'runtime-smoke')
const proofPath = resolve(artifactDirectory, 'proof.json')
const screenshotPath = resolve(artifactDirectory, 'overlay.png')
const aiCallsScreenshotPath = resolve(artifactDirectory, 'ai-calls.png')
const aiCallsTimelineScreenshotPath = resolve(
  artifactDirectory,
  'ai-calls-timeline.png'
)
const aiCallsOnly = process.argv.includes('--ai-calls-only')
const proofScope = aiCallsOnly
  ? 'deterministic-no-external-electron-fastapi-ai-call-log'
  : 'deterministic-no-external-electron-fastapi-overlay-ai-call-log'
const syntheticFrameBase64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='

await rm(artifactDirectory, { recursive: true, force: true })
await mkdir(artifactDirectory, { recursive: true })

if (process.platform !== 'win32') {
  const skipped = {
    status: 'skipped',
    proof_scope: proofScope,
    reason: 'The Electron overlay integration smoke is currently supported on Windows only.',
    platform: process.platform
  }
  await writeFile(proofPath, `${JSON.stringify(skipped, null, 2)}\n`)
  console.log(`Runtime smoke skipped: ${proofPath}`)
  process.exit(0)
}

function reservePort() {
  return new Promise((resolvePort, reject) => {
    const server = createServer()
    server.unref()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      assert.ok(address && typeof address === 'object')
      const port = address.port
      server.close((error) => (error ? reject(error) : resolvePort(port)))
    })
  })
}

async function waitFor(description, operation, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs
  let lastError
  let lastValue
  while (Date.now() < deadline) {
    try {
      const value = await operation()
      if (value) return value
      lastValue = value
    } catch (error) {
      lastError = error
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 100))
  }
  throw new Error(
    `${description} did not become ready.${
      lastError
        ? ` ${String(lastError)}`
        : lastValue === undefined
          ? ''
          : ` Last value: ${JSON.stringify(lastValue)}`
    }`
  )
}

async function terminate(child) {
  if (!child || child.exitCode !== null) return
  child.kill('SIGTERM')
  const exited = once(child, 'exit')
  const graceful = await Promise.race([
    exited.then(() => true),
    new Promise((resolveWait) => setTimeout(() => resolveWait(false), 4_000))
  ])
  if (graceful || child.exitCode !== null) return
  const killer = spawn('taskkill.exe', ['/pid', String(child.pid), '/t', '/f'], {
    stdio: 'ignore',
    windowsHide: true
  })
  await once(killer, 'exit')
}

const temporaryDirectory = await mkdtemp(resolve(tmpdir(), 'advx-runtime-smoke-'))
const backendDataDirectory = resolve(temporaryDirectory, 'backend-data')
const electronUserDataDirectory = resolve(temporaryDirectory, 'electron-user-data')
const readyFile = resolve(temporaryDirectory, 'backend-ready.json')
const port = await reservePort()
const localToken = `runtime-smoke-${crypto.randomUUID()}`
const backendUrl = `http://127.0.0.1:${port}`
const python = resolve(repositoryRoot, 'apps', 'backend', '.venv', 'Scripts', 'python.exe')
const backendScript = resolve(
  repositoryRoot,
  'apps',
  'backend',
  'scripts',
  'desktop_runtime_smoke_server.py'
)
const backend = spawn(
  python,
  [
    backendScript,
    '--port',
    String(port),
    '--data-dir',
    backendDataDirectory,
    '--token',
    localToken,
    '--ready-file',
    readyFile
  ],
  {
    cwd: repositoryRoot,
    env: process.env,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true
  }
)
let backendLog = ''
backend.stdout.setEncoding('utf8')
backend.stderr.setEncoding('utf8')
backend.stdout.on('data', (chunk) => {
  backendLog += chunk
})
backend.stderr.on('data', (chunk) => {
  backendLog += chunk
})

let electronApp
let proof
let sessionStarted = false
try {
  const ready = await waitFor('recorded backend metadata', async () => {
    try {
      return JSON.parse(await readFile(readyFile, 'utf8'))
    } catch {
      return null
    }
  })
  await waitFor('FastAPI health endpoint', async () => {
    const response = await fetch(`${backendUrl}/health`)
    return response.ok
  })

  const { ELECTRON_RUN_AS_NODE: _electronRunAsNode, ...electronEnvironment } = process.env
  electronApp = await electron.launch({
    args: ['.', `--user-data-dir=${electronUserDataDirectory}`],
    cwd: desktopRoot,
    env: {
      ...electronEnvironment,
      ADVX_BACKEND_EXTERNAL: '1',
      ADVX_BACKEND_URL: backendUrl,
      ADVX_LOCAL_TOKEN: localToken,
      ELECTRON_DISABLE_SECURITY_WARNINGS: 'true'
    }
  })
  const controlPage = await electronApp.firstWindow()
  await controlPage.getByRole('heading', { name: '直播控制台', exact: true }).waitFor()
  await waitFor('Electron BackendClient connection', () =>
    controlPage.evaluate(async () => {
      const status = await window.advx.getBackendStatus()
      if (status.connection !== 'connected') {
        throw new Error(`pending backend status: ${JSON.stringify(status)}`)
      }
      return status
    })
  )

  await controlPage.evaluate(() => {
    window.__advxRuntimeSmokeBarrages = []
    window.advx.onBackendBarrage((event) => {
      window.__advxRuntimeSmokeBarrages.push(event)
    })
  })
  const saved = await controlPage.evaluate((provider) => window.advx.saveModelConfig(provider), {
    ...ready.provider
  })
  assert.equal(saved.ok, true)
  assert.equal(saved.providerProfileId, ready.provider.providerProfileId)
  assert.equal(saved.runtimeApplyRequired, false)

  const workspace = await waitFor('persisted initial audience workspace', () =>
    controlPage.evaluate(() => window.advx.loadAudienceWorkspace())
  )
  const started = await controlPage.evaluate(
    ({ audienceWorkspace, requestId }) =>
      window.advx.startBackendSession(audienceWorkspace, requestId),
    {
      audienceWorkspace: workspace,
      requestId: 'desktop-runtime-smoke'
    }
  )
  sessionStarted = true
  assert.equal(started.state, 'running')
  assert.ok(started.sessionId)
  await controlPage.evaluate(() => window.advx.notifyVoiceActivity(Date.now()))
  let overlayPage
  if (!aiCallsOnly) {
    await controlPage.evaluate(() => window.advx.showOverlay())
    overlayPage = await waitFor('real Overlay BrowserWindow', async () => {
      const windows = electronApp.windows()
      return windows.find((page) => page.url().replaceAll('\\', '/').includes('/overlay/')) ?? null
    })
    await overlayPage.waitForLoadState('domcontentloaded')
  }
  const syntheticFrameInputId = 'desktop-runtime-smoke-frame'
  await controlPage.evaluate(
    ({ inputId, encoded, capturedAtMs }) => {
      const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0))
      return window.advx.submitVisualFrame({
        inputId,
        capturedAtMs,
        mimeType: 'image/png',
        changeScore: 0,
        body: bytes
      })
    },
    {
      inputId: syntheticFrameInputId,
      encoded: syntheticFrameBase64,
      capturedAtMs: ready.synthetic_frame_captured_at_ms
    }
  )
  await controlPage.evaluate(() =>
    window.advx.submitUserText('deterministic runtime smoke input')
  )

  const barrage = await waitFor(
    'real WebSocket barrage in the control renderer',
    () =>
      controlPage.evaluate(
        () => window.__advxRuntimeSmokeBarrages.at(-1) ?? null
      ),
    20_000
  )
  assert.equal(barrage.sessionId, started.sessionId)
  assert.equal(barrage.text, ready.expected_barrage_text)

  let overlayText = null
  if (overlayPage) {
    const overlayBarrage = overlayPage.locator('.overlay-barrage', {
      hasText: ready.expected_barrage_text
    })
    await overlayBarrage.waitFor({ state: 'visible', timeout: 10_000 })
    await waitFor('fully visible Overlay barrage', async () => {
      const [box, viewportWidth] = await Promise.all([
        overlayBarrage.boundingBox(),
        overlayPage.evaluate(() => window.innerWidth)
      ])
      return box && box.x >= 0 && box.x + box.width <= viewportWidth ? box : null
    })
    await overlayPage.screenshot({ path: screenshotPath })
    overlayText = (await overlayBarrage.textContent())?.trim()
    assert.equal(overlayText, ready.expected_barrage_text)
  }

  const aiCallSeedResponse = await fetch(`${backendUrl}/__runtime-smoke/ai-call`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${localToken}`,
      'Content-Type': 'application/json',
      'X-ADVX-Protocol-Version': String(ready.runtime_protocol)
    },
    body: JSON.stringify({ session_id: started.sessionId })
  })
  assert.equal(aiCallSeedResponse.ok, true)
  const seededAiCall = await aiCallSeedResponse.json()
  assert.match(seededAiCall.correlation_id, /^memory-[0-9a-f]{32}$/)

  await controlPage.getByRole('button', { name: 'AI 调用', exact: true }).click()
  await controlPage.getByRole('heading', { name: 'AI 调用', exact: true }).waitFor()
  const aiCallArticle = controlPage.getByRole('article')
  await aiCallArticle.getByText('发送摘要', { exact: true }).waitFor({ timeout: 10_000 })
  await aiCallArticle.getByText('接收与解析结果', { exact: true }).waitFor()
  await aiCallArticle.getByText(seededAiCall.correlation_id, { exact: true }).waitFor()
  await aiCallArticle.locator('pre').filter({ hasText: seededAiCall.visible_text }).waitFor()
  const aiCallsPanel = aiCallArticle.getByRole('heading', {
    name: '发送摘要',
    exact: true
  })
  await waitFor('visible AI call log detail', async () => {
    const box = await aiCallsPanel.boundingBox()
    return box && box.width > 0 && box.height > 0 ? box : null
  })
  await controlPage.screenshot({ path: aiCallsScreenshotPath })
  const timelineHeading = aiCallArticle.getByRole('heading', {
    name: '完整 Timeline',
    exact: true
  })
  await timelineHeading.scrollIntoViewIfNeeded()
  await timelineHeading.waitFor()
  const timeline = aiCallArticle.locator('ol')
  await timeline.getByText('preparing', { exact: true }).waitFor()
  await timeline.getByText('sent', { exact: true }).waitFor()
  await timeline.getByText('received', { exact: true }).waitFor()
  await timeline.getByText('completed', { exact: true }).waitFor()
  await controlPage.screenshot({ path: aiCallsTimelineScreenshotPath })

  const runtime = await controlPage.evaluate((sessionId) =>
    window.advx.queryAudienceRuntime(sessionId), started.sessionId)
  const traces = await controlPage.evaluate((sessionId) =>
    window.advx.queryDebugTraces(sessionId), started.sessionId)
  const frameTrace = traces.items.find((item) => item.frame_hashes.length > 0)
  assert.ok(frameTrace, 'The real viewer request trace did not include the synthetic frame.')
  const backendProofResponse = await fetch(`${backendUrl}/__runtime-smoke/proof`, {
    headers: {
      Authorization: `Bearer ${localToken}`,
      'X-ADVX-Protocol-Version': String(ready.runtime_protocol)
    }
  })
  assert.equal(backendProofResponse.ok, true)
  const backendProof = await backendProofResponse.json()
  assert.equal(backendProof.backend_pid, ready.backend_pid)
  assert.equal(backendProof.external_transport_call_count, 0)
  assert.ok(backendProof.viewer_calls >= 1)

  proof = {
    status: 'passed',
    proof_scope: proofScope,
    platform: process.platform,
    backend: {
      pid: backendProof.backend_pid,
      url: backendUrl,
      fastapi_title: 'ADVX Live Backend',
      sqlite_started: backendProof.sqlite_started,
      sqlite_path: backendProof.sqlite_path,
      production_coordinator: true,
      deterministic_adapters: backendProof.deterministic_adapters,
      capability_probe_calls: backendProof.capability_probe_calls
    },
    transport: {
      electron_backend_client: true,
      fastapi_http: true,
      realtime_websocket: true,
      real_overlay_ipc: !aiCallsOnly,
      manual_barrage_push: false,
      synthetic_frame_input_id: syntheticFrameInputId,
      synthetic_frame_hash: frameTrace.frame_hashes[0],
      external_transport_call_count: backendProof.external_transport_call_count
    },
    identity: {
      session_id: started.sessionId,
      config_revision: runtime.config_revision,
      audience_epoch: barrage.audienceEpoch,
      barrage_id: barrage.barrageId,
      observation_id: barrage.observationId,
      generation_request_id: barrage.generationRequestId,
      viewer_instance_id: barrage.viewerInstanceId,
      persona_id: barrage.personaId,
      viewer_sequence: barrage.viewerSequence
    },
    overlay: {
      rendered: Boolean(overlayPage),
      text: overlayText,
      window_url: overlayPage?.url() ?? null,
      screenshot: overlayPage ? screenshotPath : null
    },
    ai_call_log: {
      rendered: true,
      call_id: seededAiCall.call_id,
      correlation_id: seededAiCall.correlation_id,
      request_summary_visible: true,
      parsed_response_visible: true,
      timeline_visible: true,
      screenshot: aiCallsScreenshotPath,
      timeline_screenshot: aiCallsTimelineScreenshotPath
    },
    calls: {
      viewer: backendProof.viewer_calls,
      memory_extractor: backendProof.memory_extractor_calls
    }
  }
} finally {
  if (sessionStarted && electronApp) {
    const pages = electronApp.windows()
    const controlPage = pages.find((page) => page.url().replaceAll('\\', '/').includes('/control/'))
    await controlPage?.evaluate(() => window.advx.stopBackendSession()).catch(() => undefined)
  }
  await electronApp?.close().catch(() => undefined)
  await terminate(backend)
  await writeFile(resolve(artifactDirectory, 'backend.log'), backendLog)
  await rm(temporaryDirectory, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
}

assert.ok(proof)
proof.cleanup = {
  electron_closed: true,
  backend_stopped: backend.exitCode !== null || backend.signalCode !== null,
  temporary_directory_removed: true
}
await writeFile(proofPath, `${JSON.stringify(proof, null, 2)}\n`)
console.log(`Runtime smoke proof: ${proofPath}`)
if (!aiCallsOnly) console.log(`Overlay screenshot: ${screenshotPath}`)
console.log(`AI call log screenshot: ${aiCallsScreenshotPath}`)
console.log(`AI call timeline screenshot: ${aiCallsTimelineScreenshotPath}`)
