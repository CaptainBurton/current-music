// Profile, podcast, lyrics, and appearance features layered onto the core library UI.
paths.mic = '<rect x="8" y="2" width="8" height="13" rx="4"/><path d="M5 11a7 7 0 0 0 14 0M12 18v4M8 22h8"/>';
paths.lyrics = '<path d="M4 5h16M4 10h12M4 15h16M4 20h10"/>';
paths.queue = '<path d="M3 6h12M3 11h12M3 16h8M15 15l6 3-6 3z"/>';
paths.bell = '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>';
const lyricsCache = new Map();
let lastProgressSave = 0;
let activePlayerPanel = null;
let playerPanelOpener = null;
let searchTimer = null;

function avatarMarkup(user, className='avatar') {
  return `<span class="${className}">${user.avatar_url?`<img src="${esc(user.avatar_url)}" alt="">`:esc((user.display_name||user.username||'?')[0].toUpperCase())}</span>`;
}

function applyTheme() {
  document.body.dataset.theme = state.user?.theme || 'current';
}

function refreshUserChip() {
  const chip = document.querySelector('.user-chip');
  if (chip && state.user) chip.innerHTML = `${avatarMarkup(state.user)}<span class="username">${esc(state.user.display_name||state.user.username)}</span>`;
  const name=document.querySelector('.profile-menu-name');
  if(name&&state.user)name.textContent=state.user.display_name||state.user.username;
}

const coreRenderShell = renderShell;
renderShell = function() {
  coreRenderShell();
  applyTheme();
  const firstGroup = document.querySelector('.sidebar .side-group');
  firstGroup?.querySelector('[data-view="search"]')?.remove();
  firstGroup?.insertAdjacentHTML('beforeend', `<button class="nav-item" data-view="explore">${icon('disc')}<span>Explore</span></button>`);
  firstGroup?.insertAdjacentHTML('beforeend', `<button class="nav-item" data-view="podcasts">${icon('mic')}<span>Podcasts</span></button>`);
  document.querySelectorAll('.sidebar .side-group')[1]?.classList.add('secondary-side-group');
  document.querySelector('.top-left')?.insertAdjacentHTML('afterend', `<label class="top-search">${icon('search')}<input id="global-search" type="search" placeholder="Search music" aria-label="Search your library and the server" autocomplete="off"></label>`);
  document.querySelector('.top-right')?.insertAdjacentHTML('beforeend', `<div class="notification-wrap"><button class="icon-button notification-bell" aria-label="Notifications" aria-expanded="false" title="Notifications">${icon('bell')}<span class="notification-badge" hidden></span></button><div class="notification-dropdown" hidden></div></div>`);
  const chip=document.querySelector('.user-chip');
  const profileWrap=document.createElement('div');profileWrap.className='profile-menu-wrap';
  chip.before(profileWrap);profileWrap.append(chip);
  chip.setAttribute('aria-haspopup','true');chip.setAttribute('aria-expanded','false');
  profileWrap.insertAdjacentHTML('beforeend',`<div class="profile-dropdown" hidden><div class="profile-menu-heading"><strong class="profile-menu-name">${esc(state.user.display_name||state.user.username)}</strong><small>@${esc(state.user.username)} · ${esc(state.user.role)}</small></div><button type="button" data-view="profile">${icon('user')} Your Settings</button>${state.user.role==='admin'?`<button type="button" data-view="settings">${icon('settings')} Admin Settings</button>`:''}<div class="profile-menu-divider"></div><button type="button" data-action="logout">${icon('logout')} Sign out</button></div>`);
  document.querySelector('.sidebar .brand')?.insertAdjacentHTML('afterend', '<div class="spotify-side-identity"><img src="/assets/spotify-icon.svg" alt=""><span>Spotify theme</span></div>');  document.querySelector('.top-right')?.insertAdjacentHTML('afterbegin', '<div class="sabrina-theme-badge"><img src="/assets/sabrina-signature.svg" alt=""><span>SABRINA<br>CARPENTER</span></div>');
  document.querySelector('.side-bottom')?.insertAdjacentHTML('afterbegin', `<button class="nav-item" data-view="profile">${icon('user')}<span>Your Settings</span></button>`);
  document.querySelector('.player-right')?.insertAdjacentHTML('afterbegin', `<button class="icon-button player-lyrics" data-view="nowplaying" title="Now Playing and lyrics" aria-label="Now Playing and lyrics" aria-pressed="false">${icon('lyrics')}</button>`);
  document.querySelector('.player-right')?.insertAdjacentHTML('afterbegin', `<button class="icon-button player-queue" data-view="queue" title="Queue" aria-label="Queue" aria-pressed="false">${icon('queue')}</button>`);
  document.querySelector('.app')?.insertAdjacentHTML('beforeend', `<div class="player-panel-backdrop" data-player-panel-close></div><aside class="player-panel" role="dialog" aria-modal="false" aria-hidden="true" aria-labelledby="player-panel-title"><header class="player-panel-header"><div><span class="eyebrow">YOUR PLAYER</span><h2 id="player-panel-title"></h2></div><button class="icon-button player-panel-close" data-player-panel-close aria-label="Close panel" title="Close panel">${icon('close')}</button></header><div class="player-panel-body"></div></aside>`);
  activePlayerPanel = null;
  document.body.classList.remove('player-panel-open');
  const search=document.getElementById('global-search');
  search.value=state.searchQuery||'';
  search.addEventListener('focus',()=>{if(state.view!=='search')navigate('search')});
  search.addEventListener('input',()=>{state.searchQuery=search.value;if(state.view!=='search')navigate('search');else renderSearch()});
  document.querySelector('.notification-bell').addEventListener('click',toggleNotifications);
  clearInterval(state.notificationTimer);
  refreshNotifications();
  state.notificationTimer=setInterval(refreshNotifications,30000);
  refreshUserChip();
};

function renderSearch() {
  const query=(state.searchQuery||'').trim();
  setContent(`<div class="page-header"><div><div class="eyebrow">FIND YOUR NEXT FAVORITE</div><h1>Search</h1><p>Search your saved music and everything on the server.</p></div></div><div id="search-results">${query?'<p class="hint">Searching…</p>':empty('What do you want to hear?','Search from the field above, or browse popular music in Explore.','<button class="outline-button" data-view="explore">Explore music</button>')}</div>`,'Search');
  clearTimeout(searchTimer);
  if(!query)return;
  searchTimer=setTimeout(async()=>{
    try {
      const result=await api('/search?q='+encodeURIComponent(query));
      if(state.view!=='search'||(state.searchQuery||'').trim()!==query)return;
      const target=document.getElementById('search-results');if(!target)return;
      const savedTracks=result.tracks.filter(t=>t.saved),serverTracks=result.tracks.filter(t=>!t.saved);
      const savedAlbums=result.albums.filter(a=>a.saved),serverAlbums=result.albums.filter(a=>!a.saved);
      state.visibleTracks=result.tracks;
      const group=(heading,albums,tracks,artists=[])=>!albums.length&&!tracks.length&&!artists.length?'':`<section class="section search-group"><h2 class="section-title">${heading}</h2>${artists.length?`<div class="quick-grid search-artists">${artists.map(name=>`<button class="quick-card" data-artist-name="${esc(name)}"><div class="mini-cover">${icon('user')}</div><span>${esc(name)}</span></button>`).join('')}</div>`:''}${albums.length?`<h3>Albums</h3><div class="cards">${albums.map(albumCard).join('')}</div>`:''}${tracks.length?`<h3>Songs</h3>${trackTable(tracks)}`:''}</section>`;
      target.innerHTML=group('In your library',savedAlbums,savedTracks)+group('On the server',serverAlbums,serverTracks,result.artists)||empty('No results found',`Nothing matches “${query}”.`);
    } catch(err){notify(err.message)}
  },220);
}

async function renderExplore() {
  const data=await api('/explore');
  state.visibleTracks=data.tracks;
  const artists=data.artists.map(a=>`<article class="card artist-card" data-artist-name="${esc(a.name)}"><div class="artist-card-image">${a.portrait_url?`<img src="${esc(a.portrait_url)}" alt="">`:a.cover_album_id?`<img src="/api/albums/${a.cover_album_id}/cover" alt="">`:icon('user')}</div><div class="card-title">${esc(a.name)}</div><p class="card-sub">${a.album_count} release${a.album_count===1?'':'s'}</p></article>`).join('');
  setContent(`<div class="page-header"><div><div class="eyebrow">DISCOVER ON THIS SERVER</div><h1>Explore</h1><p>Popular artists, albums, and songs from the shared collection.</p></div></div>${artists?`<section class="section"><h2 class="section-title">Popular artists</h2><div class="cards artist-cards">${artists}</div></section>`:''}${data.albums.length?`<section class="section"><h2 class="section-title">Popular albums</h2><div class="cards">${data.albums.map(albumCard).join('')}</div></section>`:''}${data.tracks.length?`<section class="section"><h2 class="section-title">Popular songs</h2>${trackTable(data.tracks)}</section>`:''}${!artists&&!data.albums.length&&!data.tracks.length?empty('Nothing to explore yet','Ask an admin to scan some music.'):''}`,'Explore');
}

function notificationDate(value) {
  const date=new Date(value.replace(' ','T')+'Z');
  return Number.isNaN(date.getTime())?'':date.toLocaleDateString();
}

async function refreshNotifications() {
  if(!state.user)return;
  try {
    const data=await api('/notifications');
    const wrap=document.querySelector('.notification-wrap');if(!wrap)return;
    state.notifications=data.items;
    const badge=wrap.querySelector('.notification-badge');badge.hidden=!data.unread;badge.textContent=data.unread>99?'99+':String(data.unread);
    const dropdown=wrap.querySelector('.notification-dropdown');
    dropdown.innerHTML=`<div class="notification-head"><strong>Notifications</strong>${data.unread?'<button class="text-link" data-notification-action="read-all">Mark all read</button>':''}</div>${data.items.length?`<div class="notification-list">${data.items.map(item=>`<button class="notification-item ${item.read_at?'':'is-unread'}" data-notification-id="${item.id}" data-album-id="${item.album_id}"><span class="notification-dot"></span><span><strong>${esc(item.artist_name||'An artist you follow')}</strong> added ${esc(item.track_title)}<small>${esc(item.album_title)} · ${notificationDate(item.created_at)}</small></span></button>`).join('')}</div>`:'<p class="notification-empty">New songs from artists you follow will appear here.</p>'}`;
  } catch(err){/* A temporary refresh failure should not interrupt listening. */}
}

function toggleNotifications() {
  const wrap=document.querySelector('.notification-wrap');if(!wrap)return;
  const dropdown=wrap.querySelector('.notification-dropdown');
  dropdown.hidden=!dropdown.hidden;
  wrap.querySelector('.notification-bell').setAttribute('aria-expanded',String(!dropdown.hidden));
  if(!dropdown.hidden)refreshNotifications();
}
document.addEventListener('click',e=>{if(!e.target.closest('.notification-wrap')){const wrap=document.querySelector('.notification-wrap');if(wrap){wrap.querySelector('.notification-dropdown').hidden=true;wrap.querySelector('.notification-bell').setAttribute('aria-expanded','false')}}});

function closeProfileMenu(restoreFocus=false){
  const wrap=document.querySelector('.profile-menu-wrap');if(!wrap)return;
  const dropdown=wrap.querySelector('.profile-dropdown');
  if(dropdown.hidden)return;
  dropdown.hidden=true;
  wrap.querySelector('.user-chip').setAttribute('aria-expanded','false');
  if(restoreFocus)wrap.querySelector('.user-chip').focus();
}
function toggleProfileMenu(){
  const wrap=document.querySelector('.profile-menu-wrap');if(!wrap)return;
  const dropdown=wrap.querySelector('.profile-dropdown');
  if(!dropdown.hidden){closeProfileMenu();return}
  dropdown.hidden=false;
  wrap.querySelector('.user-chip').setAttribute('aria-expanded','true');
}
document.addEventListener('click',e=>{if(!e.target.closest('.profile-menu-wrap')||e.target.closest('.profile-dropdown button'))closeProfileMenu()});

const coreRenderHome=renderHome;
renderHome=async function() {
  await coreRenderHome();
  const data=state.home;
  const anchor=document.querySelector('.hero + .section')||document.querySelector('.hero');
  if(!anchor)return;
  const artistCards=(data.top_artists||[]).map(artist=>`<article class="card artist-card home-artist-card" data-artist-name="${esc(artist.name)}"><div class="artist-card-image">${artist.portrait_url?`<img src="${esc(artist.portrait_url)}" alt="">`:artist.cover_album_id?`<img src="/api/albums/${artist.cover_album_id}/cover" alt="">`:icon('user')}</div><div class="card-title">${esc(artist.name)}</div><p class="card-sub">Artist · ${artist.album_count} release${artist.album_count===1?'':'s'}</p></article>`).join('');
  const albumCards=(data.top_albums||[]).map(albumCard).join('');
  anchor.insertAdjacentHTML('afterend',`${artistCards?`<section class="section home-feature-section"><div class="section-head"><div><h2 class="section-title">Popular artists</h2><p class="section-sub">Discover music from the server collection</p></div><button class="text-link" data-view="explore">Explore</button></div><div class="home-scroll-row">${artistCards}</div></section>`:''}${albumCards?`<section class="section home-feature-section"><div class="section-head"><div><h2 class="section-title">Popular albums</h2><p class="section-sub">Find something new to save or play</p></div><button class="text-link" data-view="explore">Explore</button></div><div class="home-scroll-row">${albumCards}</div></section>`:''}`);
  if(document.body.dataset.theme==='sabrina')document.querySelector('.hero')?.insertAdjacentHTML('beforeend','<div class="sabrina-hero-identity"><img src="/assets/sabrina-signature.svg" alt=""><strong>SABRINA CARPENTER</strong><span>THEME</span></div>');
  if(document.body.dataset.theme==='spotify')document.querySelector('.hero')?.insertAdjacentHTML('beforeend','<div class="spotify-hero-identity"><img src="/assets/spotify-icon.svg" alt=""><strong>Spotify theme</strong></div>');
  if(document.body.dataset.theme==='doja')document.querySelector('.hero')?.insertAdjacentHTML('beforeend','<div class="doja-hero-identity"><img src="/assets/doja-cat-signature.svg" alt=""><span>DOJA CAT</span></div>');
};

const coreNavigate = navigate;
navigate = async function(view='home', id=null, push=true) {
  if (!state.user) return;
  if (view==='queue' || view==='nowplaying') {
    if (!document.getElementById('content')?.innerHTML) await coreNavigate('home',null,false);
    openPlayerPanel(view);
    return;
  }
  closePlayerPanel();
  if (!['profile','podcasts','podcast','explore'].includes(view)) return coreNavigate(view,id,push);
  state.view=view; state.id=id;
  if (push) pushAppRoute(view,id);
  try {
    if (view==='profile') renderProfile();
    else if (view==='podcasts') await renderPodcasts();
    else if (view==='podcast') await renderPodcast(id);
    else if (view==='explore') await renderExplore();
  } catch(err) {
    setContent(empty('Could not open this page',err.message),'Error');
  }
};

function setPlayerPanelContent(html, title) {
  const panel=document.querySelector('.player-panel');
  if (!panel) return;
  panel.querySelector('.player-panel-body').innerHTML=html;
  panel.querySelector('#player-panel-title').textContent=title;
}

function openPlayerPanel(view) {
  const panel=document.querySelector('.player-panel');
  if (!panel) return;
  if (activePlayerPanel===view) { closePlayerPanel(); return; }
  if (!activePlayerPanel) playerPanelOpener=document.activeElement;
  activePlayerPanel=view;
  if (view==='queue') renderQueue();
  else renderNowPlaying();
  panel.removeAttribute('aria-hidden');
  document.body.classList.add('player-panel-open');
  document.querySelector('.main')?.setAttribute('inert','');
  document.querySelector('.sidebar')?.setAttribute('inert','');
  document.querySelectorAll('.player-right [data-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.view===view)));
  panel.querySelector('.player-panel-close').focus();
}

function closePlayerPanel(restoreFocus=true) {
  if (!activePlayerPanel) return;
  activePlayerPanel=null;
  document.body.classList.remove('player-panel-open');
  document.querySelector('.main')?.removeAttribute('inert');
  document.querySelector('.sidebar')?.removeAttribute('inert');
  document.querySelectorAll('.player-right [data-view]').forEach(button=>button.setAttribute('aria-pressed','false'));
  if (restoreFocus && playerPanelOpener?.isConnected) playerPanelOpener.focus();
  playerPanelOpener=null;
  document.querySelector('.player-panel')?.setAttribute('aria-hidden','true');
  if (location.hash==='#queue' || location.hash==='#nowplaying') history.replaceState({currentApp:true,from:null},'',`#${state.view||'home'}`);
}

const coreEndSession=endSession;
endSession=function() {
  closePlayerPanel(false);
  closeProfileMenu();
  librarySummaryRequest++;
  clearInterval(state.notificationTimer);
  clearTimeout(searchTimer);
  state.searchQuery='';
  state.notifications=[];
  coreEndSession();
};

document.addEventListener('keydown',e=>{
  if(e.key!=='Escape')return;
  const modal=document.querySelector('.modal-backdrop');
  if(modal){e.preventDefault();modal.remove();return}
  if(document.querySelector('.profile-dropdown:not([hidden])')){e.preventDefault();closeProfileMenu(true);return}
  if(activePlayerPanel){e.preventDefault();closePlayerPanel()}
});
document.addEventListener('click',e=>{if(e.target.closest('[data-player-panel-close]')){e.preventDefault();closePlayerPanel()}});

function renderProfile() {
  const u=state.user;
  setContent(`<div class="page-header"><div><div class="eyebrow">MAKE IT YOURS</div><h1>Your Settings</h1><p>Your profile and the way Current looks for you.</p></div></div>
    <div class="profile-layout"><section class="settings-panel"><h2>Profile</h2><p>Other listeners can see your picture and display name. Your username is still used to sign in.</p>
      <div class="profile-avatar-row">${avatarMarkup(u,'profile-avatar')}<div><strong>${esc(u.display_name||u.username)}</strong><small>@${esc(u.username)}</small></div></div>
      <form id="avatar-form" class="avatar-form"><div class="field"><label for="avatar-file">PROFILE PICTURE</label><input id="avatar-file" type="file" accept="image/png,image/jpeg,image/webp" required><span class="hint">PNG, JPEG or WebP, up to 2 MB.</span></div><div class="profile-actions"><button class="outline-button" type="submit">Upload picture</button>${u.avatar_url?'<button class="outline-button" type="button" data-action="remove-avatar">Remove picture</button>':''}</div></form>
      <form id="profile-form"><div class="field"><label for="display-name">DISPLAY NAME</label><input id="display-name" name="display_name" maxlength="50" value="${esc(u.display_name||u.username)}" required></div><button class="primary-button" type="submit">Save name</button></form>
      <div class="profile-security"><h3>Account security</h3><button class="outline-button" data-action="change-password">Change password</button></div>
    </section><section class="settings-panel"><h2>Theme</h2><p>Choose the look you want. Your selection is saved to your account.</p>
      <div class="theme-options">
        <button class="theme-option theme-preview-current ${u.theme==='current'?'selected':''}" data-theme-choice="current"><span class="theme-swatch"><i></i><b>Current</b></span><strong>Current</strong><small>Dark, clean and cyan</small></button>
        <button class="theme-option theme-preview-ios6 ${u.theme==='ios6'?'selected':''}" data-theme-choice="ios6"><span class="theme-swatch"><i></i><b>Music</b></span><strong>iOS 6 Classic</strong><small>Brushed metal, gloss and reflected covers</small></button>
        <button class="theme-option theme-preview-sabrina ${u.theme==='sabrina'?'selected':''}" data-theme-choice="sabrina"><span class="theme-swatch"><img src="/assets/sabrina-signature.svg" alt=""><b>SC</b></span><strong>Sabrina Carpenter</strong><small>Editorial type, gold and film inspired details</small></button>
        <button class="theme-option theme-preview-spotify ${u.theme==='spotify'?'selected':''}" data-theme-choice="spotify"><span class="theme-swatch"><img src="/assets/spotify-icon.svg" alt=""><b>Spotify</b></span><strong>Spotify</strong><small>Dark surfaces, bright green and familiar rounded controls</small></button>
        <button class="theme-option theme-preview-doja ${u.theme==='doja'?'selected':''}" data-theme-choice="doja"><span class="theme-swatch"><img src="/assets/doja-cat-signature.svg" alt=""></span><strong>Doja Cat</strong><small>Lavender chrome, scanlines and hot pink serif type</small></button>
      </div></section></div>`, 'Your Settings');
  document.getElementById('profile-form').onsubmit=async e=>{e.preventDefault();try{state.user=await api('/me/profile',{method:'PUT',body:{display_name:e.target.elements.display_name.value}});refreshUserChip();notify('Display name saved');renderProfile()}catch(err){notify(err.message)}};
  document.getElementById('avatar-form').onsubmit=async e=>{e.preventDefault();const file=document.getElementById('avatar-file').files[0];if(!file)return;if(file.size>2_000_000){notify('Choose an image under 2 MB');return}try{const response=await fetch('/api/me/avatar',{method:'PUT',credentials:'same-origin',headers:{'Content-Type':file.type},body:file});const data=await response.json();if(!response.ok)throw new Error(data.detail||'Upload failed');state.user=data;refreshUserChip();notify('Profile picture updated');renderProfile()}catch(err){notify(err.message)}};
}

function episodeItem(e) {
  const date=e.published_at?new Date(e.published_at).toLocaleDateString():'';
  return `<article class="episode-row" data-episode-row="${e.id}"><div class="episode-cover">${miniCover(e)}</div><div class="episode-copy"><strong>${esc(e.title)}</strong><small>${esc(e.podcast_title||'')} ${date?'· '+esc(date):''} ${e.duration?'· '+dur(e.duration):''}</small><p>${esc(e.description||'No description supplied by this feed.')}</p>${e.position>0?`<span class="episode-resume">${dur(e.position)} listened</span>`:''}</div><button class="icon-button episode-menu-button" data-episode-menu="${e.id}" title="Episode options" aria-label="More options for ${esc(e.title)}">${icon('more')}</button><button class="round-play" data-episode-id="${e.id}" aria-label="Play ${esc(e.title)}">${icon('play')}</button></article>`;
}

async function renderPodcasts() {
  const [shows,episodes]=await Promise.all([api('/podcasts'),api('/podcasts/episodes/recent')]);
  state.visibleEpisodes=episodes;
  setContent(`<div class="page-header"><div><div class="eyebrow">LISTEN FURTHER</div><h1>Podcasts</h1><p>Follow podcasts by RSS feed and listen alongside your music.</p></div></div>
    <form id="podcast-form" class="podcast-subscribe"><div class="field"><label for="feed-url">PODCAST RSS URL</label><input id="feed-url" name="feed_url" type="url" placeholder="https://example.com/feed.xml" required></div><button class="primary-button" type="submit">${icon('plus')} Follow podcast</button></form>
    <section class="section"><div class="section-head"><div><h2 class="section-title">Your shows</h2><p class="section-sub">${shows.length} followed</p></div></div>${shows.length?`<div class="cards">${shows.map(s=>`<article class="card podcast-card" data-podcast-id="${s.id}">${cover({cover_url:s.artwork_url})}<div class="card-title">${esc(s.title)}</div><p class="card-sub">${esc(s.author||'Podcast')} · ${s.episode_count} episodes</p></article>`).join('')}</div>`:empty('No podcasts yet','Paste a podcast RSS link above to follow your first show.')}</section>
    ${episodes.length?`<section class="section"><div class="section-head"><div><h2 class="section-title">Latest episodes</h2><p class="section-sub">Fresh from the shows you follow</p></div></div><div class="episode-list">${episodes.slice(0,20).map(episodeItem).join('')}</div></section>`:''}`, 'Podcasts');
  document.getElementById('podcast-form').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;try{const show=await api('/podcasts',{method:'POST',body:{feed_url:e.target.elements.feed_url.value}});notify(`Following ${show.title}`);navigate('podcast',show.id)}catch(err){notify(err.message);button.disabled=false}};
}

async function renderPodcast(id) {
  const show=await api(`/podcasts/${id}`);
  state.visibleEpisodes=show.episodes.map(e=>({...e,podcast_title:show.title}));
  setContent(`<div class="page-header podcast-header"><div class="page-cover">${cover({cover_url:show.artwork_url})}</div><div><div class="eyebrow">PODCAST</div><h1>${esc(show.title)}</h1><p>${esc(show.author||'Podcast')} · ${show.episodes.length} episodes</p></div></div>
    <div class="page-actions"><button class="round-play" data-episode-id="${show.episodes[0]?.id||''}" ${show.episodes.length?'':'disabled'} title="Play latest episode">${icon('play')}</button><button class="outline-button" data-action="refresh-podcast" data-podcast-id="${show.id}">Refresh feed</button><button class="outline-button" data-action="unfollow-podcast" data-podcast-id="${show.id}">Unfollow</button></div>
    ${show.description?`<p class="podcast-description">${esc(show.description)}</p>`:''}
    <section class="section"><h2 class="section-title">Episodes</h2><div class="episode-list">${show.episodes.length?show.episodes.map(e=>episodeItem({...e,podcast_title:show.title})).join(''):empty('No playable episodes','This RSS feed does not have HTTPS audio enclosures.')}</div></section>`,show.title);
}

function podcastTrack(e) {
  return {kind:'podcast',id:e.id,title:e.title,artist:e.podcast_title||'Podcast',album:'Podcast',cover_url:e.cover_url||e.artwork_url||null,duration:e.duration||0,audio_url:e.audio_url,position:e.position||0,description:e.description||''};
}

function nextQueueIndex() {
  if(!state.current||state.queueIndex<0)return -1;
  const sequential=state.queueIndex+1;
  if(sequential>=state.queue.length)return -1;
  if(state.queue[sequential]?.queuedManually)return sequential;
  if(state.shuffle&&state.queue.length>1){
    if(state.randomNextIndex==null||state.randomNextIndex>=state.queue.length||state.randomNextIndex===state.queueIndex){
      let index=Math.floor(Math.random()*(state.queue.length-1));
      if(index>=state.queueIndex)index++;
      state.randomNextIndex=index;
    }
    return state.randomNextIndex;
  }
  return sequential;
}

function queueItem(item,mode) {
  if(!state.current){playTrack(item,[item]);notify('Playing now');return}
  const entry={...item,queuedManually:true,queueToken:Date.now()+Math.random()};
  if(mode==='next')state.queue.splice(state.queueIndex+1,0,entry);
  else state.queue.push(entry);
  state.randomNextIndex=null;
  notify(mode==='next'?'Will play next':'Added to queue');
  if(activePlayerPanel==='nowplaying')renderNowPlaying();
  if(activePlayerPanel==='queue')renderQueue();
}

function renderQueue() {
  const upcoming=state.queue.slice(state.queueIndex+1,state.queueIndex+101);
  setPlayerPanelContent(`<p class="player-panel-summary">${upcoming.length} item${upcoming.length===1?'':'s'} up next</p>
    ${state.current?`<section class="section"><h2 class="section-title">Now playing</h2><div class="queue-row current">${miniCover(state.current)}<div><strong>${esc(state.current.title)}</strong><small>${esc(state.current.artist)}</small></div><button class="outline-button" data-view="nowplaying">Open player</button></div></section>`:''}
    <section class="section"><h2 class="section-title">Up next</h2>${upcoming.length?`<div class="queue-list">${upcoming.map((item,i)=>`<div class="queue-row">${miniCover(item)}<div><strong>${esc(item.title)}</strong><small>${esc(item.artist)}</small></div><button class="icon-button" data-queue-remove="${state.queueIndex+1+i}" title="Remove from queue" aria-label="Remove ${esc(item.title)} from queue">${icon('close')}</button></div>`).join('')}</div>`:empty('Queue is empty','Use a song’s options menu to choose Play Next or Add to Queue.')}</section>`,'Queue');
}

function showQueueMenu(item,x,y) {
  document.querySelector('.queue-popup')?.remove();
  const menu=document.createElement('div');menu.className='queue-popup';menu.setAttribute('role','menu');
  menu.style.left=`${Math.max(8,Math.min(x,innerWidth-190))}px`;
  menu.style.top=`${Math.max(8,Math.min(y,innerHeight-126))}px`;
  menu.innerHTML=`<div class="queue-popup-title">${esc(item.title)}</div><button role="menuitem" data-queue-mode="next">${icon('next')} Play Next</button><button role="menuitem" data-queue-mode="end">${icon('queue')} Add to Queue</button>`;
  menu.addEventListener('click',e=>{const action=e.target.closest('[data-queue-mode]');if(!action)return;e.stopPropagation();queueItem(item,action.dataset.queueMode);menu.remove()});
  document.body.append(menu);
}

document.addEventListener('pointerdown',e=>{const menu=document.querySelector('.queue-popup');if(menu&&!menu.contains(e.target))menu.remove()});
document.addEventListener('contextmenu',async e=>{
  const row=e.target.closest('[data-track],[data-episode-row]');
  if(!row)return;
  e.preventDefault();
  let item;
  if(row.dataset.track){const id=Number(row.dataset.track);item=state.visibleTracks?.find(t=>t.id===id)||((await api('/tracks')).find(t=>t.id===id))}
  else item=podcastTrack(state.visibleEpisodes?.find(t=>t.id===Number(row.dataset.episodeRow))||{});
  if(item?.id)showQueueMenu(item,e.clientX,e.clientY);
});

const corePlayTrack=playTrack;
playTrack=function(item,queue) {
  if (item.kind!=='podcast') {
    corePlayTrack(item,queue);
    state.queueIndex=state.queue.indexOf(item);
    state.randomNextIndex=null;
    loadLyrics(item);
    if(activePlayerPanel==='nowplaying')renderNowPlaying();
    if(activePlayerPanel==='queue')renderQueue();
    return;
  }
  state.queue=queue||[item];state.queueIndex=state.queue.indexOf(item);state.current=item;state.randomNextIndex=null;
  audio.src=item.audio_url;
  audio.addEventListener('loadedmetadata',()=>{if(state.current===item&&item.position>0&&item.position<audio.duration-10)audio.currentTime=item.position},{once:true});
  audio.play().catch(()=>notify('Could not play this podcast episode.'));
  updatePlayer();
  if(activePlayerPanel==='nowplaying')renderNowPlaying();
  if(activePlayerPanel==='queue')renderQueue();
  if('mediaSession'in navigator)navigator.mediaSession.metadata=new MediaMetadata({title:item.title,artist:item.artist,album:'Podcast',artwork:item.cover_url?[{src:item.cover_url}]:[]});
};

advance=function(direction,auto=false){
  if(!state.queue.length)return;
  // Repeat replays the song when it ends on its own; Next still skips ahead.
  if(state.repeat&&auto){audio.currentTime=0;audio.play().catch(()=>{});return}
  const index=direction===1?nextQueueIndex():(state.queueIndex>0?state.queueIndex-1:state.queue.length-1);
  if(index<0){audio.pause();state.playing=false;updatePlayer();return}
  playTrack(state.queue[index],state.queue);
};

const coreUpdatePlayer=updatePlayer;
function syncNowToggle() {
  const toggle=document.querySelector('.now-toggle');
  if(toggle&&toggle.dataset.playing!==String(state.playing)){
    toggle.dataset.playing=String(state.playing);
    toggle.innerHTML=`${solidPlayGlyph(state.playing)}<span>${state.playing?'Pause':'Play'}</span>`;
  }
}
updatePlayer=function() {
  coreUpdatePlayer();
  const playerTrack=document.getElementById('player-track');
  if(playerTrack&&state.current){playerTrack.dataset.view='nowplaying';playerTrack.title='Open Now Playing'}
  syncNowToggle();
  syncLyrics();
};

async function loadLyrics(track,force=false) {
  if(!track||track.kind==='podcast'||lyricsCache.has(track.id))return;
  lyricsCache.set(track.id,{loading:true,synced:[],plain:''});
  try {
    let data=await api(`/tracks/${track.id}/lyrics`);
    if(force||(!data.synced?.length&&!data.plain&&data.source!=='none'))data=await api(`/tracks/${track.id}/lyrics/lookup${force?'?force=1':''}`,{method:'POST'});
    lyricsCache.set(track.id,data);
  } catch(err) {lyricsCache.set(track.id,{synced:[],plain:'',error:err.message})}
  if(activePlayerPanel==='nowplaying'&&state.current?.id===track.id&&state.current?.kind!=='podcast')renderNowPlaying();
}

function flowCard(item,position) {
  if(!item)return '';
  return `<div class="flow-card flow-${position}" data-flow-key="${esc(flowKey(item))}" ${position==='current'?'':`data-flow-jump="${position}" title="Play ${esc(item.title)}"`}><div class="flow-art">${cover(item)}</div><div class="flow-mirror" aria-hidden="true">${cover(item)}</div></div>`;
}

function flowKey(item) {
  return `${item.kind==='podcast'?'podcast':'track'}-${item.queueToken||item.id}`;
}

function transitionCoverFlow(previousStage) {
  const nextStage=document.querySelector('.now-cover-stage');
  if(!previousStage||!nextStage||document.body.dataset.theme!=='ios6')return;
  const oldCards=[...previousStage.querySelectorAll('.flow-card')];
  const newCards=[...nextStage.querySelectorAll('.flow-card')];
  const currentKey=nextStage.querySelector('.flow-current')?.dataset.flowKey;
  const oldCurrentKey=previousStage.querySelector('.flow-current')?.dataset.flowKey;
  const newKeys=new Set(newCards.map(card=>card.dataset.flowKey));
  nextStage.replaceWith(previousStage);
  if(oldCurrentKey===currentKey&&oldCards.length===newCards.length&&newCards.every(card=>oldCards.some(old=>old.dataset.flowKey===card.dataset.flowKey&&old.className===card.className)))return;
  if(window.matchMedia('(prefers-reduced-motion: reduce)').matches){previousStage.replaceChildren(...newCards);return}
  const backward=previousStage.querySelector('.flow-previous')?.dataset.flowKey===currentKey;
  const oldByKey=new Map(oldCards.map(card=>[card.dataset.flowKey,card]));
  const changes=[];
  for(const card of oldCards){
    if(newKeys.has(card.dataset.flowKey))continue;
    const side=oldCurrentKey===currentKey?(card.classList.contains('flow-previous')?'left':'right'):(backward?'right':'left');
    card.className=`flow-card flow-exit-${side}`;
    card.removeAttribute('data-flow-jump');
    card.removeAttribute('title');
  }
  for(const target of newCards){
    const card=oldByKey.get(target.dataset.flowKey);
    if(card){changes.push([card,{className:target.className,flowJump:target.dataset.flowJump,title:target.title}]);continue}
    const side=target.classList.contains('flow-previous')?'left':target.classList.contains('flow-next')?'right':backward?'left':'right';
    const desired={className:target.className,flowJump:target.dataset.flowJump,title:target.title};
    target.className=`flow-card flow-enter-${side}`;
    previousStage.append(target);
    changes.push([target,desired]);
  }
  previousStage.getBoundingClientRect();
  requestAnimationFrame(()=>{
    if(!previousStage.isConnected)return;
    for(const [card,target] of changes){
      card.className=target.className;
      if(target.flowJump)card.dataset.flowJump=target.flowJump;
      else card.removeAttribute('data-flow-jump');
      if(target.title)card.title=target.title;
      else card.removeAttribute('title');
    }
  });
  setTimeout(()=>{
    if(previousStage.isConnected)previousStage.querySelectorAll('.flow-exit-left,.flow-exit-right').forEach(card=>card.remove());
  },700);
}

function solidPlayGlyph(playing) {
  return playing?'<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M6 4h4v16H6zm8 0h4v16h-4z"/></svg>':'<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M6 3.5v17l14-8.5z"/></svg>';
}

function renderNowPlaying() {
  const item=state.current;
  if(!item){setPlayerPanelContent(empty('Nothing playing','Play a song or podcast episode to open Now Playing.'),'Now Playing');return}
  const previousStage=document.querySelector('.now-cover-stage');
  const podcast=item.kind==='podcast';
  const lyrics=podcast?null:lyricsCache.get(item.id);
  const lyricHtml=podcast?`<div class="lyrics-empty"><h3>Episode notes</h3><p>${esc(item.description||'No episode notes supplied by this podcast.')}</p></div>`:
    lyrics?.loading?'<div class="lyrics-empty"><h3>Finding lyrics…</h3><p>Checking your library and LRCLIB.</p></div>':
    lyrics?.synced?.length?lyrics.synced.map((line,i)=>`<button class="lyrics-line" data-lyric-time="${line.time}" data-line-index="${i}">${esc(line.text||'♪')}</button>`).join(''):
    lyrics?.plain?`<div class="plain-lyrics">${esc(lyrics.plain).replaceAll('\n','<br>')}</div>`:
    `<div class="lyrics-empty"><h3>Lyrics unavailable</h3><p>${esc(lyrics?.error||'Try adding an .lrc file beside this song, or search online again.')}</p><button class="outline-button" data-action="retry-lyrics">Find lyrics online</button>${state.user.role==='admin'?'<button class="outline-button" data-action="add-lyrics">Add lyrics</button>':''}</div>`;
  const previous=state.queue[state.queueIndex-1];
  const next=state.queue[nextQueueIndex()];
  const playGlyph=solidPlayGlyph(state.playing);
  setPlayerPanelContent(`<div class="now-playing"><div class="now-art-column"><div class="eyebrow">${podcast?'PODCAST EPISODE':'NOW PLAYING'}</div><div class="now-cover-stage coverflow">${flowCard(previous,'previous')}${flowCard(next,'next')}${flowCard(item,'current')}</div><div class="now-meta"><h1>${esc(item.title)}</h1><p>${esc(item.artist)}</p></div><div class="now-actions"><button class="outline-button now-toggle" data-action="now-toggle">${playGlyph}<span>${state.playing?'Pause':'Play'}</span></button><button class="outline-button" data-view="queue">${icon('queue')}<span>Queue</span></button>${!podcast&&state.user.role==='admin'?'<button class="outline-button" data-action="add-lyrics">Add lyrics</button>':''}</div></div><div class="lyrics-column"><div class="lyrics-heading"><div><div class="eyebrow">${podcast?'ABOUT THIS EPISODE':'SING ALONG'}</div><h2>${podcast?'Episode notes':'Lyrics'}</h2></div>${!podcast&&lyrics?.source?`<span class="lyrics-source">${esc(lyrics.source==='lrclib'?'LRCLIB':lyrics.source==='local'?'Local .lrc':lyrics.source==='manual'?'Your lyrics':'')}</span>`:''}</div><div class="lyrics-panel">${lyricHtml}</div></div></div>`,'Now Playing');
  transitionCoverFlow(previousStage);
  if(!podcast&&!lyrics)loadLyrics(item);
  syncLyrics();
}

function syncLyrics() {
  if(activePlayerPanel!=='nowplaying'||!state.current||state.current.kind==='podcast')return;
  const panel=document.querySelector('.lyrics-panel');
  const lines=lyricsCache.get(state.current.id)?.synced;
  if(!panel||!lines?.length)return;
  let active=-1;
  for(let i=0;i<lines.length;i++){if(lines[i].time<=audio.currentTime+.15)active=i;else break}
  if(panel.dataset.activeIndex===String(active))return;
  panel.dataset.activeIndex=String(active);
  panel.querySelectorAll('.lyrics-line').forEach((el,i)=>{el.classList.toggle('active',i===active);el.classList.toggle('passed',i<active)});
  const selected=panel.querySelector('.lyrics-line.active');
  if(selected)panel.scrollTo({top:selected.offsetTop-panel.offsetTop-panel.clientHeight/3,behavior:'smooth'});
}

function addLyricsModal() {
  const t=state.current;if(!t||t.kind==='podcast')return;
  const m=modal(`${modalHead('Add lyrics')}<p>Paste plain lyrics or timed LRC lines such as [00:12.00]First line. For automatic sing along highlighting, use timestamps.</p><form id="lyrics-form"><div class="field"><label>LYRICS</label><textarea name="lyrics" rows="12" required placeholder="[00:12.00]First line"></textarea></div><button class="primary-button" type="submit">Save lyrics</button></form>`);
  m.querySelector('form').onsubmit=async e=>{e.preventDefault();try{const data=await api(`/admin/tracks/${t.id}/lyrics`,{method:'PUT',body:{lyrics:e.target.elements.lyrics.value}});lyricsCache.set(t.id,data);m.remove();renderNowPlaying();notify('Lyrics saved')}catch(err){notify(err.message)}};
}

const coreHandleClick=handleClick;
handleClick=async function(e) {
  const el=e.target.closest('[data-episode-id],[data-podcast-id],[data-theme-choice],[data-lyric-time],[data-action]');
  try {
    if(el?.dataset.themeChoice){state.user=await api('/me/theme',{method:'PUT',body:{theme:el.dataset.themeChoice}});applyTheme();renderProfile();notify('Theme saved');return}
    if(el?.dataset.episodeId){const item=state.visibleEpisodes?.find(x=>x.id===Number(el.dataset.episodeId));if(item){const queue=state.visibleEpisodes.map(podcastTrack);playTrack(queue.find(x=>x.id===item.id),queue)}return}
    if(el?.dataset.podcastId&&!el.dataset.action){navigate('podcast',el.dataset.podcastId);return}
    if(el?.dataset.lyricTime){audio.currentTime=Number(el.dataset.lyricTime);return}
    if(el?.dataset.action==='remove-avatar'){state.user=await api('/me/avatar',{method:'DELETE'});refreshUserChip();renderProfile();notify('Profile picture removed');return}
    if(el?.dataset.action==='refresh-podcast'){el.disabled=true;await api(`/podcasts/${el.dataset.podcastId}/refresh`,{method:'POST'});navigate('podcast',el.dataset.podcastId,false);notify('Feed refreshed');return}
    if(el?.dataset.action==='unfollow-podcast'){await api(`/podcasts/${el.dataset.podcastId}/subscription`,{method:'DELETE'});navigate('podcasts');notify('Podcast unfollowed');return}
    if(el?.dataset.action==='retry-lyrics'){if(state.current?.kind!=='podcast'){lyricsCache.delete(state.current.id);loadLyrics(state.current,true);renderNowPlaying()}return}
    if(el?.dataset.action==='add-lyrics'){addLyricsModal();return}
    if(el?.dataset.action==='now-toggle'){togglePlay();return}
  } catch(err){notify(err.message);return}
  await coreHandleClick(e);
  if(!state.user)applyTheme();
};

accountModal=toggleProfileMenu;

audio.addEventListener('timeupdate',()=>{
  syncLyrics();
  if(state.current?.kind==='podcast'&&Date.now()-lastProgressSave>15000){lastProgressSave=Date.now();api(`/podcasts/episodes/${state.current.id}/progress`,{method:'PUT',body:{position:audio.currentTime}}).catch(()=>{})}
});
audio.addEventListener('play',syncNowToggle);
audio.addEventListener('pause',()=>{syncNowToggle();if(state.current?.kind==='podcast')api(`/podcasts/episodes/${state.current.id}/progress`,{method:'PUT',body:{position:audio.currentTime}}).catch(()=>{})});

renderArtists=async function() {
  const artists=await api('/artists');
  setContent(`<div class="page-header"><div><div class="eyebrow">THE PEOPLE BEHIND THE MUSIC</div><h1>Artists</h1><p>${artists.length} ${state.user.role==='admin'?'server':'followed'} artist${artists.length===1?'':'s'}</p></div></div><div class="page-actions"><button class="outline-button" data-view="albums">Albums</button><button class="outline-button" data-view="tracks">Songs</button>${state.user.role==='admin'?'<button class="outline-button" data-action="manage-artists">Manage artists</button>':''}</div>${artists.length?`<div class="cards artist-cards">${artists.map(a=>`<article class="card artist-card" data-artist-name="${esc(a.name)}"><div class="artist-card-image">${a.portrait_url?`<img src="${esc(a.portrait_url)}" alt="">`:a.cover_album_id?`<img src="/api/albums/${a.cover_album_id}/cover" alt="" onerror="this.style.display='none'">`:icon('user')}</div><div class="card-title">${esc(a.name)}</div><p class="card-sub">${a.album_count} release${a.album_count===1?'':'s'} · ${a.track_count} songs</p></article>`).join('')}</div>`:empty('No artists followed yet','Find artists in Search or Explore, then follow them for new music notifications.','<button class="outline-button" data-view="explore">Explore artists</button>')}`,'Artists');
};

renderArtist=async function(name) {
  const a=await api('/artists/'+encodeURIComponent(name));
  const all=[...a.albums,...a.singles];
  setContent(`<div class="artist-hero" ${a.banner_url?`style="background-image:linear-gradient(90deg,#090b0fe8,#090b0f55),url('${esc(a.banner_url)}')"`:''}><div class="artist-hero-portrait">${a.portrait_url?`<img src="${esc(a.portrait_url)}" alt="">`:icon('user')}</div><div class="artist-hero-copy"><div class="eyebrow">ARTIST</div><h1>${esc(a.name)}</h1><p>${a.album_count??all.length} release${all.length===1?'':'s'} · ${all.reduce((n,r)=>n+r.track_count,0)} songs</p>${a.bio?`<p class="artist-bio">${esc(a.bio)}</p>`:''}</div></div>
    <div class="page-actions"><button class="round-play" data-play-artist="${esc(a.name)}" ${all.length?'':'disabled'} title="Play artist">${icon('play')}</button><button class="outline-button follow-button ${a.followed?'is-followed':''}" data-follow-artist="${a.id}" aria-pressed="${a.followed}">${a.followed?'Following':'Follow artist'}</button>${state.user.role==='admin'?`<button class="outline-button" data-action="edit-artist" data-artist-id="${a.id}">Edit artist</button>`:''}</div>
    ${a.albums.length?`<section class="section"><h2 class="section-title">Albums</h2><div class="cards" style="margin-top:17px">${a.albums.map(albumCard).join('')}</div></section>`:''}
    ${a.singles.length?`<section class="section"><h2 class="section-title">Singles</h2><div class="cards" style="margin-top:17px">${a.singles.map(albumCard).join('')}</div></section>`:''}
    ${!all.length?empty('No releases yet','An admin can assign music to this artist in Admin Settings.'):''}`,'Artist');
};

const coreRenderSettings=renderSettings;
renderSettings=async function() {
  if(state.settingsTab==='artists')return renderArtistManager();
  await coreRenderSettings();
  document.querySelector('.settings-nav')?.insertAdjacentHTML('beforeend','<button data-settings-tab="artists">Artists</button>');
};

async function uploadArtistImage(artistId,kind,file) {
  if(file.size>6_000_000)throw new Error('Choose an image under 6 MB');
  const response=await fetch(`/api/admin/artists/${artistId}/image/${kind}`,{method:'PUT',credentials:'same-origin',headers:{'Content-Type':file.type},body:file});
  const data=await response.json();
  if(!response.ok)throw new Error(data.detail||'Upload failed');
  return data;
}

async function cropArtistImage(file,kind) {
  if(file.size>6_000_000)throw new Error('Choose an image under 6 MB');
  const source=URL.createObjectURL(file);
  const picture=new Image();
  try { await new Promise((resolve,reject)=>{picture.onload=resolve;picture.onerror=()=>reject(new Error('Could not open this image'));picture.src=source}); }
  finally { URL.revokeObjectURL(source); }
  const width=kind==='banner'?1200:600,height=kind==='banner'?400:600;
  return new Promise(resolve=>{
    const overlay=modal(`${modalHead(kind==='banner'?'Position cover image':'Crop portrait')}<p>Drag the image to position it. Use the slider to zoom.</p><div class="crop-stage"><canvas width="${width}" height="${height}" aria-label="Image crop preview"></canvas></div><div class="crop-zoom"><label for="crop-zoom">Zoom</label><input id="crop-zoom" type="range" min="1" max="3" value="1" step="0.01"></div><div class="crop-actions"><button class="outline-button" data-close-modal>Cancel</button><button class="primary-button" type="button" id="crop-save">Use photo</button></div>`);
    const canvas=overlay.querySelector('canvas'),ctx=canvas.getContext('2d');
    const slider=overlay.querySelector('#crop-zoom');
    let zoom=1,offsetX=0,offsetY=0,drag=null,done=false;
    const draw=()=>{
      const scale=Math.max(width/picture.naturalWidth,height/picture.naturalHeight)*zoom;
      const drawnWidth=picture.naturalWidth*scale,drawnHeight=picture.naturalHeight*scale;
      offsetX=Math.max((width-drawnWidth)/2,Math.min((drawnWidth-width)/2,offsetX));
      offsetY=Math.max((height-drawnHeight)/2,Math.min((drawnHeight-height)/2,offsetY));
      ctx.clearRect(0,0,width,height);
      ctx.drawImage(picture,(width-drawnWidth)/2+offsetX,(height-drawnHeight)/2+offsetY,drawnWidth,drawnHeight);
    };
    draw();
    slider.oninput=()=>{zoom=Number(slider.value);draw()};
    canvas.onpointerdown=e=>{drag={x:e.clientX,y:e.clientY,offsetX,offsetY};canvas.setPointerCapture(e.pointerId)};
    canvas.onpointermove=e=>{if(!drag)return;const rect=canvas.getBoundingClientRect();offsetX=drag.offsetX+(e.clientX-drag.x)*width/rect.width;offsetY=drag.offsetY+(e.clientY-drag.y)*height/rect.height;draw()};
    canvas.onpointerup=canvas.onpointercancel=()=>{drag=null};
    const finish=value=>{if(done)return;done=true;observer.disconnect();overlay.remove();resolve(value)};
    const observer=new MutationObserver(()=>{if(!overlay.isConnected)finish(null)});
    observer.observe(document.body,{childList:true});
    overlay.querySelector('#crop-save').onclick=()=>canvas.toBlob(blob=>{
      if(!blob){notify('Could not crop this image');return}
      finish(new File([blob],`${kind}.jpg`,{type:'image/jpeg'}));
    },'image/jpeg',0.9);
  });
}

async function renderArtistManager() {
  const artists=await api('/admin/artists');
  if(!artists.some(a=>a.id===state.adminArtistId))state.adminArtistId=artists[0]?.id||null;
  const artist=state.adminArtistId?await api(`/admin/artists/${state.adminArtistId}`):null;
  const albums=await api('/albums');
  const candidates=albums.filter(a=>a.artist_id!==state.adminArtistId);
  const tabs=`<div class="settings-nav"><button data-settings-tab="library">Music library</button><button data-settings-tab="users">Users & access</button><button data-settings-tab="metadata">Online metadata</button><button data-settings-tab="about">About Current</button><button data-settings-tab="artists" class="active">Artists</button></div>`;
  const panel=`<div class="settings-panel artist-manager"><h2>Artist profiles</h2><p>Profiles are created from album artist tags during scans. Edit artwork and names here, or move a release to another profile.</p>
    <div class="artist-manager-toolbar"><div class="field"><label>EDIT ARTIST</label><select id="admin-artist-select">${artists.map(a=>`<option value="${a.id}" ${a.id===state.adminArtistId?'selected':''}>${esc(a.name)} (${a.album_count})</option>`).join('')}</select></div><form id="new-artist-form"><div class="field"><label>NEW ARTIST</label><input name="name" placeholder="Artist name" required maxlength="200"></div><button class="outline-button" type="submit">Create profile</button></form></div>
    ${artist?`<div class="artist-image-editor"><div class="artist-image-preview portrait">${artist.portrait_url?`<img src="${esc(artist.portrait_url)}" alt="Artist portrait">`:icon('user')}</div><div class="artist-image-preview banner">${artist.banner_url?`<img src="${esc(artist.banner_url)}" alt="Artist cover image">`:'<span>Wide cover image</span>'}</div></div>
      <div class="artist-upload-row"><form data-artist-upload="portrait"><label>PORTRAIT <input type="file" accept="image/png,image/jpeg,image/webp" required></label><button class="outline-button" type="submit">Upload portrait</button>${artist.portrait_url?'<button class="outline-button" type="button" data-remove-artist-image="portrait">Remove</button>':''}</form><form data-artist-upload="banner"><label>COVER IMAGE <input type="file" accept="image/png,image/jpeg,image/webp" required></label><button class="outline-button" type="submit">Upload cover</button>${artist.banner_url?'<button class="outline-button" type="button" data-remove-artist-image="banner">Remove</button>':''}</form></div>
      <form id="artist-profile-form"><div class="field"><label>ARTIST NAME</label><input name="name" value="${esc(artist.name)}" required maxlength="200"></div><div class="field"><label>BIO</label><textarea name="bio" rows="4" maxlength="2000" placeholder="A little about this artist">${esc(artist.bio)}</textarea></div><button class="primary-button" type="submit">Save artist</button></form>
      <h3>Albums & singles</h3><p>Choose how each release appears on this artist's profile.</p><div class="artist-release-list">${artist.albums.length?artist.albums.map(a=>`<div class="artist-release-row">${miniCover(a)}<div><strong>${esc(a.title)}</strong><small>${a.track_count} song${a.track_count===1?'':'s'} · tagged as ${esc(a.source_artist||a.artist)}</small></div><select data-release-type="${a.id}"><option value="album" ${(a.release_type||(a.track_count===1?'single':'album'))==='album'?'selected':''}>Album</option><option value="single" ${(a.release_type||(a.track_count===1?'single':'album'))==='single'?'selected':''}>Single</option></select><button class="outline-button" data-save-release="${a.id}">Save</button></div>`).join(''):'<div class="hint">No releases assigned yet.</div>'}</div>
      <h3>Add an existing release</h3><form id="assign-release-form" class="assign-release-form"><div class="field"><label>RELEASE</label><select name="album_id" required>${candidates.map(a=>`<option value="${a.id}">${esc(a.title)} — ${esc(a.artist)}</option>`).join('')}</select></div><div class="field"><label>TYPE</label><select name="release_type"><option value="album">Album</option><option value="single">Single</option></select></div><button class="primary-button" type="submit" ${candidates.length?'':'disabled'}>Add to artist</button></form>`:'<p>Create a profile above, then add artwork and releases.</p>'}
  </div>`;
  setContent(`<div class="page-header"><div><div class="eyebrow">ADMIN CONTROL</div><h1>Artists</h1><p>Manage names, images and releases.</p></div></div><div class="settings-grid">${tabs}${panel}</div>`,'Admin Settings');
  const selector=document.getElementById('admin-artist-select');if(selector)selector.onchange=e=>{state.adminArtistId=Number(e.target.value);renderArtistManager()};
  document.getElementById('new-artist-form').onsubmit=async e=>{e.preventDefault();try{const created=await api('/admin/artists',{method:'POST',body:{name:e.target.elements.name.value}});state.adminArtistId=created.id;renderArtistManager();notify('Artist profile created')}catch(err){notify(err.message)}};
  if(!artist)return;
  document.getElementById('artist-profile-form').onsubmit=async e=>{e.preventDefault();try{await api(`/admin/artists/${artist.id}`,{method:'PUT',body:{name:e.target.elements.name.value,bio:e.target.elements.bio.value}});renderArtistManager();notify('Artist saved')}catch(err){notify(err.message)}};
  document.querySelectorAll('[data-artist-upload]').forEach(form=>form.onsubmit=async e=>{e.preventDefault();const file=form.querySelector('input[type=file]').files[0];if(!file)return;try{const cropped=await cropArtistImage(file,form.dataset.artistUpload);if(!cropped)return;await uploadArtistImage(artist.id,form.dataset.artistUpload,cropped);renderArtistManager();notify('Artist image saved')}catch(err){notify(err.message)}});
  document.getElementById('assign-release-form').onsubmit=async e=>{e.preventDefault();try{await api(`/admin/albums/${e.target.elements.album_id.value}/artist`,{method:'PUT',body:{artist_id:artist.id,release_type:e.target.elements.release_type.value}});renderArtistManager();notify('Release assigned')}catch(err){notify(err.message)}};
}

const featureHandleClick=handleClick;
let librarySummaryRequest=0;

async function refreshLibrarySummary(){
  if(!state.user)return;
  const request=++librarySummaryRequest;
  const data=await api('/home');
  if(request!==librarySummaryRequest||!state.user)return;
  state.home=data;
  const count=data.counts.tracks;
  const pill=document.getElementById('library-count');
  if(pill)pill.textContent=`${count} ${state.user.role==='admin'?'SERVER':'SAVED'} SONG${count===1?'':'S'}`;
}

function updateTrackSave(id,saved){
  for(const track of state.visibleTracks||[]){if(track.id===id){track.saved_track=saved;track.saved=saved||!!track.saved_album}}
  for(const track of state.queue||[]){if(track.id===id){track.saved_track=saved;track.saved=saved||!!track.saved_album}}
  if(state.current?.id===id){state.current.saved_track=saved;state.current.saved=saved||!!state.current.saved_album}
  document.querySelectorAll(`[data-library-track="${id}"]`).forEach(button=>{
    const title=button.closest('tr')?.querySelector('.track-line-text strong')?.textContent||'song';
    button.classList.toggle('is-saved',saved);
    button.setAttribute('aria-pressed',String(saved));
    button.setAttribute('aria-label',`${saved?'Remove saved song':'Save song'}: ${title}`);
    button.title=saved?'Remove saved song':'Save song';
    button.innerHTML=icon(saved?'check':'plus');
  });
}

function updateAlbumSave(id,saved){
  for(const album of [...(state.home?.albums||[]),...(state.home?.top_albums||[])]){if(album.id===id)album.saved=saved}
  for(const track of state.visibleTracks||[]){if(track.album_id===id){track.saved_album=saved;track.saved=!!track.saved_track||saved}}
  for(const track of state.queue||[]){if(track.album_id===id){track.saved_album=saved;track.saved=!!track.saved_track||saved}}
  if(state.current?.album_id===id){state.current.saved_album=saved;state.current.saved=!!state.current.saved_track||saved}
  document.querySelectorAll(`[data-library-album="${id}"]`).forEach(button=>{
    button.classList.toggle('is-saved',saved);
    button.setAttribute('aria-pressed',String(saved));
    if(button.classList.contains('card-save')){
      const title=button.closest('.card')?.querySelector('.card-title')?.textContent||'album';
      button.setAttribute('aria-label',`${saved?'Remove':'Add'} ${title} ${saved?'from':'to'} your library`);
      button.title=saved?'Remove from your library':'Add to your library';
      button.innerHTML=icon(saved?'check':'plus');
    } else button.textContent=saved?'Remove from library':'Add to library';
  });
}

function removeUnsavedTrackFromList(button,id){
  if(state.view!=='tracks'||state.user.role==='admin')return;
  const track=state.visibleTracks?.find(item=>item.id===id);
  if(track?.saved)return;
  const row=button.closest('tr');if(!row)return;
  const next=row.nextElementSibling?.querySelector('[data-library-track]')||row.previousElementSibling?.querySelector('[data-library-track]');
  row.remove();
  state.visibleTracks=state.visibleTracks.filter(item=>item.id!==id);
  const count=state.visibleTracks.length;
  document.querySelector('.page-header p').textContent=`${count} song${count===1?'':'s'} in your library`;
  if(!count)document.querySelector('.track-table')?.replaceWith(document.createRange().createContextualFragment(empty('No saved songs yet','Find songs in Search or Explore and save them to your library.')));
  next?.focus({preventScroll:true});
}

function removeUnsavedAlbumFromList(button){
  if(state.view!=='albums'||state.user.role==='admin')return;
  const card=button.closest('.card');if(!card)return;
  const next=card.nextElementSibling?.querySelector('[data-library-album]')||card.previousElementSibling?.querySelector('[data-library-album]');
  const grid=card.parentElement;card.remove();
  const count=grid.querySelectorAll('.card').length;
  document.querySelector('.page-header p').textContent=`${count} album${count===1?'':'s'} in your library`;
  if(!count)grid.replaceWith(document.createRange().createContextualFragment(empty('No albums yet','Search or Explore to add an album to your library.')));
  next?.focus({preventScroll:true});
}

handleClick=async function(e) {
  const el=e.target.closest('[data-action],[data-save-release],[data-remove-artist-image],[data-play-artist],[data-track-menu],[data-episode-menu],[data-queue-remove],[data-flow-jump],[data-library-track],[data-library-album],[data-follow-artist],[data-notification-id],[data-notification-action]');
  try {
    if(el?.dataset.libraryTrack){
      e.stopPropagation();const id=Number(el.dataset.libraryTrack),saved=el.classList.contains('is-saved');el.disabled=true;
      try {await api(`/library/tracks/${id}`,{method:saved?'DELETE':'PUT'});updateTrackSave(id,!saved);if(saved)removeUnsavedTrackFromList(el,id);notify(saved?'Song removed from saved songs':'Song saved to your library');refreshLibrarySummary().catch(()=>{})}
      finally {el.disabled=false}
      return;
    }
    if(el?.dataset.libraryAlbum){
      e.stopPropagation();const id=Number(el.dataset.libraryAlbum),saved=el.classList.contains('is-saved');el.disabled=true;
      try {await api(`/library/albums/${id}`,{method:saved?'DELETE':'PUT'});updateAlbumSave(id,!saved);if(saved)removeUnsavedAlbumFromList(el);notify(saved?'Album removed from your library':'Album added to your library');refreshLibrarySummary().catch(()=>{})}
      finally {el.disabled=false}
      return;
    }
    if(el?.dataset.followArtist){
      e.stopPropagation();const followed=el.classList.contains('is-followed');el.disabled=true;
      try {await api(`/artists/${el.dataset.followArtist}/follow`,{method:followed?'DELETE':'PUT'});el.classList.toggle('is-followed',!followed);el.setAttribute('aria-pressed',String(!followed));el.textContent=followed?'Follow artist':'Following';notify(followed?'Artist unfollowed':'Following artist — new songs will appear in notifications')}
      finally {el.disabled=false}
      return;
    }
    if(el?.dataset.notificationAction==='read-all'){e.stopPropagation();await api('/notifications/read-all',{method:'PUT'});refreshNotifications();return}
    if(el?.dataset.notificationId){e.stopPropagation();await api(`/notifications/${el.dataset.notificationId}/read`,{method:'PUT'});document.querySelector('.notification-dropdown').hidden=true;document.querySelector('.notification-bell').setAttribute('aria-expanded','false');refreshNotifications();navigate('album',el.dataset.albumId);return}
    if(el?.dataset.trackMenu){e.stopPropagation();const id=Number(el.dataset.trackMenu);const item=state.visibleTracks?.find(t=>t.id===id)||((await api('/tracks')).find(t=>t.id===id));if(item){const rect=el.getBoundingClientRect();showQueueMenu(item,rect.left,rect.bottom+5)}return}
    if(el?.dataset.episodeMenu){e.stopPropagation();const episode=state.visibleEpisodes?.find(t=>t.id===Number(el.dataset.episodeMenu));if(episode){const rect=el.getBoundingClientRect();showQueueMenu(podcastTrack(episode),rect.left,rect.bottom+5)}return}
    if(el?.dataset.queueRemove){state.queue.splice(Number(el.dataset.queueRemove),1);state.randomNextIndex=null;renderQueue();notify('Removed from queue');return}
    if(el?.dataset.flowJump){advance(el.dataset.flowJump==='next'?1:-1);return}
    if(el?.dataset.action==='manage-artists'){state.settingsTab='artists';navigate('settings');return}
    if(el?.dataset.action==='edit-artist'){state.settingsTab='artists';state.adminArtistId=Number(el.dataset.artistId);navigate('settings');return}
    if(el?.dataset.saveRelease){const select=document.querySelector(`[data-release-type="${el.dataset.saveRelease}"]`);await api(`/admin/albums/${el.dataset.saveRelease}/artist`,{method:'PUT',body:{artist_id:state.adminArtistId,release_type:select.value}});renderArtistManager();notify('Release type saved');return}
    if(el?.dataset.removeArtistImage){await api(`/admin/artists/${state.adminArtistId}/image/${el.dataset.removeArtistImage}`,{method:'DELETE'});renderArtistManager();notify('Artist image removed');return}
    if(el?.dataset.playArtist){const a=await api('/artists/'+encodeURIComponent(el.dataset.playArtist));let songs=[];for(const release of [...a.albums,...a.singles]){const detail=await api(`/albums/${release.id}`);songs.push(...detail.tracks)}if(songs.length)playTrack(songs[0],songs);return}
  } catch(err){notify(err.message);return}
  await featureHandleClick(e);
};

start();
