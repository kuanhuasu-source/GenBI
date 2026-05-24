"""tests/unit/test_template_compatibility.py — unit tests for template_compatibility.py (M5.6)."""

from __future__ import annotations

import pytest

from template_compatibility import (
    check_template_compatibility,
    TemplateCompatibilityResult,
)


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def template_asset_basic():
    return {
        "asset_type": "analysis_template",
        "dataset_id": "upload_source_001",
        "source_query": "比較各 category 的 amount",
        "asset_payload": {
            "template_steps": {
                "query": "比較各 category 的 amount",
                "plan_text": "...",
                "expected_q_columns": ["category", "total_amount"],
            },
        },
    }


@pytest.fixture
def template_asset_chart():
    return {
        "asset_type": "saved_chart",
        "dataset_id": "upload_source_001",
        "source_query": "畫 leadtime 分佈",
        "asset_payload": {
            "q_columns": ["bin_label", "bin_midpoint", "count"],
            "chart_engine": "echarts",
        },
    }


def _md_with_fields(field_names: list[str]) -> dict:
    return {
        "dataset_id": "target",
        "source_type": "upload",
        "collections": {
            "sheet1": {
                "fields": {n: {"semantic_role": "unknown"} for n in field_names},
            },
        },
    }


# ============================================================
# check_template_compatibility
# ============================================================
class TestCompatibilityLevels:
    def test_high_compatibility(self, template_asset_basic):
        target_md = _md_with_fields(["category", "total_amount", "other"])
        result = check_template_compatibility(template_asset_basic, target_md)
        assert result.compatibility_level == "HIGH"
        assert result.score == 1.0
        assert len(result.matched_columns) == 2
        assert result.missing_columns == []

    def test_medium_compatibility(self):
        # 3 個 expected,2 個有 = 67% → MEDIUM
        template = {
            "asset_type": "analysis_template",
            "dataset_id": "src",
            "asset_payload": {
                "template_steps": {
                    "query": "q",
                    "expected_q_columns": ["a", "b", "c"],
                },
            },
        }
        target_md = _md_with_fields(["a", "b", "x"])
        result = check_template_compatibility(template, target_md)
        assert result.compatibility_level == "MEDIUM"
        assert 0.6 <= result.score < 0.85

    def test_low_compatibility(self):
        template = {
            "asset_type": "analysis_template",
            "dataset_id": "src",
            "asset_payload": {
                "template_steps": {
                    "query": "q",
                    "expected_q_columns": ["a", "b", "c"],
                },
            },
        }
        # 只有 1/3 match
        target_md = _md_with_fields(["a"])
        result = check_template_compatibility(template, target_md)
        assert result.compatibility_level == "LOW"
        assert result.score < 0.6
        assert "a" in [m["target_col"] for m in result.matched_columns]

    def test_incompatible(self):
        template = {
            "asset_type": "analysis_template",
            "dataset_id": "src",
            "asset_payload": {
                "template_steps": {
                    "query": "q",
                    "expected_q_columns": ["a", "b"],
                },
            },
        }
        target_md = _md_with_fields(["xxx", "yyy"])
        result = check_template_compatibility(template, target_md)
        assert result.compatibility_level == "INCOMPATIBLE"
        assert result.score == 0.0


class TestSavedChartCompatibility:
    """Saved Chart 也該 work(q_columns 在不同 key)"""
    def test_chart_with_q_columns(self, template_asset_chart):
        target_md = _md_with_fields(["bin_label", "bin_midpoint", "count", "extra"])
        result = check_template_compatibility(template_asset_chart, target_md)
        assert result.compatibility_level == "HIGH"


class TestEdgeCases:
    def test_no_expected_columns(self):
        template = {
            "asset_type": "analysis_template",
            "dataset_id": "src",
            "asset_payload": {"template_steps": {"query": "q",
                                                   "expected_q_columns": []}},
        }
        target_md = _md_with_fields(["a"])
        result = check_template_compatibility(template, target_md)
        assert result.compatibility_level == "INCOMPATIBLE"

    def test_unknown_asset_type(self):
        template = {
            "asset_type": "something_weird",
            "asset_payload": {},
        }
        target_md = _md_with_fields(["a"])
        result = check_template_compatibility(template, target_md)
        assert result.compatibility_level == "INCOMPATIBLE"

    def test_target_no_collection(self):
        template = {
            "asset_type": "analysis_template",
            "dataset_id": "src",
            "asset_payload": {"template_steps": {"query": "q",
                                                   "expected_q_columns": ["a"]}},
        }
        target_md = {"dataset_id": "target", "collections": {}}
        result = check_template_compatibility(template, target_md)
        assert result.compatibility_level == "INCOMPATIBLE"


class TestWarnings:
    def test_missing_columns_in_warnings(self):
        template = {
            "asset_type": "analysis_template",
            "dataset_id": "src",
            "asset_payload": {
                "template_steps": {
                    "query": "q",
                    "expected_q_columns": ["a", "missing_x", "missing_y"],
                },
            },
        }
        target_md = _md_with_fields(["a"])
        result = check_template_compatibility(template, target_md)
        assert len(result.warnings) >= 1
        assert any("缺" in w or "missing" in w.lower() for w in result.warnings)


# ============================================================
# find_compatible_datasets
# ============================================================
class FakeProvider:
    def __init__(self, datasets: dict[str, dict]):
        self.datasets = datasets

    def list_available(self):
        return list(self.datasets.keys())

    def get_metadata(self, did):
        if did not in self.datasets:
            raise KeyError(did)
        return self.datasets[did]


class TestFindCompatible:
    def test_filters_by_min_compatibility(self, template_asset_basic):
        from template_compatibility import find_compatible_datasets

        provider = FakeProvider({
            "ds_high": _md_with_fields(["category", "total_amount"]),
            "ds_med":  _md_with_fields(["category", "other"]),
            "ds_low":  _md_with_fields(["unrelated"]),
            "upload_source_001": _md_with_fields(["foo"]),   # 該被 skip(同 dataset)
        })

        # Min MEDIUM:該得 ds_high + ds_med
        results = find_compatible_datasets(
            template_asset_basic, provider, min_compatibility="MEDIUM",
        )
        result_ids = {r["dataset_id"] for r in results}
        assert "ds_high" in result_ids
        # ds_med 是 50%,該 LOW 不該 MEDIUM
        # 但 ds_med 有 1/2 = 0.5,介於 LOW(<0.6) 邊界,該被 filter 掉
        assert "ds_low" not in result_ids
        assert "upload_source_001" not in result_ids   # 同 dataset 該 skip

    def test_sorted_by_score(self, template_asset_basic):
        from template_compatibility import find_compatible_datasets

        provider = FakeProvider({
            "ds_perfect": _md_with_fields(["category", "total_amount", "extra"]),
            "ds_partial": _md_with_fields(["category", "x"]),
        })
        results = find_compatible_datasets(
            template_asset_basic, provider, min_compatibility="LOW",
        )
        # ds_perfect 該排前面(score 1.0 > 0.5)
        if len(results) >= 2:
            assert results[0]["score"] >= results[1]["score"]
