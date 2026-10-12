"""Mock research assistant provider (Phase 2A) — deterministic, no external AI."""
from typing import Dict, Any, List, Optional
from datetime import datetime


class MockResearchAssistantProvider:
    """
    Deterministic mock research assistant that generates questions for missing fields.
    Does not call external AI services or invent choices for the user.
    """

    REQUIRED_FIELDS = {
        "title": "What is the title of your research hypothesis?",
        "hypothesis": "What is your research hypothesis? Describe what you expect to observe.",
        "falsification_criteria": "How will you know if your hypothesis is wrong? What would falsify it?",
        "strategy_template": "Which strategy template would you like to use? Options: buy_and_hold, sma_crossover, price_change_fixed_hold",
        "asset_symbol": "What asset symbol would you like to research? (e.g., DEMO-ALPHA)",
    }

    OPTIONAL_FIELDS = {
        "benchmark_symbol": "What benchmark symbol would you like to use? (e.g., DEMO-BENCHMARK)",
        "initial_cash": "What initial cash amount would you like to use? (e.g., 100000)",
        "commission_rate": "What commission rate would you like to use? (e.g., 0.001)",
        "slippage_rate": "What slippage rate would you like to use? (e.g., 0.0005)",
        "research_rules": "Do you have any specific research rules or constraints? (JSON object)",
    }

    def generate_questions(
        self,
        draft_fields: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Any]:
        """
        Generate questions for missing required and optional fields.
        Deterministic: always asks for the same missing fields in the same order.
        Does not invent choices or make decisions for the user.
        """
        from ..api.schemas import ResearchDraftQuestion

        questions = []

        for field, question_text in self.REQUIRED_FIELDS.items():
            if not draft_fields.get(field):
                questions.append(
                    ResearchDraftQuestion(
                        field=field,
                        question=question_text,
                        context=self._get_field_context(field, context),
                    )
                )

        for field, question_text in self.OPTIONAL_FIELDS.items():
            if not draft_fields.get(field):
                questions.append(
                    ResearchDraftQuestion(
                        field=field,
                        question=question_text,
                        context=self._get_field_context(field, context),
                    )
                )

        return questions

    def _get_field_context(
        self,
        field: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Get contextual hint for a field based on existing draft data."""
        if context is None:
            return None

        if field == "strategy_template":
            return "Available templates: buy_and_hold (simple hold), sma_crossover (moving average), price_change_fixed_hold (momentum)"
        elif field == "asset_symbol":
            return "Use demo assets for testing: DEMO-ALPHA, DEMO-BETA"
        elif field == "benchmark_symbol":
            return "Use demo benchmark for testing: DEMO-BENCHMARK"
        elif field == "initial_cash":
            return "Typical values: 10000, 100000, 1000000"
        elif field == "commission_rate":
            return "Typical values: 0.001 (0.1%), 0.002 (0.2%)"
        elif field == "slippage_rate":
            return "Typical values: 0.0005 (0.05%), 0.001 (0.1%)"

        return None

    def validate_draft_completeness(self, draft_fields: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate that a draft has all required fields.
        Returns a dict with 'complete' boolean and 'missing_fields' list.
        """
        missing = []
        for field in self.REQUIRED_FIELDS.keys():
            if not draft_fields.get(field):
                missing.append(field)

        return {
            "complete": len(missing) == 0,
            "missing_fields": missing,
        }


mock_research_assistant = MockResearchAssistantProvider()
