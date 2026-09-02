import React, { useState } from 'react';
import { ShieldAlert, FileText, Database, ShieldCheck, AlertTriangle, Eye, Lock } from 'lucide-react';

export default function SecurityInspector({
  sources = [],
  contextExcerpts = [],
  flaggedCount = 0,
  blockedCount = 0,
  authorizedCount = 0,
}) {
  const [activeTab, setActiveTab] = useState('sources'); // 'sources' | 'excerpts'

  return (
    <div className="security-metadata-box">
      {/* Prompt Injection Threat Alert Banner */}
      {flaggedCount > 0 && (
        <div className="threat-quarantine-banner">
          <ShieldAlert size={22} className="threat-icon" />
          <div className="threat-content">
            <h4>Prompt Injection Quarantined & Neutralized</h4>
            <p>
              SecureRAG sanitizer detected <strong>{flaggedCount} malicious prompt injection</strong> sequence(s) in retrieved document payloads. The payload has been encapsulated within strict boundary tags to prevent jailbreaks, instruction overrides, and cross-boundary privilege escalation.
            </p>
          </div>
        </div>
      )}

      {/* Blocked Access Info */}
      {blockedCount > 0 && (
        <div style={{
          background: 'rgba(245, 158, 11, 0.1)',
          border: '1px solid rgba(245, 158, 11, 0.3)',
          borderRadius: '8px',
          padding: '10px 14px',
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          fontSize: '0.8rem',
          color: '#fef3c7'
        }}>
          <Lock size={16} color="#f59e0b" />
          <span>
            <strong>RBAC Enforcement:</strong> {blockedCount} document chunk(s) were blocked from retrieval due to clearance level or departmental boundary restrictions.
          </span>
        </div>
      )}

      {/* Tabs */}
      <div className="tabs-header">
        <button
          type="button"
          className={`tab-btn ${activeTab === 'sources' ? 'active' : ''}`}
          onClick={() => setActiveTab('sources')}
        >
          Retrieved Sources ({sources.length})
        </button>
        <button
          type="button"
          className={`tab-btn ${activeTab === 'excerpts' ? 'active' : ''}`}
          onClick={() => setActiveTab('excerpts')}
        >
          Context Payloads & Quarantine ({contextExcerpts.length})
        </button>
      </div>

      {/* Sources Tab */}
      {activeTab === 'sources' && (
        <div>
          {sources.length === 0 ? (
            <div style={{ color: '#64748b', fontSize: '0.82rem', fontStyle: 'italic', padding: '8px 0' }}>
              No sources retrieved yet or all candidate chunks were filtered by RBAC.
            </div>
          ) : (
            sources.map((sourceName, idx) => (
              <div key={idx} className="source-card">
                <div className="source-left">
                  <FileText size={16} color="#06b6d4" />
                  <span className="source-name">{sourceName || 'Unknown Document'}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <span className="clearance-pill pill-internal">
                    AUTHORIZED
                  </span>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* Excerpts Tab */}
      {activeTab === 'excerpts' && (
        <div>
          {contextExcerpts.length === 0 ? (
            <div style={{ color: '#64748b', fontSize: '0.82rem', fontStyle: 'italic', padding: '8px 0' }}>
              No authorized context payloads available to inspect.
            </div>
          ) : (
            contextExcerpts.map((excerpt, idx) => {
              const isFlagged = excerpt.flagged;
              return (
                <div key={idx} className={`excerpt-item ${isFlagged ? 'flagged' : ''}`}>
                  <div className="excerpt-header">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <Database size={13} color={isFlagged ? '#ef4444' : '#06b6d4'} />
                      <span style={{ fontWeight: 600 }}>{excerpt.source}</span>
                      <span style={{ color: '#64748b' }}>({excerpt.department} / {excerpt.clearance})</span>
                    </div>
                    {isFlagged ? (
                      <span className="clearance-pill pill-confidential" style={{ background: 'rgba(239, 68, 68, 0.2)', color: '#f87171', border: '1px solid #ef4444' }}>
                        INJECTION FLAGGED & QUARANTINED
                      </span>
                    ) : (
                      <span className="clearance-pill pill-public">CLEAN PAYLOAD</span>
                    )}
                  </div>
                  <div className="excerpt-body">
                    {excerpt.text}
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
