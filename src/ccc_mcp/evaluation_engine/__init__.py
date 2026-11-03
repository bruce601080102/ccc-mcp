"""Evidence-weighted, rule-based evaluation of Agent output — never LLM-vs-LLM
subjective scoring. See evaluation_service.EvaluationService for the entry point.

Deliberately has no import of ..core.tasks/..core.configuration: this package is a
leaf dependency (core/evaluation.py depends on it, never the other way around) so it
stays independently testable against a plain Store fixture.
"""
