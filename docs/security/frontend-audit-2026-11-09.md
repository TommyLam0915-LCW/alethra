# Frontend Security Audit 2026-11-09

## Audit Context

- **Date**: 2026-11-09
- **Next.js**: 14.2.18 (pinned)
- **eslint-config-next**: 14.2.18 (freshly installed)
- **Node.js**: v24.18.0
- **package-lock.json**: frontend/package-lock.json (freshly generated)
- **Command**: `cd frontend && npm audit --json`
- **Exit code**: 0 (audit ran successfully; vulnerabilities reported)

## Summary

| Severity | Advisory Count | Affected Packages |
|----------|---------------|-------------------|
| Critical | 1 (next, 25 sub-advisories) | next |
| High | 4 | postcss, glob, @next/eslint-plugin-next, eslint-config-next |
| Moderate | 6 | minimatch, @typescript-eslint/{parser,eslint-plugin,type-utils,typescript-estree,utils} |
| **Total** | **11** | **8 direct + transitive** |

All 11 advisories require semver-major upgrades to resolve:
- next → 16.4.5 (major: 14 → 16)
- eslint-config-next → 16.4.5 (major: 14 → 16)

**This round does NOT upgrade Next.js per owner constraint.**

## Detailed Breakdown

### Critical: next (25 sub-advisories)

**Installed**: 14.2.18
**Affected range**: 0.9.9 – 16.3.0-preview.10
**Fix version**: 16.4.5 (semver major)

Sub-advisory categories:
- **DoS via Server Actions/Components** (8 advisories): Requires app to use Server Actions or Server Components. This project uses Pages Router with API routes; Server Actions are not used.
- **SSRF via Middleware/Image Optimization** (4 advisories): Requires public-facing deployment with image optimization or middleware proxy. This project binds to 127.0.0.1 only.
- **Cache poisoning** (4 advisories): Requires multi-tenant or public caching. Not applicable to single-user local deployment.
- **RCE via Image Optimization** (3 advisories): Requires user-uploaded images processed by Next.js image optimizer. Not used in this project.
- **XSS** (2 advisories): Requires untrusted user input in specific patterns. Code review would be needed to verify.
- **Auth bypass via Middleware** (2 advisories): This project does not use Next.js middleware for auth.
- **Info disclosure in dev server** (1 advisory): GHSA-3h52-269p-cp9r — dev server lacks origin verification. Mitigated by 127.0.0.1 binding.
- **HTTP smuggling in rewrites** (1 advisory): Requires configured rewrites to external hosts. Not configured.

**Applicability assessment**: Most critical advisories target features not used by this project (Server Actions, Image Optimization with remote patterns, middleware auth). The 127.0.0.1 binding further limits exposure. However, this assessment is based on code structure review, not penetration testing.

### High: postcss

**Installed**: via next 14.2.18 dependency
**Affected range**: ≤8.5.22
**Fix**: Requires next 16.4.5

### High: glob

**Installed**: via @next/eslint-plugin-next → glob transitive chain
**Affected range**: 9.0.0 – 14.0.1
**Advisories**:
- GHSA-5j98-mcp5-4vw2: CLI command injection via -c/--cmd
- GHSA-3g3m-2fjr-6pw4: AbortSignal cancellation bypass
**Fix**: Requires eslint-config-next 16.4.5
**Applicability**: glob CLI injection requires command-line usage of glob with -c flag. This project uses glob as a library dependency, not CLI.

### High: @next/eslint-plugin-next + eslint-config-next

**Affected range**: 14.0.5-canary.0 – 15.0.0-rc.1
**Fix**: Requires eslint-config-next 16.4.5 (semver major)
**Root cause**: Transitive dependency on vulnerable glob

### Moderate: minimatch + @typescript-eslint chain (6 advisories)

**minimatch affected range**: 7.0.0 – 11.0.0
**Advisory**: GHSA-g3hv-75wg-m249 — DoS via globparts amplification
**Chain**: minimatch → @typescript-eslint/typescript-estree → parser/plugin/type-utils/utils → eslint-config-next
**Fix**: Requires eslint-config-next 16.4.5
**Applicability**: globparts amplification requires processing untrusted glob patterns. ESLint processes developer-authored patterns at build time, not user input.

## Exploitability Context

| Factor | Status | Impact |
|--------|--------|--------|
| Deployment | 127.0.0.1 only | Eliminates remote exploitation for network-facing advisories |
| User base | Single user (owner) | No multi-tenant risk |
| Server Actions | Not used | 8 DoS advisories not applicable |
| Image Optimization (remote) | Not configured | 3 RCE advisories not applicable |
| Middleware auth | Not used | 2 auth bypass advisories not applicable |
| Dev server origin check | Not verified | GHSA-3h52-269p-cp9r partially mitigated by loopback binding |
| glob CLI | Not used as CLI | Command injection not applicable |
| minimatch input | Developer patterns only | DoS amplification not applicable |

## Next Round Remediation Plan

**Minimum fix** (addresses all 11 advisories):
1. Upgrade next: 14.2.18 → 16.4.5+ (requires React 19 compatibility verification)
2. Upgrade eslint-config-next: 14.2.18 → 16.4.5+ (may require ESLint 9 migration)

**Prerequisite verification before upgrade**:
- React 18 → 19 migration impact on existing components
- ESLint 8 → 9 flat config migration (all .eslintrc files must be converted)
- Breaking changes in Next.js 15 and 16 changelogs
- Test full build + browser acceptance after upgrade

**Risk**: Major version upgrades may introduce breaking changes requiring significant refactoring. This should be a dedicated upgrade round, not bundled with feature work.

## Audit Integrity

- Raw `npm audit --json` output was parsed programmatically
- Advisory IDs cross-referenced with GitHub Advisory Database
- Version ranges verified against installed package.json
- No `npm audit fix` or `--force` was executed
- package-lock.json was not modified by this audit
