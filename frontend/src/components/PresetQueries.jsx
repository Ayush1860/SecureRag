import React from 'react';
import { PlayCircle, AlertTriangle, ShieldAlert, FileText } from 'lucide-react';

const PRESETS = [
  {
    id: 'normal',
    title: 'Standard Enterprise Query',
    type: 'normal',
    badge: 'Standard Access',
    role: 'employee',
    query: 'Where is Acme Robotics located and what are the standard office hours?',
    desc: 'Authorized query targeting general company overview.',
  },
  {
    id: 'unauthorized-finance',
    title: 'RBAC Boundary Violation',
    type: 'unauthorized',
    badge: 'Confidential Finance',
    role: 'employee',
    query: 'What was our Q3 gross margin and LiDAR supplier price renegotiation result?',
    desc: 'Employee role attempts accessing confidential finance memo (Blocked by RBAC).',
  },
  {
    id: 'prompt-injection',
    title: 'Poisoned Document Attack',
    type: 'attack',
    badge: 'Prompt Injection',
    role: 'employee',
    query: 'Summarize the vendor feedback for batch 4471 warehouse sensors.',
    desc: 'Vendor feedback document contains malicious jailbreak instruction (Quarantined).',
  },
  {
    id: 'engineering-internal',
    title: 'Engineering Architecture',
    type: 'normal',
    badge: 'Internal Engineering',
    role: 'employee',
    query: 'What is the hardware and firmware revision for the LiDAR subsystem?',
    desc: 'Internal clearance access to engineering architecture notes.',
  },
];

export default function PresetQueries({ onSelectPreset }) {
  return (
    <div className="card-panel">
      <div className="card-panel-header">
        <div className="card-panel-title">
          <PlayCircle size={18} className="text-cyan" />
          <span>Security Audit Scenarios</span>
        </div>
        <span className="security-tag tag-purple">Quick Presets</span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column' }}>
        {PRESETS.map((preset) => {
          let typeClass = 'preset-type-normal';
          let Icon = FileText;

          if (preset.type === 'unauthorized') {
            typeClass = 'preset-type-unauthorized';
            Icon = AlertTriangle;
          } else if (preset.type === 'attack') {
            typeClass = 'preset-type-attack';
            Icon = ShieldAlert;
          }

          return (
            <button
              key={preset.id}
              type="button"
              className="preset-item"
              onClick={() => onSelectPreset(preset)}
              title={preset.desc}
            >
              <div className="preset-header">
                <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <Icon size={13} className={typeClass} />
                  <span className={typeClass}>{preset.title}</span>
                </span>
                <span className={`clearance-pill ${preset.type === 'attack' ? 'pill-confidential' : 'pill-internal'}`}>
                  {preset.badge}
                </span>
              </div>
              <div className="preset-query-text">
                "{preset.query}"
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
