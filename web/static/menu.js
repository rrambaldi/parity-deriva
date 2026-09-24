/*
 * The menu every page shares, fixed at the top right: simulate, live,
 * settings. One copy here rather than one per page.
 *
 * There is no backtest entry: one backtest is a simulation of one set of
 * values, and one run of a set opens on the run page (run.html), which is
 * reached from the set and not from here.
 * Links are relative, like the stylesheet, so a /parity/ prefix needs nothing.
 */
(function () {
  const svg = (d) => '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" '
    + 'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + d + '</svg>';
  const ICONS = {
    simulate: svg('<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/>'
      + '<circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>'),
    live: svg('<circle cx="12" cy="12" r="2"/><path d="M8.5 8.5a5 5 0 0 0 0 7M15.5 8.5a5 5 0 0 1 0 7'
      + 'M5.6 5.6a9 9 0 0 0 0 12.8M18.4 5.6a9 9 0 0 1 0 12.8"/>'),
    settings: svg('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1'
      + 'a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3'
      + 'l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0'
      + ' 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3'
      + 'a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3'
      + ' 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
  };
  // the home page is the simulation, at / and at the older /sim
  const page = { '': 'simulate', sim: 'simulate', live: 'live', settings: 'settings' }[
    location.pathname.split('/').pop()];

  const nav = document.createElement('nav');
  nav.id = 'menu';
  nav.setAttribute('aria-label', 'pages');
  const item = (name, href, title) => {
    const el = document.createElement('a');
    el.href = href;
    if (name === page) el.setAttribute('aria-current', 'page');
    el.title = title;
    el.innerHTML = ICONS[name] + '<span>' + name + '</span>';
    nav.appendChild(el);
  };
  item('simulate', './', "one run, or every combination of a strategy's parameters");
  item('live', 'live', 'the sessions trading on the accounts, as they go');
  item('settings', 'settings', 'data: stores and imports');
  document.body.prepend(nav);
})();
