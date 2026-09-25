import React, { useState } from 'react';
import { Info, ChevronDown, ChevronUp, KeyRound } from 'lucide-react';

const REPO = 'https://github.com/Ayush1860/SecureRag';

/**
 * "About this demo" panel for the hosted (Amplify + Lambda) deployment.
 * demoKeys comes from VITE_DEMO_KEYS_JSON at build time. The corpus is synthetic, so showing the
 * keys only lets visitors try each role (see SECURITY.md, "Public demo credentials").
 */
export default function AboutDemo({ demoKeys, onUseKey, hosted }) {
  const [open, setOpen] = useState(hosted);
  const roles = Object.entries(demoKeys || {});

  return (
    <div className="card-panel">
      <button type="button" className="card-panel-header about-toggle" onClick={() => setOpen(!open)}
              aria-expanded={open}>
        <div className="card-panel-title">
          <Info size={18} className="text-cyan" />
          <span>About this demo</span>
        </div>
        {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
      </button>
      {open && (
        <div className="panel-note about-body">
          <p>
            Every document here is <strong>synthetic</strong>: generated project memos with planted
            &ldquo;canary&rdquo; codes in the confidential ones. Pick a role and try to get another
            role&rsquo;s data out of it.
          </p>
          <p>
            The backend runs on AWS Lambda. The first request after a quiet period wakes it up
            (~20&nbsp;s); after that, answers take well under a second plus LLM time.
          </p>
          {roles.length > 0 && (
            <>
              <p><strong>Demo credentials</strong> (each key is locked to one role on the server):</p>
              <ul className="demo-keys">
                {roles.map(([role, key]) => (
                  <li key={role}>
                    <code>{role}</code>
                    <button type="button" className="btn-secondary" onClick={() => onUseKey(key)}>
                      <KeyRound size={12} /> <span>Use</span>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
          <p>
            <a href={REPO} target="_blank" rel="noopener noreferrer">Source</a>{' · '}
            <a href={`${REPO}/blob/main/reports/evaluation.md`} target="_blank" rel="noopener noreferrer">
              Benchmarks
            </a>{' · '}
            <a href={`${REPO}/blob/main/SECURITY.md`} target="_blank" rel="noopener noreferrer">Security model</a>
          </p>
        </div>
      )}
    </div>
  );
}
