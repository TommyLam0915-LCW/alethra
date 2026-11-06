'use client';

import { useState } from 'react';
import type { CSSProperties } from 'react';
import type { JournalFilters, FilterOptions } from './JournalDashboard';

interface Props {
  filters: JournalFilters;
  onChange: (filters: JournalFilters) => void;
  onApply: () => void;
  onReset: () => void;
  options: FilterOptions | null;
}

const inputStyle: CSSProperties = {
  width: '100%',
  padding: '10px 12px',
  border: '1px solid var(--border-subtle)',
  borderRadius: 4,
  backgroundColor: 'var(--surface-2)',
  color: 'var(--text-primary)',
  fontSize: 14,
  minHeight: 44,
  boxSizing: 'border-box',
};

const labelStyle: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 4,
  fontSize: 12,
  color: 'var(--text-secondary)',
};

// ---------------------------------------------------------------------------
// Filter chip helper
// ---------------------------------------------------------------------------

function FilterChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '3px 10px',
        backgroundColor: 'rgba(59, 130, 246, 0.14)',
        color: 'var(--primary)',
        borderRadius: 12,
        fontSize: 12,
        fontWeight: 500,
        whiteSpace: 'nowrap',
      }}
    >
      {label}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${label} filter`}
        style={{
          background: 'none',
          border: 'none',
          color: 'inherit',
          cursor: 'pointer',
          padding: 0,
          display: 'flex',
          alignItems: 'center',
          opacity: 0.75,
          lineHeight: 1,
        }}
      >
        <svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2.2">
          <path d="M3 3l6 6M9 3l-6 6" />
        </svg>
      </button>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Build human-readable chip labels for active filters
// ---------------------------------------------------------------------------

function buildChips(
  filters: JournalFilters,
  options: FilterOptions | null,
): { key: keyof JournalFilters; label: string }[] {
  const chips: { key: keyof JournalFilters; label: string }[] = [];

  if (filters.instrument_id) {
    chips.push({ key: 'instrument_id', label: filters.instrument_id });
  }
  if (filters.rule_family) {
    chips.push({ key: 'rule_family', label: filters.rule_family });
  }
  if (filters.rule_id) {
    const found = options?.rule_ids.find((r) => r.rule_id === filters.rule_id);
    chips.push({ key: 'rule_id', label: found?.rule_label_zh ?? found?.rule_id ?? filters.rule_id });
  }
  if (filters.interval_minutes !== undefined) {
    chips.push({ key: 'interval_minutes', label: `${filters.interval_minutes}m` });
  }
  if (filters.exit_reason) {
    chips.push({ key: 'exit_reason', label: filters.exit_reason });
  }
  if (filters.date_from) {
    chips.push({ key: 'date_from', label: `From: ${filters.date_from}` });
  }
  if (filters.date_to) {
    chips.push({ key: 'date_to', label: `To: ${filters.date_to}` });
  }
  if (filters.pnl_min !== undefined && filters.pnl_min !== '') {
    chips.push({ key: 'pnl_min', label: `Min P&L: $${filters.pnl_min}` });
  }
  if (filters.pnl_max !== undefined && filters.pnl_max !== '') {
    chips.push({ key: 'pnl_max', label: `Max P&L: $${filters.pnl_max}` });
  }

  return chips;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function JournalFilterBar({ filters, onChange, onApply, onReset, options }: Props) {
  const [expanded, setExpanded] = useState(false);

  const update = (key: keyof JournalFilters, value: string | number | undefined) => {
    onChange({ ...filters, [key]: value ?? undefined });
  };

  const removeChip = (key: keyof JournalFilters) => {
    onChange({ ...filters, [key]: undefined });
  };

  const chips = buildChips(filters, options);
  const hasActiveFilters = chips.length > 0;

  return (
    <div style={{ marginBottom: 16 }}>
      {/* Collapsed bar: active chips + toggle button */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '10px 14px',
          backgroundColor: 'var(--surface-1)',
          borderRadius: 6,
          border: '1px solid var(--border-subtle)',
        }}
      >
        {/* Active filter chips */}
        <div
          style={{
            flex: 1,
            display: 'flex',
            gap: 8,
            flexWrap: 'wrap',
            alignItems: 'center',
            minWidth: 0,
          }}
        >
          <span style={{ fontSize: 13, color: 'var(--text-secondary)', fontWeight: 500, flexShrink: 0 }}>
            Filters:
          </span>

          {hasActiveFilters
            ? chips.map((c) => (
                <FilterChip key={`${c.key}-${c.label}`} label={c.label} onRemove={() => removeChip(c.key)} />
              ))
            : (
                <span style={{ fontSize: 13, color: 'var(--text-secondary)', fontStyle: 'italic', opacity: 0.7 }}>
                  No filters active
                </span>
              )}
        </div>

        {/* Expand / Collapse toggle */}
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-label={expanded ? 'Collapse filters' : 'Expand filters'}
          style={{
            flexShrink: 0,
            padding: '5px 12px',
            backgroundColor: 'transparent',
            border: '1px solid var(--border-strong)',
            borderRadius: 4,
            fontSize: 13,
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            transition: 'color 0.15s, border-color 0.15s',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = 'var(--text-primary)';
            e.currentTarget.style.borderColor = 'var(--primary)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = 'var(--text-secondary)';
            e.currentTarget.style.borderColor = 'var(--border-strong)';
          }}
        >
          {expanded ? 'Hide Filters' : 'Show Filters'}
          <svg
            width="12"
            height="12"
            viewBox="0 0 12 12"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            style={{
              transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)',
              transition: 'transform 0.2s ease',
            }}
          >
            <path d="M3 5l3 3 3-3" />
          </svg>
        </button>
      </div>

      {/* Expanded panel: full filter inputs */}
      {expanded && (
        <div
          style={{
            marginTop: 8,
            padding: 16,
            backgroundColor: 'var(--surface-1)',
            borderRadius: 6,
            border: '1px solid var(--border-subtle)',
          }}
        >
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
              gap: 16,
              marginBottom: 14,
            }}
          >
            {/* Instrument */}
            <label style={labelStyle}>
              Instrument
              <select
                value={filters.instrument_id ?? ''}
                onChange={(e) => update('instrument_id', e.target.value || undefined)}
                style={inputStyle}
              >
                <option value="">All</option>
                {options?.instruments.map((inst) => (
                  <option key={inst} value={inst}>{inst}</option>
                ))}
              </select>
            </label>

            {/* Rule Family */}
            <label style={labelStyle}>
              Rule Family
              <select
                value={filters.rule_family ?? ''}
                onChange={(e) => update('rule_family', e.target.value || undefined)}
                style={inputStyle}
              >
                <option value="">All</option>
                {options?.rule_families.map((fam) => (
                  <option key={fam} value={fam}>{fam}</option>
                ))}
              </select>
            </label>

            {/* Rule ID */}
            <label style={labelStyle}>
              Skill (Rule)
              <select
                value={filters.rule_id ?? ''}
                onChange={(e) => update('rule_id', e.target.value || undefined)}
                style={inputStyle}
              >
                <option value="">All</option>
                {options?.rule_ids.map((r) => (
                  <option key={r.rule_id} value={r.rule_id}>
                    {r.rule_label_zh ?? r.rule_id}
                  </option>
                ))}
              </select>
            </label>

            {/* Interval */}
            <label style={labelStyle}>
              Interval
              <select
                value={filters.interval_minutes ?? ''}
                onChange={(e) => update('interval_minutes', e.target.value ? Number(e.target.value) : undefined)}
                style={inputStyle}
              >
                <option value="">All</option>
                {options?.intervals.map((iv) => (
                  <option key={iv} value={iv}>{iv}m</option>
                ))}
              </select>
            </label>

            {/* Exit Reason */}
            <label style={labelStyle}>
              Exit Reason
              <select
                value={filters.exit_reason ?? ''}
                onChange={(e) => update('exit_reason', e.target.value || undefined)}
                style={inputStyle}
              >
                <option value="">All</option>
                {options?.exit_reasons.map((er) => (
                  <option key={er} value={er}>{er}</option>
                ))}
              </select>
            </label>

            {/* Date From */}
            <label style={labelStyle}>
              Date From
              <input
                type="date"
                value={filters.date_from ?? ''}
                onChange={(e) => update('date_from', e.target.value || undefined)}
                style={inputStyle}
              />
            </label>

            {/* Date To */}
            <label style={labelStyle}>
              Date To
              <input
                type="date"
                value={filters.date_to ?? ''}
                onChange={(e) => update('date_to', e.target.value || undefined)}
                style={inputStyle}
              />
            </label>

            {/* P&L Min */}
            <label style={labelStyle}>
              P&L Min
              <input
                type="number"
                step="0.01"
                value={filters.pnl_min ?? ''}
                onChange={(e) => update('pnl_min', e.target.value || undefined)}
                placeholder="e.g. -100"
                style={inputStyle}
              />
            </label>

            {/* P&L Max */}
            <label style={labelStyle}>
              P&L Max
              <input
                type="number"
                step="0.01"
                value={filters.pnl_max ?? ''}
                onChange={(e) => update('pnl_max', e.target.value || undefined)}
                placeholder="e.g. 500"
                style={inputStyle}
              />
            </label>
          </div>

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button
              type="button"
              onClick={() => {
                onReset();
                setExpanded(false);
              }}
              style={{
                padding: '10px 16px',
                borderRadius: 4,
                border: '1px solid var(--border-strong)',
                background: 'transparent',
                color: 'var(--text-secondary)',
                fontSize: 14,
                cursor: 'pointer',
                minHeight: 44,
              }}
            >
              Reset
            </button>
            <button
              type="button"
              className="ws-range-btn"
              onClick={() => {
                onApply();
                setExpanded(false);
              }}
              style={{ minHeight: 44, fontSize: 14 }}
            >
              Apply &amp; Close
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
