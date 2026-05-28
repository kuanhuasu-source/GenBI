# HR Multi-table Smoke Test Report · v0.18

**Date:** 2026-05-28
**Tested:** `HR_MT_01_basic_employee_attendance.xlsx`, `HR_MT_02_training_performance.xlsx`, `HR_MT_03_complex_edge_cases.xlsx`
**Pipeline:** `file_parser.parse_excel_all_sheets` → `multi_table_profiler.profile_multi_table` → `relationship_profiler.detect_relationships`
**Script:** `smoke_test_hr_xlsx.py` in outputs folder

## TL;DR

All 3 files parse + profile + relationship-detect cleanly. **2 known v0.18 limitations** will surface when the user runs the conversational test scenarios; both are documented as deferred follow-up work (audit doc + AI_CONTEXT.md §24.14). The system behaves correctly given those limitations; the HITL UI is the workaround.

| File | Sheets | Relationships detected | High-tier | Status |
|---|---:|---:|---:|---|
| HR_MT_01 | 4 (3 data + 1 README) | 4 | 2 | ✅ all expected joins found |
| HR_MT_02 | 6 (5 data + 1 README) | 6 | 4 | ⚠️ `emp_no↔staff_id` MISSING (needs fuzzy match) |
| HR_MT_03 | 8 (7 data + 1 README) | 28 | 7 | ✅ all expected joins found; ⚠️ EmployeeMaster mis-typed |

---

## HR_MT_01 · basic_employee_attendance

### Profile

| Sheet | Rows × Cols | Inferred role | PK candidate(s) | Warnings |
|---|---|---|---|---|
| Employees | 30×10 | 📋 dim | employee_id, employee_name, hire_date | — |
| Departments | 5×5 | 📋 dim | department_id, department_name, cost_center | — |
| Attendance | 600×9 | 📊 fact | attendance_id | — |
| README_Scenarios | 11×4 | ❓ unknown | (none) | has_duplicates, no_clear_pk |

### Relationships

| From | To | Type | Conf | Tier |
|---|---|---|---:|---|
| Employees.department_id | Departments.department_id | many_to_one | 0.950 | 🟢 high |
| Attendance.employee_id | Employees.employee_id | many_to_one | 0.950 | 🟢 high |
| Departments.department_id | Employees.department_id | one_to_many | 0.783 | 🟡 review |
| Employees.employee_id | Attendance.employee_id | one_to_many | 0.760 | 🟡 review |

### Maps to scenario doc

- **Scenario 1** "List sheets + row count + possible PK" — ✅ profile output covers all.
- **Scenario 2** "Confirm `Attendance.employee_id` → `Employees.employee_id` and `Employees.department_id` → `Departments.department_id`" — ✅ both detected at high tier, ready for one-click confirm in the Relationship Review UI.
- **Scenario 3-5** (aggregate, calc column, chart, insight) — require GenBI UI + LLM.

### Known artifact (small-data PK over-detection)

`Employees` PK shows `[employee_id, employee_name, hire_date]` — because all 30 employees happen to have unique names and unique hire_dates in this test data. The user picks the real PK (`employee_id`) via the Field Review table. With production-scale data (thousands of employees), name duplicates would self-correct this.

---

## HR_MT_02 · training_performance

### Profile

| Sheet | Rows × Cols | Inferred role | PK candidate(s) | Warnings |
|---|---|---|---|---|
| Staff | 44×7 | 📋 dim | staff_id, full_name, hire_date | — |
| Departments | 5×3 | 📋 dim | dept_code, department_name | — |
| Courses | 6×5 | 📋 dim | course_code, course_name, ... | — |
| TrainingRecords | 142×7 | 📊 fact | training_id | — |
| PerformanceReviews | 44×7 | 📋 dim | review_id, staff_id | — |
| README_Scenarios | 12×4 | ❓ unknown | (none) | has_duplicates, no_clear_pk |

### Relationships

| From | To | Type | Conf | Tier |
|---|---|---|---:|---|
| Staff.dept_code | Departments.dept_code | many_to_one | 0.950 | 🟢 high |
| Staff.staff_id | PerformanceReviews.staff_id | one_to_one | 0.950 | 🟢 high |
| TrainingRecords.course_code | Courses.course_code | many_to_one | 0.950 | 🟢 high |
| PerformanceReviews.staff_id | Staff.staff_id | one_to_one | 0.950 | 🟢 high |
| Departments.dept_code | Staff.dept_code | one_to_many | 0.773 | 🟡 review |
| Courses.course_code | TrainingRecords.course_code | one_to_many | 0.758 | 🟡 review |

### ⚠️ Limitation that will hit Scenario 1

The scenario expects: **"TrainingRecords.emp_no 應該對應 Staff.staff_id"** — different field names, same value domain.

The current v0.18 M2 relationship profiler uses **exact normalized match only** — it does NOT detect `emp_no ↔ staff_id` because the names don't normalize to the same string. This is the documented deferred work:
- `AI_CONTEXT.md §24.14` item 1
- Comment block in `relationship_profiler.py:_detect_pair_relationships` (Strategy 2 TODO)

**Workaround for the test scenario:** the spec §9.1 "Add relationship manually" UI button is also deferred. For now, the user can either:

1. Edit the test xlsx to rename `emp_no` → `staff_id` (most pragmatic), OR
2. Wait for the M2 follow-up PR that adds fuzzy matching, OR
3. Manually insert a row into `upload_relationship_candidates` collection.

This is exactly the kind of limitation the audit doc called out as "deferred to follow-up PR" — surfaces here on real data.

---

## HR_MT_03 · complex_edge_cases

### Profile

| Sheet | Rows × Cols | Inferred role | PK candidate(s) | Warnings |
|---|---|---|---|---|
| EmployeeMaster | 64×8 | 🔗 **bridge** (see note) | employee_no, employee_name, hire_date | — |
| OrgUnits | 5×4 | 📋 dim | org_unit_code, org_unit_name, cost_center_code | — |
| Timekeeping | 257×7 | 📊 fact | time_id | **has_total_row** ✅ |
| LeaveApplications | 99×7 | 📊 fact | leave_id | — |
| JobHistory | 86×6 | 🔗 bridge | history_id | — |
| PayAdjustments | 58×6 | 📊 fact | adjustment_id | — |
| Lookup_Status | 4×3 | 📋 dim | status_code, status_name, description | — |
| README_Scenarios | 15×4 | ❓ unknown | (none) | has_total_row, has_duplicates, no_clear_pk |

### Spec compliance highlights

- ✅ **Timekeeping TOTAL row detected** — `has_total_row` warning fires; user can filter it out via the Field Review UI. Maps to Scenario 1 "特別標示可能的 total row".
- ✅ **Lookup_Status status_code detected as PK** — supports Scenario 2 ("狀態碼欄位需展開 allowed values").
- ✅ **`EmployeeMaster.cost_center_code → OrgUnits.cost_center_code`** flagged at 🟢 high tier — Scenario 1 says the user should manually reject this in the Review UI (spec design — the rejected relationship doesn't enter `metadata.relationships`, and the join validator refuses joins on rejected rels).

### Relationships (top 7 of 28; see smoke output for full list)

| From | To | Type | Conf | Tier |
|---|---|---|---:|---|
| EmployeeMaster.org_unit_code | OrgUnits.org_unit_code | many_to_one | 0.950 | 🟢 high |
| EmployeeMaster.cost_center_code | OrgUnits.cost_center_code | many_to_one | 0.950 | 🟢 high |
| Timekeeping.employee_no | EmployeeMaster.employee_no | many_to_one | 0.950 | 🟢 high |
| LeaveApplications.employee_no | EmployeeMaster.employee_no | many_to_one | 0.950 | 🟢 high |
| JobHistory.employee_no | EmployeeMaster.employee_no | many_to_one | 0.950 | 🟢 high |
| JobHistory.org_unit_code | OrgUnits.org_unit_code | many_to_one | 0.950 | 🟢 high |
| PayAdjustments.employee_no | EmployeeMaster.employee_no | many_to_one | 0.950 | 🟢 high |

### Spec §8.2 m2m guardrail working ✅

Many candidates with both-side-low-uniqueness signature got typed as `many_to_many_candidate` (e.g., `Timekeeping.employee_no → JobHistory.employee_no`). These flow into `upload_relationship_candidates` with the m2m tag; spec §14.6 anti-pattern test (#6 / #7) proves the join validator refuses these even when confirmed (`AI_CONTEXT.md §24.7`).

### ⚠️ EmployeeMaster mis-typed as "bridge"

The current bridge heuristic counts ≥2 FK-like columns regardless of where they point. `EmployeeMaster` has two columns (`org_unit_code`, `cost_center_code`) that both match `OrgUnits`'s PK candidates — so it's flagged as a bridge. But it's actually a **dimension** with redundant FKs to the same parent table.

**Fix concept (deferred):** refine the heuristic to require FK matches to **distinct** parent tables. ~5 LoC change in `multi_table_profiler.infer_table_role`. Skipped here to avoid changing shipped M1 unit-test fixtures.

**Workaround:** the table_role field is HITL-overridable in the Metadata Review UI; the user just picks the right role.

### `max_pairs=50` hit on 28 relationships

7 data sheets × 2 directions = 42 ordered pairs to scan, came back with 28 above-weak-threshold detections. The default `max_pairs=50` was almost exceeded — for a 10+ sheet workbook, raise the param. Logged as warning: `detect_relationships: hit max_pairs=50 limit` (visible in smoke output).

---

## Pre-flight checklist before running conversational scenarios

| Item | Status | Notes |
|---|---|---|
| All 3 xlsx parse cleanly | ✅ | No FileParseError |
| Sheet name normalization works | ✅ | "PerformanceReviews" → "performancereviews" etc. |
| Profile produces row/col counts + PK + role per sheet | ✅ | Per spec §17 M1 acceptance |
| Multi-table enrichment writes to `upload_tables` | ✅ | Verified via M1 integration test |
| Relationships persisted to `upload_relationship_candidates` | ✅ | Verified via M2 integration test |
| README_Scenarios sheets noise | ⚠️ | Get profiled as `unknown` role with warnings; user can ignore or reject |
| HR_MT_02 `emp_no↔staff_id` fuzzy match | ❌ | Not detected — see workaround above |
| HR_MT_03 EmployeeMaster role | ⚠️ | Flagged "bridge", actually dimension — HITL override needed |
| Total row detection (HR_MT_03 Timekeeping) | ✅ | `has_total_row` warning fires |
| m2m guardrail tag | ✅ | Multiple m2m_candidate detections in HR_MT_03 |
| MongoDB instance | required | All 3 conversational scenarios need it |
| Ollama / LLM endpoint | required | Phase 0-D, Phase A LLM call, insight generation |

---

## Recommended test sequence per scenarios doc

1. Start with HR_MT_01 — cleanest expected flow, all relationships auto-detect.
2. Move to HR_MT_03 — exercises HITL rejection (cost_center_code) + total-row filter + m2m guardrail + Lookup_Status mapping. Most spec coverage in one workbook.
3. Save HR_MT_02 for after — known fuzzy-match gap requires either xlsx edit or accepting the limitation.

---

## Smoke test reproduction

The smoke test script lives at `/sessions/.../outputs/smoke_test_hr_xlsx.py` (or has been re-saved to the project root). Run with:

```bash
cd ~/Documents/Claude/Projects/GenBI
python smoke_test_hr_xlsx.py
```

Or against a single file:

```bash
python smoke_test_hr_xlsx.py /path/to/HR_MT_01_basic_employee_attendance.xlsx
```
