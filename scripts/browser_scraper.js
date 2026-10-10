// Alternative to scripts/00_scrape_telegram.py when Python cannot reach t.me.
// Open https://t.me/s/kpszsu in a browser, paste this into the DevTools console.
// It walks the public web preview backwards (20 posts per page) with 3 parallel
// workers over post-ID ranges and downloads CSV parts (id, published_at, text)
// every 300 pages. Merge them with: python scripts/00_merge_browser_parts.py <dir>
// Adjust the ID ranges below (probe: fetch('/s/kpszsu?before=<id>') and look at <time>).
const SINCE = Date.parse('2025-10-01T00:00:00Z');
const RANGES = [[82745, 70000], [70001, 57000], [57001, null]];  // [start_before, stop_at_id]
const q = s => '"' + String(s).replace(/"/g, '""') + '"';
window.__tgw = [];
const worker = async (k, hi, lo) => {
  const S = {k, hi, lo, pages: 0, posts: 0, part: 0, oldest: null, done: false, error: null, buf: []};
  window.__tgw.push(S);
  const flush = () => {
    if (!S.buf.length) return;
    S.part += 1;
    const csv = 'id,published_at,text\n' + S.buf.map(r => [r[0], r[1], q(r[2])].join(',')).join('\n') + '\n';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv], {type: 'text/csv'}));
    a.download = `tg_kpszsu_w${k}_part_${String(S.part).padStart(2, '0')}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    S.buf = [];
  };
  let before = hi;
  try {
    while (true) {
      let html = null;
      for (let t = 0; t < 6 && html === null; t++) {
        try {
          const r = await fetch('/s/kpszsu?before=' + before);
          if (r.ok) html = await r.text(); else await new Promise(z => setTimeout(z, 5000 * (t + 1)));
        } catch (e) { await new Promise(z => setTimeout(z, 5000 * (t + 1))); }
      }
      if (html === null) throw new Error('fetch failed at before=' + before);
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const ms = [...doc.querySelectorAll('.tgme_widget_message[data-post]')];
      if (!ms.length) break;
      let minId = Infinity, minTs = Infinity;
      for (const m of ms) {
        const id = +m.dataset.post.split('/')[1];
        const t = m.querySelector('time')?.getAttribute('datetime');
        if (!t) continue;
        const tx = m.querySelector('.tgme_widget_message_text');
        let text = '';
        if (tx) {
          const c = tx.cloneNode(true);
          c.querySelectorAll('br').forEach(b => b.replaceWith('\n'));
          text = c.textContent.replace(/[ \t]+/g, ' ').trim();
        }
        S.buf.push([id, t, text]); S.posts += 1;
        if (id < minId) { minId = id; minTs = Date.parse(t); }
      }
      S.pages += 1; S.oldest = new Date(minTs).toISOString();
      if (S.pages % 300 === 0) flush();
      if ((lo !== null && minId <= lo) || minTs < SINCE || minId <= 1) break;
      before = minId;
      await new Promise(z => setTimeout(z, 700));
    }
  } catch (e) { S.error = String(e); }
  flush(); S.done = true;
};
RANGES.forEach(([hi, lo], i) => worker(i + 1, hi, lo));
// progress: window.__tgw.map(S => [S.k, S.pages, S.oldest, S.done])
