/* Shared persistent mini-player. Include this on every page along with the
   #miniPlayerBar markup. State is kept in localStorage so playback position
   and play/pause status survive navigation between MiniGPT's pages. */
(function(){
  function getState(){
    try{ return JSON.parse(localStorage.getItem('mg_playerstate') || 'null'); }
    catch(e){ return null; }
  }
  function setState(s){ localStorage.setItem('mg_playerstate', JSON.stringify(s)); }
  function addActivity(icon, text){
    const arr = JSON.parse(localStorage.getItem('mg_activity') || '[]');
    arr.unshift({icon, text, time: new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})});
    localStorage.setItem('mg_activity', JSON.stringify(arr.slice(0, 20)));
  }
  function fmt(t){
    if(!isFinite(t)) return '0:00';
    const m = Math.floor(t/60), s = Math.floor(t%60).toString().padStart(2,'0');
    return `${m}:${s}`;
  }

  let els = null;
  function bindEls(){
    els = {
      audio: document.getElementById('miniAudio'),
      bar: document.getElementById('miniPlayerBar'),
      playBtn: document.getElementById('miniPlayBtn'),
      seek: document.getElementById('miniSeek'),
      title: document.getElementById('miniTitle'),
      artist: document.getElementById('miniArtist'),
      art: document.getElementById('miniArt'),
      cur: document.getElementById('miniCur'),
      dur: document.getElementById('miniDur')
    };
    return !!els.bar;
  }

  function paintFromState(s){
    if(!s || !s.preview) return;
    els.bar.classList.remove('empty');
    els.title.textContent = s.title;
    els.artist.textContent = s.artist;
    if(s.artwork) els.art.style.background = `url(${s.artwork}) center/cover`;
    els.playBtn.disabled = false;
    els.seek.disabled = false;
  }

  function playTrack(track){
    if(!els) return;
    const s = {title: track.title, artist: track.artist, artwork: track.artwork, preview: track.preview, time: 0, playing: true};
    setState(s);
    paintFromState(s);
    els.audio.src = track.preview;
    els.audio.currentTime = 0;
    els.audio.play().then(() => {
      els.playBtn.textContent = '❚❚';
      addActivity('🎵', `Played "${track.title}" by ${track.artist}`);
    }).catch(() => {
      els.playBtn.textContent = '▶';
    });
  }

  function init(){
    if(!bindEls()) return; // this page doesn't include the mini player markup

    const state = getState();
    if(state && state.preview){
      paintFromState(state);
      els.audio.src = state.preview;
      const resume = () => {
        els.audio.currentTime = state.time || 0;
        if(state.playing){
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
              els.audio.play().then(() => { els.playBtn.textContent = '❚❚'; }).catch(() => {});
            };
            document.addEventListener('pointerdown', resumeOnInteract, {once:true});
            document.addEventListener('keydown', resumeOnInteract, {once:true});
          });
        } else {
          els.playBtn.textContent = '▶';
        }
      };
      if(els.audio.readyState >= 1) resume();
      else els.audio.addEventListener('loadedmetadata', resume, {once:true});
    }

    els.playBtn.onclick = () => {
      if(!els.audio.src) return;
      if(els.audio.paused){
        els.audio.play();
        els.playBtn.textContent = '❚❚';
        const s = getState() || {}; s.playing = true; setState(s);
      } else {
        els.audio.pause();
        els.playBtn.textContent = '▶';
        const s = getState() || {}; s.playing = false; setState(s);
      }
    };

    els.seek.addEventListener('input', () => {
      if(!els.audio.duration) return;
      els.audio.currentTime = (els.seek.value/100) * els.audio.duration;
    });

    els.audio.addEventListener('timeupdate', () => {
      if(!els.audio.duration) return;
      els.seek.value = (els.audio.currentTime / els.audio.duration) * 100;
      els.cur.textContent = fmt(els.audio.currentTime);
      els.dur.textContent = fmt(els.audio.duration);
      const s = getState();
      if(s){ s.time = els.audio.currentTime; setState(s); }
    });

    els.audio.addEventListener('ended', () => {
      els.playBtn.textContent = '▶';
      const s = getState(); if(s){ s.playing = false; setState(s); }
    });

    window.addEventListener('beforeunload', () => {
      const s = getState();
      if(s && els.audio.src){ s.time = els.audio.currentTime; s.playing = !els.audio.paused; setState(s); }
    });
  }

  document.addEventListener('DOMContentLoaded', init);
  window.MiniGPTPlayer = { playTrack, getState, setState, addActivity };
})();
