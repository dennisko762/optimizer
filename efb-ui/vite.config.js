import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      injectRegister: 'auto',
      devOptions: {
        enabled: false, // Service worker only in production/preview
      },
      strategies: 'generateSW',
      srcDir: 'src',
      filename: 'sw.js',
      manifest: {
        name: 'Crew Operations Platform',
        short_name: 'CrewOps',
        start_url: '/',
        display: 'standalone',
        background_color: '#0a0e14',
        theme_color: '#003366',
        icons: [
          {
            src: '/favicon.svg',
            sizes: 'any',
            type: 'image/svg+xml'
          }
        ]
      },
      workbox: {
        // /api and /health must NEVER be served from cache (live-flight EFB safety).
        // NetworkOnly ensures these always go to the network; offline → they fail cleanly.
        runtimeCaching: [
          {
            urlPattern: /\/api\//,
            handler: 'NetworkOnly',
          },
          {
            urlPattern: /\/health/,
            handler: 'NetworkOnly',
          },
        ],
        // Built assets (JS/CSS/HTML) are precached automatically by generateSW.
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api/, /^\/health/],
      },
    })
  ],
  test: {
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      reportsDirectory: 'coverage',
      include: ['src/**/*.{js,jsx}'],
      exclude: ['src/**/*.test.js'],
    },
  },
  server: {
    // In dev mode, proxy API calls to the running FastAPI backend.
    // In production the frontend is served by FastAPI itself (same origin).
    // Override the target with EFB_API_TARGET when the backend runs on
    // another port (e.g. a task worktree served on 8001).
    proxy: {
      '/api': process.env.EFB_API_TARGET || 'http://localhost:8000',
      '/health': process.env.EFB_API_TARGET || 'http://localhost:8000',
    },
  },
})
