/*
 * The KPIs of a run read in words: the table's marks (KPIS), the targets a
 * run meets, and the verdict (analyse) - the simulate page's analysis
 * dialog and the mix page's analysis, which reads the mix's whole as one
 * run. A row here is {kpi, report: {closedTrades}, margin}.
 *
 * Its formatters are its own: the pages' scripts share one global scope,
 * and a second `pct` there is a syntax error.
 */

const kpiPct = (v) => v.toFixed(1) + '%';
const kpiNum = (v) => v.toFixed(2);
const kpiPercent = (v) => (v === null || v === undefined) ? 'n/a' : (v * 100).toFixed(1) + '%';
function kpiAmount(v) {
  if (v === null || v === undefined) return '';
  return Math.abs(v) >= 1 || v === 0 ? v.toFixed(2) : v.toPrecision(3);
}

// [label, key in row.kpi, format, verdict(v) -> 'good' | 'ok' | 'bad' | '', target text]
const KPIS = [
  ['ROI', 'roi', kpiPct, (v) => v > 0 ? 'good' : 'bad', '> 0 over the window'],
  ['CAR', 'car', kpiPct, (v) => v > 15 ? 'good' : v > 0 ? 'ok' : 'bad', '> 15% a year'],
  ['profit factor', 'profitFactor', kpiNum, (v) => v > 2 ? 'good' : v > 1.5 ? 'ok' : 'bad', '> 1.5 good, > 2 excellent'],
  ['expectancy', 'expectancy', kpiAmount, (v) => v > 0 ? 'good' : 'bad', '> 0 a trade'],
  ['win rate', 'winRate', kpiPercent, () => '', '40-60% for most, depends on the strategy'],
  ['max DD', 'maxDrawdownPct', kpiPct, (v) => v < 20 ? 'good' : 'bad', '< 20% peak to trough'],
  ['risk-reward', 'riskReward', kpiNum, (v) => v >= 2 ? 'good' : v >= 1 ? 'ok' : 'bad', 'avg win / avg loss >= 2'],
  ['Sharpe', 'sharpe', kpiNum, (v) => v > 2 ? 'good' : v > 1 ? 'ok' : 'bad', '> 1 good, > 2 excellent'],
  ['CAR/MDD', 'carMdd', kpiNum, (v) => v > 1 ? 'good' : v > 0 ? 'ok' : 'bad', 'higher is better'],
  ['Ulcer', 'ulcer', kpiNum, (v) => v < 5 ? 'good' : v < 10 ? 'ok' : 'bad', 'depth and length of drawdowns, lower is better'],
];
const MARK = { good: ' \u2713', ok: ' ~', bad: ' \u2717' };
// where a score starts to be middling and good: the bar's marks and colours
const scoreVerdict = (v) => v >= 70 ? 'good' : v >= 40 ? 'ok' : 'bad';

// the targets a run meets: 'good' counts, 'ok' (the lower bar) counts half
function targets(row) {
  let met = 0;
  for (const [, key, , verdict] of KPIS) {
    const v = row.kpi && row.kpi[key];
    if (v === null || v === undefined) continue;
    const said = verdict(v);
    met += said === 'good' ? 1 : said === 'ok' ? 0.5 : 0;
  }
  return met;
}

// Each KPI judged against its usual benchmark, then a verdict in words.
// The thresholds are the ones given with the procedure, and they are not the
// table's own marks (KPIS above): the table ranks, this reads one run.
const JUDGE = [
  ['ROI', 'roi', kpiPct, (v) => v <= 0 ? ['\u2717', 'Negative'] : ['\u2713', 'Positive']],
  ['CAR', 'car', kpiPct, (v) => v <= 0 ? ['\u2717', 'Negative'] : ['\u2713', 'Positive']],
  ['Profit Factor', 'profitFactor', kpiNum, (v) => v < 1 ? ['\u2717', 'Not profitable (PF < 1)']
    : v < 1.5 ? ['\u2717', 'Weak: a thin edge, sensitive to costs and slippage']
    : v < 2 ? ['\u2713', 'Good'] : ['\u2713', 'Excellent']],
  ['Expectancy', 'expectancy', kpiAmount, (v) => v <= 0 ? ['\u2717', 'Not sustainable (expectancy \u2264 0)'] : ['\u2713', 'Positive expectancy']],
  ['Win Rate', 'winRate', kpiPercent, (v) => (v *= 100) >= 40 && v <= 60 ? ['~', 'Average for directional strategies']
    : v < 40 ? ['\u2717', 'Low'] : ['\u2713', 'High']],
  ['Max Drawdown', 'maxDrawdownPct', kpiPct, (v) => v < 10 ? ['\u2713', 'Low drawdown']
    : v < 15 ? ['~', 'Moderate drawdown'] : v < 20 ? ['\u2717', 'High drawdown']
    : ['\u2717', 'Very high drawdown, hard to live through']],
  ['Risk-Reward', 'riskReward', kpiNum, (v) => v < 1 ? ['\u2717', 'Unfavourable: average loss > average win']
    : v < 2 ? ['~', 'Neutral / moderate'] : ['\u2713', 'Favourable']],
  ['Sharpe', 'sharpe', kpiNum, (v) => v < 1 ? ['\u2717', 'Below par: weak volatility-adjusted return']
    : v < 2 ? ['\u2713', 'Good'] : ['\u2713', 'Excellent']],
  ['CAR/MDD', 'carMdd', kpiNum, (v) => v < 0.3 ? ['~', 'Low: a return \u201cexpensive\u201d in drawdown']
    : v < 0.6 ? ['~', 'Moderate'] : ['\u2713', 'Good']],
  ['Ulcer', 'ulcer', kpiNum, (v) => v < 6 ? ['\u2713', 'Contained drawdown stress']
    : v < 8 ? ['~', 'Moderate'] : ['\u2717', 'High: long stretches under water']],
];

// the procedure itself: {summary, table, strengths, weaknesses, verdict, todo}
function analyse(row) {
  // a figure the run cannot have is read as 0, except the two ratios with
  // nothing under them - no losing trade - which are then at their best
  const k = { ...row.kpi };
  for (const key of Object.keys(k)) k[key] = k[key] ?? 0;
  for (const key of ['profitFactor', 'riskReward']) k[key] = row.kpi[key] ?? Infinity;
  const { roi, car, profitFactor: pf, expectancy, winRate, maxDrawdownPct: mdd,
          riskReward: rr, sharpe, ulcer } = k;
  const win = winRate * 100;
  const profitable = pf > 1 && expectancy > 0 && roi > 0;
  const weak = pf < 1.5 || mdd > 20 || sharpe < 1;
  const met = targets(row), total = KPIS.length - 1, ratio = total ? met / total : 0;

  const table = JUDGE.map(([label, key, format, judge]) => {
    const v = row.kpi[key];
    return v === null || v === undefined ? [label, 'n/a', '', 'cannot be computed on this run']
      : [label, format(v), ...judge(v)];
  });
  const m = row.margin;
  if (m) {
    const day = (ms) => new Date(ms).toISOString().slice(0, 10);
    table.push(['Margin', `${m.leverage}:1 \u00b7 peak ${m.peakMarginPct.toFixed(1)}%`,
      ...(m.ok ? ['\u2713', `Always free margin, at least ${kpiAmount(m.minFree)}`]
        : m.negativeAt !== null ? ['\u2717', `The account goes to zero on ${day(m.negativeAt)}`]
        : ['\u2717', `Out of margin on ${day(m.breachAt)}: the account does not open the trade`])]);
  }
  table.push(['Targets Met', `${met}/${total}`, ...(ratio >= 0.66 ? ['\u2713', 'A good share of the targets met']
    : ratio >= 0.33 ? ['~', 'Only part of the targets met'] : ['\u2717', 'Few targets met'])]);

  const strengths = [];
  if (expectancy > 0) strengths.push('Positive expectancy: every trade has an expected value.');
  if (roi > 0 && car > 0) strengths.push('Positive ROI and CAR: the strategy makes money over the window.');
  if (win >= 40 && win <= 60) strengths.push('Win rate average for directional strategies.');
  const weaknesses = [];
  if (pf < 1.5) weaknesses.push('Weak profit factor: a thin edge, sensitive to costs and slippage live.');
  if (mdd > 20) weaknesses.push('Very high max drawdown: hard to live through, and risky.');
  if (sharpe < 1) weaknesses.push('Below-par Sharpe ratio: weak volatility-adjusted return.');
  if (rr < 1) weaknesses.push('Unfavourable risk-reward: average losses above average wins.');
  if (ulcer > 8) weaknesses.push('High Ulcer index: significant drawdown stress.');
  if (m && !m.ok) weaknesses.unshift(`Out of margin at ${m.leverage}:1 leverage: the account could not have opened every trade.`);
  const todo = [];
  if (m && !m.ok) todo.push('Lower the risk a trade, or raise the leverage, until the free margin stays positive.');
  if (rr < 1) todo.push('Improve the risk-reward (a trailing stop, wider take-profits, entry filters).');
  if (mdd > 15) todo.push('Reduce the drawdown (dynamic position sizing, tighter stops, regime filters).');
  todo.push('Validate out of sample and with a walk-forward analysis, to check it holds.');

  return {
    summary: m && !m.ok
      ? 'The account does not hold this run: at this leverage it runs out of margin, and a broker would have refused trades.'
      : !profitable
      ? 'Not profitable, or no clear edge: some of the key metrics (profit factor, expectancy, ROI) are not positive.'
      : weak ? 'Profitable but weak: a real edge with thin safety margins. Almost every risk-adjusted and risk metric is below the usual targets of a robust strategy.'
      : 'Profitable and sound overall: a clear edge and acceptable risk-adjusted metrics.',
    table, strengths, weaknesses, todo,
    verdict: m && !m.ok ? 'Not fit for live trading at this leverage: the account runs out of margin.'
      : !profitable ? 'Not fit for live trading: redesign it, or drop it.'
      : weak ? 'Fine for research and paper trading, not ready for live with significant capital.'
      : 'A candidate for live trading, after an out-of-sample validation and operational checks.',
  };
}

// the analysis as nodes, from the score to the advice: the page puts them where it wants
function analysisNodes(row) {
  const a = analyse(row);
  const out = [];
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const list = (items) => {
    const ul = el('ul');
    for (const item of items.length ? items : ['none found']) ul.append(el('li', item));
    return ul;
  };
  // the score on top, as a bar: marked where middling and good begin
  const score = row.kpi.score;
  if (score !== null && score !== undefined) {
    const said = scoreVerdict(score);
    const bar = el('div', undefined, 'score-bar');
    bar.setAttribute('role', 'meter');
    bar.setAttribute('aria-label', 'score');
    bar.setAttribute('aria-valuemin', '0');
    bar.setAttribute('aria-valuemax', '100');
    bar.setAttribute('aria-valuenow', score.toFixed(1));
    const fill = el('span', undefined, said);
    fill.style.width = Math.max(0, Math.min(100, score)) + '%';
    bar.append(fill);
    const trades = row.report ? row.report.closedTrades : 0;
    out.push(el('p', `Score ${score.toFixed(1)} / 100 \u00b7 `
      + { good: 'good', ok: 'middling', bad: 'weak' }[said], 'score-head'), bar,
    el('p', 'return (CAR) 35% \u00b7 risk (max DD, Ulcer) 25% \u00b7 profit factor 20% \u00b7 Sharpe 20%'
      + (trades < 30 ? ` \u00b7 cut: ${trades} closed trades, full from 30` : ''), 'hint score-parts'));
  }
  out.push(el('p', a.summary, 'analysis-summary'));
  const table = el('table');
  table.append(el('thead'));
  table.tHead.insertRow().append(...['KPI', 'Value', 'Verdict', 'Why'].map((h) => el('th', h)));
  const tbody = el('tbody');
  const cls = { '\u2713': 'good', '~': 'ok', '\u2717': 'bad' };
  for (const [label, value, mark, reason] of a.table) {
    const tr = tbody.insertRow();
    tr.append(el('td', label), el('td', value, 'num'), el('td', mark, cls[mark]), el('td', reason));
  }
  table.append(tbody);
  out.push(table,
    el('h3', 'Overall'), el('p', 'Strengths:'), list(a.strengths),
    el('p', 'Critical weaknesses:'), list(a.weaknesses),
    el('h3', 'What I would do'), el('p', 'Verdict: ' + a.verdict),
    el('p', 'Concrete steps:'), list(a.todo));
  return out;
}
