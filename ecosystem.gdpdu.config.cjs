/**
 * PM2: GDPdU Reports dev stack (port 5176 + API 8010 + Rasa 5005/5055)
 *
 *   npm install          # root — installs pm2
 *   npm run stack:start
 *   npm run stack:stop
 *   npm run stack:logs
 */
const path = require('path')
const fs = require('fs')

const root = __dirname
const py = path.join(root, 'backend/.venv/bin/python')
const rasaPy = fs.existsSync(path.join(root, 'rasa/.venv/bin/python'))
  ? path.join(root, 'rasa/.venv/bin/python')
  : py
const viteEntry = path.join(root, 'frontend/node_modules/vite/bin/vite.js')

/** @type {import('pm2').StartOptions[]} */
const apps = [
  {
    name: 'gdpdu-api',
    script: py,
    args: ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8010', '--reload', '--env-file', '.env'],
    cwd: path.join(root, 'backend'),
    env: { PYTHONPATH: path.join(root, 'backend') },
    autorestart: true,
    max_restarts: 20,
    min_uptime: '5s',
  },
  {
    name: 'gdpdu-rasa-actions',
    script: rasaPy,
    args: ['-m', 'rasa', 'run', 'actions', '--port', '5055'],
    cwd: path.join(root, 'rasa'),
    env: {
      FASTAPI_BASE_URL: 'http://127.0.0.1:8010',
      UPLOAD_BASE_DIR: '/Users/mathi/finssentials-wt-mathis/uploads',
    },
    autorestart: true,
    max_restarts: 20,
    min_uptime: '5s',
  },
  {
    name: 'gdpdu-rasa',
    script: rasaPy,
    args: ['-m', 'rasa', 'run', '--enable-api', '--cors', '*', '--port', '5005'],
    cwd: path.join(root, 'rasa'),
    autorestart: true,
    max_restarts: 10,
    min_uptime: '20s',
  },
  {
    name: 'gdpdu-vite',
    script: 'node',
    args: [viteEntry, '--mode', 'fdd-merge', '--host', '127.0.0.1', '--port', '5176', '--strictPort'],
    cwd: path.join(root, 'frontend'),
    env: { NODE_ENV: 'development' },
    autorestart: true,
    max_restarts: 30,
    min_uptime: '5s',
  },
]

module.exports = { apps }
