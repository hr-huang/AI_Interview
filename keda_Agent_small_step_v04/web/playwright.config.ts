import { defineConfig } from '@playwright/test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const pythonExecutable = process.platform === 'win32'
  ? path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
  : path.join(projectRoot, '.venv', 'bin', 'python')
const apiPort = process.env.E2E_API_PORT ?? '18001'
const webPort = process.env.E2E_WEB_PORT ?? '14173'
const e2eDataDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'evidence-hiring-e2e-'))
const databasePath = path.join(e2eDataDirectory, 'web.sqlite3')
const checkpointPath = path.join(e2eDataDirectory, 'checkpoints.sqlite3')
const apiBaseUrl = `http://127.0.0.1:${apiPort}/api`
const apiOrigin = `http://127.0.0.1:${apiPort}`

// Preview builds are static and do not have Vite's development proxy. Point
// the browser at the local FastAPI server so E2E exercises the real demo API.
process.env.VITE_API_BASE_URL = '/api'
process.env.E2E_API_BASE_URL = apiBaseUrl
process.env.E2E_API_ORIGIN = apiOrigin
process.env.WEB_DATABASE_PATH = databasePath
process.env.WEB_CHECKPOINT_PATH = checkpointPath

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    channel: 'msedge',
    headless: true,
  },
  webServer: [
    {
      command: `"${pythonExecutable}" "${path.join(projectRoot, 'web', 'e2e', 'seed_saved_report.py')}" && "${pythonExecutable}" -m uvicorn profile_agent.web.app:create_app --factory --host 127.0.0.1 --port ${apiPort}`,
      cwd: projectRoot,
      url: `${apiBaseUrl}/demo/assessment`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `npm run dev -- --config e2e/vite.e2e.config.ts --host 127.0.0.1 --port ${webPort}`,
      cwd: path.join(projectRoot, 'web'),
      env: {
        VITE_API_BASE_URL: '/api',
        E2E_API_ORIGIN: apiOrigin,
      },
      url: `http://127.0.0.1:${webPort}`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})
