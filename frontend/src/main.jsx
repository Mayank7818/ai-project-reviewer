/**
 * React entry point.
 *
 * Its only job is to mount <App /> into #root. Keeping it this thin means
 * routing, providers and layout decisions all live in App.jsx where they can be
 * read in one place.
 */
import React from 'react'
import ReactDOM from 'react-dom/client'

import App from './App.jsx'
import './index.css'

const container = document.getElementById('root')

if (!container) {
  throw new Error('Root element #root was not found in index.html')
}

ReactDOM.createRoot(container).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
