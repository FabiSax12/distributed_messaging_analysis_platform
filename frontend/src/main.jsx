import React from 'react'
import ReactDOM from 'react-dom/client'
import Root from './App.jsx'
import { AuthProvider } from './auth/AuthProvider.jsx'
import './styles.css'

// AuthProvider resolves an OAuth callback (?code=...&state=...) before any
// view or hash route runs: Cognito callback URLs are the bare origin, not
// #/ routes.
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AuthProvider>
      <Root />
    </AuthProvider>
  </React.StrictMode>,
)
