/* Orb Messenger PWA. Vanilla JS, four screens, SSE for live state. API: docs/hub-api.md */
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const S = {
  me: null, users: [], orbs: [], stickers: [], phrases: [], messages: [], historyDone: false,
  tab: location.hash.slice(1) || 'send', to: new Set(), toTouched: false, sticker: null, media: null,
  stickerMode: 'emoji', seenPending: new Set(),
};

// ---------------------------------------------------------------- api
class ApiError extends Error { constructor(status, code, detail) { super(detail || code); this.status = status; this.code = code; } }
async function api(method, path, body, opts = {}) {
  const init = { method, credentials: 'same-origin', headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) { init.headers['content-type'] = 'application/json'; init.body = JSON.stringify(body); }
  let r;
  try { r = await fetch(path, init); }
  catch { throw new ApiError(0, 'offline', "Can't reach the hub. Are you on the home Wi-Fi?"); }
  if (r.status === 401 && !opts.noAuthRedirect) { showLogin(); throw new ApiError(401, 'unauthorized', 'signed out'); }
  if (r.status === 204) return null;
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, data.error || 'error', typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || ''));
  return data;
}

// ---------------------------------------------------------------- helpers
let toastTimer;
function toast(text, ms = 2200) {
  const t = $('#toast'); t.textContent = text; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}
function hhmm(ts) { const d = new Date(ts * 1000); return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`; }
function dayLabel(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const start = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((start(now) - start(d)) / 86400000);
  if (diff === 0) return 'Today'; if (diff === 1) return 'Yesterday';
  return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
}
function ago(ts) {
  if (!ts) return 'never'; const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return 'just now'; if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`; return `${Math.floor(s / 86400)} d ago`;
}
function userName(id) { if (!id) return null; if (S.me && id === S.me.id) return 'you'; const u = S.users.find(u => u.id === id); return u ? u.name : id; }
function orbName(id) { const o = S.orbs.find(o => o.id === id); return o ? (o.name || o.id) : id; }
function otherUser() { return S.users.find(u => u.id !== S.me.id); }
function ownerBadge(owner) {
  if (!owner) return '<span class="badge">shared</span>';
  if (owner === S.me.id) return '<span class="badge mine">yours</span>';
  const u = S.users.find(u => u.id === owner); return `<span class="badge">${esc(u ? u.name + "'s" : owner)}</span>`;
}
function openSheet(html) { $('#sheet').innerHTML = html; $('#sheet-backdrop').hidden = false; return $('#sheet'); }
function closeSheet() { $('#sheet-backdrop').hidden = true; }
$('#sheet-backdrop').addEventListener('click', e => { if (e.target === e.currentTarget) closeSheet(); });

// ---------------------------------------------------------------- tabs
function showTab(tab) {
  S.tab = tab; location.hash = tab;
  $$('.screen').forEach(s => { s.hidden = s.id !== `tab-${tab}`; });
  $$('#nav button').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
  if (tab === 'orbs') { $('#orbs-dot').hidden = true; S.orbs.filter(o => o.state === 'pending').forEach(o => S.seenPending.add(o.id)); }
  window.scrollTo(0, 0);
}
$$('#nav button').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));

// ---------------------------------------------------------------- login
async function showLogin() {
  $('#app').hidden = true; $('#login').hidden = false;
  const err = $('#login-error');
  while (!S.users.length) {
    try { S.users = await api('GET', '/api/users', undefined, { noAuthRedirect: true }); err.hidden = true; }
    catch (ex) { err.textContent = ex.message; err.hidden = false; await new Promise(r => setTimeout(r, 4000)); }
  }
  const wrap = $('#login-users');
  if (!wrap.children.length) {
    wrap.innerHTML = S.users.map((u, i) => `<button type="button" class="${i === 0 ? 'on' : ''}" data-id="${esc(u.id)}">${esc(u.name)}</button>`).join('');
    const remembered = localStorage.getItem('orb.user');
    if (remembered) $$('button', wrap).forEach(b => b.classList.toggle('on', b.dataset.id === remembered));
    wrap.addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; $$('button', wrap).forEach(x => x.classList.toggle('on', x === b)); });
  }
  $('#login-pin').focus();
}
$('#login-form').addEventListener('submit', async e => {
  e.preventDefault();
  const user = $('#login-users button.on')?.dataset.id, pin = $('#login-pin').value;
  const err = $('#login-error'); err.hidden = true;
  try {
    S.me = await api('POST', '/api/session', { user, pin }, { noAuthRedirect: true });
    localStorage.setItem('orb.user', user); $('#login-pin').value = '';
    await boot();
  } catch (ex) { err.textContent = ex.code === 'bad_login' ? 'Wrong PIN' : ex.message; err.hidden = false; $('#login-form').classList.add('shake'); setTimeout(() => $('#login-form').classList.remove('shake'), 500); }
});

// ---------------------------------------------------------------- boot + live updates
async function boot() {
  $('#login').hidden = true; $('#app').hidden = false;
  [S.users, S.orbs, S.stickers, S.phrases, S.firmware, S.moods] = await Promise.all([api('GET', '/api/users'), api('GET', '/api/orbs'), api('GET', '/api/stickers'), api('GET', '/api/phrases'), api('GET', '/api/firmware'), api('GET', '/api/moods')]);
  S.orbs.filter(o => o.state === 'pending').forEach(o => S.seenPending.add(o.id));
  await loadHistory(true);
  renderAll(); showTab(S.tab); connectEvents();
}
function renderAll() { renderSend(); renderHistory(); renderOrbs(); renderStickers(); }

let es;
function connectEvents() {
  if (es) es.close();
  es = new EventSource('/api/events');
  const pill = $('#hub-pill');
  es.onopen = () => { pill.textContent = 'hub online'; pill.classList.add('on'); };
  es.onerror = () => { pill.textContent = 'reconnecting…'; pill.classList.remove('on'); };
  const refreshOrbs = async () => { [S.orbs, S.firmware] = await Promise.all([api('GET', '/api/orbs'), api('GET', '/api/firmware')]); renderSend(); renderOrbs(); renderHistory(); };
  es.addEventListener('firmware.changed', refreshOrbs);
  es.addEventListener('orb.pending', async e => {
    await refreshOrbs(); const { id } = JSON.parse(e.data);
    if (S.tab !== 'orbs' && !S.seenPending.has(id)) { $('#orbs-dot').hidden = false; toast('New orb found: open Orbs to approve it', 4000); }
  });
  es.addEventListener('orb.status', refreshOrbs);
  es.addEventListener('orb.updated', refreshOrbs);
  const refreshHistory = async () => { await loadHistory(true); renderHistory(); S.orbs = await api('GET', '/api/orbs'); renderOrbs(); };
  ['message.created', 'message.delivered', 'message.read'].forEach(n => es.addEventListener(n, refreshHistory));
}

// ---------------------------------------------------------------- SEND
function activeOrbs() { return S.orbs.filter(o => o.state === 'active'); }
function defaultTo() {
  const act = activeOrbs(); const theirs = act.filter(o => o.owner && o.owner !== S.me.id);
  return new Set((theirs.length ? theirs : act).map(o => o.id));
}
function renderSend() {
  const act = activeOrbs();
  if (!S.toTouched) S.to = defaultTo(); else S.to = new Set([...S.to].filter(id => act.some(o => o.id === id)));
  $('#to-chips').innerHTML = act.length ? act.map(o =>
    `<button type="button" class="chip ${S.to.has(o.id) ? 'on' : ''} ${o.online ? '' : 'offline'}" data-id="${esc(o.id)}">${esc(o.name)}${o.online ? '' : ' · offline'}</button>`).join('')
    : '<span class="muted">No approved orbs yet. Pair one under Orbs.</span>';
  $('#phrases').innerHTML = S.phrases.map(p => `<button type="button" class="chip" data-phrase="${esc(p)}">${esc(p)}</button>`).join('');
  $('#mood-chips').innerHTML = (S.moods || []).map(m =>
    `<button type="button" class="chip ${S.mood === m.key ? 'on' : ''}" data-mood="${esc(m.key)}">${m.emoji} ${esc(m.label)}</button>`).join('');
  $('#sticker-grid').innerHTML = S.stickers.map(s =>
    `<button type="button" class="stk ${S.sticker === s.key ? 'on' : ''}" data-key="${esc(s.key)}" title="${esc(s.name)}"><img src="${esc(s.url)}" alt="${esc(s.name)}"></button>`).join('')
    + '<button type="button" class="stk add" data-add>+</button>';
  updateSendButton();
}
function updateSendButton() {
  const n = S.to.size, has = $('#text').value.trim() || S.sticker || S.mood || S.media;
  const b = $('#send-btn'); b.textContent = n ? `Send to ${n} orb${n === 1 ? '' : 's'}` : 'Pick an orb'; b.disabled = !n || !has;
  $('#count').textContent = `${$('#text').value.length}/80`;
}
$('#to-chips').addEventListener('click', e => {
  const c = e.target.closest('.chip'); if (!c) return; S.toTouched = true;
  S.to.has(c.dataset.id) ? S.to.delete(c.dataset.id) : S.to.add(c.dataset.id); renderSend();
});
$('#phrases').addEventListener('click', e => { const c = e.target.closest('.chip'); if (!c) return; $('#text').value = c.dataset.phrase; updateSendButton(); });
$('#text').addEventListener('input', updateSendButton);
$('#sticker-grid').addEventListener('click', e => {
  const b = e.target.closest('.stk'); if (!b) return;
  if (b.dataset.add !== undefined) { showTab('stickers'); return; }
  S.sticker = S.sticker === b.dataset.key ? null : b.dataset.key; if (S.sticker) S.mood = null; renderSend();
});
$('#mood-chips').addEventListener('click', e => {
  const b = e.target.closest('[data-mood]'); if (!b) return;
  S.mood = S.mood === b.dataset.mood ? null : b.dataset.mood; if (S.mood) S.sticker = null; renderSend();
});
try { $('#auto-open').checked = localStorage.getItem('orb.autoOpen') === '1'; } catch {}
$('#auto-open').addEventListener('change', e => { try { localStorage.setItem('orb.autoOpen', e.target.checked ? '1' : '0'); } catch {} });
$('#photo-btn').addEventListener('click', () => $('#photo-input').click());
const photoBtnHtml = $('#photo-btn').innerHTML;
async function uploadPhoto(f) {
  if (!f || !f.type.startsWith('image/')) return;
  $('#photo-btn').disabled = true; $('#photo-btn').textContent = 'Uploading…';
  try {
    const fd = new FormData(); fd.append('file', f, f.name || 'pasted.png');
    S.media = await api('POST', '/api/media', fd);
    $('#photo-preview img').src = S.media.url; $('#photo-preview').hidden = false;
  } catch (ex) { toast(`Photo rejected: ${ex.message}`, 4000); }
  finally { $('#photo-btn').disabled = false; $('#photo-btn').innerHTML = photoBtnHtml; updateSendButton(); }
}
$('#photo-input').addEventListener('change', e => { uploadPhoto(e.target.files[0]); e.target.value = ''; });
// An image pasted (or dropped) anywhere on the Send tab becomes the photo to send.
document.addEventListener('paste', e => {
  if (S.tab !== 'send' || !$('#login').hidden) return;
  const item = [...(e.clipboardData?.items || [])].find(i => i.kind === 'file' && i.type.startsWith('image/'));
  if (!item) return;
  e.preventDefault(); uploadPhoto(item.getAsFile());
});
document.addEventListener('dragover', e => { if (S.tab === 'send') e.preventDefault(); });
document.addEventListener('drop', e => {
  if (S.tab !== 'send') return;
  const f = [...(e.dataTransfer?.files || [])].find(x => x.type.startsWith('image/'));
  if (!f) return;
  e.preventDefault(); uploadPhoto(f);
});
$('#photo-remove').addEventListener('click', () => { S.media = null; $('#photo-preview').hidden = true; updateSendButton(); });
$('#send-btn').addEventListener('click', async () => {
  const body = { to: [...S.to], text: $('#text').value.trim() || undefined, sticker: S.sticker || undefined, mood: S.mood || undefined, media_id: S.media?.id, open: $('#auto-open').checked || undefined };
  $('#send-btn').disabled = true;
  try {
    const r = await api('POST', '/api/messages', body);
    toast(`Sent to ${r.deliveries.length} orb${r.deliveries.length === 1 ? '' : 's'}`);
    $('#text').value = ''; S.sticker = null; S.mood = null; S.media = null; $('#photo-preview').hidden = true;
    renderSend(); await loadHistory(true); renderHistory();
  } catch (ex) { toast(`Not sent: ${ex.message}`, 4000); updateSendButton(); }
});

// ---------------------------------------------------------------- HISTORY
async function loadHistory(reset) {
  const before = reset || !S.messages.length ? '' : `&before=${S.messages[S.messages.length - 1].id}`;
  const page = await api('GET', `/api/messages?limit=50${before}`);
  S.messages = reset ? page : S.messages.concat(page); S.historyDone = page.length < 50;
}
$('#history-more').addEventListener('click', async () => { await loadHistory(false); renderHistory(); });
function myOrbIds() { return new Set(S.orbs.filter(o => o.owner === S.me.id).map(o => o.id)); }
function renderHistory() {
  const mine = myOrbIds(); let day = null; const out = [];
  for (const m of S.messages) {
    const d = dayLabel(m.ts); if (d !== day) { out.push(`<div class="day">${esc(d)}</div>`); day = d; }
    const isMine = m.from === S.me.id;
    const toMe = m.deliveries.filter(x => mine.has(x.orb));
    const unread = !isMine && toMe.length > 0 && toMe.every(x => x.status !== 'read');
    let bubble;
    if (m.type === 'photo') bubble = `<div class="bubble photo"><img src="${esc(m.media_url)}" alt="photo" loading="lazy"></div>${m.text ? `<div class="bubble" style="margin-top:8px">${esc(m.text)}</div>` : ''}`;
    else if (m.type === 'mood') { const md = (S.moods || []).find(x => x.key === m.mood); bubble = `<div class="bubble">${md ? md.emoji + ' ' : ''}feels ${esc(md ? md.label.toLowerCase() : m.mood)}${m.text ? `<br>${esc(m.text)}` : ''}</div>`; }
    else if (m.type === 'sticker') bubble = `<div class="bubble ${m.text ? '' : 'sticker-only'}">${m.text ? esc(m.text) : ''}<img class="stk-img" src="${esc(m.media_url)}" alt="${esc(m.sticker)}"></div>`;
    else bubble = `<div class="bubble">${esc(m.text)}</div>`;
    if (m.open) bubble += '<div class="muted small" style="margin-top:4px">opened on arrival</div>';
    let meta, cls = '';
    if (isMine) {
      const read = m.deliveries.filter(x => x.status === 'read'), del = m.deliveries.filter(x => x.status !== 'queued');
      const targets = m.deliveries.length === 1 ? orbName(m.deliveries[0].orb) : `${m.deliveries.length} orbs`;
      if (read.length === m.deliveries.length) { cls = 'read'; meta = `<span class="ticks">✓✓</span>${esc(targets)} · read ${hhmm(Math.max(...read.map(x => x.read_at)))}`; }
      else if (del.length) meta = `<span class="ticks">✓</span>${esc(targets)} · delivered ${hhmm(Math.max(...del.map(x => x.delivered_at)))} · not read yet`;
      else meta = `${esc(targets)} · ${hhmm(m.ts)} · queued, orb offline`;
      if (m.reaction_to) meta = `you reacted · ${hhmm(m.ts)}`;
    } else {
      const dest = m.deliveries.map(x => orbName(x.orb)).join(', ');
      const state = unread ? 'unread' : (toMe.some(x => x.status === 'read') ? 'you tapped it' : (m.deliveries.some(x => x.status === 'read') ? 'read' : hhmmState(m)));
      cls = unread ? 'unread' : '';
      meta = `${esc(m.from_name)} → ${esc(dest)} · ${hhmm(m.ts)} · ${state}`;
      if (m.reaction_to) meta = `${esc(m.from_name)} reacted · ${hhmm(m.ts)}`;
    }
    out.push(`<div class="msg ${isMine ? 'mine' : ''} ${unread ? 'unread' : ''}">${bubble}<div class="meta ${cls}">${meta}</div></div>`);
  }
  $('#history').innerHTML = out.join('') || '<p class="muted">Nothing yet. Send something!</p>';
  $('#history-more').hidden = S.historyDone;
}
function hhmmState(m) { return m.deliveries.some(x => x.status === 'delivered') ? 'on the orb' : 'queued'; }

// ---------------------------------------------------------------- ORBS
function renderOrbs() {
  const pending = S.orbs.filter(o => o.state === 'pending'), paired = S.orbs.filter(o => o.state === 'active');
  const other = otherUser();
  $('#pending').innerHTML = pending.map(o => `
    <form class="card pending" data-id="${esc(o.id)}">
      <h3>New orb found</h3>
      <div class="id">${esc(o.id)} · fw ${esc(o.fw)}${o.online ? '' : ' · offline'}</div>
      <div>Type the code showing on its screen</div>
      <div class="code">${[0, 1, 2, 3].map(i => `<input inputmode="numeric" pattern="[0-9]*" maxlength="1" autocomplete="one-time-code" aria-label="digit ${i + 1}">`).join('')}</div>
      <label class="field"><span>Name</span><input name="name" maxlength="32" placeholder="Living room" required></label>
      <span class="field"><span>Whose is it?</span></span>
      <div class="seg owner"><button type="button" class="on" data-owner="${esc(S.me.id)}">Mine</button>${other ? `<button type="button" data-owner="${esc(other.id)}">${esc(other.name)}'s</button>` : ''}<button type="button" data-owner="">Shared</button></div>
      <p class="error" hidden></p>
      <div class="actions"><button type="button" class="secondary" data-ignore>Ignore</button><button class="primary" type="submit">Approve</button></div>
    </form>`).join('');
  $('#paired').innerHTML = paired.map(o => {
    const st = o.online ? `online${o.unread ? ' · 1 unread' : ` · fw ${esc(o.fw)}`}` : `offline · last seen ${ago(o.last_seen)}`;
    return `<div class="orb-row" data-id="${esc(o.id)}"><div class="avatar ${o.online ? 'online' : 'offline'}"></div><div><div class="t">${esc(o.name)} ${ownerBadge(o.owner)}</div><div class="s ${o.online ? 'online' : ''}">${st}</div></div></div>`;
  }).join('');
  $('#no-orbs').hidden = S.orbs.length > 0;
  $$('.code', $('#pending')).forEach(wireCode);
  renderFirmware(paired);
}
function wireCode(box) {
  const inputs = $$('input', box);
  inputs.forEach((inp, i) => {
    inp.addEventListener('input', () => {
      const v = inp.value.replace(/\D/g, '');
      if (v.length > 1) { v.split('').slice(0, 4 - i).forEach((ch, k) => { inputs[i + k].value = ch; }); inputs[Math.min(3, i + v.length)].focus(); return; }
      inp.value = v; if (v && i < 3) inputs[i + 1].focus();
    });
    inp.addEventListener('keydown', e => { if (e.key === 'Backspace' && !inp.value && i > 0) inputs[i - 1].focus(); });
  });
}
$('#pending').addEventListener('click', e => {
  const seg = e.target.closest('.seg.owner button'); if (seg) { $$('button', seg.parentElement).forEach(b => b.classList.toggle('on', b === seg)); return; }
  const ign = e.target.closest('[data-ignore]'); if (ign) { const f = ign.closest('form'); api('POST', `/api/orbs/${f.dataset.id}/ignore`).then(() => toast('Ignored')).catch(ex => toast(ex.message)); }
});
$('#pending').addEventListener('submit', async e => {
  e.preventDefault(); const f = e.target, err = $('.error', f);
  const code = $$('.code input', f).map(i => i.value).join(''), name = f.name.value.trim(), owner = $('.seg.owner button.on', f).dataset.owner || null;
  err.hidden = true;
  if (code.length !== 4) { err.textContent = 'Type all four digits'; err.hidden = false; return; }
  try { await api('POST', `/api/orbs/${f.dataset.id}/approve`, { code, name, owner }); toast(`${name} paired`); }
  catch (ex) {
    err.textContent = ex.code === 'bad_code' ? "That's not the code on the orb" : ex.code === 'code_expired' ? 'Code expired, the orb shows a new one' : ex.message;
    err.hidden = false; f.classList.add('shake'); setTimeout(() => f.classList.remove('shake'), 500);
    $$('.code input', f).forEach(i => { i.value = ''; }); $('.code input', f).focus();
  }
});
function renderFirmware(paired) {
  const list = $('#fw-list');
  if (!(S.firmware || []).length) { list.textContent = 'Nothing uploaded yet. Orbs run what was flashed over USB.'; }
  else {
    list.innerHTML = S.firmware.map(f => {
      const orbs = paired.filter(o => o.model === f.model);
      const behind = orbs.filter(o => o.fw !== f.version);
      const st = !orbs.length ? 'no orbs of this model' : behind.length ? `${behind.length} of ${orbs.length} still on another version` : `all ${orbs.length} orb${orbs.length > 1 ? 's' : ''} on it`;
      return `<div class="fw-row"><div><b>${esc(f.model)}</b> ${esc(f.version)} · ${Math.round(f.size / 1024)} KB · ${st}</div><button type="button" class="link" data-fw-del="${esc(f.model)}">remove</button></div>`;
    }).join('');
  }
  const form = $('#fw-form'), m = form.elements.model;
  if (!m.value && paired[0]?.model) m.value = paired[0].model;
}
$('#fw-list').addEventListener('click', async e => {
  const b = e.target.closest('[data-fw-del]'); if (!b) return;
  try { await api('DELETE', `/api/firmware/${b.dataset.fwDel}`); toast('Firmware removed; orbs keep what they run'); } catch (ex) { toast(ex.message); }
});
$('#fw-form').addEventListener('submit', async e => {
  e.preventDefault();
  const form = e.currentTarget, err = $('.error', form), btn = $('button[type=submit]', form);
  err.hidden = true; btn.disabled = true; btn.textContent = 'Uploading…';
  try {
    const fd = new FormData(form);
    const fw = await api('POST', '/api/firmware', fd);
    toast(`${fw.model} ${fw.version} rolling out`); form.elements.file.value = ''; form.elements.version.value = '';
  } catch (ex) { err.textContent = ex.message; err.hidden = false; }
  finally { btn.disabled = false; btn.textContent = 'Upload and roll out'; }
});

$('#paired').addEventListener('click', e => { const row = e.target.closest('.orb-row'); if (row) openOrbSheet(S.orbs.find(o => o.id === row.dataset.id)); });
function openOrbSheet(o) {
  const other = otherUser();
  const sheet = openSheet(`
    <h3>${esc(o.name)}</h3>
    <div class="id muted" style="font-family:var(--mono);font-size:14px;margin-bottom:10px">${esc(o.id)} · fw ${esc(o.fw)} · ${o.online ? 'online' : `offline, last seen ${ago(o.last_seen)}`}</div>
    <form id="orb-edit">
      <label class="field"><span>Name</span><input name="name" maxlength="32" value="${esc(o.name)}" required></label>
      <span class="field"><span>Whose is it?</span></span>
      <div class="seg owner">
        <button type="button" class="${o.owner === S.me.id ? 'on' : ''}" data-owner="${esc(S.me.id)}">Mine</button>
        ${other ? `<button type="button" class="${o.owner === other.id ? 'on' : ''}" data-owner="${esc(other.id)}">${esc(other.name)}'s</button>` : ''}
        <button type="button" class="${!o.owner ? 'on' : ''}" data-owner="">Shared</button>
      </div>
      <label class="field"><span>Brightness · <b id="bright-val">${o.brightness}</b>%</span><input type="range" name="brightness" min="0" max="100" value="${o.brightness}"></label>
      <label class="check"><input type="checkbox" name="quiet_on" ${o.quiet ? 'checked' : ''}> Quiet hours (screen and LED off)</label>
      <div class="quiet-row" ${o.quiet ? '' : 'hidden'}><label>from <input type="time" name="qstart" value="${esc(o.quiet?.start || '22:00')}"></label><label>to <input type="time" name="qend" value="${esc(o.quiet?.end || '07:00')}"></label></div>
      <p class="error" hidden></p>
      <div class="actions"><button type="button" class="danger" id="orb-forget">Forget</button><button class="primary" type="submit">Save</button></div>
    </form>`);
  const f = $('#orb-edit', sheet);
  $('input[name=brightness]', f).addEventListener('input', e => { $('#bright-val', f).textContent = e.target.value; });
  $('input[name=quiet_on]', f).addEventListener('change', e => { $('.quiet-row', f).hidden = !e.target.checked; });
  $('.seg.owner', f).addEventListener('click', e => { const b = e.target.closest('button'); if (b) $$('button', b.parentElement).forEach(x => x.classList.toggle('on', x === b)); });
  $('#orb-forget', f).addEventListener('click', async () => {
    if ($('#orb-forget', f).textContent !== 'Really forget?') { $('#orb-forget', f).textContent = 'Really forget?'; return; }
    try { await api('DELETE', `/api/orbs/${o.id}`); closeSheet(); toast(`${o.name} forgotten`); } catch (ex) { toast(ex.message); }
  });
  f.addEventListener('submit', async e => {
    e.preventDefault(); const err = $('.error', f); err.hidden = true;
    const body = { name: f.name.value.trim(), owner: $('.seg.owner button.on', f).dataset.owner || null, brightness: +f.brightness.value,
      quiet: f.quiet_on.checked ? { start: f.qstart.value, end: f.qend.value } : null };
    try { await api('PATCH', `/api/orbs/${o.id}`, body); closeSheet(); toast('Saved'); } catch (ex) { err.textContent = ex.message; err.hidden = false; }
  });
}

// ---------------------------------------------------------------- STICKERS
function renderStickers() {
  $('#sticker-count').textContent = S.stickers.length;
  $('#library').innerHTML = S.stickers.map(s => `<button type="button" class="stk" data-key="${esc(s.key)}"><img src="${esc(s.url)}" alt=""><span class="nm">${esc(s.key)}</span></button>`).join('');
}
$('#sticker-mode').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return; S.stickerMode = b.dataset.mode;
  $$('button', e.currentTarget).forEach(x => x.classList.toggle('on', x === b));
  $('#emoji-field').hidden = S.stickerMode !== 'emoji'; $('#upload-field').hidden = S.stickerMode !== 'upload';
  $('#sticker-preview-emoji').hidden = S.stickerMode !== 'emoji'; $('#sticker-preview-img').hidden = S.stickerMode !== 'upload' || !$('#sticker-preview-img').src;
});
$('#sticker-emoji').addEventListener('input', e => { $('#sticker-preview-emoji').textContent = e.target.value.trim() || '?'; });
$('#sticker-file').addEventListener('change', e => { const f = e.target.files[0]; if (!f) return; const img = $('#sticker-preview-img'); img.src = URL.createObjectURL(f); img.hidden = false; });
$('#sticker-form').addEventListener('submit', async e => {
  e.preventDefault(); const err = $('#sticker-error'); err.hidden = true;
  const name = $('#sticker-name').value.trim(); if (!name) { err.textContent = 'Give it a name'; err.hidden = false; return; }
  try {
    if (S.stickerMode === 'emoji') await api('POST', '/api/stickers', { emoji: $('#sticker-emoji').value.trim(), name });
    else { const f = $('#sticker-file').files[0]; if (!f) { err.textContent = 'Pick an image'; err.hidden = false; return; } const fd = new FormData(); fd.append('file', f); fd.append('name', name); await api('POST', '/api/stickers', fd); }
    S.stickers = await api('GET', '/api/stickers'); renderStickers(); renderSend(); $('#sticker-name').value = ''; toast(`${name} added`);
  } catch (ex) { err.textContent = ex.code === 'bad_sticker' ? ex.message : ex.code === 'sticker_exists' ? 'There is already a sticker with that name' : ex.message; err.hidden = false; }
});
$('#library').addEventListener('click', e => {
  const b = e.target.closest('.stk'); if (!b) return; const s = S.stickers.find(x => x.key === b.dataset.key);
  const sheet = openSheet(`<h3>${esc(s.name)}</h3><p class="muted">Remove this sticker from the library? Orbs delete their copy on the next sync. Old messages keep showing it.</p><div class="actions"><button type="button" class="secondary" id="cancel">Keep</button><button type="button" class="danger" id="del">Delete</button></div>`);
  $('#cancel', sheet).addEventListener('click', closeSheet);
  $('#del', sheet).addEventListener('click', async () => { try { await api('DELETE', `/api/stickers/${s.key}`); S.stickers = await api('GET', '/api/stickers'); renderStickers(); renderSend(); closeSheet(); toast('Deleted'); } catch (ex) { toast(ex.message); } });
});
$('#edit-phrases').addEventListener('click', e => {
  e.preventDefault();
  const sheet = openSheet(`<h3>Quick phrases</h3><p class="muted">One per line, up to 12, 80 characters each.</p><textarea id="phrases-text" rows="8">${esc(S.phrases.join('\n'))}</textarea><p class="error" hidden></p><div class="actions"><button type="button" class="secondary" id="cancel">Cancel</button><button type="button" class="primary" id="save">Save</button></div>`);
  $('#cancel', sheet).addEventListener('click', closeSheet);
  $('#save', sheet).addEventListener('click', async () => {
    const lines = $('#phrases-text', sheet).value.split('\n').map(x => x.trim()).filter(Boolean);
    try { S.phrases = await api('PUT', '/api/phrases', lines); renderSend(); closeSheet(); toast('Phrases saved'); }
    catch (ex) { const err = $('.error', sheet); err.textContent = ex.message; err.hidden = false; }
  });
});

// ---------------------------------------------------------------- start
window.addEventListener('hashchange', () => { const t = location.hash.slice(1); if (t && t !== S.tab && $(`#tab-${t}`)) showTab(t); });
if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js').catch(() => {});
(async () => {
  try { S.me = await api('GET', '/api/me', undefined, { noAuthRedirect: true }); await boot(); }
  catch { showLogin(); }
})();
