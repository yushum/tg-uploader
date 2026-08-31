const app = document.querySelector('#app');
const crumbs = document.querySelector('.crumbs');
const globalSearch = document.querySelector('.global-search');
const globalSearchInput = globalSearch.querySelector('input');
const themeButton = document.querySelector('.theme-button');
const themeMenu = document.querySelector('.theme-menu');
const systemTheme = matchMedia('(prefers-color-scheme:dark)');
const state = { streamers: [], dates: new Map(), sessions: new Map(), sort: 'recent', query: '', channel: '', date: '', routeId: 0, transitionChannel: '', transitionName: '', currentSessions: [], activeSessionIndex: 0, activePartIndex: 0 };
const DIRECTORY_PAGE_SIZE = 16;
const FAVORITES_KEY = 'replay-favorites';
let activePlayer = null;

const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[character]);
const thumb = id => id ? `/api/thumbnail/${Number(id)}` : '';
const channelPath = name => `/streamer/${encodeURIComponent(name)}`;
const watchPath = (name, date) => `${channelPath(name)}/${date}`;

function formatDuration(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const secs = Math.floor(value % 60);
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}` : `${minutes}:${String(secs).padStart(2, '0')}`;
}

function longDate(value) {
  const date = new Date(`${value}T00:00:00`);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' }).format(date);
}

function shortDate(value) {
  const [, month, day] = value.split('-');
  return `${Number(month)} 月 ${Number(day)} 日`;
}

function relativeDate(value) {
  const date = new Date(`${value}T00:00:00`);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const days = Math.round((today - date) / 86400000);
  if (days === 0) return '今天';
  if (days === 1) return '昨天';
  if (days > 1 && days < 7) return `${days} 天前`;
  return `${date.getMonth() + 1} 月 ${date.getDate()} 日`;
}

async function api(path, params = {}) {
  const query = new URLSearchParams(params);
  const response = await fetch(`${path}${query.size ? `?${query}` : ''}`);
  if (!response.ok) throw new Error(`请求失败（${response.status}）`);
  return response.json();
}

async function getDates(channel) {
  if (!state.dates.has(channel)) state.dates.set(channel, api('/api/dates', { streamer: channel }));
  return state.dates.get(channel);
}

async function getSessions(channel, date) {
  const key = `${channel}\n${date}`;
  if (!state.sessions.has(key)) state.sessions.set(key, api('/api/sessions', { streamer: channel, date }));
  return state.sessions.get(key);
}

function navigate(path, replace = false, source = null) {
  const update = async () => {
    history[replace ? 'replaceState' : 'pushState'](null, '', path);
    await route();
  };
  if (!document.startViewTransition) { update(); return; }
  try { state.viewTransition?.skipTransition(); } catch (_) {}
  const channelSource = source?.matches('[data-open-channel]') ? source.querySelector('img') : null;
  const watchSource = source?.matches('.date-card,[data-transition="watch"]') ? source : null;
  const transitionSource = channelSource || watchSource;
  state.transitionChannel = source?.dataset.openChannel || '';
  state.transitionName = channelSource ? 'channel-cover' : watchSource ? 'watch-surface' : '';
  if (transitionSource && state.transitionName) transitionSource.style.viewTransitionName = state.transitionName;
  const transition = document.startViewTransition(update);
  state.viewTransition = transition;
  transition.finished.finally(() => {
    if (transitionSource) transitionSource.style.viewTransitionName = '';
    document.querySelector('.channel-avatar img')?.style.removeProperty('view-transition-name');
    document.querySelector('.player-shell')?.style.removeProperty('view-transition-name');
    state.transitionChannel = '';
    state.transitionName = '';
    if (state.viewTransition === transition) state.viewTransition = null;
  });
}

function setCrumbs(parts) {
  crumbs.innerHTML = parts.map(part => {
    if (typeof part === 'string') return `<span>${escapeHtml(part)}</span>`;
    if (part.href) return `<a href="${part.href}" data-link>${escapeHtml(part.label)}</a>`;
    return `<button type="button" ${part.action ? `data-${part.action}` : ''}>${escapeHtml(part.label)}</button>`;
  }).join('');
}

function setActiveNav(name) {
  document.querySelectorAll('[data-nav]').forEach(link => link.classList.toggle('active', link.dataset.nav === name));
}

function loadFavorites() {
  try {
    const saved = localStorage.getItem(FAVORITES_KEY);
    const legacy = localStorage.getItem('replay-demo-favorites');
    const value = JSON.parse(saved || legacy || '[]');
    if (!saved && legacy && Array.isArray(value)) saveFavorites(value);
    return Array.isArray(value) ? value : [];
  } catch { return []; }
}

function saveFavorites(items) {
  localStorage.setItem(FAVORITES_KEY, JSON.stringify(items));
}

function isFavorite(messageId) {
  return loadFavorites().some(item => Number(item.message_id) === Number(messageId));
}

function showLoading(label = '正在加载…') {
  app.innerHTML = `<div class="state-message"><i class="spinner"></i><span>${escapeHtml(label)}</span></div>`;
}

function showError(error) {
  console.error(error);
  app.innerHTML = `<div class="state-message error-state"><strong>暂时无法加载</strong><span>${escapeHtml(error.message)}</span><button type="button" data-retry>重试</button></div>`;
}

function coverMarkup(id, loading = 'lazy') {
  return id ? `<img src="${thumb(id)}" alt="" loading="${loading}">` : '';
}

function favoriteCardMarkup(item) {
  return `<a class="favorite-card" href="${watchPath(item.channel, item.date)}?session=${item.message_id}" data-link data-transition="watch"><span class="favorite-cover">${coverMarkup(item.message_id)}<i>★</i></span><span class="favorite-copy"><strong>${escapeHtml(item.channel)}</strong><small>${escapeHtml(longDate(item.date))} · ${escapeHtml(item.time || '')}</small></span><b>›</b></a>`;
}

function renderHome() {
  const recent = [...state.streamers].sort((a, b) => b.latest_date.localeCompare(a.latest_date) || b.cover_message_id - a.cover_message_id);
  const featured = recent.slice(0, 6);
  const favorites = loadFavorites().slice(0, 4);
  const sessions = state.streamers.reduce((total, item) => total + item.session_count, 0);
  app.innerHTML = `<section class="page home-page">
    <div class="hero">
      <span class="eyebrow"><i></i> 直播影像馆</span>
      <h1>让每一场直播，<br><span>值得重看。</span></h1>
      <p>安静地收藏每个瞬间。快速找到主播、日期与场次，然后把注意力留给内容。</p>
      <div class="hero-stats"><span><strong>${state.streamers.length}</strong> 位主播</span><i></i><span><strong>${sessions.toLocaleString()}</strong> 场直播</span><i></i><span>持续更新</span></div>
    </div>
    <div class="section-head showcase-head"><div><span class="section-kicker">Recently updated</span><h2>最近更新</h2></div><a class="all-streamers-link" href="/streamers" data-link>全部主播 <span>›</span></a></div>
    <div class="featured-grid showcase-grid">${featured.map((item, index) => `<button class="feature-card showcase-${index + 1}" type="button" data-open-channel="${escapeHtml(item.name)}">
      ${coverMarkup(item.cover_message_id, index ? 'lazy' : 'eager')}<span class="image-shade"></span>
      <span class="feature-copy"><small>${index ? '刚刚更新' : '最新归档'}</small><strong>${escapeHtml(item.name)}</strong><em>${item.session_count} 场回放 · 更新于${escapeHtml(relativeDate(item.latest_date))}</em></span>
      ${index ? '' : '<span class="feature-arrow">↗</span>'}
    </button>`).join('')}</div>
    <section class="home-favorites" id="favorites"><div class="section-head"><div><span class="section-kicker">Your collection</span><h2>我的收藏</h2></div>${favorites.length ? '<a class="all-streamers-link" href="/favorites" data-link>查看全部 <span>›</span></a>' : ''}</div>
      ${favorites.length ? `<div class="favorite-grid">${favorites.map(favoriteCardMarkup).join('')}</div>` : '<div class="favorite-empty"><span>☆</span><div><strong>收藏喜欢的录像</strong><p>播放时点亮星标，录像会出现在这里，仅保存在当前浏览器。</p></div></div>'}
    </section>
  </section>`;
}

function renderFavorites() {
  const favorites = loadFavorites();
  app.innerHTML = `<section class="page favorites-page"><a class="text-back" href="/" data-link><span>‹</span> 返回首页</a><header class="directory-head"><div><span class="section-kicker">Your collection</span><h1>我的收藏</h1><p>${favorites.length ? `已收藏 ${favorites.length} 场录像。` : '还没有收藏任何录像。'}</p></div></header>
    ${favorites.length ? `<div class="favorite-grid favorite-grid-full">${favorites.map(favoriteCardMarkup).join('')}</div>` : '<div class="favorite-empty large"><span>☆</span><div><strong>这里还很安静</strong><p>在播放页点击“收藏”后，就能从任何页面快速回来。</p></div></div>'}
  </section>`;
}

function directoryUrl(page, sort, query) {
  const params = new URLSearchParams();
  if (sort !== 'recent') params.set('sort', sort);
  if (page > 1) params.set('page', page);
  if (query) params.set('q', query);
  return `/streamers${params.size ? `?${params}` : ''}`;
}

function paginationMarkup(page, totalPages, sort, query) {
  if (totalPages <= 1) return '';
  const pages = [...new Set([1, page - 1, page, page + 1, totalPages].filter(value => value >= 1 && value <= totalPages))];
  let previous = 0;
  const numbers = pages.map(value => {
    const gap = previous && value - previous > 1 ? '<span>…</span>' : '';
    previous = value;
    return `${gap}<a class="${value === page ? 'active' : ''}" href="${directoryUrl(value, sort, query)}" data-link>${value}</a>`;
  }).join('');
  return `<nav class="pagination" aria-label="主播列表分页"><a class="page-edge ${page === 1 ? 'disabled' : ''}" href="${directoryUrl(page - 1, sort, query)}" data-link>‹ 上一页</a><div>${numbers}</div><a class="page-edge ${page === totalPages ? 'disabled' : ''}" href="${directoryUrl(page + 1, sort, query)}" data-link>下一页 ›</a></nav>`;
}

function renderDirectory() {
  const params = new URLSearchParams(location.search);
  const sort = ['recent', 'name', 'most'].includes(params.get('sort')) ? params.get('sort') : 'recent';
  const query = (params.get('q') || '').trim();
  const requestedPage = Math.max(1, Number(params.get('page')) || 1);
  const collator = new Intl.Collator('zh-CN-u-co-pinyin', { numeric: true, sensitivity: 'base' });
  const normalizedQuery = query.toLocaleLowerCase();
  const items = state.streamers.filter(item => item.name.toLocaleLowerCase().includes(normalizedQuery)).sort((a, b) => sort === 'name'
    ? collator.compare(a.name, b.name) : sort === 'most'
      ? b.session_count - a.session_count || collator.compare(a.name, b.name)
      : b.latest_date.localeCompare(a.latest_date) || b.cover_message_id - a.cover_message_id);
  const totalPages = Math.max(1, Math.ceil(items.length / DIRECTORY_PAGE_SIZE));
  const page = Math.min(requestedPage, totalPages);
  const visible = items.slice((page - 1) * DIRECTORY_PAGE_SIZE, page * DIRECTORY_PAGE_SIZE);
  state.sort = sort; state.query = query;
  globalSearchInput.value = query;
  app.innerHTML = `<section class="page directory-page">
    <a class="text-back" href="/" data-link><span>‹</span> 返回精选首页</a>
    <header class="directory-head"><div><span class="section-kicker">Streamer directory</span><h1>主播目录</h1><p>从 ${state.streamers.length} 位主播中，找到你想看的那一位。</p></div></header>
    <div class="directory-toolbar"><form class="directory-search" role="search"><svg viewBox="0 0 24 24"><circle cx="10.8" cy="10.8" r="6.5"/><path d="m16 16 4 4"/></svg><input type="search" value="${escapeHtml(query)}" placeholder="输入主播名称" aria-label="输入主播名称"><button type="submit">搜索</button></form>
      <div class="directory-sort"><span>排序</span><a class="${sort === 'recent' ? 'active' : ''}" href="${directoryUrl(1, 'recent', query)}" data-link>最近更新</a><a class="${sort === 'name' ? 'active' : ''}" href="${directoryUrl(1, 'name', query)}" data-link>名称</a><a class="${sort === 'most' ? 'active' : ''}" href="${directoryUrl(1, 'most', query)}" data-link>录像最多</a></div></div>
    <div class="directory-summary"><span>${query ? `“${escapeHtml(query)}”找到 ${items.length} 位主播` : `第 ${page} / ${totalPages} 页`}</span><small>每页最多 ${DIRECTORY_PAGE_SIZE} 位，只加载当前页封面</small></div>
    <div class="streamer-grid">${visible.length ? visible.map(item => `<button class="streamer-card" type="button" data-open-channel="${escapeHtml(item.name)}">
    <span class="streamer-cover">${coverMarkup(item.cover_message_id)}</span>
    <span class="streamer-info"><span><strong>${escapeHtml(item.name)}</strong><small>更新于${escapeHtml(relativeDate(item.latest_date))} · ${item.session_count} 场</small></span><i>›</i></span>
    </button>`).join('') : `<div class="no-results">没有找到“${escapeHtml(query)}”</div>`}</div>
    ${paginationMarkup(page, totalPages, sort, query)}
  </section>`;
}

async function renderChannel(channel, routeId) {
  showLoading(`正在读取 ${channel} 的全部录像日…`);
  const dates = await getDates(channel);
  if (routeId !== state.routeId) return;
  const streamer = state.streamers.find(item => item.name === channel) || {};
  const grouped = new Map();
  dates.forEach(item => { const month = item.date.slice(0, 7); if (!grouped.has(month)) grouped.set(month, []); grouped.get(month).push(item); });
  const totalSessions = dates.reduce((sum, item) => sum + item.session_count, 0);
  app.innerHTML = `<section class="page channel-page">
    <a class="text-back" href="/streamers" data-link><span>‹</span> 所有主播</a>
    <div class="channel-hero">
      <div class="channel-avatar">${coverMarkup(streamer.cover_message_id || dates[0]?.cover_message_id, 'eager')}</div>
      <div class="channel-intro"><span class="eyebrow"><i></i> 最近更新于${escapeHtml(relativeDate(streamer.latest_date || dates[0]?.date || ''))}</span><h1>${escapeHtml(channel)}</h1><p>${totalSessions} 场直播 · ${dates.length} 个录像日</p></div>
      ${dates.length ? `<a class="primary-action" href="${watchPath(channel, dates[0].date)}" data-link><span>▶</span> 播放最新录像</a>` : ''}
    </div>
    ${dates.length ? `<nav class="month-rail" aria-label="选择月份">${[...grouped].map(([month], index) => `<button type="button" class="${index ? '' : 'active'}" data-jump-month="${month}">${month.replace('-', ' 年 ')} 月</button>`).join('')}</nav>
      <div class="date-sections">${[...grouped].map(([month, items]) => { const [year, monthNumber] = month.split('-'); return `<section class="month-section" data-month-section="${month}">
        <div class="month-heading"><div><span class="section-kicker">${new Intl.DateTimeFormat('en', { month: 'long' }).format(new Date(`${month}-01T00:00:00`))}</span><h2>${year} 年 ${Number(monthNumber)} 月</h2></div><span>${items.length} 个录像日</span></div>
        <div class="date-grid">${items.map(item => { const date = new Date(`${item.date}T00:00:00`); return `<a class="date-card" href="${watchPath(channel, item.date)}" data-link><span class="date-day">${date.getDate()}</span><span class="date-copy"><strong>${new Intl.DateTimeFormat('zh-CN', { weekday: 'long' }).format(date)}</strong><small>${item.session_count} 场 · ${item.part_count} 个录像片段</small></span><i>›</i></a>`; }).join('')}</div>
      </section>`; }).join('')}</div>` : '<div class="no-results">这个主播还没有录像</div>'}
  </section>`;
  if (state.transitionChannel === channel) document.querySelector('.channel-avatar img')?.style.setProperty('view-transition-name', 'channel-cover');
}

function sessionMarkup(session, index) {
  const cover = session.parts.find(part => part.available)?.message_id;
  return `<button class="session-item ${index ? '' : 'active'}" type="button" data-session-index="${index}"><span class="session-thumb">${coverMarkup(cover)}${index ? '' : '<i>▶</i>'}</span><span class="session-copy"><strong>${escapeHtml(session.time.slice(0, 5))} 开播</strong><small>${formatDuration(session.total_duration)} · ${session.part_count} 个片段</small></span></button>`;
}

function drawCalendar(year = state.date.slice(0, 4), month = state.date.slice(0, 7)) {
  const calendar = document.querySelector('.recording-calendar');
  if (!calendar) return;
  const dates = state.currentDates || [];
  const years = [...new Set(dates.map(item => item.date.slice(0, 4)))];
  const chosenYear = years.includes(year) ? year : years[0];
  const months = [...new Set(dates.filter(item => item.date.startsWith(chosenYear)).map(item => item.date.slice(0, 7)))];
  const chosenMonth = months.includes(month) ? month : months[0];
  calendar.querySelector('[data-calendar-year]').innerHTML = years.map(item => `<option value="${item}" ${item === chosenYear ? 'selected' : ''}>${item} 年</option>`).join('');
  calendar.querySelector('[data-calendar-month]').innerHTML = months.map(item => `<option value="${item}" ${item === chosenMonth ? 'selected' : ''}>${Number(item.slice(5))} 月</option>`).join('');
  const available = new Map(dates.filter(item => item.date.startsWith(chosenMonth)).map(item => [Number(item.date.slice(8)), item]));
  const [calendarYear, calendarMonth] = chosenMonth.split('-').map(Number);
  const firstWeekday = (new Date(calendarYear, calendarMonth - 1, 1).getDay() + 6) % 7;
  const dayCount = new Date(calendarYear, calendarMonth, 0).getDate();
  const blanks = Array.from({ length: firstWeekday }, () => '<span class="calendar-blank"></span>').join('');
  const days = Array.from({ length: dayCount }, (_, index) => {
    const day = index + 1;
    const item = available.get(day);
    if (!item) return `<span class="calendar-day unavailable">${day}</span>`;
    return `<a class="calendar-day available ${item.date === state.date ? 'selected' : ''}" href="${watchPath(state.channel, item.date)}" data-link data-transition="watch"><span>${day}</span><i>${item.session_count}</i></a>`;
  }).join('');
  calendar.querySelector('.calendar-grid').innerHTML = blanks + days;
}

async function renderDateLibrary(channel, routeId) {
  showLoading(`正在整理 ${channel} 的日期索引…`);
  const dates = await getDates(channel);
  if (routeId !== state.routeId) return;
  const from = new URLSearchParams(location.search).get('from');
  const grouped = new Map();
  dates.forEach(item => { const year = item.date.slice(0, 4); if (!grouped.has(year)) grouped.set(year, new Map()); const month = item.date.slice(0, 7); if (!grouped.get(year).has(month)) grouped.get(year).set(month, []); grouped.get(year).get(month).push(item); });
  const allMonths = [...grouped].flatMap(([, months]) => [...months.keys()]);
  app.innerHTML = `<section class="page date-library-page">
    <a class="text-back" href="${from ? watchPath(channel, from) : channelPath(channel)}" data-link><span>‹</span> ${from ? '返回播放' : channel}</a>
    <header class="date-library-head"><span class="section-kicker">Recording timeline</span><h1>${escapeHtml(channel)}的全部录像日</h1><p>${dates.length} 个录像日，按年份和月份快速定位。</p></header>
    <nav class="library-year-rail">${allMonths.map((month, index) => `<button type="button" class="${month === from?.slice(0, 7) || (!from && !index) ? 'active' : ''}" data-library-month="${month}">${month.replace('-', ' / ')}</button>`).join('')}</nav>
    <div class="library-years">${[...grouped].map(([year, months]) => `<section class="library-year"><h2>${year}</h2>${[...months].map(([month, items]) => `<div class="library-month" data-library-month-section="${month}"><h3>${Number(month.slice(5))} 月 <small>${items.length} 天</small></h3><div class="library-date-grid">${items.map(item => `<a class="library-date ${item.date === from ? 'selected' : ''}" href="${watchPath(channel, item.date)}" data-link><strong>${Number(item.date.slice(8))}</strong><span>${new Intl.DateTimeFormat('zh-CN', { weekday: 'short' }).format(new Date(`${item.date}T00:00:00`))}<small>${item.session_count} 场</small></span><i>›</i></a>`).join('')}</div></div>`).join('')}</section>`).join('')}</div>
  </section>`;
}

async function renderWatch(channel, date, routeId) {
  showLoading('正在读取当天录像…');
  const [dates, sessions] = await Promise.all([getDates(channel), getSessions(channel, date)]);
  if (routeId !== state.routeId) return;
  state.date = date;
  const dateIndex = dates.findIndex(item => item.date === date);
  const older = dates[dateIndex + 1];
  const newer = dates[dateIndex - 1];
  const requestedMessageId = Number(new URLSearchParams(location.search).get('session'));
  const initialSessionIndex = Math.max(0, sessions.findIndex(session => session.parts.some(part => part.message_id === requestedMessageId)));
  app.innerHTML = `<section class="page watch-page">
    <div class="watch-heading"><a class="text-back" href="${channelPath(channel)}" data-link><span>‹</span> ${escapeHtml(channel)}</a><span class="source-badge"><i></i> 原始录像</span></div>
    <div class="watch-grid"><div class="watch-main">
      <div id="playerMount" class="player-mount"></div>
      <div class="now-playing"><div><span class="section-kicker">录像回放</span><h1>${escapeHtml(channel)}的直播回放</h1><p>${escapeHtml(longDate(date))}<span class="playing-meta"></span></p></div><div class="now-actions"><button class="favorite-button" type="button" data-toggle-favorite><span>☆</span> 收藏</button><button class="soft-button" type="button" data-copy-link>复制链接</button></div></div>
    </div><aside class="recording-nav">
      <div class="rail-panel sessions-panel"><div class="nav-head"><span class="section-kicker">录像导航</span><strong>${escapeHtml(channel)}</strong></div>
        <div class="date-launcher static"><span><small>当前录像日期 · 共 ${dates.length} 天</small><strong>${escapeHtml(longDate(date))}</strong></span></div>
        <div class="sessions-head"><span>当天场次</span><small>${sessions.length} 场</small></div><div class="session-stack">${sessions.map(sessionMarkup).join('') || '<div class="no-results">没有场次</div>'}</div>
        <div class="adjacent-row"><button type="button" data-adjacent-date="${older?.date || ''}" ${older ? '' : 'disabled'}>‹ ${older ? `较早 ${shortDate(older.date)}` : '已经最早'}</button><button type="button" data-adjacent-date="${newer?.date || ''}" ${newer ? '' : 'disabled'}>${newer ? `较新 ${shortDate(newer.date)}` : '已经最新'} ›</button></div>
        <div class="recording-calendar"><div class="calendar-selects"><select data-calendar-year aria-label="选择年份"></select><select data-calendar-month aria-label="选择月份"></select></div><div class="calendar-weekdays"><span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span><span>日</span></div><div class="calendar-grid"></div><p><i></i> 数字角标表示当天场次数</p></div>
      </div>
    </aside></div>
  </section>`;
  drawCalendar();
  state.currentSessions = sessions;
  if (sessions.length) {
    const requestedParts = availableParts(sessions[initialSessionIndex]);
    const requestedPartIndex = requestedParts.findIndex(part => part.message_id === requestedMessageId);
    const initialPartIndex = requestedMessageId ? Math.max(0, requestedPartIndex) : null;
    selectSession(sessions, initialSessionIndex, false, initialPartIndex);
  }
  if (state.transitionName === 'watch-surface') document.querySelector('.player-shell')?.style.setProperty('view-transition-name', 'watch-surface');
}

function availableParts(session) {
  return (session?.parts || []).filter(part => part.available).sort((a, b) => Number(a.position) - Number(b.position));
}

function selectSession(sessions, index, autoplay, partIndex = null) {
  const session = sessions[index];
  const parts = availableParts(session);
  const selectedPartIndex = Number.isInteger(partIndex) ? partIndex : 0;
  const part = parts[selectedPartIndex];
  if (!part) return;
  document.querySelectorAll('.session-item').forEach((item, itemIndex) => {
    item.classList.toggle('active', itemIndex === index);
    item.setAttribute('aria-current', itemIndex === index ? 'true' : 'false');
    const thumbIcon = item.querySelector('.session-thumb i');
    if (itemIndex === index && !thumbIcon) item.querySelector('.session-thumb').insertAdjacentHTML('beforeend', '<i>▶</i>');
    if (itemIndex !== index) thumbIcon?.remove();
  });
  state.currentSessions = sessions;
  state.activeSessionIndex = index;
  state.activePartIndex = selectedPartIndex;
  if (activePlayer) activePlayer.destroy();
  const options = {
    autoplay,
    onPartChange: (activePartIndex, partCount) => {
      state.activePartIndex = activePartIndex;
      const partLabel = partCount > 1 ? ` · 片段 ${activePartIndex + 1}/${partCount}` : '';
      document.querySelector('.playing-meta').textContent = ` · ${session.time.slice(0, 5)} 开播 · ${session.platform}${partLabel}`;
    },
    onEnded: handleSessionEnded,
  };
  if (Number.isInteger(partIndex)) options.startPartIndex = partIndex;
  activePlayer = new window.ReplayMergedPlayer(document.querySelector('#playerMount'), session, options);
  const sessionAnchor = parts[0];
  state.activeFavorite = { message_id: sessionAnchor.message_id, channel: state.channel, date: state.date, time: session.time.slice(0, 5), duration: session.total_duration };
  const url = new URL(location.href);
  url.searchParams.set('session', sessionAnchor.message_id);
  history.replaceState(null, '', `${url.pathname}${url.search}`);
  syncFavoriteButton();
  document.querySelector(`.session-item[data-session-index="${index}"]`)?.scrollIntoView({ behavior: autoplay ? 'smooth' : 'auto', block: 'nearest' });
}

function handleSessionEnded() {
  const sessions = state.currentSessions;
  if (state.activeSessionIndex + 1 < sessions.length) {
    const nextIndex = state.activeSessionIndex + 1;
    selectSession(sessions, nextIndex, true, 0);
    toast(`自动续播 · ${sessions[nextIndex].time.slice(0, 5)} 场`);
    return;
  }
  toast('当天录像已全部播放完毕');
}

function syncFavoriteButton() {
  const button = document.querySelector('[data-toggle-favorite]');
  if (!button || !state.activeFavorite) return;
  const selected = isFavorite(state.activeFavorite.message_id);
  button.classList.toggle('selected', selected);
  button.innerHTML = `<span>${selected ? '★' : '☆'}</span> ${selected ? '已收藏' : '收藏'}`;
  button.setAttribute('aria-pressed', String(selected));
}

function toggleFavorite() {
  if (!state.activeFavorite) return;
  const favorites = loadFavorites();
  const index = favorites.findIndex(item => Number(item.message_id) === Number(state.activeFavorite.message_id));
  if (index >= 0) { favorites.splice(index, 1); toast('已取消收藏'); }
  else { favorites.unshift(state.activeFavorite); toast('已加入收藏'); }
  saveFavorites(favorites);
  syncFavoriteButton();
}

function applyTheme(mode, persist = true) {
  const dark = mode === 'dark' || (mode === 'auto' && systemTheme.matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  document.documentElement.dataset.themeMode = mode;
  document.querySelector('meta[name="theme-color"]').content = dark ? '#08080a' : '#f5f5f7';
  document.querySelector('.theme-label').textContent = ({ auto: '自动', light: '浅色', dark: '深色' })[mode];
  themeMenu.querySelectorAll('[data-theme-choice]').forEach(button => button.classList.toggle('selected', button.dataset.themeChoice === mode));
  if (persist) localStorage.setItem('replay-theme', mode);
}

function toast(message) {
  const element = document.querySelector('.toast');
  element.textContent = message; element.classList.add('show');
  clearTimeout(toast.timer); toast.timer = setTimeout(() => element.classList.remove('show'), 1800);
}

async function copyCurrentLink() {
  const value = location.href;
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch (_) {}
  }
  const textarea = document.createElement('textarea');
  textarea.value = value;
  textarea.setAttribute('readonly', '');
  textarea.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0;font-size:16px';
  document.body.append(textarea);
  textarea.focus();
  textarea.select();
  textarea.setSelectionRange(0, value.length);
  let copied = false;
  try { copied = document.execCommand('copy'); } catch (_) {}
  textarea.remove();
  return copied;
}

async function route() {
  const routeId = ++state.routeId;
  if (activePlayer) { activePlayer.destroy(); activePlayer = null; }
  let parts;
  try { parts = location.pathname.split('/').filter(Boolean).map(decodeURIComponent); } catch { parts = []; }
  try {
    if (!state.streamers.length) state.streamers = await api('/api/streamers');
    if (routeId !== state.routeId) return;
    if (!parts.length) {
      state.channel = ''; state.date = ''; state.currentDates = []; state.activeFavorite = null;
      setActiveNav('home'); setCrumbs([]); document.title = 'Replay · 直播影像馆'; renderHome();
    } else if (parts[0] === 'streamers') {
      state.channel = ''; state.date = ''; state.currentDates = []; state.activeFavorite = null;
      setActiveNav('streamers'); setCrumbs([{ label: '主播目录', href: '/streamers' }]); document.title = '主播目录 · Replay'; renderDirectory();
    } else if (parts[0] === 'favorites') {
      state.channel = ''; state.date = ''; state.currentDates = []; state.activeFavorite = null;
      setActiveNav('favorites'); setCrumbs([{ label: '我的收藏', href: '/favorites' }]); document.title = '我的收藏 · Replay'; renderFavorites();
    } else if (parts[0] !== 'streamer' || !parts[1]) {
      navigate('/', true); return;
    } else if (!parts[2]) {
      state.channel = parts[1]; state.date = ''; state.currentDates = await getDates(state.channel);
      setActiveNav('streamers'); setCrumbs([{ label: state.channel, href: channelPath(state.channel) }]); document.title = `${state.channel} · Replay`; await renderChannel(state.channel, routeId);
    } else if (parts[2] === 'dates') {
      state.channel = parts[1]; state.date = new URLSearchParams(location.search).get('from') || ''; state.currentDates = await getDates(state.channel);
      setActiveNav('streamers'); setCrumbs([{ label: state.channel, href: channelPath(state.channel) }, { label: '全部日期', href: `${channelPath(state.channel)}/dates` }]); document.title = `${state.channel}的全部日期 · Replay`; await renderDateLibrary(state.channel, routeId);
    } else {
      state.channel = parts[1]; state.date = parts[2]; state.currentDates = await getDates(state.channel);
      setActiveNav('streamers'); setCrumbs([{ label: state.channel, href: channelPath(state.channel) }, { label: shortDate(state.date), action: 'focus-calendar' }]); document.title = `${state.channel} ${state.date} · Replay`; await renderWatch(state.channel, state.date, routeId);
    }
  } catch (error) { if (routeId === state.routeId) showError(error); }
}

document.addEventListener('click', async event => {
  const link = event.target.closest('a[data-link]');
  if (link && !link.classList.contains('disabled') && !event.metaKey && !event.ctrlKey && !event.shiftKey && event.button === 0) { event.preventDefault(); navigate(`${link.pathname}${link.search}`, false, link.matches('.date-card,[data-transition="watch"]') ? link : null); return; }
  const channel = event.target.closest('[data-open-channel]');
  if (channel) { navigate(channelPath(channel.dataset.openChannel), false, channel); return; }
  const sort = event.target.closest('[data-sort]');
  if (sort) { state.sort = sort.dataset.sort; drawStreamerGrid(); return; }
  const month = event.target.closest('[data-jump-month]');
  if (month) {
    document.querySelectorAll('[data-jump-month]').forEach(button => button.classList.toggle('active', button === month));
    document.querySelector(`[data-month-section="${month.dataset.jumpMonth}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); return;
  }
  const session = event.target.closest('[data-session-index]');
  if (session) { selectSession(await getSessions(state.channel, state.date), Number(session.dataset.sessionIndex), true); return; }
  const adjacent = event.target.closest('[data-adjacent-date]');
  if (adjacent?.dataset.adjacentDate) { navigate(watchPath(state.channel, adjacent.dataset.adjacentDate)); return; }
  if (event.target.closest('[data-focus-calendar]')) {
    const calendar = document.querySelector('.recording-calendar');
    calendar?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    calendar?.classList.remove('calendar-pulse'); requestAnimationFrame(() => calendar?.classList.add('calendar-pulse')); return;
  }
  const libraryMonth = event.target.closest('[data-library-month]');
  if (libraryMonth) {
    document.querySelectorAll('[data-library-month]').forEach(button => button.classList.toggle('active', button === libraryMonth));
    document.querySelector(`[data-library-month-section="${libraryMonth.dataset.libraryMonth}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); return;
  }
  if (event.target.closest('[data-copy-link]')) {
    const copied = await copyCurrentLink();
    toast(copied ? '链接已复制' : '复制失败，请从地址栏复制');
    return;
  }
  if (event.target.closest('[data-toggle-favorite]')) { toggleFavorite(); return; }
  if (event.target.closest('[data-retry]')) route();
});

document.addEventListener('change', event => {
  if (event.target.matches('[data-calendar-year]')) drawCalendar(event.target.value, '');
  if (event.target.matches('[data-calendar-month]')) drawCalendar(document.querySelector('[data-calendar-year]').value, event.target.value);
});
document.addEventListener('submit', event => {
  const form = event.target.closest('.directory-search');
  if (!form) return;
  event.preventDefault();
  const query = form.querySelector('input').value.trim();
  navigate(directoryUrl(1, state.sort, query));
});
globalSearch.addEventListener('submit', event => { event.preventDefault(); const query = globalSearchInput.value.trim(); navigate(directoryUrl(1, 'recent', query)); });
themeButton.addEventListener('click', () => { const open = themeMenu.hidden; themeMenu.hidden = !open; themeButton.setAttribute('aria-expanded', String(open)); });
themeMenu.addEventListener('click', event => { const choice = event.target.closest('[data-theme-choice]'); if (!choice) return; applyTheme(choice.dataset.themeChoice); themeMenu.hidden = true; themeButton.setAttribute('aria-expanded', 'false'); });
document.addEventListener('click', event => { if (!event.target.closest('.theme-picker')) { themeMenu.hidden = true; themeButton.setAttribute('aria-expanded', 'false'); } });
document.addEventListener('keydown', event => { if (event.key === 'Escape') themeMenu.hidden = true; });
systemTheme.addEventListener('change', () => { if (document.documentElement.dataset.themeMode === 'auto') applyTheme('auto', false); });
window.addEventListener('popstate', () => {
  if (document.startViewTransition) document.startViewTransition(() => route());
  else route();
});
applyTheme(document.documentElement.dataset.themeMode || 'auto', false);
route();
