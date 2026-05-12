# -*- coding: utf-8 -*-
"""
第 3 個 domain 通用性測試:健保理賠資料集 (Healthcare Claims)。

特色:
- 三狀態值 (P/D/IP — paid / denied / in process) 比電商的雙狀態更複雜
- 金額類欄位 (claim_amount, paid_amount) 不存在於 tFlex 也不在電商,測 LLM 是否會處理 sum/mean
- 涉及 fraud_flag (布林) — 測比率類 KPI 計算
- diagnosis_category × specialty × region 多維度組合 — 測 heatmap 場景
"""

HEALTHCARE_METADATA = {
    "metadata_version": "test_healthcare_v1",
    "dataset_id": "healthcare_claims_demo",
    "dataset_name": "Healthcare Claims Dataset",
    "generated_at": "2026-05-12T00:00:00",
    "purpose": "Generality regression test #3 — must remain domain-agnostic.",
    "recommended_mongodb": {
        "database": "claims_demo",
        "collections": {
            "claims": "claims",
            "providers": "providers",
        },
        "join_key": "provider_id",
    },
    "business_context": {
        "system_name": "ClaimsHub",
        "domain": "Medical insurance claims processing",
        "business_description": (
            "Each claim corresponds to a medical service rendered by a provider for a member. "
            "Claims may be paid, denied, or still in process. Some claims are flagged for fraud review."
        ),
        "business_grain": {
            "claims": "one document per claim line",
            "providers": "one document per healthcare provider",
        },
        "main_business_questions": [
            "Which specialties have the highest denial rate?",
            "What is the total paid amount by region?",
            "Which providers have the most fraud-flagged claims?",
        ],
    },
    "collections": {
        "claims": {
            "description": "Claim-level submission records",
            "grain": "one document per claim line",
            "primary_key": "claim_id",
            "fields": {
                "claim_id": {
                    "type": "string",
                    "description": "Unique claim identifier. Keep as string.",
                },
                "member_id": {
                    "type": "string",
                    "description": "Insured member identifier. Keep as string.",
                },
                "provider_id": {
                    "type": "string",
                    "description": "Provider identifier; joins to providers.",
                    "join_to": "providers.provider_id",
                },
                "diagnosis_category": {
                    "type": "string",
                    "description": "High-level diagnosis category.",
                    "allowed_values": [
                        "Cardiology", "Orthopedics", "Pediatrics",
                        "Oncology", "General",
                    ],
                },
                "claim_status": {
                    "type": "string",
                    "description": "P = paid, D = denied, IP = in process.",
                    "allowed_values": {
                        "P": "paid",
                        "D": "denied",
                        "IP": "in process",
                    },
                },
                "fraud_flag": {
                    "type": "string",
                    "description": "Y = flagged for fraud review, N = no flag.",
                    "allowed_values": {"Y": "flagged", "N": "not flagged"},
                },
                "claim_amount": {
                    "type": "number",
                    "description": "Total billed amount on the claim.",
                },
                "paid_amount": {
                    "type": "number",
                    "description": "Amount actually paid (0 if denied).",
                },
            },
        },
        "providers": {
            "description": "Healthcare provider reference table",
            "grain": "one document per provider",
            "primary_key": "provider_id",
            "fields": {
                "provider_id": {
                    "type": "string",
                    "description": "Provider identifier.",
                },
                "specialty": {
                    "type": "string",
                    "description": "Provider specialty.",
                    "allowed_values": ["Hospital", "Clinic", "Lab", "Pharmacy"],
                },
                "region": {
                    "type": "string",
                    "description": "Geographic region.",
                    "allowed_values": ["North", "South", "East", "West"],
                },
            },
        },
    },
    "relationships": [
        {
            "type": "many_to_one",
            "from_collection": "claims",
            "from_field": "provider_id",
            "to_collection": "providers",
            "to_field": "provider_id",
            "description": "Many claims per provider.",
        }
    ],
    "kpi_definitions": {
        "total_claims": {
            "name": "Total claims",
            "formula": "count of documents in claims",
        },
        "paid_count": {
            "name": "Paid claims",
            "formula": "count where claim_status='P'",
        },
        "denied_count": {
            "name": "Denied claims",
            "formula": "count where claim_status='D'",
        },
        "in_process_count": {
            "name": "In-process claims",
            "formula": "count where claim_status='IP'",
        },
        "denial_rate": {
            "name": "Denial rate",
            "formula": "denied_count / (paid_count + denied_count)",
            "important_note": "Denominator is processed claims (P or D), excludes IP.",
        },
        "fraud_rate": {
            "name": "Suspected fraud rate",
            "formula": "count where fraud_flag='Y' / total_claims",
        },
        "total_paid_amount": {
            "name": "Total paid amount",
            "formula": "sum(paid_amount) where claim_status='P'",
        },
        "avg_claim_amount": {
            "name": "Average claim amount",
            "formula": "mean(claim_amount)",
        },
    },
    "data_limitations": {
        "missing_dimensions": [
            "No claim submission timestamp",
            "No member demographics (age / gender)",
            "No ICD-10 codes (only diagnosis_category)",
            "No procedure codes",
        ],
        "not_supported_analysis": [
            "Time trend analysis",
            "Demographic segmentation",
            "ICD-10 / procedure-level analysis",
            "Patient outcome analysis",
        ],
    },
    "charting_guidance": {
        "recommended_charts": {
            "denial_rate_by_specialty": {
                "chart_type": "bar",
                "x": "specialty",
                "y": "denial_rate",
            },
            "paid_vs_denied_by_category": {
                "chart_type": "stacked_bar",
                "x": "diagnosis_category",
                "y": ["paid_count", "denied_count"],
            },
            "region_specialty_volume": {
                "chart_type": "heatmap",
                "x": "specialty",
                "y": "region",
                "value": "total_claims",
            },
        },
        "chart_rules": [
            "For rate KPIs, format y-axis as percentage.",
            "Use stacked bar for paid vs denied comparison (互斥狀態).",
            "Avoid pie charts with > 5 categories.",
        ],
    },
}
