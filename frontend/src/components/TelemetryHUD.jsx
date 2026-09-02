import React, { useState } from 'react';
import { Copy, Check, Clock, ShieldCheck, ShieldAlert, FileCheck, Ban, Hash } from 'lucide-react';

export default function TelemetryHUD({ telemetry }) {
  const [copied, setCopied] = useState(false);

  if (!telemetry) return null;

  const {
    requestId = '—',
    latencyMs = 0,
    authorized = 0,
    blocked = 0,
    flagged = 0,
    retrieved = 0,
  } = telemetry;

  const handleCopyRequestId = () => {
    if (!requestId || requestId === '—') return;
    navigator.clipboard.writeText(requestId);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  let securityStatus = 'CLEAN';
  let statusColor = 'green';
  if (flagged > 0) {
    securityStatus = 'THREAT QUARANTINED';
    statusColor = 'red';
  } else if (blocked > 0 && authorized === 0) {
    securityStatus = 'ACCESS BLOCKED';
    statusColor = 'amber';
  } else if (blocked > 0) {
    securityStatus = 'PARTIAL FILTERED';
    statusColor = 'cyan';
  }

  return (
    <div className="telemetry-hud">
      {/* Request ID */}
      <div className="telemetry-stat-card" style={{ gridColumn: 'span 2' }}>
        <div className="stat-label">
          <Hash size={13} />
          <span>Request Trace ID</span>
          <button
            type="button"
            className="btn-icon"
            onClick={handleCopyRequestId}
            title="Copy Request ID"
            style={{ marginLeft: 'auto' }}
          >
            {copied ? <Check size={13} color="#10b981" /> : <Copy size={13} />}
          </button>
        </div>
        <div
          className="stat-value"
          style={{
            fontSize: '0.82rem',
            letterSpacing: '0.04em',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
          title={requestId}
        >
          {requestId}
        </div>
        <div className="stat-sub">Cryptographically audited</div>
      </div>

      {/* Latency */}
      <div className="telemetry-stat-card">
        <div className="stat-label">
          <Clock size={13} />
          <span>Latency</span>
        </div>
        <div className="stat-value cyan">
          {latencyMs.toFixed(1)} <span style={{ fontSize: '0.72rem', fontWeight: 500 }}>ms</span>
        </div>
        <div className="stat-sub">End-to-end pipeline</div>
      </div>

      {/* Authorized Chunks */}
      <div className="telemetry-stat-card">
        <div className="stat-label">
          <FileCheck size={13} />
          <span>Authorized</span>
        </div>
        <div className="stat-value green">
          {authorized} <span style={{ fontSize: '0.72rem', color: '#64748b' }}>/ {retrieved}</span>
        </div>
        <div className="stat-sub">Clearance verified</div>
      </div>

      {/* Blocked Chunks */}
      <div className="telemetry-stat-card">
        <div className="stat-label">
          <Ban size={13} />
          <span>Blocked</span>
        </div>
        <div className={`stat-value ${blocked > 0 ? 'amber' : ''}`}>
          {blocked}
        </div>
        <div className="stat-sub">Cross-boundary denied</div>
      </div>

      {/* Flagged / Quarantined */}
      <div className="telemetry-stat-card">
        <div className="stat-label">
          <ShieldAlert size={13} />
          <span>Flagged</span>
        </div>
        <div className={`stat-value ${flagged > 0 ? 'red' : ''}`}>
          {flagged}
        </div>
        <div className="stat-sub">Prompt injection</div>
      </div>

      {/* Security Status Badge */}
      <div className="telemetry-stat-card">
        <div className="stat-label">
          <ShieldCheck size={13} />
          <span>Security State</span>
        </div>
        <div className={`stat-value ${statusColor}`} style={{ fontSize: '0.84rem' }}>
          {securityStatus}
        </div>
        <div className="stat-sub">AES-256 decrypted</div>
      </div>
    </div>
  );
}
