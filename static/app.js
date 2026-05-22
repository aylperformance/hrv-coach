/* HRV Coach — interactions front-end */

// =============================================================================
// Import flow : dropzone + preview AJAX + slider de transition
// =============================================================================

function initImportFlow() {
  const form = document.getElementById('previewForm');
  if (!form) return;

  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('file');
  const fileName = document.getElementById('fileName');
  const errorBox = document.getElementById('previewError');

  // Drag & drop
  dropzone.addEventListener('click', () => fileInput.click());
  dropzone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropzone.classList.add('dragover');
  });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
  dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
    if (e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      fileName.textContent = e.dataTransfer.files[0].name;
    }
  });
  fileInput.addEventListener('change', () => {
    if (fileInput.files.length) {
      fileName.textContent = fileInput.files[0].name;
    }
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorBox.hidden = true;

    const athleteId = document.getElementById('athlete_id').value;
    if (!athleteId) {
      showError(errorBox, 'Veuillez sélectionner un athlète.');
      return;
    }
    if (!fileInput.files.length) {
      showError(errorBox, 'Veuillez sélectionner un fichier.');
      return;
    }

    const fd = new FormData();
    fd.append('athlete_id', athleteId);
    fd.append('file', fileInput.files[0]);
    const correctCb = document.getElementById('correct_artifacts');
    if (correctCb && correctCb.checked) fd.append('correct_artifacts', '1');

    const submitBtn = form.querySelector('button[type=submit]');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Analyse en cours…';

    try {
      const resp = await fetch('/tests/preview', { method: 'POST', body: fd });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `Erreur ${resp.status}`);
      }
      const data = await resp.json();
      showPreview(data);
    } catch (err) {
      showError(errorBox, err.message);
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Analyser';
    }
  });

  document.getElementById('cancelPreview')?.addEventListener('click', () => {
    document.getElementById('step2').hidden = true;
    document.getElementById('step1').hidden = false;
  });
}

function showError(box, msg) {
  box.textContent = msg;
  box.hidden = false;
}

function fmt(v, digits = 1) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  return Number(v).toFixed(digits);
}

let transitionChart = null;
let currentPreview = null;

function showPreview(data) {
  currentPreview = data;
  document.getElementById('step1').hidden = true;
  document.getElementById('step2').hidden = false;

  document.getElementById('confirmToken').value = data.token;

  // Date pré-remplie
  if (data.extracted_date) {
    const dt = new Date(data.extracted_date);
    if (!isNaN(dt.getTime())) {
      const local = new Date(dt.getTime() - dt.getTimezoneOffset() * 60000);
      document.getElementById('test_date').value = local.toISOString().slice(0, 16);
    }
  }

  const r = data.result;
  const colorClass = `banner-${r.fatigue_color || 'green'}`;

  const summary = document.getElementById('previewSummary');
  summary.innerHTML = `
    <div class="preview-banner ${colorClass}">
      <strong>${escapeHtml(r.fatigue_label || 'Bilan')}</strong>
      <div>ΔFC ${fmt(r.delta_hr, 0)} bpm · ${data.source_type.toUpperCase()}</div>
    </div>
    <div class="preview-grid">
      <div class="preview-cell"><strong>RMSSD couché</strong>${fmt(r.sitting.rmssd, 0)} ms</div>
      <div class="preview-cell"><strong>HF couché</strong>${fmt(r.sitting.hf, 0)} ms²</div>
      <div class="preview-cell"><strong>LF/HF couché</strong>${fmt(r.sitting.lf_hf, 2)}</div>
      <div class="preview-cell"><strong>LF/HF debout</strong>${fmt(r.standing.lf_hf, 2)}</div>
      <div class="preview-cell"><strong>FC couché</strong>${fmt(r.sitting.mean_hr, 0)} bpm</div>
      <div class="preview-cell"><strong>FC debout</strong>${fmt(r.standing.mean_hr, 0)} bpm</div>
      <div class="preview-cell"><strong>SampEn couché</strong>${fmt(r.sitting.sampen, 2)}</div>
      <div class="preview-cell"><strong>DFA α1</strong>${fmt(r.sitting.dfa_alpha1, 2)}</div>
      <div class="preview-cell"><strong>PNS / SNS</strong>${fmt(r.sitting.pns_index, 2)} / ${fmt(r.sitting.sns_index, 2)}</div>
      <div class="preview-cell"><strong>Beats</strong>${r.n_beats_total} (${r.n_artifacts_removed} corr.)</div>
    </div>
  `;

  // Curseur de transition (uniquement pour .txt brut)
  const editor = document.getElementById('transitionEditor');
  if (data.rr_preview && data.rr_preview.length > 100) {
    editor.hidden = false;
    drawTransitionChart(data.rr_preview, r.transition_index);
    initTransitionSlider(data.rr_preview, r.transition_index);
  } else {
    editor.hidden = true;
    document.getElementById('confirmTransition').value = '';
  }
}

function drawTransitionChart(rrArray, transitionIdx) {
  const ctx = document.getElementById('transitionChart').getContext('2d');
  if (transitionChart) transitionChart.destroy();

  const hr = rrArray.map(rr => 60000 / rr);
  const labels = hr.map((_, i) => i);
  const colors = hr.map((_, i) => i < transitionIdx ? '#3B6D11' : '#1F7AB4');

  transitionChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'FC instantanée (bpm)',
        data: hr,
        borderColor: '#888',
        backgroundColor: 'transparent',
        pointRadius: 0,
        borderWidth: 1,
        segment: {
          borderColor: (c) => c.p0DataIndex < transitionIdx ? '#3B6D11' : '#1F7AB4',
        },
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        annotation: false,
      },
      scales: {
        x: { display: false },
        y: { title: { display: true, text: 'FC (bpm)' } },
      },
    },
  });
}

function initTransitionSlider(rrArray, initialIdx) {
  const slider = document.getElementById('transitionSlider');
  const label = document.getElementById('transitionLabel');
  const hidden = document.getElementById('confirmTransition');

  slider.min = 0;
  slider.max = rrArray.length - 1;
  slider.value = initialIdx;
  hidden.value = initialIdx;

  const updateLabel = () => {
    const idx = parseInt(slider.value);
    // Temps cumulé en secondes
    let t = 0;
    for (let i = 0; i < idx && i < rrArray.length; i++) t += rrArray[i];
    t = t / 1000;
    label.textContent = `beat ${idx} · ${t.toFixed(1)}s`;
  };

  slider.addEventListener('input', () => {
    hidden.value = slider.value;
    updateLabel();
    drawTransitionChart(rrArray, parseInt(slider.value));
  });
  updateLabel();
}

function escapeHtml(s) {
  if (!s) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// =============================================================================
// Import multiple (bulk) — sélection N fichiers, traitement en série
// =============================================================================

function initBulkImport() {
  const form = document.getElementById('bulkForm');
  if (!form) return;

  const dropzone = document.getElementById('bulkDropzone');
  const fileInput = document.getElementById('bulkFiles');
  const fileList = document.getElementById('bulkFileList');
  const errorBox = document.getElementById('bulkError');

  dropzone.addEventListener('click', (e) => {
    if (e.target.tagName !== 'LABEL') fileInput.click();
  });
  dropzone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropzone.classList.add('dragover');
  });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
  dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
    if (e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      renderFileList();
    }
  });
  fileInput.addEventListener('change', renderFileList);

  function renderFileList() {
    const files = Array.from(fileInput.files);
    if (!files.length) { fileList.innerHTML = ''; return; }
    const html = `<strong>${files.length} fichier${files.length > 1 ? 's' : ''} sélectionné${files.length > 1 ? 's' : ''} :</strong><ul style="margin:6px 0 0;padding-left:18px;font-weight:normal;">` +
      files.map(f => `<li>${escapeHtml(f.name)} <small>(${(f.size/1024).toFixed(1)} KB)</small></li>`).join('') +
      '</ul>';
    fileList.innerHTML = html;
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorBox.hidden = true;

    const athleteId = document.getElementById('athlete_id').value;
    const athleteName = document.getElementById('athlete_id').selectedOptions[0].textContent.trim();
    if (!athleteId) { showError(errorBox, 'Sélectionne un athlète.'); return; }
    if (!fileInput.files.length) { showError(errorBox, 'Sélectionne au moins un fichier.'); return; }

    const correctArtifacts = document.getElementById('correct_artifacts_bulk').checked;
    const files = Array.from(fileInput.files);

    document.getElementById('step1').hidden = true;
    document.getElementById('bulkProgress').hidden = false;
    const progressText = document.getElementById('progressText');
    const progressFill = document.getElementById('progressFill');

    // Traiter les fichiers un par un séquentiellement
    const results = [];
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      progressText.textContent = `Fichier ${i+1} / ${files.length} : ${f.name}`;
      progressFill.style.width = `${Math.round(100 * i / files.length)}%`;

      const fd = new FormData();
      fd.append('athlete_id', athleteId);
      fd.append('file', f);
      if (correctArtifacts) fd.append('correct_artifacts', '1');

      try {
        const resp = await fetch('/tests/single_upload', { method: 'POST', body: fd });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          results.push({
            filename: f.name,
            status: 'error',
            message: err.detail || `HTTP ${resp.status}`,
          });
        } else {
          const data = await resp.json();
          results.push(data);
        }
      } catch (err) {
        results.push({
          filename: f.name,
          status: 'error',
          message: err.message || 'Erreur réseau',
        });
      }
    }
    progressFill.style.width = '100%';
    progressText.textContent = 'Terminé.';

    const summary = {
      total: results.length,
      ok: results.filter(r => r.status === 'ok').length,
      skipped: results.filter(r => r.status === 'skipped').length,
      error: results.filter(r => r.status === 'error').length,
    };

    showBulkResults({
      athlete: { id: athleteId, name: athleteName },
      results,
      summary,
    });
  });

  document.getElementById('bulkRestart')?.addEventListener('click', () => {
    document.getElementById('bulkResults').hidden = true;
    document.getElementById('step1').hidden = false;
    fileInput.value = '';
    fileList.innerHTML = '';
  });
}

function showBulkResults(data) {
  document.getElementById('bulkProgress').hidden = true;
  document.getElementById('bulkResults').hidden = false;

  const s = data.summary;
  document.getElementById('bulkSummary').innerHTML = `
    <div class="bulk-stat"><span class="bulk-stat-num">${s.total}</span><span>fichier${s.total>1?'s':''}</span></div>
    <div class="bulk-stat bulk-ok"><span class="bulk-stat-num">${s.ok}</span><span>importé${s.ok>1?'s':''}</span></div>
    <div class="bulk-stat bulk-warn"><span class="bulk-stat-num">${s.skipped}</span><span>ignoré${s.skipped>1?'s':''}</span></div>
    <div class="bulk-stat bulk-err"><span class="bulk-stat-num">${s.error}</span><span>erreur${s.error>1?'s':''}</span></div>
  `;

  const tbody = document.getElementById('bulkResultsBody');
  tbody.innerHTML = data.results.map(r => {
    const statusClass = r.status === 'ok' ? 'res-ok' : (r.status === 'skipped' ? 'res-skip' : 'res-err');
    const statusIcon = r.status === 'ok' ? '✓' : (r.status === 'skipped' ? '–' : '✕');
    const dot = r.fatigue_color ? `<span class="status-dot status-${r.fatigue_color}"></span> ` : '';
    const link = r.test_id ? `<a href="/tests/${r.test_id}" class="btn-link">Voir</a>` : '';
    return `<tr>
      <td><span class="bulk-status ${statusClass}">${statusIcon}</span></td>
      <td>${escapeHtml(r.filename)}</td>
      <td>${dot}${escapeHtml(r.message)}</td>
      <td>${link}</td>
    </tr>`;
  }).join('');

  document.getElementById('bulkSeeAthlete').href = `/athletes/${data.athlete.id}`;
}

// =============================================================================
// Détail test : graphiques LF/HF/FC (jour + médiane) avec curseur d'échelle
// =============================================================================

let todayChart = null;
let medianChart = null;

function initTestDetailCharts() {
  const data = window.HRV && window.HRV.testData;
  if (!data) return;

  const todayCtx = document.getElementById('todayChart');
  const medianCtx = document.getElementById('medianChart');
  if (!todayCtx) return;

  // Échelle auto-déduite (max entre toutes les valeurs LF/HF * 1.2, arrondi au 500)
  const allVals = [
    data.today.sit_lf, data.today.sit_hf, data.today.std_lf, data.today.std_hf,
    data.median.sit_lf, data.median.sit_hf, data.median.std_lf, data.median.std_hf,
  ].filter(v => v && !isNaN(v));
  const autoMax = allVals.length ? Math.ceil(Math.max(...allVals) * 1.2 / 500) * 500 : 3000;
  const initMax = Math.max(500, Math.min(20000, autoMax));

  const slider = document.getElementById('lfhfScale');
  const sliderValue = document.getElementById('lfhfScaleValue');
  const autoBtn = document.getElementById('lfhfAuto');
  slider.value = initMax;
  sliderValue.textContent = initMax;

  todayChart = createLFHFChart(todayCtx, data.today, initMax);
  if (data.median.n_tests > 0) {
    medianChart = createLFHFChart(medianCtx, data.median, initMax);
  } else {
    // Pas d'historique : message dans le canvas
    const ctx = medianCtx.getContext('2d');
    ctx.fillStyle = '#6B6B5F';
    ctx.font = '14px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('Pas d\'historique sur 30 jours', medianCtx.width / 2, medianCtx.height / 2);
  }

  slider.addEventListener('input', () => {
    const v = parseInt(slider.value);
    sliderValue.textContent = v;
    updateLFHFScale(todayChart, v);
    if (medianChart) updateLFHFScale(medianChart, v);
  });

  autoBtn.addEventListener('click', () => {
    slider.value = autoMax;
    sliderValue.textContent = autoMax;
    updateLFHFScale(todayChart, autoMax);
    if (medianChart) updateLFHFScale(medianChart, autoMax);
  });
}

function createLFHFChart(canvas, vals, scaleMax) {
  return new Chart(canvas.getContext('2d'), {
    data: {
      labels: ['Couché', 'Debout'],
      datasets: [
        {
          type: 'bar',
          label: 'LF (ms²)',
          data: [vals.sit_lf, vals.std_lf],
          backgroundColor: '#A32D2D',
          borderColor: '#A32D2D',
          yAxisID: 'y',
          order: 2,
        },
        {
          type: 'bar',
          label: 'HF (ms²)',
          data: [vals.sit_hf, vals.std_hf],
          backgroundColor: '#3B6D11',
          borderColor: '#3B6D11',
          yAxisID: 'y',
          order: 2,
        },
        {
          type: 'line',
          label: 'FC (bpm)',
          data: [vals.sit_hr, vals.std_hr],
          borderColor: '#BA7517',
          backgroundColor: '#BA7517',
          pointRadius: 5,
          pointHoverRadius: 7,
          borderWidth: 2,
          yAxisID: 'y2',
          order: 1,
          tension: 0.1,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'top', labels: { boxWidth: 14 } },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const v = ctx.parsed.y;
              if (ctx.dataset.label.startsWith('FC')) return `FC : ${v.toFixed(0)} bpm`;
              return `${ctx.dataset.label} : ${v.toFixed(0)}`;
            },
          },
        },
      },
      scales: {
        y: {
          type: 'linear',
          position: 'left',
          beginAtZero: true,
          max: scaleMax,
          title: { display: true, text: 'LF / HF (ms²)' },
        },
        y2: {
          type: 'linear',
          position: 'right',
          beginAtZero: true,
          max: 140,
          grid: { drawOnChartArea: false },
          title: { display: true, text: 'FC (bpm)' },
        },
      },
    },
  });
}

function updateLFHFScale(chart, max) {
  if (!chart) return;
  chart.options.scales.y.max = max;
  chart.update('none');
}

// =============================================================================
// Évolution athlète : graphique Chart.js
// =============================================================================

let evolutionChart = null;
let evolutionData = null;

async function initEvolutionChart(athleteId) {
  if (!athleteId) return;
  try {
    const resp = await fetch(`/tests/api/athlete/${athleteId}/history`);
    if (!resp.ok) return;
    evolutionData = await resp.json();
    if (!evolutionData.tests.length) return;

    renderEvolution('sit_rmssd');

    document.querySelectorAll('input[name="metric"]').forEach(r => {
      r.addEventListener('change', (e) => renderEvolution(e.target.value));
    });
  } catch (err) {
    console.error('Erreur chargement historique', err);
  }
}

const METRIC_LABELS = {
  sit_rmssd: 'RMSSD couché (ms)',
  delta_hr: 'Delta FC (bpm)',
  sit_hf: 'HF couché (ms²)',
  std_lf_hf: 'LF/HF debout',
  sit_pns_index: 'PNS index couché',
  sit_sns_index: 'SNS index couché',
};

const FATIGUE_COLOR_MAP = {
  green: '#3B6D11',
  orange: '#BA7517',
  red: '#A32D2D',
  dark_green: '#234A06',
};

function rollingMedian(values, windowSize) {
  const out = [];
  for (let i = 0; i < values.length; i++) {
    const slice = values.slice(Math.max(0, i - windowSize + 1), i + 1)
      .filter(v => v !== null && v !== undefined && !isNaN(v));
    if (!slice.length) { out.push(null); continue; }
    const sorted = [...slice].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    out.push(sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2);
  }
  return out;
}

function renderEvolution(metric) {
  const ctx = document.getElementById('evolutionChart').getContext('2d');
  if (evolutionChart) evolutionChart.destroy();

  const tests = evolutionData.tests;
  const labels = tests.map(t => new Date(t.date));
  const values = tests.map(t => t[metric]);
  const colors = tests.map(t => FATIGUE_COLOR_MAP[t.fatigue_color] || '#888');

  // Médiane glissante : approximée par fenêtre de 5 tests (ou ~30j si plus dense)
  const median = rollingMedian(values, 5);

  evolutionChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: METRIC_LABELS[metric] || metric,
          data: values,
          borderColor: '#3B6D11',
          backgroundColor: '#3B6D1133',
          pointBackgroundColor: colors,
          pointBorderColor: colors,
          pointRadius: 5,
          pointHoverRadius: 7,
          borderWidth: 2,
          tension: 0.2,
          fill: false,
        },
        {
          label: 'Médiane glissante',
          data: median,
          borderColor: '#888',
          borderDash: [4, 4],
          pointRadius: 0,
          borderWidth: 1.5,
          fill: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        tooltip: {
          callbacks: {
            title: (items) => {
              const d = new Date(items[0].label);
              return d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
            },
            afterBody: (items) => {
              const idx = items[0].dataIndex;
              const t = tests[idx];
              const lines = [];
              if (t.comment) lines.push(`Commentaire : ${t.comment}`);
              if (t.rpe) lines.push(`RPE : ${t.rpe}/10`);
              if (t.fatigue_type && t.fatigue_type !== 'normal') lines.push(`Type : ${t.fatigue_type}`);
              return lines;
            },
          },
        },
        legend: { position: 'top' },
      },
      scales: {
        x: {
          type: 'time',
          time: { unit: 'day', displayFormats: { day: 'dd/MM' } },
          adapters: { date: {} },
        },
        y: { title: { display: true, text: METRIC_LABELS[metric] || metric } },
      },
    },
  });
}
