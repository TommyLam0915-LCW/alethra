"""Test strategy parity between research and live trading systems.

This test proves whether RULE_BY_ID (research) and STRATEGY_REGISTRY (live)
have the same rule_id set, as required by the specification.

Specification requirement (Section 七):
"研究 RULE_BY_ID 同 live STRATEGY_REGISTRY 必須有相同 rule_id 集合"
"""

import pytest


class TestStrategyParity:
    """Verify research and live strategy registries match."""

    def test_rule_id_sets_match(self):
        """RULE_BY_ID and STRATEGY_REGISTRY must have identical rule_id sets.
        
        This test will FAIL if there is a parity violation, which is the
        correct behavior — it documents the defect and prevents regression.
        """
        from app.services.strategy_research import RULE_BY_ID
        from app.services.signal_generator import STRATEGY_REGISTRY
        
        research_rule_ids = set(RULE_BY_ID.keys())
        live_rule_ids = set(STRATEGY_REGISTRY.keys())
        
        # Document what we have
        print(f"\nResearch RULE_BY_ID has {len(research_rule_ids)} rules:")
        for rule_id in sorted(research_rule_ids):
            print(f"  - {rule_id}")
        
        print(f"\nLive STRATEGY_REGISTRY has {len(live_rule_ids)} rules:")
        for rule_id in sorted(live_rule_ids):
            print(f"  - {rule_id}")
        
        # Check for mismatches
        missing_in_live = research_rule_ids - live_rule_ids
        missing_in_research = live_rule_ids - research_rule_ids
        
        if missing_in_live:
            print(f"\n⚠️  PARITY VIOLATION: {len(missing_in_live)} research rules missing in live:")
            for rule_id in sorted(missing_in_live):
                print(f"  - {rule_id}")
        
        if missing_in_research:
            print(f"\n⚠️  PARITY VIOLATION: {len(missing_in_research)} live rules missing in research:")
            for rule_id in sorted(missing_in_research):
                print(f"  - {rule_id}")
        
        # This assertion will fail until parity is achieved
        assert research_rule_ids == live_rule_ids, (
            f"Strategy parity violation: "
            f"{len(missing_in_live)} rules in research but not live, "
            f"{len(missing_in_research)} rules in live but not research. "
            f"See specification Section 七: rule_id sets must match."
        )

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


class TestStrategyParityDocumentation:
    """Document the current parity state for owner review."""

    def test_document_parity_state(self):
        """Print a detailed parity report for owner review."""
        from app.services.strategy_research import RULE_BY_ID
        from app.services.signal_generator import STRATEGY_REGISTRY
        
        research_rule_ids = set(RULE_BY_ID.keys())
        live_rule_ids = set(STRATEGY_REGISTRY.keys())
        
        missing_in_live = sorted(research_rule_ids - live_rule_ids)
        missing_in_research = sorted(live_rule_ids - research_rule_ids)
        common = sorted(research_rule_ids & live_rule_ids)
        
        report = f"""
=== STRATEGY PARITY REPORT ===

Research RULE_BY_ID: {len(research_rule_ids)} rules
Live STRATEGY_REGISTRY: {len(live_rule_ids)} rules

Common (in both): {len(common)}
{chr(10).join(f'  ✓ {r}' for r in common) if common else '  (none)'}

Missing in Live (research only): {len(missing_in_live)}
{chr(10).join(f'  ✗ {r}' for r in missing_in_live) if missing_in_live else '  (none)'}

Missing in Research (live only): {len(missing_in_research)}
{chr(10).join(f'  ✗ {r}' for r in missing_in_research) if missing_in_research else '  (none)'}

PARITY STATUS: {'✓ ACHIEVED' if not missing_in_live and not missing_in_research else '✗ VIOLATED'}

RECOMMENDATION:
{'The rule_id sets match. No action needed.' if not missing_in_live and not missing_in_research else f'''
To achieve parity, either:
1. Add {len(missing_in_live)} missing rules to live STRATEGY_REGISTRY, OR
2. Remove {len(missing_in_research)} rules from live STRATEGY_REGISTRY, OR
3. Remove {len(missing_in_live)} rules from research RULE_BY_ID

Option 1 is recommended to preserve research capability.
'''}
"""
        print(report)
        
        # This test always passes — it's for documentation
        assert True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
