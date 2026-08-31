const app = document.querySelector('#app');
const themeButton = document.querySelector('#themeButton');
const themeIcon = document.querySelector('#themeIcon');
const themeLabel = document.querySelector('#themeLabel');
const themeMenu = document.querySelector('#themeMenu');
const siteSearch = document.querySelector('#siteSearch');
const siteSearchInput = document.querySelector('#siteSearchInput');
const sideNav = document.querySelector('.side-nav');
const deviceTheme = window.matchMedia('(prefers-color-scheme: dark)');
const nameCollator = new Intl.Collator('zh-CN-u-co-pinyin', { numeric: true, sensitivity: 'base' });

const themeNames = { auto: '跟随设备', light: '浅色', dark: '深色' };
const themeIcons = {
  auto: '<path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 13.5v-7ZM9 20h6M12 16v4"/>',
  light: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.65 17.65l1.42 1.42M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.65 6.35l1.42-1.42"/>',
  dark: '<path d="M20.3 15.4A9 9 0 0 1 8.6 3.7 9 9 0 1 0 20.3 15.4Z"/>'
};
const playerIcons = {
  play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 7 8 5-8 5V7Z"/></svg>',
  pause: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 7v10M15 7v10"/></svg>',
  backward: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 7H4V2M4.7 7.2A8.5 8.5 0 1 1 3.5 15"/></svg>',
  forward: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 7h5V2M19.3 7.2A8.5 8.5 0 1 0 20.5 15"/></svg>',
  volume: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 6 7 10H4v4h3l4 4V6ZM15 9a4 4 0 0 1 0 6M17.5 6.5a8 8 0 0 1 0 11"/></svg>',
  muted: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 6 7 10H4v4h3l4 4V6ZM16 10l4 4M20 10l-4 4"/></svg>',
  pip: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M12 12h7v5h-7z"/></svg>',
  fullscreen: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/></svg>',
  fullscreenExit: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 8h5V3M21 8h-5V3M3 16h5v5M21 16h-5v5"/></svg>'
};
let themeMode = localStorage.getItem('theme-mode') || 'auto';
if (!themeNames[themeMode]) themeMode = themeMode === 'oled' ? 'dark' : 'auto';
let activePlayer = null;
let homeSort = 'recent';
let homeQuery = '';
let homeRenderer = null;

function updateNavigation(page) {
  sideNav.classList.toggle('is-hidden', page === 'watch');
  document.querySelector('[data-nav-home]').classList.toggle('active', page === 'home');
  document.querySelectorAll('[data-nav-sort]').forEach(button => {
    const selected = button.dataset.navSort === homeSort;
    button.classList.toggle('selected', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
}

function applyTheme() {
  const resolved = themeMode === 'auto' ? (deviceTheme.matches ? 'dark' : 'light') : themeMode;
  document.documentElement.dataset.theme = resolved;
  document.querySelector('meta[name="theme-color"]').content =
    resolved === 'light' ? '#ffffff' : '#000000';
  themeIcon.innerHTML = themeIcons[themeMode];
  themeLabel.textContent = themeNames[themeMode];
  themeButton.setAttribute('aria-label', `主题：${themeNames[themeMode]}`);
  themeMenu.querySelectorAll('[data-theme-choice]').forEach(item => {
    const selected = item.dataset.themeChoice === themeMode;
    item.setAttribute('aria-checked', String(selected));
    item.classList.toggle('selected', selected);
  });
}

themeButton.addEventListener('click', () => {
  const opening = themeMenu.hidden;
  themeMenu.hidden = !opening;
  themeButton.setAttribute('aria-expanded', String(opening));
});
themeMenu.addEventListener('click', event => {
  const item = event.target.closest('[data-theme-choice]');
  if (!item) return;
  themeMode = item.dataset.themeChoice;
  localStorage.setItem('theme-mode', themeMode);
  themeMenu.hidden = true;
  themeButton.setAttribute('aria-expanded', 'false');
  applyTheme();
});
document.addEventListener('click', event => {
  if (!event.target.closest('.theme-picker')) {
    themeMenu.hidden = true;
    themeButton.setAttribute('aria-expanded', 'false');
  }
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    themeMenu.hidden = true;
    themeButton.setAttribute('aria-expanded', 'false');
    siteSearch.classList.remove('expanded');
    document.querySelector('.header-inner').classList.remove('searching');
  }
});
deviceTheme.addEventListener('change', () => { if (themeMode === 'auto') applyTheme(); });
applyTheme();

siteSearch.addEventListener('submit', event => {
  event.preventDefault();
  if (window.matchMedia('(max-width: 620px)').matches && !siteSearch.classList.contains('expanded')) {
    siteSearch.classList.add('expanded');
    document.querySelector('.header-inner').classList.add('searching');
    siteSearchInput.focus();
    return;
  }
  homeQuery = siteSearchInput.value.trim();
  siteSearch.classList.remove('expanded');
  document.querySelector('.header-inner').classList.remove('searching');
  if (location.hash.replace(/^#\/?/, '').startsWith('streamer')) location.hash = '#/';
  else homeRenderer?.();
});
siteSearchInput.addEventListener('input', () => {
  homeQuery = siteSearchInput.value.trim();
  homeRenderer?.();
});
document.querySelector('.brand').addEventListener('click', () => {
  homeQuery = '';
  siteSearchInput.value = '';
  siteSearch.classList.remove('expanded');
  document.querySelector('.header-inner').classList.remove('searching');
});
document.querySelectorAll('[data-nav-sort]').forEach(button => {
  button.addEventListener('click', () => {
    homeSort = button.dataset.navSort;
    if (location.hash.replace(/^#\/?/, '').startsWith('streamer')) location.hash = '#/';
    else {
      updateNavigation('home');
      homeRenderer?.();
    }
  });
});

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[char]);
}

function coverMarkup(messageId, className = '', badge = '') {
  const url = messageId ? `/api/thumbnail/${Number(messageId)}` : '';
  const image = url ? `<img data-cover src="${url}" alt="" loading="lazy">` : '';
  const style = url ? ` style="--cover-image:url('${url}')"` : '';
  return `<span class="cover-frame ${className}"${style}>
    ${image}
    <span class="cover-fallback" aria-hidden="true">
      <svg viewBox="0 0 24 24"><path d="m9 7 8 5-8 5V7Z"/></svg>
    </span>
    ${badge ? `<span class="cover-badge">${escapeHtml(badge)}</span>` : ''}
  </span>`;
}

function bindCovers(root = document) {
  root.querySelectorAll('img[data-cover]').forEach(image => {
    const resolveShape = () => {
      const frame = image.closest('.cover-frame');
      frame?.classList.toggle('portrait-cover', image.naturalHeight > image.naturalWidth * 1.15);
      frame?.classList.add('cover-ready');
    };
    if (image.complete && image.naturalWidth) resolveShape();
    else image.addEventListener('load', resolveShape, { once: true });
    image.addEventListener('error', () => image.closest('.cover-frame')?.classList.add('cover-missing'), { once: true });
  });
}

function loading(label = '正在加载…') {
  app.innerHTML = `<div class="loading-state"><span class="spinner"></span><p>${escapeHtml(label)}</p></div>`;
}

function showError(error) {
  if (activePlayer) activePlayer.destroy();
  const template = document.querySelector('#errorTemplate').content.cloneNode(true);
  template.querySelector('p').textContent = error?.message || '发生了未知错误';
  template.querySelector('button').addEventListener('click', route);
  app.replaceChildren(template);
}

async function api(path, params = {}) {
  const query = new URLSearchParams(params);
  const response = await fetch(`${path}${query.size ? `?${query}` : ''}`, { credentials: 'same-origin' });
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

function formatCardDate(date) {
  const parsed = new Date(`${date}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return date;
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' }).format(parsed);
}

function formatRelativeDate(date) {
  const parsed = new Date(`${date}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return date;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((today - parsed) / 86400000);
  if (days === 0) return '今天';
  if (days === 1) return '昨天';
  if (days > 1 && days < 7) return `${days} 天前`;
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' }).format(parsed);
}

function formatDuration(seconds) {
  if (!seconds) return '时长未知';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  return hours > 0
    ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
    : `${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

async function renderHome() {
  loading('正在加载主播…');
  const streamers = await api('/api/streamers');
  app.dataset.page = 'home';
  updateNavigation('home');
  app.innerHTML = `
    <section class="content-heading">
      <div>
        <h1>主播</h1>
        <p>${streamers.length} 位主播的直播归档</p>
      </div>
      <div class="sort-switch" role="group" aria-label="主播排序方式">
        <button type="button" data-sort="recent">最近更新</button>
        <button type="button" data-sort="name">名称 A–Z</button>
      </div>
    </section>
    <section id="streamerGrid" class="video-grid" aria-label="主播列表"></section>`;

  const grid = document.querySelector('#streamerGrid');
  const draw = () => {
    const query = homeQuery.toLocaleLowerCase();
    const visible = streamers
      .filter(item => item.name.toLocaleLowerCase().includes(query))
      .sort((left, right) => homeSort === 'name'
        ? nameCollator.compare(left.name, right.name)
        : right.latest_date.localeCompare(left.latest_date) || right.cover_message_id - left.cover_message_id);
    document.querySelectorAll('[data-sort]').forEach(button => {
      const active = button.dataset.sort === homeSort;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    document.querySelectorAll('[data-nav-sort]').forEach(button => {
      const selected = button.dataset.navSort === homeSort;
      button.classList.toggle('selected', selected);
      button.setAttribute('aria-pressed', String(selected));
    });
    grid.innerHTML = visible.length ? visible.map(item => `
      <button class="video-card streamer-card" type="button" data-name="${escapeHtml(item.name)}">
        ${coverMarkup(item.cover_message_id, 'video-thumbnail', `${item.session_count} 场`)}
        <span class="video-copy">
          <span class="video-title">${escapeHtml(item.name)}</span>
          <span class="video-meta">更新于 ${escapeHtml(formatRelativeDate(item.latest_date))} · ${item.part_count} 个录像</span>
        </span>
      </button>`).join('') : '<div class="empty-state compact"><p>没有找到匹配的主播</p></div>';
    bindCovers(grid);
    grid.querySelectorAll('.streamer-card').forEach(card => {
      card.addEventListener('click', () => { location.hash = `#/streamer/${encodeURIComponent(card.dataset.name)}`; });
    });
  };
  homeRenderer = draw;
  document.querySelectorAll('[data-sort]').forEach(button => {
    button.addEventListener('click', () => {
      homeSort = button.dataset.sort;
      draw();
    });
  });
  draw();
}

async function renderStreamer(streamer) {
  loading(`正在读取 ${streamer} 的直播日期…`);
  const dates = await api('/api/dates', { streamer });
  app.dataset.page = 'channel';
  updateNavigation('channel');
  const groups = new Map();
  dates.forEach(item => {
    const month = item.date.slice(0, 7);
    if (!groups.has(month)) groups.set(month, []);
    groups.get(month).push(item);
  });
  const latestCover = dates[0]?.cover_message_id;
  app.innerHTML = `
    <nav class="breadcrumb" aria-label="当前位置">
      <button type="button" data-back>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg>
        主播
      </button>
    </nav>
    <section class="channel-shell">
      <div class="channel-profile">
        ${coverMarkup(latestCover, 'channel-avatar')}
        <div>
          <h1>${escapeHtml(streamer)}</h1>
          <p>${dates.reduce((sum, item) => sum + item.session_count, 0)} 场直播 · ${dates.length} 个直播日期</p>
        </div>
      </div>
    </section>
    <div class="channel-tabs"><span>直播归档</span></div>
    ${groups.size ? `<nav class="month-jump" aria-label="按月份快速定位">
      ${[...groups].map(([month]) => {
        const [year, monthNumber] = month.split('-');
        return `<button type="button" data-month="${month}">${year}/${monthNumber}</button>`;
      }).join('')}
    </nav>` : ''}
    <div>${[...groups].map(([month, items]) => {
      const [year, monthNumber] = month.split('-');
      return `<section class="month-section" data-month-section="${month}">
        <div class="month-heading"><h2>${year} 年 ${Number(monthNumber)} 月</h2><span>${items.length} 个日期</span></div>
        <div class="date-grid">${items.map(item => {
          const parsedDate = new Date(`${item.date}T00:00:00`);
          const weekday = new Intl.DateTimeFormat('zh-CN', { weekday: 'short' }).format(parsedDate);
          return `<button class="video-card date-card" type="button" data-date="${item.date}">
            ${coverMarkup(item.cover_message_id, 'video-thumbnail', `${item.session_count} 场`)}
            <span class="video-copy">
              <span class="video-title">${escapeHtml(formatCardDate(item.date))}</span>
              <span class="video-meta">${weekday} · ${item.part_count} 个录像</span>
            </span>
          </button>`;
        }).join('')}</div>
      </section>`;
    }).join('')}</div>`;
  bindCovers(app);
  document.querySelector('[data-back]').addEventListener('click', () => { location.hash = '#/'; });
  document.querySelectorAll('[data-month]').forEach(button => {
    button.addEventListener('click', () => {
      document.querySelector(`[data-month-section="${button.dataset.month}"]`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
  document.querySelectorAll('.date-card').forEach(card => {
    card.addEventListener('click', () => {
      location.hash = `#/streamer/${encodeURIComponent(streamer)}/${card.dataset.date}`;
    });
  });
}

async function renderDate(streamer, date) {
  loading('正在读取当天录像…');
  const sessions = await api('/api/sessions', { streamer, date });
  app.dataset.page = 'watch';
  updateNavigation('watch');
  app.innerHTML = `
    <nav class="breadcrumb" aria-label="当前位置">
      <button type="button" data-back>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg>
        ${escapeHtml(streamer)}
      </button>
    </nav>
    ${sessions.length ? `<div class="watch-layout">
      <div class="watch-main">
        <div id="playerMount"></div>
        <div id="playerDescription" class="player-description"></div>
      </div>
      <aside class="session-panel">
        <div class="session-panel-heading"><div><h2>当天录播</h2><p>${escapeHtml(formatCardDate(date))}</p></div><span>${sessions.length} 场</span></div>
        <div id="sessionList" class="session-list" aria-label="当天直播列表"></div>
      </aside>
    </div>` : '<section class="empty-state"><p>当天没有可用录像</p></section>'}`;
  document.querySelector('[data-back]').addEventListener('click', () => {
    location.hash = `#/streamer/${encodeURIComponent(streamer)}`;
  });
  if (!sessions.length) return;

  const list = document.querySelector('#sessionList');
  list.innerHTML = sessions.map((session, index) => `
    <button class="session-card" type="button" data-session="${index}">
      ${coverMarkup(session.parts[0]?.message_id, 'session-cover', session.total_duration ? formatDuration(session.total_duration) : '')}
      <span class="session-copy">
        <span class="session-time">${escapeHtml(session.time.slice(0, 5))} 开播</span>
        <span class="session-meta">${escapeHtml(session.platform)} · ${session.part_count} 个分片</span>
      </span>
    </button>`).join('');
  bindCovers(list);

  const selectSession = index => {
    if (activePlayer) activePlayer.destroy();
    document.querySelectorAll('.session-card').forEach((card, cardIndex) => card.classList.toggle('active', cardIndex === index));
    const session = sessions[index];
    activePlayer = new MergedPlayer(document.querySelector('#playerMount'), session);
    document.querySelector('#playerDescription').innerHTML = `
      <h1>${escapeHtml(streamer)} · ${escapeHtml(formatCardDate(date))} ${escapeHtml(session.time.slice(0, 5))} 直播回放</h1>
      <p>${escapeHtml(session.platform)} · ${session.part_count} 个分片连续播放 · 原始视频由 Telegram 提供</p>`;
  };
  list.querySelectorAll('.session-card').forEach(card => card.addEventListener('click', () => selectSession(Number(card.dataset.session))));
  selectSession(0);
}

class MergedPlayer {
  constructor(mount, session) {
    this.mount = mount;
    this.parts = session.parts.filter(part => part.available);
    this.index = 0;
    this.destroyed = false;
    this.listeners = [];
    this.loadGeneration = 0;
    this.pendingLoad = null;
    this.intendedPlaying = false;
    this.scrubbing = false;
    this.retryCount = 0;
    this.lastPositionSave = 0;
    this.sessionKey = `player-position:${this.parts.map(part => part.message_id).join('-')}`;
    const posterId = this.parts[0]?.message_id;
    this.mount.innerHTML = `
      <div class="player-shell show-controls" tabindex="0" data-player-state="idle">
        <div class="video-stage">
          <video playsinline preload="metadata"${posterId ? ` poster="/api/thumbnail/${posterId}"` : ''}></video>
          <div class="video-placeholder"><strong>${this.parts.length ? '点击播放' : '录像不可用'}</strong><span>${this.parts.length ? '视频直接从 Telegram 读取' : '频道消息可能已被删除'}</span></div>
          <div class="player-gesture-layer" aria-hidden="true"></div>
          <button class="center-play" type="button" aria-label="播放">${playerIcons.play}</button>
          <div class="player-spinner" role="status" aria-label="正在缓冲"><span></span></div>
          <div class="player-toast" role="status" aria-live="polite"></div>
          <div class="player-error" hidden>
            <strong>播放遇到问题</strong>
            <span>可以重试当前进度，或跳到附近位置。</span>
            <button type="button">重试</button>
          </div>
        </div>
        <div class="player-controls">
          <div class="timeline-wrap">
            <div class="timeline-buffer"></div>
            <input class="timeline" type="range" min="0" max="1" value="0" step="0.05" aria-label="播放进度">
            <output class="timeline-preview">00:00</output>
          </div>
          <div class="control-row">
            <button class="player-button play-button" type="button" aria-label="播放">${playerIcons.play}</button>
            <button class="player-button skip-button backward-button" type="button" aria-label="后退 10 秒">${playerIcons.backward}<span>10</span></button>
            <button class="player-button skip-button forward-button" type="button" aria-label="前进 10 秒">${playerIcons.forward}<span>10</span></button>
            <span class="player-time">00:00 / 00:00</span>
            <span class="part-indicator">第 1 / ${Math.max(1, this.parts.length)} 段</span>
            <span class="control-spacer"></span>
            <div class="volume-control">
              <button class="player-button mute-button" type="button" aria-label="静音">${playerIcons.volume}</button>
              <input class="volume-slider" type="range" min="0" max="1" value="1" step="0.02" aria-label="音量">
            </div>
            <select class="rate-select" aria-label="播放速度">
              <option value="0.5">0.5×</option><option value="0.75">0.75×</option>
              <option value="1" selected>1×</option><option value="1.25">1.25×</option>
              <option value="1.5">1.5×</option><option value="1.75">1.75×</option>
              <option value="2">2×</option><option value="2.5">2.5×</option><option value="3">3×</option>
            </select>
            <button class="player-button pip-button" type="button" aria-label="画中画">${playerIcons.pip}</button>
            <button class="player-button fullscreen-button" type="button" aria-label="全屏">${playerIcons.fullscreen}</button>
          </div>
        </div>
      </div>`;
    this.root = mount.querySelector('.player-shell');
    this.video = mount.querySelector('video');
    this.placeholder = mount.querySelector('.video-placeholder');
    this.gestureLayer = mount.querySelector('.player-gesture-layer');
    this.centerPlay = mount.querySelector('.center-play');
    this.toast = mount.querySelector('.player-toast');
    this.errorPanel = mount.querySelector('.player-error');
    this.playButton = mount.querySelector('.play-button');
    this.timeline = mount.querySelector('.timeline');
    this.timelineBuffer = mount.querySelector('.timeline-buffer');
    this.timelinePreview = mount.querySelector('.timeline-preview');
    this.timeLabel = mount.querySelector('.player-time');
    this.partIndicator = mount.querySelector('.part-indicator');
    this.rateSelect = mount.querySelector('.rate-select');
    this.volumeSlider = mount.querySelector('.volume-slider');
    this.muteButton = mount.querySelector('.mute-button');
    this.pipButton = mount.querySelector('.pip-button');
    this.fullscreenButton = mount.querySelector('.fullscreen-button');
    this.bind();
    this.restorePreferences();
    this.recalculate();
    if (this.parts.length) {
      const savedPosition = this.readSavedPosition();
      const target = this.resolveTime(savedPosition);
      this.loadPart(target.index, target.localTime, false);
    }
  }

  listen(target, event, handler, options) {
    target.addEventListener(event, handler, options);
    this.listeners.push(() => target.removeEventListener(event, handler, options));
  }

  bind() {
    this.listen(this.playButton, 'click', () => this.togglePlay());
    this.listen(this.centerPlay, 'click', () => this.togglePlay());
    this.listen(this.mount.querySelector('.backward-button'), 'click', () => this.seekBy(-10));
    this.listen(this.mount.querySelector('.forward-button'), 'click', () => this.seekBy(10));
    this.listen(this.errorPanel.querySelector('button'), 'click', () => this.retry());

    this.listen(this.video, 'loadstart', () => this.setState('loading', '正在读取录像…'));
    this.listen(this.video, 'loadedmetadata', () => this.handleMetadata());
    this.listen(this.video, 'canplay', () => {
      if (!this.pendingLoad && this.video.paused) this.setState('ready');
    });
    this.listen(this.video, 'play', () => {
      this.intendedPlaying = true;
      this.placeholder.hidden = true;
      this.errorPanel.hidden = true;
      this.syncPlayButtons();
    });
    this.listen(this.video, 'playing', () => {
      this.retryCount = 0;
      this.setState('playing');
      this.scheduleControlsHide();
    });
    this.listen(this.video, 'pause', () => {
      if (!this.pendingLoad) this.setState('paused');
      this.syncPlayButtons();
      this.showControls();
      this.savePosition();
    });
    this.listen(this.video, 'timeupdate', () => this.updateProgress());
    this.listen(this.video, 'progress', () => this.updateBuffered());
    this.listen(this.video, 'durationchange', () => this.updatePartDuration());
    this.listen(this.video, 'seeking', () => this.setState('seeking', '正在跳转…'));
    this.listen(this.video, 'seeked', () => {
      if (this.pendingLoad) this.finishPendingLoad();
      else this.setState(this.video.paused ? 'paused' : 'playing');
      this.updateProgress();
    });
    this.listen(this.video, 'waiting', () => {
      if (this.intendedPlaying) this.setState('buffering', '正在缓冲…');
      this.scheduleRecovery();
    });
    this.listen(this.video, 'stalled', () => {
      this.setState('buffering', '网络读取停滞，正在恢复…');
      this.scheduleRecovery();
    });
    this.listen(this.video, 'error', () => this.handleMediaError());
    this.listen(this.video, 'ended', () => {
      if (this.index < this.parts.length - 1) this.loadPart(this.index + 1, 0, true);
      else {
        this.intendedPlaying = false;
        this.setState('ended');
        this.syncPlayButtons();
        try { localStorage.removeItem(this.sessionKey); } catch (_) {}
      }
    });

    this.listen(this.timeline, 'pointerdown', () => {
      this.scrubbing = true;
      this.root.classList.add('is-scrubbing');
      this.showControls();
    });
    this.listen(this.timeline, 'input', () => this.previewSeek(Number(this.timeline.value)));
    this.listen(this.timeline, 'change', () => {
      this.scrubbing = false;
      this.root.classList.remove('is-scrubbing');
      this.seek(Number(this.timeline.value));
    });
    this.listen(this.timeline, 'pointerup', () => this.root.classList.remove('is-scrubbing'));

    this.listen(this.rateSelect, 'change', event => this.setRate(Number(event.target.value), true));
    this.listen(this.volumeSlider, 'input', event => this.setVolume(Number(event.target.value)));
    this.listen(this.muteButton, 'click', () => {
      this.video.muted = !this.video.muted;
      this.persistPreferences();
      this.syncVolume();
    });
    this.listen(this.pipButton, 'click', () => this.togglePictureInPicture());
    this.listen(this.fullscreenButton, 'click', () => this.toggleFullscreen());
    this.listen(document, 'fullscreenchange', () => this.syncFullscreen());
    this.listen(document, 'webkitfullscreenchange', () => this.syncFullscreen());

    this.listen(this.root, 'pointermove', () => this.showControls());
    this.listen(this.root, 'pointerleave', () => this.scheduleControlsHide());
    this.listen(this.root, 'focusin', () => this.showControls());
    this.listen(this.gestureLayer, 'click', event => this.handleGestureClick(event));
    this.listen(this.gestureLayer, 'pointerdown', event => this.handleLongPressStart(event));
    this.listen(this.gestureLayer, 'pointerup', () => this.handleLongPressEnd());
    this.listen(this.gestureLayer, 'pointercancel', () => this.handleLongPressEnd());
    this.listen(this.gestureLayer, 'pointerleave', () => this.handleLongPressEnd());
    this.listen(document, 'keydown', event => this.handleKeydown(event));

    if (!document.pictureInPictureEnabled || !this.video.requestPictureInPicture) this.pipButton.hidden = true;
    this.setupMediaSession();
  }

  recalculate() {
    this.offsets = [];
    let total = 0;
    this.parts.forEach(part => { this.offsets.push(total); total += Number(part.duration || 0); });
    this.total = total;
    this.timeline.max = Math.max(1, total);
    this.updateProgress();
  }

  resolveTime(globalTime) {
    const safeTime = Math.max(0, Math.min(Number(globalTime) || 0, Math.max(0, this.total - .05)));
    let index = Math.max(0, this.parts.length - 1);
    for (let candidate = 0; candidate < this.parts.length; candidate++) {
      const end = this.offsets[candidate] + Number(this.parts[candidate].duration || 0);
      if (safeTime < end || candidate === this.parts.length - 1) { index = candidate; break; }
    }
    return { index, localTime: Math.max(0, safeTime - (this.offsets[index] || 0)) };
  }

  loadPart(index, localTime = 0, autoplay = false) {
    if (this.destroyed || !this.parts[index]) return;
    const generation = ++this.loadGeneration;
    this.index = index;
    this.intendedPlaying = autoplay;
    const source = new URL(`/api/media/${this.parts[index].message_id}`, location.href).href;
    this.pendingLoad = { generation, index, localTime, autoplay, source };
    this.setState('loading', '正在读取录像…');
    this.video.pause();
    this.video.src = source;
    this.video.load();
    this.partIndicator.textContent = `第 ${index + 1} / ${this.parts.length} 段`;
  }

  seek(globalTime) {
    if (!this.parts.length) return;
    const target = this.resolveTime(globalTime);
    const autoplay = this.intendedPlaying || !this.video.paused;
    if (target.index === this.index && this.video.readyState > 0 && !this.pendingLoad) {
      this.setState('seeking', '正在跳转…');
      this.video.currentTime = Math.min(target.localTime, Math.max(0, this.video.duration - .05));
      if (autoplay) this.attemptPlay(this.loadGeneration);
    } else this.loadPart(target.index, target.localTime, autoplay);
    this.showToast(`跳转到 ${formatDuration(globalTime).replace('时长未知', '00:00')}`);
  }

  seekBy(delta) {
    const current = this.globalCurrentTime();
    const target = Math.max(0, Math.min(this.total || 0, current + delta));
    this.seek(target);
    this.showToast(`${delta > 0 ? '快进' : '后退'} ${Math.abs(delta)} 秒`);
  }

  globalCurrentTime() {
    return (this.offsets?.[this.index] || 0) + (Number(this.video?.currentTime) || 0);
  }

  updateProgress() {
    const current = this.globalCurrentTime();
    if (this.timeline && !this.scrubbing) this.timeline.value = Math.min(current, this.total || 1);
    if (this.timeLabel) this.timeLabel.textContent = `${formatDuration(current).replace('时长未知', '00:00')} / ${formatDuration(this.total).replace('时长未知', '00:00')}`;
    const played = this.total ? Math.min(100, current / this.total * 100) : 0;
    this.timeline?.style.setProperty('--played', `${played}%`);
    this.updateBuffered();
    if (Math.abs(current - this.lastPositionSave) >= 5) {
      this.lastPositionSave = current;
      this.savePosition();
    }
    if ('mediaSession' in navigator && Number.isFinite(this.video.duration) && this.video.duration > 0) {
      try {
        navigator.mediaSession.setPositionState({
          duration: this.total || this.video.duration,
          playbackRate: this.video.playbackRate,
          position: Math.min(current, (this.total || this.video.duration) - .01)
        });
      } catch (_) {}
    }
  }

  updateBuffered() {
    if (!this.timelineBuffer || !this.video?.buffered?.length || !this.total) return;
    let localEnd = 0;
    const current = Number(this.video.currentTime) || 0;
    for (let index = 0; index < this.video.buffered.length; index++) {
      if (current >= this.video.buffered.start(index) - .2 && current <= this.video.buffered.end(index) + .2) {
        localEnd = this.video.buffered.end(index);
        break;
      }
    }
    const globalEnd = (this.offsets[this.index] || 0) + localEnd;
    this.timelineBuffer.style.width = `${Math.min(100, globalEnd / this.total * 100)}%`;
  }

  previewSeek(value) {
    const label = formatDuration(value).replace('时长未知', '00:00');
    this.timelinePreview.textContent = label;
    this.timeLabel.textContent = `${label} / ${formatDuration(this.total).replace('时长未知', '00:00')}`;
    const ratio = this.total ? value / this.total : 0;
    this.timelinePreview.style.left = `${Math.min(98, Math.max(2, ratio * 100))}%`;
    this.timeline.style.setProperty('--played', `${ratio * 100}%`);
  }

  handleMetadata() {
    this.updatePartDuration();
    const pending = this.pendingLoad;
    if (!pending || pending.generation !== this.loadGeneration || pending.index !== this.index) return;
    if (this.video.currentSrc && this.video.currentSrc !== pending.source) return;
    this.video.defaultPlaybackRate = Number(this.rateSelect.value) || 1;
    this.video.playbackRate = this.video.defaultPlaybackRate;
    const target = Math.min(pending.localTime, Math.max(0, this.video.duration - .05));
    if (target > .05) this.video.currentTime = target;
    else this.finishPendingLoad();
  }

  finishPendingLoad() {
    const pending = this.pendingLoad;
    if (!pending || pending.generation !== this.loadGeneration) return;
    this.pendingLoad = null;
    this.setState(this.video.paused ? 'ready' : 'playing');
    if (pending.autoplay) this.attemptPlay(pending.generation);
  }

  updatePartDuration() {
    const duration = Number(this.video?.duration);
    if (!this.parts[this.index] || !Number.isFinite(duration) || duration <= 0) return;
    if (Math.abs(Number(this.parts[this.index].duration || 0) - duration) > .001) {
      this.parts[this.index].duration = duration;
      this.recalculate();
    }
  }

  async attemptPlay(generation = this.loadGeneration) {
    if (this.destroyed || generation !== this.loadGeneration) return;
    this.intendedPlaying = true;
    try {
      await this.video.play();
    } catch (error) {
      if (generation !== this.loadGeneration || error?.name === 'AbortError') return;
      this.intendedPlaying = false;
      if (error?.name !== 'NotAllowedError') this.showError('浏览器未能开始播放，请重试。');
    }
  }

  togglePlay() {
    this.root.focus({ preventScroll: true });
    if (this.video.ended) {
      this.seek(0);
      this.attemptPlay();
    } else if (this.video.paused) this.attemptPlay();
    else {
      this.intendedPlaying = false;
      this.video.pause();
    }
  }

  syncPlayButtons() {
    const playing = !this.video.paused && !this.video.ended;
    const icon = playing ? playerIcons.pause : playerIcons.play;
    this.playButton.innerHTML = icon;
    this.centerPlay.innerHTML = icon;
    this.playButton.setAttribute('aria-label', playing ? '暂停' : '播放');
    this.centerPlay.setAttribute('aria-label', playing ? '暂停' : '播放');
  }

  setState(state, status = '') {
    if (this.destroyed) return;
    this.root.dataset.playerState = state;
    this.root.setAttribute('aria-busy', String(['loading', 'seeking', 'buffering'].includes(state)));
    if (status) this.toast.setAttribute('data-status', status);
    else this.toast.removeAttribute('data-status');
  }

  showToast(message, duration = 1200) {
    clearTimeout(this.toastTimer);
    this.toast.textContent = message;
    this.toast.classList.add('visible');
    this.toastTimer = setTimeout(() => this.toast.classList.remove('visible'), duration);
  }

  showError(message) {
    this.setState('error');
    this.errorPanel.querySelector('span').textContent = message;
    this.errorPanel.hidden = false;
    this.showControls();
  }

  handleMediaError() {
    const messages = {
      1: '视频读取被中止。',
      2: '网络读取失败，请检查连接后重试。',
      3: '浏览器无法解码这段视频。',
      4: '当前视频格式不受浏览器支持。'
    };
    this.showError(messages[this.video.error?.code] || '录像加载失败，请重试。');
  }

  scheduleRecovery() {
    clearTimeout(this.recoveryTimer);
    if (!this.intendedPlaying || this.retryCount >= 2) return;
    const generation = this.loadGeneration;
    this.recoveryTimer = setTimeout(() => {
      if (!this.destroyed && generation === this.loadGeneration && this.video.readyState < 3) this.retry(true);
    }, 7000);
  }

  retry(automatic = false) {
    clearTimeout(this.recoveryTimer);
    if (automatic) this.retryCount += 1;
    else this.retryCount = 0;
    const localTime = Number(this.video.currentTime) || 0;
    const autoplay = this.intendedPlaying || !this.video.paused;
    this.errorPanel.hidden = true;
    this.showToast(automatic ? '连接停滞，正在自动恢复…' : '正在重新连接…', 1800);
    this.loadPart(this.index, localTime, autoplay);
  }

  setRate(rate, persist = false, notify = true) {
    const safeRate = Math.min(3, Math.max(.5, Number(rate) || 1));
    this.video.defaultPlaybackRate = safeRate;
    this.video.playbackRate = safeRate;
    this.rateSelect.value = String(safeRate);
    if (persist) this.persistPreferences();
    if (notify) this.showToast(`${safeRate}×`);
  }

  setVolume(volume) {
    this.video.volume = Math.min(1, Math.max(0, volume));
    this.video.muted = this.video.volume === 0;
    this.persistPreferences();
    this.syncVolume();
  }

  syncVolume() {
    this.volumeSlider.value = String(this.video.volume);
    const muted = this.video.muted || this.video.volume === 0;
    this.muteButton.innerHTML = muted ? playerIcons.muted : playerIcons.volume;
    this.muteButton.setAttribute('aria-label', muted ? '取消静音' : '静音');
  }

  restorePreferences() {
    try {
      const preferences = JSON.parse(localStorage.getItem('player-preferences') || '{}');
      const volume = Number.isFinite(preferences.volume) ? preferences.volume : 1;
      const rate = Number.isFinite(preferences.rate) ? preferences.rate : 1;
      this.video.volume = Math.min(1, Math.max(0, volume));
      this.video.muted = Boolean(preferences.muted);
      this.setRate(rate, false, false);
    } catch (_) {
      this.setRate(1, false, false);
    }
    this.syncVolume();
  }

  persistPreferences() {
    try {
      localStorage.setItem('player-preferences', JSON.stringify({
        volume: this.video.volume,
        muted: this.video.muted,
        rate: this.video.playbackRate
      }));
    } catch (_) {}
  }

  readSavedPosition() {
    try {
      const saved = Number(localStorage.getItem(this.sessionKey));
      if (Number.isFinite(saved) && saved > 5 && saved < this.total - 5) return saved;
    } catch (_) {}
    return 0;
  }

  savePosition() {
    if (!this.total || this.video.ended) return;
    try { localStorage.setItem(this.sessionKey, String(this.globalCurrentTime())); } catch (_) {}
  }

  handleGestureClick(event) {
    if (Date.now() < (this.suppressGestureClickUntil || 0)) return;
    if (event.detail >= 2) {
      clearTimeout(this.singleClickTimer);
      const bounds = this.gestureLayer.getBoundingClientRect();
      const ratio = (event.clientX - bounds.left) / bounds.width;
      if (ratio < .38) this.seekBy(-10);
      else if (ratio > .62) this.seekBy(10);
      else this.toggleFullscreen();
      return;
    }
    clearTimeout(this.singleClickTimer);
    this.singleClickTimer = setTimeout(() => this.togglePlay(), 220);
  }

  handleLongPressStart(event) {
    if (event.button !== undefined && event.button !== 0) return;
    const bounds = this.gestureLayer.getBoundingClientRect();
    if ((event.clientX - bounds.left) / bounds.width < .55) return;
    clearTimeout(this.longPressTimer);
    this.longPressTimer = setTimeout(() => {
      this.longPressRate = this.video.playbackRate;
      this.video.playbackRate = Math.max(2, this.video.playbackRate);
      this.root.classList.add('is-accelerating');
      this.showToast(`${this.video.playbackRate}× 快速播放`, 3000);
    }, 450);
  }

  handleLongPressEnd() {
    clearTimeout(this.longPressTimer);
    if (this.longPressRate == null) return;
    this.video.playbackRate = this.longPressRate;
    this.longPressRate = null;
    this.root.classList.remove('is-accelerating');
    this.suppressGestureClickUntil = Date.now() + 350;
    this.showToast('恢复正常速度');
  }

  handleKeydown(event) {
    if (this.destroyed || event.defaultPrevented) return;
    const tag = event.target?.tagName;
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(tag) && event.target !== this.timeline) return;
    const actions = {
      ' ': () => this.togglePlay(),
      k: () => this.togglePlay(),
      ArrowLeft: () => this.seekBy(-10),
      j: () => this.seekBy(-10),
      ArrowRight: () => this.seekBy(10),
      l: () => this.seekBy(10),
      ArrowUp: () => this.setVolume(this.video.volume + .05),
      ArrowDown: () => this.setVolume(this.video.volume - .05),
      m: () => { this.video.muted = !this.video.muted; this.persistPreferences(); this.syncVolume(); },
      f: () => this.toggleFullscreen(),
      p: () => this.togglePictureInPicture()
    };
    const action = actions[event.key] || actions[event.key?.toLowerCase()];
    if (!action) return;
    event.preventDefault();
    action();
  }

  async toggleFullscreen() {
    try {
      if (document.fullscreenElement || document.webkitFullscreenElement) {
        if (document.exitFullscreen) await document.exitFullscreen();
        else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
      } else if (this.root.requestFullscreen) await this.root.requestFullscreen();
      else if (this.root.webkitRequestFullscreen) this.root.webkitRequestFullscreen();
      else if (this.video.webkitEnterFullscreen) this.video.webkitEnterFullscreen();
    } catch (_) {
      this.showToast('浏览器未能切换全屏');
    } finally {
      this.syncFullscreen();
    }
  }

  syncFullscreen() {
    const fullscreen = document.fullscreenElement === this.root || document.webkitFullscreenElement === this.root;
    this.fullscreenButton.innerHTML = fullscreen ? playerIcons.fullscreenExit : playerIcons.fullscreen;
    this.fullscreenButton.setAttribute('aria-label', fullscreen ? '退出全屏' : '全屏');
    this.root.classList.toggle('is-fullscreen', fullscreen);
    this.showControls();
  }

  async togglePictureInPicture() {
    if (!document.pictureInPictureEnabled || !this.video.requestPictureInPicture) return;
    try {
      if (document.pictureInPictureElement) await document.exitPictureInPicture();
      else await this.video.requestPictureInPicture();
    } catch (_) {
      this.showToast('当前无法进入画中画');
    }
  }

  showControls() {
    clearTimeout(this.controlsTimer);
    this.root.classList.add('show-controls');
    if (!this.video.paused) this.scheduleControlsHide();
  }

  scheduleControlsHide() {
    clearTimeout(this.controlsTimer);
    if (this.video.paused || this.scrubbing) return;
    this.controlsTimer = setTimeout(() => this.root.classList.remove('show-controls'), 2600);
  }

  setupMediaSession() {
    if (!('mediaSession' in navigator)) return;
    const handlers = {
      play: () => this.attemptPlay(),
      pause: () => { this.intendedPlaying = false; this.video.pause(); },
      seekbackward: details => this.seekBy(-(details.seekOffset || 10)),
      seekforward: details => this.seekBy(details.seekOffset || 10),
      seekto: details => this.seek(details.seekTime)
    };
    for (const [action, handler] of Object.entries(handlers)) {
      try { navigator.mediaSession.setActionHandler(action, handler); } catch (_) {}
    }
  }

  destroy() {
    this.destroyed = true;
    this.savePosition();
    clearTimeout(this.toastTimer);
    clearTimeout(this.recoveryTimer);
    clearTimeout(this.controlsTimer);
    clearTimeout(this.singleClickTimer);
    clearTimeout(this.longPressTimer);
    this.listeners.splice(0).forEach(remove => remove());
    if ('mediaSession' in navigator) {
      for (const action of ['play', 'pause', 'seekbackward', 'seekforward', 'seekto']) {
        try { navigator.mediaSession.setActionHandler(action, null); } catch (_) {}
      }
    }
    if (this.video) { this.video.pause(); this.video.removeAttribute('src'); this.video.load(); }
    this.mount.innerHTML = '';
  }
}

async function route() {
  if (activePlayer) { activePlayer.destroy(); activePlayer = null; }
  homeRenderer = null;
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
  try {
    if (parts[0] !== 'streamer') await renderHome();
    else if (!parts[2]) await renderStreamer(parts[1]);
    else await renderDate(parts[1], parts[2]);
  } catch (error) {
    console.error(error);
    showError(error);
  }
}

window.addEventListener('hashchange', route);
route();
