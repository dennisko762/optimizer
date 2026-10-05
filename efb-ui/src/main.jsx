import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './crew/crew.css'
import App from './App.jsx'
import { CrewPlatformProvider } from './crew/CrewPlatformContext.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <CrewPlatformProvider>
      <App />
    </CrewPlatformProvider>
  </StrictMode>,
)
