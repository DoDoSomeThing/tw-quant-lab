# data/

資料**不進 git**(kline_deep.json 約 100MB)。

## 取得方式

**選項 A — 指向既有資料(預設)**
`framework/config.py` 預設指向同層 `../tw-stock-bot/cache`。本機已有 bot 就免設定。

**選項 B — 環境變數覆蓋**
```bash
export QLAB_DATA_DIR=/path/to/cache        # kline_deep.json / revenue.json
export QLAB_T86_DIR=/path/to/t86_cache     # 法人逐日 json(選用)
```

**選項 C — 自行 backfill(公開版)**
用自己的 FinMind token:
```bash
export FINMIND_TOKEN=你的token
python backfill/backfill_kline.py
python backfill/backfill_revenue.py
```
(backfill/ 為階段2 加入)

## 資料格式
- `kline_deep.json` = `{sid: [{date, open, close, max, min, volume}, ...]}`
- `revenue.json` = `{sid: [[avail_date, yyyymm, rev], ...]}`
- `t86_cache/YYYYMMDD.json` = `{code: [外資, 投信, 自營], ...}`
