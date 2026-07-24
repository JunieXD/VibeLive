import { access, readFile } from 'node:fs/promises'
import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'

const require = createRequire(import.meta.url)
const electronDirectory = dirname(require.resolve('electron/package.json'))
const pathFile = join(electronDirectory, 'path.txt')
const installScript = join(electronDirectory, 'install.js')
const fallbackMirror = 'https://npmmirror.com/mirrors/electron/'

async function installedExecutable() {
  try {
    const relativePath = (await readFile(pathFile, 'utf8')).trim()
    if (!relativePath) return null
    const executable = join(electronDirectory, 'dist', relativePath)
    await access(executable)
    return executable
  } catch {
    return null
  }
}

function runInstaller() {
  return new Promise((resolveInstall, rejectInstall) => {
    const child = spawn(process.execPath, [installScript], {
      stdio: 'inherit',
      env: {
        ...process.env,
        ELECTRON_MIRROR: process.env.ELECTRON_MIRROR ?? fallbackMirror
      }
    })
    child.once('error', rejectInstall)
    child.once('exit', (code, signal) => {
      if (code === 0) {
        resolveInstall()
        return
      }
      rejectInstall(
        new Error(
          signal
            ? `Electron installer stopped by ${signal}.`
            : `Electron installer exited with code ${code ?? 'unknown'}.`
        )
      )
    })
  })
}

if (!(await installedExecutable())) {
  console.log('Electron runtime is missing; installing it before startup...')
  await runInstaller()
}

const executable = await installedExecutable()
if (!executable) {
  throw new Error('Electron installation completed without a usable executable.')
}

console.log(`Electron runtime ready: ${executable}`)
