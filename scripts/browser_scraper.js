// Alternative to scripts/00_scrape_telegram.py for when Python cannot reach t.me.
// Open https://t.me/s/kpszsu in a browser and paste this into the DevTools console.
//
// It walks the public web preview backwards (20 posts per page, "?before=<id>"),
// starting from the newest post, until it reaches SINCE or a post with
// id <= STOP_AT_ID (use the max id of data/raw/telegram_kpszsu.csv for an
// incremental update). Posts are saved as CSV parts (id, published_at, text)
// every PART_PAGES pages; downloads go through a queue, one at a time, because
// browsers silently drop simultaneous downloads (this lost data once).
// Merge the parts with:  python scripts/00_merge_browser_parts.py ~/Downloads
//
// Progress: window.__tg
const CHANNEL = 'kpszsu';
const SINCE = Date.parse('2025-10-01T00:00:00Z');
const STOP_AT_ID = 0;          // e.g. 84887 to fetch only posts newer than that
const PART_PAGES = 300;
const PAUSE_MS = 700;

(async () => {
  const S = window.__tg = {pages: 0, posts: 0, parts: 0, oldest: null, done: false, error: null};
  const q = s => '"' + String(s).replace(/"/g, '""') + '"';
  let queue = Promise.resolve();
  const save = rows => {
    if (!rows.length) return;
    const n = ++S.parts;
    const csv = 'id,published_at,text\n' + rows.map(r => [r[0], r[1], q(r[2])].join(',')).join('\n') + '\n';
    queue = queue.then(async () => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([csv], {type: 'text/csv'}));
      a.download = `tg_${CHANNEL}_part_${String(n).padStart(3, '0')}.csv`;
      document.body.appendChild(a); a.click(); a.remove();
      await new Promise(z => setTimeout(z, 3000));   // let the download finish
    });
  };
  const fetchPage = async before => {
    for (let t = 0; t < 6; t++) {
      try {
        const r = await fetch(`/s/${CHANNEL}` + (before ? `?before=${before}` : ''));
        if (r.ok) return await r.text();
      } catch (e) { /* retry */ }
      await new Promise(z => setTimeout(z, 5000 * (t + 1)));
    }
    throw new Error('fetch failed at before=' + before);
  };
  let rows = [], before = null;
  try {
    while (true) {
      const doc = new DOMParser().parseFromString(await fetchPage(before), 'text/html');
      const ms = [...doc.querySelectorAll('.tgme_widget_message[data-post]')];
      if (!ms.length) break;
      let minId = Infinity, minTs = Infinity;
      for (const m of ms) {
        const id = +m.dataset.post.split('/')[1];
        const t = m.querySelector('time')?.getAttribute('datetime');
        if (!t || id <= STOP_AT_ID) continue;
        const tx = m.querySelector('.tgme_widget_message_text');
        let text = '';
        if (tx) {
          const c = tx.cloneNode(true);
          c.querySelectorAll('br').forEach(b => b.replaceWith('\n'));
          text = c.textContent.replace(/[ \t]+/g, ' ').trim();
        }
        rows.push([id, t, text]); S.posts += 1;
        if (id < minId) { minId = id; minTs = Date.parse(t); }
      }
      S.pages += 1;
      if (minId !== Infinity) S.oldest = new Date(minTs).toISOString();
      if (S.pages % PART_PAGES === 0) { save(rows); rows = []; }
      if (minId === Infinity || minId <= STOP_AT_ID + 1 || minTs < SINCE || minId <= 1) break;
      before = minId;
      await new Promise(z => setTimeout(z, PAUSE_MS));
    }
  } catch (e) { S.error = String(e); }
  save(rows);
  await queue;
  S.done = true;
})();
