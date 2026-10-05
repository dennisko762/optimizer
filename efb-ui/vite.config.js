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
  server: {
    // In dev mode, proxy API calls to the running FastAPI backend.
    // In production the frontend is served by FastAPI itself (same origin).
    proxy: {
      '/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
