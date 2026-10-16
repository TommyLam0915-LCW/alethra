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
1. Connects to local IB Gateway/TWS via broker transport
2. Polls Alethra platform for authorized order intents
3. Submits orders to broker
4. Reports fills back to Alethra platform
5. Handles reconnection, errors, and partial fills

TRANSPORT ABSTRACTION
=====================

The agent uses two transport protocols:
- BrokerTransport: broker operations (connect, submit, cancel, fills)
- PlatformTransport: platform API operations (poll intents, report fills)

Fake implementations exist for offline testing:
- FakeBrokerTransport: configurable behavior (fills, rejections, disconnects)
- FakePlatformTransport: tracks all interactions for assertions

Production implementations (BLOCKED on external prerequisites):
- IBKRTransport: wraps ib_insync → IB Gateway (BLOCKED on IBKR account)
- HTTPPlatformTransport: HTTPS → Alethra API (BLOCKED on deployment)

SAFETY
======

- Live trading mode DISABLED by default. Requires ALETHRA_LIVE_TRADING_ENABLED=true.
- Kill switch checked before every processing cycle.
- Unknown execution-critical metadata blocks submission.
- Expired intents are rejected.
- Duplicate intents (same idempotency key) are skipped.

STATUS
======

Agent control flow: IMPLEMENTED — OFFLINE VERIFIED (via fake transports)
Real IBKR transport: BLOCKED on IBKR account and IB Gateway installation
Real platform transport: BLOCKED on Alethra platform deployment
"""

IMPLEMENTATION_STATUS = "IMPLEMENTED — OFFLINE VERIFIED (fake transports)"
BLOCKED_ON = "IBKR account + IB Gateway (real broker transport)"
