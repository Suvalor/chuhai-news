// chuhainews 前台增强：内容全部由服务端渲染，这里只做动效、键盘、收藏/赞、翻译开关和滚动续读。
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const body = document.body;
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const ease = 'cubic-bezier(.16,1,.3,1)';

  // ---------- 本地状态（与设计稿同一个 key） ----------
  const KEY = 'chuhai.v3';
  const load = () => { try { const s = JSON.parse(localStorage.getItem(KEY) || '{}'); return { liked: s.liked || {}, saved: s.saved || {}, read: s.read || {}, unreadOnly: !!s.unreadOnly }; } catch (e) { return { liked: {}, saved: {}, read: {}, unreadOnly: false }; } };
  const store = load();
  const persist = () => { try { localStorage.setItem(KEY, JSON.stringify(store)); } catch (e) {} };
  const ids = f => Object.keys(store[f]).filter(k => store[f][k]);
  const fmt = n => n >= 1000 ? (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K' : String(n);
  const post = (slug, action) => fetch(`/api/a/${encodeURIComponent(slug)}/${action}`, { method: 'POST' }).then(r => r.ok ? r.json() : null).catch(() => null);

  const paintMarks = () => {
    $$('[data-saved-count]').forEach(el => { const n = ids('saved').length; el.textContent = n ? String(n) : ''; });
    $$('[data-like-mark]').forEach(el => el.classList.toggle('on', !!store.liked[el.dataset.likeMark]));
    for (const [attr, f] of [['like', 'liked'], ['save', 'saved']]) {
      $$(`[data-${attr}]`).forEach(b => {
        const on = !!store[f][b.dataset[attr]];
        b.classList.toggle('on', on);
        b.setAttribute('aria-pressed', on);
        const g = $('.glyph', b); if (g) g.textContent = on ? g.dataset.on : g.dataset.off;
      });
    }
  };

  // ---------- 进场揭示 ----------
  const kf = [{ opacity: 0, transform: 'translateY(22px)' }, { opacity: 1, transform: 'none' }];
  const io = 'IntersectionObserver' in window && !still ? new IntersectionObserver(es => es.forEach(e => {
    if (e.isIntersecting) { e.target.animate(kf, { duration: 800, easing: ease }); io.unobserve(e.target); }
  }), { rootMargin: '0px 0px -40px 0px' }) : null;
  const reveal = (root = document) => {
    if (still || document.hidden) return; // 后台标签页时间线暂停，动画会让内容停在透明帧
    let i = 0;
    $$('[data-rv]', root).forEach(el => {
      if (el.__seen) return; el.__seen = true;
      if (el.getBoundingClientRect().top < innerHeight) el.animate(kf, { duration: 900, delay: Math.min(i++, 14) * 55, easing: ease, fill: 'backwards' });
      else if (io) io.observe(el);
    });
  };

  const pop = el => { if (!still && el && el.animate) el.animate([{ transform: 'scale(1)' }, { transform: 'scale(1.6) rotate(-8deg)' }, { transform: 'scale(1)' }], { duration: 460, easing: 'cubic-bezier(.34,1.56,.64,1)' }); };

  // ---------- 赞 / 收藏 ----------
  async function toggle(kind, slug, btn) {
    const f = kind === 'like' ? 'liked' : 'saved';
    const on = !store[f][slug];
    store[f][slug] = on; if (!on) delete store[f][slug];
    persist(); paintMarks();
    if (btn) pop($('.glyph', btn));
    const res = await post(slug, (on ? '' : 'un') + kind);
    const n = res && (res.likes ?? res.saves);
    if (n != null) $$(`[data-${kind}="${CSS.escape(slug)}"] [data-n]`).forEach(el => { el.textContent = fmt(n); });
  }
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-like],[data-save]');
    if (b) toggle(b.dataset.like ? 'like' : 'save', b.dataset.like || b.dataset.save, b);
  });

  // ---------- 阅读页 ----------
  const stream = $('[data-stream]');
  let visible = null;
  const currentArt = () => visible || $('[data-art]');

  function setVisible(art) {
    if (!art || art === visible) return;
    visible = art;
    paintTranslation(art);
    const slug = art.dataset.art;
    const tpl = name => $(`template[data-tpl-${name}]`, art);
    const side = $('[data-side]');
    if (side && tpl('side')) side.innerHTML = tpl('side').innerHTML;
    const meta = $('[data-meta]'); if (meta && tpl('meta')) meta.innerHTML = tpl('meta').innerHTML;
    $$('.rbar [data-like]').forEach(b => { b.dataset.like = slug; });
    $$('.rbar [data-save]').forEach(b => { b.dataset.save = slug; });
    const sideLike = side && $('[data-like] [data-n]', side), sideSave = side && $('[data-save] [data-n]', side);
    if (sideLike) $('.rbar [data-like] [data-n]').textContent = sideLike.textContent;
    if (sideSave) $('.rbar [data-save] [data-n]').textContent = sideSave.textContent;
    paintRail();
    paintMarks();
    if (location.pathname !== '/a/' + slug) { history.replaceState(null, '', '/a/' + slug); document.title = art.dataset.title; }
    store.read[slug] = true; persist();
    if (!sessionStorage.getItem('v:' + slug)) { sessionStorage.setItem('v:' + slug, '1'); post(slug, 'view'); }
  }

  function paintRail() {
    const art = currentArt(); if (!art) return;
    const ctx = body.dataset.ctx;
    const tpl = $(`template[data-tpl-rail-${ctx}]`, art);
    $$('[data-rail]').forEach(r => { if (tpl) r.innerHTML = tpl.innerHTML; });
    const [auName, catName, nA, nC] = ($('template[data-tpl-ctx]', art).innerHTML || '').split('|');
    const nm = $('[data-ctx-name]'); if (nm) nm.textContent = ctx === 'author' ? auName : catName;
    $$('[data-ctx-n="author"]').forEach(el => { el.textContent = nA; });
    $$('[data-ctx-n="cat"]').forEach(el => { el.textContent = nC; });
    $$('button[data-ctx]').forEach(b => b.classList.toggle('on', b.dataset.ctx === ctx));
  }

  const go = href => { if (href) location.href = href; };
  function step(d) {
    if (d > 0) {
      // 先在侧栏模板里找当前上下文的下一篇
      const art = currentArt();
      const side = document.createElement('div'); side.innerHTML = $('template[data-tpl-side]', art).innerHTML;
      const a = $(`.side-next.ctx-${body.dataset.ctx}`, side) || $('[data-next]', side);
      return go(a && a.getAttribute('href'));
    }
    const art = currentArt();
    const list = document.createElement('div'); list.innerHTML = $(`template[data-tpl-rail-${body.dataset.ctx}]`, art).innerHTML;
    const items = $$('.rail-i', list), i = items.findIndex(x => x.dataset.slug === art.dataset.art);
    if (items.length > 1) go(items[(i - 1 + items.length) % items.length].getAttribute('href'));
  }

  function paintTranslation(art) {
    const on = art.classList.contains('translated');
    body.classList.toggle('trans', on);
    const btn = $('.trans-btn');
    if (btn) {
      btn.hidden = art.dataset.hasTrans !== '1';
      btn.setAttribute('aria-pressed', String(on));
      const label = $('[data-trans-label]', btn);
      if (label) label.textContent = on ? label.dataset.original : label.dataset.translation;
    }
  }

  function toggleTrans(art = currentArt()) {
    if (!art || art.dataset.hasTrans !== '1') return;
    art.classList.toggle('translated');
    if (art === currentArt()) paintTranslation(art);
    if (!still) $$('[data-body], .art-h, .deck', art).forEach(el => el.animate([{ opacity: 0, filter: 'blur(3px)' }, { opacity: 1, filter: 'blur(0)' }], { duration: 420, easing: ease }));
  }

  let loading = false, ended = false;
  async function loadNext() {
    const arts = $$('[data-art]', stream);
    if (loading || ended || arts.length >= 10) return;
    loading = true;
    const last = arts[arts.length - 1].dataset.art;
    const ex = arts.map(a => a.dataset.art).join(',');
    try {
      const r = await fetch(`/fragment/next?slug=${encodeURIComponent(last)}&ctx=${body.dataset.ctx}&exclude=${encodeURIComponent(ex)}`);
      if (r.status === 200) {
        const box = document.createElement('div'); box.innerHTML = await r.text();
        const art = $('[data-art]', box);
        if (art) { stream.appendChild(art); if (!still) art.animate(kf, { duration: 800, easing: ease }); }
      } else ended = true;
    } catch (e) { ended = true; }
    loading = false;
    const flow = $('[data-flow]');
    if (flow && (ended || $$('[data-art]', stream).length >= 10)) flow.textContent = flow.dataset.end;
  }

  let raf = 0;
  function onScroll() {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      const arts = $$('[data-art]', stream);
      let vis = arts[0];
      arts.forEach(a => { if (a.getBoundingClientRect().top <= 180) vis = a; });
      setVisible(vis);
      const r = vis.getBoundingClientRect();
      const p = Math.max(0, Math.min(1, -r.top / Math.max(1, vis.offsetHeight - innerHeight * 0.6)));
      const bar = $('[data-progress]'); if (bar) bar.style.width = (p * 100).toFixed(1) + '%';
      if (scrollY + innerHeight > document.documentElement.scrollHeight - 600) loadNext();
    });
  }

  function initRead() {
    body.dataset.ctx = 'author';
    setVisible($('[data-art]'));
    addEventListener('scroll', onScroll, { passive: true });
    document.addEventListener('click', e => {
      const c = e.target.closest('button[data-ctx]');
      if (c) { body.dataset.ctx = c.dataset.ctx; paintRail(); ended = false; const f = $('[data-flow]'); if (f) f.textContent = f.dataset.more; }
      const translate = e.target.closest('[data-trans]');
      if (translate) toggleTrans(translate.closest('[data-art]') || currentArt());
      if (e.target.closest('[data-go-next]')) step(1);
      if (e.target.closest('[data-sheet-open]')) openSheet();
      if (e.target.closest('[data-sheet-close]')) closeSheet();
      const back = e.target.closest('[data-back]');
      if (back && document.referrer.startsWith(location.origin) && history.length > 1) { e.preventDefault(); history.back(); }
    });
    onScroll();
  }
  function openSheet() {
    const w = $('[data-sheet]'); w.hidden = false;
    if (!still) $('.sheet', w).animate([{ transform: 'translateY(100%)' }, { transform: 'none' }], { duration: 420, easing: ease });
  }
  const closeSheet = () => { const w = $('[data-sheet]'); if (w) w.hidden = true; };

  // ---------- 首页未读筛选 ----------
  function initHome() {
    const control = $('[data-unread-control]'), filter = $('[data-unread-only]');
    const empty = $('[data-unread-empty]'), cards = $$('.home-feed .card');
    const apply = () => {
      let visibleCount = 0;
      for (const card of cards) {
        const read = !!store.read[card.dataset.slug];
        card.classList.toggle('is-read', read);
        card.hidden = filter.checked && read;
        if (!card.hidden) visibleCount++;
      }
      for (const day of $$('[data-feed-day]')) {
        const n = $$('.card:not([hidden])', day).length;
        day.hidden = n === 0;
        const count = $('[data-day-count]', day);
        count.textContent = body.dataset.ui === 'zh' ? n + ' 篇' : n + (n === 1 ? ' piece' : ' pieces');
      }
      empty.hidden = !filter.checked || !cards.length || visibleCount > 0;
    };
    filter.checked = store.unreadOnly;
    control.hidden = false;
    filter.addEventListener('change', () => {
      store.unreadOnly = filter.checked;
      persist(); apply();
    });
    addEventListener('pageshow', () => {
      const latest = load();
      store.read = latest.read;
      store.saved = latest.saved;
      store.liked = latest.liked;
      store.unreadOnly = latest.unreadOnly;
      filter.checked = latest.unreadOnly;
      paintMarks(); apply();
    });
    apply();
  }

  // ---------- 收藏页 ----------
  async function initSaved() {
    const rows = $('[data-rows]'), empty = $('[data-empty]');
    const tabs = $$('[data-saved-tab]');
    tabs.forEach(b => { $('[data-n]', b).textContent = ids(b.dataset.savedTab).length; });
    async function show(kind) {
      tabs.forEach(b => b.classList.toggle('on', b.dataset.savedTab === kind));
      $('[data-list-label]').textContent = $(`[data-saved-tab="${kind}"]`).firstChild.textContent.trim();
      const list = ids(kind);
      rows.innerHTML = list.length ? await (await fetch('/fragment/rows?slugs=' + encodeURIComponent(list.join(',')))).text() : '';
      const n = $$('[data-slug]', rows).length;
      empty.hidden = n > 0;
      empty.textContent = kind === 'liked' ? empty.dataset.likedEmpty : empty.dataset.savedEmpty;
      paintMarks(); reveal(rows);
    }
    tabs.forEach(b => b.addEventListener('click', () => show(b.dataset.savedTab)));
    show('saved');
  }

  // ---------- 键盘 ----------
  addEventListener('keydown', e => {
    const tag = (e.target && e.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key.toLowerCase();
    if (body.dataset.view !== 'read') {
      if (k === '/' && body.dataset.view !== 'search') { e.preventDefault(); const homeSearch = $('#home-q'); if (homeSearch) homeSearch.focus(); else location.href = '/search'; }
      return;
    }
    const art = currentArt();
    if (k === 'arrowright' || k === 'j') step(1);
    else if (k === 'arrowleft' || k === 'k') step(-1);
    else if (k === 't' && $('[data-trans]')) toggleTrans();
    else if (k === 'l') toggle('like', art.dataset.art, $('[data-side] [data-like]'));
    else if (k === 's') toggle('save', art.dataset.art, $('[data-side] [data-save]'));
    else if (k === 'escape') { if ($('[data-sheet]') && !$('[data-sheet]').hidden) closeSheet(); else $('[data-back]').click(); }
  });

  paintMarks();
  reveal();
  if (body.dataset.view === 'read') initRead();
  if (body.dataset.view === 'saved') initSaved();
  if (body.dataset.view === 'home') initHome();
})();
