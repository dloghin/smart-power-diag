// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

const MAX_POINTS = 200;
const MAX_TABLE_ROWS = 50;

class LineChart {
  constructor(canvas, wrap, colorVar, unit, fixedRange = null) {
    this.canvas = canvas;
    this.wrap = wrap;
    this.colorVar = colorVar;
    this.unit = unit;
    this.fixedRange = fixedRange;
    this.ctx = canvas.getContext('2d');
    this.vizRoot = document.querySelector('.viz-root');
    this.data = [];
    this.dpr = window.devicePixelRatio || 1;

    this.tooltip = document.createElement('div');
    this.tooltip.className = 'tooltip';
    wrap.appendChild(this.tooltip);

    this.crosshair = document.createElement('div');
    this.crosshair.className = 'crosshair-line';
    wrap.appendChild(this.crosshair);

    wrap.addEventListener('pointermove', e => this.onPointerMove(e));
    wrap.addEventListener('pointerleave', () => this.hideHover());

    new ResizeObserver(() => this.resize()).observe(wrap);
    this.resize();
  }

  resize() {
    const rect = this.wrap.getBoundingClientRect();
    this.width = rect.width;
    this.height = rect.height;
    this.canvas.width = Math.max(1, Math.round(this.width * this.dpr));
    this.canvas.height = Math.max(1, Math.round(this.height * this.dpr));
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.draw();
  }

  setData(list) {
    this.data = list.slice(-MAX_POINTS);
    this.draw();
  }

  push(sample) {
    this.data.push(sample);
    if (this.data.length > MAX_POINTS) this.data.shift();
    this.draw();
  }

  niceStep(range, targetTicks = 4) {
    if (range <= 0) return 1;
    const rough = range / targetTicks;
    const mag = Math.pow(10, Math.floor(Math.log10(rough)));
    const norm = rough / mag;
    let step;
    if (norm < 1.5) step = 1;
    else if (norm < 3) step = 2;
    else if (norm < 7) step = 5;
    else step = 10;
    return step * mag;
  }

  formatValue(v) {
    return Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1);
  }

  draw() {
    const ctx = this.ctx;
    const styles = getComputedStyle(this.vizRoot);
    const gridColor = styles.getPropertyValue('--gridline').trim();
    const mutedColor = styles.getPropertyValue('--text-muted').trim();
    const surfaceColor = styles.getPropertyValue('--surface-1').trim();
    const seriesColor = styles.getPropertyValue(this.colorVar).trim();

    ctx.clearRect(0, 0, this.width, this.height);
    if (this.data.length === 0) {
      this._plotBounds = null;
      return;
    }

    const padLeft = 44;
    const padRight = 8;
    const padTop = 10;
    const padBottom = 18;
    const plotW = this.width - padLeft - padRight;
    const plotH = this.height - padTop - padBottom;

    let niceMin, niceMax;
    if (this.fixedRange) {
      niceMin = this.fixedRange.min;
      niceMax = this.fixedRange.max;
    } else {
      let min = Math.min(...this.data.map(d => d.v));
      let max = Math.max(...this.data.map(d => d.v));
      if (min === max) {
        min -= 1;
        max += 1;
      }
      const step = this.niceStep(max - min);
      niceMin = Math.floor(min / step) * step;
      niceMax = Math.ceil(max / step) * step;
    }
    const step = this.niceStep(niceMax - niceMin);

    const xForIndex = i =>
      padLeft + (this.data.length === 1 ? 0 : (i / (this.data.length - 1)) * plotW);
    const yForValue = v => padTop + plotH - ((v - niceMin) / (niceMax - niceMin)) * plotH;

    // hairline gridlines + muted y-axis labels
    ctx.strokeStyle = gridColor;
    ctx.lineWidth = 1;
    ctx.fillStyle = mutedColor;
    ctx.font = '11px system-ui, sans-serif';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';

    const ticks = [];
    for (let v = niceMin; v <= niceMax + step / 2; v += step) ticks.push(v);

    ctx.beginPath();
    ticks.forEach(v => {
      const y = yForValue(v);
      ctx.moveTo(padLeft, y);
      ctx.lineTo(this.width - padRight, y);
    });
    ctx.stroke();

    ticks.forEach(v => {
      ctx.fillText(this.formatValue(v), padLeft - 8, yForValue(v));
    });

    // 2px line, round joins
    ctx.strokeStyle = seriesColor;
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.beginPath();
    this.data.forEach((d, i) => {
      const x = xForIndex(i);
      const y = yForValue(d.v);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // moving-average overlay: dashed, same color, reduced opacity
    if (this.data.some(d => d.avg !== undefined)) {
      ctx.save();
      ctx.globalAlpha = 0.55;
      ctx.strokeStyle = seriesColor;
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      let started = false;
      this.data.forEach((d, i) => {
        if (d.avg === undefined) return;
        const x = xForIndex(i);
        const y = yForValue(d.avg);
        if (!started) {
          ctx.moveTo(x, y);
          started = true;
        } else {
          ctx.lineTo(x, y);
        }
      });
      ctx.stroke();
      ctx.restore();
    }

    // end marker with a surface ring, and its direct value label
    const lastIdx = this.data.length - 1;
    const lastX = xForIndex(lastIdx);
    const lastY = yForValue(this.data[lastIdx].v);

    ctx.beginPath();
    ctx.fillStyle = surfaceColor;
    ctx.arc(lastX, lastY, 6, 0, Math.PI * 2);
    ctx.fill();

    ctx.beginPath();
    ctx.fillStyle = seriesColor;
    ctx.arc(lastX, lastY, 4, 0, Math.PI * 2);
    ctx.fill();

    this._xForIndex = xForIndex;
    this._yForValue = yForValue;
    this._plotBounds = { padLeft, plotW };
  }

  onPointerMove(e) {
    if (this.data.length === 0 || !this._plotBounds) return;
    const rect = this.wrap.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const { padLeft, plotW } = this._plotBounds;
    const clamped = Math.min(Math.max(x, padLeft), padLeft + plotW);
    const ratio = plotW === 0 ? 0 : (clamped - padLeft) / plotW;
    const idx = Math.round(ratio * (this.data.length - 1));
    const sample = this.data[idx];
    if (!sample) return;

    const px = this._xForIndex(idx);
    const py = this._yForValue(sample.v);

    this.crosshair.style.display = 'block';
    this.crosshair.style.left = `${px}px`;

    this.tooltip.style.display = 'block';
    this.tooltip.textContent = '';
    const timeEl = document.createElement('div');
    timeEl.textContent = new Date(sample.t * 1000).toLocaleTimeString();
    const valueEl = document.createElement('div');
    valueEl.className = 'tooltip-value';
    valueEl.textContent = `${sample.v.toFixed(2)} ${this.unit}`;
    this.tooltip.appendChild(timeEl);
    this.tooltip.appendChild(valueEl);

    const tooltipWidth = this.tooltip.offsetWidth || 80;
    let left = px + 12;
    if (left + tooltipWidth > this.width) left = px - tooltipWidth - 12;
    this.tooltip.style.left = `${Math.max(left, 0)}px`;
    this.tooltip.style.top = `${Math.max(py - 36, 0)}px`;
  }

  hideHover() {
    this.crosshair.style.display = 'none';
    this.tooltip.style.display = 'none';
  }
}

const statusEl = document.getElementById('status');
const voltageValueEl = document.getElementById('voltage-value');
const current1ValueEl = document.getElementById('current1-value');
const current2ValueEl = document.getElementById('current2-value');
const frequencyValueEl = document.getElementById('frequency-value');
const power1ValueEl = document.getElementById('power1-value');
const power2ValueEl = document.getElementById('power2-value');
const tableBody = document.getElementById('data-table-body');
const plug1Button = document.getElementById('plug1-toggle');
const plug1ToggleLabel = document.getElementById('plug1-toggle-label');
const plug2Button = document.getElementById('plug2-toggle');
const plug2ToggleLabel = document.getElementById('plug2-toggle-label');
const rawChartsButton = document.getElementById('raw-charts-toggle');
const rawChartsToggleLabel = document.getElementById('raw-charts-toggle-label');
const vizRoot = document.querySelector('.viz-root');

// Each sensor keeps its own color across its raw and calibrated chart.
const charts = {
  voltageRaw: new LineChart(
    document.getElementById('voltage-raw-chart'),
    document.getElementById('voltage-raw-wrap'),
    '--series-voltage',
    'V'
  ),
  voltageCal: new LineChart(
    document.getElementById('voltage-cal-chart'),
    document.getElementById('voltage-cal-wrap'),
    '--series-voltage',
    'V',
    { min: 0, max: 300 }
  ),
  current1Raw: new LineChart(
    document.getElementById('current1-raw-chart'),
    document.getElementById('current1-raw-wrap'),
    '--series-current1',
    'V'
  ),
  current1Cal: new LineChart(
    document.getElementById('current1-cal-chart'),
    document.getElementById('current1-cal-wrap'),
    '--series-current1',
    'A',
    { min: 0, max: 5 }
  ),
  current2Raw: new LineChart(
    document.getElementById('current2-raw-chart'),
    document.getElementById('current2-raw-wrap'),
    '--series-current2',
    'V'
  ),
  current2Cal: new LineChart(
    document.getElementById('current2-cal-chart'),
    document.getElementById('current2-cal-wrap'),
    '--series-current2',
    'A',
    { min: 0, max: 5 }
  ),
};

function setStatus(online) {
  statusEl.textContent = online ? 'Live' : 'Disconnected';
  statusEl.className = `status ${online ? 'status-online' : 'status-offline'}`;
}

function applyPlug1State(state) {
  const on = !!(state && state.on);
  plug1Button.setAttribute('aria-pressed', String(on));
  plug1ToggleLabel.textContent = on ? 'On' : 'Off';
}

function applyPlug2State(state) {
  const on = !!(state && state.on);
  plug2Button.setAttribute('aria-pressed', String(on));
  plug2ToggleLabel.textContent = on ? 'On' : 'Off';
}

function togglePlug1() {
  const nextOn = plug1Button.getAttribute('aria-pressed') !== 'true';
  ui.send_message('set_plug1', { on: nextOn });
}

function togglePlug2() {
  const nextOn = plug2Button.getAttribute('aria-pressed') !== 'true';
  ui.send_message('set_plug2', { on: nextOn });
}

plug1Button.addEventListener('click', togglePlug1);
plug2Button.addEventListener('click', togglePlug2);

function toggleRawCharts() {
  const shown = rawChartsButton.getAttribute('aria-pressed') === 'true';
  const nextShown = !shown;
  rawChartsButton.setAttribute('aria-pressed', String(nextShown));
  rawChartsToggleLabel.textContent = nextShown ? 'Shown' : 'Hidden';
  vizRoot.classList.toggle('hide-raw-charts', !nextShown);
  [charts.voltageRaw, charts.current1Raw, charts.current2Raw].forEach(c => c.resize());
}

rawChartsButton.addEventListener('click', toggleRawCharts);

function addTableRow(reading) {
  const row = document.createElement('tr');
  const cells = [
    new Date(reading.t * 1000).toLocaleTimeString(),
    reading.voltage_cal.toFixed(2),
    reading.current1_cal.toFixed(2),
    reading.current2_cal.toFixed(2),
  ];
  cells.forEach(text => {
    const cell = document.createElement('td');
    cell.textContent = text;
    row.appendChild(cell);
  });
  tableBody.insertBefore(row, tableBody.firstChild);
  while (tableBody.children.length > MAX_TABLE_ROWS) {
    tableBody.removeChild(tableBody.lastChild);
  }
}

const AVG_WINDOW = 10;
let voltageAvgWindow = [];
let current1AvgWindow = [];
let current2AvgWindow = [];

function pushAvgWindow(window, value) {
  window.push(value);
  if (window.length > AVG_WINDOW) window.shift();
  return window.reduce((a, b) => a + b, 0) / window.length;
}

// Recomputes a full {t, v, avg} series from historical samples for chart backfill.
function withMovingAverage(list, key) {
  const window = [];
  return list.map(s => ({ t: s.t, v: s[key], avg: pushAvgWindow(window, s[key]) }));
}

function applyReading(reading) {
  voltageValueEl.textContent = reading.voltage_cal.toFixed(1);
  current1ValueEl.textContent = reading.current1_cal.toFixed(2);
  current2ValueEl.textContent = reading.current2_cal.toFixed(2);
  frequencyValueEl.textContent = reading.frequency.toFixed(2);
  power1ValueEl.textContent = reading.power1.toFixed(1);
  power2ValueEl.textContent = reading.power2.toFixed(1);

  const voltageAvg = pushAvgWindow(voltageAvgWindow, reading.voltage_cal);
  const current1Avg = pushAvgWindow(current1AvgWindow, reading.current1_cal);
  const current2Avg = pushAvgWindow(current2AvgWindow, reading.current2_cal);

  charts.voltageRaw.push({ t: reading.t, v: reading.voltage_raw });
  charts.voltageCal.push({ t: reading.t, v: reading.voltage_cal, avg: voltageAvg });
  charts.current1Raw.push({ t: reading.t, v: reading.current1_raw });
  charts.current1Cal.push({ t: reading.t, v: reading.current1_cal, avg: current1Avg });
  charts.current2Raw.push({ t: reading.t, v: reading.current2_raw });
  charts.current2Cal.push({ t: reading.t, v: reading.current2_cal, avg: current2Avg });

  addTableRow(reading);
}

const ui = new WebUI({
  path: '/socket.io',
  transports: ['polling', 'websocket'],
  autoConnect: true,
});

ui.on_connect(() => {
  setStatus(true);
  // Pick up the plug's current state whenever the socket (re)connects.
  ui.send_message('get_plug1');
  ui.send_message('get_plug2');
});
ui.on_disconnect(() => setStatus(false));
ui.on_message('reading', applyReading);
ui.on_message('plug1', applyPlug1State);
ui.on_message('plug2', applyPlug2State);

// Backfill chart history from the last samples the app already collected.
fetch('/samples')
  .then(r => r.json())
  .then(list => {
    if (!Array.isArray(list)) return;
    charts.voltageRaw.setData(list.map(s => ({ t: s.t, v: s.voltage_raw })));
    charts.voltageCal.setData(withMovingAverage(list, 'voltage_cal'));
    charts.current1Raw.setData(list.map(s => ({ t: s.t, v: s.current1_raw })));
    charts.current1Cal.setData(withMovingAverage(list, 'current1_cal'));
    charts.current2Raw.setData(list.map(s => ({ t: s.t, v: s.current2_raw })));
    charts.current2Cal.setData(withMovingAverage(list, 'current2_cal'));

    // keep the live rolling windows continuous with the backfilled history
    voltageAvgWindow = list.slice(-AVG_WINDOW).map(s => s.voltage_cal);
    current1AvgWindow = list.slice(-AVG_WINDOW).map(s => s.current1_cal);
    current2AvgWindow = list.slice(-AVG_WINDOW).map(s => s.current2_cal);

    list.slice(-MAX_TABLE_ROWS).forEach(addTableRow);
  })
  .catch(() => {});
