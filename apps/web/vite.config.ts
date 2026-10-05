import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const rootEnv = fileURLToPath(new URL('../..', import.meta.url))

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, rootEnv, '')
  const host = env.API_HOST === '0.0.0.0' ? '127.0.0.1' : (env.API_HOST || '127.0.0.1')
  return {
    envDir: rootEnv,
    plugins: [react()],
    server: {
      host: env.WEB_HOST || '127.0.0.1',
      port: Number(env.WEB_PORT || 5173),
      strictPort: true,
      proxy: { '/api': { target: `http://${host}:${env.API_PORT || 8000}`, changeOrigin: true } },
    },
    preview: { host: env.WEB_HOST || '127.0.0.1', port: Number(env.WEB_PORT || 5173) },
  }
})
