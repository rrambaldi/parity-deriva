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
  ['ROI', 'roi', kpiPct, (v) => v <= 0 ? ['\u2717', 'Negativo'] : ['\u2713', 'Positivo']],
  ['CAR', 'car', kpiPct, (v) => v <= 0 ? ['\u2717', 'Negativo'] : ['\u2713', 'Positivo']],
  ['Profit Factor', 'profitFactor', kpiNum, (v) => v < 1 ? ['\u2717', 'Non profittevole (PF < 1)']
    : v < 1.5 ? ['\u2717', 'Debole: edge esiguo, sensibile a costi/slippage']
    : v < 2 ? ['\u2713', 'Buono'] : ['\u2713', 'Ottimo']],
  ['Expectancy', 'expectancy', kpiAmount, (v) => v <= 0 ? ['\u2717', 'Non sostenibile (expectancy \u2264 0)'] : ['\u2713', 'Positiva']],
  ['Win Rate', 'winRate', kpiPercent, (v) => (v *= 100) >= 40 && v <= 60 ? ['~', 'Nella media per strategie direzionali']
    : v < 40 ? ['\u2717', 'Basso'] : ['\u2713', 'Alto']],
  ['Max Drawdown', 'maxDrawdownPct', kpiPct, (v) => v < 10 ? ['\u2713', 'Drawdown basso']
    : v < 15 ? ['~', 'Drawdown moderato'] : v < 20 ? ['\u2717', 'Drawdown alto']
    : ['\u2717', 'Drawdown molto alto, psicologicamente difficile']],
  ['Risk-Reward', 'riskReward', kpiNum, (v) => v < 1 ? ['\u2717', 'Sfavorevole: perdite medie > guadagni medi']
    : v < 2 ? ['~', 'Neutro/moderato'] : ['\u2713', 'Favorevole']],
  ['Sharpe', 'sharpe', kpiNum, (v) => v < 1 ? ['\u2717', 'Sub-par: rendimento aggiustato per volatilit\u00e0 debole']
    : v < 2 ? ['\u2713', 'Buono'] : ['\u2713', 'Ottimo']],
  ['CAR/MDD', 'carMdd', kpiNum, (v) => v < 0.3 ? ['~', 'Basso: rendimento \u201ccostoso\u201d in termini di drawdown']
    : v < 0.6 ? ['~', 'Moderato'] : ['\u2713', 'Buono']],
  ['Ulcer', 'ulcer', kpiNum, (v) => v < 6 ? ['\u2713', 'Stress da drawdown contenuto']
    : v < 8 ? ['~', 'Moderato'] : ['\u2717', 'Elevato: periodi di sofferenza prolungati']],
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
    return v === null || v === undefined ? [label, 'n/a', '', 'non calcolabile su questo run']
      : [label, format(v), ...judge(v)];
  });
  const m = row.margin;
  if (m) {
    const day = (ms) => new Date(ms).toISOString().slice(0, 10);
    table.push(['Margine', `${m.leverage}:1 \u00b7 picco ${m.peakMarginPct.toFixed(1)}%`,
      ...(m.ok ? ['\u2713', `Sempre margine libero, al minimo ${kpiAmount(m.minFree)}`]
        : m.negativeAt !== null ? ['\u2717', `Il conto va a zero il ${day(m.negativeAt)}`]
        : ['\u2717', `Fuori margine il ${day(m.breachAt)}: il conto non apre il trade`])]);
  }
  table.push(['Targets Met', `${met}/${total}`, ...(ratio >= 0.66 ? ['\u2713', 'Buona percentuale di target soddisfatti']
    : ratio >= 0.33 ? ['~', 'Solo una parte dei target soddisfatti'] : ['\u2717', 'Pochi target soddisfatti'])]);

  const strengths = [];
  if (expectancy > 0) strengths.push('Expectancy positiva: ogni trade genera valore atteso.');
  if (roi > 0 && car > 0) strengths.push('ROI e CAR positivi: la strategia \u00e8 redditizia nel periodo analizzato.');
  if (win >= 40 && win <= 60) strengths.push('Win rate nella media per strategie direzionali.');
  const weaknesses = [];
  if (pf < 1.5) weaknesses.push('Profit factor debole: edge esiguo, sensibile a costi e slippage in live.');
  if (mdd > 20) weaknesses.push('Max drawdown molto alto: psicologicamente difficile e rischioso.');
  if (sharpe < 1) weaknesses.push('Sharpe ratio sub-par: rendimento aggiustato per volatilit\u00e0 debole.');
  if (rr < 1) weaknesses.push('Risk-reward sfavorevole: perdite medie superiori ai guadagni medi.');
  if (ulcer > 8) weaknesses.push('Ulcer index elevato: stress da drawdown significativo.');
  if (m && !m.ok) weaknesses.unshift(`Fuori margine a leva ${m.leverage}:1: il conto non avrebbe potuto aprire tutti i trade.`);
  const todo = [];
  if (m && !m.ok) todo.push('Ridurre il rischio per trade o alzare la leva, finch\u00e9 il margine libero resta sempre positivo.');
  if (rr < 1) todo.push('Migliorare il risk-reward (es. trailing stop, take-profit pi\u00f9 ampi, filtri di ingresso).');
  if (mdd > 15) todo.push('Ridurre il drawdown (position sizing dinamico, stop pi\u00f9 stretti, filtri di regime).');
  todo.push('Validare su out-of-sample e con walk-forward analysis per verificare robustezza.');

  return {
    summary: m && !m.ok
      ? 'Il conto non regge questo run: a questa leva finisce fuori margine, e un broker avrebbe rifiutato dei trade.'
      : !profitable
      ? 'Strategia non profittevole o con edge non chiaro: alcune metriche fondamentali (profit factor, expectancy, ROI) non sono positive.'
      : weak ? 'Strategia profittevole ma debole: edge reale ma margini di sicurezza ridotti. Quasi tutte le metriche risk-adjusted e di rischio sono sotto i target tipici per una strategia robusta.'
      : 'Strategia profittevole e complessivamente solida: edge chiaro e metriche risk-adjusted accettabili.',
    table, strengths, weaknesses, todo,
    verdict: m && !m.ok ? 'Non adatta per live trading a questa leva: il conto resta senza margine.'
      : !profitable ? 'Non adatta per live trading; richiede riprogettazione o scarto.'
      : weak ? 'Accettabile per ricerca / paper trading, ma non pronta per live con capitale significativo.'
      : 'Candidata per live trading, previa validazione out-of-sample e controlli operativi.',
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
    for (const item of items.length ? items : ['nessuno']) ul.append(el('li', item));
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
      + { good: 'buono', ok: 'medio', bad: 'debole' }[said], 'score-head'), bar,
    el('p', 'guadagno (CAR) 35% \u00b7 rischio (max DD, Ulcer) 25% \u00b7 profit factor 20% \u00b7 Sharpe 20%'
      + (trades < 30 ? ` \u00b7 ridotto: ${trades} trade chiusi, pieno da 30` : ''), 'hint score-parts'));
  }
  out.push(el('p', a.summary, 'analysis-summary'));
  const table = el('table');
  table.append(el('thead'));
  table.tHead.insertRow().append(...['KPI', 'Valore', 'Giudizio', 'Motivazione'].map((h) => el('th', h)));
  const tbody = el('tbody');
  const cls = { '\u2713': 'good', '~': 'ok', '\u2717': 'bad' };
  for (const [label, value, mark, reason] of a.table) {
    const tr = tbody.insertRow();
    tr.append(el('td', label), el('td', value, 'num'), el('td', mark, cls[mark]), el('td', reason));
  }
  table.append(tbody);
  out.push(table,
    el('h3', 'Valutazione complessiva'), el('p', 'Punti di forza:'), list(a.strengths),
    el('p', 'Punti deboli critici:'), list(a.weaknesses),
    el('h3', 'Cosa farei / Raccomandazioni'), el('p', 'Classificazione: ' + a.verdict),
    el('p', 'Azioni concrete suggerite:'), list(a.todo));
  return out;
}
