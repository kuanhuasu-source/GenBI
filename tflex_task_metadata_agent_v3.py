# -*- coding: utf-8 -*-
# Agent-oriented metadata for tFlex employee benefit application dataset.
# Usage: from tflex_task_metadata_agent_v3 import TASK_METADATA

TASK_METADATA = {
    "metadata_version": "agent_v3",
    "dataset_id": "tflex_employee_benefit_application",
    "dataset_name": "tFlex Employee Benefit Application Dataset",
    "generated_at": "2026-05-12T05:01:55",
    "purpose": "Agent-oriented metadata for LLM prompt context, text-to-MongoDB, data preprocessing, charting, reporting, and insight generation.",
    "source_files": {
        "applications_rawdata_csv": "tflex_applications_rawdata_v2.csv",
        "company_hc_rawdata_csv": "tflex_company_hc_rawdata_v2.csv",
        "mongodb_import_script": "import_tflex_to_mongodb.py"
    },
    "recommended_mongodb": {
        "database": "tflex_demo",
        "collections": {
            "applications": "tflex_applications",
            "company_hc": "tflex_company_hc"
        },
        "join_key": "company_code"
    },
    "business_context": {
        "system_name": "tFlex",
        "domain": "Employee benefit application",
        "business_description": "This dataset records employee benefit application forms submitted through tFlex. Each application belongs to one employee and one subsidiary company. Applications may be completed or still in progress. Completed applications may be approved for payment or returned.",
        "business_grain": {
            "tflex_applications": "one document per benefit application form",
            "tflex_company_hc": "one document per subsidiary company headcount reference"
        },
        "main_business_questions": [
            "Which companies have higher or lower employee submission rates?",
            "Which companies have higher return rates?",
            "How many applications are still in progress?",
            "What is the distribution of benefit application categories?",
            "What is the AI review adoption rate among completed applications?",
            "Which companies have high application volume and high return workload?"
        ]
    },
    "collections": {
        "tflex_applications": {
            "description": "Application-level tFlex benefit claim records",
            "grain": "one document per application form",
            "primary_key": "application_no",
            "fields": {
                "employee_id": {
                    "type": "string",
                    "description": "Six-digit employee ID. Keep as string, not integer.",
                    "format": "^[0-9]{6}$"
                },
                "company_code": {
                    "type": "string",
                    "description": "Three-letter subsidiary company code.",
                    "allowed_values": [
                        "TST",
                        "TSC",
                        "TSA",
                        "TSN",
                        "JSM",
                        "TWT",
                        "TSU",
                        "TDI",
                        "TDJ",
                        "ESM",
                        "TSE",
                        "TRJ",
                        "TDC",
                        "TSJ",
                        "TSK"
                    ],
                    "join_to": "tflex_company_hc.company_code"
                },
                "application_no": {
                    "type": "string",
                    "description": "Eight-digit sequential application number. Keep as string.",
                    "format": "^[0-9]{8}$"
                },
                "application_category": {
                    "type": "string",
                    "description": "Benefit application category.",
                    "allowed_values": [
                        "Family Care",
                        "Wellness",
                        "Medical & Insurance",
                        "Development & Voluteering"
                    ]
                },
                "review_status": {
                    "type": "string",
                    "description": "Y = completed, N = in progress.",
                    "allowed_values": {
                        "Y": "completed",
                        "N": "in progress"
                    }
                },
                "review_result": {
                    "type": "string_or_null",
                    "description": "Y = approved/payable, N = returned/rejected, null = not completed yet.",
                    "allowed_values": {
                        "Y": "approved and payable",
                        "N": "returned or rejected",
                        "null": "not completed yet"
                    }
                },
                "review_mechanism": {
                    "type": "string_or_null",
                    "description": "AI = AI review, H = human review, null = not completed yet.",
                    "allowed_values": {
                        "AI": "AI review",
                        "H": "human review",
                        "null": "not completed yet"
                    }
                }
            }
        },
        "tflex_company_hc": {
            "description": "Company-level headcount reference view",
            "grain": "one document per company",
            "primary_key": "company_code",
            "fields": {
                "company_code": {
                    "type": "string",
                    "description": "Three-letter subsidiary company code.",
                    "allowed_values": [
                        "TST",
                        "TSC",
                        "TSA",
                        "TSN",
                        "JSM",
                        "TWT",
                        "TSU",
                        "TDI",
                        "TDJ",
                        "ESM",
                        "TSE",
                        "TRJ",
                        "TDC",
                        "TSJ",
                        "TSK"
                    ]
                },
                "hc": {
                    "type": "integer",
                    "description": "Company headcount"
                }
            }
        }
    },
    "relationships": [
        {
            "type": "many_to_one",
            "from_collection": "tflex_applications",
            "from_field": "company_code",
            "to_collection": "tflex_company_hc",
            "to_field": "company_code",
            "description": "Many application documents belong to one company headcount reference record."
        }
    ],
    "kpi_definitions": {
        "headcount": {
            "name": "H/C",
            "formula": "hc from tflex_company_hc"
        },
        "submitter_count": {
            "name": "送單人數",
            "formula": "distinct count of employee_id in tflex_applications"
        },
        "total_applications": {
            "name": "總申請張數",
            "formula": "count of documents in tflex_applications"
        },
        "pay_count": {
            "name": "PAY",
            "formula": "count where review_status='Y' and review_result='Y'"
        },
        "return_count": {
            "name": "RTN",
            "formula": "count where review_status='Y' and review_result='N'"
        },
        "completed_count": {
            "name": "Completed applications",
            "formula": "count where review_status='Y'"
        },
        "in_progress_count": {
            "name": "In-progress applications",
            "formula": "count where review_status='N'"
        },
        "employee_submission_rate": {
            "name": "員工送單率",
            "formula": "distinct employee_id count / company hc"
        },
        "average_return_rate": {
            "name": "平均退單率",
            "formula": "return_count / completed_count",
            "important_note": "Do not include in-progress applications in the denominator."
        },
        "completion_rate": {
            "name": "審核完成率",
            "formula": "completed_count / total_applications"
        },
        "ai_review_rate": {
            "name": "AI 審查率",
            "formula": "count where review_status='Y' and review_mechanism='AI' / completed_count"
        }
    },
    "mongo_query_guidance": {
        "default_database": "tflex_demo",
        "default_collections": {
            "applications": "tflex_applications",
            "company_hc": "tflex_company_hc"
        },
        "join_pattern": {
            "from": "tflex_company_hc",
            "localField": "company_code",
            "foreignField": "company_code",
            "as": "company_info"
        },
        "common_filters": {
            "completed_only": {
                "review_status": "Y"
            },
            "in_progress_only": {
                "review_status": "N"
            },
            "pay_only": {
                "review_status": "Y",
                "review_result": "Y"
            },
            "returned_only": {
                "review_status": "Y",
                "review_result": "N"
            },
            "ai_reviewed_only": {
                "review_status": "Y",
                "review_mechanism": "AI"
            },
            "human_reviewed_only": {
                "review_status": "Y",
                "review_mechanism": "H"
            }
        },
        "aggregation_rules": [
            "Use $group by company_code for company-level analysis.",
            "Use $addToSet employee_id and then $size for distinct submitter count.",
            "Use $lookup when calculating employee_submission_rate because hc is stored in tflex_company_hc.",
            "Do not calculate return rate using in-progress applications as denominator.",
            "Do not treat null review_result as returned or approved.",
            "Do not cast employee_id or application_no to integer."
        ]
    },
    "example_mongodb_aggregations": {
        "company_level_kpi_summary": [
            {
                "$group": {
                    "_id": "$company_code",
                    "submitters": {
                        "$addToSet": "$employee_id"
                    },
                    "total_applications": {
                        "$sum": 1
                    },
                    "completed_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$eq": [
                                        "$review_status",
                                        "Y"
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    },
                    "pay_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$review_status",
                                                "Y"
                                            ]
                                        },
                                        {
                                            "$eq": [
                                                "$review_result",
                                                "Y"
                                            ]
                                        }
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    },
                    "return_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$review_status",
                                                "Y"
                                            ]
                                        },
                                        {
                                            "$eq": [
                                                "$review_result",
                                                "N"
                                            ]
                                        }
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    },
                    "ai_review_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$review_status",
                                                "Y"
                                            ]
                                        },
                                        {
                                            "$eq": [
                                                "$review_mechanism",
                                                "AI"
                                            ]
                                        }
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    },
                    "human_review_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$review_status",
                                                "Y"
                                            ]
                                        },
                                        {
                                            "$eq": [
                                                "$review_mechanism",
                                                "H"
                                            ]
                                        }
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    }
                }
            },
            {
                "$lookup": {
                    "from": "tflex_company_hc",
                    "localField": "_id",
                    "foreignField": "company_code",
                    "as": "company_info"
                }
            },
            {
                "$unwind": "$company_info"
            },
            {
                "$addFields": {
                    "company_code": "$_id",
                    "hc": "$company_info.hc",
                    "submitter_count": {
                        "$size": "$submitters"
                    },
                    "employee_submission_rate": {
                        "$divide": [
                            {
                                "$size": "$submitters"
                            },
                            "$company_info.hc"
                        ]
                    },
                    "average_return_rate": {
                        "$cond": [
                            {
                                "$gt": [
                                    "$completed_count",
                                    0
                                ]
                            },
                            {
                                "$divide": [
                                    "$return_count",
                                    "$completed_count"
                                ]
                            },
                            None
                        ]
                    },
                    "ai_review_rate": {
                        "$cond": [
                            {
                                "$gt": [
                                    "$completed_count",
                                    0
                                ]
                            },
                            {
                                "$divide": [
                                    "$ai_review_count",
                                    "$completed_count"
                                ]
                            },
                            None
                        ]
                    }
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "company_code": 1,
                    "hc": 1,
                    "submitter_count": 1,
                    "total_applications": 1,
                    "completed_count": 1,
                    "pay_count": 1,
                    "return_count": 1,
                    "ai_review_count": 1,
                    "human_review_count": 1,
                    "employee_submission_rate": 1,
                    "average_return_rate": 1,
                    "ai_review_rate": 1
                }
            },
            {
                "$sort": {
                    "total_applications": -1
                }
            }
        ]
    },
    "data_preprocessing_guidance": {
        "id_handling": [
            "employee_id is a six-digit string and must not be converted to integer.",
            "application_no is an eight-digit string and must not be converted to integer."
        ],
        "missing_value_rules": [
            "review_result is null when review_status=N.",
            "review_mechanism is null when review_status=N.",
            "Null review_result means in progress, not returned."
        ],
        "derived_fields": {
            "is_completed": "review_status == 'Y'",
            "is_in_progress": "review_status == 'N'",
            "is_pay": "review_status == 'Y' and review_result == 'Y'",
            "is_returned": "review_status == 'Y' and review_result == 'N'",
            "is_ai_review": "review_status == 'Y' and review_mechanism == 'AI'",
            "is_human_review": "review_status == 'Y' and review_mechanism == 'H'"
        },
        "small_sample_warning": [
            "Companies with very small hc, such as TSK, should not be over-interpreted.",
            "When hc or completed_count is small, show both rate and absolute count."
        ]
    },
    "charting_guidance": {
        "recommended_charts": {
            "company_total_applications": {
                "chart_type": "bar",
                "x": "company_code",
                "y": "total_applications"
            },
            "company_submission_rate": {
                "chart_type": "bar",
                "x": "company_code",
                "y": "employee_submission_rate"
            },
            "pay_vs_return_by_company": {
                "chart_type": "stacked_bar",
                "x": "company_code",
                "y": [
                    "pay_count",
                    "return_count"
                ]
            },
            "return_rate_by_company": {
                "chart_type": "bar",
                "x": "company_code",
                "y": "average_return_rate"
            },
            "ai_vs_human_review": {
                "chart_type": "stacked_bar",
                "x": "company_code",
                "y": [
                    "ai_review_count",
                    "human_review_count"
                ]
            },
            "category_distribution": {
                "chart_type": "bar",
                "x": "application_category",
                "y": "application_count"
            }
        },
        "chart_rules": [
            "For rate charts, format y-axis as percentage.",
            "For small companies, show count labels together with percentages.",
            "Avoid pie charts when there are many companies.",
            "Use stacked bar charts for PAY vs RTN or AI vs H comparison."
        ]
    },
    "reporting_guidance": {
        "default_report_structure": [
            "Executive Summary",
            "Company Comparison",
            "Review Result Analysis",
            "AI Review Analysis",
            "Category Analysis",
            "Key Findings and Recommendations"
        ],
        "tone": "business analytical, concise, suitable for HR operations and management reporting"
    },
    "insight_guidance": {
        "analysis_principles": [
            "Always compare both absolute volume and rate.",
            "High return rate with low volume may not be operationally critical.",
            "High return count with moderate return rate may still be important because of workload impact.",
            "AI review rate should only be calculated among completed applications.",
            "In-progress applications may indicate workload backlog but not rejection risk."
        ],
        "cautions": [
            "Do not infer employee satisfaction directly from this dataset.",
            "Do not infer payment amount because there is no amount field.",
            "Do not infer trend because there is no application date field.",
            "Do not infer review duration because there is no submission or completion timestamp."
        ]
    },
    "data_limitations": {
        "missing_dimensions": [
            "No application date",
            "No payment amount",
            "No employee department",
            "No employee level",
            "No application reason",
            "No reviewer ID",
            "No review completion timestamp",
            "No policy version",
            "No country or location field"
        ],
        "not_supported_analysis": [
            "Trend analysis over time",
            "Seasonality analysis",
            "Payment amount analysis",
            "Review cycle time analysis",
            "Reviewer productivity analysis",
            "Department-level comparison",
            "Employee demographic analysis"
        ],
        "recommended_future_fields": [
            "application_date",
            "review_completed_date",
            "payment_amount",
            "department_code",
            "employee_grade",
            "reviewer_id",
            "return_reason_code",
            "policy_rule_id"
        ]
    },
    "statistics_reference": {
        "overall": {
            "hc": 91907,
            "submitter_count": 86483,
            "pay": 130313,
            "rtn": 4963,
            "total_applications_by_company_sum": 147526,
            "completed_applications": 135276,
            "in_progress_applications": 12250,
            "employee_submission_rate": 0.94098382060126,
            "average_return_rate": 0.03668795647417132,
            "completion_rate": 0.9169637894337269,
            "ai_review_target_rate_completed": 0.43
        },
        "company_statistics": {
            "TST": {
                "hc": 80919,
                "submitter_count": 77004,
                "pay": 114744,
                "rtn": 4285,
                "total_applications": 128922,
                "completed_applications": 119029,
                "in_progress_applications": 9893,
                "employee_submission_rate": 0.9516182849516183,
                "average_return_rate": 0.03599963034218552,
                "completion_rate": 0.9232636788135462
            },
            "TSC": {
                "hc": 2427,
                "submitter_count": 2399,
                "pay": 3680,
                "rtn": 168,
                "total_applications": 4184,
                "completed_applications": 3848,
                "in_progress_applications": 336,
                "employee_submission_rate": 0.988463123197363,
                "average_return_rate": 0.04365904365904366,
                "completion_rate": 0.9196940726577438
            },
            "TSA": {
                "hc": 2235,
                "submitter_count": 1392,
                "pay": 2176,
                "rtn": 62,
                "total_applications": 2699,
                "completed_applications": 2238,
                "in_progress_applications": 461,
                "employee_submission_rate": 0.6228187919463087,
                "average_return_rate": 0.02770330652368186,
                "completion_rate": 0.8291959985179697
            },
            "TSN": {
                "hc": 2121,
                "submitter_count": 2068,
                "pay": 3484,
                "rtn": 211,
                "total_applications": 4224,
                "completed_applications": 3695,
                "in_progress_applications": 529,
                "employee_submission_rate": 0.975011786892975,
                "average_return_rate": 0.0571041948579161,
                "completion_rate": 0.8747632575757576
            },
            "JSM": {
                "hc": 1852,
                "submitter_count": 1724,
                "pay": 3240,
                "rtn": 139,
                "total_applications": 3876,
                "completed_applications": 3379,
                "in_progress_applications": 497,
                "employee_submission_rate": 0.9308855291576674,
                "average_return_rate": 0.041136430896715,
                "completion_rate": 0.8717750257997936
            },
            "TWT": {
                "hc": 1051,
                "submitter_count": 711,
                "pay": 1061,
                "rtn": 58,
                "total_applications": 1266,
                "completed_applications": 1119,
                "in_progress_applications": 147,
                "employee_submission_rate": 0.6764985727878211,
                "average_return_rate": 0.05183199285075961,
                "completion_rate": 0.8838862559241706
            },
            "TSU": {
                "hc": 378,
                "submitter_count": 344,
                "pay": 562,
                "rtn": 11,
                "total_applications": 669,
                "completed_applications": 573,
                "in_progress_applications": 96,
                "employee_submission_rate": 0.91005291005291,
                "average_return_rate": 0.019197207678883072,
                "completion_rate": 0.8565022421524664
            },
            "TDI": {
                "hc": 348,
                "submitter_count": 316,
                "pay": 530,
                "rtn": 10,
                "total_applications": 727,
                "completed_applications": 540,
                "in_progress_applications": 187,
                "employee_submission_rate": 0.9080459770114943,
                "average_return_rate": 0.018518518518518517,
                "completion_rate": 0.7427785419532325
            },
            "TDJ": {
                "hc": 270,
                "submitter_count": 254,
                "pay": 404,
                "rtn": 10,
                "total_applications": 465,
                "completed_applications": 414,
                "in_progress_applications": 51,
                "employee_submission_rate": 0.9407407407407408,
                "average_return_rate": 0.024154589371980676,
                "completion_rate": 0.8903225806451613
            },
            "ESM": {
                "hc": 78,
                "submitter_count": 67,
                "pay": 97,
                "rtn": 3,
                "total_applications": 102,
                "completed_applications": 100,
                "in_progress_applications": 2,
                "employee_submission_rate": 0.8589743589743589,
                "average_return_rate": 0.03,
                "completion_rate": 0.9803921568627451
            },
            "TSE": {
                "hc": 72,
                "submitter_count": 60,
                "pay": 94,
                "rtn": 1,
                "total_applications": 106,
                "completed_applications": 95,
                "in_progress_applications": 11,
                "employee_submission_rate": 0.8333333333333334,
                "average_return_rate": 0.010526315789473684,
                "completion_rate": 0.8962264150943396
            },
            "TRJ": {
                "hc": 54,
                "submitter_count": 52,
                "pay": 101,
                "rtn": 2,
                "total_applications": 119,
                "completed_applications": 103,
                "in_progress_applications": 16,
                "employee_submission_rate": 0.9629629629629629,
                "average_return_rate": 0.019417475728155338,
                "completion_rate": 0.865546218487395
            },
            "TDC": {
                "hc": 53,
                "submitter_count": 46,
                "pay": 67,
                "rtn": 2,
                "total_applications": 79,
                "completed_applications": 69,
                "in_progress_applications": 10,
                "employee_submission_rate": 0.8679245283018868,
                "average_return_rate": 0.028985507246376812,
                "completion_rate": 0.8734177215189873
            },
            "TSJ": {
                "hc": 47,
                "submitter_count": 44,
                "pay": 70,
                "rtn": 1,
                "total_applications": 81,
                "completed_applications": 71,
                "in_progress_applications": 10,
                "employee_submission_rate": 0.9361702127659575,
                "average_return_rate": 0.014084507042253521,
                "completion_rate": 0.8765432098765432
            },
            "TSK": {
                "hc": 2,
                "submitter_count": 2,
                "pay": 3,
                "rtn": 0,
                "total_applications": 7,
                "completed_applications": 3,
                "in_progress_applications": 4,
                "employee_submission_rate": 1.0,
                "average_return_rate": 0.0,
                "completion_rate": 0.42857142857142855
            }
        },
        "note": "The user-provided Total row reported total_applications=140295, but the sum of company-level total_applications is 147526. The generated rawdata follows company-level details."
    },
    "llm_prompt_context": {
        "role": "This metadata describes a MongoDB dataset for an employee benefit application system called tFlex. Use it to answer business analysis questions, generate MongoDB aggregation pipelines, preprocess data, create charts, write reports, and generate insights.",
        "collections_summary": "There are two MongoDB collections.\n1. tflex_applications: one document per benefit application form. Fields: employee_id, company_code, application_no, application_category, review_status, review_result, review_mechanism.\n2. tflex_company_hc: one document per company. Fields: company_code, hc.",
        "kpi_summary": "Total applications = count of tflex_applications. Submitter count = distinct count of employee_id. Employee submission rate = distinct submitter count / hc. PAY = review_status=Y and review_result=Y. RTN = review_status=Y and review_result=N. Completed = review_status=Y. In-progress = review_status=N. Average return rate = RTN / completed. AI review rate = completed AI reviews / completed.",
        "query_rules": "Use company_code to join collections. Do not treat null review_result as rejection. Calculate return rate only among completed applications. Keep employee_id and application_no as strings. For small companies, show both percentage and absolute count.",
        "analysis_limitations": "No date field, so do not perform trend/monthly/seasonality analysis. No amount field, so do not analyze payment amount. No department field, so do not analyze department-level patterns. No review timestamp, so do not calculate review cycle time."
    }
}
