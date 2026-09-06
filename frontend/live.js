/* The studio edits a local draft. A running server task is a separate snapshot. */
export function initLiveStudio({api, state, escapeHtml, longDate, formatDuration}) {
  const $ = id => document.getElementById(id);
  const sheet = $('liveSheet');
  const DRAFT_KEY = 'replay-live-draft-v1', CHANNEL_KEY = 'live-target-channel';
  const selected = new Map();
  let server = null, busy = false, searching = false, polling = false, streamersLoading = false;
  let previousOverflow = '', previousFocus = null, statusGeneration = 0, scrollLocked = false;
  try {
    const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || '[]');
    if (Array.isArray(draft)) draft.forEach(row => {
      if (Array.isArray(row) && Number.isSafeInteger(row[0]) && row[0] > 0 && typeof row[1] === 'string') selected.set(row[0], row[1]);
    });
    $('liveChannel').value = localStorage.getItem(CHANNEL_KEY) || '';
  } catch { /* Unavailable storage must not prevent selection or streaming. */ }
  function saveDraft() {
    try { localStorage.setItem(DRAFT_KEY, JSON.stringify([...selected])); }
    catch { feedback('浏览器无法保存草稿；关闭或刷新页面后可能丢失。', true); }
  }
  function feedback(message, error = false) {
    $('liveFeedback').textContent = message;
    $('liveFeedback').hidden = !message;
    $('liveFeedback').dataset.error = String(error);
  }
  function empty(title, message, tag = 'div') {
    return `<${tag} class="live-empty"><span aria-hidden="true">＋</span><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p></${tag}>`;
  }
  function setView(view) {
    sheet.dataset.view = view;
    sheet.querySelectorAll('[data-live-view]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.liveView === view)));
  }
  function openSheet() {
    if (sheet.open) return;
    previousFocus = document.activeElement;
    previousOverflow = document.body.style.overflow;
    sheet.showModal();
    scrollLocked = true;
    document.body.style.overflow = 'hidden';
    $('liveSheetClose').focus();
    fillStreamers();
    refreshStatus();
  }
  function restorePage() {
    if (!scrollLocked) return;
    scrollLocked = false;
    document.body.style.overflow = previousOverflow;
    if (previousFocus?.isConnected) previousFocus.focus();
  }
  function closeSheet() { sheet.close(); restorePage(); }
  sheet.addEventListener('cancel', e => { e.preventDefault(); closeSheet(); });
  // Native close events are queued: never unlock a newly reopened dialog.
  sheet.addEventListener('close', () => { if (!sheet.open) restorePage(); });
  function streamerSummary() {
    const count = sheet.querySelectorAll('#liveStreamerBox input:checked').length;
    $('liveStreamerSummary').textContent = count ? `已选 ${count} 位` : '全部主播';
  }
  async function fillStreamers() {
    const box = $('liveStreamerBox');
    if (box.dataset.loaded || streamersLoading) return;
    streamersLoading = true;
    try {
      const list = await api('/api/streamers');
      box.innerHTML = list.map(s => `<label class="live-streamer"><input type="checkbox" value="${escapeHtml(s.name)}" ${s.name === state.channel ? 'checked' : ''}><span>${escapeHtml(s.name)}</span><small>${Number(s.session_count)} 场</small></label>`).join('') || '<p class="live-hint">暂无主播</p>';
      box.dataset.loaded = '1';
      streamerSummary();
    } catch {
      box.innerHTML = '<p class="live-hint">主播加载失败 <button type="button" id="liveRetryStreamers" class="live-text-button">重试</button></p>';
    } finally { streamersLoading = false; }
  }
  async function search() {
    if (searching) return;
    const from = $('liveFrom').value, to = $('liveTo').value;
    if (from && to && from > to) {
      $('liveTo').setCustomValidity('结束日期不能早于开始日期');
      $('liveTo').reportValidity();
      return;
    }
    const exclude = $('liveExclude').value.trim().replaceAll('，', ',');
    if (exclude && !exclude.split(',').every(d => /^\d{4}-\d{2}-\d{2}$/.test(d.trim()) && !Number.isNaN(Date.parse(d.trim())))) {
      $('liveExclude').closest('details').open = true;
      $('liveExclude').setCustomValidity('请使用 YYYY-MM-DD 格式，多个日期用逗号分隔');
      $('liveExclude').reportValidity();
      return;
    }
    const box = $('liveResults');
    searching = true;
    $('liveSearch').disabled = true;
    $('liveSearch').textContent = '查找中…';
    box.setAttribute('aria-busy', 'true');
    $('liveResultSummary').textContent = '正在查找录像…';
    box.innerHTML = empty('正在整理录像', '录像较多时可能需要几十秒，请稍候。');
    updateResultActions();
    try {
      const params = {};
      const streamers = [...sheet.querySelectorAll('#liveStreamerBox input:checked')].map(c => c.value);
      if (streamers.length) params.streamer = streamers;
      if (from) params.from_date = from;
      if (to) params.to_date = to;
      if (exclude) params.exclude = exclude;
      const items = await api('/api/live/candidates', params);
      const available = items.filter(i => i.available).length;
      $('liveResultSummary').textContent = `${items.length} 段录像 · ${available} 段可播`;
      if (!items.length) {
        box.innerHTML = empty('没有找到录像', '试试其他主播，或扩大日期范围。');
        return;
      }
      const days = new Map();
      items.forEach(i => { if (!days.has(i.date)) days.set(i.date, []); days.get(i.date).push(i); });
      box.innerHTML = [...days].map(([date, rows]) => `<div class="live-day"><label class="live-day-head"><input type="checkbox" data-day="${escapeHtml(date)}"><span>${escapeHtml(longDate(date))}</span><small>${rows.length} 段</small></label>${rows.map(i => {
        const label = escapeHtml(`${i.streamer} ${i.label}`);
        return `<label class="live-result ${i.available ? '' : 'unavailable'}"><input type="checkbox" data-mid="${Number(i.message_id)}" data-label="${label}" ${i.available ? '' : 'disabled'}><span>${label}</span><small>${i.available ? formatDuration(i.duration) : '已失效'}</small></label>`;
      }).join('')}</div>`).join('');
      renderPlaylist(false);
    } catch (e) {
      $('liveResultSummary').textContent = '查找失败';
      box.innerHTML = empty('暂时无法获取录像', `${e.message}。请点击「查找录像」重试。`);
    } finally {
      searching = false;
      $('liveSearch').disabled = false;
      $('liveSearch').textContent = '查找录像';
      box.setAttribute('aria-busy', 'false');
      updateResultActions();
    }
  }
  function resultInputs() { return [...sheet.querySelectorAll('.live-result input:not(:disabled)')]; }
  function updateResultActions() {
    const disabled = searching || !resultInputs().length;
    ['liveResultsAll', 'liveResultsNone', 'liveResultsInvert'].forEach(id => $(id).disabled = disabled);
  }
  function sessionParts(index) {
    const s = (state.currentSessions || [])[index];
    return (s?.parts || []).filter(p => p.available).map(p => ({mid: Number(p.message_id), label: `${state.channel} ${state.date} ${s.time.slice(0, 5)} ${p.label || ('P' + p.position)}`}));
  }
  function syncSessionChecks() {
    document.querySelectorAll('.live-check').forEach(c => {
      const parts = sessionParts(Number(c.dataset.session));
      const count = parts.filter(p => selected.has(p.mid)).length;
      c.checked = parts.length > 0 && count === parts.length;
      c.indeterminate = count > 0 && count < parts.length;
    });
  }
  function renderPlaylist(persist = true) {
    if (persist) saveDraft();
    $('liveCount').textContent = selected.size;
    $('liveMobileCount').textContent = selected.size;
    $('liveFabBadge').hidden = !selected.size;
    $('liveFabBadge').textContent = selected.size;
    $('liveClear').disabled = !selected.size;
    const entries = [...selected];
    $('livePlaylist').innerHTML = entries.length ? entries.map(([mid, label], index) => `<li><span>${escapeHtml(label)}</span><div class="live-queue-tools"><button type="button" data-move-mid="${mid}" data-direction="-1" aria-label="上移第 ${index + 1} 段" ${index === 0 ? 'disabled' : ''}>↑</button><button type="button" data-move-mid="${mid}" data-direction="1" aria-label="下移第 ${index + 1} 段" ${index === entries.length - 1 ? 'disabled' : ''}>↓</button></div><button type="button" data-remove-mid="${mid}" aria-label="移除第 ${index + 1} 段">✕</button></li>`).join('') : empty('待播清单还是空的', '在录像库勾选片段，它们就会出现在这里。', 'li');
    resultInputs().forEach(c => c.checked = selected.has(Number(c.dataset.mid)));
    sheet.querySelectorAll('.live-day').forEach(day => {
      const head = day.querySelector('.live-day-head input');
      const rows = [...day.querySelectorAll('.live-result input:not(:disabled)')];
      const count = rows.filter(c => c.checked).length;
      head.disabled = !rows.length;
      head.checked = rows.length > 0 && count === rows.length;
      head.indeterminate = count > 0 && count < rows.length;
    });
    syncSessionChecks();
    renderStatus();
  }
  function selectInput(input, checked) {
    const mid = Number(input.dataset.mid);
    if (checked) selected.set(mid, input.dataset.label); else selected.delete(mid);
  }
  function renderStatus() {
    const running = server?.status === 'STREAMING';
    $('liveFabDot').hidden = !running;
    $('liveMonitor').dataset.status = !server ? 'unknown' : server.error ? 'error' : running ? 'streaming' : 'idle';
    $('liveStatus').textContent = !server ? '状态未连接' : running ? '正在推流' : server.error ? '推流异常' : '准备就绪';
    $('liveCurrent').textContent = !server ? '暂时无法确认服务状态，正在自动重试。' : running ? `${server.channel} · ${server.current || '正在连接…'}${server.error ? ' · ' + server.error : ''}` : server.error || (selected.size ? `已选 ${selected.size} 段，按清单顺序播放。` : '添加录像并填写目标频道后即可开播。');
    $('liveProgressText').textContent = running ? `${server.index} / ${server.total}` : '';
    $('liveProgress').hidden = !running;
    $('liveProgress').max = Math.max(1, server?.total || 0);
    $('liveProgress').value = server?.index || 0;
    $('liveStartBtn').hidden = running;
    $('liveStartBtn').disabled = busy || !server || !selected.size || !$('liveChannel').value.trim();
    $('liveStartBtn').textContent = busy ? '正在提交…' : '开始推流';
    // After a connection loss, stopping is still available as a safe recovery action.
    $('liveStopBtn').hidden = !!server && !running;
    $('liveStopBtn').disabled = busy;
    $('liveStopBtn').textContent = busy ? '正在提交…' : '停止推流';
    $('liveChannel').disabled = busy || running;
    sheet.querySelector('.live-close-hint').textContent = running ? '清单修改仅用于下次开播 · 关闭不会中断推流' : '关闭工作台不会中断推流';
  }
  async function refreshStatus() {
    if (polling || busy) return;
    polling = true;
    const generation = statusGeneration;
    try {
      const result = await api('/api/live/status');
      if (generation === statusGeneration) server = result;
    } catch { if (generation === statusGeneration) server = null; }
    finally { polling = false; renderStatus(); }
  }
  async function command(path, body) {
    const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, ...(body ? {body: JSON.stringify(body)} : {})});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '请求失败，请稍后重试');
    return result;
  }
  async function startLive() {
    if (busy || !server || server.status === 'STREAMING') return;
    const channel = $('liveChannel').value.trim();
    if (!channel || !selected.size) return feedback('请先添加录像并填写目标频道。', true);
    busy = true;
    statusGeneration++;
    feedback('');
    renderStatus();
    try {
      try { localStorage.setItem(CHANNEL_KEY, channel); } catch { /* optional */ }
      const message_ids = [...selected.keys()];
      const result = await command('/api/live/start', {channel, message_ids});
      server = {status: 'STREAMING', channel, index: 0, total: message_ids.length, current: '', error: ''};
      feedback(`已提交 ${result.count ?? message_ids.length} 段录像，正在连接目标频道。`);
    } catch (e) { feedback(`开播失败：${e.message}`, true); }
    finally { busy = false; await refreshStatus(); renderStatus(); }
  }
  async function stopLive() {
    if (busy) return;
    busy = true;
    statusGeneration++;
    feedback('');
    renderStatus();
    try {
      await command('/api/live/stop');
      server = {status: 'IDLE', error: ''};
      feedback('推流已停止，待播草稿已保留。');
    }
    catch (e) { feedback(`停止失败：${e.message}`, true); }
    finally { busy = false; await refreshStatus(); renderStatus(); }
  }
  function injectCheckboxes() {
    document.querySelectorAll('.session-item').forEach(item => {
      if (item.parentElement.classList.contains('session-selection')) return;
      const index = Number(item.dataset.sessionIndex), parts = sessionParts(index);
      if (!parts.length) return;
      // Keep the checkbox outside the playback button: no nested interactive controls.
      const wrapper = document.createElement('div');
      wrapper.className = 'session-selection';
      item.before(wrapper);
      wrapper.append(item);
      const label = document.createElement('label');
      label.className = 'live-check-label';
      label.innerHTML = `<input type="checkbox" class="live-check" data-session="${index}">加入待播 · ${parts.length} 段`;
      wrapper.append(label);
      label.querySelector('input').addEventListener('change', e => {
        sessionParts(index).forEach(p => { if (e.target.checked) selected.set(p.mid, p.label); else selected.delete(p.mid); });
        renderPlaylist();
      });
    });
    syncSessionChecks();
  }
  $('liveFab').addEventListener('click', openSheet);
  $('liveFilterForm').addEventListener('submit', e => { e.preventDefault(); search(); });
  sheet.addEventListener('input', e => {
    if (e.target.matches('input')) e.target.setCustomValidity('');
    if (e.target.id === 'liveFrom') $('liveTo').setCustomValidity('');
    if (e.target.id === 'liveChannel') renderStatus();
  });
  sheet.addEventListener('click', e => {
    const button = e.target.closest('button');
    if (!button || button.disabled) return;
    const id = button.id;
    if (id === 'liveSheetClose') closeSheet();
    if (button.dataset.liveView) setView(button.dataset.liveView);
    if (id === 'liveRetryStreamers') fillStreamers();
    if (id === 'liveStartBtn') startLive();
    if (id === 'liveStopBtn') stopLive();
    if (id === 'liveStreamersAll' || id === 'liveStreamersNone') {
      sheet.querySelectorAll('#liveStreamerBox input').forEach(c => c.checked = id === 'liveStreamersAll');
      streamerSummary();
    }
    if (['liveResultsAll', 'liveResultsNone', 'liveResultsInvert'].includes(id)) {
      resultInputs().forEach(c => selectInput(c, id === 'liveResultsAll' || (id === 'liveResultsInvert' && !c.checked)));
      renderPlaylist();
    }
    if (id === 'liveClear') {
      if (!window.confirm(`清空待播的 ${selected.size} 段录像？正在运行的推流不会受影响。`)) return;
      selected.clear(); renderPlaylist();
    }
    if (button.dataset.removeMid) {
      const next = button.closest('li').nextElementSibling?.querySelector('[data-remove-mid]');
      const nextId = next?.dataset.removeMid;
      selected.delete(Number(button.dataset.removeMid)); renderPlaylist();
      (sheet.querySelector(`[data-remove-mid="${nextId}"]`) || $('liveQueueTitle')).focus();
    }
    if (button.dataset.moveMid) {
      const entries = [...selected], mid = Number(button.dataset.moveMid);
      const index = entries.findIndex(([id]) => id === mid), target = index + Number(button.dataset.direction);
      if (target < 0 || target >= entries.length) return;
      [entries[index], entries[target]] = [entries[target], entries[index]];
      selected.clear(); entries.forEach(([id, label]) => selected.set(id, label));
      renderPlaylist();
      const controls = [...sheet.querySelectorAll(`[data-move-mid="${mid}"]`)];
      (controls.find(c => c.dataset.direction === button.dataset.direction && !c.disabled) || controls.find(c => !c.disabled))?.focus();
    }
  });
  sheet.addEventListener('change', e => {
    if (e.target.matches('#liveStreamerBox input')) streamerSummary();
    if (e.target.matches('.live-result input')) { selectInput(e.target, e.target.checked); renderPlaylist(); }
    if (e.target.matches('.live-day-head input')) {
      e.target.closest('.live-day').querySelectorAll('.live-result input:not(:disabled)').forEach(c => selectInput(c, e.target.checked));
      renderPlaylist();
    }
    if (e.target.id === 'liveChannel') {
      try { localStorage.setItem(CHANNEL_KEY, e.target.value.trim()); } catch { /* optional */ }
    }
  });
  $('liveQueueTitle').tabIndex = -1;
  const observer = new MutationObserver(injectCheckboxes);
  observer.observe(document.getElementById('app'), {childList: true, subtree: true});
  injectCheckboxes();
  renderPlaylist(false);
  refreshStatus();
  setInterval(() => { if (!document.hidden) refreshStatus(); }, 3000);
  window.__live = {selected, openSheet, startLive, stopLive};
}
