/*
 * The menu every page shares, at the right of the page's header: simulate,
 * mix, live, settings - and after it the theme switch. One copy here rather
 * than one per page.
 *
 * There is no backtest entry: one backtest is a simulation of one set of
 * values, and one run of a set opens on the run page (run.html), which is
 * reached from the set and not from here.
 * Links are relative, like the stylesheet, so a /parity/ prefix needs nothing.
 *
 * A sweep and a live session go on in the service whether a page is open or
 * not, so every page asks api/busy every few seconds and the entry of what
 * is going lights up: see #menu a.busy in app.css.
 */

/*
 * The colours of the theme on show, for the canvases. A canvas cannot read a
 * CSS variable, so every draw asks for them again: both themes use the same
 * names (tokens.css), and no script ever knows which one it is drawing in.
 */
function palette() {
  const style = getComputedStyle(document.documentElement);
  const read = (name) => style.getPropertyValue('--' + name).trim();
  return {
    panel: read('panel'), line: read('line'), border: read('border'), grid: read('grid'),
    text: read('text'), text2: read('text-2'), text3: read('text-3'),
    entry: read('entry'), up: read('up'), down: read('down'),
    exit: read('mark-exit'), trail: read('mark-trail'), span: read('trade-span'),
    mono: read('font-mono'),
  };
}

/*
 * A series that is one of many - a mix's runs, a strategy's curves, the live
 * page's feeds - is told apart by its dash and not by a colour of its own:
 * the data has three colours and a fourth does not pass (web/DESIGN.md § 3).
 * The legend draws its sample with the same dash, so the two cannot drift.
 */
const DASHES = [[], [6, 4], [2, 3], [10, 3, 2, 3], [1, 5], [12, 6], [4, 2, 1, 2], [8, 2]];
function dashSample(dash) {
  return '<svg class="dash" width="18" height="6" aria-hidden="true"><line x1="0" y1="3" x2="18" y2="3"'
    + ` stroke="currentColor" stroke-width="2" stroke-dasharray="${dash.join(' ')}"/></svg>`;
}

/*
 * The candle charts' shared pieces - app.js on the run page, livechart.js on
 * the live one - so the two answer the same keys and tag their axes alike.
 */

// A label in a filled box: the crosshair's price on the price axis ('left':
// right edge at x, middle at y), its time on the time axis ('bottom': middle
// at x, top at y), a measure by the pointer ('at': top left corner at x, y).
// Kept inside the canvas, whichever side it would have run off.
function axisTag(context, pal, text, x, y, side, fill) {
  context.font = '11px ' + pal.mono;
  const w = context.measureText(text).width + 10, h = 17;
  const cw = context.canvas.clientWidth, ch = context.canvas.clientHeight;
  let left = side === 'left' ? x - w : side === 'bottom' ? x - w / 2 : x;
  let top = side === 'left' ? y - h / 2 : y;
  left = Math.max(0, Math.min(cw - w, left));
  top = Math.max(0, Math.min(ch - h, top));
  context.fillStyle = fill || pal.text;
  context.fillRect(left, top, w, h);
  context.fillStyle = pal.panel;
  context.textAlign = 'center';
  context.textBaseline = 'middle';
  context.fillText(text, left + w / 2, top + h / 2 + 0.5);
}

// The chart's keys, the same on both pages. Shift and the arrows pan: the
// arrows on their own already walk the runs and the trades on the run page.
// Nothing is taken while a field has the focus or a dialog is open.
function chartKey(event) {
  if (event.ctrlKey || event.altKey || event.metaKey) return null;
  if (event.target.closest && event.target.closest('input, select, textarea, button, dialog')) return null;
  if (document.querySelector('dialog[open]')) return null;
  if (event.shiftKey && event.key === 'ArrowLeft') return 'left';
  if (event.shiftKey && event.key === 'ArrowRight') return 'right';
  if (event.key === '+' || event.key === '=') return 'in';
  if (event.key === '-' || event.key === '_') return 'out';
  if (event.key === 'Home') return 'home';
  if (event.key === 'End') return 'end';
  if (event.key === 'f' || event.key === 'F') return 'fit';
  return null;
}

// What a shift-drag measured from a to b, each {ms, price}: the move in pips
// and in percent, the bars it took (of the timeframe drawn, when it is known)
// and the time
function measured(a, b, pip, bars, timeframe) {
  const move = b.price - a.price, sign = move < 0 ? '-' : '+';
  const minutes = Math.round(Math.abs(b.ms - a.ms) / 60000);
  const time = [[Math.floor(minutes / 1440), 'd'], [Math.floor(minutes % 1440 / 60), 'h'], [minutes % 60, 'm']]
    .filter(([n]) => n).map(([n, unit]) => n + unit).join(' ') || '0m';
  return `${sign}${Math.abs(move / pip).toFixed(1)} pips · ${sign}${Math.abs(move / a.price * 100).toFixed(2)}%`
    + ` · ${bars} ${timeframe ? timeframe + ' ' : ''}bars · ${time}`;
}

/*
 * A parameter's value as the tables print it: the form's switches, 0 or 1
 * on the wire, as Y and N (the grid's own y and n boxes, sim.html), and an
 * empty one as none - the strategy's own.
 */
const SWITCH_PARAMS = ['intraday', 'inverse', 'trailing', 'trailProfit'];
function paramValue(name, value) {
  if (value === '' || value === null || value === undefined) return 'none';
  if (SWITCH_PARAMS.includes(name) && (String(value) === '0' || String(value) === '1')) {
    return String(value) === '1' ? 'Y' : 'N';
  }
  return String(value);
}

/*
 * What an account on margin needed to carry a run (report.margin in
 * performance/report.py), in one line: every page that shows a run says it.
 */
function marginText(m) {
  if (!m) return 'margin: n/a, no saved trades';
  const day = (ms) => new Date(ms).toISOString().slice(0, 10);
  const money = (v) => (v < 0 ? '\u2212' : '') + Math.abs(v).toFixed(2);
  return `margin at ${m.leverage}:1 \u00b7 peak ${money(m.peakMargin)} (${m.peakMarginPct.toFixed(1)}% of capital)`
    + (m.minFree === null ? '' : ` \u00b7 lowest free ${money(m.minFree)}`)
    + ` \u00b7 at most ${m.maxOpen} open`
    + (m.negativeAt !== null ? ` \u00b7 \u2717 the account goes to zero on ${day(m.negativeAt)}`
      : m.breachAt !== null ? ` \u00b7 \u2717 out of margin on ${day(m.breachAt)}` : ' \u00b7 \u2713 always room');
}

(function () {
  const svg = (d) => '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" '
    + 'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + d + '</svg>';
  const ICONS = {
    simulate: svg('<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/>'
      + '<circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>'),
    mix: svg('<path d="m12 2 10 5-10 5L2 7l10-5z"/><path d="m2 12 10 5 10-5"/><path d="m2 17 10 5 10-5"/>'),
    live: svg('<circle cx="12" cy="12" r="2"/><path d="M8.5 8.5a5 5 0 0 0 0 7M15.5 8.5a5 5 0 0 1 0 7'
      + 'M5.6 5.6a9 9 0 0 0 0 12.8M18.4 5.6a9 9 0 0 1 0 12.8"/>'),
    settings: svg('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1'
      + 'a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3'
      + 'l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0'
      + ' 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3'
      + 'a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3'
      + ' 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
    docs: svg('<path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v17H6.5A2.5 2.5 0 0 0 4 21.5z"/><path d="M4 21.5A2.5 2.5 0 0 1 6.5 19H20v3H6.5"/><path d="M8 7h8M8 11h6"/>'),
    // the theme switch's three: the system's screen, the sun, the moon
    system: svg('<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>'),
    light: svg('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4'
      + 'M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
    dark: svg('<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>'),
  };
  // the home page is the simulation, at / and at the older /sim
  const page = { '': 'simulate', sim: 'simulate', mix: 'mix', live: 'live', settings: 'settings', docs: 'docs' }[
    location.pathname.split('/').pop()];

  const nav = document.createElement('nav');
  nav.id = 'menu';
  nav.setAttribute('aria-label', 'pages');
  const item = (name, href, title) => {
    const el = document.createElement('a');
    el.href = href;
    el.dataset.page = name;
    if (name === page) el.setAttribute('aria-current', 'page');
    el.title = title;
    el.innerHTML = ICONS[name] + '<span>' + name + '</span>';
    nav.appendChild(el);
  };
  item('simulate', './', "one run, or every combination of a strategy's parameters");
  item('mix', 'mix', 'runs of different sets traded side by side: their capitals added up');
  item('live', 'live', 'the sessions trading on the accounts, as they go');
  item('settings', 'settings', 'data: stores and imports; AI assistants (MCP)');
  item('docs', 'docs', 'how a strategy is written, and the helpers it is built from');
  const brand = document.getElementById('brand');
  brand.appendChild(nav);

  /*
   * A spinner while the page waits on the service, after the badge. Every
   * fetch of the page counts, but only one that takes a while shows it: a
   * poll that answers at once would make it blink. The menu's own api/busy
   * goes round it, for the same reason.
   */
  const spinner = document.createElement('span');
  spinner.id = 'spinner';
  spinner.setAttribute('role', 'status');
  spinner.setAttribute('aria-label', 'loading');
  spinner.hidden = true;
  document.getElementById('env-badge').after(spinner);
  // what the server trades, demo accounts or real money (PARITY_DERIVA_ACCOUNTS
  // in .env, never set from a page): beside the page's own badge, on every page
  const accounts = document.createElement('span');
  accounts.id = 'accounts-badge';
  accounts.hidden = true;
  document.getElementById('env-badge').after(accounts);
  const plain = window.fetch.bind(window);
  let waiting = 0;
  window.fetch = (...args) => {
    waiting++;
    const later = setTimeout(() => { spinner.hidden = false; }, 250);
    return plain(...args).finally(() => {
      clearTimeout(later);
      if (--waiting === 0) spinner.hidden = true;
    });
  };

  /*
   * The theme: the system's until the switch forces one. Three buttons, one
   * per choice, the one in force pressed - a picture each, so the switch says
   * what it is set to without a word. The choice is kept in this browser only,
   * and a browser that will not keep it still switches (the <head> of every
   * page reads it back before the first paint).
   */
  const current = document.documentElement.dataset.theme || 'system';
  const toggle = document.createElement('div');
  toggle.id = 'theme';
  toggle.setAttribute('role', 'group');
  toggle.setAttribute('aria-label', 'theme');
  // every page already redraws its canvases on resize; a new theme, or the
  // real font arriving, asks the same of them - draw again with what is on show
  const redraw = () => window.dispatchEvent(new Event('resize'));
  for (const [theme, title] of [['system', "the system's theme"], ['light', 'light'], ['dark', 'dark']]) {
    const button = document.createElement('button');
    button.type = 'button';
    button.title = title;
    button.setAttribute('aria-label', 'theme: ' + theme);
    button.setAttribute('aria-pressed', String(theme === current));
    button.innerHTML = ICONS[theme];
    button.addEventListener('click', () => {
      if (theme === 'system') delete document.documentElement.dataset.theme;
      else document.documentElement.dataset.theme = theme;
      try { localStorage.setItem('parity-deriva.theme', theme); } catch (error) { /* not kept, still switched */ }
      for (const other of toggle.children) other.setAttribute('aria-pressed', String(other === button));
      redraw();
    });
    toggle.appendChild(button);
  }
  brand.appendChild(toggle);
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', redraw);
  // the canvases measure their labels in the font they are given: until Plex
  // Mono is in they measure the fallback, so they are drawn again once it is
  document.fonts.load('11px "IBM Plex Mono"').then(redraw, () => {});

  /*
   * How loaded the server is: one chip before the theme switch, and the
   * detail in a panel on a click (web/DESIGN.md § 6, Risorse del server).
   * Was: four green meters always on show, some 690 px of header, green even
   * as they filled - and green here is long, a target, a gain. Now the chip
   * says "server ok", or names the worst measure; high is ink, critical is
   * red with a "!" - never the colour alone. Same api/busy, same round.
   */
  const HIGH = 80, CRIT = 90;
  const ROUND = 5000;
  const level = (v) => (v >= CRIT ? 'crit' : v >= HIGH ? 'warn' : '');
  const ICO = '<svg class="sys-ico" viewBox="0 0 12 12" aria-hidden="true"><path d="M6 1.2 11 10.4H1z" fill="currentColor"/>'
    + '<path d="M6 4.6v2.9M6 8.9v.1" style="stroke: var(--panel)" stroke-width="1.4" stroke-linecap="round"/></svg>';
  const sys = document.createElement('div');
  sys.className = 'sys';
  sys.id = 'sys';
  sys.innerHTML = '<button type="button" class="sys-chip" id="sys-chip" aria-expanded="false" aria-controls="sys-pop" aria-label="server resources">'
    + '<span class="sys-mini" aria-hidden="true"></span><span id="sys-text">server</span></button>'
    + '<div class="sys-pop" id="sys-pop" role="dialog" aria-labelledby="sys-title" hidden>'
    + '<h2 class="panel-title" id="sys-title">server</h2><div id="sys-rows"></div><p class="sys-foot" id="sys-foot"></p></div>';
  brand.insertBefore(sys, toggle);
  const chip = sys.querySelector('#sys-chip');
  const pop = sys.querySelector('#sys-pop');
  const mini = sys.querySelector('.sys-mini');
  const text = sys.querySelector('#sys-text');
  const rowsBox = sys.querySelector('#sys-rows');
  const foot = sys.querySelector('#sys-foot');

  const gb = (bytes) => (bytes / 2 ** 30).toFixed(1);
  // cpu, ram, then every disk the service lists, in its order; a disk by the
  // top of its path (/mnt), the whole path in its tooltip. A disk's share is
  // df's use%: the space kept for root is neither used nor free for us
  const measures = (m) => [
    { key: 'cpu', name: 'cpu', label: 'cpu', v: m.cpu, abs: `${m.cpus} cores` },
    { key: 'ram', name: 'ram', label: 'ram', v: 100 * m.memUsed / m.memTotal,
      abs: `${gb(m.memUsed)} / ${gb(m.memTotal)} GB` + (m.swapTotal ? ` · swap ${gb(m.swapUsed)} / ${gb(m.swapTotal)}` : '') },
    ...m.disks.map((d) => {
      const top = '/' + d.path.split('/')[1];
      return { key: d.path, name: 'disk ' + top, label: `disk <span class="path">${top}</span>`, path: d.path,
        v: 100 * d.used / (d.used + d.free), abs: `${gb(d.used)} / ${gb(d.used + d.free)} GB` };
    }),
  ].map((x) => ({ ...x, v: Math.round(Math.max(0, Math.min(100, x.v))) }));

  // the nodes are made once for a set of measures and then only updated: an
  // open panel must not close, nor lose the focus, every five seconds
  let shown = '';
  let rows = [];
  const build = (list) => {
    mini.innerHTML = list.map(() => '<i></i>').join('');
    rowsBox.innerHTML = list.map((x) => '<div class="sys-row" role="meter" aria-valuemin="0" aria-valuemax="100"'
      + ` aria-label="${x.name}"${x.path ? ` title="${x.path}"` : ''}><span class="l">${x.label}</span>`
      + '<span class="track"><span class="fill"></span></span><span class="v"></span><span class="abs"></span></div>').join('');
    rows = [...rowsBox.children];
    shown = list.map((x) => x.key).join('|');
  };
  let last = null; // the measures and the time of the last good answer
  const showServer = () => {
    const age = last ? Date.now() - last.at : Infinity;
    foot.textContent = (last ? `updated ${Math.round(age / 1000)} s ago · ` : 'no answer yet · ')
      + `high from ${HIGH} %, critical from ${CRIT} %`;
    if (age > 3 * ROUND) {
      // nothing for three rounds: say so, and keep the last figures in the panel
      chip.className = 'sys-chip stale';
      text.textContent = 'server ?';
      chip.title = last ? `no data since ${new Date(last.at).toISOString().slice(11, 19)} UTC` : 'no data yet';
      return;
    }
    const list = last.list;
    const bad = list.filter((x) => x.v >= HIGH).sort((a, b) => b.v - a.v);
    const worst = bad[0];
    chip.className = 'sys-chip' + (worst ? ' ' + level(worst.v) : '');
    text.innerHTML = !worst ? 'server ok'
      : (level(worst.v) === 'crit' ? ICO : '') + `${worst.name} <span class="v">${worst.v}%</span>`
        + (bad.length > 1 ? ` +${bad.length - 1}` : '');
    chip.title = list.map((x) => `${x.name} ${x.v} %`).join(' · ');
    list.forEach((x, i) => {
      const state = level(x.v);
      const bar = mini.children[i];
      bar.className = state;
      bar.style.height = Math.max(2, Math.round(x.v / 100 * 14)) + 'px';
      const row = rows[i];
      row.className = 'sys-row' + (state ? ' ' + state : '');
      row.setAttribute('aria-valuenow', x.v);
      row.setAttribute('aria-valuetext', `${x.v} %` + (state === 'crit' ? ' · critical' : state === 'warn' ? ' · high' : ''));
      row.querySelector('.fill').style.width = x.v + '%';
      row.querySelector('.v').innerHTML = (state === 'crit' ? ICO : '') + x.v + '%';
      row.querySelector('.abs').textContent = x.abs;
    });
  };
  const serverAnswer = (m) => {
    const list = measures(m);
    if (list.map((x) => x.key).join('|') !== shown) build(list);
    last = { at: Date.now(), list };
  };
  // the panel: the chip opens and closes it, a click outside or esc closes it,
  // and esc gives the focus back to the chip
  const openPop = (open) => {
    pop.hidden = !open;
    chip.setAttribute('aria-expanded', String(open));
  };
  chip.addEventListener('click', () => openPop(pop.hidden));
  document.addEventListener('click', (event) => { if (!pop.hidden && !sys.contains(event.target)) openPop(false); });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !pop.hidden) { openPop(false); chip.focus(); }
  });

  const titles = Object.fromEntries([...nav.children].map((el) => [el.dataset.page, el.title]));
  const light = async () => {
    // a tab in the background asks nothing; it asks again once shown
    if (!document.hidden) {
      try {
        const busy = await (await plain('api/busy')).json();
        for (const [name, on, what] of [['simulate', busy.simulate, 'a simulation is running'],
          ['live', busy.live, `${busy.live} live session${busy.live === 1 ? '' : 's'} running`]]) {
          const el = nav.querySelector(`[data-page="${name}"]`);
          el.classList.toggle('busy', !!on);
          el.title = titles[name] + (on ? ' - ' + what : '');
        }
        if (busy.server) serverAnswer(busy.server);
        if (busy.accounts) {
          const real = busy.accounts === 'real';
          accounts.hidden = false;
          accounts.className = 'badge ' + (real ? 'live' : 'practice');
          accounts.textContent = real ? 'real money server' : 'demo server';
          accounts.title = (real ? 'this server trades real money accounts only'
            : 'this server trades demo accounts only') + ' (PARITY_DERIVA_ACCOUNTS in .env)';
          document.documentElement.dataset.accounts = busy.accounts;
        }
      } catch (error) { /* no answer, no light: the page itself is unaffected */ }
      showServer();
    }
    setTimeout(light, ROUND);
  };
  light();
})();
