from typing import Optional, Dict, Any
from enum import Enum


class FeatureSwitch(str, Enum):
    DEMO_DATA = "demo_data"
    SANDBOX_BROKER = "sandbox_broker"
    LIVE_BROKER = "live_broker"
    TEST_PAYMENTS = "test_payments"
    LIVE_PAYMENTS = "live_payments"
    AI_MOCK = "ai_mock"
    AI_LIVE = "ai_live"


_SWITCHES: Dict[str, bool] = {
    FeatureSwitch.DEMO_DATA: True,
    FeatureSwitch.SANDBOX_BROKER: False,
    FeatureSwitch.LIVE_BROKER: False,
    FeatureSwitch.TEST_PAYMENTS: True,
    FeatureSwitch.LIVE_PAYMENTS: False,
    FeatureSwitch.AI_MOCK: True,
    FeatureSwitch.AI_LIVE: False,
}


def is_enabled(switch: FeatureSwitch) -> bool:
    return _SWITCHES.get(switch, False)


def set_switch(switch: FeatureSwitch, value: bool) -> None:
    if switch in (FeatureSwitch.LIVE_BROKER, FeatureSwitch.LIVE_PAYMENTS, FeatureSwitch.AI_LIVE):
        raise ValueError(f"Cannot enable live switch '{switch}' programmatically. Set via environment only.")
    _SWITCHES[switch] = value


def get_all_switches() -> Dict[str, bool]:
    return dict(_SWITCHES)


def get_operational_status() -> Dict[str, Any]:
    return {
        "feature_switches": get_all_switches(),
        "mode": "live" if is_enabled(FeatureSwitch.LIVE_PAYMENTS) else "test",
        "data_mode": "live" if not is_enabled(FeatureSwitch.DEMO_DATA) else "demo",
        "broker_mode": "live" if is_enabled(FeatureSwitch.LIVE_BROKER) else ("sandbox" if is_enabled(FeatureSwitch.SANDBOX_BROKER) else "paper-only"),
        "ai_mode": "live" if is_enabled(FeatureSwitch.AI_LIVE) else "mock",
    }
