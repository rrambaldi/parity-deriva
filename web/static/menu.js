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
    // the theme switch's three: the system's screen, the sun, the moon
    system: svg('<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>'),
    light: svg('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4'
      + 'M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
    dark: svg('<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>'),
  };
  // the home page is the simulation, at / and at the older /sim
  const page = { '': 'simulate', sim: 'simulate', mix: 'mix', live: 'live', settings: 'settings' }[
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
   * How loaded the server is, before the pages: CPU, memory and every disk,
   * from the same api/busy. A bar each with its value written after it: the
   * bar is green, and red from HIGH up - the value says it too, so the
   * colour is never the only sign (web/DESIGN.md § 1).
   */
  const HIGH = 85;
  const server = document.createElement('span');
  server.id = 'server';
  server.hidden = true;
  brand.insertBefore(server, nav);
  const gb = (bytes) => (bytes / 2 ** 30).toFixed(1);
  const pct = (used, of) => Math.round(100 * used / of) + '%';
  const meter = (name, share, value) => {
    const p = Math.max(0, Math.min(100, share));
    return `<span class="load${p >= HIGH ? ' high' : ''}"><span class="load-name">${name}</span>`
      + `<span class="load-bar" aria-hidden="true"><span style="width:${p.toFixed(0)}%"></span></span>`
      + `<span class="load-value">${value}</span></span>`;
  };
  const showServer = (m) => {
    // df's use%: the space kept for root is neither used nor free for us
    const disks = m.disks.map((d) => ({ ...d, name: '/' + d.path.split('/')[1],
      pct: pct(d.used, d.used + d.free) }));
    server.innerHTML = meter('cpu', m.cpu, Math.round(m.cpu) + '%')
      + meter('ram', 100 * m.memUsed / m.memTotal, `${gb(m.memUsed)}/${gb(m.memTotal)}G`)
      + disks.map((d) => meter(d.name, 100 * d.used / (d.used + d.free), d.pct)).join('');
    server.title = `the server, red from ${HIGH}%: cpu ${m.cpu.toFixed(1)}% of ${m.cpus} cores\n`
      + `ram ${gb(m.memUsed)} of ${gb(m.memTotal)} GB in use (${pct(m.memUsed, m.memTotal)})`
      + (m.swapTotal ? `, swap ${gb(m.swapUsed)} of ${gb(m.swapTotal)} GB` : '')
      + disks.map((d) => `\ndisk ${d.path}: ${gb(d.used)} GB used, ${gb(d.free)} GB free (${d.pct})`).join('');
    server.hidden = false;
  };

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
        if (busy.server) showServer(busy.server);
      } catch (error) { /* no answer, no light: the page itself is unaffected */ }
    }
    setTimeout(light, 5000);
  };
  light();
})();
