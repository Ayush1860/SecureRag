import React from 'react';
import { Shield, Lock, Users, Key, Briefcase } from 'lucide-react';

const ROLE_ICONS = {
  guest: Users,
  employee: Briefcase,
  finance_lead: Lock,
  exec: Key,
  admin: Shield,
};

const ROLE_DISPLAY_NAMES = {
  guest: 'Guest Identity',
  employee: 'Internal Employee',
  finance_lead: 'Finance Lead',
  exec: 'Executive Officer',
  admin: 'Administrator (no document access)',
};

const CLEARANCE_LEVELS = {
  public: { label: 'PUBLIC', class: 'pill-public' },
  internal: { label: 'INTERNAL', class: 'pill-internal' },
  confidential: { label: 'CONFIDENTIAL', class: 'pill-confidential' },
};

export default function RoleSelector({ currentRole, onSelectRole, rolePolicies, locked = false }) {
  const currentPolicy = rolePolicies[currentRole] || {
    max_clearance: 'internal',
    departments: ['general', 'engineering', 'hr'],
  };

  const clearanceMeta = CLEARANCE_LEVELS[currentPolicy.max_clearance] || {
    label: currentPolicy.max_clearance.toUpperCase(),
    class: 'pill-internal',
  };

  return (
    <div className="card-panel">
      <div className="card-panel-header">
        <div className="card-panel-title">
          <Shield size={18} className="text-cyan" />
          <span>Identity & Clearance (RBAC)</span>
        </div>
        <span className="security-tag tag-cyan">{locked ? 'From credential' : 'Dev role switcher'}</span>
      </div>

      <div className="role-grid">
        {Object.entries(rolePolicies).map(([roleKey, policy]) => {
          const Icon = ROLE_ICONS[roleKey] || Users;
          const isActive = currentRole === roleKey;
          const pill = CLEARANCE_LEVELS[policy.max_clearance] || {
            label: policy.max_clearance.toUpperCase(),
            class: 'pill-internal',
          };

          return (
            <button
              key={roleKey}
              type="button"
              className={`role-card-btn ${isActive ? 'active' : ''}`}
              disabled={locked && !isActive}
              title={locked ? 'Your role comes from your credential' : undefined}
              onClick={() => !locked && onSelectRole(roleKey)}
              aria-label={`Select role ${roleKey}`}
            >
              <div className="role-card-top">
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <Icon size={14} />
                  <span className="role-name-text">
                    {roleKey.replace('_', ' ').toUpperCase()}
                  </span>
                </div>
                <span className={`clearance-pill ${pill.class}`}>
                  {pill.label}
                </span>
              </div>
              <div className="role-depts-preview" title={policy.departments.join(', ')}>
                Depts: {policy.departments.join(', ')}
              </div>
            </button>
          );
        })}
      </div>

      <div className="policy-banner">
        <div className="policy-banner-row">
          <span className="policy-label">Active Clearance:</span>
          <span className="policy-value">{clearanceMeta.label}</span>
        </div>
        <div className="policy-banner-row">
          <span className="policy-label">Authorized Depts:</span>
          <span className="policy-value" style={{ fontSize: '0.68rem' }}>
            {currentPolicy.departments.join(', ')}
          </span>
        </div>
        <div className="policy-banner-row">
          <span className="policy-label">Security Boundary:</span>
          <span className="policy-value" style={{ color: '#38bdf8', fontSize: '0.68rem' }}>
            Dual-Stage Pre & Post Enforcement
          </span>
        </div>
      </div>
    </div>
  );
}
