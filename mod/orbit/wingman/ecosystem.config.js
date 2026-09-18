const path = require('path')
const DIR = __dirname
const API_DIR = path.join(DIR, 'src', 'api')
const APP_DIR = path.join(DIR, 'src', 'app')
const API_PORT = process.env.WINGMAN_API_PORT || '50830'
const APP_PORT = process.env.WINGMAN_APP_PORT || '50831'

module.exports = {
  apps: [
    {
      name: 'wingman-api',
      script: path.join(API_DIR, 'target', 'release', 'wingman-api'),
      args: API_PORT,
      cwd: API_DIR,
      env: {
        WINGMAN_MOD_DIR: DIR,
        WINGMAN_BIND: process.env.WINGMAN_BIND || '0.0.0.0',
        WINGMAN_API_PORT: API_PORT,
      },
      interpreter: 'none',
      autorestart: true,
      watch: false,
      error_file: path.join(DIR, 'logs', 'api-err.log'),
      out_file: path.join(DIR, 'logs', 'api-out.log'),
    },
    {
      name: 'wingman-app',
      script: 'node_modules/.bin/next',
      args: `start -p ${APP_PORT} -H 0.0.0.0`,
      cwd: APP_DIR,
      env: {
        NODE_ENV: 'production',
        NEXT_PUBLIC_BASE_PATH: '/wingman',
        WINGMAN_APP_PORT: APP_PORT,
      },
      autorestart: true,
      watch: false,
      error_file: path.join(DIR, 'logs', 'app-err.log'),
      out_file: path.join(DIR, 'logs', 'app-out.log'),
    },
  ],
}
