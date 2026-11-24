// G3-6: two address defects, one class.
//
// The journal's tab was React state, so every view of /journal shared one URL:
// a bookmark, a pasted link, or a reload always returned to Trades, whatever
// was on screen. `/journal/unified` is now a page, the other panels keep
// `?tab=`, and both are written by one function so path and query cannot
// disagree about which view is open.
//
// The empty-state CTA sent a person with no trades to `/strategy-lab`, which
// has never been a route in this app — a 404 aimed at the one reader the button
// exists for. That is a second instance of the same mistake (a destination
// invented rather than looked up), so the gate here is the whole sweep, not the
// one string that was reported.
//
// Scope of the sweep, stated because it is a blind spot: quoted literal paths
// in `src/**` passed to onNavigate/router.push/router.replace or placed in
// `href="…"`. Template-literal hrefs (`/models/${id}`) are not read here — a
// value interpolated from data cannot be resolved statically, and the routes
// they target are only in the denominator below.
//
// Run with: node22 --test tests/journalTabUrl.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { register } from 'node:module';

register('./tsResolveLoader.mjs', import.meta.url);

const {
  DEFAULT_JOURNAL_SOURCE,
  JOURNAL_PAGE_PATH,
  JOURNAL_SOURCES,
  UNIFIED_TAB,
  journalPagePath,
  journalQueryString,
  journalViewUrl,
  readJournalSource,
  readJournalTab,
} = await import('../src/lib/journalTabUrl.ts');

const ROOT = new URL('../', import.meta.url);
const src = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const readAt = (rootRel) => readFileSync(new URL(rootRel, ROOT), 'utf8');

const panelSource = () => src('../src/components/journal/JournalDashboard.tsx');

// Read from the panel's own tab table. A hand-written list here would be a
// second registry, and the test would keep passing while the address rejected a
// tab that is on screen.
const TAB_KEYS = [...panelSource().matchAll(/^\s*\{ key: '([^']+)', label:/gm)].map(
  (match) => match[1],
);
assert.ok(TAB_KEYS.length >= 9, `found only ${TAB_KEYS.length} tabs: ${TAB_KEYS}`);

// --- which tab the address names ------------------------------------------

test('the address names a tab the panel has', () => {
  for (const key of TAB_KEYS) {
    assert.equal(readJournalTab(key, TAB_KEYS, 'trades'), key);
  }
});

test('an address naming no tab falls back instead of rendering nothing', () => {
  // The danger is not the fallback; it is the URL still claiming a tab that is
  // not on screen. Falling back is only honest because the panel rewrites the
  // address afterwards, which the source gate below checks.
  for (const bad of ['', 'strategy', 'Trades', '../../admin', 'unified ', null, undefined]) {
    assert.equal(readJournalTab(bad, TAB_KEYS, 'trades'), 'trades', `accepted ${JSON.stringify(bad)}`);
  }
});

test('a tab name is matched exactly, not as a prefix or substring', () => {
  for (const near of ['trade', 'unifiedx', 'compare', 'equity']) {
    assert.equal(readJournalTab(near, TAB_KEYS, 'trades'), 'trades');
  }
});

test('a page path can seed the tab the query does not name', () => {
  // `/journal/unified` with no `?tab=` must open Unified; without this the new
  // route would render Trades under a unified address.
  assert.equal(readJournalTab(null, TAB_KEYS, 'unified'), 'unified');
  assert.equal(readJournalTab('equity-curve', TAB_KEYS, 'unified'), 'equity-curve');
});

test('the ledger scope is one of the three the service knows', () => {
  for (const source of JOURNAL_SOURCES) {
    assert.equal(readJournalSource(source), source);
  }
  for (const bad of ['', 'both', 'ALL', 'all;', null, undefined]) {
    assert.equal(readJournalSource(bad), DEFAULT_JOURNAL_SOURCE, `accepted ${JSON.stringify(bad)}`);
  }
});

// --- what the address says back --------------------------------------------

test('the unified view has a page of its own', () => {
  assert.equal(journalPagePath('unified'), '/journal/unified');
  for (const tab of TAB_KEYS.filter((key) => key !== UNIFIED_TAB)) {
    assert.equal(journalPagePath(tab), JOURNAL_PAGE_PATH, `${tab} invented a page`);
  }
});

test('the default view keeps the plain address', () => {
  // Two spellings of one view are two links that can disagree, and `/journal`
  // is the link the navigation already uses.
  assert.equal(journalViewUrl('trades', 'trades', 'all'), '/journal');
});

test('a section of the page is addressed by its query', () => {
  assert.equal(journalViewUrl('equity-curve', 'trades', 'all'), '/journal?tab=equity-curve');
  assert.equal(journalViewUrl('paper-trading', 'trades', 'all'), '/journal?tab=paper-trading');
});

test('the page path and the tab parameter are never both written', () => {
  // `/journal/unified?tab=unified` states the view twice; the day the two
  // halves disagree is the bug this whole file exists to prevent.
  assert.equal(journalViewUrl('unified', 'trades', 'all'), '/journal/unified');
  assert.equal(journalQueryString('unified', 'trades', 'all'), '');
});

test('the unified page carries its own scope', () => {
  assert.equal(journalViewUrl('unified', 'trades', 'research'), '/journal/unified?source=research');
  assert.equal(journalViewUrl('unified', 'trades', 'paper'), '/journal/unified?source=paper');
});

test('a scope control the view ignores is not written', () => {
  // `source` only selects a leg of the unified view. Writing it anywhere else
  // puts a parameter in the address that changes nothing — the same misleading
  // control this round removed from the panel.
  for (const tab of TAB_KEYS.filter((key) => key !== UNIFIED_TAB)) {
    for (const source of ['research', 'paper']) {
      assert.ok(
        !journalViewUrl(tab, 'trades', source).includes('source='),
        `${tab} carried an inert source=: ${journalViewUrl(tab, 'trades', source)}`,
      );
    }
  }
});

test('whatever the address states can be read back', () => {
  for (const tab of TAB_KEYS) {
    for (const source of JOURNAL_SOURCES) {
      const url = journalViewUrl(tab, 'trades', source);
      const { pathname, searchParams } = new URL(`http://localhost${url}`);
      const seeded = pathname === journalPagePath('unified') ? UNIFIED_TAB : 'trades';
      const readTab = readJournalTab(searchParams.get('tab'), TAB_KEYS, seeded);
      const readSource = readJournalSource(searchParams.get('source'));
      assert.equal(readTab, tab, `${url} does not reopen ${tab}`);
      // Only the unified view has a scope, so only its address carries one. On
      // the other tabs the omission is the earlier gate working: a parameter
      // that changes nothing is left out, and reading back the default is the
      // honest answer rather than a loss. Assert the two cases apart instead of
      // demanding one equality that the design refuses to satisfy.
      if (tab === UNIFIED_TAB) {
        assert.equal(readSource, source, `${url} does not reopen scope ${source}`);
      } else {
        assert.ok(
          !url.includes('source='),
          `${url} states a scope the ${tab} view ignores`,
        );
        assert.equal(readSource, DEFAULT_JOURNAL_SOURCE, `${url} read back a scope`);
      }
    }
  }
});

// --- page addresses and API addresses are not the same string ---------------

test('the page address is not an API address', () => {
  assert.equal(JOURNAL_PAGE_PATH, '/journal');
  assert.ok(!JOURNAL_PAGE_PATH.startsWith('/api/'), 'the page path became an API path');
  const url = journalViewUrl('unified', 'trades', 'research');
  assert.ok(!url.includes('/api/'), `the view address carried an API path: ${url}`);
  // The unified data route keeps the same word in a different namespace.
  const client = src('../src/lib/api.ts');
  assert.ok(client.includes('/api/v1/journal/unified?'), 'the unified data route moved');
});

// --- the panel actually uses the contract -----------------------------------

test('the panel reads the address and writes it on change', () => {
  const panel = panelSource();

  assert.match(panel, /const searchParams = useSearchParams\(\);/);
  assert.match(
    panel,
    /useState<TabKey>\(\(\) =>\s*readJournalTab\(searchParams\.get\('tab'\), TAB_KEYS, initialTab \?\? DEFAULT_TAB\)/,
    'the tab is not seeded from the address',
  );
  assert.match(
    panel,
    /useState<JournalSource>\(\(\) =>\s*readJournalSource\(searchParams\.get\('source'\)\)/,
    'the ledger scope is not seeded from the address',
  );
  assert.match(panel, /journalViewUrl\(activeTab, DEFAULT_TAB, unifiedSource\)/, 'the view url is built elsewhere');
  assert.match(panel, /router\.replace\(next, \{ scroll: false \}\)/, 'the address never updates');
  // Rewriting an address that already matches is a navigation, and doing it on
  // mount would kick a reader off the link they just opened.
  assert.match(panel, /if \(next !== current\)/, 'the panel re-navigates on every render');
});

test('the tab list the address is validated against is the tab list on screen', () => {
  const panel = panelSource();

  // A second, hand-written list of tab names is a second truth: add a tab to
  // TABS, forget it here, and its address silently falls back to Trades.
  assert.match(panel, /const TAB_KEYS: TabKey\[\] = TABS\.map\(\(tab\) => tab\.key\);/);
  assert.match(panel, /readJournalTab\(searchParams\.get\('tab'\), TAB_KEYS/, 'validation uses another list');
});

test('both journal routes mount the same panel behind a Suspense boundary', () => {
  // One tab bar for every view: a second page with its own navigation is where
  // `/journal` and `/journal/unified` would start disagreeing about what exists.
  for (const [rel, initialTab] of [
    ['src/app/journal/page.tsx', null],
    ['src/app/journal/unified/page.tsx', 'unified'],
  ]) {
    const page = readAt(rel);
    assert.match(page, /import \{ Suspense \} from 'react';/, `${rel} has no boundary`);
    assert.match(page, /<Suspense[\s\S]*<JournalDashboard[\s\S]*<\/Suspense>/, `${rel} is not wrapped`);
    assert.match(page, /import JournalDashboard from '@\/components\/journal\/JournalDashboard';/);
    if (initialTab) {
      assert.match(page, new RegExp(`initialTab="${initialTab}"`), `${rel} does not select ${initialTab}`);
    } else {
      assert.ok(!/initialTab=/.test(page), `${rel} pins a tab it should read from the query`);
    }
  }
});

// --- no invented destinations ----------------------------------------------

function walk(dir, out = []) {
  for (const name of readdirSync(new URL(dir, ROOT))) {
    const full = `${dir}/${name}`;
    if (statSync(new URL(full, ROOT)).isDirectory()) walk(full, out);
    else if (/\.(tsx|ts)$/.test(name) && !full.includes('/tests/')) out.push(full);
  }
  return out;
}

function pageRoutes() {
  const routes = [];
  const stack = ['src/app'];
  while (stack.length) {
    const dir = stack.pop();
    for (const name of readdirSync(new URL(dir, ROOT))) {
      const full = `${dir}/${name}`;
      if (statSync(new URL(full, ROOT)).isDirectory()) stack.push(full);
      else if (name === 'page.tsx' || name === 'page.ts') {
        const rel = dir.slice('src/app'.length);
        routes.push(rel === '' ? '/' : rel);
      }
    }
  }
  return routes;
}

function matchesRoute(href, routes) {
  const parts = href.split('/');
  return routes.some((route) => {
    const routeParts = route.split('/');
    if (routeParts.length !== parts.length) return false;
    return routeParts.every((seg, i) => seg === parts[i] || /^\[.+\]$/.test(seg));
  });
}

test('every literal internal link points at a route that exists', () => {
  const routes = pageRoutes();
  assert.ok(routes.length > 10, `route discovery found ${routes.length} pages, which is vacuous`);
  assert.ok(routes.includes('/journal'), 'the journal route is not where the contract says');
  // Without this the sweep could pass on a route set that never contained the
  // page this contract is about.
  assert.ok(routes.includes('/journal/unified'), 'the unified route the sweep must be able to see');

  const offenders = [];
  let seen = 0;
  const pattern =
    /(?:onNavigate|router\.push|router\.replace)\(\s*['"]([^'"$]*?)['"]|href=['"]([^'"$]*?)['"]/g;

  for (const file of walk('src')) {
    const text = readAt(file);
    for (const match of text.matchAll(pattern)) {
      const raw = match[1] ?? match[2];
      if (!raw.startsWith('/') || raw.startsWith('//')) continue;
      if (raw.startsWith('/api/')) continue;
      const path = raw.split('?')[0].split('#')[0];
      if (path === '') continue;
      seen += 1;
      if (!matchesRoute(path, routes)) offenders.push(`${file} -> ${raw}`);
    }
  }

  assert.ok(seen > 0, 'the sweep matched no link at all, so it proves nothing');
  assert.deepEqual(offenders, [], `links to routes that do not exist:\n${offenders.join('\n')}`);
});

test('the empty-state CTA reaches the page that hosts the Strategy lab', () => {
  const panel = panelSource();

  assert.ok(!panel.includes("'/strategy-lab'"), 'the dead route is back');
  assert.match(panel, /const STRATEGY_LAB_PAGE_PATH = '\/market-data\/crypto';/);
  assert.match(panel, /onNavigate\(STRATEGY_LAB_PAGE_PATH\)/);
  // The destination has to host the panel the button names, or the link is only
  // less broken than a 404.
  const host = readAt('src/app/market-data/crypto/page.tsx');
  assert.match(host, /import StrategyLabPanel from '@\/components\/market\/StrategyLabPanel';/);
  assert.match(host, /<StrategyLabPanel/);
});
