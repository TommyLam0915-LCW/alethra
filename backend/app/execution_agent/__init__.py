"""IBKR Execution Agent — User-side broker integration.

ARCHITECTURE
============

Alethra does NOT connect directly to Interactive Brokers. Instead, users run
this execution agent on their local machine::

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
1. Connects to local IB Gateway/TWS via a pluggable BrokerTransport
2. Polls Alethra platform for authorized order intents
3. Submits orders to the broker
4. Reports fills back to Alethra platform
5. Handles reconnection, errors, and partial fills

COMPONENTS
==========

- ``broker_transport`` — Abstract transport + FakeBrokerTransport for tests
- ``ibkr_client`` — Broker-facing client (wraps transport)
- ``alethra_client`` — Platform-facing HTTP client
- ``agent`` — Main control loop with safety invariants
- ``config`` — Configuration from environment variables

SECURITY
========

- Agent authenticates to Alethra platform via Bearer token (JWT or API key)
- Agent does NOT store IBKR account credentials
- Agent connects to IB Gateway on localhost only
- All communication with Alethra platform is HTTPS

SAFETY INVARIANTS
=================

- Never resubmits an order if the submission acknowledgement was lost
- Tracks partial fills and reports each individually
- Reconciles in-flight orders on startup after disconnect/restart
- Skips expired intents
"""

IMPLEMENTATION_STATUS = "IMPLEMENTED — broker transport abstraction with fake transport for offline testing"
