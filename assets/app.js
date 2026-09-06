// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

const MAX_POINTS = 200;
const MAX_TABLE_ROWS = 50;

class LineChart {
  constructor(canvas, wrap, colorVar, unit) {
    this.canvas = canvas;
    this.wrap = wrap;
    this.colorVar = colorVar;
    this.unit = unit;
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

    let min = Math.min(...this.data.map(d => d.v));
    let max = Math.max(...this.data.map(d => d.v));
    if (min === max) {
      min -= 1;
      max += 1;
    }
    const step = this.niceStep(max - min);
    const niceMin = Math.floor(min / step) * step;
    const niceMax = Math.ceil(max / step) * step;

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
const currentValueEl = document.getElementById('current-value');
const tableBody = document.getElementById('data-table-body');

const voltageChart = new LineChart(
  document.getElementById('voltage-chart'),
  document.getElementById('voltage-wrap'),
  '--series-voltage',
  'V'
);
const currentChart = new LineChart(
  document.getElementById('current-chart'),
  document.getElementById('current-wrap'),
  '--series-current',
  'A'
);

function setStatus(online) {
  statusEl.textContent = online ? 'Live' : 'Disconnected';
  statusEl.className = `status ${online ? 'status-online' : 'status-offline'}`;
}

function applyReading(reading) {
  voltageValueEl.textContent = reading.voltage.toFixed(1);
  currentValueEl.textContent = reading.current.toFixed(2);

  voltageChart.push({ t: reading.t, v: reading.voltage });
  currentChart.push({ t: reading.t, v: reading.current });

  const row = document.createElement('tr');
  const timeCell = document.createElement('td');
  timeCell.textContent = new Date(reading.t * 1000).toLocaleTimeString();
  const voltageCell = document.createElement('td');
  voltageCell.textContent = reading.voltage.toFixed(2);
  const currentCell = document.createElement('td');
  currentCell.textContent = reading.current.toFixed(2);
  row.appendChild(timeCell);
  row.appendChild(voltageCell);
  row.appendChild(currentCell);
  tableBody.insertBefore(row, tableBody.firstChild);
  while (tableBody.children.length > MAX_TABLE_ROWS) {
    tableBody.removeChild(tableBody.lastChild);
  }
}

const ui = new WebUI({
  path: '/socket.io',
  transports: ['polling', 'websocket'],
  autoConnect: true,
});

ui.on_connect(() => setStatus(true));
ui.on_disconnect(() => setStatus(false));
ui.on_message('reading', applyReading);

// Backfill chart history from the last samples the app already collected.
fetch('/samples')
  .then(r => r.json())
  .then(list => {
    if (!Array.isArray(list)) return;
    const points = list.map(s => ({ t: s.t, v: s.voltage }));
    const currents = list.map(s => ({ t: s.t, v: s.current }));
    voltageChart.setData(points);
    currentChart.setData(currents);
    list.slice(-MAX_TABLE_ROWS).forEach(applyReadingToTableOnly);
  })
  .catch(() => {});

function applyReadingToTableOnly(reading) {
  const row = document.createElement('tr');
  const timeCell = document.createElement('td');
  timeCell.textContent = new Date(reading.t * 1000).toLocaleTimeString();
  const voltageCell = document.createElement('td');
  voltageCell.textContent = reading.voltage.toFixed(2);
  const currentCell = document.createElement('td');
  currentCell.textContent = reading.current.toFixed(2);
  row.appendChild(timeCell);
  row.appendChild(voltageCell);
  row.appendChild(currentCell);
  tableBody.insertBefore(row, tableBody.firstChild);
}
