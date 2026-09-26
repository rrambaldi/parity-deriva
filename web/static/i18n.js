/*
 * The pages in the language chosen. English is what the code says, and it is
 * the key of every catalogue: a catalogue is {"English text": "its text"},
 * with {name} for a value the page puts in ("{n} running of {m}"), served by
 * api/i18n/<code> (web/i18n.py: the built-in web/static/i18n/<code>.json with
 * an uploaded one over it). A text with no entry stays English.
 *
 * Loaded before the other scripts of a page, so the catalogue is read - once,
 * before the first paint - and the page's own text is put in it as it is
 * drawn: every text node, title, placeholder and aria-label, and what the
 * page adds later (a MutationObserver). Code that draws on a canvas asks
 * t(text, values) itself. The switch in the header keeps the choice in this
 * browser only; English until one is made.
 */
const i18n = (() => {
  const KEY = 'parity-deriva.lang';
  let lang = 'en';
  try { lang = localStorage.getItem(KEY) || 'en'; } catch (error) { /* English */ }
  document.documentElement.lang = lang;

  let catalogue = {};
  if (lang !== 'en') {
    try {
      // synchronous on purpose: before the first paint, or the page would
      // show in English for a moment on every load
      const xhr = new XMLHttpRequest();
      xhr.open('GET', 'api/i18n/' + encodeURIComponent(lang), false);
      xhr.send();
      if (xhr.status === 200) catalogue = JSON.parse(xhr.responseText).texts || {};
    } catch (error) { /* no catalogue: English */ }
  }

  // plain entries by their text; those with {name} parts as patterns
  const plain = {};
  const patterns = [];
  const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // a text as the page has it, its runs of white space one space: an HTML
  // text across two lines of the source is one line on the page
  const squeeze = (text) => text.trim().replace(/\s+/g, ' ');
  for (const [raw, text] of Object.entries(catalogue)) {
    if (!text) continue;
    const key = squeeze(raw);
    if (!/\{\w+\}/.test(key)) { plain[key] = text; continue; }
    // "{a} of {b}" would take in any text with an "of" in it: a pattern
    // needs four letters of its own to be one
    if (key.replace(/\{\w+\}/g, '').replace(/[^A-Za-z]/g, '').length < 4) continue;
    const names = [];
    const source = key.split(/(\{\w+\})/).map((part) => {
      const name = /^\{(\w+)\}$/.exec(part);
      if (!name) return escape(part);
      names.push(name[1]);
      return '(.+?)';
    }).join('');
    patterns.push({ re: new RegExp('^' + source + '$', 's'), names, text });
  }
  // the longest first: "{n} runs saved" before "{n} runs"
  patterns.sort((a, b) => b.re.source.length - a.re.source.length);

  const fill = (text, values) => String(text).replace(/\{(\w+)\}/g,
    (all, name) => (values && Object.prototype.hasOwnProperty.call(values, name) ? values[name] : all));
  // nothing to say in a number, a date, a price or a sign
  const MUTE = /^[\d\s.,:;%+\-−–—·/()×$€£¥#|→←↑↓≤≥<>=*~^'"!?… ]*$/;
  const seen = new Map();
  function translate(text) {
    const core = squeeze(text);
    if (!core || MUTE.test(core)) return text;
    let out = seen.get(core);
    if (out === undefined) {
      out = Object.prototype.hasOwnProperty.call(plain, core) ? plain[core] : null;
      for (let i = 0; out === null && i < patterns.length; i++) {
        const found = patterns[i].re.exec(core);
        // a value is a piece of data, never a run of the page's " · " parts:
        // a pattern that would swallow one is not this text's
        if (found && found.slice(1).some((value) => value.includes(' · '))) continue;
        // a value put in is said in the language too, when it is a text of its own
        if (found) out = fill(patterns[i].text, Object.fromEntries(patterns[i].names.map((n, j) => [n, translate(found[j + 1])])));
      }
      if (seen.size > 20000) seen.clear();
      seen.set(core, out);
    }
    if (out === null) return text;
    // the white space around it kept: a text node between two tags leans on it
    const [, before, , after] = /^(\s*)([\s\S]*?)(\s*)$/.exec(text);
    return before + out + after;
  }

  // for code: a canvas's labels, an alert's text. The key is the English
  // template, values fill its {name} parts after it is translated
  function t(text, values) {
    return fill(Object.prototype.hasOwnProperty.call(catalogue, text) && catalogue[text] ? catalogue[text] : text, values);
  }

  const SKIP = new Set(['SCRIPT', 'STYLE', 'CODE', 'PRE', 'TEXTAREA', 'KBD', 'SAMP']);
  const ATTRS = ['title', 'placeholder', 'aria-label'];
  function walk(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      const parent = node.parentElement;
      if (!parent || SKIP.has(parent.nodeName) || parent.closest('[data-i18n-skip]')) return;
      const out = translate(node.nodeValue);
      if (out !== node.nodeValue) node.nodeValue = out;
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE || SKIP.has(node.nodeName) || node.hasAttribute('data-i18n-skip')) return;
    for (const name of ATTRS) {
      const value = node.getAttribute(name);
      if (value) {
        const out = translate(value);
        if (out !== value) node.setAttribute(name, out);
      }
    }
    for (const child of node.childNodes) walk(child);
  }

  if (lang !== 'en' && patterns.length + Object.keys(plain).length) {
    document.title = translate(document.title);
    walk(document.body);
    new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === 'childList') record.addedNodes.forEach(walk);
        else walk(record.target);
      }
    }).observe(document.body, { subtree: true, childList: true, characterData: true,
      attributes: true, attributeFilter: ATTRS });
  }

  /*
   * The switch, in the theme's box in the header: the languages there are
   * (api/i18n), each with how much of the pages it says. A choice reloads
   * the page, which reads its catalogue before it is drawn.
   */
  function switcher() {
    const brand = document.getElementById('brand');
    if (!brand || document.getElementById('lang')) return;
    const box = document.createElement('select');
    box.id = 'lang';
    box.setAttribute('aria-label', 'language');
    box.title = 'language: kept in this browser';
    box.add(new Option(lang.toUpperCase(), lang));
    // in the theme's box, first: one control less for a header on one line
    const theme = document.getElementById('theme');
    if (theme) theme.prepend(box); else brand.appendChild(box);
    fetch('api/i18n').then((r) => r.json()).then(({ languages }) => {
      box.textContent = '';
      // the code alone, to keep the header on one line; the name in its tip
      for (const l of languages || []) {
        const option = new Option(l.code.toUpperCase(), l.code);
        option.title = l.code === 'en' ? l.name : `${l.name} · ${Math.round(100 * l.share)}% translated`;
        box.add(option);
      }
      box.value = lang;
    }).catch(() => {});
    box.addEventListener('change', () => {
      try { localStorage.setItem(KEY, box.value); } catch (error) { /* this page only */ }
      location.reload();
    });
  }
  // the header is built by menu.js, after this script: once the page is in
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', switcher);
  else setTimeout(switcher);

  return { lang, t, translate };
})();
const t = i18n.t;
