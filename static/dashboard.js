const fmt1 = (v) => (v === null || v === undefined || isNaN(v)) ? '—' : Number(v).toFixed(1);
const fmt0 = (v) => (v === null || v === undefined || isNaN(v)) ? '—' : Number(v).toFixed(0);
const esc = (s) => (s ?? '').toString().replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
// bpd -> kbd (千桶/日), the unit used everywhere else in this dashboard for refinery capacity
const fmtKbd = (bpd) => (bpd === null || bpd === undefined || isNaN(bpd)) ? '—' : Number(bpd / 1000).toFixed(bpd >= 10000 ? 0 : 1);

// Populated as each module loads; read by renderSummary() at the end so the homepage tab can
// compose a cross-module executive briefing without re-fetching anything.
const STATE = {usRisk: null, euRisk: null, usWm: null, euWm: null, kaub: null, agri: null, wasde: null};

function goToTab(page) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.page === page));
  document.querySelectorAll('section.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + page));
}

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => goToTab(tab.dataset.page));
});

// Temperature color scale: blue (cold) -> green -> yellow -> orange -> deep red (hot)
const TEMP_STOPS = [
  [-10, [43,108,176]], [10,[66,153,225]], [20,[72,187,120]], [28,[236,201,75]],
  [33,[237,137,54]], [38,[245,101,101]], [45,[197,48,48]],
];
function rgbToHex([r,g,b]) { return '#' + [r,g,b].map(v => Math.round(v).toString(16).padStart(2,'0')).join(''); }
function tempColor(t) {
  if (t === null || t === undefined || isNaN(t)) return '#8b949e';
  if (t <= TEMP_STOPS[0][0]) return rgbToHex(TEMP_STOPS[0][1]);
  for (let i = 0; i < TEMP_STOPS.length - 1; i++) {
    const [t0, c0] = TEMP_STOPS[i], [t1, c1] = TEMP_STOPS[i+1];
    if (t >= t0 && t <= t1) {
      const f = (t - t0) / (t1 - t0);
      return rgbToHex(c0.map((v, idx) => v + (c1[idx] - v) * f));
    }
  }
  return rgbToHex(TEMP_STOPS[TEMP_STOPS.length - 1][1]);
}

// Real state/country boundary maps via d3-geo + TopoJSON (Natural Earth data, jsdelivr CDN —
// same host already proven reachable in this sandbox for chart.js). No basemap tile server
// involved, so there's nothing that can hang waiting on unreachable imagery.
const US_STATE_NAMES = {
  AL:'Alabama',AK:'Alaska',AZ:'Arizona',AR:'Arkansas',CA:'California',CO:'Colorado',CT:'Connecticut',
  DE:'Delaware',FL:'Florida',GA:'Georgia',HI:'Hawaii',ID:'Idaho',IL:'Illinois',IN:'Indiana',IA:'Iowa',
  KS:'Kansas',KY:'Kentucky',LA:'Louisiana',ME:'Maine',MD:'Maryland',MA:'Massachusetts',MI:'Michigan',
  MN:'Minnesota',MS:'Mississippi',MO:'Missouri',MT:'Montana',NE:'Nebraska',NV:'Nevada',NH:'New Hampshire',
  NJ:'New Jersey',NM:'New Mexico',NY:'New York',NC:'North Carolina',ND:'North Dakota',OH:'Ohio',
  OK:'Oklahoma',OR:'Oregon',PA:'Pennsylvania',RI:'Rhode Island',SC:'South Carolina',SD:'South Dakota',
  TN:'Tennessee',TX:'Texas',UT:'Utah',VT:'Vermont',VA:'Virginia',WA:'Washington',WV:'West Virginia',
  WI:'Wisconsin',WY:'Wyoming',
};
const EU_COUNTRY_NAME_FIX = {UK: 'United Kingdom'};
const EU_MAP_COUNTRIES = [
  'Portugal','Spain','France','United Kingdom','Ireland','Netherlands','Belgium','Luxembourg','Germany',
  'Switzerland','Austria','Italy','Denmark','Norway','Sweden','Finland','Poland','Czechia','Slovakia',
  'Hungary','Slovenia','Croatia','Bosnia and Herz.','Serbia','Montenegro','Albania','Macedonia',
  'Greece','Bulgaria','Romania','Estonia','Latvia','Lithuania','Iceland',
];

let usGeoPromise = null, worldGeoPromise = null;
function loadUsGeo() {
  if (!usGeoPromise) {
    usGeoPromise = fetch('https://cdn.jsdelivr.net/npm/us-atlas@3/states-10m.json')
      .then(r => r.json())
      .then(topo => topojson.feature(topo, topo.objects.states));
  }
  return usGeoPromise;
}
function loadWorldGeo() {
  if (!worldGeoPromise) {
    worldGeoPromise = fetch('https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json')
      .then(r => r.json())
      .then(topo => topojson.feature(topo, topo.objects.countries));
  }
  return worldGeoPromise;
}

async function renderTempMap(mapKey, containerId, legendId, refineries) {
  const container = document.getElementById(containerId);
  const W = container.clientWidth || 800, H = 460;

  let features, keyFn, projection;
  if (mapKey === 'us') {
    const geo = await loadUsGeo();
    features = geo.features;
    keyFn = (f) => Object.entries(US_STATE_NAMES).find(([,name]) => name === f.properties.name)?.[0];
    projection = d3.geoAlbersUsa();
  } else {
    const geo = await loadWorldGeo();
    // Several countries (France, Netherlands, UK...) bundle far-flung overseas territories into
    // the same MultiPolygon; strip those rings so fitSize zooms to mainland Europe, not the Caribbean.
    const inEuropeBox = ([lon, lat]) => lon >= -30 && lon <= 45 && lat >= 33 && lat <= 72;
    const clipToEurope = (f) => {
      if (f.geometry.type === 'Polygon') {
        return inEuropeBox(f.geometry.coordinates[0][0]) ? f : null;
      }
      if (f.geometry.type === 'MultiPolygon') {
        const kept = f.geometry.coordinates.filter(poly => inEuropeBox(poly[0][0]));
        return kept.length ? {...f, geometry: {type: 'MultiPolygon', coordinates: kept}} : null;
      }
      return f;
    };
    features = geo.features
      .filter(f => EU_MAP_COUNTRIES.includes(f.properties.name))
      .map(clipToEurope)
      .filter(Boolean);
    keyFn = (f) => f.properties.name;
    projection = d3.geoMercator();
  }

  const refByRegionKey = {};
  refineries.forEach(r => {
    const k = mapKey === 'us' ? r.state : (EU_COUNTRY_NAME_FIX[r.country] || r.country);
    (refByRegionKey[k] = refByRegionKey[k] || []).push(r);
  });

  const featureCollection = {type: 'FeatureCollection', features};
  projection.fitSize([W, H], featureCollection);
  const path = d3.geoPath(projection);

  container.innerHTML = '';
  const svg = d3.select(container).append('svg')
    .attr('viewBox', `0 0 ${W} ${H}`).attr('width', '100%').style('background', '#0b0f14');

  svg.append('g').selectAll('path').data(features).join('path')
    .attr('d', path)
    .attr('fill', d => {
      const key = keyFn(d);
      const regionRefs = refByRegionKey[key];
      if (!regionRefs) return '#1c2230';
      const maxT = Math.max(...regionRefs.map(r => r.risk.peak_forecast_tmax_c ?? -999));
      return tempColor(maxT === -999 ? null : maxT);
    })
    .attr('fill-opacity', d => refByRegionKey[keyFn(d)] ? 0.75 : 1)
    .attr('stroke', '#30363d').attr('stroke-width', 0.8)
    .append('title').text(d => {
      const key = keyFn(d);
      const regionRefs = refByRegionKey[key];
      if (!regionRefs) return d.properties.name;
      const maxT = Math.max(...regionRefs.map(r => r.risk.peak_forecast_tmax_c ?? -999));
      return `${d.properties.name}\n炼厂: ${regionRefs.length} 座\n区内未来7日最高温: ${fmt1(maxT === -999 ? null : maxT)}°C`;
    });

  const capMax = Math.max(...refineries.map(r => r.capacity_kbd));
  svg.append('g').selectAll('circle').data(refineries).join('circle')
    .attr('class', 'refinery-dot')
    .attr('cx', r => projection([r.lon, r.lat])?.[0] ?? -100)
    .attr('cy', r => projection([r.lon, r.lat])?.[1] ?? -100)
    .attr('r', r => 3.5 + (r.capacity_kbd / capMax) * 9)
    .attr('fill', r => tempColor(r.risk.peak_forecast_tmax_c))
    .attr('fill-opacity', 0.95)
    .attr('stroke', '#0d1117').attr('stroke-width', 1.2)
    .append('title').text(r =>
      `${r.name} (${r.region})\n产能: ${fmt0(r.capacity_kbd)} 千桶/日\n未来7日最高温: ${fmt1(r.risk.peak_forecast_tmax_c)}°C\n高温风险: ${{high:'高',moderate:'中',low:'低'}[r.risk.heat_risk]||'—'}`
    );

  document.getElementById(legendId).innerHTML =
    `<span>冷</span><div class="bar"></div><span>热 (≥40°C)</span>&nbsp;&nbsp;<span class="muted">州/国区块颜色=区内炼厂未来7日最高温峰值；● 大小=单个炼厂产能，悬停查看详情</span>`;
}

function riskBadge(level) {
  return `<span class="badge ${level}">${{high:'高', moderate:'中', low:'低'}[level] || level}</span>`;
}
function trendBadge(t) {
  const label = {worsening:'恶化', improving:'好转', steady:'持平'}[t] || t;
  return `<span class="badge ${t}">${label}</span>`;
}

async function loadRefineryPanel(market) {
  const prefix = market.toLowerCase();
  const res = await fetch(`/api/refinery_risk?market=${market}`);
  const data = await res.json();
  STATE[prefix + 'Risk'] = data;

  renderTempMap(prefix, `${prefix}-map`, `${prefix}-map-legend`, data.refineries);

  const counts = {heat: {high:0,moderate:0,low:0}, storm: {high:0,moderate:0,low:0}, freeze: {high:0,moderate:0,low:0}};
  data.refineries.forEach(r => {
    const risk = r.risk || {};
    if (risk.heat_risk) counts.heat[risk.heat_risk]++;
    if (risk.storm_flood_risk) counts.storm[risk.storm_flood_risk]++;
    if (risk.freeze_snow_risk) counts.freeze[risk.freeze_snow_risk]++;
  });

  const statsEl = document.getElementById(`${prefix}-stats`);
  statsEl.innerHTML = `
    <div class="stat high"><div class="n">${counts.heat.high}</div><div class="l">高温高风险炼厂</div></div>
    <div class="stat high"><div class="n">${counts.storm.high}</div><div class="l">雷暴/洪水高风险炼厂</div></div>
    <div class="stat high"><div class="n">${counts.freeze.high}</div><div class="l">寒潮/积雪高风险炼厂</div></div>
    <div class="stat"><div class="n">${data.refineries.length}</div><div class="l">监控炼厂总数</div></div>
  `;

  const totalCap = data.refineries.reduce((s, r) => s + r.capacity_kbd, 0);
  const heatHighCap = data.refineries.filter(r => r.risk.heat_risk === 'high').reduce((s, r) => s + r.capacity_kbd, 0);
  const stormHighCap = data.refineries.filter(r => r.risk.storm_flood_risk === 'high').reduce((s, r) => s + r.capacity_kbd, 0);
  const freezeHighCap = data.refineries.filter(r => r.risk.freeze_snow_risk === 'high').reduce((s, r) => s + r.capacity_kbd, 0);
  const namesFor = (field) => data.refineries.filter(r => r.risk[field] === 'high').map(r => r.name).join('、');

  let analystHtml = `<h3>分析师解读</h3><ul>`;
  analystHtml += `<li>覆盖产能约 ${fmt0(totalCap)} 千桶/日。`;
  if (heatHighCap > 0) analystHtml += ` 其中 <b>${fmt0(heatHighCap)}</b> 千桶/日产能未来7天面临高温高风险（${esc(namesFor('heat_risk'))}），高温可能导致冷却塔效率下降、空冷器降负荷，历史上是夏季非计划降负荷的常见诱因。`;
  else analystHtml += ` 未来7天未见高温高风险炼厂。`;
  analystHtml += `</li>`;
  if (stormHighCap > 0) analystHtml += `<li><b>${fmt0(stormHighCap)}</b> 千桶/日产能面临雷暴/强降水/大风高风险（${esc(namesFor('storm_flood_risk'))}），需关注短期停电、内涝及装置紧急停车风险。</li>`;
  else analystHtml += `<li>未来7天未见雷暴/洪水高风险炼厂。</li>`;
  if (freezeHighCap > 0) analystHtml += `<li><b>${fmt0(freezeHighCap)}</b> 千桶/日产能面临寒潮/积雪高风险（${esc(namesFor('freeze_snow_risk'))}）——参考2021年德州寒潮教训，未按严寒设计的墨西哥湾/地中海炼厂对低温更敏感。</li>`;
  else analystHtml += `<li>未来7天未见寒潮/积雪高风险炼厂。</li>`;
  analystHtml += `</ul>`;
  document.getElementById(`${prefix}-analyst`).innerHTML = analystHtml;

  const tbody = document.querySelector(`#${prefix}-refinery-table tbody`);
  tbody.innerHTML = data.refineries
    .slice()
    .sort((a, b) => b.capacity_kbd - a.capacity_kbd)
    .map(r => `<tr>
      <td>${esc(r.name)}<div class="muted" style="font-size:11px">${esc(r.company)}</div></td>
      <td>${esc(r.region)}</td>
      <td>${esc(r.state || r.country)}</td>
      <td class="num">${fmt0(r.capacity_kbd)}</td>
      <td class="num">${fmt1(r.risk.current_temp_c)}°C</td>
      <td class="num">${fmt1(r.risk.peak_forecast_tmax_c)}°C</td>
      <td>${riskBadge(r.risk.heat_risk)}</td>
      <td>${riskBadge(r.risk.storm_flood_risk)}</td>
      <td>${riskBadge(r.risk.freeze_snow_risk)}</td>
    </tr>`).join('');

  if (market === 'US') {
    const dtbody = document.querySelector('#us-drought-table tbody');
    dtbody.innerHTML = data.drought.map(d => `<tr>
      <td>${d.state}</td><td>${d.as_of}</td>
      <td class="num">${fmt1(d.none_pct)}%</td><td class="num">${fmt1(d.d0_abnormally_dry_pct)}%</td>
      <td class="num">${fmt1(d.d1_moderate_pct)}%</td>
      <td class="num" style="color:${d.d2_severe_plus_pct>20?'var(--red)':'inherit'}">${fmt1(d.d2_severe_plus_pct)}%</td>
      <td class="num">${fmt1(d.d3_extreme_plus_pct)}%</td>
      <td>${trendBadge(d.trend)}</td>
    </tr>`).join('');
  } else {
    const dtbody = document.querySelector('#eu-drought-table tbody');
    dtbody.innerHTML = data.drought.map(d => `<tr>
      <td>${esc(d.region)}</td>
      <td class="num">${fmt1(d.trailing_90d_precip_mm)}</td>
      <td class="num">${fmt1(d.climatology_avg_90d_precip_mm)}</td>
      <td class="num" style="color:${d.anomaly_pct<-20?'var(--red)':'inherit'}">${d.anomaly_pct>0?'+':''}${fmt1(d.anomaly_pct)}%</td>
      <td><span class="badge ${d.status==='normal'?'low':(d.status==='surplus'?'low':'high')}">${
        {severe_deficit:'严重偏干',moderate_deficit:'中度偏干',surplus:'偏湿',normal:'正常'}[d.status] || d.status
      }</span></td>
    </tr>`).join('');
  }
}

const RISK_LABEL = {high: '高温高风险', moderate: '高温中等风险'};
const STORM_LABEL = {high: '雷暴/洪水高风险', moderate: '雷暴/洪水中等风险'};
const FREEZE_LABEL = {high: '寒潮/积雪高风险', moderate: '寒潮/积雪中等风险'};

// 1-10 shutdown-risk score: weather forecast risk (0-9, 3pts per high category / 1pt per moderate)
// blended with current WoodMac outage severity (0-5, scaled by % of nameplate CDU capacity down;
// a flat 1.5 if only non-primary units are down), capped at 10.
function computeSeverityScore(r, outage, cduPct) {
  const wp = {high: 3, moderate: 1, low: 0};
  const weatherPts = (wp[r.risk.heat_risk] || 0) + (wp[r.risk.storm_flood_risk] || 0) + (wp[r.risk.freeze_snow_risk] || 0);
  let outagePts = 0;
  if (outage.cdu > 0) outagePts = Math.min(cduPct, 100) / 100 * 5;
  else if (outage.total > 0) outagePts = 1.5;
  return Math.min(weatherPts + outagePts, 10);
}

function severityBadgeClass(score) {
  return score >= 7 ? 'hm-high' : (score >= 4 ? 'hm-moderate' : 'hm-low');
}

function renderRiskHeatmap(containerId, riskData, wmData) {
  const container = document.getElementById(containerId);
  if (!riskData) { container.innerHTML = ''; return; }
  const refs = riskData.refineries || [];

  // CDU (crude distillation) capacity is directly comparable to a refinery's nameplate rating;
  // downstream units (VDU/FCC/hydrotreaters) have their own separate ratings for a fraction of the
  // CDU's output, so summing every OFF unit against nameplate would double-count past 100%.
  const outageByRefinery = {};
  if (wmData && wmData.facility_cards) {
    wmData.facility_cards.forEach(c => {
      if (c.matched_refinery) {
        const prev = outageByRefinery[c.matched_refinery] || {total: 0, cdu: 0};
        outageByRefinery[c.matched_refinery] = {
          total: prev.total + (c.total_capacity_off || 0),
          cdu: prev.cdu + (c.cdu_capacity_off || 0),
        };
      }
    });
  }

  const scored = refs.map(r => {
    const outage = outageByRefinery[r.name] || {total: 0, cdu: 0};
    const cduPct = r.capacity_kbd && outage.cdu ? (outage.cdu / 1000) / r.capacity_kbd * 100 : 0;
    const score = computeSeverityScore(r, outage, cduPct);
    const reasons = [];
    if (RISK_LABEL[r.risk.heat_risk]) reasons.push(RISK_LABEL[r.risk.heat_risk]);
    if (STORM_LABEL[r.risk.storm_flood_risk]) reasons.push(STORM_LABEL[r.risk.storm_flood_risk]);
    if (FREEZE_LABEL[r.risk.freeze_snow_risk]) reasons.push(FREEZE_LABEL[r.risk.freeze_snow_risk]);
    if (outage.cdu > 0) reasons.push(`主装置停车${fmtKbd(outage.cdu)}千桶/日 (${cduPct >= 100 ? '≥100' : cduPct.toFixed(0)}%)`);
    else if (outage.total > 0) reasons.push(`非主装置停车${fmtKbd(outage.total)}千桶/日`);
    return {r, score, reasons};
  })
    .filter(x => x.score >= 2)
    .sort((a, b) => b.score - a.score);

  if (!scored.length) {
    container.innerHTML = `<h4>炼厂关停风险评分（1-10分，越高越严重）</h4><div class="muted" style="padding:8px 0">未来7天及当前 WoodMac 停运数据中，暂无需要重点关注的炼厂。</div>`;
    return;
  }

  const bodyRows = scored.map(({r, score, reasons}) => `<tr>
    <td class="hm-name">${esc(r.name)}</td>
    <td class="hm-cap">${fmt0(r.capacity_kbd)}千桶/日</td>
    <td class="${severityBadgeClass(score)}" style="font-weight:700">${score.toFixed(1)}</td>
    <td class="hm-name" style="text-align:left">${reasons.join('、')}</td>
  </tr>`).join('');

  container.innerHTML = `
    <h4>炼厂关停风险评分（1-10分，越高越严重，只列出有明显风险信号的炼厂）</h4>
    <table class="heatmap-table">
      <thead><tr><th>炼厂</th><th>产能</th><th>风险评分</th><th>具体风险</th></tr></thead>
      <tbody>${bodyRows}</tbody>
    </table>`;
}

function weatherFlagHtml(risk) {
  if (!risk) return '<span class="muted">—</span>';
  const flags = [];
  if (risk.heat_risk === 'high') flags.push('🔥高温');
  if (risk.storm_flood_risk === 'high') flags.push('⛈️雷暴/洪水');
  if (risk.freeze_snow_risk === 'high') flags.push('❄️寒潮');
  if (!flags.length) return '<span class="muted">正常</span>';
  return `<span class="badge high">⚠️ ${flags.join(' ')}</span>`;
}

const wmChartInstances = {};
function renderWmUtilChart(prefix, utilization) {
  const ctx = document.getElementById(`${prefix}-wm-util-chart`).getContext('2d');
  if (wmChartInstances[prefix]) wmChartInstances[prefix].destroy();
  wmChartInstances[prefix] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: utilization.map(r => r.region),
      datasets: [
        {label: '一次加工', data: utilization.map(r => r.primary.util_pct), backgroundColor: '#58a6ff'},
        {label: '轻质品', data: utilization.map(r => r.light.util_pct), backgroundColor: '#3fb950'},
        {label: '中质馏分', data: utilization.map(r => r.middle.util_pct), backgroundColor: '#d29922'},
        {label: '重质品', data: utilization.map(r => r.heavy.util_pct), backgroundColor: '#f85149'},
      ],
    },
    options: {
      responsive: true,
      scales: {
        x: {ticks: {color: '#8b949e'}, grid: {display: false}},
        y: {min: 0, max: 100, title: {display: true, text: '开工率 %', color: '#8b949e'},
            ticks: {color: '#8b949e'}, grid: {color: '#30363d'}},
      },
      plugins: {legend: {labels: {color: '#e6edf3'}}},
    },
  });
}

function wmCardHtml(card, todaySet) {
  const unitRows = card.units.map(u => {
    const isToday = todaySet && todaySet.has(`${card.facility}|${u.unit_name}`);
    return `<div class="wm-unit-row">
    <span class="un">${isToday ? '<span title="最近24小时内变化">🕐</span> ' : ''}${esc(u.unit_name)}</span>
    <span class="cap">${fmtKbd(u.capacity_bpd)} 千桶/日</span>
    <span class="st ${u.status.toLowerCase()}">${u.status === 'OFF' ? '停车' : '开启/恢复'}
      ${u.comment ? `<span class="since">${esc(u.comment.replace(/^since\s*/i, ''))}</span>` : ''}
    </span>
  </div>`;
  }).join('');
  return `<div class="wm-card ${card.weather_flagged ? 'flagged' : ''}">
    <div class="top">
      <div><h4>${esc(card.matched_refinery || card.facility)}</h4><div class="sub">${esc(card.subregion)}${card.matched_refinery ? '' : ' · 未匹配天气模型'}</div></div>
      ${card.weather_flagged ? `<span class="badge high">⚠️</span>` : ''}
    </div>
    ${unitRows}
  </div>`;
}

async function loadWmModule(market) {
  const prefix = market.toLowerCase();
  const res = await fetch(`/api/wm_refinery?market=${market}`);
  const d = await res.json();
  STATE[prefix + 'Wm'] = d;
  if (d.error) {
    document.getElementById(`${prefix}-wm-analyst`).innerHTML = `<span class="muted">${esc(d.error)}</span>`;
    return;
  }

  renderWmUtilChart(prefix, d.utilization);

  const flaggedCount = d.weather_flagged_outages.length;
  const worst = d.worst_utilization_region;
  const catLabel = {primary: '一次加工', light: '轻质品', middle: '中质馏分', heavy: '重质品'};
  const catBreakdown = Object.entries(d.offline_by_category_kbd || {})
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${catLabel[k] || k} ${fmt1(v)}`).join(' / ');
  document.getElementById(`${prefix}-wm-analyst`).innerHTML = `<h3>Wood Mackenzie 数据解读（截至 ${esc(d.report_date)}）</h3><ul>
    <li>当前合计 <b>${fmt1(d.total_offline_kbd)}</b> 千桶/日产能停车在册（${catBreakdown}，单位：千桶/日）。</li>
    <li>一次加工开工率最低的地区是 <b>${esc(worst.name)}</b>（${fmt1(worst.primary_util_pct)}%）。</li>
    <li>当前记录在案的停车装置中，<b>${d.long_running_outage_count}</b> 个已停车 ≥30 天（长期检修/关停），<b>${d.recent_outage_count}</b> 个是最近14天内新增停车。</li>
    ${d.todays_changes.length ? `<li><b style="color:var(--amber)">🕐 最近24小时内 ${d.todays_changes.length}</b> 起装置状态变化，详见下方卡片标注。</li>` : ''}
    ${flaggedCount ? `<li><b style="color:var(--red)">⚠️ ${flaggedCount}</b> 个停车装置所属炼厂同时命中未来7天天气高风险——已停车叠加天气事件，重启/检修进度可能进一步延后：${
      d.weather_flagged_outages.slice(0,5).map(o=>esc(o.matched_refinery)).join('、')
    }</li>` : '<li>当前停车装置暂未发现与天气高风险叠加的情况。</li>'}
  </ul>`;

  const todaySet = new Set(d.todays_changes.map(c => `${c.facility}|${c.unit_name}`));
  document.getElementById(`${prefix}-wm-cards`).innerHTML = d.facility_cards.map(c => wmCardHtml(c, todaySet)).join('');
}

let kaubChartInstance = null;
async function loadKaub() {
  const res = await fetch('/api/kaub');
  const d = await res.json();
  STATE.kaub = d;

  const kpis = document.getElementById('kaub-kpis');
  const belowLow = d.current_level_cm !== null && d.current_level_cm <= d.low_water_threshold_cm;
  kpis.innerHTML = `
    <div class="kpi"><div class="n" style="color:${belowLow?'var(--red)':'var(--fg)'}">${fmt0(d.current_level_cm)} cm</div><div class="l">当前实测水位（Pegelonline）</div></div>
    <div class="kpi"><div class="n">${d.low_water_threshold_cm} cm</div><div class="l">枯水关口阈值</div></div>
    <div class="kpi"><div class="n">${d.forecast_days_below_low_water}</div><div class="l">未来展望期内预计低于关口的天数</div></div>
    <div class="kpi"><div class="n">${(d.rating_curve_r2*100).toFixed(0)}%</div><div class="l">流量→水位换算拟合度 (R², ${d.rating_curve_confidence})</div></div>
  `;

  const labels = d.series.map(r => r.date);
  const actual = d.series.map(r => r.level_actual_cm);
  const modeled = d.series.map(r => r.level_modeled_cm);
  const threshold = d.series.map(() => d.low_water_threshold_cm);

  const ctx = document.getElementById('kaubChart').getContext('2d');
  if (kaubChartInstance) kaubChartInstance.destroy();
  kaubChartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {label: '实测水位 (cm)', data: actual, borderColor: '#58a6ff', backgroundColor: 'transparent', pointRadius: 0, borderWidth: 2, spanGaps: true},
        {label: '模型估算水位 (cm)', data: modeled, borderColor: '#d29922', backgroundColor: 'rgba(210,153,34,.08)', pointRadius: 0, borderWidth: 1.5, fill: false},
        {label: '枯水阈值 78cm', data: threshold, borderColor: '#f85149', borderDash: [6,4], pointRadius: 0, borderWidth: 1},
      ],
    },
    options: {
      responsive: true,
      interaction: {mode: 'index', intersect: false},
      scales: {
        x: {ticks: {maxTicksLimit: 14, color: '#8b949e'}, grid: {color: '#30363d'}},
        y: {title: {display: true, text: 'cm', color: '#8b949e'}, ticks: {color: '#8b949e'}, grid: {color: '#30363d'}},
      },
      plugins: {legend: {labels: {color: '#e6edf3'}}},
    },
  });

  const breachNote = d.first_low_water_breach
    ? `模型显示水位预计将于 <b>${d.first_low_water_breach}</b> 前后跌破枯水阈值，展望期内共有 <b>${d.forecast_days_below_low_water}</b> 天低于该关口——若成真，内河驳船需减载运营，莱茵河沿岸炼厂/化工厂原料及产品运输成本可能上升（历史参考：2018年、2022年枯水导致驳船附加费大幅上涨）。`
    : `展望期内模型未显示水位跌破枯水阈值，但换算拟合度为 ${d.rating_curve_confidence}（R²=${d.rating_curve_r2}），建议结合实测水位持续跟踪，不作为唯一决策依据。`;
  document.getElementById('kaub-analyst').innerHTML = `<h3>分析师解读</h3><p>${breachNote}</p>`;
}

function sparklineSvg(vals) {
  if (!vals || vals.length < 2) return '';
  const w = 240, h = 40, pad = 3;
  const min = Math.min(...vals), max = Math.max(...vals);
  const range = (max - min) || 1;
  const pts = vals.map((v, i) => {
    const x = pad + (i / (vals.length - 1)) * (w - 2*pad);
    const y = h - pad - ((v - min) / range) * (h - 2*pad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const up = vals[vals.length-1] >= vals[0];
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" fill="none" stroke="${up?'#3fb950':'#f85149'}" stroke-width="1.6"/></svg>`;
}

let conditionChartInstance = null;
function renderConditionChart(conditions) {
  const entries = Object.entries(conditions);
  const labels = entries.map(([k]) => k.replace(' Condition', ''));
  const ctx = document.getElementById('conditionChart').getContext('2d');
  if (conditionChartInstance) conditionChartInstance.destroy();
  conditionChartInstance = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {label: '很差', data: entries.map(([,c]) => c.very_poor_pct), backgroundColor: '#c53030'},
        {label: '差', data: entries.map(([,c]) => c.poor_pct), backgroundColor: '#ed8936'},
        {label: '一般', data: entries.map(([,c]) => c.fair_pct), backgroundColor: '#ecc94b'},
        {label: '良好', data: entries.map(([,c]) => c.good_pct), backgroundColor: '#68d391'},
        {label: '优秀', data: entries.map(([,c]) => c.excellent_pct), backgroundColor: '#2f855a'},
      ],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      scales: {
        x: {stacked: true, max: 100, ticks: {color: '#8b949e'}, grid: {color: '#30363d'}},
        y: {stacked: true, ticks: {color: '#e6edf3'}, grid: {display: false}},
      },
      plugins: {legend: {labels: {color: '#e6edf3'}}, tooltip: {mode: 'index'}},
    },
  });
}

const STAGE_LABELS = {
  Planted: '播种', Emerged: '出苗', Blooming: '开花', 'Setting Pods': '结荚', 'Setting Bolls': '结铃',
  Silking: '吐丝', Squaring: '现蕾', 'Bolls Opening': '吐絮', Headed: '抽穗', Coloring: '转色',
  'Turning Color': '转色', Harvested: '收获', Dough: '乳熟', Dented: '蜡熟', Mature: '成熟', Jointed: '拔节',
};

function renderProgressCards(progressByCrop) {
  const el = document.getElementById('progress-cards');
  el.innerHTML = Object.entries(progressByCrop).map(([crop, stages]) => {
    const stageHtml = Object.entries(stages).map(([stage, s]) => {
      const label = STAGE_LABELS[stage] || stage;
      const vsAvg = s.current_pct - s.avg_5yr_pct;
      const vsAvgTxt = Math.abs(vsAvg) < 1 ? '与近5年均值持平'
        : (vsAvg > 0 ? `快于近5年均值 ${vsAvg.toFixed(0)} 个百分点` : `慢于近5年均值 ${Math.abs(vsAvg).toFixed(0)} 个百分点`);
      return `<div class="progress-stage">
        <div class="lbl"><span>${esc(label)} (${esc(stage)})</span><b>${s.current_pct.toFixed(0)}%</b></div>
        <div class="progress-track">
          <div class="progress-fill" style="width:${s.current_pct}%"></div>
          <div class="progress-avgmark" style="left:${s.avg_5yr_pct}%" title="近5年均值 ${s.avg_5yr_pct.toFixed(0)}%"></div>
        </div>
        <div class="muted" style="font-size:11px;margin-top:2px">${vsAvgTxt}（近5年均值 ${s.avg_5yr_pct.toFixed(0)}% · 上周 ${s.week_ago_pct.toFixed(0)}%）</div>
      </div>`;
    }).join('');
    return `<div class="progress-card"><h4>${esc(crop)}</h4>${stageHtml}</div>`;
  }).join('');
}

function renderUsdaVizAnalyst(conditions, progressByCrop) {
  const condEntries = Object.entries(conditions);
  if (!condEntries.length) { document.getElementById('usda-viz-analyst').innerHTML = ''; return; }
  const sorted = condEntries.slice().sort((a, b) => a[1].good_excellent_pct - b[1].good_excellent_pct);
  const worst = sorted[0], best = sorted[sorted.length - 1];
  const leaders = [], laggards = [];
  Object.entries(progressByCrop).forEach(([crop, stages]) => {
    Object.entries(stages).forEach(([stage, s]) => {
      const diff = s.current_pct - s.avg_5yr_pct;
      if (diff >= 8) leaders.push(`${crop} ${STAGE_LABELS[stage]||stage}(+${diff.toFixed(0)}pt)`);
      if (diff <= -8) laggards.push(`${crop} ${STAGE_LABELS[stage]||stage}(${diff.toFixed(0)}pt)`);
    });
  });
  document.getElementById('usda-viz-analyst').innerHTML = `<h3>客观数据解读</h3><p>
    本周优良率（良好+优秀）最低的是 <b>${esc(worst[0].replace(' Condition',''))}</b>（${fmt0(worst[1].good_excellent_pct)}%），
    最高的是 <b>${esc(best[0].replace(' Condition',''))}</b>（${fmt0(best[1].good_excellent_pct)}%）。
    ${leaders.length ? `生长/收获进度明显快于近5年同期：<b>${leaders.join('、')}</b>；` : ''}
    ${laggards.length ? `明显慢于近5年同期：<b>${laggards.join('、')}</b>。` : (leaders.length ? '' : '整体生长节奏接近近5年均值，未见显著进度异常。')}
    下方图表为 USDA 原始客观数据（未做多空判断），交易建议见「子模块二」。</p>`;
}

async function loadAgri() {
  const res = await fetch('/api/agri');
  const d = await res.json();
  STATE.agri = d;

  renderConditionChart(d.usda_conditions || {});
  renderProgressCards(d.usda_progress_by_crop || {});
  renderUsdaVizAnalyst(d.usda_conditions || {}, d.usda_progress_by_crop || {});

  const commodities = Object.values(d.commodities);
  const bullish = commodities.filter(c => c.stance === 'bullish');
  const bearish = commodities.filter(c => c.stance === 'bearish');

  document.getElementById('agri-summary').innerHTML = `
    <h3>本周综述 (USDA ${esc(d.usda_released || '')}, 报告号 ${esc(d.usda_report || '')})</h3>
    <p>规则化框架当前偏多头：<b>${bullish.map(c=>esc(c.name)).join('、') || '无'}</b>；
    偏空头：<b>${bearish.map(c=>esc(c.name)).join('、') || '无'}</b>；
    其余品种维持中性。核心驱动来自 USDA 作物生长报告（优良率同比/环比变化）与主产区 USDM 干旱数据的交叉验证，
    软商品（可可/咖啡/糖）另叠加海外主产地未来7日降水信号。</p>
  `;

  const cardsEl = document.getElementById('agri-cards');
  cardsEl.innerHTML = commodities.map(c => {
    const p = c.price || {};
    const chgClass = (p.ret_5d_pct ?? 0) >= 0 ? 'pos' : 'neg';
    return `<div class="card">
      <div class="top">
        <div><h4>${esc(c.name)}</h4><div class="tk">${esc(c.ticker)} · ${esc(c.unit)}</div></div>
        <span class="badge ${c.stance}">${{bullish:'偏多',bearish:'偏空',neutral:'中性'}[c.stance]}</span>
      </div>
      <div class="price">${p.last ?? '—'} <span class="chg ${chgClass}">${p.ret_5d_pct>=0?'+':''}${fmt1(p.ret_5d_pct)}% (5日)</span></div>
      <div class="muted" style="font-size:11.5px;margin-top:2px">20日: ${p.ret_20d_pct>=0?'+':''}${fmt1(p.ret_20d_pct)}% · 距50日均线: ${p.vs_ma50_pct>=0?'+':''}${fmt1(p.vs_ma50_pct)}%</div>
      ${sparklineSvg(p.sparkline)}
      <ul class="reasons">${(c.reasons||[]).map(r => `<li>${esc(r)}</li>`).join('') || '<li class="muted">暂无显著驱动因素</li>'}</ul>
      ${c.momentum_note ? `<div class="momentum">⚡ ${esc(c.momentum_note)}</div>` : ''}
    </div>`;
  }).join('');

  document.getElementById('agri-note').innerText =
    '数据来源：期货价格 — Yahoo Finance（yfinance，免费）；作物生长/优良率 — USDA NASS《Crop Progress》周报（release.nass.usda.gov，免 key 公开文本报告，每周自动同步一次）；干旱 — U.S. Drought Monitor 官方 API；软商品海外产区天气 — Open-Meteo。以上多空判断为规则化分析框架输出，仅供研究参考，不构成投资建议。';
}

const WASDE_NAMES = {corn: '玉米 Corn', soybean: '大豆 Soybean', wheat: '小麦 Wheat', cotton: '棉花 Cotton', sugar: '糖 Sugar'};
const WASDE_ROW_ORDER = ['production', 'ending stocks', 'avg. farm price', 'avg. price', 'stocks to use ratio'];

async function loadWasde() {
  const res = await fetch('/api/wasde');
  const d = await res.json();
  STATE.wasde = d;
  if (d.error) {
    document.getElementById('wasde-analyst').innerHTML = `<span class="muted">${esc(d.error)}</span>`;
    document.getElementById('wasde-cards').innerHTML = '';
    return;
  }

  const commodities = Object.entries(d.commodities || {});
  let biggest = null;
  commodities.forEach(([key, table]) => {
    if (table.error) return;
    ['production', 'ending stocks'].forEach(metric => {
      const m = table.metrics && table.metrics[metric];
      if (m && m.proj_prev_month != null && m.proj_latest != null && m.proj_prev_month !== 0) {
        const pctChg = (m.proj_latest - m.proj_prev_month) / Math.abs(m.proj_prev_month) * 100;
        if (!biggest || Math.abs(pctChg) > Math.abs(biggest.pctChg)) {
          biggest = {key, metric, pctChg, prev: m.proj_prev_month, latest: m.proj_latest};
        }
      }
    });
  });

  document.getElementById('wasde-analyst').innerHTML = `<h3>WASDE 数据解读（${d.release_month}月号，源文件 ${esc(d.source_file)}）</h3>
    <ul>
      <li>WASDE 是 USDA 每月发布的供需平衡表（产量/库存/出口/期末库存预测），本月号相对上月号对2026/27年度的修正是市场最关注的信号，下方每张卡片括号内的"环比"就是这个修正幅度。</li>
      ${biggest ? `<li>本月修正幅度最大：<b>${esc(WASDE_NAMES[biggest.key] || biggest.key)}</b>的${biggest.metric === 'production' ? '产量' : '期末库存'}从 ${fmt1(biggest.prev)} 修正为 <b>${fmt1(biggest.latest)}</b>（${biggest.pctChg >= 0 ? '+' : ''}${fmt1(biggest.pctChg)}%）。</li>` : ''}
    </ul>`;

  document.getElementById('wasde-cards').innerHTML = commodities.map(([key, table]) => {
    if (table.error) return `<div class="wasde-card"><h4>${esc(WASDE_NAMES[key] || key)}</h4><span class="muted">解析失败</span></div>`;
    const rowsHtml = WASDE_ROW_ORDER.map(metric => {
      const m = table.metrics && table.metrics[metric];
      if (!m) return '';
      const delta = (m.proj_latest != null && m.proj_prev_month != null) ? m.proj_latest - m.proj_prev_month : null;
      const deltaClass = delta > 0 ? 'pos' : (delta < 0 ? 'neg' : '');
      return `<div class="wasde-row">
        <span class="wl">${esc(m.label)}</span>
        <span><span class="wv">${fmt1(m.proj_latest)}</span>${delta != null && Math.abs(delta) > 0.001 ? `<span class="wd ${deltaClass}">（环比${delta >= 0 ? '+' : ''}${fmt1(delta)}）</span>` : ''}</span>
      </div>`;
    }).join('');
    return `<div class="wasde-card"><h4>${esc(WASDE_NAMES[key] || key)}</h4>${rowsHtml}</div>`;
  }).join('');

  document.getElementById('wasde-note').innerText =
    'WASDE（世界农产品供需预测）数据来源：USDA/Cornell 官方 xls 快照（免费，无需 key），每月约8-12日发布一次，与上方每周更新的 Crop Progress 数据互补。产量/库存单位：玉米大豆小麦=百万蒲式耳，棉花=百万包(480磅/包)，糖=千短吨；价格单位见各行标签。点击"强制同步 USDA"会同时刷新周度数据和本模块。';
}

function weatherBlockHtml(marketLabel, tabKey, riskData, wmData) {
  if (!riskData) return '';
  const refs = riskData.refineries || [];
  const highHeat = refs.filter(r => r.risk.heat_risk === 'high');
  const highStorm = refs.filter(r => r.risk.storm_flood_risk === 'high');
  const highFreeze = refs.filter(r => r.risk.freeze_snow_risk === 'high');
  const totalHigh = new Set([...highHeat, ...highStorm, ...highFreeze].map(r => r.name)).size;

  const droughtSorted = (riskData.drought || []).slice().sort((a, b) =>
    (b.d2_severe_plus_pct ?? -Infinity) - (a.d2_severe_plus_pct ?? -Infinity) ||
    (a.anomaly_pct ?? Infinity) - (b.anomaly_pct ?? Infinity));
  const worstDrought = droughtSorted[0];
  const droughtLine = worstDrought
    ? ('d2_severe_plus_pct' in worstDrought
        ? `干旱最严重的州是 <b>${esc(worstDrought.state)}</b>（D2+ 占比 ${fmt1(worstDrought.d2_severe_plus_pct)}%，趋势${{worsening:'恶化',improving:'好转',steady:'持平'}[worstDrought.trend]||''}）`
        : `降水偏离最大的区域是 <b>${esc(worstDrought.region)}</b>（较近15年同期 ${worstDrought.anomaly_pct>0?'+':''}${fmt1(worstDrought.anomaly_pct)}%）`)
    : '';

  let wmLine = '', capLine = '', todaysAlert = '';
  if (wmData && !wmData.error) {
    const flagged = wmData.weather_flagged_outages || [];
    const catLabel = {primary: '一次加工', light: '轻质品', middle: '中质馏分', heavy: '重质品'};
    const catBreakdown = Object.entries(wmData.offline_by_category_kbd || {})
      .sort((a, b) => b[1] - a[1]).map(([k, v]) => `${catLabel[k]||k} ${fmt1(v)}`).join(' / ');
    capLine = `Wood Mackenzie 在册停车产能合计 <b>${fmt1(wmData.total_offline_kbd)}</b> 千桶/日（${catBreakdown}，单位千桶/日）`;

    wmLine = `最近14天新增停车 <b>${wmData.recent_outage_count}</b> 起，长期停车(≥30天) <b>${wmData.long_running_outage_count}</b> 起` +
      (flagged.length ? `，其中 <b class="summary-flag">${flagged.length} 起同时命中天气高风险</b>（${flagged.slice(0,3).map(o=>esc(o.matched_refinery)).join('、')}）` : '，暂无与天气风险叠加的情况');
    if (wmData.headlines && wmData.headlines.length) {
      wmLine += `。最新动态：${esc(wmData.headlines[0].text.slice(0, 140))}${wmData.headlines[0].text.length > 140 ? '…' : ''}`;
    }

    const today = wmData.todays_changes || [];
    if (today.length) {
      const rows = today.slice(0, 6).map(c => {
        const label = c.matched_refinery || c.facility;
        const flag = _isFlagged(c.weather_risk) ? ' ⚠️' : '';
        const action = c.status === 'OFF' ? '停车' : '开启/恢复';
        return `<li><b>${esc(label)}</b> ${esc(c.unit_name)}（${fmtKbd(c.capacity_bpd)}千桶/日）<span class="${c.status==='OFF'?'summary-flag':''}">${action}</span>${flag}</li>`;
      }).join('');
      todaysAlert = `<div class="note" style="border-left-color:var(--amber);margin-top:10px">
        <b>🕐 最近24小时装置变化提醒（${today.length}起）</b>
        <ul style="margin:6px 0 0;padding-left:20px">${rows}</ul>
      </div>`;
    }
  }

  return `<div class="summary-block">
    <h3>${marketLabel} <span class="goto" data-goto="${tabKey}">查看详情 →</span></h3>
    <ul>
      <li>监控 <b>${refs.length}</b> 座炼厂，其中 <b class="${totalHigh ? 'summary-flag' : ''}">${totalHigh}</b> 座至少命中一项未来7天天气高风险
        （高温 ${highHeat.length} / 雷暴洪水 ${highStorm.length} / 寒潮积雪 ${highFreeze.length}）。</li>
      ${droughtLine ? `<li>${droughtLine}。</li>` : ''}
      ${capLine ? `<li>${capLine}。</li>` : ''}
      ${wmLine ? `<li>${wmLine}。</li>` : ''}
    </ul>
    ${todaysAlert}
  </div>`;
}
function _isFlagged(risk) {
  return !!risk && ['heat_risk','storm_flood_risk','freeze_snow_risk'].some(k => risk[k] === 'high');
}

function renderSummary() {
  const el = document.getElementById('summary-content');
  const genEl = document.getElementById('summary-generated');
  genEl.textContent = '生成时间：' + new Date().toLocaleString('zh-CN');

  const blocks = [];
  blocks.push(weatherBlockHtml('🇺🇸 美国天气 & 炼厂风险', 'us', STATE.usRisk, STATE.usWm));
  blocks.push(weatherBlockHtml('🇪🇺 欧洲天气 & 炼厂风险', 'eu', STATE.euRisk, STATE.euWm));

  if (STATE.kaub) {
    const k = STATE.kaub;
    const belowLow = k.current_level_cm !== null && k.current_level_cm <= k.low_water_threshold_cm;
    blocks.push(`<div class="summary-block">
      <h3>🌊 Kaub 水位 <span class="goto" data-goto="kaub">查看详情 →</span></h3>
      <ul>
        <li>当前实测水位 <b class="${belowLow?'summary-flag':''}">${fmt0(k.current_level_cm)} cm</b>（枯水关口 ${k.low_water_threshold_cm} cm）${belowLow ? '，<b class="summary-flag">已跌破关口</b>' : ''}。</li>
        <li>${k.first_low_water_breach
          ? `模型预测将于 <b>${esc(k.first_low_water_breach)}</b> 前后跌破枯水阈值，展望期内共 <b>${k.forecast_days_below_low_water}</b> 天低于关口——需关注驳船减载/运费上涨风险。`
          : `展望期内模型未显示跌破枯水阈值（换算拟合度 ${k.rating_curve_confidence}）。`}</li>
      </ul>
    </div>`);
  }

  if (STATE.agri) {
    const a = STATE.agri;
    const commodities = Object.values(a.commodities || {});
    const bullish = commodities.filter(c => c.stance === 'bullish');
    const bearish = commodities.filter(c => c.stance === 'bearish');
    const biggestMove = commodities.slice().sort((x, y) =>
      Math.abs(y.price?.ret_5d_pct ?? 0) - Math.abs(x.price?.ret_5d_pct ?? 0))[0];
    const biggestCondChange = commodities
      .filter(c => c.usda_condition && c.usda_condition.good_excellent_pct_prev_week !== undefined)
      .sort((x, y) => Math.abs(y.usda_condition.good_excellent_pct - y.usda_condition.good_excellent_pct_prev_week)
                     - Math.abs(x.usda_condition.good_excellent_pct - x.usda_condition.good_excellent_pct_prev_week))[0];

    const statRows = commodities.map(c => {
      const p = c.price || {};
      const uc = c.usda_condition;
      const wowDelta = uc && uc.good_excellent_pct_prev_week !== undefined ? uc.good_excellent_pct - uc.good_excellent_pct_prev_week : null;
      const yoyDelta = uc && uc.good_excellent_pct_prev_year !== undefined ? uc.good_excellent_pct - uc.good_excellent_pct_prev_year : null;
      const chgClass = (p.ret_5d_pct ?? 0) >= 0 ? 'pos' : 'neg';
      return `<tr>
        <td>${esc(c.name)}<span class="badge ${c.stance}" style="margin-left:6px;font-size:10px">${{bullish:'偏多',bearish:'偏空',neutral:'中性'}[c.stance]}</span></td>
        <td class="num">${p.last ?? '—'}</td>
        <td class="num ${chgClass}">${p.ret_5d_pct!=null ? (p.ret_5d_pct>=0?'+':'')+fmt1(p.ret_5d_pct)+'%' : '—'}</td>
        <td class="num">${uc ? fmt0(uc.good_excellent_pct)+'%' : '—'}</td>
        <td class="num ${wowDelta<0?'neg':(wowDelta>0?'pos':'')}">${wowDelta!=null ? (wowDelta>=0?'+':'')+fmt1(wowDelta)+'pt' : '—'}</td>
        <td class="num ${yoyDelta<0?'neg':(yoyDelta>0?'pos':'')}">${yoyDelta!=null ? (yoyDelta>=0?'+':'')+fmt1(yoyDelta)+'pt' : '—'}</td>
      </tr>`;
    }).join('');

    blocks.push(`<div class="summary-block">
      <h3>🌱 农产品分析师 <span class="goto" data-goto="agri">查看详情 →</span></h3>
      <table style="margin-bottom:10px"><thead><tr>
        <th>品种</th><th class="num">价格</th><th class="num">5日涨跌</th>
        <th class="num">优良率</th><th class="num">环比(周)</th><th class="num">同比(年)</th>
      </tr></thead><tbody>${statRows}</tbody></table>
      <ul>
        <li>本期 USDA《Crop Progress》报告发布于 <b>${esc(a.usda_released || '—')}</b>（报告号 ${esc(a.usda_report || '—')}）。</li>
        <li>规则化框架：偏多头 <b>${bullish.map(c=>esc(c.name)).join('、') || '无'}</b>；偏空头 <b>${bearish.map(c=>esc(c.name)).join('、') || '无'}</b>。</li>
        ${biggestMove ? `<li>本周价格波动最大：<b>${esc(biggestMove.name)}</b>（5日 ${biggestMove.price.ret_5d_pct>=0?'+':''}${fmt1(biggestMove.price.ret_5d_pct)}%）。</li>` : ''}
        ${biggestCondChange ? `<li>作物优良率环比变化最大：<b>${esc(biggestCondChange.name)}</b>（${fmt0(biggestCondChange.usda_condition.good_excellent_pct)}% ${biggestCondChange.usda_condition.good_excellent_pct >= biggestCondChange.usda_condition.good_excellent_pct_prev_week ? '↑' : '↓'} 环比${biggestCondChange.usda_condition.good_excellent_pct - biggestCondChange.usda_condition.good_excellent_pct_prev_week >= 0 ? '+' : ''}${fmt1(biggestCondChange.usda_condition.good_excellent_pct - biggestCondChange.usda_condition.good_excellent_pct_prev_week)}pt）。</li>` : ''}
      </ul>
    </div>`);
  }

  el.innerHTML = blocks.join('') || '<div class="muted">数据加载中…</div>';
  el.querySelectorAll('[data-goto]').forEach(elm => {
    elm.addEventListener('click', () => goToTab(elm.dataset.goto));
  });
}

async function loadAll() {
  await Promise.allSettled([
    loadRefineryPanel('US'),
    loadRefineryPanel('EU'),
    loadWmModule('US'),
    loadWmModule('EU'),
    loadKaub(),
    loadAgri(),
    loadWasde(),
  ]);
  renderRiskHeatmap('us-heatmap', STATE.usRisk, STATE.usWm);
  renderRiskHeatmap('eu-heatmap', STATE.euRisk, STATE.euWm);
  renderSummary();
}

document.getElementById('refreshBtn').addEventListener('click', () => {
  document.getElementById('refreshBtn').innerText = '⏳ 刷新中…';
  loadAll().finally(() => { document.getElementById('refreshBtn').innerText = '🔄 刷新数据'; });
});
document.getElementById('syncUsdaBtn').addEventListener('click', async () => {
  document.getElementById('syncUsdaBtn').innerText = '⏳ 同步中…';
  await fetch('/api/agri/sync', {method: 'POST'});
  await Promise.allSettled([loadAgri(), loadWasde()]);
  renderSummary();
  document.getElementById('syncUsdaBtn').innerText = '📥 强制同步 USDA+WASDE';
});

['us', 'eu'].forEach(prefix => {
  const input = document.getElementById(`${prefix}-wm-upload`);
  const status = document.getElementById(`${prefix}-wm-upload-status`);
  input.addEventListener('change', async () => {
    const file = input.files[0];
    if (!file) return;
    status.textContent = `⏳ 上传解析中… (${file.name})`;
    const form = new FormData();
    form.append('file', file);
    try {
      const res = await fetch(`/api/wm_refinery/upload?market=${prefix.toUpperCase()}`, {method: 'POST', body: form});
      const d = await res.json();
      if (d.error) {
        status.textContent = `❌ ${d.error}`;
      } else {
        status.textContent = `✅ 已解析：${d.source_file}（截至 ${d.report_date}）`;
        await loadWmModule(prefix.toUpperCase());
        renderSummary();
      }
    } catch (e) {
      status.textContent = `❌ 上传失败：${e}`;
    }
    input.value = '';
  });
});

loadAll();
