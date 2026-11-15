import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  // .lottie（dotLottie zip 包）需要作为静态资产按 URL 引入
  assetsInclude: ['**/*.lottie'],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  },
  css: {
    postcss: {}
  },
  server: {
    port: 3001,
    host: true,
    proxy: {
      '/api/sse': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        timeout: 300000,
        proxyTimeout: 300000,
        ws: false,
        selfHandleResponse: true,
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes, _req, res) => {
            if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
              delete proxyRes.headers['content-encoding']
              proxyRes.headers['cache-control'] = 'no-cache'
              proxyRes.headers['connection'] = 'keep-alive'
              proxyRes.headers['x-accel-buffering'] = 'no'
              res.writeHead(proxyRes.statusCode!, proxyRes.headers)
              proxyRes.on('data', (chunk: Buffer) => {
                res.write(chunk)
              })
              proxyRes.on('end', () => {
                res.end()
              })
              proxyRes.on('error', (err: Error) => {
                console.error('[SSE proxy] proxyRes error:', err.message)
                res.end()
              })
            } else {
              proxyRes.pipe(res)
            }
          })
        },
      },
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        timeout: 300000,
        proxyTimeout: 300000,
        ws: false,
      },
      '/uploads': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/outputs': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/blcaptain': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
})