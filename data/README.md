# data/

資料**不進 git**(kline_deep.json 約 100MB)。

預設資料目錄就是這裡(`framework/config.py` 預設指 `./data`)。

## 取得方式

**選項 A — 自行 backfill(用自己的 FinMind token)**
```bash
export FINMIND_TOKEN=你的token
python backfill/backfill_kline.py     # → data/kline_deep.json
python backfill/backfill_revenue.py   # → data/revenue.json
python backfill/backfill_t86.py       # → data/t86_cache/(法人,選用)
```
全市場 1000+ 檔會撞 FinMind 每小時配額,腳本會自動睡到整點續跑、可中斷 resume。

**選項 B — 指向已有資料目錄(環境變數覆蓋)**
```bash
export QLAB_DATA_DIR=/path/to/cache        # kline_deep.json / revenue.json
export QLAB_T86_DIR=/path/to/t86_cache     # 法人逐日 json(選用)
```

## 保持最新

```bash
python backfill/update_data.py --check     # 只檢測新不新鮮(免 token)
export FINMIND_TOKEN=你的token
python backfill/update_data.py             # 過期才增量補到今天(kline 接新日、revenue 補新月)
```
增量只補現有股票缺的部分,比全量 backfill 快。新上市股票要全量時另跑 backfill_kline.py。

## 資料格式
- `kline_deep.json` = `{sid: [{date, open, close, max, min, volume}, ...]}`
- `revenue.json` = `{sid: [[avail_date, yyyymm, rev], ...]}`
- `t86_cache/YYYYMMDD.json` = `{code: [外資, 投信, 自營], ...}`
