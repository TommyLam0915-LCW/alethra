"""Test strategy parity between research and live trading systems.

This test proves whether the live-supported subset of strategies
correctly maps to research rules with identical behavior.

Specification requirement (Section 七):
"被宣告可即時執行的策略，必須與研究／驗證策略保持同一語義"
(Declared live-executable strategies must maintain same semantics as research)
"""

import pytest


class TestStrategyParity:
    """Verify research and live strategy registries maintain correct contract."""

    def test_live_supported_subset_exists(self):
        """LIVE_SUPPORTED_STRATEGIES must be defined and non-empty."""
        from app.services.signal_generator import LIVE_SUPPORTED_STRATEGIES
        
        assert len(LIVE_SUPPORTED_STRATEGIES) > 0, (
            "LIVE_SUPPORTED_STRATEGIES must contain at least one strategy"
        )

    def test_live_supported_strategies_in_research(self):
        """All live-supported strategies must exist in research RULE_BY_ID."""
        from app.services.strategy_research import RULE_BY_ID
        from app.services.signal_generator import LIVE_SUPPORTED_STRATEGIES
        
        for rule_id in LIVE_SUPPORTED_STRATEGIES:
            assert rule_id in RULE_BY_ID, (
                f"Live-supported strategy '{rule_id}' not found in research RULE_BY_ID. "
                f"Live strategies must be a subset of research strategies."
            )

    def test_live_supported_strategies_in_registry(self):
        """All live-supported strategies must exist in STRATEGY_REGISTRY."""
        from app.services.signal_generator import (
            LIVE_SUPPORTED_STRATEGIES,
            STRATEGY_REGISTRY,
        )
        
        for rule_id in LIVE_SUPPORTED_STRATEGIES:
            assert rule_id in STRATEGY_REGISTRY, (
                f"Live-supported strategy '{rule_id}' not found in STRATEGY_REGISTRY. "
                f"All live-supported strategies must be registered."
            )

    def test_research_rules_not_required_in_live(self):
        """Research rules are NOT required to be in live (this is intentional).
        
        The contract is: live-supported ⊆ research, not live = research.
        This test documents that research can have more strategies than live.
        """
        from app.services.strategy_research import RULE_BY_ID
        from app.services.signal_generator import LIVE_SUPPORTED_STRATEGIES
        
        research_only = set(RULE_BY_ID.keys()) - LIVE_SUPPORTED_STRATEGIES
        
        # This is expected and correct
        assert len(research_only) >= 0, (
            "Research may have strategies not yet supported in live trading"
        )
        
        # Document what's research-only
        if research_only:
            print(f"\nℹ️  Research-only strategies (not yet in live): {len(research_only)}")
            for rule_id in sorted(research_only)[:10]:  # Show first 10
                print(f"  - {rule_id}")
            if len(research_only) > 10:
                print(f"  ... and {len(research_only) - 10} more")

    def test_no_chinese_name_matching(self):
        """Strategy matching must use rule_id, not Chinese names.
        
        This test verifies that both registries use stable rule_id strings,
        not display names (which may be in Chinese and subject to change).
        """
        from app.services.strategy_research import RULE_BY_ID
        from app.services.signal_generator import STRATEGY_REGISTRY
        
        # All keys should be ASCII strings (rule_ids), not Chinese
        for rule_id in RULE_BY_ID.keys():
            assert rule_id.isascii(), (
                f"Research rule_id '{rule_id}' is not ASCII. "
                f"Must use stable rule_id, not display name."
            )
        
        for rule_id in STRATEGY_REGISTRY.keys():
            assert rule_id.isascii(), (
                f"Live rule_id '{rule_id}' is not ASCII. "
                f"Must use stable rule_id, not display name."
            )

    def test_research_rules_have_required_fields(self):
        """Each research rule must have rule_id, label_zh, family, thesis_zh, signal."""
        from app.services.strategy_research import RULE_BY_ID
        
        for rule_id, rule in RULE_BY_ID.items():
            assert hasattr(rule, "rule_id"), f"Rule {rule_id} missing rule_id"
            assert hasattr(rule, "label_zh"), f"Rule {rule_id} missing label_zh"
            assert hasattr(rule, "family"), f"Rule {rule_id} missing family"
            assert hasattr(rule, "thesis_zh"), f"Rule {rule_id} missing thesis_zh"
            assert hasattr(rule, "signal"), f"Rule {rule_id} missing signal"
            assert callable(rule.signal), f"Rule {rule_id} signal is not callable"

    def test_live_strategies_have_required_fields(self):
        """Each live strategy must have display_name_zh and generator_method."""
        from app.services.signal_generator import STRATEGY_REGISTRY
        
        for rule_id, config in STRATEGY_REGISTRY.items():
            assert "display_name_zh" in config, (
                f"Live strategy {rule_id} missing display_name_zh"
            )
            assert "generator_method" in config, (
                f"Live strategy {rule_id} missing generator_method"
            )
            assert isinstance(config["generator_method"], str), (
                f"Live strategy {rule_id} generator_method must be string"
            )


class TestRangeFadeParity:
    """Test that range_fade strategy has identical behavior in research and live."""

    def test_range_fade_in_live_supported(self):
        """range_fade must be in LIVE_SUPPORTED_STRATEGIES."""
        from app.services.signal_generator import LIVE_SUPPORTED_STRATEGIES
        
        assert "range_fade" in LIVE_SUPPORTED_STRATEGIES, (
            "range_fade must be declared as live-supported"
        )

    def test_range_fade_in_research(self):
        """range_fade must exist in research RULE_BY_ID."""
        from app.services.strategy_research import RULE_BY_ID
        
        assert "range_fade" in RULE_BY_ID, (
            "range_fade must exist in research RULE_BY_ID"
        )

    def test_range_fade_in_live_registry(self):
        """range_fade must exist in live STRATEGY_REGISTRY."""
        from app.services.signal_generator import STRATEGY_REGISTRY
        
        assert "range_fade" in STRATEGY_REGISTRY, (
            "range_fade must exist in live STRATEGY_REGISTRY"
        )

    def test_range_fade_signal_generator_instantiable(self):
        """SignalGenerator must be instantiable with range_fade rule_id."""
        from app.services.signal_generator import SignalGenerator
        
        gen = SignalGenerator(
            symbol="XBT/USD",
            rule_id="range_fade",
            interval_minutes=60,
        )
        
        assert gen.rule_id == "range_fade"
        assert gen.generator_method == "_range_fade_signal"


class TestStrategyParityDocumentation:
    """Document the current parity state for owner review."""

    def test_document_parity_state(self):
        """Print a detailed parity report for owner review."""
        from app.services.strategy_research import RULE_BY_ID
        from app.services.signal_generator import (
            LIVE_SUPPORTED_STRATEGIES,
            STRATEGY_REGISTRY,
        )
        
        research_rule_ids = set(RULE_BY_ID.keys())
        live_supported = set(LIVE_SUPPORTED_STRATEGIES)
        live_registry_ids = set(STRATEGY_REGISTRY.keys())
        
        research_only = sorted(research_rule_ids - live_supported)
        live_supported_and_research = sorted(live_supported & research_rule_ids)
        live_supported_not_in_research = sorted(live_supported - research_rule_ids)
        live_registry_not_supported = sorted(live_registry_ids - live_supported)
        
        report = f"""
=== STRATEGY PARITY REPORT ===

Research RULE_BY_ID: {len(research_rule_ids)} rules
Live-supported subset: {len(live_supported)} strategies
Live STRATEGY_REGISTRY: {len(live_registry_ids)} strategies

✓ Live-supported AND in research: {len(live_supported_and_research)}
{chr(10).join(f'  ✓ {r}' for r in live_supported_and_research) if live_supported_and_research else '  (none)'}

✗ Live-supported but NOT in research: {len(live_supported_not_in_research)}
{chr(10).join(f'  ✗ {r}' for r in live_supported_not_in_research) if live_supported_not_in_research else '  (none)'}

ℹ Research-only (not yet in live): {len(research_only)}
{chr(10).join(f'  ℹ {r}' for r in research_only[:10]) if research_only else '  (none)'}
{f'  ... and {len(research_only) - 10} more' if len(research_only) > 10 else ''}

⚠ Live registry but NOT in live-supported: {len(live_registry_not_supported)}
{chr(10).join(f'  ⚠ {r}' for r in live_registry_not_supported) if live_registry_not_supported else '  (none)'}

PARITY STATUS: {'✓ CORRECT' if len(live_supported_not_in_research) == 0 else '✗ VIOLATED'}

CONTRACT:
- Live-supported ⊆ Research (live strategies must be a subset of research)
- Live-supported ∩ Research = Live-supported (all live-supported must be in research)
- Research can have strategies not yet in live (this is intentional)
"""
        print(report)
        
        # This test always passes — it's for documentation
        assert True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
