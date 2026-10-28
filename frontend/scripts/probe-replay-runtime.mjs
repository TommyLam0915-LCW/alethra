/**
 * Read-only probe of the disposable E2E runtime's replay state.
 *
 * Logs in with the generated git-ignored credential file and reports which paper
 * portfolios are bound to which simulation session, plus the server-owned replay
 * cursor for each session. Nothing is written and no credential, token or secret
 * value is ever printed — only ids, statuses and cursor fields.
 *
 *   E2E_CRED_FILE=.local/e2e-credentials.env node scripts/probe-replay-runtime.mjs
 */
import { readFileSync } from 'node:fs';

const CRED_FILE = process.env.E2E_CRED_FILE ?? '.local/e2e-credentials.env';
const BACKEND = process.env.E2E_BACKEND_BASE ?? 'http://127.0.0.1:8099';

function loadEnv(file) {
  const out = {};
  for (const line of readFileSync(file, 'utf-8').split('\n')) {
    const m = /^([A-Z0-9_]+)=(.*)$/.exec(line.trim());
    if (m) out[m[1]] = m[2];
  }
  return out;
}

const creds = loadEnv(CRED_FILE);
const login = await fetch(`${BACKEND}/api/auth/login`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email: creds.E2E_EMAIL, password: creds.E2E_PASSWORD }),
});
if (!login.ok) {
  console.log(`LOGIN_HTTP=${login.status}`);
  process.exit(1);
}
const { access_token: token } = await login.json();
console.log(`LOGIN=ok token_present=${!!token}`);

const auth = { Authorization: `Bearer ${token}` };
const get = async (path) => {
  const res = await fetch(`${BACKEND}${path}`, { headers: auth });
  return { status: res.status, body: res.ok ? await res.json() : await res.text() };
};

const portfolios = await get('/api/v1/paper-portfolio');
if (!Array.isArray(portfolios.body)) {
  console.log(`PORTFOLIOS=http ${portfolios.status}`);
  process.exit(0);
}
console.log(`PORTFOLIOS=${portfolios.body.length}`);
for (const p of portfolios.body) {
  console.log(
    `  portfolio id=${p.id} status=${p.status} currency=${p.currency} ` +
      `cash=${p.current_virtual_cash} session=${p.simulation_session_id ?? 'none'}`
  );
}

const seen = new Set();
for (const p of portfolios.body) {
  const id = p.simulation_session_id;
  if (!id || seen.has(id)) continue;
  seen.add(id);
  const s = await get(`/api/v1/simulations/${id}`);
  if (!s.body || typeof s.body !== 'object') {
    console.log(`  session ${id} = http ${s.status}`);
    continue;
  }
  const b = s.body;
  console.log(
    `  session id=${b.id} status=${b.status} timeframe=${b.timeframe ?? 'null(1d)'} ` +
      `snapshot=${b.current_snapshot_date ?? 'null'} bar=${b.current_bar_timestamp ?? 'null'} ` +
      `cutoff=${b.data_cutoff_at} observed_days=${b.observed_days} decisions=${b.total_decisions} ` +
      `provider=${b.provider_id ?? 'null'} version=${b.experiment_version_id}`
  );
}

const layout = await get('/api/v1/layouts/workspace');
const saved = layout.body?.layout_json ?? layout.body?.layout ?? null;
console.log(
  `LAYOUT=http ${layout.status} symbol=${saved?.symbol ?? 'default(US:AAPL)'} ` +
    `range=${saved?.range ?? 'default(1Y)'} tab=${saved?.tab ?? 'default'}`
);

const symbol = saved?.symbol ?? 'US:AAPL';
const bars = await get(
  `/api/v1/instruments/${encodeURIComponent(symbol)}/bars?start_date=1900-01-01&end_date=2099-12-31`
);
if (!Array.isArray(bars.body?.bars)) {
  console.log(`BARS ${symbol} = http ${bars.status}`);
} else {
  const list = bars.body.bars;
  console.log(
    `BARS symbol=${symbol} count=${list.length} timeframe=${bars.body.timeframe ?? '?'} ` +
      `first=${list[0]?.date ?? '-'} last=${list[list.length - 1]?.date ?? '-'} ` +
      `as_of=${bars.body.data_as_of ?? bars.body.data_quality?.latest_bar_date ?? '-'}`
  );
}

// Strategy rules are only reachable through the experiment that owns the version,
// and the session response carries no experiment id — so the mapping has to go via
// each experiment's current_version_id. Read-only, and ids/spec only.
const experiments = await get('/api/v1/experiments/');
if (!Array.isArray(experiments.body)) {
  console.log(`EXPERIMENTS=http ${experiments.status}`);
} else {
  console.log(`EXPERIMENTS=${experiments.body.length}`);
  for (const id of seen) {
    const s = await get(`/api/v1/simulations/${id}`);
    const versionId = s.body?.experiment_version_id;
    const owner = experiments.body.find((e) => e.current_version_id === versionId) ?? null;
    console.log(
      `  session ${id} version=${versionId} ` +
        `initial_cash=${s.body?.initial_virtual_cash} equity=${s.body?.paper_equity ?? 'null'} ` +
        `state_version=${s.body?.state_version} policy=${s.body?.simulation_policy_version}`
    );
    if (!owner) {
      console.log(`    rules: version ${versionId} is not any experiment's current_version_id -> unresolved`);
      continue;
    }
    console.log(`    experiment id=${owner.id} title=${owner.title} status=${owner.status}`);
    const versions = await get(`/api/v1/experiments/${owner.id}/versions`);
    if (!Array.isArray(versions.body)) {
      console.log(`    versions=http ${versions.status}`);
      continue;
    }
    const v = versions.body.find((x) => x.id === versionId);
    if (!v) {
      console.log(`    version ${versionId} absent from ${versions.body.length} listed versions`);
      continue;
    }
    const spec = v.strategy_spec ?? {};
    console.log(
      `    v${v.version_number} template=${spec.template ?? spec.strategy_template ?? '?'} ` +
        `asset_ids=${JSON.stringify(spec.asset_ids ?? null)} ` +
        `start=${spec.start_date ?? '?'} end=${spec.end_date ?? '?'} ` +
        `freq=${spec.frequency ?? spec.timeframe ?? '-'} ` +
        `params=${JSON.stringify(spec.params ?? spec.parameters ?? null)} ` +
        `config_hash=${(v.config_hash ?? '').slice(0, 12) || 'null'} ` +
        `locked_at=${v.locked_at ?? 'null'} approved_at=${v.user_approved_at ?? 'null'}`
    );
    console.log(`    spec_keys=${Object.keys(spec).join(',')} assumptions=${v.assumptions ? 'present' : 'null'}`);
  }
}

// The manual paper order can only be reviewed honestly when the session-bound asset
// is addressable from the workspace: check search, detail and stored bars for it.
const assetIds = new Set();
for (const id of seen) {
  const s = await get(`/api/v1/simulations/${id}`);
  const owner = experiments.body?.find?.((e) => e.current_version_id === s.body?.experiment_version_id);
  if (!owner) continue;
  const versions = await get(`/api/v1/experiments/${owner.id}/versions`);
  const v = Array.isArray(versions.body) ? versions.body.find((x) => x.id === s.body?.experiment_version_id) : null;
  for (const a of v?.strategy_spec?.asset_ids ?? []) assetIds.add(a);
}
for (const asset of assetIds) {
  const search = await get(`/api/v1/instruments?q=${encodeURIComponent(asset)}`);
  const hits = Array.isArray(search.body?.instruments) ? search.body.instruments.length : `http ${search.status}`;
  const detail = await get(`/api/v1/instruments/${encodeURIComponent(asset)}`);
  const dBars = await get(
    `/api/v1/instruments/${encodeURIComponent(asset)}/bars?start_date=1900-01-01&end_date=2099-12-31`
  );
  const list = Array.isArray(dBars.body?.bars) ? dBars.body.bars : null;
  console.log(
    `SESSION_ASSET ${asset}: search_hits=${hits} detail=http ${detail.status} ` +
      `(lot_size=${detail.body?.lot_size ?? 'n/a'} currency=${detail.body?.currency ?? 'n/a'} name=${detail.body?.name ?? 'n/a'}) ` +
      `bars=http ${dBars.status} count=${list ? list.length : '-'} ` +
      `range=${list && list.length ? `${list[0].date}..${list[list.length - 1].date}` : '-'} ` +
      `timeframe=${dBars.body?.timeframe ?? '-'}`
  );
}
