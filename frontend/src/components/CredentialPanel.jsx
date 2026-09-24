import React, { useState } from 'react';
import { KeyRound, LogOut } from 'lucide-react';

/**
 * Credential entry for AUTH_MODE=api_key / jwt. The credential is kept in sessionStorage only
 * (cleared when the tab closes) and sent as a header; the role always comes from the server.
 */
export default function CredentialPanel({ authMode, principal, onSubmit, onSignOut, error }) {
  const [value, setValue] = useState('');
  const label = authMode === 'jwt' ? 'Bearer token (JWT)' : 'API key';

  if (principal) {
    return (
      <div className="card-panel">
        <div className="card-panel-header">
          <div className="card-panel-title">
            <KeyRound size={18} className="text-cyan" />
            <span>Signed in</span>
          </div>
          <button type="button" className="btn-secondary" onClick={onSignOut}>
            <LogOut size={14} /> <span>Sign out</span>
          </button>
        </div>
        <div className="panel-note">
          <strong>{principal.principal}</strong> · role <code>{principal.role}</code> · via {principal.method}
        </div>
      </div>
    );
  }

  return (
    <div className="card-panel">
      <div className="card-panel-header">
        <div className="card-panel-title">
          <KeyRound size={18} className="text-cyan" />
          <span>Authenticate</span>
        </div>
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (value.trim()) onSubmit(value.trim());
        }}
      >
        <label className="panel-note" htmlFor="credential-input">{label}</label>
        <input
          id="credential-input"
          type="password"
          autoComplete="off"
          className="query-input"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={authMode === 'jwt' ? 'eyJhbGciOi…' : 'srag_…'}
        />
        <button type="submit" className="btn-primary" style={{ marginTop: '8px' }}>Sign in</button>
        {error && <div className="error-banner" role="alert">{error}</div>}
      </form>
    </div>
  );
}
