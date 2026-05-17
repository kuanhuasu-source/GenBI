# GenBI 部署指南:A100 40GB + Qwen3.6-27B-AWQ-INT4

> **對象**:DevOps / MLOps,負責把 GenBI 從本地 dev(Ollama + qwen3-coder:30b)推上 A100 40GB production。
>
> **目標**:在 A100 40GB 上以 vLLM 服務 Qwen3.6-27B-AWQ-INT4,並把 GenBI 切過去。
>
> **預期效能對照**:
>
> | 環境 | per-query | 主要用途 |
> |---|---|---|
> | M-series 64GB + Ollama + qwen3-coder:30b | ~43s | 本地 dev |
> | A100 40GB + vLLM + Qwen3.6-27B-AWQ-INT4 | ~15-25s(估)| **production target** |

---

## 1. 系統需求

### 1.1 硬體

| 項目 | 最低 | 推薦 |
|---|---|---|
| GPU | A100 40GB(PCIe 或 SXM4) | A100 40GB SXM4(NVLink 加分) |
| GPU VRAM | 40GB HBM2 | — |
| 系統 RAM | 32GB | 64GB+(vLLM 啟動時會 mmap 模型,大則更穩) |
| 磁碟 | 80GB free | 150GB(留量化中間檔 / log 空間)|
| CUDA | 12.1+ | 12.4+ |

### 1.2 軟體

```bash
# OS:Ubuntu 22.04+ / Debian 12+ / RHEL 9+
# Python: 3.10 or 3.11(vLLM / autoawq 對 3.12 有些 known issue)
# NVIDIA Driver: 535+ 對應 CUDA 12.1+
# Docker:可選(若走容器化部署)

# 確認硬體
nvidia-smi
# 預期:
# NVIDIA A100-SXM4-40GB   |  Driver 535.x  CUDA 12.x  |  40960MiB total
```

### 1.3 GenBI dependencies(MongoDB / Streamlit / etc.)

延用既有 `requirements.txt`,無需改動。MongoDB 可同台 host 或外部 server。

---

## 2. 部署模式選擇

### 2.1 兩條路徑

| 路徑 | 適用 | 耗時 | 工程量 |
|---|---|---|---|
| **A:下載現成 AWQ**(推薦) | 快速 production / 不在乎 calibration 來源 | 15-30 分鐘 | 低 |
| **B:自己量化**(`awq_quantize/`) | 需內部 audit / 客製 calibration dataset / 公司 policy 不准用第三方 quant | 1.5-3 小時 | 中 |

**先試 A,失敗或不適合再走 B**。

### 2.2 候選現成 AWQ 模型

兩個社群品牌都可選:

| HuggingFace ID | 標籤 | 備註 |
|---|---|---|
| `cyankiwi/Qwen3.6-27B-AWQ-INT4` | INT4 W4A16 | 通用 calibration |
| `QuantTrio/Qwen3.6-27B-AWQ` | INT4 W4A16 | 要求 CUDA 12.8+ / vLLM 0.19.0+ |

**首選 `cyankiwi/Qwen3.6-27B-AWQ-INT4`**(CUDA 版本要求較寬)。如果驅動或 vLLM 版本太新可試 QuantTrio。

---

## 3. 路徑 A — 部署現成 AWQ

### 3.1 安裝 vLLM

```bash
# 建 venv
python3 -m venv /opt/genbi/venv && source /opt/genbi/venv/bin/activate
pip install --upgrade pip

# 安裝 vLLM(要對應你的 CUDA 版本;這裡示範 cu121)
pip install "vllm>=0.7.0" --extra-index-url https://download.pytorch.org/whl/cu121

# 驗證
python -c "import vllm; print(vllm.__version__)"
# 預期看到 0.7.x or newer
```

### 3.2 vLLM 啟動

```bash
vllm serve cyankiwi/Qwen3.6-27B-AWQ-INT4 \
  --served-model-name qwen36-27b \
  --quantization awq_marlin \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.85 \
  --max-num-seqs 8 \
  --enforce-eager false \
  --disable-log-stats false \
  --port 8000 \
  --host 0.0.0.0
```

**參數解釋與調校建議**:

| 參數 | 值 | 為什麼 |
|---|---|---|
| `--quantization` | `awq_marlin` | A100 上 awq_marlin kernel 比 plain awq 快 1.5-2x |
| `--max-model-len` | 8192 | GenBI Phase A/B/C system prompt 約 3-4K,加 user msg + 完整 thinking + output,8K 夠用 |
| `--gpu-memory-utilization` | 0.85 | 40GB × 85% = ~34GB,給 model (~14GB) + KV cache (~20GB) + 8% 緩衝 |
| `--max-num-seqs` | 8 | 並發上限。並發大 KV cache 吃越多,8 算保守值。視實際負載調 |
| `--enforce-eager` | false | 啟用 CUDA Graph,latency 微減,但首次 warmup 慢 |
| `--port` | 8000 | OpenAI-compat API 預設 port |

### 3.3 第一次啟動行為

預期 console 看到:
```
INFO ... Loading model weights took 14.3GB
INFO ... # GPU blocks: 4096, # CPU blocks: 1024     ← KV cache 配置
INFO ... Started server process [...]
INFO ... Application startup complete.
INFO ... Uvicorn running on http://0.0.0.0:8000
```

首次 download 模型約 16GB,以 100MB/s 計約 3 分鐘。

### 3.4 Health check

```bash
# Models list 端點
curl -s http://localhost:8000/v1/models | jq

# 預期 output:
# {
#   "object": "list",
#   "data": [{"id": "qwen36-27b", ...}]
# }

# 簡單 chat completion 試打
curl -sS http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen36-27b",
    "messages": [{"role": "user", "content": "1+1=?"}],
    "max_tokens": 50
  }' | jq -r '.choices[0].message.content'
```

預期回應`2`(可能帶 `<think>...</think>` block,下節處理)。

---

## 4. 路徑 B — 自己量化(可選)

如果用現成 AWQ 行不通(質量不符 / 內部 audit / 想客製 calibration),用 `awq_quantize/` 工具:

### 4.1 修改 `awq_quantize/awq_quantize.py`

```python
# 三個變數修改
MODEL_ID   = "Qwen/Qwen3.6-27B"
QUANT_DIR  = "./Qwen3.6-27B-AWQ-INT4"

# Calibration dataset 換成 code-distribution
# (原本是 reasoning 的 dataset,GenBI 是 code-gen 任務,要換)
CALIB_DATASET = "bigcode/the-stack-smol"  # code samples
# 或 fallback 用 pile(質量稍差但廣 universal)
```

### 4.2 跑量化

```bash
cd awq_quantize
source .venv/bin/activate
pip install "autoawq>=0.2.6" "transformers>=4.45"

# 注意:這台機器要 ≥ 40GB VRAM(剛好的話用 CPU offload)
python awq_quantize.py

# 預估 wall time:
# - Model download (54GB BF16): 20-60 min
# - AWQ quantize:               60-90 min
# - Total: 1.5-3 hr
```

### 4.3 驗證量化結果

```bash
python smoke_test.py   # 改 path 指到 ./Qwen3.6-27B-AWQ-INT4
```

預期看到:
- Model loads from local path
- Sample inference 跑通,輸出合理
- VRAM 用量 ~14-16GB(non-batched)

### 4.4 用自製 AWQ 啟動 vLLM

```bash
vllm serve /absolute/path/to/Qwen3.6-27B-AWQ-INT4 \
  --served-model-name qwen36-27b \
  --quantization awq_marlin \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.85 \
  --max-num-seqs 8 \
  --port 8000
```

---

## 5. GenBI 設定切換

### 5.1 `.env`(production 配置)

```bash
# ============================================================
# LLM Provider
# ============================================================
HRDA_MODEL_PROVIDER=vllm
HRDA_MODEL_BASE_URL=http://<vllm-host>:8000/v1
HRDA_MODEL_API_KEY=vllm-dummy           # vLLM 預設不檢 key
HRDA_MODEL_NAME=qwen36-27b              # 對應 --served-model-name
HRDA_MODEL_TIMEOUT_S=120                # AWQ-Int4 略慢於 FP16,給寬

# ============================================================
# Sampling Profile(v0.10.6+)
# ============================================================
# Qwen3.6-27B 預設 thinking,要切 non-thinking + 用對 sampling 數值。
# reasoning_distilled profile 設計上就是針對這類:
#   coding=0.6 / general thinking=1.0 / non-thinking=0.7 + presence_penalty=1.5
HRDA_MODEL_PROFILE=reasoning_distilled

# ============================================================
# Prompt repository / MongoDB(維持既有)
# ============================================================
MONGO_URI=mongodb://<mongo-host>:27017/
MONGO_DB=tflex_demo
GENBI_PROMPT_REPO=true                  # production 用 DB 走 prompt
```

### 5.2 為什麼用 `reasoning_distilled` profile?

Qwen 官方對 Qwen3.6 non-thinking mode 的建議 sampling:
- `temperature=0.7`
- `top_p=0.80`
- `top_k=20`
- `presence_penalty=1.5`

跟我們 `reasoning_distilled` profile 的 `insight`/`meta_response` phase 設定**幾乎完全一致**。Coding 跟 Plan 我們用 0.6 / 1.0 是合理推估。

**Profile snapshot 對照**(`config.py:MODEL_PROFILES["reasoning_distilled"]`):

| Phase | 我們 profile | Qwen 官方 non-thinking 建議 |
|---|---|---|
| `plan` | temp=1.0 | (general thinking)|
| `pipeline / preprocess / plotly / echarts` | temp=0.6 / retry=0.75 | (coding 用較低 temp 合理)|
| `insight / meta_response` | temp=0.7 + presence_penalty=1.5 | temp=0.7 / pp=1.5 ✅ |

### 5.3 Disable thinking mode

Qwen3.6 預設會吐 `<think>...</think>` block。兩層處理:

**Layer 1:vLLM 端 — 用 chat template variant**(若 chat template 提供):

```bash
# Qwen3.6 系列通常用 chat_template 變體切 thinking
# 確認 model card 有沒有 `enable_thinking=false` 之類的 jinja var
```

**Layer 2:GenBI 端 — `_strip_think_blocks()` 已內建**:

v0.10.6 已加在 `llm_service.py:_call_llm` 出口,任何漏網的 `<think>...</think>` 都會被 strip。安全網不用改。

### 5.4 驗證 GenBI 接得到 vLLM

```bash
cd /path/to/GenBI
source venv/bin/activate

python -c "
import config
config.print_summary()
"

# 預期看到:
#   LLM endpoint: http://<vllm-host>:8000/v1
#   LLM model:    qwen36-27b
#   LLM profile:  reasoning_distilled
```

簡單 smoke:

```bash
python bench_model.py qwen36-27b --profile reasoning_distilled --queries 1
```

跑 1 個 query 4 個 phase ≈ 20-60s。

---

## 6. 測試 — Smoke / Bench / Baseline

### 6.1 Smoke test(1 分鐘)

```bash
python bench_model.py qwen36-27b --profile reasoning_distilled --queries 1
```

**判讀基準**:

| Per-call 耗時 | 判讀 |
|---|---|
| Phase A < 10s | ⭐ 完美 |
| Phase A 10-20s | ✅ 正常 |
| Phase A 20-40s | ⚠️ 可用但慢,看是不是 thinking 沒關 |
| Phase A > 60s | ❌ 異常,進故障排除 §8 |

### 6.2 Full bench(5-10 分鐘)

```bash
python bench_model.py qwen36-27b --profile reasoning_distilled --queries 3
```

**3-query 對照表**(用過去資料校準):

| Model + Profile | Per-query Total | LLM calls | Tok/sec |
|---|---|---|---|
| **目標 baseline** Qwen3.6-27B-AWQ + vLLM A100 40GB | **15-30s** | 13 clean | 200-400 |
| 對照 qwen3-coder:30b + Ollama M-series 64GB | 43.5s | 13 clean | 376 |

要求:
- `LLM calls = 13`(不超過)— 表示沒 retry,prompt + model 行為對
- `Per-query < 35s`(嚴格)/ `< 50s`(寬鬆)— prod target

### 6.3 Full baseline(20-30 分鐘)

```bash
python test_runner.py --domain tflex 2>&1 | tee test_results_A100_qwen36-27b.log
```

**判讀**:

| Pass rate | 判讀 |
|---|---|
| ≥ 92%(對照 v0.10.7 qwen3-coder:30b 的 24/26) | ✅ ship to prod |
| 85-92% | ⚠️ 有 regression,可比較 fail case 找根因 |
| < 85% | ❌ 不適合,回滾,看是 prompt 問題或量化問題 |

### 6.4 比對 baseline diff

```bash
# 過去 baseline 結果存在
test_results_v0.10.7_baseline.{md,json}

# diff 兩份 baseline 看哪些 case 變動
diff test_results_v0.10.7_baseline.md test_results_A100_qwen36-27b.log | head -80
```

---

## 7. Production checklist

部署前 checklist:

- [ ] A100 40GB driver / CUDA / Python 環境就緒(`nvidia-smi` / `python --version`)
- [ ] vLLM 安裝 & `vllm serve` 啟動成功
- [ ] curl `http://localhost:8000/v1/models` 回 `qwen36-27b`
- [ ] curl chat completion 試打,回應合理
- [ ] GenBI `.env` 切到 vllm provider + `reasoning_distilled` profile
- [ ] `bench_model.py` 1 query smoke pass
- [ ] `bench_model.py` 3 query 全 phase < 50s
- [ ] `test_runner.py --domain tflex` 跑完 pass rate ≥ 92%
- [ ] MongoDB 連得到、test_runs 寫得進去
- [ ] task_traces 也寫得進去(self-learning 餵料)
- [ ] Streamlit `app.py` 跑得起來、能跑 1 個 query 完整 5 phase

部署後監控:
- [ ] vLLM `--disable-log-stats=false` 看 throughput / latency / GPU util
- [ ] MongoDB 監控 task_traces 數量
- [ ] Streamlit / API 端 latency p50/p95

---

## 8. 故障排除

### 8.1 vLLM 起不來

| 症狀 | 可能原因 | 對策 |
|---|---|---|
| `CUDA out of memory` 啟動就爆 | `--gpu-memory-utilization` 太高 | 降到 0.80 或 0.75 |
| `CUDA out of memory` 跑一陣子才爆 | KV cache 撐爆(並發太高 / max-model-len 太大) | 降 `--max-num-seqs` 或 `--max-model-len` |
| `awq_marlin` not supported | A100 + 太舊 vLLM | 升級 vLLM 或 fallback `--quantization awq`(慢但穩) |
| `model loading failed` | Disk 不足 / HF 連線中斷 | 確認磁碟 + 重試 |

### 8.2 GenBI 接得到但結果怪

| 症狀 | 排查 |
|---|---|
| Response 含 `<think>...</think>` 沒 strip | 確認 `HRDA_MODEL_PROFILE=reasoning_distilled` + GenBI 版本 ≥ v0.10.6 |
| Pass rate 低 < 80% | 多半是 `non-thinking mode 沒啟用`,看 model card 確認 chat_template variant |
| JSON parse 失敗多 | thinking trace 漏進 JSON parser,確認 `<think>` strip 設定 |
| Phase A 慢(>30s/call) | 多半 thinking trace 太長 → 同上 |

### 8.3 量化質量差

如果 pass rate 大幅低於 v0.10.7 baseline(92%):

1. **試另一個現成 AWQ**:`QuantTrio/Qwen3.6-27B-AWQ`
2. **自己 quantize(路徑 B)** + 用 code-heavy calibration dataset
3. **試 Qwen3.6-35B-A3B-AWQ**(MoE,如果 VRAM 還夠 — 27GB 模型 + KV cache 在 40GB 上會吃緊,要降 max-num-seqs 到 4)

---

## 9. 回滾計畫

如果 production 上 Qwen3.6-27B-AWQ 不如預期,**3 分鐘可回到 dev baseline**:

```bash
# 停 vLLM
pkill -f "vllm serve"

# 切 .env 回 ollama + qwen3-coder
sed -i 's/HRDA_MODEL_PROVIDER=vllm/HRDA_MODEL_PROVIDER=ollama/' .env
sed -i 's/HRDA_MODEL_NAME=qwen36-27b/HRDA_MODEL_NAME=qwen3-coder-30b-8k/' .env
sed -i 's/HRDA_MODEL_PROFILE=reasoning_distilled/HRDA_MODEL_PROFILE=default/' .env

# 重啟 Streamlit
systemctl restart genbi-streamlit
```

⚠️ 注意:若已累積 task_traces 是新 model 來的,self-learning loop 的 instinct 可能有「混雜不同模型 pattern」現象。回滾前先 backup learning_* collections:

```bash
mongodump --db tflex_demo --collection learning_observations --out /backup/$(date +%Y%m%d-rollback)/
mongodump --db tflex_demo --collection learning_instincts     --out /backup/$(date +%Y%m%d-rollback)/
mongodump --db tflex_demo --collection learning_candidates    --out /backup/$(date +%Y%m%d-rollback)/
```

---

## 10. 進階:延伸場景

### 10.1 並發放大(高 QPS)

若日常負載 < 8 concurrent users,A100 40GB 夠。要更高:

| 並發目標 | 建議配置 |
|---|---|
| 8-16 並發 | A100 80GB(直接升記憶體,可開 `--max-num-seqs 16`) |
| 16+ 並發 | 多卡 tensor parallelism:`--tensor-parallel-size 2` 或 4 |
| 50+ 並發 | H100 80GB × N + FP8(無需 AWQ) |

### 10.2 多 domain 切換

GenBI v0.3.0+ 已支援多 domain。換 domain 不需要重啟 vLLM,只切 Streamlit sidebar。

### 10.3 Self-learning loop

部署完 nightly cron(細節見 `SELF_LEARNING_OPS.md`):

```cron
# 凌晨 2 點跑 self-learning pipeline
0 2 * * * cd /opt/genbi && /opt/genbi/venv/bin/python scripts/run_learning_jobs.py >> /var/log/genbi_learning.log 2>&1
```

---

## 11. 參考連結

- [Qwen3.6-27B 官方 model card](https://huggingface.co/Qwen/Qwen3.6-27B)
- [cyankiwi/Qwen3.6-27B-AWQ-INT4](https://huggingface.co/cyankiwi/Qwen3.6-27B-AWQ-INT4)
- [QuantTrio/Qwen3.6-27B-AWQ](https://huggingface.co/QuantTrio/Qwen3.6-27B-AWQ)
- [vLLM AWQ docs](https://docs.vllm.ai/en/latest/quantization/auto_awq.html)
- [AutoAWQ GitHub](https://github.com/casper-hansen/AutoAWQ)
- [vLLM serve params reference](https://docs.vllm.ai/en/latest/usage/engine_args.html)
- 本 repo 內相關文件:
  - `SELF_LEARNING_OPS.md` — self-learning 維運手冊
  - `AI_CONTEXT.md §20` — Model profile system 設計
  - `awq_quantize/README_RUN.md` — 既有 AWQ 工具鏈說明
