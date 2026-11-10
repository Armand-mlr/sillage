'use strict';
window.Batches = (() => {
  const runningStates = new Set(['preparing', 'running', 'stopping']);
  const labels = {preparing: 'Préparation…', review: 'À vérifier', running: 'En cours', stopping: 'Arrêt…', done: 'Terminé', partial: 'À compléter', cancelled: 'Arrêté', interrupted: 'À reprendre', error: 'Échec'};
  let mode = 'video', current = null, file = null, destination = '', exportRoot = '', activeId = null, working = false;
  let saving = Promise.resolve();
  let history = [], historySignature = '', rowsBatch = '', opening = 0;
  const nodes = new Map();
  const el = (tag, className, content) => { const n = document.createElement(tag); if (className) n.className = className; if (content !== undefined) n.textContent = content; return n; };
  const isRunning = () => !!current && runningStates.has(current.status);
  const error = message => { $('batch-error').textContent = message; $('batch-error').hidden = !message; };

  function setMode(next, focus = false) {
    if (mode !== next) saveDraft();
    mode = next;
    document.querySelectorAll('[data-mode]').forEach(button => { const selected = button.dataset.mode === mode; button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1; });
    $('video-view').hidden = mode !== 'video'; $('batch-view').hidden = mode === 'video';
    $('playlist-view').hidden = mode !== 'playlist'; $('music-view').hidden = mode !== 'music';
    if (mode !== 'video') $(`${mode}-view`).append($('batch-view'));
    error('');
    $('playlist-source').hidden = mode !== 'playlist'; $('music-source').hidden = mode !== 'music';
    $('batch-source-title').textContent = mode === 'playlist' ? 'Toute une playlist, un seul dossier.' : 'Ta playlist Musique prend le relais.';
    $('batch-source-description').textContent = mode === 'playlist' ? 'Retrouve chaque vidéo en fichier audio, dans l’ordre de la playlist.' : 'Importe les titres, vérifie les vidéos proposées, puis crée tes fichiers audio.';
    $('prepare-import').querySelector('span').textContent = mode === 'playlist' ? 'Lire la playlist' : 'Rechercher sur YouTube';
    if (current && current.kind !== mode && mode !== 'video') { current = null; rowsBatch = ''; }
    if (mode !== 'video') render();
    if (focus) $(`tab-${mode}`).focus();
    notifyNative();
  }
  document.querySelectorAll('[data-mode]').forEach(button => {
    button.addEventListener('click', () => setMode(button.dataset.mode));
    button.addEventListener('keydown', event => {
      const modes = ['video', 'playlist', 'music'];
      let index = modes.indexOf(mode);
      if (event.key === 'ArrowRight') index = (index + 1) % 3;
      else if (event.key === 'ArrowLeft') index = (index + 2) % 3;
      else if (event.key === 'Home') index = 0;
      else if (event.key === 'End') index = 2;
      else return;
      event.preventDefault(); setMode(modes[index], true);
    });
  });

  function reset() { if (busy || working) return; current = null; error(''); rowsBatch = ''; render(); if (mode === 'playlist') $('playlist-url').focus(); }
  $('new-import').addEventListener('click', reset);
  function chooseFile() {
    if (busy || working) return;
    setMode('music');
    if (native) native.postMessage({action: 'importFile'}); else $('import-file').click();
  }
  $('choose-import').addEventListener('click', chooseFile);
  function receiveFile(value) {
    if (busy || working) return;
    setMode('music'); current = null; file = value; $('track-text').value = '';
    $('import-filename').textContent = value.filename; error(''); render();
  }
  $('import-file').addEventListener('change', async event => {
    const selected = event.target.files[0]; if (!selected) return;
    if (selected.size > 2 * 1024 * 1024) { error('Le fichier doit faire 2 Mo maximum.'); event.target.value = ''; return; }
    const reader = new FileReader();
    reader.onload = () => receiveFile({filename: selected.name, content: String(reader.result).split(',')[1]});
    reader.onerror = () => error('Impossible de lire ce fichier. Choisis-le à nouveau.');
    reader.readAsDataURL(selected); event.target.value = '';
  });
  $('track-text').addEventListener('input', () => { file = null; $('import-filename').textContent = 'Liste saisie ci-dessous'; error(''); });
  function encodedText(value) { const bytes = new TextEncoder().encode(value); let binary = ''; for (const byte of bytes) binary += String.fromCharCode(byte); return btoa(binary); }
  $('batch-source-form').addEventListener('submit', async event => {
    event.preventDefault(); if (busy || working) return;
    error('');
    let payload;
    if (mode === 'playlist') {
      if (!$('playlist-url').value.trim()) { error('Ajoute le lien de la playlist.'); $('playlist-url').focus(); return; }
      payload = {kind: 'playlist', url: $('playlist-url').value.trim()};
    } else {
      const value = $('track-text').value.trim();
      if (!file && !value) { error('Choisis un export XML ou texte, ou colle une liste de titres.'); return; }
      if (new TextEncoder().encode(value).length > 2 * 1024 * 1024) { error('La liste doit faire 2 Mo maximum.'); return; }
      payload = {kind: 'music', ...(value ? {filename: 'Liste de titres.txt', content: encodedText(value)} : file)};
    }
    working = true; render();
    try { current = await request('/api/imports', payload); activeId = current.id; setBusy(true); render(); }
    catch (exc) { error(exc.message); }
    finally { working = false; render(); notifyNative(); }
  });

  function saveDraft() {
    if (!current || isRunning() || working || failures >= 3) return saving;
    const id = current.id;
    const body = {items: current.items.map(i => ({index: i.index, selected: i.selected, url: i.url})), format: $('batch-format').value, destination};
    saving = saving.then(() => request(`/api/imports/${id}/selection`, body)).catch(exc => { if (current?.id === id) error('La sélection n’a pas été mémorisée. ' + exc.message); });
    return saving;
  }
  function selectedPending() { return current?.items.filter(i => i.selected && i.url && i.status !== 'done') || []; }
  function render() {
    $('batch-setup').hidden = !!current; $('batch-detail').hidden = !current;
    $('prepare-import').disabled = busy || working || failures >= 3;
    $('choose-import').disabled = busy || working;
    $('playlist-url').disabled = busy || working; $('paste-playlist').disabled = busy || working;
    $('track-text').disabled = busy || working;
    if (!current) return;
    const active = isRunning(), done = current.items.filter(i => i.status === 'done').length;
    const searched = current.items.filter(i => i.searched).length;
    const selected = current.items.filter(i => i.selected).length;
    $('batch-title').textContent = current.title;
    $('batch-kind').textContent = current.kind === 'music' ? 'IMPORT MUSIQUE' : 'PLAYLIST YOUTUBE';
    $('batch-status').textContent = current.status === 'review' && current.kind === 'playlist' ? 'Prête' : labels[current.status] || current.status;
    $('batch-summary').textContent = current.status === 'preparing'
      ? (current.kind === 'music' ? `${searched} sur ${current.items.length} titres recherchés sur YouTube` : `${current.items.length || 'Lecture des'} vidéos · préparation de la playlist`)
      : `${done} enregistré${done === 1 ? '' : 's'} · ${selected} sélectionné${selected === 1 ? '' : 's'} · ${current.items.length} au total`;
    $('batch-notice').textContent = current.notice || ''; $('batch-notice').hidden = !current.notice;
    const progress = current.status === 'preparing' ? searched / Math.max(1, current.items.length) * 100 : done / Math.max(1, selected) * 100;
    $('batch-progress').hidden = !active;
    $('batch-progress').classList.toggle('indeterminate', current.status === 'preparing' && current.kind === 'playlist');
    $('batch-progress-fill').style.width = `${progress}%`;
    if (current.status === 'preparing' && current.kind === 'playlist') $('batch-progress').removeAttribute('aria-valuenow');
    else $('batch-progress').setAttribute('aria-valuenow', String(Math.round(progress)));
    if (current.folder || active) $('batch-format').value = current.format;
    $('batch-format').disabled = busy || working || !!current.folder;
    const path = current.folder || destination || exportRoot;
    $('destination-label').textContent = current.folder || destination ? path.split('/').filter(Boolean).slice(-2).join(' / ') : 'Sillage / exports';
    $('destination-label').title = path;
    $('choose-destination').hidden = !native || !!current.folder;
    $('choose-destination').disabled = busy || working;
    $('match-explanation').hidden = current.kind !== 'music';
    $('selection-count').textContent = `${selectedPending().length} à télécharger`;
    $('select-all').disabled = busy || working; $('select-none').disabled = busy || working;
    renderRows();
    const count = selectedPending().length;
    $('start-import').hidden = active || (current.status === 'done' && count === 0);
    $('start-import').disabled = busy || working || failures >= 3 || count === 0;
    $('start-import').textContent = `${current.folder ? 'Reprendre' : 'Télécharger'} ${count} morceau${count === 1 ? '' : 'x'} en ${$('batch-format').value.toUpperCase()}`;
    $('stop-import').hidden = !active; $('stop-import').disabled = working || current.status === 'stopping';
    $('retry-search').hidden = active || !!current.folder || (current.kind === 'playlist' ? current.items.length > 0 : !current.items.some(i => !i.candidates.length));
    $('retry-search').textContent = current.kind === 'playlist' ? 'Réessayer la lecture' : 'Relancer les recherches manquantes';
    $('retry-search').disabled = busy || working;
    $('new-import').disabled = busy || working;
    $('open-export').hidden = !native || !current.folder;
    $('batch-footnote').textContent = current.folder ? 'Les fichiers terminés sont conservés. Un bilan est enregistré dans le dossier à la fin du traitement.' : 'Un sous-dossier sera créé pour cet import. Les fichiers déjà présents ne seront pas remplacés.';
  }

  function buildRow(item) {
    const row = el('article', 'batch-item');
    const check = el('input'); check.type = 'checkbox'; check.setAttribute('aria-label', `Sélectionner ${item.source.title || 'ce morceau'}`);
    const source = el('div', 'track-source');
    source.append(el('div', 'track-number', String(item.index + 1).padStart(3, '0')), el('h3', '', item.source.title || 'Titre manquant'), el('p', '', [item.source.artist, item.source.version, duration(item.source.duration)].filter(Boolean).join(' · ')));
    const result = el('div', 'track-result');
    const select = el('select', 'candidate-choice'); select.setAttribute('aria-label', `Vidéo YouTube pour ${item.source.title}`);
    item.candidates.forEach(c => { const option = el('option', '', [c.title, c.author, duration(c.duration)].filter(Boolean).join(' · ')); option.value = c.url; select.append(option); });
    const manual = el('option', '', 'Coller un autre lien YouTube…'); manual.value = ''; select.append(manual);
    const note = el('p', 'candidate-note');
    const status = el('p', 'track-status');
    const tools = el('div', 'track-tools');
    const view = el('button', 'inline-button', 'Voir la vidéo ↗'); view.type = 'button';
    const play = el('button', 'inline-button', 'Écouter le fichier'); play.type = 'button';
    view.addEventListener('click', () => { if (!item.url) return; if (native) native.postMessage({action: 'youtube', url: item.url}); else window.open(item.url, '_blank', 'noopener'); });
    play.addEventListener('click', () => { const job = jobs.find(j => j.id === item.job_id); if (job && !busy) { setMode('video'); selectAudio(job, true); } });
    tools.append(view, play);
    const details = el('details', 'track-manual');
    details.append(el('summary', '', 'Utiliser un lien précis'));
    const label = el('label', 'field-label', 'Lien de la vidéo'); label.htmlFor = `manual-${item.index}`;
    const input = el('input'); input.type = 'url'; input.id = label.htmlFor; input.placeholder = 'https://youtu.be/…'; input.autocomplete = 'off'; input.spellcheck = false; input.value = item.url;
    details.append(label, input);
    check.addEventListener('change', () => { item.selected = check.checked; render(); saveDraft(); });
    select.addEventListener('change', () => { item.url = select.value; item.selected = !!item.url; input.value = item.url; if (!item.url) { details.open = true; input.focus(); } error(''); render(); saveDraft(); });
    input.addEventListener('input', () => { item.url = input.value.trim(); item.selected = !!item.url; error(''); render(); });
    input.addEventListener('change', saveDraft);
    result.append(select, note, status, tools, details); row.append(check, source, result);
    row.refs = {check, select, note, status, view, play, details, input};
    return row;
  }
  function renderRows() {
    if (rowsBatch !== current.id) { nodes.clear(); $('batch-items').replaceChildren(); rowsBatch = current.id; }
    current.items.forEach(item => {
      const signature = JSON.stringify([item.source, item.candidates]);
      let row = nodes.get(item.index);
      if (!row || row.signature !== signature) {
        const replacement = buildRow(item); replacement.signature = signature;
        if (row) row.replaceWith(replacement); else $('batch-items').append(replacement);
        row = replacement; nodes.set(item.index, row);
      }
      // Callbacks keep the live object even after a server refresh.
      else if (row.item !== item) {
        Object.assign(row.item, item);
        current.items[item.index] = row.item;
        item = row.item;
      }
      row.item = item;
      const r = row.refs, completed = item.status === 'done';
      r.check.checked = item.selected; r.check.disabled = busy || working || completed || !item.url;
      r.select.disabled = busy || working || completed; r.select.value = item.candidates.some(c => c.url === item.url) ? item.url : '';
      r.select.hidden = current.kind === 'playlist' || !item.candidates.length;
      const chosen = item.candidates.find(c => c.url === item.url);
      r.note.textContent = chosen ? (chosen.confidence === 'probable' ? 'Correspondance probable · ' : chosen.confidence === 'direct' ? '' : 'À vérifier · ') + chosen.reason : item.url ? 'Lien choisi manuellement' : item.searched ? item.search_error || 'Aucune proposition. Ajoute un lien.' : 'Recherche à venir…';
      r.note.dataset.confidence = chosen?.confidence || 'review';
      r.status.dataset.state = item.status;
      const stages = {queued: 'Préparation', analyzing: 'Lecture de la vidéo', downloading: 'Récupération', converting: 'Conversion'};
      r.status.textContent = completed ? 'Enregistré · ' + item.export_path.split('/').pop() : item.status === 'error' ? item.error : item.status === 'running' ? `${stages[item.stage] || 'Préparation'}${item.progress == null ? '…' : ' · ' + Math.round(item.progress) + ' %'}` : '';
      r.input.disabled = busy || working || completed;
      r.details.hidden = completed;
      if (document.activeElement !== r.input) r.input.value = item.url;
      r.view.hidden = !item.url; r.play.hidden = !completed; r.play.disabled = busy;
    });
  }

  $('select-all').addEventListener('click', () => { if (busy || !current) return; current.items.forEach(i => { if (i.url && i.status !== 'done') i.selected = true; }); render(); saveDraft(); });
  $('select-none').addEventListener('click', () => { if (busy || !current) return; current.items.forEach(i => { if (i.status !== 'done') i.selected = false; }); render(); saveDraft(); });
  $('batch-format').addEventListener('change', () => { render(); saveDraft(); notifyNative(); });
  $('choose-destination').addEventListener('click', () => native?.postMessage({action: 'chooseDirectory'}));
  $('open-export').addEventListener('click', () => { if (current?.folder) native?.postMessage({action: 'openDirectory', path: current.folder}); });
  $('start-import').addEventListener('click', async () => {
    if (!current || busy || working) return;
    error(''); working = true; render();
    try {
      await saving;
      current = await request(`/api/imports/${current.id}/start`, {format: $('batch-format').value, destination, items: current.items.map(i => ({index: i.index, selected: i.selected, url: i.url}))});
      activeId = current.id; setBusy(true);
    } catch (exc) { error(exc.message); }
    finally { working = false; render(); notifyNative(); }
  });
  async function cancelActive() {
    const id = activeId || (isRunning() ? current.id : null); if (!id || working) return;
    working = true; render();
    try { await request(`/api/imports/${id}/cancel`, {}); if (current?.id === id) current.status = 'stopping'; toast('Arrêt demandé. Les fichiers terminés sont conservés.'); }
    catch (exc) { error(exc.message); }
    finally { working = false; render(); }
  }
  $('stop-import').addEventListener('click', cancelActive);
  $('retry-search').addEventListener('click', async () => {
    if (!current || busy || working) return;
    error(''); working = true; render();
    try { current = await request(`/api/imports/${current.id}/search`, {}); activeId = current.id; setBusy(true); }
    catch (exc) { error(exc.message); }
    finally { working = false; render(); }
  });
  async function openImport(id) {
    const sequence = ++opening;
    await saveDraft();
    try { const value = await request(`/api/imports/${id}`); if (sequence !== opening) return; setMode(value.kind); current = value; destination = current.destination || ''; $('batch-format').value = current.format; error(''); render(); $('batch-detail').scrollIntoView({block: 'start'}); }
    catch (exc) { error(exc.message); }
  }
  function renderHistory() {
    const signature = JSON.stringify(history);
    $('batch-history').hidden = !history.length;
    if (signature === historySignature) return;
    historySignature = signature; $('import-history-list').replaceChildren();
    history.forEach(batch => {
      const button = el('button', 'import-history-row'); button.type = 'button';
      const info = el('div'); info.append(el('strong', '', batch.title), el('p', '', `${batch.kind === 'music' ? 'Import Musique' : 'Playlist YouTube'} · ${batch.done}/${batch.total} enregistrés · ${batch.format.toUpperCase()}`));
      button.append(info, el('span', '', labels[batch.status] || batch.status)); button.addEventListener('click', () => openImport(batch.id)); $('import-history-list').append(button);
    });
  }
  async function update(state) {
    history = state.imports || []; activeId = state.active_import; exportRoot = state.export_root || '';
    renderHistory();
    if (current && (isRunning() || current.id === activeId)) {
      const id = current.id;
      try { const detail = await request(`/api/imports/${id}`); if (current?.id === id) current = detail; }
      catch (exc) { error(exc.message); }
    }
    if (mode !== 'video') render();
    notifyNative();
  }
  $('paste-playlist').addEventListener('click', async () => {
    if (native) { native.postMessage({action: 'paste'}); return; }
    try { paste(await navigator.clipboard.readText()); } catch { $('playlist-url').focus(); toast('Colle le lien avec ⌘V ou Ctrl+V.'); }
  });
  function paste(value) { if (mode !== 'playlist' || busy) return false; $('playlist-url').value = String(value).trim(); $('playlist-url').focus(); return true; }
  function command(name) {
    if (name === 'playlist' || name === 'music') { setMode(name); return true; }
    if (name === 'importFile') { chooseFile(); return true; }
    if (name === 'cancel' && activeId) { cancelActive(); return true; }
    if (mode === 'video') return false;
    if (name === 'new') reset();
    else if (name === 'convert') { if (current) $('start-import').click(); else $('batch-source-form').requestSubmit(); }
    else if (name === 'focus') { if (mode === 'playlist') $('playlist-url').focus(); else chooseFile(); }
    else if ((name === 'wav' || name === 'mp3') && !busy && !current?.folder) { $('batch-format').value = name; render(); saveDraft(); }
    else return false;
    notifyNative(); return true;
  }
  window.sillageImportFile = receiveFile;
  window.sillageDirectory = path => { destination = String(path); render(); saveDraft(); };
  window.sillageImportError = message => { error(message); };
  return {update, command, paste, setMode, fail: message => { error(message); render(); }, getFormat: () => mode === 'video' ? chosenFormat() : $('batch-format').value};
})();
