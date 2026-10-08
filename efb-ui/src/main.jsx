import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './crew/crew.css'
import App from './App.jsx'
import { CrewPlatformProvider } from './crew/CrewPlatformContext.jsx'

// A prior production/preview visit can leave a Workbox service worker scoped
// to localhost:5175. Vite dev must never render a cached EFB shell or cached
// API state while testing a live SimConnect milestone.
if (import.meta.env.DEV && 'serviceWorker' in navigator) {
  void navigator.serviceWorker.getRegistrations()
    .then(async (registrations) => {
      await Promise.all(registrations.map((registration) => registration.unregister()))
      if ('caches' in window) {
        const cacheNames = await caches.keys()
        await Promise.all(cacheNames.map((cacheName) => caches.delete(cacheName)))
      }
    })
    .catch(() => {})
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <CrewPlatformProvider>
      <App />
    </CrewPlatformProvider>
  </StrictMode>,
)
