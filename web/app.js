'use strict';
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="sillage-token"]').content;
const activeStates = new Set(['queued', 'analyzing', 'downloading', 'converting']);
const native = window.webkit?.messageHandlers?.sillage;
let nativeState = '';
let jobs = [], selectedId = null, busy = false, submitting = false, lastLibrary = '', lastAudio = '', polling = false, failures = 0, toastTimer;

for (let index = 0; index < 66; index++) {
  const bar = document.createElement('span');
  const envelope = Math.pow(Math.sin(index / 65 * Math.PI), 1.1);
  const rhythm = .26 + .74 * Math.abs(Math.sin(index * 1.63) * Math.cos(index * .39));
  bar.style.setProperty('--h', `${5 + envelope * rhythm * 100}px`);
  bar.style.setProperty('--o', `${.25 + envelope * .7}`);
  bar.style.setProperty('--d', `${-(index % 13) * .13}s`);
  $('wave-bars').append(bar);
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return '';
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(2).replace('.', ',')} Go` : `${(bytes / 1e6).toFixed(1).replace('.', ',')} Mo`;
}
function duration(seconds) {
  if (!seconds) return '';
  const minutes = Math.floor(seconds / 60), remainder = Math.floor(seconds % 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')} min` : `${minutes}:${String(remainder).padStart(2, '0')}`;
}
function chosenFormat() { return document.querySelector('input[name="format"]:checked').value; }
function showError(message, invalid = false) { $('form-error').textContent = message; $('form-error').hidden = !message; $('video-url').setAttribute('aria-invalid', String(invalid)); }
function toast(message) {
  $('toast').textContent = message; $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4500);
}
async function request(path, body) {
  const response = await fetch(path, body === undefined ? {cache: 'no-store'} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Sillage-Token': token}, body: JSON.stringify(body)
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'Une erreur est survenue. Réessaie.');
  return value;
}
function setBusy(value) {
  busy = value;
  $('convert-button').disabled = value || submitting || failures >= 3;
  $('video-url').disabled = value;
  $('clear-url').disabled = value;
  $('paste-url').disabled = value;
  document.querySelectorAll('input[name="format"]').forEach(input => { input.disabled = value; });
  $('convert-label').textContent = value ? 'Conversion en cours…' : `Convertir en ${chosenFormat().toUpperCase()}`;
}

function renderJob(job) {
  const panel = $('sound-panel');
  panel.classList.toggle('has-job', !!job);
  panel.classList.toggle('is-active', !!job && activeStates.has(job.status));
  $('idle-panel').hidden = !!job; $('job-panel').hidden = !job;
  panel.dataset.status = job?.status || 'idle';
  if (!job) { $('panel-status').textContent = 'En attente'; notifyNative(); return; }
  const active = activeStates.has(job.status), done = job.status === 'done';
  const error = job.status === 'error', cancelled = job.status === 'cancelled';
  $('panel-status').textContent = done ? 'Terminé' : active ? 'En cours' : error ? 'Échec' : 'Annulé';
  $('job-title').textContent = job.title;
  $('job-author').textContent = job.author || 'YOUTUBE';
  $('job-duration').textContent = [duration(job.duration), job.format.toUpperCase(), done ? formatBytes(job.size) : ''].filter(Boolean).join(' · ');
  if (job.video_id && /^[\w-]{11}$/.test(job.video_id)) {
    const thumbnail = `https://i.ytimg.com/vi/${job.video_id}/mqdefault.jpg`;
    if ($('video-thumbnail').getAttribute('src') !== thumbnail) $('video-thumbnail').src = thumbnail;
    $('video-thumbnail').hidden = false;
  } else { $('video-thumbnail').hidden = true; $('video-thumbnail').removeAttribute('src'); }
  const labels = {queued: 'Préparation…', analyzing: 'Lecture de la vidéo…', downloading: 'Récupération de l’audio', converting: `Création du ${job.format.toUpperCase()}`, done: 'Ton audio est prêt.', error: 'La conversion n’a pas abouti.', cancelled: 'Conversion annulée.'};
  $('job-stage').textContent = labels[job.status] || '';
  const percent = Math.min(100, Math.max(0, job.progress || 0));
  $('job-percent').textContent = active && job.progress != null ? `${Math.round(percent)} %` : '';
  $('progress-fill').style.width = `${percent}%`;
  $('progress-track').classList.toggle('indeterminate', active && (job.progress == null || ['queued', 'analyzing'].includes(job.status)));
  $('progress-track').hidden = done || error || cancelled;
  if (active && job.progress != null && !['queued', 'analyzing'].includes(job.status)) $('progress-track').setAttribute('aria-valuenow', String(Math.round(percent)));
  else $('progress-track').removeAttribute('aria-valuenow');
  $('job-steps').hidden = !active;
  document.querySelectorAll('.job-steps li').forEach(step => {
    if (step.dataset.stage === job.status || (step.dataset.stage === 'analyzing' && job.status === 'queued')) step.setAttribute('aria-current', 'step');
    else step.removeAttribute('aria-current');
  });
  $('job-detail').textContent = error ? job.error : cancelled ? 'Tu peux coller un nouveau lien ou réessayer.' : done ? 'Écoute le résultat, puis enregistre-le où tu veux.' : job.status === 'downloading' ? [job.speed ? `${formatBytes(job.speed)}/s` : '', Number.isFinite(job.eta) ? `environ ${job.eta < 60 ? Math.ceil(job.eta) + ' s' : Math.ceil(job.eta / 60) + ' min'}` : ''].filter(Boolean).join(' · ') : job.status === 'converting' ? 'La conversion se fait directement sur ton Mac.' : 'Recherche de la meilleure piste audio disponible…';
  $('cancel-button').hidden = !active;
  $('reset-button').hidden = active;
  $('result-download').hidden = !done;
  $('audio-preview').hidden = !done;
  if (done) {
    $('result-download').href = `/api/download/${job.id}`;
    $('result-download').setAttribute('download', job.filename);
    $('result-download').querySelector('span').textContent = `Enregistrer le ${job.format.toUpperCase()}…`;
    if (lastAudio !== job.id) { $('audio-preview').src = `/api/audio/${job.id}`; lastAudio = job.id; }
  } else if (lastAudio) {
    $('audio-preview').pause(); $('audio-preview').removeAttribute('src'); $('audio-preview').load(); lastAudio = '';
  }
  notifyNative();
}

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#i-${name}`); svg.append(use); return svg;
}
function notifyNative() {
  const selected = jobs.find(job => job.id === selectedId);
  const state = JSON.stringify({action: 'state', busy, canSave: selected?.status === 'done', format: window.Batches?.getFormat() || chosenFormat()});
  if (native && state !== nativeState) { native.postMessage(JSON.parse(state)); nativeState = state; }
}
function selectAudio(job, autoplay = false) {
  if (busy) { toast('Attends la fin de la conversion pour ouvrir un autre audio.'); return; }
  selectedId = job.id; renderJob(job); renderLibrary();
  const behavior = matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
  $('sound-panel').scrollIntoView({behavior, block: 'nearest'});
  if (autoplay) $('audio-preview').play().catch(() => toast('Appuie sur Lecture pour écouter le fichier.'));
}
function renderLibrary() {
  const ready = jobs.filter(job => job.status === 'done' && !job.batch_id);
  const signature = JSON.stringify(ready.map(job => [job.id, job.size]));
  if (signature === lastLibrary) {
    document.querySelectorAll('.library-row').forEach(row => { row.dataset.selected = String(row.dataset.id === selectedId); });
    return;
  }
  lastLibrary = signature;
  $('library-count').textContent = ready.length;
  $('library-empty').hidden = !!ready.length;
  $('library-table').hidden = !ready.length;
  $('library-list').replaceChildren();
  ready.forEach(job => {
    const row = document.createElement('article'); row.className = 'library-row'; row.dataset.id = job.id; row.dataset.selected = String(job.id === selectedId);
    const main = document.createElement('div'); main.className = 'file-main';
    const badge = document.createElement('span'); badge.className = 'file-icon'; badge.append(icon('music')); badge.setAttribute('aria-hidden', 'true');
    const info = document.createElement('div'); info.className = 'file-info';
    const title = document.createElement('h3'); title.textContent = job.title;
    const author = document.createElement('p'); author.textContent = job.author;
    info.append(title, author); main.append(badge, info);
    const format = document.createElement('span'); format.className = 'file-format'; format.textContent = job.format.toUpperCase();
    const time = document.createElement('span'); time.className = 'file-duration'; time.textContent = duration(job.duration);
    const size = document.createElement('span'); size.className = 'file-size'; size.textContent = formatBytes(job.size);
    const actions = document.createElement('div'); actions.className = 'row-actions';
    const play = document.createElement('button'); play.className = 'row-button'; play.append(icon('play')); play.setAttribute('aria-label', `Écouter ${job.title} (${job.format.toUpperCase()})`); play.title = 'Écouter';
    play.addEventListener('click', () => selectAudio(job, true));
    const download = document.createElement('a'); download.className = 'download-link'; download.href = `/api/download/${job.id}`; download.setAttribute('download', job.filename); download.setAttribute('aria-label', `Enregistrer ${job.title} (${job.format.toUpperCase()})`); download.append(icon('download'));
    const label = document.createElement('span'); label.textContent = 'Enregistrer'; download.append(label);
    actions.append(play, download); row.append(main, format, time, size, actions); $('library-list').append(row);
  });
}
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const state = await request('/api/state'); jobs = state.jobs;
    if (failures >= 3) { showError(''); document.body.classList.remove('offline'); }
    failures = 0;
    const active = jobs.find(job => activeStates.has(job.status));
    if (active) selectedId = active.id;
    setBusy(!!active || !!state.active_import);
    renderJob(jobs.find(job => job.id === selectedId)); renderLibrary();
    await window.Batches?.update(state);
  } catch (error) {
    failures++;
    if (failures >= 3) { document.body.classList.add('offline'); showError('La connexion à Sillage a été interrompue. Relance l’application pour continuer.'); $('convert-button').disabled = true; window.Batches?.fail('La connexion à Sillage a été interrompue. Relance l’application pour continuer.'); }
  } finally {
    polling = false; setTimeout(poll, busy ? 600 : 1800);
  }
}

$('convert-form').addEventListener('submit', async (event) => {
  event.preventDefault(); if (busy || submitting) return;
  const url = $('video-url').value.trim();
  if (!url) { showError('Ajoute le lien de la vidéo que tu veux convertir.', true); $('video-url').focus(); return; }
  showError(''); submitting = true; $('convert-button').disabled = true;
  try {
    const job = await request('/api/jobs', {url, format: window.Batches?.getFormat() || chosenFormat()});
    selectedId = job.id; jobs.unshift(job); setBusy(true); renderJob(job);
  } catch (error) { showError(error.message); }
  finally { submitting = false; setBusy(busy); }
});
document.querySelectorAll('input[name="format"]').forEach(input => input.addEventListener('change', updateFormat));
$('video-url').addEventListener('input', () => { $('clear-url').hidden = !$('video-url').value; showError(''); });
$('clear-url').addEventListener('click', () => { $('video-url').value = ''; $('clear-url').hidden = true; showError(''); $('video-url').focus(); });
$('cancel-button').addEventListener('click', async () => {
  if (!selectedId) return;
  $('cancel-button').disabled = true;
  try { await request(`/api/jobs/${selectedId}/cancel`, {}); toast('Conversion annulée.'); }
  catch (error) { showError(error.message); }
  finally { $('cancel-button').disabled = false; }
});
function newConversion() {
  if (busy) { toast('Une conversion est déjà en cours.'); return; }
  selectedId = null; $('audio-preview').pause(); $('audio-preview').removeAttribute('src'); $('audio-preview').load(); lastAudio = '';
  renderJob(null); renderLibrary(); $('video-url').value = ''; $('clear-url').hidden = true; showError(''); $('video-url').focus();
}
$('reset-button').addEventListener('click', newConversion);
$('help-open').addEventListener('click', openHelp);
$('formats-help').addEventListener('click', openHelp);
$('help-close').addEventListener('click', () => $('help-dialog').close());
$('help-dialog').addEventListener('click', event => { if (event.target === $('help-dialog')) { const rect = event.target.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) $('help-dialog').close(); } });
$('video-thumbnail').addEventListener('error', () => { $('video-thumbnail').hidden = true; });
function openHelp() { if (!$('help-dialog').open) $('help-dialog').showModal(); }
function updateFormat() {
  const wav = chosenFormat() === 'wav';
  setBusy(busy);
  $('panel-format').textContent = wav ? '.wav' : '.mp3';
  $('output-description').textContent = wav ? 'Prêt pour le montage' : 'Prêt à emporter';
  $('format-explanation').textContent = wav ? 'Le WAV garde l’audio décodé sans compression supplémentaire.' : 'Le MP3 prend moins de place : environ 2,4 Mo par minute.';
  notifyNative();
}
window.sillagePaste = text => {
  if (busy) return;
  if (window.Batches?.paste(text)) return;
  $('video-url').value = String(text).trim();
  $('video-url').dispatchEvent(new Event('input', {bubbles: true}));
  $('video-url').focus();
};
$('paste-url').addEventListener('click', async () => {
  if (native) { native.postMessage({action: 'paste'}); return; }
  try { window.sillagePaste(await navigator.clipboard.readText()); }
  catch { $('video-url').focus(); toast('Colle ton lien avec ⌘V ou Ctrl+V.'); }
});
$('open-folder').hidden = !native;
$('open-folder').addEventListener('click', () => native?.postMessage({action: 'files'}));
window.sillageCommand = command => {
  if (window.Batches?.command(command)) return;
  if (command === 'new') newConversion();
  if (command === 'convert' && !busy && !submitting) $('convert-form').requestSubmit();
  if (command === 'cancel' && busy) $('cancel-button').click();
  if (command === 'save' && !$('result-download').hidden) $('result-download').click();
  if (command === 'play' && !$('audio-preview').hidden) {
    if ($('audio-preview').paused) $('audio-preview').play().catch(() => {}); else $('audio-preview').pause();
  }
  if (command === 'help') openHelp();
  if (command === 'focus' && !busy) $('video-url').focus();
  if ((command === 'wav' || command === 'mp3') && !busy) { document.querySelector(`input[value="${command}"]`).checked = true; updateFormat(); }
};
if (!native) document.querySelector('.keyboard-hint').textContent = /Mac/.test(navigator.platform) ? '⌘ ↵' : 'Ctrl ↵';
document.addEventListener('keydown', event => {
  if (!(event.metaKey || event.ctrlKey) || event.altKey) return;
  let command;
  if (event.key === 'Enter') command = 'convert';
  else if (event.key.toLowerCase() === 'l') command = 'focus';
  else if (event.key === '.') command = 'cancel';
  if (command) { event.preventDefault(); window.sillageCommand(command); }
});
notifyNative();
poll();
