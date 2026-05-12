# -*- coding: utf-8 -*-
"""
通用性測試用的「假電商訂單」metadata。

結構與 tflex_task_metadata_agent_v3.TASK_METADATA 完全一致,但 domain 完全不同。
透過此 metadata 驗證 llm_service.py 沒有任何 tFlex 硬編殘留 — 若 LLM 在 prompt 流程中
還會冒出 tFlex 欄位名 (company_code, review_status, average_return_rate 等),
代表通用化沒做乾淨。

商業情境設定:
- 一個多通路電商平台,訂單 (orders) 加產品 (products) 兩張表
- 訂單可能已付款 / 未付款,已付款可能 shipped / returned
- 用 channel (web/mobile/store) 與 product category 做維度分析
"""

ECOMMERCE_METADATA = {
    "metadata_version": "test_ecommerce_v1",
    "dataset_id": "ecommerce_orders_demo",
    "dataset_name": "E-commerce Order Dataset",
    "generated_at": "2026-05-12T00:00:00",
    "purpose": "Generality regression test — should NOT mention any tFlex term.",
    "recommended_mongodb": {
        "database": "shop_demo",
        "collections": {
            "orders": "orders",
            "products": "products",
        },
        "join_key": "product_id",
    },
    "business_context": {
        "system_name": "MyShop",
        "domain": "E-commerce order management",
        "business_description": (
            "Multi-channel e-commerce platform. Each order belongs to one customer "
            "and one product. Orders may be paid or unpaid; paid orders may be shipped "
            "or returned by the customer."
        ),
        "business_grain": {
            "orders": "one document per order line",
            "products": "one document per product SKU",
        },
        "main_business_questions": [
            "Which sales channel produces the most revenue?",
            "What is the return rate by product category?",
            "Which products are most frequently returned?",
        ],
    },
    "collections": {
        "orders": {
            "description": "Order-level transaction records",
            "grain": "one document per order line",
            "primary_key": "order_id",
            "fields": {
                "order_id": {
                    "type": "string",
                    "description": "Unique order identifier. Keep as string.",
                },
                "customer_id": {
                    "type": "string",
                    "description": "Customer identifier. Keep as string.",
                },
                "product_id": {
                    "type": "string",
                    "description": "Product SKU; joins to products.",
                    "join_to": "products.product_id",
                },
                "channel": {
                    "type": "string",
                    "description": "Sales channel.",
                    "allowed_values": ["web", "mobile", "store"],
                },
                "order_status": {
                    "type": "string",
                    "description": "Y = paid, N = pending payment.",
                    "allowed_values": {"Y": "paid", "N": "pending"},
                },
                "shipment_status": {
                    "type": "string_or_null",
                    "description": "Y = shipped, N = returned, null = not yet processed.",
                    "allowed_values": {
                        "Y": "shipped",
                        "N": "returned",
                        "null": "not processed",
                    },
                },
                "quantity": {
                    "type": "integer",
                    "description": "Quantity ordered (>=1).",
                },
                "unit_price": {
                    "type": "number",
                    "description": "Unit price at time of order.",
                },
            },
        },
        "products": {
            "description": "Product reference table",
            "grain": "one document per SKU",
            "primary_key": "product_id",
            "fields": {
                "product_id": {
                    "type": "string",
                    "description": "Product SKU.",
                },
                "category": {
                    "type": "string",
                    "description": "Product category.",
                    "allowed_values": ["Apparel", "Electronics", "Books", "Home"],
                },
                "list_price": {
                    "type": "number",
                    "description": "List price for the product.",
                },
            },
        },
    },
    "relationships": [
        {
            "type": "many_to_one",
            "from_collection": "orders",
            "from_field": "product_id",
            "to_collection": "products",
            "to_field": "product_id",
            "description": "Many orders per product SKU.",
        }
    ],
    "kpi_definitions": {
        "total_orders": {
            "name": "Total orders",
            "formula": "count of documents in orders",
        },
        "paid_count": {
            "name": "Paid orders",
            "formula": "count where order_status='Y'",
        },
        "shipped_count": {
            "name": "Shipped orders",
            "formula": "count where order_status='Y' and shipment_status='Y'",
        },
        "return_count": {
            "name": "Returned orders",
            "formula": "count where order_status='Y' and shipment_status='N'",
        },
        "return_rate": {
            "name": "Return rate",
            "formula": "return_count / (shipped_count + return_count)",
            "important_note": "Denominator is processed orders only (Y or N), not all paid.",
        },
        "total_revenue": {
            "name": "Total revenue",
            "formula": "sum(quantity * unit_price) where order_status='Y'",
        },
        "avg_order_value": {
            "name": "Average order value",
            "formula": "total_revenue / paid_count",
        },
        "channel_share": {
            "name": "Channel volume share",
            "formula": "count by channel / total_orders",
        },
    },
    "data_limitations": {
        "missing_dimensions": [
            "No order timestamp",
            "No customer demographics (age/gender)",
            "No shipping address / region",
            "No customer feedback / rating",
        ],
        "not_supported_analysis": [
            "Time trend analysis",
            "Demographic segmentation",
            "Geographic analysis",
            "Customer satisfaction analysis",
        ],
    },
    "charting_guidance": {
        "recommended_charts": {
            "channel_total_orders": {
                "chart_type": "bar",
                "x": "channel",
                "y": "total_orders",
            },
            "category_shipped_vs_returned": {
                "chart_type": "stacked_bar",
                "x": "category",
                "y": ["shipped_count", "return_count"],
            },
            "category_return_rate": {
                "chart_type": "bar",
                "x": "category",
                "y": "return_rate",
            },
            "channel_category_revenue": {
                "chart_type": "heatmap",
                "x": "category",
                "y": "channel",
                "value": "total_revenue",
            },
        },
        "chart_rules": [
            "For rate charts, format y-axis as percentage.",
            "Use stacked bar for shipped vs return comparison.",
            "Avoid pie charts when comparing many categories.",
        ],
    },
}
