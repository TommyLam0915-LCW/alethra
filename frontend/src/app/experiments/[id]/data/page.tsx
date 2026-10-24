'use client';

import { useEffect, useState } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { api } from '@/lib/api';

interface Dataset {
  id: string;
  name: string;
  provider: string;
  source_type: string;
  license_status: string | null;
  description: string | null;
  created_at: string;
}

interface ExperimentVersion {
  id: string;
  version_number: number;
  strategy_spec: any;
  dataset_id: string | null;
  data_source: string | null;
  data_fetch_date_range: any;
  data_gaps_notes: string | null;
  data_timezone: string | null;
}

export default function ExperimentDataPage() {
  const router = useRouter();
  const params = useParams();
  const experimentId = params.id as string;

  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [currentVersion, setCurrentVersion] = useState<ExperimentVersion | null>(null);
  const [selectedDatasetId, setSelectedDatasetId] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadData();
  }, [experimentId]);

  async function loadData() {
    try {
      setLoading(true);
      setError(null);

      // Load datasets
      const datasetsResp = await api.datasets.list();
      setDatasets(datasetsResp);

      // Load current experiment version
      const expResp = await api.experiments.get(experimentId);
      if (expResp.current_version_id) {
        const versionsResp = await api.experiments.versions(experimentId);
        const current = versionsResp.find((v: ExperimentVersion) => v.id === expResp.current_version_id);
        setCurrentVersion(current || null);
        if (current?.dataset_id) {
          setSelectedDatasetId(current.dataset_id);
        }
      }
    } catch (err: any) {
      setError(err.message || 'Failed to load data');
    } finally {
      setLoading(false);
    }
  }

  async function handleBindDataset() {
    if (!selectedDatasetId || !currentVersion) return;

    try {
      setSaving(true);
      setError(null);

      const selectedDataset = datasets.find(d => d.id === selectedDatasetId);
      if (!selectedDataset) {
        throw new Error('Selected dataset not found');
      }

      // Update experiment version with dataset binding
      await api.experiments.update(experimentId, {
        change_reason: `Bound to dataset: ${selectedDataset.name}`,
        dataset_id: selectedDatasetId,
        data_source: selectedDataset.provider,
        data_fetch_date_range: {
          requested_start: currentVersion.strategy_spec.start_date,
          requested_end: currentVersion.strategy_spec.end_date,
          note: 'Actual dates depend on data availability',
        },
        data_gaps_notes: null,
        data_timezone: 'America/New_York', // Default for US stocks
      });

      await loadData();
      alert('Dataset bound successfully');
    } catch (err: any) {
      setError(err.message || 'Failed to bind dataset');
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <div className="p-8">Loading...</div>;
  }

  return (
    <div className="max-w-4xl mx-auto p-8">
      <div className="mb-8">
        <button
          onClick={() => router.push(`/experiments/${experimentId}`)}
          className="text-blue-600 hover:text-blue-800 mb-4"
        >
          ← Back to Experiment
        </button>
        <h1 className="text-3xl font-bold">Dataset Management</h1>
        <p className="text-gray-600 mt-2">
          Bind a specific dataset to this experiment version for reproducible backtesting.
        </p>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded mb-6">
          {error}
        </div>
      )}

      {currentVersion && (
        <div className="bg-white border rounded-lg p-6 mb-6">
          <h2 className="text-xl font-semibold mb-4">Current Version (v{currentVersion.version_number})</h2>
          
          {currentVersion.dataset_id ? (
            <div>
              <p className="text-green-700 font-medium mb-2">✓ Dataset Bound</p>
              <div className="space-y-2 text-sm">
                <p><strong>Dataset ID:</strong> {currentVersion.dataset_id}</p>
                <p><strong>Source:</strong> {currentVersion.data_source || 'N/A'}</p>
                <p><strong>Timezone:</strong> {currentVersion.data_timezone || 'N/A'}</p>
                {currentVersion.data_fetch_date_range && (
                  <div>
                    <p><strong>Date Range:</strong></p>
                    <pre className="bg-gray-50 p-2 rounded mt-1 text-xs overflow-auto">
                      {JSON.stringify(currentVersion.data_fetch_date_range, null, 2)}
                    </pre>
                  </div>
                )}
                {currentVersion.data_gaps_notes && (
                  <p><strong>Gaps Notes:</strong> {currentVersion.data_gaps_notes}</p>
                )}
              </div>
            </div>
          ) : (
            <p className="text-gray-600">No dataset bound. Select one below.</p>
          )}
        </div>
      )}

      <div className="bg-white border rounded-lg p-6">
        <h2 className="text-xl font-semibold mb-4">Bind Dataset</h2>
        
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Select Dataset
            </label>
            <select
              value={selectedDatasetId}
              onChange={(e) => setSelectedDatasetId(e.target.value)}
              className="w-full border rounded px-3 py-2"
              disabled={saving}
            >
              <option value="">-- Select a dataset --</option>
              {datasets.map(dataset => (
                <option key={dataset.id} value={dataset.id}>
                  {dataset.name} ({dataset.provider}, {dataset.source_type})
                </option>
              ))}
            </select>
          </div>

          {selectedDatasetId && (
            <div className="bg-gray-50 p-4 rounded">
              <p className="text-sm text-gray-600 mb-2">Selected dataset details:</p>
              {(() => {
                const ds = datasets.find(d => d.id === selectedDatasetId);
                if (!ds) return null;
                return (
                  <div className="space-y-1 text-sm">
                    <p><strong>Name:</strong> {ds.name}</p>
                    <p><strong>Provider:</strong> {ds.provider}</p>
                    <p><strong>Type:</strong> {ds.source_type}</p>
                    {ds.description && <p><strong>Description:</strong> {ds.description}</p>}
                    {ds.license_status && <p><strong>License:</strong> {ds.license_status}</p>}
                  </div>
                );
              })()}
            </div>
          )}

          <button
            onClick={handleBindDataset}
            disabled={!selectedDatasetId || saving}
            className="bg-blue-600 text-white px-6 py-2 rounded hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
          >
            {saving ? 'Binding...' : 'Bind Dataset'}
          </button>
        </div>
      </div>

      <div className="mt-8 bg-yellow-50 border border-yellow-200 rounded-lg p-6">
        <h3 className="font-semibold text-yellow-900 mb-2">About Dataset Binding</h3>
        <ul className="text-sm text-yellow-800 space-y-1 list-disc list-inside">
          <li>Binding a dataset ensures reproducible backtesting with the exact same data.</li>
          <li>The system records the data source, date range, timezone, and any gaps.</li>
          <li>Once bound, the dataset cannot be changed for locked versions.</li>
          <li>For real market data, ensure you have proper licensing for research use.</li>
        </ul>
      </div>
    </div>
  );
}
