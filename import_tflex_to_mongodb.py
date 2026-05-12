# -*- coding: utf-8 -*-
"""
Import tFlex synthetic CSV rawdata into MongoDB.

資料來源：
1. tflex_applications_rawdata_v2.csv
2. tflex_company_hc_rawdata_v2.csv

使用方式：
    pip install pymongo
    python import_tflex_to_mongodb.py

可用環境變數調整：
    MONGO_URI
    MONGO_DB
    MONGO_COLLECTION_APPLICATIONS
    MONGO_COLLECTION_COMPANY_HC
    APPLICATIONS_CSV_PATH
    COMPANY_HC_CSV_PATH
    IMPORT_MODE
    BATCH_SIZE

範例：
    set MONGO_URI=mongodb://localhost:27017/
    set MONGO_DB=tflex_demo
    set MONGO_COLLECTION_APPLICATIONS=tflex_applications
    set MONGO_COLLECTION_COMPANY_HC=tflex_company_hc
    set IMPORT_MODE=upsert
    python import_tflex_to_mongodb.py

PowerShell 範例：
    $env:MONGO_URI="mongodb://localhost:27017/"
    $env:MONGO_DB="tflex_demo"
    $env:IMPORT_MODE="drop_insert"
    python .\import_tflex_to_mongodb.py
"""

import csv
import os
from pathlib import Path
from typing import Dict, Iterable, List, Any

from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection


# ============================================================
# 1. 可調整參數區
# ============================================================

import config

MONGO_URI = config.MONGO_URI
DB_NAME = config.MONGO_DB
COLLECTION_APPLICATIONS = config.MONGO_COLL_APPLICATIONS
COLLECTION_COMPANY_HC = config.MONGO_COLL_COMPANY_HC

# 預設讀取 config.DATA_DIR 下的 CSV
APPLICATIONS_CSV_PATH = Path(os.getenv(
    "APPLICATIONS_CSV_PATH",
    str(config.DATA_DIR / "tflex_applications_rawdata_v2.csv")
))

COMPANY_HC_CSV_PATH = Path(os.getenv(
    "COMPANY_HC_CSV_PATH",
    str(config.DATA_DIR / "tflex_company_hc_rawdata_v2.csv")
))

# 匯入模式：
#   upsert      : 預設，依主鍵更新或新增，可重複執行
#   drop_insert : 匯入前清空 collection，然後重新 insert
IMPORT_MODE = os.getenv("IMPORT_MODE", "upsert").strip().lower()

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "5000"))


# ============================================================
# 2. 欄位轉換邏輯
# ============================================================

def clean_blank(value: str) -> Any:
    """
    CSV 空字串轉成 None，MongoDB 中會存成 null。
    """
    if value is None:
        return None

    value = value.strip()
    return value if value != "" else None


def transform_application_row(row: Dict[str, str]) -> Dict[str, Any]:
    """
    Table 1: tFlex 員工福利申請明細

    注意：
    - employee_id 必須保留為六碼字串
    - application_no 必須保留為八碼字串
    - review_status = N 時，review_result / review_mechanism 會是 None
    """
    return {
        "employee_id": row["employee_id"].strip(),
        "company_code": row["company_code"].strip(),
        "application_no": row["application_no"].strip(),
        "application_category": row["application_category"].strip(),
        "review_status": row["review_status"].strip(),
        "review_result": clean_blank(row.get("review_result", "")),
        "review_mechanism": clean_blank(row.get("review_mechanism", "")),
    }


def transform_company_hc_row(row: Dict[str, str]) -> Dict[str, Any]:
    """
    Table 2: 子公司 HC 參考表
    """
    return {
        "company_code": row["company_code"].strip(),
        "hc": int(row["hc"]),
    }


# ============================================================
# 3. MongoDB 匯入工具函式
# ============================================================

def iter_csv_rows(csv_path: Path) -> Iterable[Dict[str, str]]:
    """
    逐列讀取 CSV。
    utf-8-sig 可處理 Excel 常見 BOM。
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path.resolve()}")

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def chunks(items: Iterable[Dict[str, Any]], batch_size: int) -> Iterable[List[Dict[str, Any]]]:
    """
    將 iterator 切成固定 batch，避免一次塞太多資料進記憶體或 MongoDB。
    """
    batch = []
    for item in items:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []

    if batch:
        yield batch


def import_documents(
    collection: Collection,
    csv_path: Path,
    transform_func,
    unique_key: str,
    import_mode: str,
    batch_size: int,
) -> int:
    """
    將 CSV 匯入指定 collection。

    import_mode:
      - drop_insert: 先刪除 collection 內容，再 insert_many
      - upsert: 依 unique_key 進行 upsert，可安全重跑
    """
    total_count = 0

    rows = (transform_func(row) for row in iter_csv_rows(csv_path))

    if import_mode == "drop_insert":
        collection.delete_many({})

        for batch in chunks(rows, batch_size):
            if batch:
                collection.insert_many(batch, ordered=False)
                total_count += len(batch)

    elif import_mode == "upsert":
        for batch in chunks(rows, batch_size):
            operations = [
                UpdateOne(
                    {unique_key: doc[unique_key]},
                    {"$set": doc},
                    upsert=True
                )
                for doc in batch
            ]

            if operations:
                collection.bulk_write(operations, ordered=False)
                total_count += len(operations)

    else:
        raise ValueError(
            f"Unsupported IMPORT_MODE: {import_mode}. "
            "Allowed values: upsert, drop_insert"
        )

    return total_count


def create_indexes(applications_col: Collection, company_hc_col: Collection) -> None:
    """
    建立常用查詢索引。
    """
    applications_col.create_index("application_no", unique=True)
    applications_col.create_index("employee_id")
    applications_col.create_index("company_code")
    applications_col.create_index("application_category")
    applications_col.create_index("review_status")
    applications_col.create_index("review_result")
    applications_col.create_index("review_mechanism")

    # 常見分析查詢組合
    applications_col.create_index([
        ("company_code", 1),
        ("review_status", 1),
        ("review_result", 1),
    ])

    applications_col.create_index([
        ("company_code", 1),
        ("review_mechanism", 1),
    ])

    company_hc_col.create_index("company_code", unique=True)


def print_import_summary(
    applications_col: Collection,
    company_hc_col: Collection,
    app_imported_count: int,
    hc_imported_count: int,
) -> None:
    """
    顯示匯入後的基本驗證資訊。
    """
    total_applications = applications_col.count_documents({})
    total_companies = company_hc_col.count_documents({})

    completed_count = applications_col.count_documents({"review_status": "Y"})
    in_progress_count = applications_col.count_documents({"review_status": "N"})
    pay_count = applications_col.count_documents({
        "review_status": "Y",
        "review_result": "Y",
    })
    rtn_count = applications_col.count_documents({
        "review_status": "Y",
        "review_result": "N",
    })
    ai_count = applications_col.count_documents({
        "review_status": "Y",
        "review_mechanism": "AI",
    })
    h_count = applications_col.count_documents({
        "review_status": "Y",
        "review_mechanism": "H",
    })

    ai_rate = ai_count / completed_count if completed_count else 0

    print("\n========== tFlex MongoDB Import Summary ==========")
    print(f"MongoDB URI                 : {MONGO_URI}")
    print(f"Database                    : {DB_NAME}")
    print(f"Applications Collection     : {COLLECTION_APPLICATIONS}")
    print(f"Company HC Collection       : {COLLECTION_COMPANY_HC}")
    print(f"Import Mode                 : {IMPORT_MODE}")
    print("--------------------------------------------------")
    print(f"Applications processed      : {app_imported_count:,}")
    print(f"Company HC processed        : {hc_imported_count:,}")
    print(f"Applications in MongoDB     : {total_applications:,}")
    print(f"Companies in MongoDB        : {total_companies:,}")
    print("--------------------------------------------------")
    print(f"Completed review            : {completed_count:,}")
    print(f"In-progress review          : {in_progress_count:,}")
    print(f"PAY                         : {pay_count:,}")
    print(f"RTN                         : {rtn_count:,}")
    print(f"AI reviewed completed cases : {ai_count:,}")
    print(f"Human reviewed completed    : {h_count:,}")
    print(f"AI review rate completed    : {ai_rate:.4%}")
    print("==================================================\n")


# ============================================================
# 4. 主程式
# ============================================================

def main() -> None:
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]

    applications_col = db[COLLECTION_APPLICATIONS]
    company_hc_col = db[COLLECTION_COMPANY_HC]

    create_indexes(applications_col, company_hc_col)

    hc_imported_count = import_documents(
        collection=company_hc_col,
        csv_path=COMPANY_HC_CSV_PATH,
        transform_func=transform_company_hc_row,
        unique_key="company_code",
        import_mode=IMPORT_MODE,
        batch_size=BATCH_SIZE,
    )

    app_imported_count = import_documents(
        collection=applications_col,
        csv_path=APPLICATIONS_CSV_PATH,
        transform_func=transform_application_row,
        unique_key="application_no",
        import_mode=IMPORT_MODE,
        batch_size=BATCH_SIZE,
    )

    create_indexes(applications_col, company_hc_col)

    print_import_summary(
        applications_col=applications_col,
        company_hc_col=company_hc_col,
        app_imported_count=app_imported_count,
        hc_imported_count=hc_imported_count,
    )

    client.close()


if __name__ == "__main__":
    main()
