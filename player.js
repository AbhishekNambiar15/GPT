/* Shared persistent player state and controls across MiniGPT's pages.

   Global Music State (persisted in localStorage under 'mg_playerstate'):
     - current track fields: title, artist, artwork, provider ('youtube'|'archive'),
       videoId / preview, time, duration, playing
     - history:        array of every track that has actually been played, in order
     - historyIndex:   pointer into history for the track currently playing/loaded
     - upNext:         manual "Play Next / Add to Queue" list (separate from history)
     - autoPlay:       whether autoplay-recommendations are enabled

   Search results (from Internet Archive or YouTube search) are NEVER written into
   this state as a playback sequence. They only feed three actions: "play this now"
   (starts a new branch of history), "add to queue" and "play next" (both go to
   upNext). Next/Previous and natural song-end all resolve through one controller
   (see `advance()` / `previousTrack()` below) so they can never fall back to
   replaying old search results. */
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
  function hasPlayableTrack(state) {
    return Boolean(state && (state.preview || (state.provider === 'youtube' && state.videoId)));
  }
  function normalizeTitle(title) {
    if (!title) return '';
    let t = title.toLowerCase();
    t = t.replace(/\([^)]*\)/g, ' ').replace(/\[[^\]]*\]/g, ' ');
    t = t.replace(/\b(official\s*(music\s*)?video|official\s*audio|official|lyrics?|lyric\s*video|audio|video|hd|hq|4k|remaster(ed)?|live|explicit|clean|visualizer|full\s*version|mv)\b/g, ' ');
    t = t.replace(/\bft\.?\b|\bfeat\.?\b/g, ' ');
    t = t.replace(/[^a-z0-9\s]/g, ' ');
    t = t.replace(/\s+/g, ' ').trim();
    return t;
  }
  const NON_MUSIC_REGEX = /\b(podcast|full\s*podcast|interview|full\s*interview|episode\s*\d+|ep\s*\.?\s*\d+|full\s*episode|reacts?|reacting|reaction\s*video|album\s*review|song\s*review|track\s*review|music\s*review|documentary|docuseries|behind\s*the\s*scenes|making\s*of|speaks\s*on|talks\s*about|q&a|vlog|daily\s*vlog|livestream|live\s*stream|stream\s*highlight|tutorial|how\s*to\s*play|guitar\s*lesson|piano\s*lesson|drum\s*lesson|unboxing|parody|audiobook)\b/i;

  function isNonMusicTrack(title) {
    if (!title) return false;
    return NON_MUSIC_REGEX.test(title);
  }
  function emitQueueChanged() {
    document.dispatchEvent(new CustomEvent('mg-queue-updated'));
  }
  /* A track as stored in upNext / history: provider-agnostic shape. */
  function normalizeQueueTrack(track) {
    if (track.provider === 'youtube' || track.videoId) {
      return {
        videoId: track.videoId,
        title: track.title || 'Untitled',
        artist: track.channelTitle || track.artist || 'YouTube',
        artwork: track.thumbnail || track.artwork || '',
        provider: 'youtube',
        duration: Number(track.duration) || 0
      };
    }
    return {
      preview: track.preview || '',
      title: track.title || 'Untitled',
      artist: track.artist || 'Internet Archive',
      artwork: track.artwork || '',
      provider: 'archive',
      duration: Number(track.duration) || 0
    };
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
    document.getElementById('miniSidePrev').onclick = () => previousTrack();
    document.getElementById('miniSideNext').onclick = () => nextTrack();
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
    if (!hasPlayableTrack(state)) {
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
    if (seek) {
      seek.value = state.duration ? ((state.time || 0) / state.duration) * 100 : 0;
      seek.disabled = !(state.duration > 0);
    }
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
    if (!hasPlayableTrack(s)) return;
    if (els && els.bar) {
      els.bar.classList.remove('empty');
      els.title.textContent = s.title || 'Now playing';
      els.artist.textContent = s.artist || 'YouTube';
      if (s.artwork) els.art.style.background = `url(${s.artwork}) center/cover`;
      els.playBtn.disabled = false;
      els.seek.disabled = !(s.duration > 0);
      els.cur.textContent = fmt(s.time || 0);
      els.dur.textContent = fmt(s.duration || 0);
      els.seek.value = s.duration ? ((s.time || 0) / s.duration) * 100 : 0;
    }
    updateSideDock(s);
    const autoPlayButton = document.getElementById('autoPlayTrack');
    if (autoPlayButton) autoPlayButton.textContent = s.autoPlay ? '🔁 Auto Play: On' : '🔁 Auto Play: Off';
  }

  /* ---- Low-level "make this the current track" mechanics ----
     These never touch history/queue themselves; callers decide whether a
     track being loaded is a brand-new branch (push to history) or a move
     through existing history (Previous/Next, no push). */
  function startArchivePlayback(track) {
    if (!els || !els.audio) return;
    const previous = getState() || {};
    if (previous.provider === 'youtube' && window.MiniGPTYouTubePlayer) {
      window.MiniGPTYouTubePlayer.stopVideo();
    }
    if (els.audio.src) els.audio.pause();
    const s = {
      ...previous,
      title: track.title || 'Untitled',
      artist: track.artist || 'Internet Archive',
      artwork: track.artwork || '',
      preview: track.preview,
      provider: 'archive',
      videoId: '',
      time: 0,
      duration: Number(track.duration) || 0,
      playing: true,
      autoPlay: Boolean(previous.autoPlay),
      upNext: previous.upNext || [],
      history: previous.history || [],
      historyIndex: Number.isInteger(previous.historyIndex) ? previous.historyIndex : -1,
      sessionPlayedIds: previous.sessionPlayedIds || []
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

  function setYouTubeTrack(track) {
    if (!track || !track.videoId) return;
    const previous = getState() || {};
    if (els && els.audio && !els.audio.paused) els.audio.pause();
    const state = {
      ...previous,
      title: track.title || 'Untitled video',
      artist: track.channelTitle || track.artist || 'YouTube',
      artwork: track.thumbnail || track.artwork || '',
      preview: '',
      provider: 'youtube',
      videoId: track.videoId,
      time: 0,
      duration: Number(track.duration) || 0,
      playing: true,
      autoPlay: Boolean(previous.autoPlay),
      upNext: previous.upNext || [],
      history: previous.history || [],
      historyIndex: Number.isInteger(previous.historyIndex) ? previous.historyIndex : -1,
      sessionPlayedIds: previous.sessionPlayedIds || []
    };
    setState(state);
    paintFromState(state);
    addActivity('🎵', `Played "${state.title}" on YouTube`);
    notifyParent();
  }

  function physicallyPlay(track) {
    if (track.provider === 'youtube' && track.videoId) {
      setYouTubeTrack(track);
      if (window.MiniGPTYouTubePlayer) window.MiniGPTYouTubePlayer.loadVideoById(track.videoId);
    } else {
      startArchivePlayback(track);
    }
  }

  function updateYouTubePlayback(progress) {
    const state = getState();
    if (!state || state.provider !== 'youtube') return;
    if (Number.isFinite(progress.time)) state.time = progress.time;
    if (Number.isFinite(progress.duration)) state.duration = progress.duration;
    if (typeof progress.playing === 'boolean') state.playing = progress.playing;
    recordCurrentDuration(state);
    setState(state);
    paintFromState(state);
    notifyParent();
  }

  /* Once a track's real duration is known, stamp it onto its history entry
     too so features like Recently Played can show track length accurately. */
  function recordCurrentDuration(state) {
    if (!state || !state.duration) return;
    const history = state.history || [];
    const idx = state.historyIndex;
    const entry = Number.isInteger(idx) && idx >= 0 && idx < history.length ? history[idx] : null;
    if (entry && (!entry.duration || Math.abs(entry.duration - state.duration) > 1)) {
      entry.duration = state.duration;
      if (!entry.artwork && state.artwork) entry.artwork = state.artwork;
      if ((!entry.artist || entry.artist === 'YouTube') && state.artist) entry.artist = state.artist;
      setState(state);
      emitQueueChanged();
    }
  }

  /* ---- Playback history: strictly the latest 4 songs played (FIFO queue) ----
     Only genuine new plays (clicks from search results, queue items, or autoplay
     recommendations) push to history. Moving with Previous/Next only shifts the
     historyIndex pointer. When a 5th song is added, the oldest is discarded. */
  const MAX_HISTORY = 4;
  function pushHistory(track) {
    const state = getState() || {};
    const entry = normalizeQueueTrack(track);
    entry.normTitle = normalizeTitle(entry.title);
    if (!entry.duration && state.duration && (state.videoId === entry.videoId || state.preview === entry.preview)) {
      entry.duration = state.duration;
    }
    let history = state.history || [];

    const lastEntry = history.length ? history[history.length - 1] : null;
    const isSameTrack = lastEntry && (
      (entry.provider === 'youtube' && entry.videoId && lastEntry.videoId === entry.videoId) ||
      (entry.provider === 'archive' && entry.preview && lastEntry.preview === entry.preview)
    );

    if (isSameTrack) {
      if (!lastEntry.duration && entry.duration) lastEntry.duration = entry.duration;
      if (!lastEntry.artwork && entry.artwork) lastEntry.artwork = entry.artwork;
      state.historyIndex = history.length - 1;
    } else {
      history.push(entry);
      // FIFO queue behavior: keep only latest 4 songs, discard oldest from front
      while (history.length > MAX_HISTORY) {
        history.shift();
      }
      state.historyIndex = history.length - 1;
    }

    state.history = history;
    setState(state);
    emitQueueChanged();
  }

  /* "Play this as a brand-new song": records in 4-song history queue AND physically plays it. */
  function playNewTrack(track) {
    pushHistory(track);
    physicallyPlay(track);
    emitQueueChanged();
  }

  /* ---- Public: play a track chosen directly from search results ---- */
  function playTrack(track) {
    if (!isMusicOwner) { sendCommand({ type: 'play-track', track }); return; }
    playNewTrack(track);
  }
  function playYouTubeTrack(track) {
    if (!isMusicOwner) { sendCommand({ type: 'youtube-track', track }); return; }
    playNewTrack({ ...track, provider: 'youtube' });
  }

  /* ---- Public: Previous / Next, backed by the 4-song history stack ---- */
  function previousTrack() {
    if (!isMusicOwner) { sendCommand({ type: 'previous' }); return; }
    const state = getState() || {};
    const history = state.history || [];
    let idx = Number.isInteger(state.historyIndex) ? state.historyIndex : history.length - 1;
    if (idx <= 0 || idx > history.length - 1) return;
    const targetIdx = idx - 1;
    const track = history[targetIdx];
    state.historyIndex = targetIdx;
    setState(state);
    physicallyPlay(track);
    emitQueueChanged();
  }

  /* Central playback controller used by BOTH the manual Next button and a
     song ending naturally:
       1. step forward through recent history (e.g. right after pressing Previous)
       2. otherwise, play whatever is at the front of the manual queue
       3. otherwise, if Autoplay is on, ask for a recommendation
       4. otherwise, stop */
  async function advance() {
    const state = getState() || {};
    const history = state.history || [];
    const idx = Number.isInteger(state.historyIndex) ? state.historyIndex : -1;
    if (idx >= 0 && idx < history.length - 1) {
      const targetIdx = idx + 1;
      const track = history[targetIdx];
      state.historyIndex = targetIdx;
      setState(state);
      physicallyPlay(track);
      emitQueueChanged();
      return;
    }
    if (state.upNext && state.upNext.length) {
      playQueueItem(0);
      return;
    }
    if (state.autoPlay) {
      await playAutoplayRecommendation();
      return;
    }
    const s2 = getState() || {};
    s2.playing = false;
    setState(s2);
    paintFromState(s2);
    notifyParent();
  }
  function nextTrack() {
    if (!isMusicOwner) { sendCommand({ type: 'next' }); return; }
    return advance();
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
    emitQueueChanged();
  }

  /* ---- Manual "Up Next" queue (separate from search results and history) ---- */
  function getUpNext() {
    const state = getState() || {};
    return state.upNext || [];
  }
  function addToQueue(track) {
    const state = getState() || {};
    state.upNext = state.upNext || [];
    state.upNext.push(normalizeQueueTrack(track));
    setState(state);
    addActivity('➕', `Added "${track.title}" to the queue`);
    emitQueueChanged();
  }
  function playNext(track) {
    const state = getState() || {};
    state.upNext = state.upNext || [];
    /* FIFO: "Play Next" songs go ahead of normal "+ Queue" songs, but stay
       in the order they were added among themselves (a plain unshift would
       reverse them, i.e. behave like a stack). */
    const entry = normalizeQueueTrack(track);
    entry.priority = true;
    let insertAt = 0;
    while (insertAt < state.upNext.length && state.upNext[insertAt].priority) insertAt++;
    state.upNext.splice(insertAt, 0, entry);
    setState(state);
    addActivity('⏭', `"${track.title}" will play next`);
    emitQueueChanged();
  }
  function removeFromQueue(index) {
    const state = getState() || {};
    if (!state.upNext) return;
    state.upNext.splice(index, 1);
    setState(state);
    emitQueueChanged();
  }
  function moveQueueItem(index, direction) {
    const state = getState() || {};
    const q = state.upNext || [];
    const newIndex = index + direction;
    if (newIndex < 0 || newIndex >= q.length) return;
    const [item] = q.splice(index, 1);
    q.splice(newIndex, 0, item);
    setState(state);
    emitQueueChanged();
  }
  function clearQueue() {
    const state = getState() || {};
    state.upNext = [];
    setState(state);
    addActivity('🗑', 'Cleared the queue');
    emitQueueChanged();
  }
  /* Playing a queued song removes it from upNext and plays it as a new
     history branch — the queue always wins over autoplay recommendations. */
  function playQueueItem(index) {
    if (!isMusicOwner) return;
    const state = getState() || {};
    const q = state.upNext || [];
    if (index < 0 || index >= q.length) return;
    const [track] = q.splice(index, 1);
    setState(state);
    emitQueueChanged();
    playNewTrack(track);
  }

  /* ---- What plays when Autoplay needs to pick something related ----
     Only meaningful for YouTube-sourced tracks (the backend's /related endpoint).
     Excludes everything played in this session so autoplay never repeats recent songs. */
  let recommendationInFlight = false;
  async function playAutoplayRecommendation() {
    if (recommendationInFlight) return;
    const state = getState() || {};
    if (state.provider !== 'youtube' || !state.videoId) {
      const s = getState() || {};
      s.playing = false;
      setState(s);
      paintFromState(s);
      notifyParent();
      return;
    }
    recommendationInFlight = true;
    document.dispatchEvent(new CustomEvent('mg-recommendation-loading'));
    try {
      let sessionPlayed = state.sessionPlayedIds || [];
      if (!sessionPlayed.includes(state.videoId)) {
        sessionPlayed = [...sessionPlayed, state.videoId];
      }
      const historyList = state.history || [];
      const upNextList = state.upNext || [];

      const allSeenIds = new Set([
        ...sessionPlayed,
        ...historyList.filter(r => r.provider === 'youtube' && r.videoId).map(r => r.videoId),
        ...upNextList.filter(r => r.provider === 'youtube' && r.videoId).map(r => r.videoId),
        state.videoId
      ]);
      const allSeenTitles = new Set([
        ...historyList.map(r => r.normTitle || normalizeTitle(r.title)).filter(Boolean),
        ...upNextList.map(r => r.normTitle || normalizeTitle(r.title)).filter(Boolean),
        normalizeTitle(state.title)
      ]);

      const excludeIdsArr = Array.from(allSeenIds).slice(-30);
      const excludeTitlesArr = Array.from(allSeenTitles).slice(-30);

      const params = new URLSearchParams({
        videoId: state.videoId,
        title: state.title || '',
        channelTitle: state.artist || '',
        excludeIds: excludeIdsArr.join(','),
        excludeTitles: excludeTitlesArr.join('|')
      });
      const response = await fetch(`/api/youtube/related?${params.toString()}`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.videos || !data.videos.length) {
        const s = getState() || {};
        s.playing = false;
        setState(s);
        paintFromState(s);
        notifyParent();
        document.dispatchEvent(new CustomEvent('mg-recommendation-empty', { detail: (data && data.error) || null }));
        return;
      }

      // Pick the first candidate that hasn't been played in this session
      let chosen = null;
      for (const vid of data.videos) {
        if (!vid || !vid.videoId) continue;
        if (isNonMusicTrack(vid.title)) continue;
        if (allSeenIds.has(vid.videoId)) continue;
        const norm = normalizeTitle(vid.title);
        if (norm && allSeenTitles.has(norm)) continue;
        chosen = vid;
        break;
      }
      if (!chosen) {
        chosen = data.videos.find(v => v && v.videoId && !allSeenIds.has(v.videoId) && !isNonMusicTrack(v.title));
      }
      if (!chosen) {
        chosen = data.videos.find(v => v && v.videoId && v.videoId !== state.videoId && !isNonMusicTrack(v.title));
      }
      if (!chosen) {
        chosen = data.videos.find(v => v && v.videoId && !allSeenIds.has(v.videoId)) || data.videos[0];
      }

      sessionPlayed.push(chosen.videoId);
      if (sessionPlayed.length > 50) sessionPlayed = sessionPlayed.slice(-50);
      const curState = getState() || {};
      curState.sessionPlayedIds = sessionPlayed;
      setState(curState);

      const next = { ...chosen, provider: 'youtube' };
      playNewTrack(next);
      addActivity('🔁', `Autoplay picked "${next.title}"`);
    } catch (e) {
      const s = getState() || {};
      s.playing = false;
      setState(s);
      paintFromState(s);
      notifyParent();
      document.dispatchEvent(new CustomEvent('mg-recommendation-empty', { detail: 'Autoplay could not reach the server.' }));
    } finally {
      recommendationInFlight = false;
    }
  }

  function handleCommand(command) {
    if (!isMusicOwner || !command) return;
    if (command.type === 'play-track') {
      playNewTrack(command.track);
      return;
    }
    if (command.type === 'youtube-track') {
      playNewTrack({ ...command.track, provider: 'youtube' });
      return;
    }
    if (command.type === 'previous') {
      previousTrack();
      return;
    }
    if (command.type === 'next') {
      advance();
      return;
    }
    if (command.type === 'auto-play') {
      toggleAutoPlay();
      return;
    }
    if (command.type === 'seek') {
      const state = getState();
      if (state && state.provider === 'youtube' && window.MiniGPTYouTubePlayer) {
        window.MiniGPTYouTubePlayer.seekTo(command.time);
        return;
      }
      if (els.audio.duration) els.audio.currentTime = command.time;
      return;
    }
    if (command.type === 'toggle') {
      const state = getState() || {};
      if (state.provider === 'youtube' && window.MiniGPTYouTubePlayer) {
        if (state.playing) window.MiniGPTYouTubePlayer.pauseVideo();
        else window.MiniGPTYouTubePlayer.playVideo();
        return;
      }
      if (!els.audio.src) return;
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
    if (isMusicOwner && state && state.provider === 'youtube' && state.videoId) {
      paintFromState(state);
    } else if (isMusicOwner && state && state.preview) {
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
    } else if (!isMusicOwner && hasPlayableTrack(state)) {
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
      const state = getState();
      if (state && state.provider === 'youtube' && window.MiniGPTYouTubePlayer && state.duration) {
        window.MiniGPTYouTubePlayer.seekTo((els.seek.value / 100) * state.duration);
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
      if (s) { s.time = els.audio.currentTime; s.duration = els.audio.duration; recordCurrentDuration(s); setState(s); notifyParent(); updateSideDock(s); }
    });

    els.audio.addEventListener('ended', () => {
      if (!isMusicOwner) return;
      // Natural end uses the exact same controller as the manual Next button.
      advance();
    });

    window.addEventListener('beforeunload', () => {
      if (!isMusicOwner) return;
      const s = getState();
      if (s && s.provider !== 'youtube' && els.audio.src) { s.time = els.audio.currentTime; s.playing = !els.audio.paused; setState(s); notifyParent(); }
    });
  }

  const sideDockCss = `
    /* Defensive: some pages define their own broad resets (e.g. a page-wide
       "button { all: unset }"), which can strip box-sizing from these
       injected elements since the dock never used to declare it itself.
       With content-box instead of border-box, a button's declared
       padding/border gets added on top of "width: 100%" instead of being
       included in it, pushing it outside the card. Owning box-sizing here
       makes the dock immune to whatever the host page does. */
    .mini-right-dock, .mini-right-dock * {
      box-sizing: border-box;
    }
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
    playYouTubeTrack,
    previous: previousTrack,
    next: nextTrack,
    toggleAutoPlay,
    getState,
    setState,
    updateYouTubePlayback,
    addActivity,
    getUpNext,
    addToQueue,
    playNext,
    removeFromQueue,
    moveQueueItem,
    clearQueue,
    playQueueItem,
    isNonMusicTrack
  };
})();
