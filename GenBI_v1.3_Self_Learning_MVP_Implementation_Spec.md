# GenBI v1 Self-Learning MVP Implementation Specification

Version: 1.3
Date: 2026-05-16
Status: Implementation Ready
Target Duration: 4–6 Weeks

---

# 1. Objective

This specification defines the minimum viable implementation required to add self-learning capability to GenBI.

The purpose is to enable GenBI to:

1. Record every important execution trace.
2. Convert failures into structured observations.
3. Independently verify observations.
4. Consolidate verified observations into reusable instincts.
5. Automatically convert failures into regression test cases.
6. Generate prompt rule candidates for human review.

This specification is intentionally limited to an MVP scope.

---

# 2. Design Principles

## 2.1 Core Principle

> Do not allow the LLM to directly modify production code or prompts.
> Instead, convert experience into structured memory, validate it, and promote only after regression testing.

## 2.2 Engineering Principles

1. Capture and Consolidation are separate.
2. Implementer and Verifier are separate.
3. Every fix becomes a regression test.
4. Human approval is required before activation.
5. Structural validators are preferred over prompt-only rules.

---

# 3. Current GenBI Baseline

This design integrates with existing components.

| Existing Component | Current Status | Role in Self-Learning |
|-------------------|---------------|----------------------|
| task_traces | Available | L1 episodic memory |
| test_cases | Available | Regression library |
| test_runs | Available | Benchmark results |
| prompt_repository | Available | Prompt version control |
| validators | Available | Structural defenses |
| admin prompt UI | Available | Human approval interface |

No replacement is required.

---

# 4. MVP Scope

Included in v1:

1. Task trace integration
2. Failure filtering
3. Observation extraction
4. Verifier agent
5. Instinct consolidation
6. Failure-to-test-case conversion
7. Prompt rule candidates
8. Regression gate
9. Learning dashboard

Excluded from v1:

- L3 Skills
- L4 Strategic Rules
- Automatic production updates
- Code self-modification
- HyperAgents
- REST APIs

---

# 5. Non-Goals

The following are explicitly out of scope:

- Self-modifying source code
- Automatic Git commits
- Autonomous prompt activation
- Global promotion across domains
- Multi-agent code evolution

---

# 6. High-Level Architecture

```text
User Query
    ↓
GenBI Pipeline
    ↓
task_traces
    ↓
Failure Filter
    ↓
Observation Extractor
    ↓
Verifier Agent
    ↓
learning_instincts
    ↓
Prompt Rule Candidate Generator
    ↓
Regression Tests
    ↓
Human Approval
    ↓
prompt_repository
```

---

# 7. Data Model

## 7.1 learning_observations

```json
{
  "_id": "ObjectId",
  "observation_id": "OBS-000001",
  "run_id": "uuid",
  "source_trace_id": "ObjectId",

  "query_hash": "sha256",
  "phase": "phase_a|phase_b|phase_c|phase_d",

  "context": "What was the task?",
  "action": "What did the system do?",
  "result": "What happened?",
  "cause": "Why did it happen?",
  "recommendation": "What should be done next time?",

  "tags": ["echarts", "percentage"],
  "status": "candidate|verified|rejected|merged",
  "dedupe_key": "sha256",
  "created_at": "datetime"
}
```

Indexes:

- observation_id (unique)
- run_id
- status
- dedupe_key (unique)

---

## 7.2 verifier_results

```json
{
  "_id": "ObjectId",
  "observation_id": "OBS-000001",

  "decision": "accept|revise|reject",
  "confidence": 0.86,

  "reasoning": "The observation is supported by trace evidence.",
  "issues": [],
  "created_at": "datetime"
}
```

Indexes:

- observation_id
- decision

---

## 7.3 learning_instincts

```json
{
  "_id": "ObjectId",
  "instinct_id": "INST-000001",

  "rule": "If value column already contains 0-100 percentages, do not multiply by 100.",

  "scope": "project",
  "domain": "tflex",

  "confidence": 0.87,
  "evidence_count": 4,
  "contradiction_count": 0,

  "supporting_observation_ids": [],
  "status": "candidate|testing|active|deprecated",

  "created_at": "datetime",
  "updated_at": "datetime"
}
```

Indexes:

- instinct_id (unique)
- status
- domain

---

## 7.4 learning_jobs

```json
{
  "_id": "ObjectId",
  "job_id": "JOB-000001",

  "job_type": "observation_extraction|consolidation|promotion",
  "status": "queued|running|completed|failed",

  "input_count": 0,
  "output_count": 0,

  "started_at": "datetime",
  "completed_at": "datetime",

  "error_message": null
}
```

Indexes:

- job_id (unique)
- status
- job_type

---

## 7.5 prompt_rule_candidates

```json
{
  "_id": "ObjectId",
  "candidate_id": "PRC-000001",

  "instinct_id": "INST-000001",
  "target_component": "phase_c_prompt",

  "proposed_rule": "Do not multiply percentage columns already in 0-100 range.",

  "evidence_count": 4,
  "confidence": 0.87,

  "status": "candidate|testing|approved|rejected",
  "created_at": "datetime"
}
```

Indexes:

- candidate_id (unique)
- status

---

# 8. Bootstrap Strategy

Use hybrid warm start.

## 8.1 Historical Seed Rules (from GenBI v0.3.x–v0.7.x hotfixes)

Week 1 must seed the following historical instincts into `learning_instincts`.

All bootstrap seed records should use:

```json
{
  "source": "historical_seed",
  "confidence": 0.95,
  "evidence_count": 10,
  "status": "active"
}
```

### Phase A — MongoDB Pipeline

#### 1. strip_derived_expressions

- Version source: v0.4.1
- Rule: If `$project`, `$addFields`, or `$set` contains derived expressions such as `$cond`, `$divide`, or `$multiply`, strip that derived field before execution.
- Phase: `phase_a`
- error_class: `unsupported_operator`
- Implementation: `sanitize_pipeline()` in `llm_service.py`

#### 2. defensive_json_extraction

- Version source: v0.3.6
- Rule: If the LLM response includes preamble text, markdown fences, or code block wrappers, use a balanced-brace parser to extract the first valid JSON object.
- Phase: `phase_a`
- error_class: `json_parse`
- Implementation: `extract_json_block()` in `llm_service.py`

#### 3. phase_a_retry_with_error_feedback

- Version source: v0.3.6
- Rule: If Phase A parsing fails, retry with the previous error message and stricter JSON-only instruction. Maximum 3 attempts.
- Phase: `phase_a`
- error_class: `json_parse`

---

### Phase B — Pandas Preprocess

#### 4. series_to_dataframe_safety_net

- Version source: v0.3.6
- Rule: If Phase B produces `Q` as a pandas Series because `reset_index()` was omitted, automatically convert it to a DataFrame with `to_frame()`.
- Phase: `phase_b`
- error_class: `type_mismatch`

#### 5. forbid_import_in_phase_b

- Version source: v0.7.1
- Rule: Phase B must not import packages. `pd` and `np` are already available. A chart-related user query must not cause Phase B to hallucinate `matplotlib` or visualization imports.
- Phase: `phase_b`
- error_class: `import_forbidden`

#### 6. forbid_phase_b_replay_raw_df

- Version source: v0.7.1
- Rule: `Q` is the terminal output of Phase B. Phase B must not replay calculations from raw-level fields that may have been removed by Phase A `$project`.
- Phase: `phase_b`
- error_class: `column_missing`

---

### Phase C — ECharts Visualization

#### 7. coerce_numpy_to_native

- Version source: v0.4.6
- Rule: Cast all `numpy.int64`, `numpy.float64`, `NaN`, and `pandas.Timestamp` values inside ECharts option dict to Python-native JSON-safe types.
- Phase: `phase_c`
- error_class: `numpy_serialization`
- Implementation: `coerce_option_native_types()` in `llm_service.py`

#### 8. rescue_empty_echarts

- Version source: v0.4.7
- Rule: If the LLM produces an empty-shell option such as `xAxis.data=[]` or `series=[]`, use `Q` to auto-pivot and reconstruct valid series. Wide format with one dimension plus multiple numeric columns is supported.
- Phase: `phase_c`
- error_class: `empty_shell`
- Implementation: `rescue_empty_echarts()` in `llm_service.py`

#### 9. rescue_in_except_path

- Version source: v0.4.7
- Rule: If Phase C execution fails, still inspect the partial `option` object in the execution namespace. If it can be rescued, return the rescued option and stop retrying.
- Phase: `phase_c`
- error_class: `empty_shell`

#### 10. dual_axis_force_route

- Version source: v0.4.2
- Rule: If the query asks for absolute volume, rate, and comparison at the same time, force a dual-axis bar-plus-line chart using `yAxisIndex` 0 and 1. Do not downgrade to `_use_table` KPI cards.
- Phase: `phase_c`
- intent: `line_dual`

#### 11. forbid_empty_shell_dynamic_fill

- Version source: v0.4.7
- Rule: Phase C must not use the anti-pattern of declaring an empty-shell option and dynamically filling it later. The full option literal should be constructed in one pass to reduce KeyError risk.
- Phase: `phase_c`
- error_class: `empty_shell`

---

### Phase 0 — Planning / Refusal

#### 12. chart_word_not_refuse_trigger

- Version source: v0.4.3
- Rule: Chart words such as `pie`, `bar`, `heatmap`, `圓餅圖`, and `長條圖` must not participate in refusal decisions. They are presentation choices, not missing data dimensions.
- Phase: `phase_0`
- error_class: `false_positive_refusal`

---

### Cross-Phase

#### 13. prompt_invariants_enforcement

- Version source: v0.7.2
- Rule: Each phase prompt and all intent variants must be checked with sentinel invariant tests to ensure critical rules are not lost during refactoring.
- Phase: `meta`
- error_class: `regression_protection`
- Implementation: `scripts/check_prompt_invariants.py`

## 8.2 Ongoing Learning

All future failures contribute additional evidence.

---

# 9. Failure Filter

Observation extraction is triggered only when one or more of the following conditions are true:

- status = failed
- retry_count > 0
- fallback_used = true
- user_feedback = negative
- manually flagged

This controls cost.

---

# 10. Observation Extraction

## 10.1 Input

- task_trace
- errors
- retry history
- final outputs

## 10.2 Output Schema

Strict JSON with five required fields:

- context
- action
- result
- cause
- recommendation

## 10.3 Rejection Rules

Reject extraction if:

- any required field missing
- recommendation is generic
- cause unsupported by evidence
- duplicate dedupe_key exists

---

# 11. Observation Extraction Prompt

```text
You are an execution analyst.

Analyze the provided GenBI trace.

Extract one concrete and actionable learning.

Rules:
1. Cause must be directly supported by the trace.
2. Recommendation must be specific and testable.
3. Avoid vague statements.
4. Return strict JSON only.

Required fields:
- context
- action
- result
- cause
- recommendation
- tags
```

---

# 12. Verifier Agent

## 12.1 Purpose

Validate whether the observation is:

- factual
- actionable
- non-duplicative

## 12.2 Decision Rules

### Accept

All conditions are met:

- supported by trace
- recommendation is actionable
- not duplicate
- confidence >= 0.75

### Revise

Useful but incomplete.

### Reject

Any of:

- hallucinated cause
- generic recommendation
- duplicate
- confidence < 0.75

---

# 13. Confidence Calculation

```text
confidence =
  0.40 * evidence_support
+ 0.30 * specificity
+ 0.20 * consistency
+ 0.10 * novelty
```

Each component ranges from 0 to 1.

---

# 14. Instinct Consolidation

## 14.1 Trigger

Run when:

- at least 10 verified observations
- or manually triggered

## 14.2 Clustering

Group by:

- tags
- semantic similarity
- phase

## 14.3 Creation Rule

Create instinct when:

- at least 3 similar verified observations
- average confidence >= 0.80

---

# 15. Contradiction Handling

If a new observation contradicts an active instinct:

1. Increment contradiction_count.
2. Reduce confidence by 0.05.
3. If confidence < 0.60, mark as deprecated.
4. Notify for manual review.

---

# 16. Confidence Decay

Nightly job.

If an instinct has no supporting evidence in 90 days:

- confidence -= 0.02

If confidence < 0.50:

- status = deprecated

---

# 17. Failure to Test Case Conversion

## Trigger

When a failed run is later resolved successfully.

## Process

Create a new test_cases record with:

- user query
- expected intent
- expected chart type
- expected constraints
- regression tags

---

# 18. Prompt Rule Candidate Generation

Generate a candidate when:

- instinct is active
- confidence >= 0.85
- evidence_count >= 3

---

# 19. Regression Gate

Before approving any candidate:

1. Run benchmark suite.
2. Compare with baseline.
3. Require:
   - no critical regressions
   - pass rate not lower
   - latency increase < 10%
   - cost increase < 15%

---

# 20. Human Approval Workflow

```text
Candidate
    ↓
Testing
    ↓
Benchmark Passed
    ↓
Human Review
    ↓
Approved
    ↓
Manual Merge to prompt_repository
```

---

# 21. Structural Validator Integration

Prompt candidates should first be classified as:

- validator rule
- prompt rule
- metadata rule

Priority order:

1. Structural validator
2. Metadata update
3. Prompt update

---

# 22. Cost Control

Only failed and high-value runs are analyzed.

Recommended daily limits:

- max observation extractions: 50
- max verifier calls: 50
- max consolidation jobs: 1

---

# 23. Risk Control

## Observation Hallucination

Mitigation:

- independent verifier
- evidence-backed reasoning

## Confidence Inflation

Mitigation:

- contradiction tracking
- decay

## Duplicate Noise

Mitigation:

- dedupe_key

## Regression Corruption

Mitigation:

- test cases are immutable after approval

---

# 24. Context Definition

Context is defined as:

(domain, phase, chart_type, intent)

Promotion across contexts requires evidence from at least 3 distinct contexts.

---

# 25. Learning Dashboard Metrics

## Operational

- observations created
- accepted / rejected counts
- active instincts

## Quality

- retry rate
- fallback rate
- benchmark pass rate

## Impact

- number of approved candidates
- precision improvement

---


# 27. Function Interfaces

## observation_extractor.py

```python
def extract_observation(trace: dict) -> dict:
    ...
```

## verifier.py

```python
def verify_observation(observation: dict, trace: dict) -> dict:
    ...
```

## instinct_consolidator.py

```python
def consolidate_instincts(observations: list[dict]) -> list[dict]:
    ...
```

---

# 28. Implementation Roadmap

## Week 1

- MongoDB collections
- bootstrap seeds
- failure filter

## Week 2

- observation extractor
- dedupe

## Week 3

- verifier
- confidence calculation

## Week 4

- instinct consolidation
- contradiction handling

## Week 5

- failure-to-test conversion
- prompt candidates

## Week 6

- dashboard
- promotion workflow

---

# 29. Acceptance Criteria

System is complete when:

1. Failed runs automatically generate observations.
2. Verifier accepts or rejects observations.
3. Similar observations consolidate into instincts.
4. Resolved failures generate regression tests.
5. Prompt rule candidates are benchmarked.
6. Human can approve candidates.
7. Dashboard shows learning metrics.

---

# 30. Success Metrics

After 1–3 months:

- retry rate reduced by 30%
- fallback rate reduced by 30%
- benchmark pass rate improved by 10%
- manual prompt tuning reduced by 50%

---

# 31. Future Scope (Not in v1)

- L3 Skills
- L4 Strategic Rules
- Cross-domain promotion
- Autonomous curation
- HyperAgents self-modification

---


# 11.5 Observation Extraction Few-Shot Examples

## ✅ Good Observation Example

```json
{
  "context": "Phase C echarts generation for 100% stacked bar (intent=stacked_100)",
  "action": "LLM wrote `(Q['pct'] * 100).tolist()` for series.data",
  "result": "Y-axis displayed 0-10000% instead of 0-100%",
  "cause": "Q['pct'] was already normalized to 0-100 by Phase B; multiplying by 100 again was duplicate conversion",
  "recommendation": "Add Phase C rule: If column name contains _pct, percentage, or _rate, do not multiply by 100",
  "tags": ["phase_c", "stacked_100", "percentage", "double_conversion"]
}
```

## ❌ Bad Observation Example (Should Be Rejected)

```json
{
  "context": "Phase C had an issue",
  "action": "Generated some code",
  "result": "Failed",
  "cause": "LLM made a mistake",
  "recommendation": "Improve Phase C prompt",
  "tags": ["phase_c"]
}
```

Reasons for rejection:

- Cause is unsupported.
- Recommendation is too vague.
- Missing concrete field names or operators.

## Controlled Tag Vocabulary

Tags must be selected from controlled vocabularies.

### phase
- phase_a
- phase_b
- phase_c
- phase_d

### chart_type
- pie
- bar
- stacked_raw
- stacked_100
- line
- line_dual
- heatmap
- scatter
- kpi_table

### error_class
- column_missing
- numpy_serialization
- empty_shell
- duplicate_conversion
- import_forbidden
- json_parse
- divide_by_zero
- unsupported_operator

### domain
- tflex
- hr
- healthcare
- ecommerce
- finance


# 13.5 Confidence Sub-Component Definitions

| Component | Range | Computation Method |
|----------|------:|-------------------|
| evidence_support | 0–1 | min(trace_quotes_count / 3, 1.0) |
| specificity | 0–1 | 0.5 if recommendation contains column name or operator; +0.3 if contains numeric threshold; +0.2 if directly testable in code |
| consistency | 0–1 | similar_observations_same_recommendation / max(similar_observations, 1) |
| novelty | 0–1 | 1 - max(cosine_similarity(current_observation, active_instincts)) |

## Implementation Requirement

These calculations must be implemented as pure Python functions in:

```text
learning/confidence.py
```

## Example

```python
def compute_evidence_support(trace_quotes_count: int) -> float:
    return min(trace_quotes_count / 3.0, 1.0)
```


# 17.5 Resolution Detection Algorithm

A failed case is considered resolved when all conditions are met:

1. A later `task_traces` record exists with the same `query_hash`.
2. The later record has `status = completed`.
3. The timestamp difference is less than 30 days.
4. The `prompt_repository` version changed between the failed and successful runs.

## Nightly Scan Logic

```text
For each query_hash:
    if failed run exists
    and later completed run exists
    and prompt version changed
    and no existing regression test
        create test_cases record
```

## Manual Override

A user may manually mark a failed case as resolved even if the prompt version did not change.

Manual resolution is performed through:

```text
pages/06_learning_review.py
Button: Mark as Resolved
```

This supports cases where fixes were implemented in validators or metadata rather than prompts.


# 15.5 Manual Review Notification

When an instinct's confidence falls below 0.60 due to contradictions:

1. Insert a notification record into `learning_jobs`.
2. Show the instinct in the Learning Dashboard under "Needs Review".
3. Require human review before reactivation.

No email or external notification is required in v1.


# 16.5 Scheduler Execution Model

All scheduled learning jobs are executed by:

```text
scripts/run_learning_jobs.py
```

Recommended deployment options:

- Linux cron
- systemd timer
- Windows Task Scheduler

Example cron entry:

```cron
0 2 * * * /usr/bin/python /path/to/GenBI/scripts/run_learning_jobs.py
```

Jobs executed:

1. confidence decay
2. resolution detection
3. instinct consolidation
4. dashboard metric refresh


# 19.5 Threshold Calibration Note

The following thresholds are initial defaults:

- latency increase < 10%
- cost increase < 15%

These values are heuristic and should be recalibrated after 3 months of production data.


# 21.5 Rule Classification Decision Tree

Use the following decision logic:

## Validator Rule
Choose when the rule can be implemented deterministically in Python.

Examples:
- cast numpy numeric types
- detect duplicate percentage conversion
- validate column existence

## Metadata Rule
Choose when the rule represents domain facts or schema information.

Examples:
- no time column exists
- default aggregation column

## Prompt Rule
Choose when the rule modifies LLM reasoning behavior.

Examples:
- prioritize pie chart for distribution questions
- ask clarifying questions when dimensions are ambiguous

## Decision Priority

1. Validator Rule
2. Metadata Rule
3. Prompt Rule

The `candidate_generator.py` module is responsible for initial classification.
Human reviewers may override the classification.


# 26. Package Structure

```text
GenBI/
├── learning/
│   ├── failure_filter.py
│   ├── observation_extractor.py
│   ├── verifier.py
│   ├── confidence.py
│   ├── instinct_consolidator.py
│   ├── candidate_generator.py
│   ├── promotion_gate.py
│   ├── bootstrap.py
│   └── dashboard_metrics.py
│
├── task_trace.py
├── prompt_repository.py
├── embedded_prompts.py
├── app.py
├── admin/
├── pages/
├── scripts/
│   └── run_learning_jobs.py
```


# 30.5 Success Metrics Measurement Method

## Baseline Window

Use the 30-day period immediately before enabling the self-learning system.

## Evaluation Window

Use rolling 30-day windows after deployment.

## Attribution Method

Compare:

- benchmark pass rate
- retry rate
- fallback rate

between:

- baseline period
- post-deployment period

Operational improvements from unrelated hotfixes should be documented in release notes.


# 32. Final Summary

GenBI v1 self-learning MVP implements one core loop:

```text
Failure
 → Observation
 → Verification
 → Instinct
 → Test Case
 → Prompt Candidate
 → Regression Gate
 → Human Approval
 → Production Improvement
```

This creates a controlled and auditable learning mechanism that continuously improves system precision and stability without allowing uncontrolled autonomous changes.
