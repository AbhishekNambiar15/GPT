/* Shared persistent player state and controls across MiniGPT's pages. */
(function () {
  function getState() {
    try { return JSON.parse(localStorage.getItem('mg_playerstate') || 'null'); }
    catch (e) { return null; }
  }
  function setState(s) { localStorage.setItem('mg_playerstate', JSON.stringify(s)); }
  function addActivity(icon, text) {
    const arr = JSON.parse(localStorage.getItem('mg_activity') || '[]');
    arr.unshift({ icon, text, time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) });
    localStorage.setItem('mg_activity', JSON.stringify(arr.slice(0, 20)));
  }
  function fmt(t) {
    if (!isFinite(t)) return '0:00';
    const m = Math.floor(t / 60), s = Math.floor(t % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
  }

  let els = null;
  const inShell = window.parent !== window;
  const isMusicOwner = window.location.pathname.endsWith('/music.html');
  function sendToParent(message) {
    if (inShell) window.parent.postMessage(message, window.location.origin);
  }
  function sendCommand(command) {
    sendToParent({ type: 'player-command', command });
  }
  function notifyParent() {
    if (!inShell || !isMusicOwner) return;
    const state = getState();
    if (state) sendToParent({ type: 'player-state', state });
  }

  function ensureSideDock() {
    if (document.getElementById('miniRightDock')) return;
    const dock = document.createElement('aside');
    dock.id = 'miniRightDock';
    dock.className = 'mini-right-dock collapsed';
    dock.innerHTML = `
      <div class="mini-right-header">
        <span>Music</span>
        <button class="mini-right-close" type="button" aria-label="Close music panel">×</button>
      </div>
      <div class="mini-right-card">
        <div class="mini-right-art" id="miniSideArt"></div>
        <div class="mini-right-title" id="miniSideTitle">Nothing playing</div>
        <div class="mini-right-artist" id="miniSideArtist">Choose a song</div>
        <div class="mini-right-controls">
          <button id="miniSidePrev" type="button">⏮</button>
          <button id="miniSidePlay" type="button">▶</button>
          <button id="miniSideNext" type="button">⏭</button>
        </div>
        <div class="mini-right-row">
          <span id="miniSideCur">0:00</span>
          <input id="miniSideSeek" type="range" min="0" max="100" value="0" disabled>
          <span id="miniSideDur">0:00</span>
        </div>
        <button id="miniSideAuto" class="mini-right-auto" type="button">🔁 Auto Play: Off</button>
      </div>
    `;
    document.body.appendChild(dock);

    const closeBtn = dock.querySelector('.mini-right-close');
    const toggleBtn = document.createElement('button');
    toggleBtn.id = 'miniRightDockToggle';
    toggleBtn.type = 'button';
    toggleBtn.textContent = '🎵';
    toggleBtn.setAttribute('aria-label', 'Open music panel');
    toggleBtn.setAttribute('aria-expanded', 'false');
    document.body.appendChild(toggleBtn);

    closeBtn.onclick = () => {
      dock.classList.add('collapsed');
      toggleBtn.setAttribute('aria-expanded', 'false');
    };
    toggleBtn.onclick = () => {
      dock.classList.toggle('collapsed');
      toggleBtn.setAttribute('aria-expanded', String(!dock.classList.contains('collapsed')));
    };

    const sidePlay = document.getElementById('miniSidePlay');
    if (sidePlay) {
      sidePlay.onclick = () => {
        if (isMusicOwner) handleCommand({ type: 'toggle' });
        else sendCommand({ type: 'toggle' });
      };
    }
    document.getElementById('miniSidePrev').onclick = () => playQueued(-1);
    document.getElementById('miniSideNext').onclick = () => playQueued(1);
    document.getElementById('miniSideAuto').onclick = () => toggleAutoPlay();
  }

  function updateSideDock(state) {
    const dock = document.getElementById('miniRightDock');
    if (!dock) return;
    const title = document.getElementById('miniSideTitle');
    const artist = document.getElementById('miniSideArtist');
    const art = document.getElementById('miniSideArt');
    const play = document.getElementById('miniSidePlay');
    const seek = document.getElementById('miniSideSeek');
    const cur = document.getElementById('miniSideCur');
    const dur = document.getElementById('miniSideDur');
    const auto = document.getElementById('miniSideAuto');
    if (!state || !state.preview) {
      if (title) title.textContent = 'Nothing playing';
      if (artist) artist.textContent = 'Choose a song';
      if (play) play.textContent = '▶';
      if (seek) { seek.value = 0; seek.disabled = true; }
      if (cur) cur.textContent = '0:00';
      if (dur) dur.textContent = '0:00';
      if (auto) auto.textContent = '🔁 Auto Play: Off';
      return;
    }
    if (title) title.textContent = state.title || 'Nothing playing';
    if (artist) artist.textContent = state.artist || 'Internet Archive';
    if (art) art.style.background = state.artwork ? `url(${state.artwork}) center/cover` : 'linear-gradient(135deg, #4c3a7a, #1e3a5f)';
    if (play) play.textContent = state.playing ? '❚❚' : '▶';
    if (seek) { seek.value = state.duration ? ((state.time || 0) / state.duration) * 100 : 0; seek.disabled = false; }
    if (cur) cur.textContent = fmt(state.time || 0);
    if (dur) dur.textContent = fmt(state.duration || 0);
    if (auto) auto.textContent = state.autoPlay ? '🔁 Auto Play: On' : '🔁 Auto Play: Off';
  }

  function bindEls() {
    els = {
      audio: document.getElementById('miniAudio'),
      bar: document.getElementById('miniPlayerBar'),
      playBtn: document.getElementById('miniPlayBtn') || document.getElementById('miniSidePlay'),
      seek: document.getElementById('miniSeek') || document.getElementById('miniSideSeek'),
      title: document.getElementById('miniTitle'),
      artist: document.getElementById('miniArtist'),
      art: document.getElementById('miniArt'),
      cur: document.getElementById('miniCur') || document.getElementById('miniSideCur'),
      dur: document.getElementById('miniDur') || document.getElementById('miniSideDur')
    };
    return true;
  }

  function paintFromState(s) {
    if (!s || !s.preview) return;
    if (els && els.bar) {
      els.bar.classList.remove('empty');
      els.title.textContent = s.title;
      els.artist.textContent = s.artist;
      if (s.artwork) els.art.style.background = `url(${s.artwork}) center/cover`;
      els.playBtn.disabled = false;
      els.seek.disabled = false;
      els.cur.textContent = fmt(s.time || 0);
      els.dur.textContent = fmt(s.duration || 0);
      els.seek.value = s.duration ? ((s.time || 0) / s.duration) * 100 : 0;
    }
    updateSideDock(s);
    const autoPlayButton = document.getElementById('autoPlayTrack');
    if (autoPlayButton) autoPlayButton.textContent = s.autoPlay ? '🔁 Auto Play: On' : '🔁 Auto Play: Off';
  }

  function playTrack(track) {
    if (!els) return;
    if (inShell && !isMusicOwner) {
      sendCommand({ type: 'play-track', track });
      return;
    }
    const previous = getState() || {};
    const queue = previous.queue || [];
    const queueIndex = queue.findIndex(item => item.preview === track.preview);
    const s = {
      title: track.title,
      artist: track.artist,
      artwork: track.artwork,
      preview: track.preview,
      time: 0,
      playing: true,
      queue,
      queueIndex: queueIndex < 0 ? 0 : queueIndex,
      autoPlay: Boolean(previous.autoPlay)
    };
    setState(s);
    paintFromState(s);
    els.audio.src = track.preview;
    els.audio.currentTime = 0;
    els.audio.play().then(() => {
      els.playBtn.textContent = '❚❚';
      addActivity('🎵', `Played "${track.title}" by ${track.artist}`);
      notifyParent();
    }).catch(() => {
      els.playBtn.textContent = '▶';
    });
  }

  function setQueue(tracks) {
    const state = getState() || {};
    state.queue = tracks;
    state.queueIndex = tracks.findIndex(item => item.preview === state.preview);
    if (state.queueIndex < 0) state.queueIndex = 0;
    setState(state);
    notifyParent();
  }

  function playQueued(offset) {
    if (!isMusicOwner) {
      sendCommand({ type: offset < 0 ? 'previous' : 'next' });
      return;
    }
    const state = getState() || {};
    const queue = state.queue || [];
    if (!queue.length) return;
    const currentIndex = Number.isInteger(state.queueIndex) && state.queueIndex >= 0
      ? state.queueIndex
      : Math.max(0, queue.findIndex(item => item.preview === state.preview));
    const nextIndex = (currentIndex + offset + queue.length) % queue.length;
    const nextTrack = queue[nextIndex];
    state.queueIndex = nextIndex;
    setState(state);
    playTrack(nextTrack);
  }

  function toggleAutoPlay() {
    if (!isMusicOwner) {
      sendCommand({ type: 'auto-play' });
      return;
    }
    const state = getState() || {};
    state.autoPlay = !state.autoPlay;
    setState(state);
    notifyParent();
  }

  function handleCommand(command) {
    if (!isMusicOwner || !command) return;
    if (command.type === 'play-track') {
      playTrack(command.track);
      return;
    }
    if (command.type === 'previous') {
      playQueued(-1);
      return;
    }
    if (command.type === 'next') {
      playQueued(1);
      return;
    }
    if (command.type === 'auto-play') {
      toggleAutoPlay();
      return;
    }
    if (command.type === 'seek') {
      if (els.audio.duration) els.audio.currentTime = command.time;
      return;
    }
    if (command.type === 'toggle') {
      if (!els.audio.src) return;
      const state = getState() || {};
      if (els.audio.paused) {
        els.audio.play().then(() => {
          state.playing = true;
          setState(state);
          els.playBtn.textContent = '❚❚';
          notifyParent();
        }).catch(() => { });
      } else {
        els.audio.pause();
        state.playing = false;
        setState(state);
        els.playBtn.textContent = '▶';
        notifyParent();
      }
    }
  }

  function init() {
    ensureSideDock();
    bindEls();

    if (inShell) {
      window.addEventListener('message', event => {
        if (event.origin !== window.location.origin || !event.data) return;
        if (event.data.type === 'player-command') handleCommand(event.data.command);
        if (event.data.type === 'player-state' && !isMusicOwner) {
          const state = event.data.state;
          setState(state);
          paintFromState(state);
        }
      });
    }

    if (window.parent !== window) {
      document.addEventListener('click', event => {
        const link = event.target.closest('a[href]');
        if (!link || link.origin !== window.location.origin) return;
        const page = link.pathname.split('/').pop() || 'index.html';
        if (!/^(index|memory|modes|tools|settings|welcome|music)\.html$/.test(page)) return;
        event.preventDefault();
        window.parent.postMessage({ type: 'navigate', page }, window.location.origin);
      });
    }

    const state = getState();
    if (isMusicOwner && state && state.preview) {
      paintFromState(state);
      els.audio.src = state.preview;
      const resume = () => {
        els.audio.currentTime = state.time || 0;
        if (state.playing) {
          // Browsers block autoplay-with-sound after a fresh page load, but
          // starting muted is always allowed, and unmuting right after it
          // has begun playing is not blocked — this makes navigation feel gapless.
          els.audio.muted = true;
          els.audio.play().then(() => {
            els.audio.muted = false;
            els.playBtn.textContent = '❚❚';
          }).catch(() => {
            // Still blocked (rare) — resume on the very next interaction anywhere on the page.
            els.playBtn.textContent = '▶';
            const resumeOnInteract = () => {
              els.audio.muted = false;
              els.audio.play().then(() => { els.playBtn.textContent = '❚❚'; }).catch(() => { });
            };
            document.addEventListener('pointerdown', resumeOnInteract, { once: true });
            document.addEventListener('keydown', resumeOnInteract, { once: true });
          });
        } else {
          els.playBtn.textContent = '▶';
        }
      };
      if (els.audio.readyState >= 1) resume();
      else els.audio.addEventListener('loadedmetadata', resume, { once: true });
    } else if (!isMusicOwner && state && state.preview) {
      paintFromState(state);
    }

    if (els.playBtn) els.playBtn.onclick = () => {
      if (!isMusicOwner) {
        sendCommand({ type: 'toggle' });
        return;
      }
      if (!els.audio.src) return;
      if (els.audio.paused) {
        els.audio.play().then(() => {
          els.playBtn.textContent = '❚❚';
          const s = getState() || {}; s.playing = true; setState(s); notifyParent();
        }).catch(() => { });
      } else {
        els.audio.pause();
        els.playBtn.textContent = '▶';
        const s = getState() || {}; s.playing = false; setState(s); notifyParent();
      }
    };

    if (els.seek) els.seek.addEventListener('input', () => {
      if (!isMusicOwner) {
        const state = getState();
        if (state && state.duration) sendCommand({ type: 'seek', time: (els.seek.value / 100) * state.duration });
        return;
      }
      if (!els.audio.duration) return;
      els.audio.currentTime = (els.seek.value / 100) * els.audio.duration;
    });

    if (!els.audio) return;
    els.audio.addEventListener('timeupdate', () => {
      if (!isMusicOwner) return;
      if (!els.audio.duration) return;
      els.seek.value = (els.audio.currentTime / els.audio.duration) * 100;
      els.cur.textContent = fmt(els.audio.currentTime);
      els.dur.textContent = fmt(els.audio.duration);
      const s = getState();
      if (s) { s.time = els.audio.currentTime; s.duration = els.audio.duration; setState(s); notifyParent(); updateSideDock(s); }
    });

    els.audio.addEventListener('ended', () => {
      if (!isMusicOwner) return;
      const state = getState();
      if (state && state.autoPlay && state.queue && state.queue.length) {
        playQueued(1);
      } else {
        els.playBtn.textContent = '▶';
        if (state) { state.playing = false; setState(state); notifyParent(); }
      }
    });

    window.addEventListener('beforeunload', () => {
      if (!isMusicOwner) return;
      const s = getState();
      if (s && els.audio.src) { s.time = els.audio.currentTime; s.playing = !els.audio.paused; setState(s); notifyParent(); }
    });
  }

  const sideDockCss = `
    .mini-right-dock {
      position: fixed;
      top: 18px;
      right: 18px;
      width: 270px;
      background: rgba(17, 21, 31, 0.97);
      border: 1px solid #232a3a;
      border-radius: 18px;
      box-shadow: 0 14px 40px rgba(0,0,0,0.35);
      z-index: 999;
      transition: transform 0.25s ease;
      padding: 12px;
      backdrop-filter: blur(8px);
    }
    .mini-right-dock.collapsed {
      transform: translateX(calc(100% + 26px));
    }
    .mini-right-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      color: #eef0f5;
      font-weight: 700;
      margin-bottom: 10px;
      padding: 2px 4px 8px;
      border-bottom: 1px solid #232a3a;
    }
    .mini-right-close {
      border: none;
      background: transparent;
      color: #8993a8;
      font-size: 24px;
      line-height: 1;
      cursor: pointer;
    }
    .mini-right-card {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .mini-right-art {
      width: 100%;
      height: 168px;
      border-radius: 14px;
      background: linear-gradient(135deg, #4c3a7a, #1e3a5f);
      background-size: cover;
      background-position: center;
    }
    .mini-right-title {
      font-weight: 700;
      font-size: 15px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .mini-right-artist {
      font-size: 12px;
      color: #8993a8;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .mini-right-controls {
      display: flex;
      justify-content: center;
      gap: 12px;
      margin-top: 4px;
    }
    .mini-right-controls button,
    .mini-right-auto {
      border: 1px solid #232a3a;
      background: #161b28;
      color: #eef0f5;
      border-radius: 10px;
      padding: 8px 12px;
      cursor: pointer;
    }
    .mini-right-controls button:first-child,
    .mini-right-controls button:last-child {
      width: 42px;
      height: 42px;
      padding: 0;
      font-size: 16px;
    }
    .mini-right-controls button:nth-child(2) {
      width: 52px;
      height: 42px;
      padding: 0;
      font-size: 17px;
      background: #6366f1;
      border-color: #6366f1;
    }
    .mini-right-row {
      display: flex;
      align-items: center;
      gap: 8px;
      color: #8993a8;
      font-size: 11px;
    }
    .mini-right-row input[type=range] {
      flex: 1;
      -webkit-appearance: none;
      height: 4px;
      border-radius: 2px;
      background: #232a3a;
      outline: none;
    }
    .mini-right-row input[type=range]::-webkit-slider-thumb {
      -webkit-appearance: none;
      width: 12px;
      height: 12px;
      border-radius: 50%;
      background: #6366f1;
      cursor: pointer;
    }
    .mini-right-auto {
      width: 100%;
      margin-top: 6px;
      font-size: 12px;
    }
    #miniRightDockToggle {
      all: unset;
      position: fixed;
      right: 16px;
      bottom: 80px;
      width: 46px;
      height: 46px;
      box-sizing: border-box;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 0;
      border-radius: 50%;
      border: 1px solid #232a3a;
      background: rgba(17, 21, 31, 0.95);
      color: #eef0f5;
      font-size: 20px;
      line-height: 1;
      text-align: center;
      box-shadow: 0 8px 24px rgba(0,0,0,0.35);
      cursor: pointer;
      z-index: 998;
      display: block;
    }
    .mini-right-dock.collapsed + #miniRightDockToggle {
      display: block;
    }
    .mini-right-dock:not(.collapsed) + #miniRightDockToggle {
      display: none;
    }
    @media (max-width: 900px) {
      .mini-right-dock {
        right: 10px;
        width: 220px;
      }
    }
    @media (max-width: 600px) {
      .mini-right-dock {
        top: auto;
        right: 12px;
        bottom: 12px;
        width: min(270px, calc(100vw - 24px));
        max-height: calc(100vh - 24px);
        overflow-y: auto;
      }
      #miniRightDockToggle {
        right: 12px;
        bottom: 76px;
      }
    }
  `;
  const style = document.createElement('style');
  style.textContent = sideDockCss;
  document.head.appendChild(style);

  document.addEventListener('DOMContentLoaded', init);
  window.MiniGPTPlayer = {
    playTrack,
    setQueue,
    previous: () => playQueued(-1),
    next: () => playQueued(1),
    toggleAutoPlay,
    getState,
    setState,
    addActivity
  };
})();
