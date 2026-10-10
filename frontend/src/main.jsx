import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import AccessGate from './AccessGate.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AccessGate />
  </StrictMode>,
)
