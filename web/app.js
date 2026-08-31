const app = document.querySelector('#app');
const themeButton = document.querySelector('#themeButton');
const themeIcon = document.querySelector('#themeIcon');
const themeLabel = document.querySelector('#themeLabel');
const themeMenu = document.querySelector('#themeMenu');
const deviceTheme = window.matchMedia('(prefers-color-scheme: dark)');

const themeNames = { auto: '跟随设备', light: '浅色', dark: '深色' };
const themeIcons = {
  auto: '<path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 13.5v-7ZM9 20h6M12 16v4"/>',
  light: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.65 17.65l1.42 1.42M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.65 6.35l1.42-1.42"/>',
  dark: '<path d="M20.3 15.4A9 9 0 0 1 8.6 3.7 9 9 0 1 0 20.3 15.4Z"/>'
};
const playerIcons = {
  play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 7 8 5-8 5V7Z"/></svg>',
  pause: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 7v10M15 7v10"/></svg>',
  volume: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 6 7 10H4v4h3l4 4V6ZM15 9a4 4 0 0 1 0 6M17.5 6.5a8 8 0 0 1 0 11"/></svg>',
  muted: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 6 7 10H4v4h3l4 4V6ZM16 10l4 4M20 10l-4 4"/></svg>',
  fullscreen: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/></svg>'
};
let themeMode = localStorage.getItem('theme-mode') || 'auto';
if (!themeNames[themeMode]) themeMode = themeMode === 'oled' ? 'dark' : 'auto';
let activePlayer = null;

function applyTheme() {
  const resolved = themeMode === 'auto' ? (deviceTheme.matches ? 'dark' : 'light') : themeMode;
  document.documentElement.dataset.theme = resolved;
  document.querySelector('meta[name="theme-color"]').content =
    resolved === 'light' ? '#f5f5f3' : '#000000';
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
  }
});
deviceTheme.addEventListener('change', () => { if (themeMode === 'auto') applyTheme(); });
applyTheme();

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[char]);
}

function coverMarkup(messageId, className = '') {
  const image = messageId
    ? `<img data-cover src="/api/thumbnail/${Number(messageId)}" alt="" loading="lazy">`
    : '';
  return `<span class="cover-frame ${className}">
    ${image}
    <span class="cover-fallback" aria-hidden="true">
      <svg viewBox="0 0 24 24"><path d="m9 7 8 5-8 5V7Z"/></svg>
    </span>
  </span>`;
}

function bindCoverFallbacks(root = document) {
  root.querySelectorAll('img[data-cover]').forEach(image => {
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

function formatDate(date) {
  const parsed = new Date(`${date}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return date;
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' }).format(parsed);
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
  app.innerHTML = `
    <section class="library-heading">
      <div class="heading-copy">
        <p class="eyebrow">LIBRARY</p>
        <h1>主播</h1>
        <p class="page-subtitle">${streamers.length} 位主播的直播记录</p>
      </div>
      <label class="search-wrap">
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>
        <input id="streamerSearch" class="search-input" type="search" placeholder="搜索主播" autocomplete="off">
      </label>
    </section>
    <section id="streamerGrid" class="media-grid" aria-label="主播列表"></section>`;

  const grid = document.querySelector('#streamerGrid');
  const draw = items => {
    grid.innerHTML = items.length ? items.map(item => `
      <button class="streamer-card" type="button" data-name="${escapeHtml(item.name)}">
        ${coverMarkup(item.cover_message_id, 'streamer-cover')}
        <span class="card-copy">
          <span class="card-title">${escapeHtml(item.name)}</span>
          <span class="card-meta">${item.session_count} 场直播</span>
        </span>
      </button>`).join('') : '<div class="empty-state compact"><p>没有找到匹配的主播</p></div>';
    bindCoverFallbacks(grid);
    grid.querySelectorAll('.streamer-card').forEach(card => {
      card.addEventListener('click', () => { location.hash = `#/streamer/${encodeURIComponent(card.dataset.name)}`; });
    });
  };
  draw(streamers);
  document.querySelector('#streamerSearch').addEventListener('input', event => {
    const query = event.target.value.trim().toLocaleLowerCase();
    draw(streamers.filter(item => item.name.toLocaleLowerCase().includes(query)));
  });
}

async function renderStreamer(streamer) {
  loading(`正在读取 ${streamer} 的直播日期…`);
  const dates = await api('/api/dates', { streamer });
  const groups = new Map();
  dates.forEach(item => {
    const month = item.date.slice(0, 7);
    if (!groups.has(month)) groups.set(month, []);
    groups.get(month).push(item);
  });
  app.innerHTML = `
    <nav class="breadcrumb" aria-label="当前位置">
      <button type="button" data-back>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg>
        主播
      </button>
    </nav>
    <section class="detail-heading">
      <div class="heading-copy">
        <h1>${escapeHtml(streamer)}</h1>
        <p class="page-subtitle">${dates.reduce((sum, item) => sum + item.session_count, 0)} 场直播 · ${dates.length} 个日期</p>
      </div>
    </section>
    <div>${[...groups].map(([month, items]) => {
      const [year, monthNumber] = month.split('-');
      return `<section class="month-section">
        <div class="month-heading"><h2>${Number(monthNumber)} 月</h2><span>${year}</span></div>
        <div class="date-grid">${items.map(item => {
          const parsedDate = new Date(`${item.date}T00:00:00`);
          const weekday = new Intl.DateTimeFormat('zh-CN', { weekday: 'short' }).format(parsedDate);
          return `<button class="date-card" type="button" data-date="${item.date}">
            ${coverMarkup(item.cover_message_id, 'date-cover')}
            <span class="date-copy">
              <span class="date-title">${Number(monthNumber)} 月 ${Number(item.date.slice(8, 10))} 日</span>
              <span class="date-meta">${weekday} · ${item.session_count} 场直播</span>
            </span>
          </button>`;
        }).join('')}</div>
      </section>`;
    }).join('')}</div>`;
  bindCoverFallbacks(app);
  document.querySelector('[data-back]').addEventListener('click', () => { location.hash = '#/'; });
  document.querySelectorAll('.date-card').forEach(card => {
    card.addEventListener('click', () => {
      location.hash = `#/streamer/${encodeURIComponent(streamer)}/${card.dataset.date}`;
    });
  });
}

async function renderDate(streamer, date) {
  loading('正在读取当天录像…');
  const sessions = await api('/api/sessions', { streamer, date });
  app.innerHTML = `
    <nav class="breadcrumb" aria-label="当前位置">
      <button type="button" data-back>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg>
        ${escapeHtml(streamer)}
      </button>
    </nav>
    <section class="detail-heading watch-heading">
      <div class="heading-copy">
        <h1>${escapeHtml(formatDate(date))}</h1>
        <p class="page-subtitle">${escapeHtml(streamer)} · ${sessions.length} 场直播</p>
      </div>
    </section>
    ${sessions.length ? `<div class="watch-layout">
      <div class="watch-main">
        <div id="playerMount"></div>
        <div id="playerDescription" class="player-description"></div>
      </div>
      <aside class="session-panel">
        <div class="session-panel-heading"><h2>当天录播</h2><span>${sessions.length}</span></div>
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
      ${coverMarkup(session.parts[0]?.message_id, 'session-cover')}
      <span class="session-copy">
        <span class="session-time">${escapeHtml(session.time.slice(0, 5))}</span>
        <span class="session-meta">${escapeHtml(session.platform)} · ${session.part_count} 段 · ${formatDuration(session.total_duration)}</span>
      </span>
    </button>`).join('');
  bindCoverFallbacks(list);

  const selectSession = index => {
    if (activePlayer) activePlayer.destroy();
    document.querySelectorAll('.session-card').forEach((card, cardIndex) => card.classList.toggle('active', cardIndex === index));
    const session = sessions[index];
    activePlayer = new MergedPlayer(document.querySelector('#playerMount'), session);
    document.querySelector('#playerDescription').innerHTML = `
      <div><h2>${escapeHtml(session.time.slice(0, 5))} 开播</h2><p>${escapeHtml(session.platform)} · ${session.part_count} 个分片连续播放</p></div>`;
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
    const posterId = this.parts[0]?.message_id;
    this.mount.innerHTML = `
      <div class="player-shell">
        <div class="video-stage">
          <video playsinline preload="none"${posterId ? ` poster="/api/thumbnail/${posterId}"` : ''}></video>
          <div class="video-placeholder"><strong>${this.parts.length ? '点击播放' : '录像不可用'}</strong><span>${this.parts.length ? '视频直接从 Telegram 读取' : '频道消息可能已被删除'}</span></div>
        </div>
        <div class="player-controls">
          <button class="player-button play-button" type="button" aria-label="播放">${playerIcons.play}</button>
          <input class="timeline" type="range" min="0" max="1" value="0" step="0.1" aria-label="播放进度">
          <span class="player-time">00:00 / 00:00</span>
          <span class="part-indicator">第 1 / ${Math.max(1, this.parts.length)} 段</span>
          <select class="rate-select" aria-label="播放速度">
            <option value="0.75">0.75×</option><option value="1" selected>1×</option>
            <option value="1.25">1.25×</option><option value="1.5">1.5×</option><option value="2">2×</option>
          </select>
          <button class="player-button mute-button" type="button" aria-label="静音">${playerIcons.volume}</button>
          <button class="player-button fullscreen-button" type="button" aria-label="全屏">${playerIcons.fullscreen}</button>
        </div>
      </div>`;
    this.root = mount.querySelector('.player-shell');
    this.video = mount.querySelector('video');
    this.placeholder = mount.querySelector('.video-placeholder');
    this.playButton = mount.querySelector('.play-button');
    this.timeline = mount.querySelector('.timeline');
    this.timeLabel = mount.querySelector('.player-time');
    this.partIndicator = mount.querySelector('.part-indicator');
    this.bind();
    this.recalculate();
    if (this.parts.length) this.loadPart(0, 0, false);
  }

  bind() {
    this.togglePlay = () => this.video.paused ? this.video.play().catch(() => {}) : this.video.pause();
    this.playButton.addEventListener('click', this.togglePlay);
    this.video.addEventListener('click', this.togglePlay);
    this.video.addEventListener('play', () => { this.playButton.innerHTML = playerIcons.pause; this.placeholder.hidden = true; });
    this.video.addEventListener('pause', () => { this.playButton.innerHTML = playerIcons.play; });
    this.video.addEventListener('timeupdate', () => this.updateProgress());
    this.video.addEventListener('durationchange', () => {
      if (Number.isFinite(this.video.duration) && this.video.duration > 0 && !this.parts[this.index].duration) {
        this.parts[this.index].duration = this.video.duration;
        this.recalculate();
      }
    });
    this.video.addEventListener('ended', () => {
      if (this.index < this.parts.length - 1) this.loadPart(this.index + 1, 0, true);
    });
    this.timeline.addEventListener('input', () => {
      this.timeLabel.textContent = `${formatDuration(Number(this.timeline.value)).replace('时长未知', '00:00')} / ${formatDuration(this.total).replace('时长未知', '00:00')}`;
    });
    this.timeline.addEventListener('change', () => this.seek(Number(this.timeline.value)));
    this.mount.querySelector('.rate-select').addEventListener('change', event => { this.video.playbackRate = Number(event.target.value); });
    this.mount.querySelector('.mute-button').addEventListener('click', event => {
      this.video.muted = !this.video.muted;
      event.currentTarget.innerHTML = this.video.muted ? playerIcons.muted : playerIcons.volume;
    });
    this.mount.querySelector('.fullscreen-button').addEventListener('click', () => {
      if (this.root.requestFullscreen) this.root.requestFullscreen();
      else if (this.video.webkitEnterFullscreen) this.video.webkitEnterFullscreen();
    });
  }

  recalculate() {
    this.offsets = [];
    let total = 0;
    this.parts.forEach(part => { this.offsets.push(total); total += Number(part.duration || 0); });
    this.total = total;
    this.timeline.max = Math.max(1, total);
    this.updateProgress();
  }

  loadPart(index, localTime = 0, autoplay = false) {
    if (this.destroyed || !this.parts[index]) return;
    this.index = index;
    this.video.src = `/api/media/${this.parts[index].message_id}`;
    this.video.load();
    this.partIndicator.textContent = `第 ${index + 1} / ${this.parts.length} 段`;
    const ready = () => {
      this.video.currentTime = Math.min(localTime, Math.max(0, this.video.duration - .05));
      if (autoplay) this.video.play().catch(() => {});
    };
    this.video.addEventListener('loadedmetadata', ready, { once: true });
  }

  seek(globalTime) {
    if (!this.parts.length) return;
    let targetIndex = this.parts.length - 1;
    for (let index = 0; index < this.parts.length; index++) {
      const end = this.offsets[index] + Number(this.parts[index].duration || 0);
      if (globalTime < end || index === this.parts.length - 1) { targetIndex = index; break; }
    }
    const localTime = Math.max(0, globalTime - this.offsets[targetIndex]);
    const autoplay = !this.video.paused;
    if (targetIndex === this.index && this.video.readyState > 0) this.video.currentTime = localTime;
    else this.loadPart(targetIndex, localTime, autoplay);
  }

  updateProgress() {
    const current = (this.offsets?.[this.index] || 0) + (Number(this.video?.currentTime) || 0);
    if (this.timeline) this.timeline.value = Math.min(current, this.total || 1);
    if (this.timeLabel) this.timeLabel.textContent = `${formatDuration(current).replace('时长未知', '00:00')} / ${formatDuration(this.total).replace('时长未知', '00:00')}`;
  }

  destroy() {
    this.destroyed = true;
    if (this.video) { this.video.pause(); this.video.removeAttribute('src'); this.video.load(); }
    this.mount.innerHTML = '';
  }
}

async function route() {
  if (activePlayer) { activePlayer.destroy(); activePlayer = null; }
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
