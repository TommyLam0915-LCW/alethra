'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { ReplaySession } from './ReplayController';

/** Anything the API does not return is rendered as this, never as 0 or '—'. */
export const NOT_PROVIDED = '未提供';

export interface ExperimentSummary {
  id: string;
  title: string;
  status: string;
  current_version_id: string | null;
}

export interface VersionRules {
  id: string;
  version_number: number;
  strategy_spec: Record<string, unknown> | null;
  assumptions: Record<string, unknown> | null;
  change_reason: string | null;
  config_hash: string | null;
  locked_at: string | null;
  user_approved_at: string | null;
}

export type RulesState =
  | { status: 'idle'; versionId: null }
  | { status: 'loading'; versionId: string }
  | { status: 'resolved'; versionId: string; experiment: ExperimentSummary; version: VersionRules; assetIds: string[] }
  | { status: 'unresolved'; versionId: string; reason: string }
  | { status: 'error'; versionId: string; message: string };

/** strategy_spec.asset_ids is the only field that names what a session actually trades. */
export function specAssetIds(spec: Record<string, unknown> | null | undefined): string[] {
  const ids = spec?.asset_ids;
  return Array.isArray(ids) ? ids.filter((x): x is string => typeof x === 'string') : [];
}

/**
 * The session response carries `experiment_version_id` but no experiment id, and no
 * endpoint maps a version back to its experiment, so the rules are only reachable
 * through an experiment whose `current_version_id` equals that version. When that
 * match fails the rules stay unavailable — they are never inferred from defaults.
 */
export function useStrategyRules(session: ReplaySession | null): RulesState {
  const versionId = session?.experiment_version_id ?? null;
  const [state, setState] = useState<RulesState>({ status: 'idle', versionId: null });

  useEffect(() => {
    if (!versionId) {
      setState({ status: 'idle', versionId: null });
      return;
    }
    let cancelled = false;
    setState({ status: 'loading', versionId });
    (async () => {
      try {
        const experiments = (await api.experiments.list()) as ExperimentSummary[];
        const list = Array.isArray(experiments) ? experiments : [];
        const owner = list.find((e) => e.current_version_id === versionId) ?? null;
        if (!owner) {
          if (!cancelled) {
            setState({
              status: 'unresolved',
              versionId,
              reason: `${list.length} 個實驗沒有任何一個的 current_version_id 等於此版本，後端未提供 version→experiment 端點。`,
            });
          }
          return;
        }
        const versions = (await api.experiments.versions(owner.id)) as VersionRules[];
        const version = (Array.isArray(versions) ? versions : []).find((v) => v.id === versionId) ?? null;
        if (!version) {
          if (!cancelled) {
            setState({
              status: 'unresolved',
              versionId,
              reason: `實驗「${owner.title}」的版本清單查無此版本（可能已被新版本取代）。`,
            });
          }
          return;
        }
        if (cancelled) return;
        setState({
          status: 'resolved',
          versionId,
          experiment: owner,
          version,
          assetIds: specAssetIds(version.strategy_spec),
        });
      } catch (err) {
        if (!cancelled) {
          setState({
            status: 'error',
            versionId,
            message: err instanceof Error ? err.message : '策略規則無法載入',
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [versionId]);

  return state;
}
