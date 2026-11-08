/* Independent CCTV source clock, delayed live display, and complete replay. */
(() => {
  'use strict';
  const weaponColors = ['', '#41df83', '#ffe052', '#ff4658'];
  const violenceColors = ['#dce7f5', '#ffe052', '#ff9b38', '#ff4658'];
  const levels = ['Below sensitive', 'Sensitive', 'Moderate', 'Certain'];
  const displayDelaySeconds = 2;
  const playbackUnavailableLabel = 'Playback unavailable · analysis continues in background';
  function markPlaybackError(session) {
    if (!session) return playbackUnavailableLabel;
    session.playerError = true;
    session.needsGesture = true;
    session.drawKey = null;
    return playbackUnavailableLabel;
  }
  function eventAt(events, detector, time) {
    let result = null;
    for (const e of events) if (e.detector === detector && e.sourceSeconds <= time + 1e-6 &&
      (!result || e.sourceSeconds >= result.sourceSeconds)) result = e;
    return result && time < result.validUntilSeconds - 1e-7 ? result : null;
  }
  function alarmsBetween(events, from, to) {
    return events.filter(e => e.alarm && e.sourceSeconds > from && e.sourceSeconds <= to && to < e.validUntilSeconds);
  }
  function containRect(sw, sh, width, height) {
    const scale = Math.min(width / sw, height / sh);
    return {x:(width-sw*scale)/2, y:(height-sh*scale)/2, width:sw*scale, height:sh*scale};
  }
  function livePosition(elapsed, duration, delay=displayDelaySeconds) {
    return Math.min(Math.max(0,duration),Math.max(0,elapsed-delay));
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {eventAt, alarmsBetween, containRect, weaponColors, violenceColors, livePosition, markPlaybackError}; return;
  }
  const sessions = new Map(), requested = new Set(), closed = new Set();
  let timer = null, polling = false, audio = null, soundEnabled = true, lastTone = -Infinity;
  const find = (card, selector) => card.querySelector(selector);
  const clock = seconds => `${Math.floor((seconds || 0)/60)}:${String(Math.floor((seconds || 0)%60)).padStart(2,'0')}`;
  function updateSoundButtons() {
    const active = soundEnabled && audio?.state === 'running';
    document.querySelectorAll('.monitor-sound').forEach(button => {
      button.textContent = active ? 'Mute alert sound' : !soundEnabled ? 'Turn on alert sound' : 'Enable alert sound';
      button.setAttribute('aria-pressed', String(active));
      button.title = active ? 'Turn off warning tones for all feeds' : 'Turn on warning tones for all feeds';
    });
    document.querySelectorAll('.monitor-alert-status').forEach(el => {
      el.textContent = active ? 'Alerts: On' : !soundEnabled ? 'Alerts: Muted' : 'Alerts: Needs permission';
    });
  }
  function enableSound() {
    try {
      audio ||= new (window.AudioContext || window.webkitAudioContext)();
      audio.onstatechange = updateSoundButtons; soundEnabled = true;
      audio.resume().then(updateSoundButtons).catch(updateSoundButtons);
    } catch (_) { soundEnabled = false; }
    updateSoundButtons();
  }
  function tone(level) {
    if (!soundEnabled || audio?.state !== 'running' || performance.now()-lastTone < 180) return;
    lastTone = performance.now();
    for (let i=0;i<2;i++) {
      const oscillator=audio.createOscillator(), gain=audio.createGain(), at=audio.currentTime+i*.18;
      oscillator.frequency.value=[0,620,780,960][level];
      gain.gain.setValueAtTime(0,at); gain.gain.linearRampToValueAtTime(.1,at+.015);
      gain.gain.exponentialRampToValueAtTime(.001,at+.13);
      oscillator.connect(gain); gain.connect(audio.destination); oscillator.start(at); oscillator.stop(at+.14);
      oscillator.onended=()=>{oscillator.disconnect();gain.disconnect();};
    }
  }
  function controls(card) {
    if (card._monitorControls) return;
    card._monitorControls=true;
    find(card,'.monitor-sound')?.remove();
    const wrap=find(card,'.video-wrap'), shell=document.createElement('div'); shell.className='monitor-player';
    wrap.before(shell); shell.append(wrap);
    shell.insertAdjacentHTML('beforeend', `<div class="monitor-controls">
      <div class="monitor-transport"><button type="button" class="monitor-play">Play</button><input class="monitor-seek" type="range" min="0" max="1" step="0.01" value="0" aria-label="Video timeline"><span class="monitor-time">0:00</span><button type="button" class="monitor-fullscreen">Full screen</button></div>
      <div class="monitor-audio"><button type="button" class="monitor-video-sound">Unmute video</button><label>Video volume <input class="monitor-volume" type="range" min="0" max="1" step="0.05" value="0.7" aria-label="Video volume"></label><span class="monitor-alert-status">Alerts: Needs permission</span><button type="button" class="monitor-sound" aria-pressed="false">Enable alert sound</button></div><small class="monitor-phase">Ready</small></div>`);
    const video=find(card,'video'); video.controls=false; video.disablePictureInPicture=true; video.volume=.7;
    const current=()=>[...sessions.values()].find(s=>s.card===card);
    const refresh=()=>{
      const s=current(), live=s?.mode==='live', busy=s?.mode==='loadingReplay';
      const seek=find(card,'.monitor-seek'); seek.disabled=Boolean(live||busy||s?.playerError);
      seek.max=String(video.duration || s?.duration || 1); seek.value=String(video.currentTime);
      find(card,'.monitor-time').textContent=`${clock(video.currentTime)} / ${clock(video.duration || s?.duration)}`;
      const play=find(card,'.monitor-play'); play.disabled=Boolean(busy || (live&&!s.needsGesture));
      play.textContent=live ? s.needsGesture ? 'Start playback' : 'Live playback' : video.paused ? 'Play' : 'Pause';
      find(card,'.monitor-video-sound').textContent=video.muted || video.volume===0 ? 'Unmute video' : 'Mute video';
      find(card,'.monitor-volume').value=String(video.volume);
      find(card,'.monitor-phase').textContent=s?.playerError ? playbackUnavailableLabel : live ? video.ended ? 'Video ended · finishing background analysis…' : s.playerWaiting ? 'Video buffering · CCTV and analysis continue' : s.processed<video.currentTime-.1 ? `Live · analysis catching up (${(video.currentTime-s.processed).toFixed(1)}s) · notifications remain active` : `Live · ${s.delay}-second display delay · seeking locked` : busy ? 'Loading replay results…' : s?.mode==='replay' ? 'Replay · seeking available' : 'Ready';
    };
    card._refreshMonitorControls=refresh;
    video.addEventListener('error',()=>{
      video.dataset.monitorPlaybackError='true';
      const s=current();
      if(s){markPlaybackError(s);refresh();draw(s,video.currentTime);}
    });
    for (const name of ['loadedmetadata','timeupdate','volumechange','play','pause','ended','seeked']) video.addEventListener(name,()=>{
      const s=current();
      if(name==='loadedmetadata'&&s?.playerError){s.playerError=false;s.needsGesture=false;video.dataset.monitorPlaybackError='false';}
      refresh();if(s){s.drawKey=null;draw(s,video.currentTime);}
    });
    video.addEventListener('waiting',()=>{const s=current();if(s){s.playerWaiting=true;if(!s.stallStarted){s.stallCount++;s.stallStarted=performance.now();}}refresh();});
    video.addEventListener('playing',()=>{const s=current();if(s){s.playerWaiting=false;if(s.stallStarted){s.stallMilliseconds+=performance.now()-s.stallStarted;s.stallStarted=null;}catchUp(s);}refresh();});
    video.addEventListener('seeked',()=>{const s=current();if(s?.internalSeek){s.internalSeek=false;s.allowSeek=false;}});
    video.addEventListener('seeking',()=>{
      const s=current();
      if(s?.mode==='live' && !s.allowSeek && Math.abs(video.currentTime-(s.lastTime||0))>.05) video.currentTime=s.lastTime||0;
    });
    find(card,'.monitor-play').onclick=()=>{
      const s=current(); if(s?.mode==='live'&&!s.needsGesture)return;
      if(s?.playerError){video.load();s.playerError=false;s.needsGesture=false;}
      if(video.paused){if(s)s.needsGesture=false;video.play().catch(()=>{if(s)s.needsGesture=true;refresh();});}else video.pause();
    };
    find(card,'.monitor-seek').oninput=event=>{if(current()?.mode==='live')return;video.currentTime=Number(event.target.value);};
    find(card,'.monitor-video-sound').onclick=()=>{if(video.muted||video.volume===0){video.muted=false;if(video.volume===0)video.volume=.7;}else video.muted=true;refresh();};
    find(card,'.monitor-volume').oninput=event=>{video.volume=Number(event.target.value);video.muted=video.volume===0;refresh();};
    find(card,'.monitor-fullscreen').onclick=async()=>{
      try{if(document.fullscreenElement===shell)await document.exitFullscreen();else await shell.requestFullscreen();}
      catch(_){find(card,'.monitor-phase').textContent='This browser could not enter full screen';}
    };
    document.addEventListener('fullscreenchange',()=>{
      if(!card.isConnected)return;
      find(card,'.monitor-fullscreen').textContent=document.fullscreenElement===shell?'Exit full screen':'Full screen';
      const s=current();if(s){s.drawKey=null;draw(s,video.currentTime);}
    });
    refresh(); updateSoundButtons();
  }
  function detach(id) {
    const s=sessions.get(id);if(!s)return;
    cancelAnimationFrame(s.raf);s.video.pause();sessions.delete(id);
  }
  function stop(id,label='Playback stopped') {
    const s=sessions.get(id);if(!s)return;
    detach(id);closed.add(id);
    const c=find(s.card,'.monitor-boxes');c.getContext('2d').clearRect(0,0,c.width,c.height);
    find(s.card,'.monitor-probability').textContent=label;s.card._refreshMonitorControls();
  }
  function draw(s,time) {
    const {card,video,events}=s, violence=eventAt(events,'violence',time), weapon=eventAt(events,'weapon',time);
    const canvas=find(card,'.monitor-boxes'),badge=find(card,'.monitor-probability');
    const width=canvas.clientWidth,height=canvas.clientHeight,dpr=Math.min(devicePixelRatio||1,2);
    const key=`${s.mode}:${violence?.sequence}:${weapon?.sequence}:${time<s.firstWindow}:${width}:${height}`;
    if(key===s.drawKey)return;s.drawKey=key;
    if(s.playerError){badge.style.color='#f4b860';badge.style.borderColor='#f4b860';badge.textContent=playbackUnavailableLabel;canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height);return;}
    badge.style.color=badge.style.borderColor=violenceColors[violence?.level||0];
    badge.textContent=violence?`Violence ${(violence.probability*100).toFixed(1)}% · ${levels[violence.level]}`:time<s.firstWindow?s.mode==='replay'?`Violence · first window ends at ${s.firstWindow}s`:'Violence · collecting first window…':'Violence · waiting for result…';
    canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
    const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);
    if(!video.videoWidth||!video.videoHeight)return;
    const r=containRect(video.videoWidth,video.videoHeight,width,height);
    for(const b of weapon?.boxes||[]) {
      const x=r.x+b.x1*r.width,y=r.y+b.y1*r.height;
      ctx.strokeStyle=weaponColors[b.level];ctx.lineWidth=2;ctx.strokeRect(x,y,(b.x2-b.x1)*r.width,(b.y2-b.y1)*r.height);
      ctx.font='600 11px sans-serif';const text=`${b.className} ${(b.confidence*100).toFixed(1)}% · ${levels[b.level]}`;
      const tw=ctx.measureText(text).width+8,tx=Math.max(r.x,Math.min(x,r.x+r.width-tw)),ty=Math.max(r.y,y-20);
      ctx.fillStyle='rgba(0,0,0,.88)';ctx.fillRect(tx,ty,tw,19);ctx.fillStyle=weaponColors[b.level];ctx.fillText(text,tx+4,ty+13);
    }
  }
  async function history(s) {
    if(s.historyPromise)return s.historyPromise;
    s.historyPromise=(async()=>{
      let after=0;const all=[];
      while(true){
        const response=await fetch(`/api/analysis/${encodeURIComponent(s.id)}/results?after=${after}&limit=1000`);
        if(!response.ok)throw Error('Replay results unavailable');const body=await response.json();
        all.push(...body.events);after=all[all.length-1]?.sequence||0;
        if(after>=body.latestSequence)break;
        if(!body.events.length)throw Error('Incomplete replay results');
      }
      s.events=all;s.historyLoaded=true;s.drawKey=null;
    })().catch(error=>{s.historyPromise=null;throw error;});
    return s.historyPromise;
  }
  function replay(s) {
    if(s.mode==='loadingReplay'||s.mode==='replay')return;
    s.video.pause();s.mode='loadingReplay';s.card._refreshMonitorControls();
    history(s).then(()=>{
      if(!sessions.has(s.id))return;s.mode='replay';s.card._refreshMonitorControls();draw(s,s.video.currentTime);
    }).catch(()=>{if(sessions.has(s.id)){s.mode='live';find(s.card,'.monitor-phase').textContent='Retrying replay download…';}});
  }
  function catchUp(s) {
    if(s.mode!=='live'||s.anchor==null||s.internalSeek||s.video.seeking||s.video.ended)return;
    const target=livePosition(s.elapsed+(performance.now()-s.anchor)/1000,s.duration,s.delay);
    // Normal playback runs continuously. Seek only to recover accumulated delay,
    // never on each clock tick, and never replay sounds from skipped footage.
    if(target-s.video.currentTime<=.35||performance.now()-(s.lastCatchUp||0)<750)return;
    s.allowSeek=true;s.internalSeek=true;s.lastCatchUp=performance.now();
    s.catchUpCount++;s.skippedDisplaySeconds+=target-s.video.currentTime;
    s.audioFloor=Math.max(s.audioFloor??0,target);s.video.currentTime=target;s.lastTime=target;s.drawKey=null;
  }
  function tick(s) {
    if(!sessions.has(s.id))return;
    if(!s.card.isConnected){detach(s.id);return;}
    const v=s.video;
    if(s.mode==='live'&&s.anchor!=null){
      const elapsed=s.elapsed+(performance.now()-s.anchor)/1000;
      const ready=s.startedPlayback || elapsed>=s.delay;
      if(!ready){v.pause();find(s.card,'.monitor-probability').textContent=`Buffering · ${s.delay}-second display delay`;}
      else {
        if(!s.startedPlayback){s.startedPlayback=true;catchUp(s);}
        if(!v.paused&&!s.playerWaiting&&v.readyState>=3)catchUp(s);
        if(v.readyState>=2&&v.paused&&!v.ended&&!s.playPending&&!s.needsGesture){
          s.playPending=true;v.play().catch(()=>{s.needsGesture=true;s.card._refreshMonitorControls();}).finally(()=>{s.playPending=false;});
        }
        v.playbackRate=1;s.lastTime=v.currentTime;draw(s,v.currentTime);
        if(s.audioFloor==null)s.audioFloor=s.fresh?-1e-6:v.currentTime;
        if(!v.paused){const alarms=alarmsBetween(s.events,s.audioFloor,v.currentTime).filter(e=>!s.heard.has(e.sequence));alarms.forEach(e=>s.heard.add(e.sequence));if(alarms.length)tone(Math.max(...alarms.map(e=>e.alarmLevel)));}
        if(v.ended&&s.status==='completed')replay(s);
      }
    }else if(s.mode==='replay')draw(s,v.currentTime);
    s.raf=requestAnimationFrame(()=>tick(s));
  }
  async function poll() {
    const live=[...sessions.values()].filter(s=>s.mode==='live');if(polling||!live.length)return;
    polling=true;const sent=performance.now();
    try{
      const cursors=Object.fromEntries(live.map(s=>[s.id,s.cursor])),players=Object.fromEntries(live.map(s=>[s.id,s.video.currentTime]));
      const response=await fetch('/api/monitor/playback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cursors,players}),cache:'no-store'});
      if(!response.ok)throw Error(`Monitor ${response.status}`);const body=await response.json(),received=performance.now();
      for(const job of body.jobs){
        const s=sessions.get(job.id);if(!s||s.mode!=='live')continue;
        if(['failed','cancelled'].includes(job.status)){stop(job.id,`Analysis ${job.status}`);continue;}
        s.status=job.status;s.duration=job.durationSeconds;s.processed=job.processedSeconds;s.firstWindow=job.firstWindowSeconds;
        s.delay=Number.isFinite(job.playbackDelaySeconds)&&job.playbackDelaySeconds>0?job.playbackDelaySeconds:displayDelaySeconds;
        if(s.lastReceipt!=null){s.cadences.push((received-s.lastReceipt)/1000);if(s.cadences.length>64)s.cadences.shift();}
        s.lastReceipt=received;s.timings=job.timings;s.pendingMediaJobs=job.pendingMediaJobs;
        if(job.elapsedSeconds!=null){s.elapsed=job.elapsedSeconds+(received-sent)/2000;s.anchor=received;}
        if(!s.attached&&s.anchor!=null){s.attached=true;}
        for(const e of job.events)if(e.sequence>s.cursor){s.events.push(e);s.cursor=e.sequence;}
        s.events=s.events.filter(e=>e.validUntilSeconds>=s.video.currentTime-1);
      }
    }catch(_){for(const s of live)find(s.card,'.monitor-phase').textContent='Reconnecting · waiting for player sync…';}
    finally{polling=false;clearTimeout(timer);if([...sessions.values()].some(s=>s.mode==='live'))timer=setTimeout(poll,Math.max(0,100-(performance.now()-sent)));}
  }
  function bind(card,job) {
    controls(card);
    for(const s of sessions.values())if(s.card.dataset.cameraId===card.dataset.cameraId&&s.id!==job?.id)stop(s.id);
    if(!job||['failed','cancelled'].includes(job.status)){if(job)stop(job.id);return;}
    if(closed.has(job.id)||sessions.has(job.id))return;
    const video=find(card,'video');video.pause();
    const s={id:job.id,card,video,events:[],cursor:0,anchor:null,delay:displayDelaySeconds,processed:0,duration:job.durationSeconds,firstWindow:4,audioFloor:null,heard:new Set(),fresh:requested.delete(job.cameraId),status:job.status,mode:'live',lastTime:0,allowSeek:true,cadences:[],stallCount:0,stallMilliseconds:0,catchUpCount:0,skippedDisplaySeconds:0,playerError:video.dataset.monitorPlaybackError==='true'||Boolean(video.error)};
    if(s.playerError)markPlaybackError(s);
    video.currentTime=0;setTimeout(()=>{s.allowSeek=false;},200);sessions.set(job.id,s);
    if(job.status==='completed')replay(s);else poll();draw(s,video.currentTime);tick(s);card._refreshMonitorControls();
  }
  document.addEventListener('click',event=>{if(event.target.closest('.monitor-sound')){if(soundEnabled&&audio?.state==='running'){soundEnabled=false;updateSoundButtons();}else enableSound();}});
  window.MonitorPlayback={bind,stop,enableSound,sessions,updateSoundButtons,delaySeconds:displayDelaySeconds,
    startRequested(cameraId){requested.add(cameraId);if(soundEnabled)enableSound();},
    prune(){for(const s of sessions.values())if(!s.card.isConnected)detach(s.id);}};
})();
