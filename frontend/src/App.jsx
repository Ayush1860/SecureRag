import React, { useState, useEffect } from 'react';
import {
  Shield,
  Send,
  Loader2,
  Sliders,
  History,
  AlertCircle,
  Database,
  Lock,
  Cpu,
} from 'lucide-react';
import RoleSelector from './components/RoleSelector';
import PresetQueries from './components/PresetQueries';
import TelemetryHUD from './components/TelemetryHUD';
import AnswerPanel from './components/AnswerPanel';
import SecurityInspector from './components/SecurityInspector';
import AuditTrailModal from './components/AuditTrailModal';
import CredentialPanel from './components/CredentialPanel';

const CREDENTIAL_KEY = 'securerag.credential';

function readCredential() {
  try {
    return sessionStorage.getItem(CREDENTIAL_KEY) || '';
  } catch {
    return '';
  }
}

export default function App() {
  const [currentRole, setCurrentRole] = useState('employee');
  // Authentication: in AUTH_MODE=dev the role picker sets X-Dev-Role; otherwise the role comes
  // from the API key / JWT and the picker is locked to it.
  const [authMode, setAuthMode] = useState('dev');
  const [credential, setCredential] = useState(readCredential);
  const [principal, setPrincipal] = useState(null);
  const [authError, setAuthError] = useState(null);
  const [rolePolicies, setRolePolicies] = useState({
    guest: { max_clearance: 'public', departments: ['general'] },
    employee: { max_clearance: 'internal', departments: ['general', 'engineering', 'hr'] },
    finance_lead: { max_clearance: 'confidential', departments: ['general', 'finance'] },
    exec: { max_clearance: 'confidential', departments: ['general', 'engineering', 'hr', 'finance', 'exec'] },
  });

  const [query, setQuery] = useState('');
  const [topK, setTopK] = useState(5);
  const [loading, setLoading] = useState(false);
  const [backendOnline, setBackendOnline] = useState(false);
  const [chunkCount, setChunkCount] = useState(0);
  const [errorMessage, setErrorMessage] = useState(null);

  // Active Query Results
  const [resultData, setResultData] = useState(null);

  // Audit trail state
  const [auditModalOpen, setAuditModalOpen] = useState(false);
  const [auditLogs, setAuditLogs] = useState([]);
  const [auditLoading, setAuditLoading] = useState(false);

  const authHeaders = (cred = credential, role = currentRole) => {
    if (authMode === 'dev') return { 'X-Dev-Role': role };
    if (!cred) return {};
    return authMode === 'jwt' ? { Authorization: `Bearer ${cred}` } : { 'X-API-Key': cred };
  };

  // Fetch backend status, auth mode & roles on mount
  useEffect(() => {
    checkHealth();
    fetchRoles();
    fetch('/api/auth/mode')
      .then((r) => (r.ok ? r.json() : { mode: 'api_key' }))
      .then((data) => setAuthMode(data.mode))
      .catch(() => setAuthMode('api_key'));
  }, []);

  useEffect(() => {
    if (authMode !== 'dev' && credential) signIn(credential);
    if (authMode === 'dev') fetchAuditTrail();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authMode]);

  const signIn = async (cred) => {
    setAuthError(null);
    try {
      const res = await fetch('/api/auth/me', { headers: authHeaders(cred) });
      if (!res.ok) throw new Error('Credential rejected');
      const me = await res.json();
      try {
        sessionStorage.setItem(CREDENTIAL_KEY, cred);
      } catch {
        /* storage unavailable: keep it in memory only */
      }
      setCredential(cred);
      setPrincipal(me);
      setCurrentRole(me.role);
    } catch (err) {
      setPrincipal(null);
      setAuthError(err.message);
    }
  };

  const signOut = () => {
    try {
      sessionStorage.removeItem(CREDENTIAL_KEY);
    } catch {
      /* ignore */
    }
    setCredential('');
    setPrincipal(null);
    setResultData(null);
    setAuditLogs([]);
  };

  const checkHealth = async () => {
    try {
      const res = await fetch('/api/health');
      if (res.ok) {
        const data = await res.json();
        setBackendOnline(true);
        setChunkCount(data.chunks || 0);
      } else {
        setBackendOnline(false);
      }
    } catch {
      setBackendOnline(false);
    }
  };

  const fetchRoles = async () => {
    try {
      const res = await fetch('/api/roles');
      if (res.ok) {
        const data = await res.json();
        setRolePolicies(data);
      }
    } catch (err) {
      console.error('Failed to fetch roles:', err);
    }
  };

  const fetchAuditTrail = async () => {
    setAuditLoading(true);
    try {
      const res = await fetch('/api/audit?limit=25', { headers: authHeaders() });
      if (res.ok) {
        const logs = await res.json();
        setAuditLogs(logs);
      } else {
        setAuditLogs([]);
      }
    } catch (err) {
      console.error('Failed to fetch audit trail:', err);
    } finally {
      setAuditLoading(false);
    }
  };

  const handleSubmit = async (e) => {
    if (e) e.preventDefault();
    const cleanQuery = query.trim();
    if (!cleanQuery || loading) return;

    setLoading(true);
    setErrorMessage(null);

    try {
      const res = await fetch('/api/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        // No role in the body: the server derives it from the authenticated principal.
        body: JSON.stringify({
          query: cleanQuery,
          top_k: parseInt(topK, 10),
        }),
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({ detail: `Error: HTTP ${res.status}` }));
        throw new Error(errJson.detail || `Request failed with HTTP ${res.status}`);
      }

      const data = await res.json();
      setResultData(data);
      // Refresh audit trail in background
      fetchAuditTrail();
    } catch (err) {
      setErrorMessage(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleSelectPreset = (preset) => {
    if (preset.role && authMode === 'dev') setCurrentRole(preset.role);
    setQuery(preset.query);
  };

  const handleKeyDown = (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="app-wrapper">
      {/* Header */}
      <header className="app-header">
        <div className="brand-section">
          <div className="brand-shield">
            <Shield size={22} />
          </div>
          <div>
            <div className="brand-title">SecureRAG</div>
            <span className="brand-badge">Zero-Trust Enterprise RAG</span>
          </div>
        </div>

        <div className="header-status-group">
          <div className="health-beacon">
            <span className={`beacon-dot ${backendOnline ? '' : 'offline'}`} />
            <span>
              {backendOnline
                ? `System Online (${chunkCount} chunks)`
                : 'System Offline'}
            </span>
          </div>

          <div style={{ display: 'flex', gap: '8px' }}>
            <span className="security-tag tag-cyan">
              <Lock size={12} /> AES-256-GCM
            </span>
            <span className="security-tag tag-purple">
              <Cpu size={12} /> LangGraph
            </span>
            <span className="security-tag tag-emerald">
              <Shield size={12} /> Dual RBAC
            </span>
          </div>

          <button
            type="button"
            className="btn-secondary"
            onClick={() => setAuditModalOpen(true)}
            title="View cryptographic audit log"
          >
            <History size={14} />
            <span>Audit Trail</span>
          </button>
        </div>
      </header>

      {/* Main Grid */}
      <main className="main-content">
        {/* Left Column: Role Selector & Scenario Presets */}
        <aside className="sidebar-col">
          {authMode !== 'dev' && (
            <CredentialPanel
              authMode={authMode}
              principal={principal}
              onSubmit={signIn}
              onSignOut={signOut}
              error={authError}
            />
          )}
          <RoleSelector
            currentRole={currentRole}
            onSelectRole={setCurrentRole}
            rolePolicies={rolePolicies}
            locked={authMode !== 'dev'}
          />

          <PresetQueries onSelectPreset={handleSelectPreset} />
        </aside>

        {/* Right Column: Query Box, Telemetry, Answer, and Security Inspector */}
        <section className="workspace-col">
          {/* Query Box Card */}
          <div className="card-panel">
            <form className="query-box" onSubmit={handleSubmit}>
              <div className="query-input-wrapper">
                <textarea
                  id="query-input"
                  className="query-textarea"
                  placeholder="Enter an enterprise query (e.g., policy, financial reports, engineering specs)..."
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={handleKeyDown}
                  disabled={loading}
                />
              </div>

              <div className="query-controls-bar">
                <div className="slider-group">
                  <Sliders size={14} color="#06b6d4" />
                  <span>Top-K Retrieval: <strong>{topK}</strong></span>
                  <input
                    id="topk-slider"
                    type="range"
                    min="1"
                    max="10"
                    value={topK}
                    onChange={(e) => setTopK(Number(e.target.value))}
                    disabled={loading}
                  />
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <button
                    id="submit-btn"
                    type="submit"
                    className="btn-primary"
                    disabled={loading || !query.trim()}
                  >
                    {loading ? (
                      <>
                        <Loader2 size={16} className="spin" />
                        <span>Evaluating Pipeline...</span>
                      </>
                    ) : (
                      <>
                        <Send size={15} />
                        <span>Execute Secure Query</span>
                      </>
                    )}
                  </button>
                </div>
              </div>
            </form>

            {errorMessage && (
              <div
                style={{
                  marginTop: '12px',
                  padding: '10px 14px',
                  background: 'rgba(239, 68, 68, 0.12)',
                  border: '1px solid rgba(239, 68, 68, 0.35)',
                  borderRadius: '8px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  fontSize: '0.82rem',
                  color: '#fca5a5',
                }}
              >
                <AlertCircle size={16} color="#ef4444" />
                <span>{errorMessage}</span>
              </div>
            )}
          </div>

          {/* Security Telemetry HUD */}
          {resultData && (
            <TelemetryHUD
              telemetry={{
                requestId: resultData.request_id,
                latencyMs: resultData.latency_ms || 0,
                authorized: resultData.authorized || 0,
                blocked: resultData.blocked || 0,
                flagged: resultData.flagged_chunks || 0,
                retrieved: resultData.retrieved || 0,
              }}
            />
          )}

          {/* Clear Distinction: Trusted Answer Panel */}
          <AnswerPanel
            answer={resultData?.answer}
            sources={resultData?.sources}
            role={resultData?.role || currentRole}
            authorizedCount={resultData?.authorized || 0}
          />

          {/* Clear Distinction: Security Metadata & Inspector */}
          {resultData && (
            <SecurityInspector
              sources={resultData.sources}
              contextExcerpts={resultData.context_excerpts}
              flaggedCount={resultData.flagged_chunks}
              blockedCount={resultData.blocked}
              authorizedCount={resultData.authorized}
            />
          )}
        </section>
      </main>

      {/* Audit Modal */}
      <AuditTrailModal
        isOpen={auditModalOpen}
        onClose={() => setAuditModalOpen(false)}
        auditLogs={auditLogs}
        onRefresh={fetchAuditTrail}
        loading={auditLoading}
      />
    </div>
  );
}
