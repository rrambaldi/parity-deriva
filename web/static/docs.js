/*
 * The docs page: how a strategy is written, and the helpers it is built from.
 *
 * Everything that changes with the code comes from api/mcp/docs - the imports
 * allowed, the rules the assistants are held to, and the reference, which the
 * service reads off the source of the helper modules (web/mcp.py reference).
 * A docstring is shown the way it is written: paragraphs, "* " lists, and
 * indented blocks as code, with **bold** and `code` inline.
 */

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// **bold** and `code` inside a line of a docstring, the rest as text
function inline(parent, text) {
  for (const part of text.split(/(\*\*[^*]+\*\*|`[^`]+`)/)) {
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      parent.appendChild(el('strong', '', part.slice(2, -2)));
    } else if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      parent.appendChild(el('code', '', part.slice(1, -1)));
    } else if (part) {
      parent.appendChild(document.createTextNode(part));
    }
  }
  return parent;
}

function docBlock(text) {
  const box = el('div', 'doc');
  for (const para of (text || '').split(/\n\s*\n/)) {
    const lines = para.split('\n').filter((l) => l.trim());
    if (!lines.length) continue;
    if (lines.every((l) => /^(\t| {2,})/.test(l))) {
      box.appendChild(el('pre', '', lines.map((l) => l.replace(/^(\t| {4}| {2})/, '')).join('\n')));
    } else if (/^\* /.test(lines[0])) {
      const list = el('ul');
      for (const l of lines) {
        if (/^\* /.test(l) || !list.lastChild) list.appendChild(el('li'));
        inline(list.lastChild, `${l.replace(/^\* /, '').trim()} `);
      }
      box.appendChild(list);
    } else {
      box.appendChild(inline(el('p'), lines.map((l) => l.trim()).join(' ')));
    }
  }
  return box;
}

// a method's arguments without the self every one of them starts with
const args = (text) => text.replace(/^self(, )?/, '');

function constants(rows) {
  const list = el('dl', 'doc-constants');
  for (const c of rows) {
    list.appendChild(el('dt', 'doc-sig', `${c.name} = ${c.value}`));
    list.appendChild(el('dd', '', c.doc));
  }
  return list;
}

function render(module) {
  const box = el('article', 'doc-module');
  box.id = module.module;
  box.appendChild(el('h3', 'doc-module-name', module.module));
  if (module.doc) {
    const about = el('details', 'doc-about');
    about.appendChild(el('summary', '', 'about the module'));
    about.appendChild(docBlock(module.doc));
    box.appendChild(about);
  }
  if (module.constants.length) box.appendChild(constants(module.constants));
  for (const item of module.items) {
    const entry = el('div', 'doc-item');
    entry.dataset.name = item.name.toLowerCase();
    entry.appendChild(el('h4', 'doc-sig', item.kind === 'class'
      ? `class ${item.name}(${item.args})` : `${item.name}(${args(item.args)})`));
    entry.appendChild(docBlock(item.doc));
    if ((item.constants || []).length) entry.appendChild(constants(item.constants));
    for (const method of item.methods || []) {
      const row = el('div', 'doc-method');
      row.dataset.name = `${item.name}.${method.name}`.toLowerCase();
      row.appendChild(el('div', 'doc-sig', `${item.name}.${method.name}(${args(method.args)})`));
      row.appendChild(docBlock(method.doc));
      entry.appendChild(row);
    }
    box.appendChild(entry);
  }
  return box;
}

// a helper is kept when its name or its words hold what is typed; a class
// whose own name matches keeps all its methods
function filter(query) {
  const q = query.trim().toLowerCase();
  for (const item of document.querySelectorAll('.doc-item')) {
    const own = !q || item.dataset.name.includes(q)
      || item.querySelector(':scope > .doc').textContent.toLowerCase().includes(q);
    let any = false;
    for (const method of item.querySelectorAll('.doc-method')) {
      const hit = own || method.dataset.name.includes(q) || method.textContent.toLowerCase().includes(q);
      method.hidden = !hit;
      any = any || hit;
    }
    item.hidden = !(own || any);
  }
  for (const module of document.querySelectorAll('.doc-module')) {
    module.hidden = Boolean(q) && !module.querySelector('.doc-item:not([hidden])');
  }
}

async function load() {
  const response = await fetch('api/mcp/docs');
  const docs = await response.json();
  if (!response.ok || docs.error) throw new Error(docs.error || response.statusText);

  $('docs-allowed').replaceChildren(...docs.allowed.map((m) => el('code', '', m)));
  $('docs-refused').replaceChildren(...docs.refused.map((m) => el('code', '', m)));
  // the numbered rules at the end of what the assistants are told
  const rules = (docs.guide.split('\nRules:\n')[1] || '').split(/\n(?=\d+\. )/);
  $('docs-rules').replaceChildren(...rules.filter((r) => r.trim()).map(
    (r) => inline(el('li'), r.replace(/^\d+\.\s*/, '').replace(/\s+/g, ' '))));
  $('docs-guide-text').textContent = docs.guide;

  $('docs-modules').replaceChildren(...docs.reference.map((m) => {
    const link = el('a', '', m.module.replace(/^parity_deriva\./, ''));
    link.href = `#${m.module}`;
    return link;
  }));
  $('docs-reference').replaceChildren(...docs.reference.map(render));
  filter($('docs-filter').value);
  // a link to a module, made before the page had drawn it
  follow();
}

// the tab a panel is in, opened; the address says which, for a link to it
const tabs = [...document.querySelectorAll('#docs-tabs [role="tab"]')];

function openTab(tab, focus) {
  for (const t of tabs) {
    const on = t === tab;
    t.setAttribute('aria-selected', String(on));
    t.tabIndex = on ? 0 : -1;
    $(t.getAttribute('aria-controls')).hidden = !on;
  }
  if (focus) tab.focus();
}

// the tab holding what the address points at - a panel, or a part of one
function follow() {
  const target = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
  const panel = target && target.closest('[role="tabpanel"]');
  if (!panel) return;
  openTab(tabs.find((t) => t.getAttribute('aria-controls') === panel.id));
  if (target !== panel) target.scrollIntoView();
}

for (const tab of tabs) {
  tab.addEventListener('click', () => {
    openTab(tab);
    history.replaceState(null, '', `#${tab.getAttribute('aria-controls')}`);
  });
  // the arrows move along the tabs, as a tab list does
  tab.addEventListener('keydown', (event) => {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
    if (!step) return;
    const next = tabs[(tabs.indexOf(tab) + step + tabs.length) % tabs.length];
    openTab(next, true);
    history.replaceState(null, '', `#${next.getAttribute('aria-controls')}`);
  });
}
window.addEventListener('hashchange', follow);
follow();

$('docs-filter').addEventListener('input', (event) => filter(event.target.value));
load().catch((error) => $('docs-reference').replaceChildren(el('p', 'docs-note', String(error.message || error))));
