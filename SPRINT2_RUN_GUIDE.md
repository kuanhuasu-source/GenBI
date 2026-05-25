# Sprint 2 / M6.4 · RAG A/B Run Guide

Side-by-side comparison run:**RAG OFF baseline** vs **RAG ON challenger**。
全程在你 Mac 跑(sandbox 沒 Ollama),約 30-50 分鐘。

---

## 0. 前置確認

```bash
cd /Users/kururu/Documents/Claude/Projects/GenBI

# Ollama 跑著
curl -s http://localhost:11434/api/tags | head

# MongoDB 跑著
mongosh --quiet --eval "db.runCommand({ping:1})"

# 確認當前 ollama model = qwen3-coder:30b(per .env)
grep OLLAMA_MODEL .env
```

如果 ollama / mongo 任何一個沒跑,先起。

---

## 1. 一次性安裝(只第一次)

```bash
# 第一次跑要載 sentence-transformers(~90MB,純 CPU 跑)
# chromadb 走 embedded mode(無 server),也是純 pip 裝
pip install sentence-transformers chromadb

# 確認可 import + 模型可下載
python -c "
from sentence_transformers import SentenceTransformer
m = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
v = m.encode(['hello'])
print('OK · dim =', v.shape[1])
"
# 預期:OK · dim = 384(第一次會 download ~90MB,後續走 ~/.cache 即時)
```

**離線部署準備**(可選,Sprint 4 才用):
模型已 cache 進 `~/.cache/huggingface/hub/`,要搬到地端伺服器時整個資料夾 rsync 即可。

---

## 2. Build 真實 RAG 索引

從 MongoDB 的 `domain_metadata`(tflex active version)抽 schema fields + KPI definitions,
embed 後寫進 `./rag_indices/`(Chroma persist directory)。

```bash
# Dry-run 先看會 build 什麼(零寫入)
python scripts/build_rag_indices.py --full-rebuild --domain tflex --dry-run

# 真 build(會 download model + 寫 chroma file)
python scripts/build_rag_indices.py --full-rebuild --domain tflex

# 預期輸出尾部:
#   ✅ schema_index: 35-60 docs in 2-5s
#   ✅ kpi_index:    8-15 docs in <1s
#   version doc id: ObjectId(...)
```

驗證:

```bash
ls -la rag_indices/
# 該看到 chroma.sqlite3 + 一些 dirs

# 看 rag_index_versions 寫進去了
mongosh genbi --quiet --eval "db.rag_index_versions.find({status:'champion'}).pretty()"
```

---

## 3. A/B 跑 test_runner

**重要**:兩次跑用同一份 test_cases(同 filter),才有可比性。

### 3.1 RAG OFF(baseline)

```bash
# 全 cases,RAG off,不寫 baseline 旗
python test_runner.py --domain tflex 2>&1 | tee runs/rag_off_$(date +%Y%m%d_%H%M).log

# 跑完看 summary
mongosh genbi --quiet --eval '
  db.test_runs.findOne({rag_enabled:false}, {summary:1, started_at:1, total_wall_s:1})
'

# 備份 JSON
cp test_results.json runs/rag_off_$(date +%Y%m%d_%H%M).json
```

預期輸出含:
```
RAG       : OFF(--rag-on 啟用)
...
✅ 通過 XX / 26
```

### 3.2 RAG ON(challenger)

```bash
# 同 cases,RAG on
python test_runner.py --domain tflex --rag-on 2>&1 | tee runs/rag_on_$(date +%Y%m%d_%H%M).log

# 備份
cp test_results.json runs/rag_on_$(date +%Y%m%d_%H%M).json
```

預期輸出含:
```
RAG       : ON · indices=./rag_indices
            embedder=sentence-transformers/all-MiniLM-L6-v2(dim=384)
            schema_index doc count: 4X
...
```

---

## 4. 對比

### 4.1 Pass-rate diff

```bash
# 兩次 summary 並排
mongosh genbi --quiet --eval '
  db.test_runs.find({
    domain:"tflex",
    completed_at:{$gte:new Date(Date.now() - 6*3600*1000)}
  }).sort({completed_at:-1}).limit(2).forEach(r => {
    print(`rag=${r.rag_enabled} · pass=${r.summary.passed}/${r.summary.total_cases} · ` +
          `wall=${r.total_wall_s}s · tokens=${r.summary.total_tokens}`);
  });
'
```

### 4.2 Per-case diff

```bash
# 哪些 case 在 RAG-on 變過 / 變壞了
python -c "
import json
off = json.load(open('runs/rag_off_<時間戳>.json'))
on  = json.load(open('runs/rag_on_<時間戳>.json'))
off_by_id = {r['case_id']: r['status'] for r in off}
on_by_id  = {r['case_id']: r['status'] for r in on}
for cid in sorted(set(off_by_id) | set(on_by_id)):
    a, b = off_by_id.get(cid,'?'), on_by_id.get(cid,'?')
    if a != b:
        print(f'{cid}: off={a:25s} → on={b}')
"
```

### 4.3 解讀指南

| 指標 | RAG 提升 | RAG 持平 | RAG 退步 |
|---|---|---|---|
| `passed` count | +2~+5(實質改善)| ±1(噪音帶)| -2 以上(roll back)|
| `total_wall_s` | +5~15%(retrieval 加成本)| 持平 | +30% 以上(查 chroma load)|
| `total_tokens` | -5~-10%(動態 prompt 較短)| ±2% | +10% 以上(slot 寫太大,查 §9.5 budget)|
| Per-case 流動 | 同數量 off→on pass(穩定改善) | 1-2 case 互換(LLM 噪音) | net negative(RAG 注入錯 context)|

**Decision rule**(對齊 spec §11.2 lifecycle):
- ≥+8% pass-rate 且 p<0.05 → promote challenger → champion
- 變化在噪音帶內(±1 case)→ 保持 RAG off,蒐集更多訓練 data 再試
- 任何明顯退步 → keep champion(RAG off),把 RAG retrieve 拉出來 case 級檢查(看抽到啥)

---

## 5. 除錯小抄

### RAG init failed

```
❌ RAG init failed: <error>
```

按錯誤訊息對症:
- `No module named 'chromadb'` → `pip install chromadb`
- `No module named 'sentence_transformers'` → `pip install sentence-transformers`
- `<persist_dir> not found` → 跑步驟 2 build index 先
- Chroma version mismatch → `pip install -U chromadb`

### schema_index empty

```
⚠️  schema_index empty — 跑 scripts/build_rag_indices.py 先 build
```

代表 RAG init 成功但 index 沒 docs。可能原因:
- `domain_metadata` 沒有 active doc → `mongosh genbi --eval "db.domain_metadata.findOne({domain:'tflex', is_active:true})"`
- build 跑過但寫錯 persist_dir → 確認 `./rag_indices/chroma.sqlite3` 存在
- 不同 `--rag-persist-dir` 跟 build script 用的不同 → 統一即可

### LLM service 跑著但 RAG 沒效果

`grep "retrieve_rag_slots" log` 看 retrieval 有沒有被叫到。若無:
- 確認 `--rag-on` 沒 typo
- 確認 `_last_query` 在 phase 起點被 set
- 看 logger warning:`RAG retrieve failed for phase=...`(orchestrator 內部炸了)

---

## 6. 跑完之後

把兩個 log + 兩個 JSON 貼回 Claude,我會:
1. 算 pass-rate delta + 統計顯著性
2. 比對哪些 case 流動了(off→on)
3. Per-phase token usage diff(看 RAG 在哪個 phase 真的有 inject)
4. 建議 next step(promote / iterate retrieval policy / 蒐集更多 anti-pattern docs)

**重點看**:即使 pass count 持平,只要 RAG-on 用更少 token 達到同 pass-rate,
也是有意義的優化(prompt 瘦身,spec §1 主要動機之一)。

---

## 7. 離線 / 內網環境:手動部署 embedding model

如果部署環境**無法連 huggingface.co**(air-gap / 內網限制),不能走 §1 的
自動下載。要先在「有網路的機器」抓檔,搬進部署機,再用 env var 指向本地路徑。

### 7.1 要抓哪些檔案

模型來源:`https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/tree/main`

必要檔案(共 ~92 MB):

```
sentence-transformers/all-MiniLM-L6-v2/
├── config.json                              ← model config
├── config_sentence_transformers.json        ← ST framework config
├── sentence_bert_config.json                ← max_seq_length 等
├── modules.json                             ← module 組裝順序
├── 1_Pooling/
│   └── config.json                          ← pooling layer config
├── model.safetensors                        ← ★ 主權重 ~90 MB(優先抓這個)
│   (或 pytorch_model.bin,兩者擇一,safetensors 較安全)
├── tokenizer.json                           ← 完整 BPE/WordPiece dict
├── tokenizer_config.json                    ← tokenizer 參數
├── special_tokens_map.json                  ← [CLS] [SEP] 等符號定義
└── vocab.txt                                ← WordPiece vocab
```

**可選**(沒影響行為,可省):`README.md`、`train_script.py`、`data_config.json`。

### 7.2 三種下載方法(挑一個)

**方法 A:`huggingface-cli`(推薦,有網路的機器跑)**

```bash
pip install huggingface_hub
huggingface-cli download sentence-transformers/all-MiniLM-L6-v2 \
    --local-dir ./all-MiniLM-L6-v2 \
    --local-dir-use-symlinks False

# 跑完整個資料夾就是要搬的內容
ls -la all-MiniLM-L6-v2/
```

**方法 B:`git clone`(同樣有網路的機器)**

```bash
# Git LFS 必須先裝(safetensors 走 LFS)
git lfs install
git clone https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2

# 完成後 all-MiniLM-L6-v2/ 就是完整目錄
```

**方法 C:逐檔手動下載**

到 [HF 模型頁面](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/tree/main)
點每個檔案的「⬇」icon 下載,按 §7.1 的目錄結構放好。
`1_Pooling/config.json` 在 [這個 sub-dir](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/tree/main/1_Pooling) — 別漏。

### 7.3 放在哪裡

**推薦路徑**(專案內):
```
/Users/kururu/Documents/Claude/Projects/GenBI/
└── models/
    └── all-MiniLM-L6-v2/
        ├── config.json
        ├── model.safetensors
        ├── ... (按 §7.1)
        └── 1_Pooling/config.json
```

或放專案外的共用路徑(多 service 共用 model 時):
```
/opt/genbi/models/all-MiniLM-L6-v2/
```

**規則**:絕對路徑、無中文 / 空白、permissions `chmod -R a+rX`(read 即可,
sentence-transformers 不會寫 model dir)。

### 7.4 設定 env var 讓 GenBI 找到它

加到 `.env`(或 systemd service / docker env):

```bash
# 指向本地 model 目錄(絕對路徑;相對路徑 GenBI 也吃但易踩 cwd 不同的雷)
GENBI_EMBEDDING_MODEL=/Users/kururu/Documents/Claude/Projects/GenBI/models/all-MiniLM-L6-v2

# 強制離線模式(防 sentence-transformers 仍試 HF 線上版本)
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1

# (可選)若 huggingface_hub 仍想 cache 寫 ~/.cache,改寫到專案內
HF_HOME=/Users/kururu/Documents/Claude/Projects/GenBI/.hf_cache
```

**為什麼要 `HF_HUB_OFFLINE=1`**:即使指向本地路徑,某些 HF 版本仍會試發
「版本檢查」HEAD request。內網直接會 timeout(看似 hang)。強制 offline 就斷網。

### 7.5 驗證部署機能載入

```bash
# 在部署機上(無網路)跑
cd /Users/kururu/Documents/Claude/Projects/GenBI
source .env   # 載 env vars

python -c "
import os
print('GENBI_EMBEDDING_MODEL =', os.environ.get('GENBI_EMBEDDING_MODEL'))
print('HF_HUB_OFFLINE       =', os.environ.get('HF_HUB_OFFLINE'))

from embedding_pipeline import get_embedding_pipeline
ep = get_embedding_pipeline()
print(f'Model:{ep.model_name}')
vec = ep.embed_one('hello world')
print(f'OK · dim={vec.shape[0]} · first 3 = {vec[:3]}')
"
```

預期(成功):
```
GENBI_EMBEDDING_MODEL = /Users/.../models/all-MiniLM-L6-v2
HF_HUB_OFFLINE       = 1
Model:/Users/.../models/all-MiniLM-L6-v2
OK · dim=384 · first 3 = [ 0.0341 -0.0123  0.0788]
```

若失敗,看訊息:
- `OSError: Can't load tokenizer` → 缺 tokenizer.json / vocab.txt(回 §7.1 補)
- `huggingface_hub.errors.LocalEntryNotFoundError` → 缺 model.safetensors(回 §7.2)
- `Connection refused / timeout` → 沒設 `HF_HUB_OFFLINE=1`,還在試上網

### 7.6 sentence-transformers 本身的離線安裝

模型搞定但 Python package 還沒裝?分兩步:

**Step 1**(有網路的機器)— 抓 wheel:
```bash
mkdir -p ./genbi_wheels
pip download \
    sentence-transformers chromadb \
    -d ./genbi_wheels \
    --no-deps
# 也抓相依
pip download \
    torch transformers tokenizers huggingface_hub numpy scipy \
    onnxruntime \
    -d ./genbi_wheels
```

**Step 2**(部署機,離線)— 從本地 wheel 安裝:
```bash
pip install --no-index --find-links=./genbi_wheels \
    sentence-transformers chromadb
```

注意 `torch` wheel 要對應**部署機架構**(Linux x86_64 vs macOS arm64 vs CUDA 版本不同)
— 用 `pip download` 時加 `--platform` / `--python-version` 鎖死,或在跟部署機同
架構的中介機跑 `pip download`。

### 7.7 整合進 §1 / §2 / §3

設好 §7.4 的 env var 後,§1 跳過(模型已有)、§2 / §3 照原樣跑即可。
`scripts/build_rag_indices.py` 與 `test_runner.py --rag-on` 都會自動讀
`GENBI_EMBEDDING_MODEL` env var,不必改 code。
