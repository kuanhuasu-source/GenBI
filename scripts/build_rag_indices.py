"""
scripts/build_rag_indices.py — v0.16.0+ (M6.1 Sprint 1 Day 3-4)

Build / rebuild RAG vector indices from:
- schema_index ← domain_metadata.collections.fields(per-domain)+
                  upload_metadata_versions.collections.fields(confirmed)
- kpi_index    ← domain_metadata.kpi_definitions(per-domain)+ uploads

對應 spec §8.1 / §8.2 + Sprint Plan Day 3-4。

# CLI

```bash
# 全 rebuild(所有 domain + 所有 index)
python scripts/build_rag_indices.py --full-rebuild

# 只重 schema_index
python scripts/build_rag_indices.py --index schema_index

# 只 build 某 domain
python scripts/build_rag_indices.py --domain tflex

# Dry-run(不寫 vector store,只列要 build 什麼)
python scripts/build_rag_indices.py --full-rebuild --dry-run
```

# 流程

```
1. 連 MongoDB(讀 domain_metadata / upload_metadata_versions)
2. 拿 schema fields → 組 content → embed → 寫 schema_index
3. 拿 kpi_definitions → 組 content → embed → 寫 kpi_index
4. 寫 rag_index_versions(challenger 狀態,等 A/B 跑完 promote)
5. 印 summary
```
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import config
from embedding_pipeline import (
    DEFAULT_DIM,
    DEFAULT_MODEL,
    get_embedding_pipeline,
)
from rag_index_repository import (
    RAGIndexRepository,
    make_chroma_factory,
    make_inmemory_factory,
)
from rag_index_versions_repository import RAGIndexVersionsRepository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


# ============================================================
# Content builders
# ============================================================
def _build_schema_field_content(
    domain: str, table: str, field_name: str, field_meta: dict,
) -> str:
    """組 schema_index doc 的 content 文字。"""
    parts = [f"{field_name}"]
    ftype = field_meta.get("type", "")
    role = field_meta.get("semantic_role") or field_meta.get("type", "")
    if ftype or role:
        parts.append(f"({ftype}, semantic_role={role})")
    if field_meta.get("description"):
        parts.append(f": {field_meta['description']}")
    allowed = field_meta.get("allowed_values")
    if isinstance(allowed, dict) and allowed:
        sample = ", ".join(f"{k}={v}" for k, v in list(allowed.items())[:5])
        parts.append(f" allowed: {sample}")
    elif isinstance(allowed, list) and allowed:
        parts.append(f" values: {', '.join(str(v) for v in allowed[:5])}")
    if field_meta.get("unit"):
        parts.append(f" unit={field_meta['unit']}")
    return " ".join(parts).strip()


def _build_kpi_content(domain: str, kpi_key: str, kpi_meta: dict) -> str:
    """組 kpi_index doc 的 content。"""
    parts = []
    if kpi_meta.get("name"):
        parts.append(f"{kpi_meta['name']} ({kpi_key})")
    else:
        parts.append(kpi_key)
    if kpi_meta.get("formula"):
        parts.append(f"= {kpi_meta['formula']}")
    if kpi_meta.get("important_note"):
        parts.append(f"⚠️ {kpi_meta['important_note']}")
    return " ".join(parts).strip()


# ============================================================
# Source readers
# ============================================================
def _read_domain_metadata(mongo_db, domain: str | None = None) -> list[dict]:
    """從 domain_metadata 拿 active metadata。

    v0.16.0+ fix:DB 空時 fallback 到 embedded_metadata.EMBEDDED_METADATA。
    對齊 PromptRepository.get_metadata 的「DB → embedded」路徑。

    Returns:
        list of {"domain": str, "metadata": dict} — 統一結構(metadata 為 flat dict,
        包含 collections / kpi_definitions / ...)。
    """
    coll = mongo_db[config.METADATA_COLLECTION]
    q: dict[str, Any] = {"is_active": True}
    if domain:
        q["domain"] = domain

    out: list[dict] = []
    for raw in coll.find(q):
        dom = raw.get("domain", "?")
        # 兩種 DB 結構都接:
        #   (A) flat:collections/kpi_definitions 在 top-level(PromptRepository 用)
        #   (B) nested:在 raw["metadata"](舊版可能用過)
        if "metadata" in raw and isinstance(raw["metadata"], dict):
            md = raw["metadata"]
        else:
            md = {k: v for k, v in raw.items()
                  if k not in ("_id", "domain", "version", "is_active",
                               "created_at", "created_by", "notes")}
        out.append({"domain": dom, "metadata": md})

    if out:
        logger.info(f"_read_domain_metadata: DB has {len(out)} active doc(s)")
        return out

    # Fallback to embedded(production tflex 通常走這條,DB 沒 seed)
    try:
        from embedded_metadata import EMBEDDED_METADATA
        for dom_name, md in EMBEDDED_METADATA.items():
            if domain and dom_name != domain:
                continue
            out.append({"domain": dom_name, "metadata": md})
        if out:
            logger.info(
                f"_read_domain_metadata: DB empty for domain={domain!r}, "
                f"fallback to embedded ({len(out)} domain(s))"
            )
    except ImportError as e:
        logger.warning(f"embedded_metadata import failed: {e}")

    return out


def _read_upload_metadata(mongo_db, dataset_id: str | None = None) -> list[dict]:
    """從 upload_metadata_versions 拿 confirmed metadata。"""
    coll = mongo_db["upload_metadata_versions"]
    q: dict[str, Any] = {"is_active": True, "confirmation_status": "confirmed"}
    if dataset_id:
        q["dataset_id"] = dataset_id
    return list(coll.find(q))


# ============================================================
# Index builders
# ============================================================
def build_schema_index(
    rag_repo: RAGIndexRepository,
    mongo_db,
    embedding_pipeline,
    domain: str | None = None,
    dry_run: bool = False,
) -> int:
    """建 schema_index。Returns doc count added."""
    if not dry_run:
        rag_repo.clear("schema_index")
    n = 0

    # Source 1: domain_metadata
    for md_doc in _read_domain_metadata(mongo_db, domain=domain):
        md = md_doc.get("metadata", {})
        dom = md_doc.get("domain") or md.get("dataset_id", "?")
        for table_id, table_meta in (md.get("collections") or {}).items():
            for field_name, field_meta in (table_meta.get("fields") or {}).items():
                content = _build_schema_field_content(
                    dom, table_id, field_name, field_meta,
                )
                doc_id = f"schema_{dom}_{table_id}_{field_name}"
                if dry_run:
                    logger.info(f"[DRY] would add {doc_id}: {content[:60]}")
                    n += 1
                    continue
                emb = embedding_pipeline.embed_one(content)
                rag_repo.add_doc(
                    "schema_index", doc_id, content, emb,
                    metadata={
                        "source_type": "static",
                        "domain": dom,
                        "table": table_id,
                        "field": field_name,
                        "semantic_role": field_meta.get("semantic_role", "unknown"),
                        "embedding_model": embedding_pipeline.model_name,
                    },
                )
                n += 1

    # Source 2: upload_metadata_versions(confirmed only)
    for upload_doc in _read_upload_metadata(mongo_db):
        md = upload_doc.get("metadata", {})
        dataset_id = upload_doc.get("dataset_id", "?")
        for table_id, table_meta in (md.get("collections") or {}).items():
            for field_name, field_meta in (table_meta.get("fields") or {}).items():
                content = _build_schema_field_content(
                    dataset_id, table_id, field_name, field_meta,
                )
                doc_id = f"schema_{dataset_id}_{table_id}_{field_name}"
                if dry_run:
                    logger.info(f"[DRY] would add {doc_id}(upload): {content[:60]}")
                    n += 1
                    continue
                emb = embedding_pipeline.embed_one(content)
                rag_repo.add_doc(
                    "schema_index", doc_id, content, emb,
                    metadata={
                        "source_type": "upload",
                        "domain": dataset_id,
                        "table": table_id,
                        "field": field_name,
                        "semantic_role": field_meta.get("semantic_role", "unknown"),
                        "embedding_model": embedding_pipeline.model_name,
                    },
                )
                n += 1

    return n


def build_anti_pattern_index(
    rag_repo: RAGIndexRepository,
    mongo_db,
    embedding_pipeline,
    domain: str | None = None,   # 接受但忽略,anti-pattern 跨 domain 共用
    dry_run: bool = False,
) -> int:
    """建 anti_pattern_index。

    Source 1: anti_pattern_seed.ANTI_PATTERNS(hand-curated from validators)
    Source 2: learning_instincts(若 collection 存在且有 verified docs)

    `domain` 參數忽略 — anti-pattern 跨 domain 共用(spec §9.2 anti_pattern
    slot 的 filter_keys 為空)。
    """
    if not dry_run:
        rag_repo.clear("anti_pattern_index")
    n = 0

    # Source 1: hand-curated seed
    try:
        from anti_pattern_seed import ANTI_PATTERNS
    except ImportError as e:
        logger.warning(f"anti_pattern_seed import failed: {e}")
        ANTI_PATTERNS = []
    for ap in ANTI_PATTERNS:
        doc_id = f"ap_seed_{ap['id']}"
        content = ap["content"]
        if dry_run:
            logger.info(f"[DRY] would add {doc_id}: {content[:60]}")
            n += 1
            continue
        emb = embedding_pipeline.embed_one(content)
        rag_repo.add_doc(
            "anti_pattern_index", doc_id, content, emb,
            metadata={
                "source_type": "seed",
                "applies_to_phase": ap.get("applies_to_phase", "?"),
                "tags": ap.get("tags", []),
                "embedding_model": embedding_pipeline.model_name,
            },
        )
        n += 1

    # Source 2: learning_instincts(active only,若 collection 存在)
    # v0.16.0+ M6.5 fix:GenBI 學習 pipeline 用 status='active' 標可信 instinct
    # (status='candidate' = 剛 cluster 出來等審;'deprecated' = 被矛盾推翻;
    #  'active' = 已 promoted 進 production rule set)
    try:
        coll = mongo_db["learning_instincts"]
        cursor = coll.find({"status": "active"})
        for inst in cursor:
            text = inst.get("rule") or inst.get("text") or ""
            if not text:
                continue
            inst_id = inst.get("instinct_id") or str(inst.get("_id"))
            doc_id = f"ap_learn_{inst_id}"
            # 規範化 phase 值對齊 anti_pattern_seed 的 "phase_X" 格式
            raw_phase = inst.get("phase", "?")
            if raw_phase in ("a", "phaseA", "phase_a", "A"):
                phase_norm = "phase_a"
            elif raw_phase in ("b", "phaseB", "phase_b", "B"):
                phase_norm = "phase_b"
            elif raw_phase in ("c", "phaseC", "phase_c", "C"):
                phase_norm = "phase_c"
            else:
                phase_norm = str(raw_phase)
            if dry_run:
                logger.info(f"[DRY] would add {doc_id}: {text[:60]}")
                n += 1
                continue
            emb = embedding_pipeline.embed_one(text)
            rag_repo.add_doc(
                "anti_pattern_index", doc_id, text, emb,
                metadata={
                    "source_type": "learning_instinct",
                    "applies_to_phase": phase_norm,
                    "instinct_id": inst_id,
                    "confidence": inst.get("confidence", 1.0),
                    "embedding_model": embedding_pipeline.model_name,
                },
            )
            n += 1
    except Exception as e:
        logger.info(f"learning_instincts 不可用或為空: {e}")

    return n


def build_chart_recipe_index(
    rag_repo: RAGIndexRepository,
    mongo_db,
    embedding_pipeline,
    domain: str | None = None,
    dry_run: bool = False,
) -> int:
    """建 chart_recipe_index。

    Source:domain_metadata.charting_guidance —
      - `recommended_charts`(per-intent ECharts spec)
      - `chart_rules`(general best-practice strings)

    Filter:by intent(recommended_charts 帶 intent key 或 chart_type)。
    chart_rules 沒 intent — 跨 intent 共用。
    """
    if not dry_run:
        rag_repo.clear("chart_recipe_index")
    n = 0

    for md_doc in _read_domain_metadata(mongo_db, domain=domain):
        md = md_doc.get("metadata", {})
        dom = md_doc.get("domain", "?")
        cg = md.get("charting_guidance") or {}

        # Source 1: recommended_charts(per-intent)
        recommended = cg.get("recommended_charts") or {}
        for chart_key, chart_spec in recommended.items():
            if not isinstance(chart_spec, dict):
                continue
            chart_type = chart_spec.get("chart_type", "?")
            x = chart_spec.get("x", "?")
            y = chart_spec.get("y", "?")
            content = (
                f"{chart_key}: chart_type={chart_type}, x={x}, y={y}"
            )
            # 加 extra fields(series, stack, group_by, etc.)
            extras = [f"{k}={v}" for k, v in chart_spec.items()
                       if k not in ("chart_type", "x", "y")]
            if extras:
                content += " · " + ", ".join(extras)

            doc_id = f"cr_{dom}_{chart_key}"
            if dry_run:
                logger.info(f"[DRY] would add {doc_id}: {content[:80]}")
                n += 1
                continue
            emb = embedding_pipeline.embed_one(content)
            rag_repo.add_doc(
                "chart_recipe_index", doc_id, content, emb,
                metadata={
                    "source_type": "recommended_chart",
                    "domain": dom,
                    "chart_key": chart_key,
                    "intent": chart_type,   # filter key in orchestrator
                    "embedding_model": embedding_pipeline.model_name,
                },
            )
            n += 1

        # Source 2: chart_rules(general strings,跨 intent 共用)
        rules = cg.get("chart_rules") or []
        if isinstance(rules, list):
            for i, rule in enumerate(rules):
                if not isinstance(rule, str) or not rule.strip():
                    continue
                content = f"Chart rule: {rule.strip()}"
                doc_id = f"cr_rule_{dom}_{i}"
                if dry_run:
                    logger.info(f"[DRY] would add {doc_id}: {content[:80]}")
                    n += 1
                    continue
                emb = embedding_pipeline.embed_one(content)
                rag_repo.add_doc(
                    "chart_recipe_index", doc_id, content, emb,
                    metadata={
                        "source_type": "chart_rule",
                        "domain": dom,
                        "rule_index": i,
                        # 沒 intent — chart_rules 跨 intent 通用
                        "embedding_model": embedding_pipeline.model_name,
                    },
                )
                n += 1

    return n


def build_few_shot_index(
    rag_repo: RAGIndexRepository,
    mongo_db,
    embedding_pipeline,
    domain: str | None = None,
    dry_run: bool = False,
    max_examples: int = 50,
    max_pipeline_chars: int = 600,
    rag_on_only: bool = True,
) -> int:
    """建 few_shot_index。Source:test_runs.case_results where status='pass'。

    每 (domain, query_first_60_chars) 只留最近一筆,避免同 query 跑多次塞滿 index。

    v0.16.0+ M6.5:`rag_on_only=True`(default)只從 RAG-on era runs 抽 few-shot。
    Sprint 3 結論:RAG-off era 的 pipeline 對 RAG-on inference 是 semantic mismatch
    (LLM 試圖 pattern-match 沒 RAG context 時的解法)。`test_runs.rag_enabled=True`
    的 run 才是「同 prompt regime 的成功案例」。

    需要 rag_enabled=False 的舊行為時:`rag_on_only=False`(eg bootstrap 階段,
    還沒任何 RAG-on test_run 可用)。
    """
    import json as _json
    if not dry_run:
        rag_repo.clear("few_shot_index")
    n = 0
    coll = mongo_db[config.TEST_RUNS_COLLECTION]
    q: dict[str, Any] = {}
    if domain:
        q["domain"] = domain
    if rag_on_only:
        # v0.16.0+ M6.5:只取 RAG-on 寫進來的 run(test_runner --rag-on)
        q["rag_enabled"] = True
    # 按 completed_at desc 取近的 run(避免太舊的 prompt 行為不一致)
    cursor = coll.find(q).sort("completed_at", -1).limit(20)

    seen_keys: set[tuple[str, str]] = set()
    for run in cursor:
        run_domain = run.get("domain", "?")
        for case in run.get("case_results", []):
            if case.get("status") != "pass":
                continue
            query = (case.get("query") or "").strip()
            if not query:
                continue
            dedup_key = (run_domain, query[:60])
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            # 取 Phase A pipeline(若有)做 example body
            phases = case.get("phases") or {}
            pipe_phase = phases.get("pipeline") or {}
            pipe_obj = pipe_phase.get("pipeline_obj") or pipe_phase.get("parsed")
            pipe_text = ""
            if pipe_obj:
                try:
                    pipe_text = _json.dumps(
                        pipe_obj, ensure_ascii=False,
                    )[:max_pipeline_chars]
                except Exception:
                    pipe_text = str(pipe_obj)[:max_pipeline_chars]
            intent = case.get("intent") or pipe_phase.get("intent", "?")

            parts = [f"Query: {query}"]
            if intent and intent != "?":
                parts.append(f"Intent: {intent}")
            if pipe_text:
                parts.append(f"Phase A pipeline: {pipe_text}")
            content = "\n".join(parts)

            case_id = case.get("case_id") or case.get("id") or "?"
            doc_id = f"fs_{run_domain}_{case_id}_{run['_id']}"

            if dry_run:
                logger.info(f"[DRY] would add {doc_id}: {content[:80]}")
                n += 1
                if n >= max_examples:
                    break
                continue

            # Embed key 用 query — query 自然語義最強
            emb = embedding_pipeline.embed_one(query)
            rag_repo.add_doc(
                "few_shot_index", doc_id, content, emb,
                metadata={
                    "source_type": "test_run_pass",
                    "domain": run_domain,
                    "case_id": case_id,
                    "intent": intent,
                    "run_id": str(run["_id"]),
                    "completed_at": str(run.get("completed_at", "")),
                    "embedding_model": embedding_pipeline.model_name,
                },
            )
            n += 1
            if n >= max_examples:
                break
        if n >= max_examples:
            break

    return n


def build_kpi_index(
    rag_repo: RAGIndexRepository,
    mongo_db,
    embedding_pipeline,
    domain: str | None = None,
    dry_run: bool = False,
) -> int:
    """建 kpi_index。"""
    if not dry_run:
        rag_repo.clear("kpi_index")
    n = 0

    # domain_metadata
    for md_doc in _read_domain_metadata(mongo_db, domain=domain):
        md = md_doc.get("metadata", {})
        dom = md_doc.get("domain") or md.get("dataset_id", "?")
        for kpi_key, kpi_meta in (md.get("kpi_definitions") or {}).items():
            content = _build_kpi_content(dom, kpi_key, kpi_meta)
            doc_id = f"kpi_{dom}_{kpi_key}"
            if dry_run:
                logger.info(f"[DRY] would add {doc_id}: {content[:60]}")
                n += 1
                continue
            emb = embedding_pipeline.embed_one(content)
            rag_repo.add_doc(
                "kpi_index", doc_id, content, emb,
                metadata={
                    "source_type": "static", "domain": dom,
                    "kpi_key": kpi_key,
                    "embedding_model": embedding_pipeline.model_name,
                },
            )
            n += 1

    # uploads
    for upload_doc in _read_upload_metadata(mongo_db):
        md = upload_doc.get("metadata", {})
        dataset_id = upload_doc.get("dataset_id", "?")
        for kpi_key, kpi_meta in (md.get("kpi_definitions") or {}).items():
            content = _build_kpi_content(dataset_id, kpi_key, kpi_meta)
            doc_id = f"kpi_{dataset_id}_{kpi_key}"
            if dry_run:
                logger.info(f"[DRY] would add {doc_id}(upload): {content[:60]}")
                n += 1
                continue
            emb = embedding_pipeline.embed_one(content)
            rag_repo.add_doc(
                "kpi_index", doc_id, content, emb,
                metadata={
                    "source_type": "upload", "domain": dataset_id,
                    "kpi_key": kpi_key,
                    "embedding_model": embedding_pipeline.model_name,
                },
            )
            n += 1

    return n


# ============================================================
# Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Build RAG indices")
    parser.add_argument("--full-rebuild", action="store_true",
                         help="rebuild all indices(clear + repopulate)")
    parser.add_argument("--index", choices=[
        "schema_index", "kpi_index", "few_shot_index",
        "anti_pattern_index", "chart_recipe_index",
    ], help="只 build 指定 index")
    parser.add_argument("--domain", help="只 build 指定 domain(skip 其他)")
    parser.add_argument("--dry-run", action="store_true",
                         help="不寫 DB,只列要 build 什麼")
    parser.add_argument("--persist-dir", default="./rag_indices",
                         help="Chroma persist directory")
    parser.add_argument(
        "--include-rag-off-runs",
        action="store_true",
        help="few_shot_index 也納入 RAG-off 時代的成功 run(bootstrap "
             "階段才開;沒 RAG-on run 可用時用)。預設只用 RAG-on 時代。",
    )
    parser.add_argument("--use-inmemory", action="store_true",
                         help="跑 InMemoryBackend(test 用,production 不該)")
    args = parser.parse_args()

    # MongoDB
    from pymongo import MongoClient
    try:
        client = MongoClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=config.MONGO_SERVER_SELECTION_TIMEOUT_MS,
        )
        client.admin.command("ping")
        mongo_db = client[config.MONGO_DB]
    except Exception as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        return 1

    # RAG repository
    if args.use_inmemory:
        factory = make_inmemory_factory()
        logger.info("Using InMemoryBackend(test mode)")
    else:
        factory = make_chroma_factory(args.persist_dir)
        logger.info(f"Using ChromaBackend(persist={args.persist_dir})")
    rag_repo = RAGIndexRepository(backend_factory=factory)

    # Embedding pipeline
    ep = get_embedding_pipeline()
    logger.info(f"Embedding model: {ep.model_name}(dim={ep.dim})")

    # Versions repository
    versions_repo = RAGIndexVersionsRepository(mongo_db)
    versions_repo.ensure_indexes()

    # Plan
    indices_to_build = []
    if args.full_rebuild:
        # v0.16.0+ M6.3:5 個 index 全部 build
        indices_to_build = [
            "schema_index", "kpi_index",
            "anti_pattern_index", "few_shot_index",
            "chart_recipe_index",
        ]
    elif args.index:
        indices_to_build = [args.index]
    else:
        logger.error("Need --full-rebuild or --index <name>")
        return 1

    # Build
    total_start = time.time()
    summary: dict[str, int] = {}
    for idx_name in indices_to_build:
        idx_start = time.time()
        logger.info(f"--- Building {idx_name} ---")
        if idx_name == "schema_index":
            n = build_schema_index(rag_repo, mongo_db, ep,
                                     domain=args.domain, dry_run=args.dry_run)
        elif idx_name == "kpi_index":
            n = build_kpi_index(rag_repo, mongo_db, ep,
                                  domain=args.domain, dry_run=args.dry_run)
        elif idx_name == "anti_pattern_index":
            n = build_anti_pattern_index(
                rag_repo, mongo_db, ep,
                domain=args.domain, dry_run=args.dry_run,
            )
        elif idx_name == "few_shot_index":
            n = build_few_shot_index(
                rag_repo, mongo_db, ep,
                domain=args.domain, dry_run=args.dry_run,
                rag_on_only=not args.include_rag_off_runs,
            )
        elif idx_name == "chart_recipe_index":
            n = build_chart_recipe_index(
                rag_repo, mongo_db, ep,
                domain=args.domain, dry_run=args.dry_run,
            )
        else:
            logger.warning(f"⚠️ {idx_name} build 尚未實作(後續 milestone 才做)")
            continue
        summary[idx_name] = n
        elapsed = time.time() - idx_start
        logger.info(f"✅ {idx_name}: {n} docs in {elapsed:.1f}s")

        # 寫 version doc(skip dry-run)
        if not args.dry_run and n > 0:
            v_id = versions_repo.create_version(
                index_name=idx_name,
                embedding_model=ep.model_name,
                embedding_dim=ep.dim,
                doc_count=n,
                status="champion",   # 首版直接 champion,後續更新才走 challenger
                notes=f"Built by build_rag_indices.py ({args.domain or 'all domains'})",
            )
            logger.info(f"   version doc id: {v_id}")

    total_elapsed = time.time() - total_start
    logger.info(f"\n══ Summary ══(total {total_elapsed:.1f}s)")
    for idx, n in summary.items():
        logger.info(f"  {idx}: {n} docs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
