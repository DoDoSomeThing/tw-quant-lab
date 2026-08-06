export const meta = {
  name: 'tw-research-team',
  description: '紀律型台股研究所 §7.4 上線 gate:對抗辯證層能否殺掉已知為假的動能 scanner',
  phases: [
    { title: '藍方', detail: '建構動能是 edge 的最強論證' },
    { title: '紅方', detail: '3 lens skeptic,預設 refuted=true' },
    { title: '深度討論', detail: '藍反駁→紅再攻,上限 2 輪' },
    { title: '判官', detail: '未參與辯論的新 agent 判決' },
    { title: 'gate', detail: '判決須為 💀,否則團隊廢掉重寫' },
  ],
}

// ── 固定 fixture:12-1 月動能五關報告(QLAB_PRICE=adj,2026-07-23 實跑)。
// 已知為假(2026-06 大樣本判無 alpha)。判死線:p=0.4796>0.05 → 應判 💀。
const MOMENTUM_REPORT = `
訊號驗證:12-1 月動能  資料 2021-06-01~2026-07-09  1237交易日 1081檔  持有20 再平衡21 取前10% 成本0.6%
關1+2 公平基準+樣本外(切2024): n=46 超額均=+0.43% 勝率=48% 年化超額=+5.3% Sharpe≈0.36
   IS(<2024)=+0.21%  OOS(>=2024)=+0.59%
   分年 2022:-1.2%(n7) 2023:+1.0%(n12) 2024:-0.9%(n11) 2025:+0.4%(n12) 2026:+5.1%(n4)
關2b 真樣本外(切2025): IS(<2025)=-0.19%  OOS(>=2025)=+1.60%
關3 隨機對照: 你的均值+0.43%;300次隨機中位-0.59%;隨機>=你=0.0%
   bootstrap: n=46 均值+0.43% CI[-0.73,+1.70] p=0.4796 → 🟡 帳面正但不顯著
關4 成本敏感: 0.4%→+0.63%(勝50%)  0.6%→+0.43%(勝48%)  0.8%→+0.23%(勝43%)
關5 regime: 0050 分年 2024+49.3%⚠️ 2025+38.1%⚠️ 2026+60.3%⚠️  極端年 3/6=50%
`.trim()

// 判死線(事前寫死,與法人動向 SPEC 一致):超額≤0 或 年化<2% 或 p>0.05,任一即死。
const DEATH_LINE = '判死線(硬規則,不談「接近顯著」):超額≤0 或 年化<2% 或 p(bootstrap)>0.05,任一中即 💀。'

const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['lens', 'refuted', 'kill_reason', 'strongest_point'],
  properties: {
    lens: { type: 'string' },
    refuted: { type: 'boolean', description: '此格是否被推翻(不是 edge)。預設 true。' },
    kill_reason: { type: 'string', description: '若 refuted,一句話死因,須引報告數字' },
    strongest_point: { type: 'string', description: '對方(藍方)最站得住的一點,誠實列' },
  },
}
const JUDGE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['verdict', 'strongest_objection', 'blue_best_defense', 'why_not_convinced', 'must_refute_to_overturn'],
  properties: {
    verdict: { type: 'string', enum: ['💀', '✅'] },
    strongest_objection: { type: 'string' },
    blue_best_defense: { type: 'string' },
    why_not_convinced: { type: 'string' },
    must_refute_to_overturn: { type: 'string' },
  },
}

const LENSES = [
  { key: '樣本外', focus: 'IS/OOS。看 IS 段是否為負、正報酬是否只集中在 OOS 尾端某一年。' },
  { key: '隨機/顯著性', focus: 'bootstrap p 值與隨機對照。「隨機>=你=0%」是否被 p 值推翻?區分「贏過亂選」與「統計顯著」。' },
  { key: 'regime污染', focus: '正報酬是否踩在極端多頭年(2024/25/26)。極端年占比。抽掉極端年還剩什麼。' },
]

// ── 藍方:建構最強論證(§10 對抗辯證,先給正方最好一擊)
phase('藍方')
const blueCase = await agent(
  `你是藍方。任務:給「12-1 月動能是真 edge」的最強論證,挑報告對你有利的數字。\n` +
  `${DEATH_LINE}\n\n報告:\n${MOMENTUM_REPORT}\n\n只寫最強論證,3-5 句。`,
  { phase: '藍方' }
)

// ── 紅方 3 lens + 深度討論(藍反駁 → 紅再攻,上限 2 輪)
phase('紅方')
async function redAttack(lens, round, blueRebuttal) {
  const ctx = round === 1
    ? `藍方論證:\n${blueCase}`
    : `藍方論證:\n${blueCase}\n\n藍方對第一輪紅方的反駁:\n${blueRebuttal}`
  return agent(
    `你是紅方 skeptic,lens=「${lens.key}」。任務是「推翻」動能是 edge,不是確認。預設 refuted=true。\n` +
    `專攻:${lens.focus}\n${DEATH_LINE}\n\n報告:\n${MOMENTUM_REPORT}\n\n${ctx}\n\n` +
    `只從你的 lens 出手。第${round}輪。`,
    { phase: round === 1 ? '紅方' : '深度討論', label: `紅方:${lens.key}·r${round}`, schema: VERDICT_SCHEMA }
  )
}

const round1 = (await parallel(LENSES.map(l => () => redAttack(l, 1)))).filter(Boolean)

phase('深度討論')
const blueRebuttal = await agent(
  `你是藍方。紅方三路攻擊如下,逐一回應、盡力守住動能是 edge。若守不住,誠實承認。\n` +
  `紅方:\n${round1.map(r => `[${r.lens}] refuted=${r.refuted} 死因:${r.kill_reason}`).join('\n')}\n\n` +
  `${DEATH_LINE}\n報告:\n${MOMENTUM_REPORT}`,
  { phase: '深度討論', label: '藍方反駁' }
)
// 收斂條件:第一輪三路已全 refuted 就不必第二輪(loop-until-dry 精神)
const allKilledR1 = round1.length === LENSES.length && round1.every(r => r.refuted)
const round2 = allKilledR1 ? [] : (await parallel(LENSES.map(l => () => redAttack(l, 2, blueRebuttal)))).filter(Boolean)
const finalRed = round2.length ? round2 : round1

// ── 判官:未參與辯論的新 agent,只讀最終雙方陳述
phase('判官')
const judge = await agent(
  `你是判官,沒參與辯論。只讀最終雙方陳述,依判死線判決。舉證責任在藍方:要證明「活」,不是證明「沒死」。\n` +
  `${DEATH_LINE}\n\n【報告】\n${MOMENTUM_REPORT}\n\n【藍方最終】\n${blueCase}\n\n藍方反駁:${blueRebuttal}\n\n` +
  `【紅方最終】\n${finalRed.map(r => `[${r.lens}] refuted=${r.refuted} 死因:${r.kill_reason} 承認對方強點:${r.strongest_point}`).join('\n')}`,
  { phase: '判官', schema: JUDGE_SCHEMA }
)

// ── §7.4 gate 斷言
phase('gate')
const redRefuteVotes = finalRed.filter(r => r.refuted).length
const passGate = judge.verdict === '💀' && redRefuteVotes >= 2
log(`判決=${judge.verdict}  紅方 refute 票=${redRefuteVotes}/${finalRed.length}  gate=${passGate ? 'PASS ✅' : 'FAIL ❌'}`)

return {
  gate: passGate ? 'PASS' : 'FAIL',
  expected: '💀 (動能已知為假,p=0.4796>0.05 且 regime 污染)',
  judge,
  red_refute_votes: `${redRefuteVotes}/${finalRed.length}`,
  rounds_used: round2.length ? 2 : 1,
  note: passGate
    ? '裁判殺掉了已知為假的動能 scanner → 對抗辯證層通過體檢,可續建掃描層。'
    : '❌ 裁判放過動能 scanner → 團隊壞了,依 SPEC §7.4 整套廢掉重寫,不准上線。',
}
