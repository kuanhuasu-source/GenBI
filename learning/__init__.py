"""
GenBI Self-Learning Layer (v1 MVP)

Modules:
  - bootstrap          — Seed historical hotfix rules into learning_instincts
  - failure_filter     — Filter task_traces for failed runs to analyze
  - observation_extractor — Convert trace + error into structured observation
  - verifier           — Independently validate observations
  - confidence         — Compute confidence sub-components
  - instinct_consolidator — Cluster verified observations into atomic rules
  - candidate_generator   — Generate prompt rule candidates from instincts
  - promotion_gate     — Regression test gate before approval
  - dashboard_metrics  — Compute learning system metrics

See GenBI_v1.3_Self_Learning_MVP_Implementation_Spec.md for details.
"""

__version__ = "0.8.2-alpha"
