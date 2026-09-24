/*
 * Collect the ForexFactory calendar from your own browser.
 *
 * The server cannot do this. The site sits behind a front end that refuses a
 * datacentre address after a handful of requests - this machine was answered
 * eight times and then 403 for everything - and no user agent string fixes
 * that. Your browser, on your own connection, is already allowed: it is a
 * person reading a calendar, which is what this is.
 *
 * It cannot be run from the parity-deriva page either, and that is the same
 * rule working as intended: a page on one site may not read another site's
 * pages. So it runs *on* forexfactory.com, where the calendar is same-origin
 * and the weeks are already in the page's own state, and it hands you a file
 * at the end. Nothing is sent anywhere by this script.
 *
 *     1. open https://www.forexfactory.com/calendar
 *     2. F12 -> Console
 *     3. paste this, press enter, leave the tab alone
 *     4. the weeks are pushed to parity-deriva as they are read
 *
 * Pushed, not downloaded, when the page you copied it from filled the two
 * blanks below with its own address and a token. The token is new every time
 * the service starts and is only in the script you pasted, so the service
 * listens to this and not to whatever else the calendar page is running.
 * Copied from the file instead, with the blanks still in it, it downloads
 * calendar.csv and you import that by hand.
 *
 * If a push fails - the service stopped, the browser refused the call - the
 * rows are not lost: they stay here and go out with the next push, and
 * whatever never made it is downloaded at the end.
 *
 * It asks for one week at a time with a pause between them, which is the
 * pace a person clicking would manage. Type `ffStop = true` in the console
 * to stop it early: it pushes what it has rather than losing it.
 */
window.ffStop = false;

/* filled in by the page that hands out this script; empty in the file */
const FF_PUSH_TO = '__PUSH_TO__';
const FF_TOKEN = '__TOKEN__';

/* the first week to read. Filled in by the page with the day your stores
   start, or with the day after the calendar already covers, so pasting the
   script twice does not read the same years twice. */
const FF_FROM = '__FROM__';

/* weeks between pushes. Often enough that closing the tab loses minutes and
   not hours, rarely enough that the service is not written to per week. */
const FF_EVERY = 25;

/*
 * Left on the window on purpose, so the console can ask for another stretch
 * without pasting the script again:
 *
 *     ffCollect('2008-01-01', '2015-01-01')   // older years
 *     ffCollect('2019-03-01', '2019-06-01')   // the weeks that failed
 *     ffCollect('2015-01-01', null, 4000)     // slower, if the site drags
 *
 * The service merges what arrives, so a stretch read twice costs time and
 * nothing else.
 */
window.ffCollect = async function collect(from, to = null, pause = 1500) {
  from = from || (FF_FROM.indexOf('__') === 0 ? '2015-01-01' : FF_FROM);
  const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun',
                  'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
  // the page's own word for each impact, spelled the way the importer spells
  // it. 'Holiday' and 'non-economic' are the same thing under two names.
  const IMPACT = { high: 'high', medium: 'medium', low: 'low',
                   'non-economic': 'non-economic', holiday: 'non-economic' };

  const week = (d) => `${MONTHS[d.getUTCMonth()]}${d.getUTCDate()}.${d.getUTCFullYear()}`;
  const pad = (n) => String(n).padStart(2, '0');
  const utc = (seconds) => {
    const d = new Date(seconds * 1000);
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
         + ` ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
  };

  /*
   * The days array out of the page's own state. Brackets are balanced rather
   * than matched with a pattern: an event called "Non-Farm Employment Change
   * [NFP]" closes a bracket the array never opened, and a regular expression
   * would stop there and take Monday for a week.
   */
  function daysOf(page) {
    const at = page.indexOf('window.calendarComponentStates[');
    if (at < 0) throw new Error('no calendar state in that page');
    let start = page.indexOf('days: [', at);
    if (start < 0) throw new Error('no days in the calendar state');
    start += 'days: '.length;
    let depth = 0;
    for (let i = start; i < page.length; i++) {
      if (page[i] === '[') depth++;
      else if (page[i] === ']' && --depth === 0) {
        return JSON.parse(page.slice(start, i + 1));
      }
    }
    throw new Error('the days array never closes');
  }

  const rows = new Map();   // one row per time+currency+title, like the importer
  let pushed = 0;           // how many of them the service has taken
  const add = (days) => {
    for (const day of days) {
      for (const event of (day.events || [])) {
        const currency = (event.currency || '').trim();
        // no time means "tentative", no currency means a site-wide note;
        // neither can be the middle of a window, so neither is collected
        if (!event.dateline || !currency) continue;
        const impact = (event.impactName || event.impactTitle || '')
          .trim().toLowerCase();
        const row = [utc(event.dateline), currency,
                     IMPACT[impact] || impact || 'unknown',
                     (event.name || '').trim()];
        rows.set(row[0] + '|' + row[1] + '|' + row[3], row);
      }
    }
  };

  const first = new Date(from + 'T00:00:00Z');
  const last = to ? new Date(to + 'T00:00:00Z') : new Date();
  const weeks = [];
  for (let d = new Date(first); d <= last; d.setUTCDate(d.getUTCDate() + 7)) {
    weeks.push(new Date(d));
  }

  console.log(`%cparity-deriva: ${weeks.length} weeks to read, about `
    + `${Math.round(weeks.length * (pause + 400) / 60000)} minutes. `
    + `Set ffStop = true to stop early.`, 'font-weight:bold');

  const csvOf = (list) => ['time,currency,impact,title'].concat(
    list.map((r) => r.map((cell) => (/[",]/.test(cell)
      ? '"' + cell.replace(/"/g, '""') + '"' : cell)).join(','))).join('\n');

  /*
   * Send what has not been sent. The service merges rather than replaces, so
   * sending a row twice is not a problem and sending none is not an error -
   * which is what makes "push what is new, keep the rest for next time" the
   * whole of the recovery story here.
   */
  async function push() {
    if (!FF_PUSH_TO || FF_PUSH_TO.indexOf('__') === 0) return false;
    const all = [...rows.values()].sort((a, b) => (a[0] < b[0] ? -1 : 1));
    const fresh = all.slice(pushed);
    if (!fresh.length) return true;
    try {
      const answer = await fetch(
        `${FF_PUSH_TO}/api/calendar?token=${encodeURIComponent(FF_TOKEN)}`,
        { method: 'POST', body: csvOf(fresh),
          headers: { 'Content-Type': 'text/plain' } });
      const said = await answer.json();
      if (!answer.ok || said.error) throw new Error(said.error || answer.status);
      pushed = all.length;
      console.log(`%cpushed ${fresh.length} rows, ${said.events} in the `
        + `calendar on the server`, 'color:#3ca370');
      return true;
    } catch (error) {
      console.warn(`push failed (${error.message}); keeping the rows here`);
      return false;
    }
  }

  let done = 0, failed = 0;
  for (const day of weeks) {
    if (window.ffStop) { console.log('stopped; keeping what is here'); break; }
    try {
      const page = await (await fetch(`/calendar?week=${week(day)}`,
                                      { credentials: 'include' })).text();
      add(daysOf(page));
      done++;
    } catch (error) {
      failed++;
      console.warn(`${week(day)}: ${error.message}`);
    }
    if (done % 10 === 0 || done === 1) {
      console.log(`${week(day)}  ${done}/${weeks.length} weeks, `
        + `${rows.size} events${failed ? `, ${failed} failed` : ''}`);
    }
    if (done && done % FF_EVERY === 0) await push();
    await new Promise((r) => setTimeout(r, pause));
  }

  // everything that is still here goes now; only what the service would not
  // take is worth a file
  if (await push()) {
    console.log(`%cparity-deriva: ${rows.size} events collected, all of them `
      + `on the server${failed ? ` (${failed} weeks failed - run it again for `
      + `those)` : ''}`, 'font-weight:bold');
    return;
  }

  const csv = csvOf([...rows.values()].sort((a, b) => (a[0] < b[0] ? -1 : 1)));

  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'calendar.csv';
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  console.log(`%cparity-deriva: ${rows.size} events in calendar.csv`
    + `${failed ? ` (${failed} weeks failed - run it again for those)` : ''}`,
    'font-weight:bold');
};

// and it starts straight away, on the stretch the page asked for
window.ffCollect();
