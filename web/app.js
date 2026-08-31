const app = document.querySelector('#app');
const themeButton = document.querySelector('#themeButton');
const themeIcon = document.querySelector('#themeIcon');
const themeLabel = document.querySelector('#themeLabel');

const themeModes = ['auto', 'light', 'dark', 'oled'];
const themeNames = { auto: '自动', light: '日间', dark: '夜间', oled: 'OLED' };
const themeIcons = { auto: '◐', light: '☀', dark: '☾', oled: '●' };
let themeMode = localStorage.getItem('theme-mode') || 'auto';
let activePlayer = null;

function automaticTheme() {
  const hour = new Date().getHours();
  if (hour >= 7 && hour < 18) return 'light';
  if (hour >= 18 && hour < 23) return 'dark';
  return 'oled';
}

function applyTheme() {
  const resolved = themeMode === 'auto' ? automaticTheme() : themeMode;
  document.documentElement.dataset.theme = resolved;
  document.querySelector('meta[name="theme-color"]').content =
    resolved === 'light' ? '#f4f6fa' : resolved === 'dark' ? '#11141b' : '#000000';
  themeIcon.textContent = themeIcons[themeMode];
  themeLabel.textContent = themeNames[themeMode];
  themeButton.title = `当前：${themeNames[themeMode]}，点击切换`;
}

themeButton.addEventListener('click', () => {
  themeMode = themeModes[(themeModes.indexOf(themeMode) + 1) % themeModes.length];
  localStorage.setItem('theme-mode', themeMode);
  applyTheme();
});
applyTheme();
setInterval(() => { if (themeMode === 'auto') applyTheme(); }, 60_000);

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[char]);
}

function hueFor(value) {
  let hash = 0;
  for (const char of value) hash = ((hash << 5) - hash + char.codePointAt(0)) | 0;
  return Math.abs(hash) % 300;
}

function initials(name) {
  return [...name.trim()].slice(0, 2).join('').toUpperCase();
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
  loading('正在整理主播列表…');
  const streamers = await api('/api/streamers');
  app.innerHTML = `
    <section class="page-heading">
      <div class="heading-copy">
        <p class="eyebrow">Archive</p>
        <h1>所有主播</h1>
        <p class="page-subtitle">${streamers.length} 位主播，选择一位查看完整直播记录</p>
      </div>
      <label class="search-wrap">
        <input id="streamerSearch" class="search-input" type="search" placeholder="搜索主播" autocomplete="off" aria-label="搜索主播">
      </label>
    </section>
    <section id="streamerGrid" class="streamer-grid"></section>`;

  const grid = document.querySelector('#streamerGrid');
  const draw = items => {
    grid.innerHTML = items.length ? items.map(item => `
      <button class="streamer-card" type="button" data-name="${escapeHtml(item.name)}">
        <span class="avatar" style="--hue:${hueFor(item.name)}">${escapeHtml(initials(item.name))}</span>
        <span class="card-copy">
          <span class="card-title">${escapeHtml(item.name)}</span>
          <span class="card-meta">${item.session_count} 场 · 更新至 ${escapeHtml(item.latest_date)}</span>
        </span>
        <span class="card-arrow" aria-hidden="true">›</span>
      </button>`).join('') : '<div class="empty-state"><p>没有找到匹配的主播</p></div>';
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
    <button class="back-button" type="button" data-back>← 所有主播</button>
    <section class="page-heading">
      <div class="heading-copy">
        <p class="eyebrow">Streamer</p>
        <h1>${escapeHtml(streamer)}</h1>
        <p class="page-subtitle">共 ${dates.reduce((sum, item) => sum + item.session_count, 0)} 场直播，按开播日期归档</p>
      </div>
    </section>
    <div>${[...groups].map(([month, items]) => {
      const [year, monthNumber] = month.split('-');
      return `<section class="month-section">
        <h2 class="month-title">${year} 年 ${Number(monthNumber)} 月</h2>
        <div class="date-grid">${items.map(item => {
          const date = new Date(`${item.date}T00:00:00`);
          const weekday = new Intl.DateTimeFormat('zh-CN', { weekday: 'short' }).format(date);
          return `<button class="date-card" type="button" data-date="${item.date}">
            <span class="date-day">${Number(item.date.slice(8, 10))}</span>
            <span class="date-weekday">${weekday}</span>
            <span class="date-count">${item.session_count} 场直播</span>
          </button>`;
        }).join('')}</div>
      </section>`;
    }).join('')}</div>`;
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
    <button class="back-button" type="button" data-back>← ${escapeHtml(streamer)}</button>
    <section class="page-heading">
      <div class="heading-copy">
        <p class="eyebrow">${escapeHtml(streamer)}</p>
        <h1>${escapeHtml(formatDate(date))}</h1>
        <p class="page-subtitle">当天共 ${sessions.length} 场直播</p>
      </div>
    </section>
    ${sessions.length ? `<div class="watch-layout">
      <div class="watch-main">
        <div id="playerMount"></div>
        <div id="playerDescription" class="player-description"></div>
      </div>
      <aside id="sessionList" class="session-list" aria-label="当天直播列表"></aside>
    </div>` : '<section class="empty-state"><p>当天没有可用录像</p></section>'}`;
  document.querySelector('[data-back]').addEventListener('click', () => {
    location.hash = `#/streamer/${encodeURIComponent(streamer)}`;
  });
  if (!sessions.length) return;

  const list = document.querySelector('#sessionList');
  list.innerHTML = sessions.map((session, index) => `
    <button class="session-card" type="button" data-session="${index}">
      <div class="session-time">${escapeHtml(session.time.slice(0, 5))}</div>
      <div class="session-meta">
        <span>${escapeHtml(session.platform)}</span>
        <span class="dot">${session.part_count} 段</span>
        <span class="dot">${formatDuration(session.total_duration)}</span>
      </div>
    </button>`).join('');

  const selectSession = index => {
    if (activePlayer) activePlayer.destroy();
    document.querySelectorAll('.session-card').forEach((card, cardIndex) => card.classList.toggle('active', cardIndex === index));
    const session = sessions[index];
    activePlayer = new MergedPlayer(document.querySelector('#playerMount'), session);
    document.querySelector('#playerDescription').innerHTML = `
      <div><h2>${escapeHtml(session.time.slice(0, 5))} 开播</h2><p>${escapeHtml(session.platform)} · ${session.part_count} 个录像分片</p></div>`;
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
    this.mount.innerHTML = `
      <div class="player-shell">
        <div class="video-stage">
          <video playsinline preload="none"></video>
          <div class="video-placeholder"><strong>${this.parts.length ? '点击播放' : '录像不可用'}</strong><span>${this.parts.length ? '视频直接从 Telegram 读取' : '频道消息可能已被删除'}</span></div>
        </div>
        <div class="player-controls">
          <button class="player-button play-button" type="button" aria-label="播放">▶</button>
          <input class="timeline" type="range" min="0" max="1" value="0" step="0.1" aria-label="播放进度">
          <span class="player-time">00:00 / 00:00</span>
          <span class="part-indicator">第 1 / ${Math.max(1, this.parts.length)} 段</span>
          <select class="rate-select" aria-label="播放速度">
            <option value="0.75">0.75×</option><option value="1" selected>1×</option>
            <option value="1.25">1.25×</option><option value="1.5">1.5×</option><option value="2">2×</option>
          </select>
          <button class="player-button mute-button" type="button" aria-label="静音">♬</button>
          <button class="player-button fullscreen-button" type="button" aria-label="全屏">⛶</button>
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
    this.video.addEventListener('play', () => { this.playButton.textContent = 'Ⅱ'; this.placeholder.hidden = true; });
    this.video.addEventListener('pause', () => { this.playButton.textContent = '▶'; });
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
      event.currentTarget.textContent = this.video.muted ? '×' : '♬';
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
