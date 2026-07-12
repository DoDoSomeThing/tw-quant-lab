#!/usr/bin/env python3
"""
紙上模擬 —— revenue combo(七關全過,2026-07-12 升級)的 forward 追蹤。

鎖死基準組,不准調參(過擬合防線):
  大型(量前50%)× 月營收YoY>20% × 非空頭(0050>MA120)× 站上MA60 × 60日動能>0
  等權持有全部入選股 20 交易日,月再平衡(前後批至少隔 21 交易日),成本 0.6% 來回。

設計:
  - log(data/paper_log.json)只存 進場日+代號,**不存價格** —— 報酬結算時
    從當下的還原價序列取進出場價,除權息自動含入、前復權重錨也不會壞帳。
  - 每次執行:先結算已到期批次(進場+20td ≤ 資料尾),再看要不要開新批
    (最新批進場 ≥21td 前 → 用資料最新日重新選股開新批)。
  - 對照:全市場等權(EW,同期)+ 0050(同期)。判準與回測同一把尺:
    扣成本後超額(vs EW)。回測期望 +1.70%/期,forward 掉到哪裡,誠實記錄。

月更流程(建議每月初):
  export FINMIND_TOKEN=... && python backfill/update_data.py      # raw 補到今天
  python backfill/build_adj.py                                     # 還原價重建(事件快取,快)
  QLAB_PRICE=adj python studies/paper_combo.py                     # 結算+開新批
"""
import os
import sys
import json
import statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from framework import config
from framework.data import Data, load_revenue
from framework.context import Context

# log 路徑可用 QLAB_PAPER_LOG 覆寫(tw-stock-bot v2 把正本放它的 repo 追蹤)
LOG_PATH = os.environ.get("QLAB_PAPER_LOG") or os.path.join(config.DATA_DIR, "paper_log.json")
HOLD, GAP, COST = 20, 21, config.COST
YOY_TH, MA_N, MOM_N, SIZE_P = 0.20, 60, 60, 0.5   # 鎖死。要改=重跑七關,不是改這裡。


def load_log():
    if os.path.exists(LOG_PATH):
        return json.load(open(LOG_PATH, encoding="utf-8"))
    return {"batches": []}


def save_log(log):
    tmp = LOG_PATH + ".tmp"
    json.dump(log, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, LOG_PATH)


def pick(data, ctx, dt):
    """基準組訊號,回入選 sid list(等權)。"""
    size_cut = ctx.size_pct(SIZE_P)
    out = []
    for sid in data.d:
        if sid == data.bench or ctx.avgvol(sid) < size_cut:
            continue
        if ctx.regime(dt) is not True:
            return []          # 空頭整批空手
        yoy = ctx.revenue_yoy(sid, dt)
        if yoy is None or yoy < YOY_TH:
            continue
        px = ctx.close(sid, dt)
        ma = ctx.ma(sid, dt, MA_N)
        mom = ctx.momentum(sid, dt, lookback=MOM_N)
        if px is None or ma is None or mom is None or px <= ma or mom <= 0:
            continue
        out.append(sid)
    return out


def settle(data, log):
    """結算所有已到期且未結的批次(結果寫回 log 的 batch dict)。"""
    cal, cidx, C = data.cal, data.cidx, data.C
    ew = data.build_ew(HOLD)
    for b in log["batches"]:
        e = b["entry_date"]
        i = cidx.get(e)
        if i is None or i + HOLD >= len(cal):
            continue                      # 未到期
        x = cal[i + HOLD]
        rets = []
        for sid in b["sids"]:
            ce, cx = C[sid].get(e), C[sid].get(x)
            if ce and cx and ce > 0:
                rets.append(cx / ce - 1)
        if not rets or e not in ew:
            continue
        port = statistics.mean(rets)
        exc = (port - COST) - ew[e]
        b.update({"exit_date": x, "port": round(port, 4), "ew": round(ew[e], 4),
                  "bench": round(data.bench_fwd(e, x) or 0, 4), "excess": round(exc, 4)})


def main():
    data = Data()
    ctx = Context(data, revenue=load_revenue())
    log = load_log()
    last = data.cal[-1]
    print(f"paper_combo  資料尾 {last}  價格模式 {config.PRICE_MODE}"
          f"{'' if config.PRICE_MODE == 'adj' else '  ⚠️ 建議用 QLAB_PRICE=adj(除息才入帳)'}")

    # 1. 結算
    settle(data, log)
    closed = [b for b in log["batches"] if "excess" in b]
    if closed:
        print(f"\n已結 {len(closed)} 批:")
        for b in closed:
            print(f"  {b['entry_date']}→{b['exit_date']}  {len(b['sids'])}檔"
                  f"  組合{b['port']*100:+.2f}%  EW{b['ew']*100:+.2f}%  0050{b['bench']*100:+.2f}%"
                  f"  → 超額 {b['excess']*100:+.2f}%")
        xs = [b["excess"] for b in closed]
        n = len(xs)
        m = statistics.mean(xs)
        pos = sum(1 for v in xs if v > 0) / n * 100
        print(f"  累計:n={n} 超額均={m*100:+.2f}%/期 勝率={pos:.0f}%(回測期望 +1.70%/期、71%)")

    # 2. 開新批(最新批 ≥GAP 交易日前,或無批)
    opens = [b for b in log["batches"]]
    need_new = True
    if opens:
        last_entry = max(b["entry_date"] for b in opens)
        i0, i1 = data.cidx.get(last_entry), data.cidx.get(last)
        if i0 is not None and i1 is not None and i1 - i0 < GAP:
            need_new = False
            print(f"\n最新批 {last_entry} 未滿 {GAP} 交易日,不開新批。")
    if need_new:
        sids = pick(data, ctx, last)
        if not sids:
            print(f"\n{last}:regime 空頭或無入選 → 本期空手(也記一批,誠實記空手期)。")
        log["batches"].append({"entry_date": last, "sids": sids})
        print(f"\n開新批 {last}:{len(sids)} 檔")
        if sids:
            print("  " + " ".join(sids))

    save_log(log)
    print(f"\nlog → {LOG_PATH}")


if __name__ == "__main__":
    main()
