const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function apiFetch(endpoint: string, options: RequestInit = {}) {
  const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...options.headers as Record<string, string>,
  };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_URL}${endpoint}`, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    if (typeof window !== 'undefined') {
      localStorage.removeItem('token');
      window.location.href = '/login';
    }
    throw new Error('Unauthorized');
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new ApiError(error.detail || 'Request failed', response.status);
  }

  // 204 No Content carries no body; response.json() would throw on it.
  if (response.status === 204) {
    return null;
  }

  return response.json();
}

export const api = {
  auth: {
    register: (data: { email: string; password: string; display_name?: string }) =>
      apiFetch('/api/auth/register', { method: 'POST', body: JSON.stringify(data) }),
    login: (data: { email: string; password: string }) =>
      apiFetch('/api/auth/login', { method: 'POST', body: JSON.stringify(data) }),
    me: () => apiFetch('/api/auth/me'),
  },
  brand: {
    get: () => apiFetch('/api/v1/brand'),
  },
  demoAssets: {
    list: () => apiFetch('/api/v1/demo-assets'),
  },
  experiments: {
    list: () => apiFetch('/api/v1/experiments/'),
    get: (id: string) => apiFetch(`/api/v1/experiments/${id}`),
    create: (data: any) =>
      apiFetch('/api/v1/experiments/', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: string, data: any) =>
      apiFetch(`/api/v1/experiments/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    versions: (id: string) => apiFetch(`/api/v1/experiments/${id}/versions`),
    export: (id: string) => apiFetch(`/api/v1/experiments/${id}/export`),
    methodReview: (id: string) => apiFetch(`/api/v1/experiments/${id}/method-review`),
  },
  runs: {
    create: (versionId: string) =>
      apiFetch(`/api/v1/versions/${versionId}/runs`, { method: 'POST' }),
    get: (runId: string) =>
      apiFetch(`/api/v1/runs/${runId}`),
    list: (experimentId: string) => apiFetch(`/api/v1/experiments/${experimentId}/runs`),
  },
  timeline: {
    get: (experimentId: string) => apiFetch(`/api/v1/experiments/${experimentId}/timeline`),
  },
  notes: {
    list: (experimentId: string) => apiFetch(`/api/v1/experiments/${experimentId}/notes`),
    create: (experimentId: string, data: { content: string; version_id?: string }) =>
      apiFetch(`/api/v1/experiments/${experimentId}/notes`, { method: 'POST', body: JSON.stringify(data) }),
  },
  versions: {
    compare: (v1Id: string, v2Id: string) =>
      apiFetch(`/api/v1/versions/compare?v1=${v1Id}&v2=${v2Id}`),
  },
  simulations: {
    create: (versionId: string, data: { initial_virtual_cash: number }) =>
      apiFetch(`/api/v1/simulations/versions/${versionId}`, { method: 'POST', body: JSON.stringify(data) }),
    get: (sessionId: string) =>
      apiFetch(`/api/v1/simulations/${sessionId}`),
    advance: (sessionId: string) =>
      apiFetch(`/api/v1/simulations/${sessionId}/advance`, { method: 'POST' }),
    decisions: (sessionId: string) =>
      apiFetch(`/api/v1/simulations/${sessionId}/decisions`),
    pause: (sessionId: string) =>
      apiFetch(`/api/v1/simulations/${sessionId}/pause`, { method: 'POST' }),
    resume: (sessionId: string) =>
      apiFetch(`/api/v1/simulations/${sessionId}/resume`, { method: 'POST' }),
    retire: (sessionId: string) =>
      apiFetch(`/api/v1/simulations/${sessionId}/retire`, { method: 'POST' }),
  },
  paperPortfolio: {
    list: () => apiFetch('/api/v1/paper-portfolio'),
    get: (id: string) => apiFetch(`/api/v1/paper-portfolio/${id}`),
    create: (data: { initial_virtual_cash: number; currency?: string; simulation_session_id?: string }) =>
      apiFetch('/api/v1/paper-portfolio', { method: 'POST', body: JSON.stringify(data) }),
    positions: (id: string, statusFilter?: string) =>
      apiFetch(`/api/v1/paper-portfolio/${id}/positions${statusFilter ? `?status_filter=${statusFilter}` : ''}`),
    orders: (id: string) => apiFetch(`/api/v1/paper-portfolio/${id}/orders`),
    createOrder: (id: string, data: any) =>
      apiFetch(`/api/v1/paper-portfolio/${id}/orders`, { method: 'POST', body: JSON.stringify(data) }),
    fills: (portfolioId: string, orderId: string) =>
      apiFetch(`/api/v1/paper-portfolio/${portfolioId}/orders/${orderId}/fills`),
    decisions: (id: string) => apiFetch(`/api/v1/paper-portfolio/${id}/decisions`),
    createDecision: (id: string, data: any) =>
      apiFetch(`/api/v1/paper-portfolio/${id}/decisions`, { method: 'POST', body: JSON.stringify(data) }),
    evaluateOutcome: (portfolioId: string, decisionId: string, data: any) =>
      apiFetch(`/api/v1/paper-portfolio/${portfolioId}/decisions/${decisionId}/outcome`, { method: 'POST', body: JSON.stringify(data) }),
    createOverride: (id: string, data: any) =>
      apiFetch(`/api/v1/paper-portfolio/${id}/override-requests`, { method: 'POST', body: JSON.stringify(data) }),
    pause: (id: string) =>
      apiFetch(`/api/v1/paper-portfolio/${id}/pause`, { method: 'POST' }),
    resume: (id: string) =>
      apiFetch(`/api/v1/paper-portfolio/${id}/resume`, { method: 'POST' }),
    retire: (id: string) =>
      apiFetch(`/api/v1/paper-portfolio/${id}/retire`, { method: 'POST' }),
  },
  modelResearch: {
    list: (statusFilter?: string) =>
      apiFetch(`/api/v1/model-research${statusFilter ? `?status_filter=${statusFilter}` : ''}`),
    get: (id: string) => apiFetch(`/api/v1/model-research/${id}`),
    create: (data: { title: string; purpose: string; description?: string }) =>
      apiFetch('/api/v1/model-research', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: string, data: any) =>
      apiFetch(`/api/v1/model-research/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
    versions: (recordId: string) =>
      apiFetch(`/api/v1/model-research/${recordId}/versions`),
    createVersion: (recordId: string, data: any) =>
      apiFetch(`/api/v1/model-research/${recordId}/versions`, { method: 'POST', body: JSON.stringify(data) }),
    lockVersion: (recordId: string, versionId: string) =>
      apiFetch(`/api/v1/model-research/${recordId}/versions/${versionId}/lock`, { method: 'POST' }),
    revokeVersion: (recordId: string, versionId: string, reason: string) =>
      apiFetch(`/api/v1/model-research/${recordId}/versions/${versionId}/revoke?reason=${encodeURIComponent(reason)}`, { method: 'POST' }),
    evaluations: (recordId: string) =>
      apiFetch(`/api/v1/model-research/${recordId}/evaluations`),
    createEvaluation: (recordId: string, data: any) =>
      apiFetch(`/api/v1/model-research/${recordId}/evaluations`, { method: 'POST', body: JSON.stringify(data) }),
    createExport: (recordId: string, data: { model_version_id: string; include_private_notes?: boolean }) =>
      apiFetch(`/api/v1/model-research-records/${recordId}/exports`, { method: 'POST', body: JSON.stringify(data) }),
    getExport: (exportId: string) =>
      apiFetch(`/api/v1/exports/${exportId}`),
    downloadExport: (exportId: string) =>
      apiFetch(`/api/v1/exports/${exportId}/download`),
  },
  personalMemory: {
    list: (typeFilter?: string) =>
      apiFetch(`/api/v1/personal-memory${typeFilter ? `?memory_type_filter=${typeFilter}` : ''}`),
    get: (id: string) => apiFetch(`/api/v1/personal-memory/${id}`),
    create: (data: { memory_type: string; content: string; user_confirmed?: boolean; privacy_scope?: string }) =>
      apiFetch('/api/v1/personal-memory', { method: 'POST', body: JSON.stringify(data) }),
    confirm: (id: string, confirmed: boolean) =>
      apiFetch(`/api/v1/personal-memory/${id}/confirm`, { method: 'POST', body: JSON.stringify({ confirmed }) }),
    deactivate: async (id: string): Promise<void> => {
      await apiFetch(`/api/v1/personal-memory/${id}`, { method: 'DELETE' });
    },
    setTrainingOptIn: (id: string, optIn: boolean) =>
      apiFetch(`/api/v1/personal-memory/${id}/training-opt-in?opt_in=${optIn}`, { method: 'PUT' }),
  },
  modelCards: {
    get: (id: string) => apiFetch(`/api/v1/model-cards/${id}`),
    create: (data: any) =>
      apiFetch('/api/v1/model-cards', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: string, data: any) =>
      apiFetch(`/api/v1/model-cards/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  },
  researchDrafts: {
    list: () => apiFetch('/api/v1/research-drafts/'),
    get: (id: string) => apiFetch(`/api/v1/research-drafts/${id}`),
    create: (data: any) =>
      apiFetch('/api/v1/research-drafts/', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: string, data: any) =>
      apiFetch(`/api/v1/research-drafts/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
    delete: async (id: string): Promise<void> => {
      await apiFetch(`/api/v1/research-drafts/${id}`, { method: 'DELETE' });
    },
    generateQuestions: (id: string) =>
      apiFetch(`/api/v1/research-drafts/${id}/questions`, { method: 'POST' }),
    submitAnswers: (id: string, answers: Record<string, string>) =>
      apiFetch(`/api/v1/research-drafts/${id}/answers`, { method: 'POST', body: JSON.stringify({ answers }) }),
    approve: (id: string, action: 'accept' | 'reject', notes?: string) =>
      apiFetch(`/api/v1/research-drafts/${id}/approve`, { method: 'POST', body: JSON.stringify({ action, notes }) }),
    revisions: (id: string) =>
      apiFetch(`/api/v1/research-drafts/${id}/revisions`),
    auditEvents: (id: string) =>
      apiFetch(`/api/v1/research-drafts/${id}/audit-events`),
  },
  pilot: {
    submitApplication: (data: any) =>
      apiFetch('/api/v1/pilot/applications', { method: 'POST', body: JSON.stringify(data) }),
    getMyApplication: () => apiFetch('/api/v1/pilot/applications/me'),
    listApplications: (statusFilter?: string) =>
      apiFetch(`/api/v1/pilot/applications${statusFilter ? `?status_filter=${statusFilter}` : ''}`),
    updateApplicationStatus: (id: string, status: string) =>
      apiFetch(`/api/v1/pilot/applications/${id}/status?new_status=${status}`, { method: 'PUT' }),
    getMyEntitlement: () => apiFetch('/api/v1/pilot/entitlements/me'),
    grantEntitlement: (data: any) =>
      apiFetch('/api/v1/pilot/entitlements', { method: 'POST', body: JSON.stringify(data) }),
    updateEntitlement: (id: string, data: any) =>
      apiFetch(`/api/v1/pilot/entitlements/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    listEntitlements: () => apiFetch('/api/v1/pilot/entitlements'),
    recordUsageEvent: (data: { event_type: string; metadata_json?: any }) =>
      apiFetch('/api/v1/pilot/usage-events', { method: 'POST', body: JSON.stringify(data) }),
    getMyUsageEvents: (eventType?: string) =>
      apiFetch(`/api/v1/pilot/usage-events/me${eventType ? `?event_type=${eventType}` : ''}`),
    listUsageEvents: (params?: { event_type?: string; user_id?: string }) => {
      const qs = new URLSearchParams();
      if (params?.event_type) qs.set('event_type', params.event_type);
      if (params?.user_id) qs.set('user_id', params.user_id);
      const q = qs.toString();
      return apiFetch(`/api/v1/pilot/usage-events${q ? `?${q}` : ''}`);
    },
    createCostRecord: (data: any) =>
      apiFetch('/api/v1/pilot/costs', { method: 'POST', body: JSON.stringify(data) }),
    listCostRecords: (params?: { cost_type?: string; source?: string }) => {
      const qs = new URLSearchParams();
      if (params?.cost_type) qs.set('cost_type', params.cost_type);
      if (params?.source) qs.set('source', params.source);
      const q = qs.toString();
      return apiFetch(`/api/v1/pilot/costs${q ? `?${q}` : ''}`);
    },
    createSupportLog: (data: any) =>
      apiFetch('/api/v1/pilot/support-logs', { method: 'POST', body: JSON.stringify(data) }),
    updateSupportLog: (id: string, data: any) =>
      apiFetch(`/api/v1/pilot/support-logs/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    listSupportLogs: (params?: { user_id?: string; category?: string }) => {
      const qs = new URLSearchParams();
      if (params?.user_id) qs.set('user_id', params.user_id);
      if (params?.category) qs.set('category', params.category);
      const q = qs.toString();
      return apiFetch(`/api/v1/pilot/support-logs${q ? `?${q}` : ''}`);
    },
    createHypothesis: (data: any) =>
      apiFetch('/api/v1/pilot/hypotheses', { method: 'POST', body: JSON.stringify(data) }),
    updateHypothesis: (id: string, data: any) =>
      apiFetch(`/api/v1/pilot/hypotheses/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    listHypotheses: (statusFilter?: string) =>
      apiFetch(`/api/v1/pilot/hypotheses${statusFilter ? `?status_filter=${statusFilter}` : ''}`),
    getAdminDashboard: () => apiFetch('/api/v1/pilot/admin/dashboard'),
    getHypothesesDashboard: () => apiFetch('/api/v1/pilot/admin/hypotheses-dashboard'),
  },
  apiKeys: {
    list: () => apiFetch('/api/v1/api-keys'),
    create: (data: { name: string; scopes?: string[] }) =>
      apiFetch('/api/v1/api-keys', { method: 'POST', body: JSON.stringify(data) }),
    revoke: (id: string) =>
      apiFetch(`/api/v1/api-keys/${id}`, { method: 'DELETE' }),
  },
  marketData: {
    status: () => apiFetch('/api/v1/market-data/status'),
    bars: (symbol: string, startDate?: string, endDate?: string) => {
      const qs = new URLSearchParams();
      if (startDate) qs.set('start_date', startDate);
      if (endDate) qs.set('end_date', endDate);
      const q = qs.toString();
      return apiFetch(`/api/v1/market-data/bars/${symbol}${q ? `?${q}` : ''}`);
    },
    latest: (symbol: string) => apiFetch(`/api/v1/market-data/latest/${symbol}`),
  },
  instruments: {
    list: (market?: string, assetType?: string) => {
      const qs = new URLSearchParams();
      if (market) qs.set('market', market);
      if (assetType) qs.set('asset_type', assetType);
      const q = qs.toString();
      return apiFetch(`/api/v1/instruments${q ? `?${q}` : ''}`);
    },
    get: (instrumentId: string) => apiFetch(`/api/v1/instruments/${instrumentId}`),
    bars: (instrumentId: string, startDate: string, endDate: string) => {
      const qs = new URLSearchParams({ start_date: startDate, end_date: endDate });
      return apiFetch(`/api/v1/instruments/${instrumentId}/bars?${qs}`);
    },
    markets: () => apiFetch('/api/v1/markets'),
    getMarket: (marketCode: string) => apiFetch(`/api/v1/markets/${marketCode}`),
  },
  execution: {
    listIntents: () => apiFetch('/api/v1/execution/intents'),
    getIntent: (intentId: string) => apiFetch(`/api/v1/execution/intents/${intentId}`),
    createIntent: (data: { portfolio_id: string; symbol: string; side: string; quantity: number; order_type?: string; limit_price?: number; idempotency_key?: string }) =>
      apiFetch('/api/v1/execution/intents', { method: 'POST', body: JSON.stringify(data) }),
    authorize: (intentId: string, authorizationToken: string) =>
      apiFetch(`/api/v1/execution/intents/${intentId}/authorize`, { method: 'POST', body: JSON.stringify({ authorization_token: authorizationToken }) }),
    submit: (intentId: string) =>
      apiFetch(`/api/v1/execution/intents/${intentId}/submit`, { method: 'POST' }),
    cancel: (intentId: string) =>
      apiFetch(`/api/v1/execution/intents/${intentId}/cancel`, { method: 'POST' }),
    reconcile: (intentId: string) =>
      apiFetch(`/api/v1/execution/intents/${intentId}/reconcile`, { method: 'POST' }),
  },
  inference: {
    listRuns: () => apiFetch('/api/v1/inference/runs'),
    getRun: (runId: string) => apiFetch(`/api/v1/inference/runs/${runId}`),
    createRun: (data: { model_version_id: string; input_spec: any }) =>
      apiFetch('/api/v1/inference/runs', { method: 'POST', body: JSON.stringify(data) }),
  },
  training: {
    listJobs: () => apiFetch('/api/v1/training/jobs'),
    getJob: (jobId: string) => apiFetch(`/api/v1/training/jobs/${jobId}`),
    createJob: (data: { experiment_id: string; parameter_grid: Record<string, any[]> }) =>
      apiFetch('/api/v1/training/jobs', { method: 'POST', body: JSON.stringify(data) }),
    cancel: (jobId: string) =>
      apiFetch(`/api/v1/training/jobs/${jobId}/cancel`, { method: 'POST' }),
  },
  ml: {
    train: (params: { instrument_id: string; model_type?: string; experiment_id?: string; start_date?: string; end_date?: string }) => {
      const qs = new URLSearchParams();
      qs.set('instrument_id', params.instrument_id);
      if (params.model_type) qs.set('model_type', params.model_type);
      if (params.experiment_id) qs.set('experiment_id', params.experiment_id);
      if (params.start_date) qs.set('start_date', params.start_date);
      if (params.end_date) qs.set('end_date', params.end_date);
      return apiFetch(`/api/v1/ml/train?${qs}`, { method: 'POST' });
    },
    listModels: () => apiFetch('/api/v1/ml/models'),
    getModel: (modelId: string) => apiFetch(`/api/v1/ml/models/${modelId}`),
    deleteModel: (modelId: string) => apiFetch(`/api/v1/ml/models/${modelId}`, { method: 'DELETE' }),
    predict: (modelId: string, params?: { instrument_id?: string; start_date?: string; end_date?: string }) => {
      const qs = new URLSearchParams();
      if (params?.instrument_id) qs.set('instrument_id', params.instrument_id);
      if (params?.start_date) qs.set('start_date', params.start_date);
      if (params?.end_date) qs.set('end_date', params.end_date);
      const q = qs.toString();
      return apiFetch(`/api/v1/ml/models/${modelId}/predict${q ? `?${q}` : ''}`, { method: 'POST' });
    },
    predictLatest: (modelId: string, instrumentId?: string) => {
      const qs = instrumentId ? `?instrument_id=${instrumentId}` : '';
      return apiFetch(`/api/v1/ml/models/${modelId}/predict-latest${qs}`, { method: 'POST' });
    },
  },
  billing: {
    listPlans: () => apiFetch('/api/v1/plans'),
    getMySubscription: () => apiFetch('/api/v1/subscriptions/me'),
    createPaymentIntent: (data: { subscription_id: string }) =>
      apiFetch('/api/v1/payments/create-intent', { method: 'POST', body: JSON.stringify(data) }),
    getOperationalStatus: () => apiFetch('/api/v1/operational-status'),
  },
};
