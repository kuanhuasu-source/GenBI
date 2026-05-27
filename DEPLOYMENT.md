# GenBI · Production / Air-Gap Deployment Guide

For ops/SRE rolling GenBI v0.16+ into a production or air-gap environment.

對應 milestone:M6.6(Sprint 4)。LLM 自己的部署(vLLM / Ollama)看
`DEPLOY_A100_QWEN36_27B.md`,這份 doc 只談 **GenBI app + RAG 基建**。

---

## 0. Inventory:GenBI v0.16+ runtime needs

| Component | Required | Notes |
|---|---|---|
| Python 3.10+ | ✓ | 3.11 / 3.12 也測過 |
| Streamlit + deps | ✓ | `requirements.txt` |
| MongoDB | ✓ | 7.x;集群 / standalone 都行 |
| LLM 服務(Ollama / vLLM) | ✓ | 跑 qwen3-coder:30b(或同等 30B coder model) |
| **sentence-transformers + chromadb** | ✓ v0.16+ | RAG 必須;~700MB venv 含 torch |
| **all-MiniLM-L6-v2 model files** | ✓ v0.16+ | ~92MB,放在 `GENBI_EMBEDDING_MODEL` 指向的目錄 |
| Chroma persist dir(`./rag_indices`) | ✓ v0.16+ | RAG indices 落地處,**要 backup** |

Disk:核心 ~2GB(含 venv + sentence-transformers + model);LLM 自己另算。
Memory:streamlit + chromadb embedded mode 跑得起來 ~2-4GB。LLM 自己另算。

---

## 1. 三種部署型態 — 挑一個

### 1.1 純 venv + systemd(最常見、最簡單)

適合:單機 Linux server,可長期跑 streamlit + cron 跑 build_rag_indices。

```bash
# 部署機跑
cd /opt
git clone <internal-repo>/GenBI.git
cd GenBI
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# 設 .env(見 §3)
# 第一次手動跑驗證 OK 之後,寫 systemd unit(見 §4)
```

### 1.2 Air-gap wheel cache + venv

適合:部署機無 internet,需從中介機搬 wheel + model。

```bash
# 中介機(有 internet,同架構)
bash scripts/build_wheel_cache.sh
# 把 ./wheels + ./all-MiniLM-L6-v2 + 整個 repo tar 起來
tar czf genbi_deploy.tar.gz GenBI/ wheels/ all-MiniLM-L6-v2/

# 部署機(離線)
tar xzf genbi_deploy.tar.gz
cd GenBI
python3.11 -m venv .venv
source .venv/bin/activate
pip install --no-index --find-links=../wheels -r requirements.txt
# .env 指向 ../all-MiniLM-L6-v2(SPRINT2_RUN_GUIDE.md §7.4)
```

### 1.3 Docker(若你的環境本來就 docker-native)

```dockerfile
# Dockerfile sketch — 視 base image / registry 調
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# 對 air-gap docker:先 build image,然後 docker save | scp | docker load
# Embedding model 走 volume mount,不打進 image
VOLUME /models /app/rag_indices
CMD ["streamlit", "run", "app.py", "--server.port=8501"]
```

Docker 路徑只列 sketch — production 細節依公司 registry / k8s policy 而定。
本 doc 不再展開(若需要可單獨開 issue 討論)。

---

## 2. 預備工作 checklist

```
☐ Python venv 建好
☐ requirements.txt 全裝(或 wheel cache 全裝)
☐ MongoDB 連得到(genbi DB 存在,集合 indexes 自動建)
☐ LLM 服務跑著(curl http://localhost:11434/api/tags 回 OK)
☐ all-MiniLM-L6-v2 model 在指定路徑
☐ GENBI_EMBEDDING_MODEL env 指向那路徑
☐ HF_HUB_OFFLINE=1(air-gap 環境)
☐ scripts/verify_embedding_model.py 跑過 ✅
☐ scripts/build_rag_indices.py --full-rebuild 跑過(./rag_indices/ 生成)
☐ scripts/inspect_rag_retrieval.py 確認 5 個 index 都有 docs + hit 健康
```

---

## 3. `.env` 範本(production)

```bash
# ─── LLM 服務 ───
OLLAMA_URL=http://localhost:11434/v1/chat/completions
OLLAMA_MODEL=qwen3-coder:30b
OLLAMA_TIMEOUT=180

# ─── MongoDB ───
MONGO_URI=mongodb://localhost:27017
MONGO_DB=genbi

# ─── Prompt repository ───
GENBI_PROMPT_REPO=true                # DB-driven prompt(已有 seed 跑過)

# ─── v0.16 RAG ───
GENBI_RAG_ENABLED=true                # Phase 0/A/D RAG(Sprint 2 champion)
GENBI_RAG_PHASE_BC=false              # Phase B/C RAG(Sprint 3 deferred)

# ── Embedding backend ── 兩個選一:

# Option A · 本地 sentence-transformers(v0.16.0 default)
# GENBI_EMBEDDING_BACKEND=local
# GENBI_EMBEDDING_MODEL=/opt/genbi/models/all-MiniLM-L6-v2
# HF_HUB_OFFLINE=1                    # ★ air-gap 必設
# TRANSFORMERS_OFFLINE=1

# Option B · HTTP-served bge-m3(v0.16.1+,production 推薦)
# 跟 LLM 走同一條服務:Ollama dev / vLLM prod 都吃 OpenAI-compatible /v1/embeddings
GENBI_EMBEDDING_BACKEND=http
GENBI_EMBEDDING_API_URL=http://localhost:11434/v1/embeddings   # Ollama default
GENBI_EMBEDDING_MODEL=bge-m3
GENBI_EMBEDDING_API_KEY=ollama        # Ollama 任意值;vLLM 看部署設定
GENBI_EMBEDDING_BATCH_SIZE=64
GENBI_EMBEDDING_TIMEOUT_S=30

# production(vLLM):
# GENBI_EMBEDDING_API_URL=http://vllm-embed-host:8000/v1/embeddings

# ─── Thinking model ───
LLM_DISABLE_THINKING=false            # qwen3-coder 不需要(預設 false)

# ─── Telemetry / opts ───
GENBI_TASK_TRACE=true                 # 寫 task_traces collection
```

---

## 4. systemd unit 範本

`/etc/systemd/system/genbi-app.service`:

```ini
[Unit]
Description=GenBI Streamlit app
After=network.target mongod.service

[Service]
Type=simple
User=genbi
WorkingDirectory=/opt/GenBI
EnvironmentFile=/opt/GenBI/.env
ExecStart=/opt/GenBI/.venv/bin/streamlit run app.py \
    --server.port=8501 \
    --server.headless=true \
    --browser.gatherUsageStats=false
Restart=on-failure
RestartSec=10
StandardOutput=append:/var/log/genbi/app.log
StandardError=append:/var/log/genbi/app.err.log

[Install]
WantedBy=multi-user.target
```

```bash
sudo mkdir -p /var/log/genbi && sudo chown genbi:genbi /var/log/genbi
sudo systemctl daemon-reload
sudo systemctl enable --now genbi-app
sudo systemctl status genbi-app
```

定時重建 RAG index(每天 04:00 把當日新增的 confirmed metadata + test_runs
納入 index)— `/etc/systemd/system/genbi-rag-rebuild.timer`:

```ini
[Unit]
Description=Daily RAG index rebuild

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

`/etc/systemd/system/genbi-rag-rebuild.service`:

```ini
[Unit]
Description=GenBI RAG index rebuild
After=mongod.service

[Service]
Type=oneshot
User=genbi
WorkingDirectory=/opt/GenBI
EnvironmentFile=/opt/GenBI/.env
ExecStart=/opt/GenBI/.venv/bin/python scripts/build_rag_indices.py \
    --full-rebuild --domain tflex
ExecStartPost=/opt/GenBI/scripts/backup_rag_indices.sh backup \
    /opt/GenBI/rag_indices /opt/GenBI/backups
StandardOutput=append:/var/log/genbi/rag-rebuild.log
```

```bash
sudo systemctl enable --now genbi-rag-rebuild.timer
sudo systemctl list-timers | grep genbi
```

---

## 5. Backup / restore

```bash
# 手動 backup
bash scripts/backup_rag_indices.sh backup /opt/GenBI/rag_indices /opt/GenBI/backups
# 列舊 backup
bash scripts/backup_rag_indices.sh list /opt/GenBI/backups
# Restore 某 backup
bash scripts/backup_rag_indices.sh restore /opt/GenBI/backups/rag_indices_20260601_040012.tar.gz
```

**保留策略**(在 cron / systemd timer 內加):

```bash
# 只留近 14 天
find /opt/GenBI/backups -name "rag_indices_*.tar.gz" -mtime +14 -delete
```

MongoDB 也要 backup(本 doc 不展開,用 `mongodump` 走標準流程)。

---

## 6. Embedding model 升級流程

### 6.1 切換 backend(local sentence-transformers → HTTP Ollama/vLLM)

v0.16.1+ 推薦走 **HTTP backend** — 跟 LLM 共用同一條服務,deployment 一致。

**Dev(Ollama):**
```bash
# 一次性 pull
ollama pull bge-m3

# Ollama 起來後 /v1/embeddings 自動可用(走 OpenAI-compat path)
curl http://localhost:11434/v1/embeddings -d '{
    "model": "bge-m3", "input": "hello"
}' | head -c 200    # 確認 200 OK + embedding 數字

# .env 切過去
GENBI_EMBEDDING_BACKEND=http
GENBI_EMBEDDING_API_URL=http://localhost:11434/v1/embeddings
GENBI_EMBEDDING_MODEL=bge-m3

# bge-m3 是 1024-dim,跟 all-MiniLM(384-dim)不相容 — 必須整套 rebuild
rm -rf rag_indices
python scripts/build_rag_indices.py --full-rebuild --domain tflex
python scripts/inspect_rag_retrieval.py   # 確認 5 個 index 都重 build 上來
```

**Production(vLLM):**
```bash
# vLLM 啟動 embedder 模式
vllm serve BAAI/bge-m3 \
    --task embed \
    --served-model-name bge-m3 \
    --port 8000

# .env
GENBI_EMBEDDING_BACKEND=http
GENBI_EMBEDDING_API_URL=http://<vllm-host>:8000/v1/embeddings
GENBI_EMBEDDING_MODEL=bge-m3
GENBI_EMBEDDING_API_KEY=<production-key-if-set>
```

vLLM 多卡跑 bge-m3:加 `--tensor-parallel-size 2`(看卡數)。bge-m3 模型大小
~2.3GB,單張 A100 80GB 跑得很寬鬆;若跟 LLM 同卡,記得 `--gpu-memory-utilization 0.3`
留空間給 LLM。

### 6.2 切 embedding model(同 backend 內)

當你決定從 `all-MiniLM-L6-v2`(384-dim)切到 e.g. `BAAI/bge-base-zh`(768-dim):

```bash
# 1. 在中介機抓新模型(有 internet)
huggingface-cli download BAAI/bge-base-zh \
    --local-dir ./bge-base-zh \
    --local-dir-use-symlinks False

# 2. rsync 到部署機 → /opt/genbi/models/bge-base-zh

# 3. 改 .env(temporarily 留舊 model 設定,跑完 validation 才切)
# (testing)
GENBI_EMBEDDING_MODEL=/opt/genbi/models/bge-base-zh

# 4. 重建 indices(舊的不能用 — dim 不同)
python scripts/build_rag_indices.py --full-rebuild --domain tflex

# 5. A/B 驗證新 model
python test_runner.py --domain tflex --rag-on
# 比對:跟舊 model 的 baseline pass-rate / cost / wall time

# 6. 若新 model 跑 worse → 改回 .env;舊 rag_indices/ 保留(從 backup restore)
# 若 better → systemctl restart genbi-app 切換生效
```

⚠️ **dim 不同的 model 不能跟舊 indices 混用** — `EmbeddingPipeline.embed` 出來
的向量維度跟 Chroma collection 建時的 dim 必須一致,否則 search 直接 ValueError。
Embed model 一改,index 一定要全部 rebuild。

---

## 7. Monitoring & sanity checks

`scripts/inspect_rag_retrieval.py` 可寫成 cron job 自動跑代表 query,
若 PART 1 出現 `⚠️ 空` → 立刻 alert(MongoDB / Chroma 異常)。

```bash
# /etc/cron.hourly/genbi-rag-health
#!/bin/bash
cd /opt/GenBI
source .venv/bin/activate
source .env && export $(grep -v '^#' .env | xargs)
OUTPUT=$(python scripts/inspect_rag_retrieval.py --query "健康檢查" --skip-sweep 2>&1)
if echo "$OUTPUT" | grep -q "⚠️ 空"; then
    echo "$OUTPUT" | mail -s "[ALERT] GenBI RAG index empty" sre@example.com
fi
```

更完整的 metric(per-phase pass-rate / token usage / RAG hit rate)透過
`test_runs` collection 跑 daily aggregation(M6.5 self-learning 順帶把這
些 metric streaming 進 `learning_observations`)。

---

## 8. 緊急 rollback 流程

如果 v0.16 RAG 上線後出問題:

```bash
# Step 1:把 RAG 關掉(立即降回 v0.15 行為,byte-equal)
sudo sed -i 's/^GENBI_RAG_ENABLED=true/GENBI_RAG_ENABLED=false/' /opt/GenBI/.env
sudo systemctl restart genbi-app

# Step 2:確認 prompt 完全沒帶 RAG context
# (rag_<slot> 全空,Jinja2 {%- if %} block 全 collapse)

# Step 3:再深度 rollback(若 RAG 機制 import 都炸了)
# 退到 v0.15 標籤
cd /opt/GenBI
git fetch --tags
git checkout v0.15.0
.venv/bin/pip install -r requirements.txt    # 退到 v0.15 deps
sudo systemctl restart genbi-app
```

freeze-clause(spec §10.2 + 470 unit tests)保證 Step 1 立即生效,
不必動 code 就把 RAG 完全停掉。

---

## 9. 第一次部署檢核清單

```
☐ python -m venv .venv && source .venv/bin/activate
☐ pip install -r requirements.txt(或 --find-links=./wheels)
☐ python scripts/verify_embedding_model.py(model 載得起來)
☐ python -c "from pymongo import MongoClient; MongoClient().admin.command('ping')"
☐ curl http://localhost:11434/api/tags(LLM 服務)
☐ python scripts/build_rag_indices.py --full-rebuild --domain tflex
☐ python scripts/inspect_rag_retrieval.py(5 個 index 都有 docs)
☐ python test_runner.py --domain tflex --rag-on(跑一次 A/B 確認)
☐ systemctl status genbi-app(若用 systemd)
☐ 開 streamlit UI 試一個 query 確認 page 正常
☐ scripts/backup_rag_indices.sh backup(留第一份 baseline backup)
☐ systemctl list-timers | grep genbi(rebuild timer 有跑)
```
