"""Who performed an action, judged only by the credential the server verified.

The label is derived from the authentication channel, never from the request
body, a user-agent header, or the wording of a rationale. Three values exist:

* ``human`` — a session created by entering a password. The platform verifies
  that the account holder authenticated with their credential; it still cannot
  prove a person was physically present for every later request made with that
  session token, so this is a channel label rather than biometric proof.
* ``agent`` — an API key. API keys exist to be used by programs, so an event
  written through one is never an owner-confirmed decision.
* ``unknown`` — anything else, including tokens minted without an
  authentication-method claim and the local passwordless test-mode session.
  Unknown stays unknown: it is never upgraded to ``human`` by inference.
"""

from typing import Any

HUMAN = "human"
AGENT = "agent"
UNKNOWN = "unknown"

VALID_ACTOR_TYPES = frozenset({HUMAN, AGENT, UNKNOWN})

# Claim written by the password login endpoint. The local candidate test-mode
# endpoint writes ``cand`` instead and is therefore never labelled human.
PASSWORD_AUTH_METHOD = "pwd"


def request_actor_provenance(request: Any) -> str:
    if getattr(request.state, "api_key_scopes", None) is not None:
        return AGENT
    methods = getattr(request.state, "auth_amr", None) or []
    if PASSWORD_AUTH_METHOD in methods:
        return HUMAN
    return UNKNOWN


def event_actor_provenance(event: Any) -> str:
    """Provenance recorded on a stored row; a missing value is unknown."""
    value = getattr(event, "actor_type", None)
    return value if value in VALID_ACTOR_TYPES else UNKNOWN
