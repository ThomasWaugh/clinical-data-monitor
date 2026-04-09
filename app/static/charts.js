// Clinical Data Monitor — Dashboard JS
// Consumes two SSE streams: /stream (readings) and /events/stream (detection events)

const VITAL_COLORS = {
  heart_rate:       { line: '#f97316', point: '#fdba74' },   // orange
  spo2:             { line: '#22d3ee', point: '#67e8f9' },   // cyan
  systolic_bp:      { line: '#a78bfa', point: '#c4b5fd' },   // violet
  respiratory_rate: { line: '#4ade80', point: '#86efac' },   // green
  temperature:      { line: '#fb7185', point: '#fda4af' },   // rose
};

const SEVERITY_LABELS = {
  high: '🔴 HIGH',
  medium: '🟡 MEDIUM',
  low: '🔵 LOW',
};

const DETECTOR_LABELS = {
  cusum: 'CUSUM Drift',
  zscore: 'Z-Score Anomaly',
  evidently: 'Distribution Drift',
};

// ── Chart factory ─────────────────────────────────────────────────────────────

function makeChart(canvasId, datasets, yLabel) {
  const ctx = document.getElementById(canvasId).getContext('2d');
  return new Chart(ctx, {
    type: 'line',
    data: { datasets },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: true,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          labels: { color: '#94a3b8', font: { size: 11 } }
        },
        streaming: {
          duration: 5 * 60 * 1000,  // show last 5 minutes
          refresh: 2000,
          delay: 2000,
        },
      },
      scales: {
        x: {
          type: 'realtime',
          ticks: { color: '#475569', maxTicksLimit: 6 },
          grid: { color: '#1e293b' },
        },
        y: {
          ticks: { color: '#475569' },
          grid: { color: '#1e293b' },
          title: { display: !!yLabel, text: yLabel, color: '#64748b', font: { size: 10 } },
        },
      },
    },
  });
}

function makeDataset(key, label) {
  return {
    label,
    data: [],
    borderColor: VITAL_COLORS[key].line,
    backgroundColor: VITAL_COLORS[key].line + '15',
    borderWidth: 1.5,
    pointRadius: 0,
    pointHoverRadius: 4,
    tension: 0.3,
    fill: false,
  };
}

// ── Initialise charts ─────────────────────────────────────────────────────────

const chartHrSpo2 = makeChart('chart-hr-spo2', [
  makeDataset('heart_rate', 'Heart Rate (bpm)'),
  makeDataset('spo2', 'SpO₂ (%)'),
]);

const chartBpRr = makeChart('chart-bp-rr', [
  makeDataset('systolic_bp', 'Systolic BP (mmHg)'),
  makeDataset('respiratory_rate', 'Resp. Rate (br/min)'),
]);

const chartTemp = makeChart('chart-temp', [
  makeDataset('temperature', 'Temperature (°C)'),
]);

const CHART_MAP = {
  heart_rate: { chart: chartHrSpo2, dsIndex: 0 },
  spo2: { chart: chartHrSpo2, dsIndex: 1 },
  systolic_bp: { chart: chartBpRr, dsIndex: 0 },
  respiratory_rate: { chart: chartBpRr, dsIndex: 1 },
  temperature: { chart: chartTemp, dsIndex: 0 },
};

// ── SSE: readings stream ──────────────────────────────────────────────────────

function connectReadingsStream() {
  const es = new EventSource('/stream');
  let retryDelay = 1000;

  es.onopen = () => {
    retryDelay = 1000;
    document.getElementById('status-dot').className = 'w-2 h-2 rounded-full bg-emerald-400 animate-pulse';
    document.getElementById('stream-status').querySelector('#status-dot').nextSibling.textContent = ' Live';
    document.getElementById('stream-status').lastChild.textContent = ' Live';
  };

  es.onmessage = (e) => {
    const reading = JSON.parse(e.data);
    const now = Date.now();

    // Update vital cards
    ['heart_rate', 'spo2', 'systolic_bp', 'respiratory_rate', 'temperature'].forEach(key => {
      const el = document.getElementById('val-' + key);
      if (el) el.textContent = reading[key];
    });

    // Drift badge
    document.getElementById('drift-badge').classList.toggle('hidden', !reading.drift_active);

    // Push to charts
    Object.entries(CHART_MAP).forEach(([key, { chart, dsIndex }]) => {
      chart.data.datasets[dsIndex].data.push({ x: now, y: reading[key] });
    });
    [chartHrSpo2, chartBpRr, chartTemp].forEach(c => c.update('quiet'));
  };

  es.onerror = () => {
    es.close();
    document.getElementById('status-dot').className = 'w-2 h-2 rounded-full bg-red-400';
    document.getElementById('stream-status').lastChild.textContent = ' Reconnecting…';
    setTimeout(connectReadingsStream, retryDelay);
    retryDelay = Math.min(retryDelay * 2, 30000);
  };
}

// ── SSE: events stream ────────────────────────────────────────────────────────

let eventCount = 0;
const knownEvents = new Map();  // id → card element

function connectEventsStream() {
  const es = new EventSource('/events/stream');
  let retryDelay = 1000;

  es.onmessage = (e) => {
    const ev = JSON.parse(e.data);

    if (knownEvents.has(ev.id) && ev.explanation) {
      // Update existing card with explanation
      updateEventCard(ev.id, ev);
      return;
    }

    if (!knownEvents.has(ev.id)) {
      createEventCard(ev);
    }
  };

  es.onerror = () => {
    es.close();
    setTimeout(connectEventsStream, retryDelay);
    retryDelay = Math.min(retryDelay * 2, 30000);
  };
}

function createEventCard(ev) {
  eventCount++;
  document.getElementById('event-count').textContent = eventCount + ' event' + (eventCount !== 1 ? 's' : '');

  // Remove placeholder
  const placeholder = document.querySelector('#event-log .text-slate-600');
  if (placeholder) placeholder.remove();

  const vital = ev.vital.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  const detector = DETECTOR_LABELS[ev.detector] || ev.detector;
  const time = new Date(ev.timestamp).toLocaleTimeString();
  const severityClass = ev.severity ? 'severity-' + ev.severity : '';

  const card = document.createElement('div');
  card.id = 'event-' + ev.id;
  card.className = 'event-card ' + severityClass;
  card.innerHTML = `
    <div class="flex items-start justify-between mb-2">
      <div>
        <span class="font-bold text-white">${vital}</span>
        <span class="text-slate-400 ml-2">${detector}</span>
      </div>
      <div class="flex items-center gap-2">
        ${ev.severity ? `<span class="severity-badge">${SEVERITY_LABELS[ev.severity] || ev.severity}</span>` : ''}
        <span class="text-slate-500">${time}</span>
      </div>
    </div>
    <div class="text-slate-300 mb-2">${ev.change_summary}</div>
    <div class="explanation-area text-slate-400 italic">
      ${ev.explanation ? renderExplanation(ev.explanation) : '<span class="generating">Generating clinical explanation…</span>'}
    </div>
  `;

  const log = document.getElementById('event-log');
  log.insertBefore(card, log.firstChild);
  knownEvents.set(ev.id, card);
}

function updateEventCard(id, ev) {
  const card = knownEvents.get(id);
  if (!card) return;

  if (ev.severity) {
    card.className = 'event-card severity-' + ev.severity;
    const badge = card.querySelector('.severity-badge');
    if (badge) badge.textContent = SEVERITY_LABELS[ev.severity] || ev.severity;
  }

  const expArea = card.querySelector('.explanation-area');
  if (expArea && ev.explanation) {
    expArea.innerHTML = renderExplanation(ev.explanation);
    expArea.classList.remove('italic');
  }
}

function renderExplanation(exp) {
  return `
    <div class="bg-slate-800/60 rounded-lg p-3 mt-1 not-italic">
      <div class="font-semibold text-slate-200 mb-1">${escapeHtml(exp.headline)}</div>
      <div class="text-slate-300 mb-1">${escapeHtml(exp.explanation)}</div>
      <div class="text-slate-400 border-t border-slate-700 pt-1 mt-1">
        <span class="text-slate-500">Consider: </span>${escapeHtml(exp.suggested_action)}
      </div>
    </div>
  `;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(str || ''));
  return div.innerHTML;
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────

// Set status text correctly after initial HTML render
document.getElementById('stream-status').innerHTML =
  '<span id="status-dot" class="w-2 h-2 rounded-full bg-slate-600"></span> Connecting…';

connectReadingsStream();
connectEventsStream();
