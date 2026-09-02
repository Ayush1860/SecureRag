import React from 'react';
import { X, ShieldCheck, RefreshCw, FileCode } from 'lucide-react';

export default function AuditTrailModal({ isOpen, onClose, auditLogs = [], onRefresh, loading }) {
  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <ShieldCheck size={20} color="#06b6d4" />
            <div>
              <h3 style={{ fontSize: '1rem', fontWeight: 700 }}>Cryptographic Audit Trail Log</h3>
              <p style={{ fontSize: '0.74rem', color: '#94a3b8' }}>
                Immutable JSONL audit records with SHA-256 hashed queries and telemetry
              </p>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <button
              type="button"
              className="btn-secondary"
              onClick={onRefresh}
              disabled={loading}
              title="Refresh audit logs"
            >
              <RefreshCw size={13} className={loading ? 'spin' : ''} />
              <span>Refresh</span>
            </button>
            <button type="button" className="btn-icon" onClick={onClose} title="Close">
              <X size={18} />
            </button>
          </div>
        </div>

        <div className="modal-body">
          {auditLogs.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '2rem', color: '#64748b' }}>
              No audit logs recorded yet. Run queries to generate verifiable audit entries.
            </div>
          ) : (
            <table className="audit-table">
              <thead>
                <tr>
                  <th>Timestamp</th>
                  <th>Role</th>
                  <th>Query SHA-256 Hash</th>
                  <th>Auth / Blk / Flg</th>
                  <th>Latency</th>
                  <th>Provider</th>
                </tr>
              </thead>
              <tbody>
                {auditLogs.map((log, idx) => (
                  <tr key={idx}>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      {log.timestamp ? log.timestamp.split('T')[1]?.slice(0, 8) || log.timestamp : '—'}
                    </td>
                    <td>
                      <span className="clearance-pill pill-internal" style={{ fontSize: '0.65rem' }}>
                        {log.user_role}
                      </span>
                    </td>
                    <td title={log.query_hash} style={{ maxWidth: '140px', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {log.query_hash?.slice(0, 16)}...
                    </td>
                    <td>
                      <span style={{ color: '#10b981' }}>{log.authorized_count}</span> /{' '}
                      <span style={{ color: log.blocked_count > 0 ? '#f59e0b' : '#64748b' }}>{log.blocked_count}</span> /{' '}
                      <span style={{ color: log.flagged_count > 0 ? '#ef4444' : '#64748b' }}>{log.flagged_count}</span>
                    </td>
                    <td style={{ color: '#38bdf8' }}>
                      {typeof log.latency_ms === 'number' ? `${log.latency_ms.toFixed(1)}ms` : '—'}
                    </td>
                    <td style={{ color: '#94a3b8' }}>
                      {log.provider || 'mock'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
