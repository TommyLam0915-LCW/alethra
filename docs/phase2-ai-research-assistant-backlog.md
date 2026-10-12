# Phase 2: AI Research Assistant — Backlog

**Status**: Not scheduled
**Created**: 2026-10-12
**Priority**: Future expansion — requires independent research and validation

---

## Overview

Phase 2 was deferred during initial development to focus on the core research methodology engine (backtesting, simulation, paper portfolio, model research, export, pilot validation). This backlog captures the intended Phase 2 scope for future implementation.

---

## Planned Features

### 2.1 AI Clarification Assistant (AI 澄清助手)

**Goal**: Help users refine research hypotheses and strategy assumptions through structured dialogue.

**Requirements**:
- Conversational interface integrated with experiment context
- Access to strategy_spec, backtest results, and simulation decisions
- Must NOT provide investment advice or trading recommendations
- Must cite data sources and methodology when responding
- Bilingual support (English + Traditional Chinese)
- User consent required for any conversation logging

**Safety constraints**:
- AI output must be clearly labeled as "research assistance" not "investment advice"
- Mandatory disclaimer on every AI response
- No access to live market data or broker APIs
- No ability to execute trades or modify strategies directly

**Technical prerequisites**:
- AI provider integration (configurable: OpenAI, Anthropic, local models)
- Context window management for experiment data
- Conversation history storage with privacy controls
- Rate limiting and cost controls

---

### 2.2 Strategy Visualization Improvements (策略視覺化改善)

**Goal**: Better visual representation of strategy performance and decision history.

**Requirements**:
- Equity curve charts with benchmark overlay
- Trade markers on price charts (entry/exit points)
- Drawdown visualization
- Decision timeline with methodology status transitions
- Export charts as PNG/SVG for research papers

**Technical prerequisites**:
- Frontend charting library (e.g., Recharts, D3.js)
- Time-series data API endpoints
- Chart configuration persistence

---

### 2.3 Multi-Asset Portfolio Support

**Goal**: Extend backtesting and simulation to multi-asset strategies.

**Requirements**:
- Portfolio-level position tracking
- Cross-asset correlation analysis
- Rebalancing strategy templates
- Multi-asset benchmark construction

**Technical prerequisites**:
- Extended data model for multi-asset OHLCV
- Portfolio optimization engine
- Additional demo data fixtures

---

### 2.4 Walk-Forward Optimization

**Goal**: Support walk-forward analysis for strategy robustness testing.

**Requirements**:
- Configurable in-sample / out-of-sample windows
- Rolling window optimization
- Overfitting detection metrics
- Walk-forward equity curve construction

**Safety constraints**:
- Must clearly label results as "in-sample" vs "out-of-sample"
- Must warn about overfitting risk
- Must not be presented as predictive of future performance

---

## Implementation Notes

- Each feature should be implemented on its own feature branch
- Each feature requires its own validation and testing cycle
- AI integration requires separate security review
- No feature should compromise the core methodology invariants (strategy locking, benchmark consistency, audit trail)
- Phase 2 features must maintain the bilingual (English + Traditional Chinese) interface standard

---

## Dependencies

- Phase 2 AI features depend on selecting and validating an AI provider
- Walk-forward optimization depends on multi-asset support
- All Phase 2 features assume Phase 1–6 remain stable and unchanged
