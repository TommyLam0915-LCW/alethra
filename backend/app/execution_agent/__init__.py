"""IBKR Execution Agent — User-side broker integration.

ARCHITECTURE
============

Alethra does NOT connect directly to Interactive Brokers. Instead, users run
this execution agent on their local machine:

    User's Machine
    ┌─────────────────────────────────────────┐
    │  IB Gateway / TWS                       │
    │  (port 7496 live, 7497 paper)           │
    │       ↓                                 │
    │  Alethra Execution Agent (this module)  │
    │       ↓                                 │
    │  HTTPS → Alethra Platform API           │
    └─────────────────────────────────────────┘

The agent:
1. Connects to local IB Gateway/TWS via `ib_insync` library
2. Polls Alethra platform for authorized order intents
3. Submits orders to IBKR
4. Reports fills back to Alethra platform
5. Handles reconnection, errors, and partial fills

SECURITY
========

- Agent authenticates to Alethra platform via API key (user-specific)
- Agent does NOT store IBKR account credentials
- Agent connects to IB Gateway on localhost only (no remote connections)
- All communication with Alethra platform is HTTPS

STATUS
======

**NOT IMPLEMENTED** — BLOCKED on user IBKR account and IB Gateway installation.

This module defines the architecture and interfaces. Implementation requires:
1. User creates IBKR paper trading account
2. User installs IB Gateway or TWS
3. User enables API access in IB Gateway settings
4. User provides IB Gateway port to agent configuration

See docs/external-prerequisites-checklist.md for full details.
"""

# Status marker for external dependency tracking
IMPLEMENTATION_STATUS = "NOT IMPLEMENTED — BLOCKED on IBKR account setup"
