# Frontend Security Audit 2026-11-09 (Corrected)

## Audit Context

- **Date**: 2026-11-09
- **Next.js**: 14.2.18 (installed) → 14.2.35 (latest stable 14.x, required)
- **eslint-config-next**: 14.2.18 (installed) → 14.2.35 (latest stable 14.x)
- **Node.js**: v26.5.0 (runtime), npm 11.17.0
- **package-lock.json**: frontend/package-lock.json
  - SHA-256: `6f1f919f188a0e5d18f0047e997d67d6e74c210629fbdfbbb8aa9dc239847e14`
- **Command**: `cd frontend && npm audit --json`
- **Exit code**: 1 (vulnerabilities reported)

## Critical Correction: App Router, Not Pages Router

**Previous error**: The initial audit incorrectly stated "Pages Router with API routes."

**Actual architecture**:
- **App Router** (`src/app/` directory structure)
- **33 page routes** (all `page.tsx` files, no `route.ts` API routes in frontend)
- **No Server Actions** (grep for `'use server'` returned 0 results)
- **No middleware files** (no `middleware.ts` at project root or in `src/`)
- **No next/image usage** (grep for `next/image` imports returned 0 results)
- **No rewrites/redirects** configured in `next.config.js`
- **Frontend binds to 127.0.0.1** (`next dev -H 127.0.0.1` in package.json)
- **Backend API**: separate service at `http://localhost:8000` (configured via `NEXT_PUBLIC_API_URL`)

## Summary: 41 Unique Advisories Across 11 Packages

| Severity | Unique Advisories | Affected Packages |
|----------|------------------|-------------------|
| Critical | 4 | next |
| High | 15 | next, postcss, glob, @next/eslint-plugin-next, eslint-config-next |
| Moderate | 18 | next, minimatch, @typescript-eslint/* |
| Low | 4 | next |
| **Total** | **41** | **11 packages** |

**Important**: npm audit reports 11 vulnerable packages, but these map to **41 unique advisory URLs** (GHSA IDs). The previous document conflated package count with advisory count.

## Dependency Classification

### Runtime Dependencies (5 packages)
- `next`: 14.2.18 → **14.2.35 available** (non-major upgrade)
- `react`: ^18.3.1
- `react-dom`: ^18.3.1
- `lightweight-charts`: ^5.2.1
- `react-resizable-panels`: ^2.1.7

### Dev Dependencies (affected subset)
- `eslint`: ^8.57.1
- `eslint-config-next`: ^14.2.18 → **14.2.35 available** (non-major upgrade)
- `typescript`: ^5.6.3

## Advisory Breakdown by Feature Affected

### Category 1: Server Actions / Server Components (20 advisories)

**Affected advisories**:
- GHSA-7m27-7ghc-44w9: DoS with Server Actions (moderate)
- GHSA-mwv6-3258-q52c: DoS with Server Components (high)
- GHSA-5j59-xgg2-r9c4: DoS with Server Components - Incomplete Fix (high)
- GHSA-h25m-26qc-wcjf: HTTP request deserialization DoS (high)
- GHSA-q4gf-8mx6-v5v3: DoS with Server Components (high)
- GHSA-8h8q-6873-q5fj: DoS with Server Components (high)
- GHSA-m99w-x7hq-7vfj: DoS in App Router using Server Actions (high)
- GHSA-89xv-2m56-2m9x: SSRF in Server Actions on custom servers (high)
- GHSA-p3m5-59mp-jv5r: DoS in App Router using Server Actions (high)
- GHSA-wfc6-r584-vfw7: Cache poisoning in RSC responses (moderate)
- GHSA-vfv6-92ff-j949: Cache poisoning via RSC cache-busting (low)
- GHSA-68g3-v927-f742: Cache confusion for requests with bodies (moderate)
- GHSA-4633-3j49-mh5q: Cache confusion with invalid UTF-8 (moderate)
- GHSA-4c39-4ccg-62r3: Unbounded Server Action payload in Edge (moderate)
- GHSA-955p-x3mx-jcvp: Disclosure of internal Server Function endpoints (moderate)
- GHSA-ffhc-5mcf-pf4q: XSS in App Router with CSP nonces (moderate)
- GHSA-gx5p-jg67-6x7h: XSS in beforeInteractive scripts (moderate)
- **CVE-2025-66478 / GHSA-9qr9-h5gf-34mp**: React2Shell RCE (critical) — **NOT AFFECTED**
- **CVE-2025-55184 / GHSA-mwv6-3258-q52c**: DoS (high) — **AFFECTED, fixed in 14.2.35**
- **CVE-2025-55183**: Source code exposure (medium) — **AFFECTED, fixed in 14.2.35**

**Project impact**: 
- **Server Actions NOT used** (verified: 0 files with `'use server'`)
- **React2Shell (CVE-2025-66478) does NOT affect Next.js 14.x stable** — only affects 15.x, 16.x, and 14.3.0-canary.77+
- **But**: CVE-2025-55184 (DoS) and CVE-2025-55183 (source code exposure) DO affect 14.x and are fixed in 14.2.35
- Most other Server Actions/Components advisories require the feature to be in use

### Category 2: Image Optimization (7 advisories)

**Affected advisories**:
- GHSA-g5qg-72qw-gw5v: Cache key confusion for Image Optimization API (moderate)
- GHSA-xv57-4mr9-wg8v: Content injection for Image Optimization (moderate)
- GHSA-9g9p-9gw9-jx7f: DoS via Image Optimizer remotePatterns (moderate)
- GHSA-3x4c-7xq6-9pq8: Unbounded next/image disk cache growth (moderate)
- GHSA-h64f-5h5j-jqjh: DoS in Image Optimization API (moderate)
- GHSA-2xp9-vwfh-vxw4: RCE in Image Optimization with AVIF (critical)
- GHSA-2464-fhr4-w9xw: RCE in Image Optimization with SVG (critical)

**Project impact**:
- **next/image NOT used** (verified: 0 imports of `next/image`)
- **No remote image patterns configured** (verified: `next.config.js` has no `images` config)
- These advisories require image optimization to be in use

### Category 3: Middleware (4 advisories)

**Affected advisories**:
- GHSA-4342-x723-ch2f: Improper redirect handling leads to SSRF (moderate)
- GHSA-f82v-jwr5-mffw: Authorization bypass in middleware (critical)
- GHSA-3g8h-86w9-wvmq: Middleware/proxy redirects cache poisoning (low)
- GHSA-36qx-fr4f-26g5: Middleware/proxy bypass in Pages Router with i18n (high)

**Project impact**:
- **No middleware files** (verified: no `middleware.ts` at root or in `src/`)
- GHSA-36qx-fr4f-26g5 specifically targets Pages Router with i18n — **we use App Router**

### Category 4: Rewrites / HTTP Smuggling (2 advisories)

**Affected advisories**:
- GHSA-ggv3-7p47-pfv8: HTTP request smuggling in rewrites (moderate)
- GHSA-p9j2-gv94-2wf4: SSRF in rewrites via attacker-controlled hostname (high)

**Project impact**:
- **No rewrites configured** (verified: `next.config.js` has no `rewrites` or `redirects`)

### Category 5: Dev Server (1 advisory)

**Affected advisory**:
- GHSA-3h52-269p-cp9r: Information exposure due to lack of origin verification (low)

**Project impact**:
- **Mitigated by 127.0.0.1 binding** — dev server only accessible from localhost
- **Not an absolute elimination** — if a user visits a malicious page while dev server is running, DNS rebinding attacks could still reach it
- **Recommendation**: This is a dev-only issue; production deployment would use a reverse proxy with proper origin checks

### Category 6: Cache Poisoning (2 advisories)

**Affected advisories**:
- GHSA-qpjv-v59x-3qc4: Race condition to cache poisoning (low)
- GHSA-3g8h-86w9-wvmq: Middleware/proxy redirects cache poisoning (low)

**Project impact**:
- Requires specific caching configurations not present in this project

### Category 7: Windows-Specific RCE (1 advisory)

**Affected advisory**:
- GHSA-p293-qw3h-jr36: Unauthenticated RCE on Windows-hosted servers (critical)

**Project impact**:
- **Not applicable** — this project runs on macOS/Linux (verified: `uname` returns `Darwin`)

### Category 8: WebSocket SSRF (1 advisory)

**Affected advisory**:
- GHSA-c4j6-fc7j-m34r: SSRF in applications using WebSocket upgrades (high)

**Project impact**:
- **No WebSocket usage** (verified: no `ws` or WebSocket imports in source)

### Category 9: Transitive Dependencies — postcss (4 advisories)

**Affected advisories**:
- GHSA-qx2v-qp2m-jg93: XSS via unescaped `</style>` in CSS stringify (high)
- GHSA-6g55-p6wh-862q: Arbitrary file read via sourceMappingURL (high)
- GHSA-fxqj-rqcc-2cmp: Incomplete fix for sourceMappingURL (high)
- GHSA-r28c-9q8g-f849: Path traversal in source map auto-loading (high)

**Project impact**:
- postcss is a transitive dependency of Next.js
- These require processing untrusted CSS with sourceMappingURL comments
- **Not applicable to this project** — CSS is authored by developers, not user-supplied
- **Fix**: Requires Next.js 16.x (postcss 8.5.32+ patched)

### Category 10: Transitive Dependencies — glob (2 advisories)

**Affected advisories**:
- GHSA-5j98-mcp5-4vw2: CLI command injection via -c/--cmd (high)
- GHSA-3g3m-2fjr-6pw4: AbortSignal cancellation bypass (high)

**Project impact**:
- glob is a transitive dependency via @next/eslint-plugin-next
- **CLI injection not applicable** — glob is used as a library, not invoked with `-c` flag
- AbortSignal bypass requires specific async patterns not present in ESLint usage
- **Fix**: Requires eslint-config-next 16.x

### Category 11: Transitive Dependencies — minimatch (1 advisory)

**Affected advisory**:
- GHSA-g3hv-75wg-m249: DoS via globparts amplification (moderate)

**Project impact**:
- minimatch is a transitive dependency via @typescript-eslint/*
- Requires processing untrusted glob patterns
- **Not applicable** — ESLint processes developer-authored patterns at build time
- **Fix**: Requires eslint-config-next 16.x

## Exploitability Assessment

### NOT AFFECTED (13 advisories)

| Advisory | Reason |
|----------|--------|
| CVE-2025-66478 (React2Shell RCE) | Next.js 14.x stable not affected |
| GHSA-p293-qw3h-jr36 (Windows RCE) | Project runs on macOS/Linux |
| GHSA-c4j6-fc7j-m34r (WebSocket SSRF) | No WebSocket usage |
| GHSA-36qx-fr4f-26g5 (Pages Router i18n bypass) | Uses App Router, not Pages Router |
| 7 Image Optimization advisories | next/image not used |
| 2 Rewrite/HTTP smuggling advisories | No rewrites configured |
| GHSA-5j98-mcp5-4vw2 (glob CLI injection) | glob used as library, not CLI |

### AFFECTED — FIX AVAILABLE IN 14.2.35 (2 advisories)

| Advisory | Severity | Description |
|----------|----------|-------------|
| CVE-2025-55184 / GHSA-mwv6-3258-q52c | High | DoS via infinite loop in RSC deserialization |
| CVE-2025-55183 | Medium | Source code exposure via Server Function requests |

**Action required**: Upgrade `next` from 14.2.18 → **14.2.35** (non-major, patch upgrade)

### AFFECTED — REQUIRES MAJOR UPGRADE (26 advisories)

These advisories are not patched in the 14.x line and require Next.js 16.x or eslint-config-next 16.x:

- 4 postcss advisories (transitive via next)
- 2 glob advisories (transitive via eslint-config-next)
- 1 minimatch advisory (transitive via @typescript-eslint/*)
- 19 next advisories (various Server Actions/Components, cache poisoning, middleware issues)

**However**: Most of these require features not used by this project:
- Server Actions (not used)
- Middleware (not used)
- Image Optimization (not used)
- Rewrites (not configured)

### PARTIALLY MITIGATED (1 advisory)

| Advisory | Mitigation | Residual Risk |
|----------|------------|---------------|
| GHSA-3h52-269p-cp9r (dev server origin check) | 127.0.0.1 binding | DNS rebinding if user visits malicious page while dev server runs |

## Minimal Viable Fix Path

### Option A: Patch Upgrade (RECOMMENDED)

**Upgrade**: `next` 14.2.18 → 14.2.35, `eslint-config-next` 14.2.18 → 14.2.35

**Resolves**: 2 advisories (CVE-2025-55184, CVE-2025-55183)

**Risk**: Low — patch upgrade within 14.x line

**Command**:
```bash
cd frontend
npm install next@14.2.35 eslint-config-next@14.2.35
```

**Remaining advisories**: 39 (but most require features not used)

### Option B: Major Upgrade (NOT RECOMMENDED THIS ROUND)

**Upgrade**: `next` 14.2.18 → 16.4.5, `eslint-config-next` 14.2.18 → 16.4.5

**Resolves**: All 41 advisories

**Risk**: High — requires:
- React 18 → 19 migration
- ESLint 8 → 9 flat config migration
- Potential breaking changes in Next.js 15 and 16
- Full regression testing

**Prerequisite**: Dedicated upgrade round, not bundled with feature work

### Option C: Accept Risk (DEFENSIBLE)

**Rationale**: 
- 13 advisories: not affected (features not used)
- 2 advisories: fixable with patch upgrade (Option A)
- 26 advisories: require major upgrade, but most target unused features
- 1 advisory: partially mitigated by loopback binding

**Remaining exposure**:
- If Server Actions are added in the future, 19 advisories become exploitable
- If next/image with remote patterns is added, 7 advisories become exploitable
- If middleware is added, 4 advisories become exploitable

**Recommendation**: Implement Option A (patch upgrade) to fix the 2 critical/high advisories that DO affect this project. Defer Option B (major upgrade) to a dedicated round.

## Attack Surface Inventory

### Verified Absent Features

| Feature | Verification Method | Result |
|---------|-------------------|--------|
| Server Actions | `grep -r "'use server'" frontend/src/` | 0 files |
| Middleware | `find frontend/ -name "middleware.{ts,tsx,js,jsx}"` | 0 files |
| next/image | `grep -r "next/image" frontend/src/` | 0 imports |
| API routes (frontend) | `find frontend/src/app -name "route.{ts,tsx}"` | 0 files |
| Rewrites/redirects | Read `frontend/next.config.js` | Not configured |
| WebSocket | `grep -r "WebSocket\|'ws'" frontend/src/` | 0 imports |
| Remote image patterns | Read `frontend/next.config.js` | No `images` config |

### Present Features

| Feature | Verification | Count |
|---------|-------------|-------|
| App Router pages | `find frontend/src/app -name "page.tsx"` | 33 pages |
| Client components | Implicit (React 18 SPA) | All pages |
| Backend API calls | `NEXT_PUBLIC_API_URL` in next.config.js | 1 endpoint |
| Loopback binding | `"dev": "next dev -H 127.0.0.1"` in package.json | Confirmed |

## Audit Integrity

- Raw `npm audit --json` output parsed programmatically
- 41 unique advisory URLs extracted and categorized
- Advisory details verified against official Next.js security blog posts:
  - [CVE-2025-66478](https://nextjs.org/blog/CVE-2025-66478) (December 3, 2025)
  - [Security Update December 11, 2025](https://nextjs.org/blog/security-update-2025-12-11)
- Version availability verified via `npm info next versions`
- Attack surface verified by grep/find on actual source code
- No `npm audit fix` or `--force` was executed
- package-lock.json was not modified by this audit

## Correction Log

### Errors in Previous Document (2026-11-09 initial)

1. **WRONG**: "Pages Router with API routes"  
   **CORRECT**: App Router (`src/app/`), no frontend API routes

2. **WRONG**: "127.0.0.1 binding eliminates remote exploitation"  
   **CORRECT**: Mitigates but does not eliminate (DNS rebinding still possible in dev)

3. **WRONG**: "11 advisories"  
   **CORRECT**: 11 vulnerable packages, but 41 unique advisory URLs

4. **WRONG**: "All 11 advisories require semver-major upgrades"  
   **CORRECT**: 2 advisories (CVE-2025-55184, CVE-2025-55183) are fixed in 14.2.35 (patch upgrade)

5. **WRONG**: "Server Actions not used" (stated without verification)  
   **CORRECT**: Verified by grep — 0 files contain `'use server'`

6. **WRONG**: Node.js v24.18.0  
   **CORRECT**: Node.js v26.5.0 (verified at audit time)

7. **MISSING**: CVE-2025-66478 (React2Shell) — critical RCE affecting Next.js 15.x/16.x  
   **CORRECT**: Next.js 14.x stable NOT affected (only 14.3.0-canary.77+ affected)

8. **WRONG**: Advisory categorization by package  
   **CORRECT**: Categorized by feature/attack vector with exploitability assessment

## Next Steps

1. **Immediate**: Upgrade to Next.js 14.2.35 (fixes 2 high/medium advisories)
2. **Short-term**: Monitor Next.js 14.x EOL status — no future patches expected
3. **Long-term**: Plan dedicated Next.js 16.x upgrade round with full regression testing
4. **If features added**: Re-evaluate advisories if Server Actions, middleware, or next/image are introduced

---

**Audit completed**: 2026-11-09  
**Auditor**: Agent (automated)  
**Review status**: Owner review pending  
**Action status**: Option A (patch upgrade) recommended
