# tw-quant-lab — 台股策略「誠實驗證器」

> 不是找賺錢策略,是**用嚴格框架拆穿訊號是不是幻覺**。

市面回測工具都產「漂亮報告」:挑好看的區間、用台積電權值撐起的 0050 當基準、
拿同一段資料反覆擬合、忽略成本。這個專案反過來——它的工作是**打臉**:
給你一個訊號,它盡力證明那是運氣不是 edge。

## 五關驗證

任何訊號都得過這五關才算數:

| 關 | 名稱 | 在問什麼 |
|---|------|---------|
| 1 | **公平基準** | 贏得過「全市場等權」嗎(不靠台積電撐起的 0050 自欺) |
| 2 | **真·樣本外** | 時間切分,新年份不可碰。今天用 2025 一翻就破 = demo |
| 3 | **隨機對照** | 贏得過同條件亂選嗎(bootstrap 經驗 p 值) |
| 4 | **成本敏感** | 來回成本拉高會不會翻負 |
| 5 | **regime 分層 + 污染警示** | 分年/多空;並偵測「樣本期是否全是極端 regime」 |

## 架構

```
framework/
  config.py     路徑(env 覆蓋,預設指向資料源)、成本、基準、OOS 切點
  data.py       Data:一次載入 kline/revenue/t86 → cal/regime/等權基準
  context.py    Context:傳給訊號函式的 point-in-time 查詢(close/動能/MA/YoY/法人)
  engine.py     validate():吃訊號函式 → 自動跑完五關出報告
  gates.py      五關零件:報告 / bootstrap / 隨機對照 / regime 污染警示
  finmind.py    自帶資料抓取工具(脫離 bot)
studies/        各訊號 = 套五關(含 example_* 通用介面範例)
backfill/       FinMind 回填(kline/revenue/t86),token 走 env
results/        CSV + 每 study 一句結論
data/           不進 git;config 指向(見 data/README.md)
```

## 快速開始 —— 寫一個訊號,自動跑五關

只寫一個函式,框架包辦驗證:

```python
# studies/my_signal.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from framework.engine import validate

def signal(sid, date, ctx):
    """回傳當日訊號分數(None=不選);分數越大越優先。ctx 只給到 date 為止的資料,不偷看未來。"""
    return ctx.momentum(sid, date, lookback=252, gap=21)   # 12-1 動能

validate(signal, "我的訊號")
```

```bash
python studies/my_signal.py
# → 自動印 關1 公平基準 / 關2 真OOS(2024+2025) / 關3 隨機對照 / 關4 成本敏感 / 關5 regime污染
```

`ctx` 提供:`close / window / momentum / ma / volatility / regime / avgvol / size_pct /
revenue_yoy / inst_buy`(全 point-in-time)。內建範例:`studies/example_momentum.py`、
`studies/example_revenue_combo.py`。

資料:用 `backfill/` 拿自己的 FinMind token 自抓進 `data/`(預設目錄),
或 `export QLAB_DATA_DIR=/path/to/cache` 指向已有資料(見 data/README.md)。
純標準庫,只有 `backfill/` 需 `requests`。Python 3.10+。

## Case study:營收 surprise(2021-24 神 / 2025 破)

唯一通過前四關、看似有 edge 的訊號:**大型 × 高YoY(>20%) × 非空頭 × 站MA60 × 動能>0**。

```
壓力掃描(基準=全市場等權,每格 全期超額% / 勝率% / 2025真OOS%):
YoY> 20% (n=2131):  20日[+1.18/43%/-2.33]  40日[+2.37/44%/-2.22]  60日[+3.61/44%/-0.64]
```

- 全期超額 **+1.18%/20日**,2021→2024 整片正、單調、穩健 → 一度以為突破。
- **2025 真·樣本外 −2.33%** → 第二關當場破功。

為什麼?第五關給答案:

```
基準 0050 各年(污染檢查):
  2024: 報酬 +45.1%  年化波動 25.3%  ⚠️ 極端
  2025: 報酬 -66.2%  年化波動 81.1%  ⚠️ 極端
```

台股 2021-25 幾乎**無一正常年**(froth→熊→AI 狂→崩),樣本全被極端 regime 污染。
→ 選股 edge 在這段**驗不出**;唯一穩定有效的是「持有指數 + regime 防禦」。

這就是重點:**漂亮的回測 + 樣本外一翻就破 = 幻覺**。誠實驗證器讓它當場現形。

## 狀態

- 階段1 ✅:framework 五關零件 + 4 支原始 study(factor_scan / revenue_offline_bt / revenue_refine / revenue_stress),跑得出全部結論,bot 不受影響,資料不重複。
- 階段2 ✅:通用訊號介面 `signal(sid, date, ctx)->float|None` + `validate()` 自動五關引擎 + `backfill/`(自帶 FinMind token,脫離 bot)+ 範例 study。
- 別人 clone → 抓資料 → 寫一個 signal 函式,就能得到五關報告。
