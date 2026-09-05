'use strict';
(() => {
  const root = document.getElementById('document');
  const latest = document.getElementById('latest');
  let userAtBottom = true;
  let applying = false;
  let hasContent = false;
  const nearBottom = () => document.documentElement.scrollHeight - innerHeight - scrollY < 90;
  addEventListener('scroll', () => { if (!applying) userAtBottom = nearBottom(); if (userAtBottom) latest.hidden = true; }, {passive: true});
  latest.addEventListener('click', () => { userAtBottom = true; latest.hidden = true; scrollTo(0, document.documentElement.scrollHeight); });
  document.addEventListener('copy', event => { if (!document.body.classList.contains('ready')) event.preventDefault(); });
  document.addEventListener('visibilitychange', () => { if (document.hidden) document.body.classList.remove('motion'); });
  window.updateDocument = payload => {
    const previousY = scrollY;
    const firstContent = !hasContent && payload.blocks.length > 0;
    const follow = userAtBottom && !(firstContent && payload.ready);
    // Multiple anchors permit recovery when the first visible block is removed by a patch.
    const anchors = [...root.children].map(el => ({id: el.id, top: el.getBoundingClientRect().top, bottom: el.getBoundingClientRect().bottom})).filter(a => a.bottom > 0);
    applying = true;
    document.body.classList.toggle('ready', payload.ready);
    document.body.classList.toggle('motion', payload.motion);
    document.body.classList.toggle('waiting', payload.waiting);
    document.body.classList.toggle('has-content', payload.blocks.length > 0);
    const existing = new Map([...root.children].map(el => [el.id, el]));
    const desired = new Set(payload.blocks.map(b => b.id));
    let changed = false;
    payload.blocks.forEach((block, index) => {
      let el = existing.get(block.id);
      if (!el) { el = document.createElement('section'); el.id = block.id; el.className = 'block'; }
      if (el.dataset.source !== block.html) {
        el.innerHTML = block.html; // HTML is generated only from the validated, escaped C2D model.
        el.dataset.source = block.html;
        el.querySelectorAll('.math').forEach(math => {
          const source = math.dataset.latex;
          try { katex.render(source, math, {trust: false, throwOnError: true, maxExpand: 1000, maxSize: 30, strict: 'error', output: 'htmlAndMathml'}); }
          catch (_) { math.textContent = source; math.classList.add('fallback'); }
        });
        el.classList.remove('changed');
        void el.offsetWidth;
        if (payload.motion) el.classList.add('changed');
        changed = true;
      }
      if (root.children[index] !== el) root.insertBefore(el, root.children[index] || null);
    });
    existing.forEach((el, id) => { if (!desired.has(id)) { el.remove(); changed = true; } });
    if (follow) scrollTo(0, document.documentElement.scrollHeight);
    else {
      const anchor = anchors.find(a => document.getElementById(a.id));
      if (anchor) scrollTo(0, previousY + document.getElementById(anchor.id).getBoundingClientRect().top - anchor.top);
      else scrollTo(0, previousY);
      if (changed && !firstContent) latest.hidden = false;
    }
    if (firstContent) { hasContent = true; userAtBottom = nearBottom(); }
    requestAnimationFrame(() => { applying = false; });
  };
})();
