import React, { useState } from 'react';
import { ShieldCheck, Copy, Check, Lock, Sparkles, FileText, AlertOctagon } from 'lucide-react';

export default function AnswerPanel({ answer, sources = [], role, authorizedCount = 0 }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    if (!answer) return;
    navigator.clipboard.writeText(answer);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const isAccessDenied = authorizedCount === 0 && !answer;

  return (
    <div className="trusted-answer-panel">
      {/* Header bar distinguishing Trusted Output */}
      <div className="trusted-answer-header">
        <div className="trusted-badge-group">
          <div className="verified-badge">
            <ShieldCheck size={14} />
            <span>TRUSTED MODEL OUTPUT</span>
          </div>
          <span className="security-tag tag-cyan">
            Role: {role?.replace('_', ' ').toUpperCase()}
          </span>
        </div>

        <button
          type="button"
          className="btn-secondary"
          onClick={handleCopy}
          title="Copy verified answer"
        >
          {copied ? (
            <>
              <Check size={13} color="#10b981" />
              <span style={{ color: '#10b981' }}>Copied</span>
            </>
          ) : (
            <>
              <Copy size={13} />
              <span>Copy Response</span>
            </>
          )}
        </button>
      </div>

      {/* Answer Content */}
      <div className="answer-body">
        {answer ? (
          <div>{answer}</div>
        ) : (
          <div style={{ color: '#64748b', fontStyle: 'italic' }}>
            No query executed yet. Select a role and enter a question above to execute the SecureRAG pipeline.
          </div>
        )}
      </div>

      {/* Grounded Sources strip (under the answer) */}
      {sources && sources.length > 0 && (
        <div className="grounded-sources-strip">
          <span style={{ fontSize: '0.74rem', color: '#94a3b8', display: 'flex', alignItems: 'center', gap: '5px' }}>
            <FileText size={13} />
            Grounded Citations:
          </span>
          {Array.from(new Set(sources.filter(Boolean))).map((src, idx) => (
            <span key={idx} className="source-chip">
              {src}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
