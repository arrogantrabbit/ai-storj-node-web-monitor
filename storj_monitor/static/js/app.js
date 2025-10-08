import { map, heatmap } from './map.js';
import * as charts from './charts.js';

// --- Constants & State ---
const PERFORMANCE_INTERVAL_MS = 2000;
const MAX_PERF_POINTS = 150;
const SATELLITE_NAMES = { '121RTSDpyNZVcEU84Ticf2L1ntiuUimbWgfATz21tuvgk3vzoA6': 'ap1', '12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S': 'us1', '12L9ZFwhzVpuEKMUNUqkaTLGzwY9G24tbiigLiXpmZWKwmcNDDs': 'eu1', '1wFTAgs9DP5RSnCqKV1eLf6N9wtk4EAtmN5DpSxcs8EjT69tGE': 'saltlake' };
const TOGGLEABLE_CARDS = {
    'map-card': 'Live Traffic Heatmap',
    'stats-card': 'Overall Success Rates & Speed',
    'health-card': 'Node Health & History',
    'performance-card': 'Live Performance',
    'satellite-card': 'Traffic by Satellite',
    'analysis-card': 'Network & Error Analysis',
    'size-charts-card': 'Data Transfer Size Distribution',
    'active-compactions-card': 'Active Hashstore Compactions',
    'hashstore-chart-card': 'Hashstore Compaction Trends',
    'hashstore-card': 'Hashstore Compaction Details'
};
const VISIBILITY_STORAGE_KEY = 'storj-pro-monitor-card-visibility';


let performanceState = {
    view: 'rate', // rate, volume, pieces, concurrency
    range: '5m', // 5m, 30m, 1h, 6h, 24h
    agg: 'sum' // sum, avg
};
let cardVisibilityState = {};
let livePerformanceBins = {};
let maxHistoricalTimestampByView = {};
let isHistoricalDataLoaded = false;
let currentNodeView = ['Aggregate'];
let availableNodes = [];
let chartUpdateTimer = null;
let hashstoreMasterData = [];
let hashstoreFilters = { satellite: 'all', store: 'all' };
let hashstoreSort = { column: 'last_run_iso', direction: 'desc' };
let nodeConnectionStates = {};  // Track connection status for network nodes
let ws;

// --- Helper Functions ---
function formatBytes(bytes, decimals = 2) { if (!bytes || bytes === 0) return '0 Bytes'; const k = 1024; const dm = decimals < 0 ? 0 : decimals; const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB']; const i = Math.floor(Math.log(bytes) / Math.log(k)); return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i]; }
function getRateClass(rate) { if (rate > 99.5) return 'rate-good'; if (rate > 95) return 'rate-ok'; return 'rate-bad'; }
function formatCompactionDate(isoString) {
    const date = new Date(isoString);
    const today = new Date();
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const timeStr = date.toLocaleTimeString();
    if (date.toDateString() === today.toDateString()) return timeStr;
    if (date.toDateString() === yesterday.toDateString()) return `Yesterday ${timeStr}`;
    return `${date.toLocaleDateString()} ${timeStr}`;
}

// --- Card Visibility & Layout ---
function isCardVisible(cardId) {
    return cardVisibilityState[cardId] !== false;
}

function initializeDisplayMenu() {
    const container = document.getElementById('display-menu-container');
    const btn = document.getElementById('display-menu-btn');
    const dropdown = document.getElementById('display-menu-dropdown');
    if (!container || !btn || !dropdown) return;
    let dropdownHTML = '<h5>Visible Cards</h5>';
    for (const cardId in TOGGLEABLE_CARDS) {
        dropdownHTML += `<label><input type="checkbox" data-card-id="${cardId}"><span>${TOGGLEABLE_CARDS[cardId]}</span></label>`;
    }
    dropdown.innerHTML = dropdownHTML;
    btn.addEventListener('click', (e) => { e.stopPropagation(); dropdown.classList.toggle('visible'); btn.classList.toggle('active'); });
    dropdown.addEventListener('change', (e) => {
        if (e.target.type === 'checkbox') {
            const cardId = e.target.dataset.cardId;
            cardVisibilityState[cardId] = e.target.checked;
            localStorage.setItem(VISIBILITY_STORAGE_KEY, JSON.stringify(cardVisibilityState));
            applyCardLayout();
        }
    });
    document.addEventListener('click', (e) => {
        if (!container.contains(e.target)) {
            dropdown.classList.remove('visible');
            btn.classList.remove('active');
        }
    });
    loadCardVisibility();
}

function loadCardVisibility() {
    const savedState = localStorage.getItem(VISIBILITY_STORAGE_KEY);
    let state = {};
    if (savedState) {
        try { state = JSON.parse(savedState); } catch (e) { console.error("Could not parse visibility state.", e); }
    }
    const defaultState = Object.keys(TOGGLEABLE_CARDS).reduce((acc, key) => ({...acc, [key]: true }), {});
    cardVisibilityState = { ...defaultState, ...state };
    applyCardLayout();
}

function applyCardLayout() {
    for (const cardId in cardVisibilityState) {
        const cardElement = document.getElementById(cardId);
        const checkbox = document.querySelector(`#display-menu-dropdown input[data-card-id="${cardId}"]`);
        if (cardElement && checkbox) {
            const isVisible = cardVisibilityState[cardId];
            cardElement.classList.toggle('is-hidden', !isVisible);
            checkbox.checked = isVisible;
            if (cardId === 'map-card') {
                if (isVisible) heatmap.resume();
                else heatmap.pause();
            }
        }
    }
    reflowGrid();
}

function reflowGrid() {
    let currentRow = 2;
    const isVisible = (id) => cardVisibilityState[id];
    const setStyle = (id, col, row) => {
        const el = document.getElementById(id);
        if (el) Object.assign(el.style, { gridColumn: col, gridRow: row });
    };
    const mapVisible = isVisible('map-card'), statsVisible = isVisible('stats-card'), healthVisible = isVisible('health-card');
    if (mapVisible) {
        setStyle('map-card', '1 / 8', '2 / 4');
        if (statsVisible) setStyle('stats-card', '8 / 13', '2 / 3');
        if (healthVisible) setStyle('health-card', '8 / 13', '3 / 4');
        currentRow = 4;
    } else {
        if (statsVisible && healthVisible) { setStyle('stats-card', '1 / 7', '2 / 3'); setStyle('health-card', '7 / 13', '2 / 3'); currentRow = 3; }
        else if (statsVisible) { setStyle('stats-card', '1 / -1', '2 / 3'); currentRow = 3; }
        else if (healthVisible) { setStyle('health-card', '1 / -1', '2 / 3'); currentRow = 3; }
    }
    const perfVisible = isVisible('performance-card'), satVisible = isVisible('satellite-card');
    if (perfVisible && satVisible) { setStyle('performance-card', '1 / 7', `${currentRow} / ${currentRow + 1}`); setStyle('satellite-card', '7 / 13', `${currentRow} / ${currentRow + 1}`); currentRow++; }
    else if (perfVisible) { setStyle('performance-card', '1 / -1', `${currentRow} / ${currentRow + 1}`); currentRow++; }
    else if (satVisible) { setStyle('satellite-card', '1 / -1', `${currentRow} / ${currentRow + 1}`); currentRow++; }
    ['analysis-card', 'size-charts-card', 'active-compactions-card', 'hashstore-chart-card', 'hashstore-card'].forEach(cardId => {
        if (isVisible(cardId)) { setStyle(cardId, '1 / -1', `${currentRow} / ${currentRow + 1}`); currentRow++; }
    });
    if (mapVisible) {
        setTimeout(() => map.invalidateSize(), 150);
    }
}


// --- UI Update Functions ---
function updateAllVisuals(data) {
    if (isCardVisible('stats-card')) updateOverallStats(data.overall);
    if (isCardVisible('satellite-card')) charts.updateSatelliteChart(data.satellites);
    if (isCardVisible('size-charts-card')) charts.updateSizeBarChart(data.transfer_sizes);
    updateTitles(data.first_event_iso, data.last_event_iso);
    if (isCardVisible('health-card')) updateHistoricalTable(data.historical_stats);
    if (isCardVisible('analysis-card')) updateAnalysisTables(data);
}
function updateOverallStats(stats) { if (!stats || Object.keys(stats).length === 0) return; const totalDownloads = stats.dl_success + stats.dl_fail; const totalUploads = stats.ul_success + stats.ul_fail; const totalAudits = stats.audit_success + stats.audit_fail; const dlRate = totalDownloads > 0 ? (stats.dl_success / totalDownloads * 100).toFixed(2) : '100.00'; const ulRate = totalUploads > 0 ? (stats.ul_success / totalUploads * 100).toFixed(2) : '100.00'; const auditRate = totalAudits > 0 ? (stats.audit_success / totalAudits * 100).toFixed(2) : '100.00'; document.getElementById('dl-rate').textContent = `${dlRate}%`; document.getElementById('dl-rate').className = `stat-value ${getRateClass(parseFloat(dlRate))}`; document.getElementById('dl-success').textContent = stats.dl_success; document.getElementById('dl-total').textContent = totalDownloads; document.getElementById('ul-rate').textContent = `${ulRate}%`; document.getElementById('ul-rate').className = `stat-value ${getRateClass(parseFloat(ulRate))}`; document.getElementById('ul-success').textContent = stats.ul_success; document.getElementById('ul-total').textContent = totalUploads; document.getElementById('dl-speed').textContent = `${(stats.avg_egress_mbps||0).toFixed(2)} Mbps`; document.getElementById('ul-speed').textContent = `${(stats.avg_ingress_mbps||0).toFixed(2)} Mbps`; document.getElementById('audit-rate').textContent = `${auditRate}%`; document.getElementById('audit-rate').className = `stat-value ${getRateClass(parseFloat(auditRate))}`; document.getElementById('audit-success').textContent = stats.audit_success; document.getElementById('audit-total').textContent = totalAudits; }
function updateTitles(firstIso, lastIso) {
    const formatTime = date => date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    let timeWindowStr = "(Last 60 Mins)";
    if (firstIso && lastIso) {
        timeWindowStr = `(${formatTime(new Date(firstIso))} - ${formatTime(new Date(lastIso))})`;
    }
    const getFriendlyViewName = () => {
        if (currentNodeView.length === 1 && currentNodeView[0] === 'Aggregate') return 'Aggregate';
        if (currentNodeView.length === 1) return currentNodeView[0];
        if (currentNodeView.length > 3) return `${currentNodeView.length} nodes`;
        return currentNodeView.join(', ');
    };
    const viewName = getFriendlyViewName();
    const viewNameForTitle = viewName === 'Aggregate' ? '' : ` (${viewName})`;

    if (isCardVisible('stats-card')) {
        document.getElementById('stats-title').textContent = `Overall Success Rates & Speed${viewNameForTitle} ${timeWindowStr}`;
        const isAggregateView = currentNodeView.length > 1 || (currentNodeView.length === 1 && currentNodeView[0] === 'Aggregate');
        document.getElementById('dl-speed-label').textContent = isAggregateView ? 'Total Download Speed' : 'Download Speed';
        document.getElementById('ul-speed-label').textContent = isAggregateView ? 'Total Upload Speed' : 'Upload Speed';
    }
    if (isCardVisible('satellite-card')) document.getElementById('satellite-title').textContent = `Traffic by Satellite${viewNameForTitle} ${timeWindowStr}`;
    if (isCardVisible('size-charts-card')) document.getElementById('size-chart-title').textContent = `Data Transfer Size Distribution${viewNameForTitle} ${timeWindowStr}`;
    if (isCardVisible('hashstore-chart-card')) document.getElementById('hashstore-chart-title').textContent = `Hashstore Compaction Trends${viewNameForTitle}`;
    if (isCardVisible('hashstore-card')) document.getElementById('hashstore-title').textContent = `Hashstore Compaction Details${viewNameForTitle}`;
}
function updateHistoricalTable(history) { const tbody = document.getElementById('history-body'); tbody.innerHTML = ''; if (!history || history.length === 0) { tbody.innerHTML = '<tr><td colspan="6" style="text-align: center;">No historical data yet.</td></tr>'; return; } for (const hour of history) { const totalDl = hour.dl_success + hour.dl_fail, dlRate = totalDl > 0 ? (hour.dl_success / totalDl * 100) : 100; const totalUl = hour.ul_success + hour.ul_fail, ulRate = totalUl > 0 ? (hour.ul_success / totalUl * 100) : 100; const totalAudit = hour.audit_success + hour.audit_fail, auditRate = totalAudit > 0 ? (hour.audit_success / totalAudit * 100) : 100; const dlSpeed = hour.dl_mbps !== undefined ? hour.dl_mbps.toFixed(2) : '...'; const ulSpeed = hour.ul_mbps !== undefined ? hour.ul_mbps.toFixed(2) : '...'; const row = tbody.insertRow(); row.innerHTML = `<td>${new Date(hour.hour_timestamp).toLocaleTimeString([],{hour:'numeric'})}</td><td class="numeric ${getRateClass(dlRate)}">${dlRate.toFixed(2)}%</td><td class="numeric ${getRateClass(ulRate)}">${ulRate.toFixed(2)}%</td><td class="numeric ${getRateClass(auditRate)}">${auditRate.toFixed(2)}%</td><td class="numeric">${dlSpeed} Mbps</td><td class="numeric">${ulSpeed} Mbps</td>`; } }
function updateAnalysisTables(data) { const errorBody = document.getElementById('error-body'); errorBody.innerHTML = ''; if (data.error_categories && data.error_categories.length > 0) { data.error_categories.forEach(e => { errorBody.innerHTML += `<tr><td class="reason-cell">${e.reason}</td><td class="numeric">${e.count}</td></tr>`; }); } else { errorBody.innerHTML = '<tr><td colspan="2" style="text-align: center;">No errors in this time window.</td></tr>'; } const piecesBody = document.getElementById('pieces-body'); piecesBody.innerHTML = ''; if (data.top_pieces && data.top_pieces.length > 0) { data.top_pieces.forEach(p => { piecesBody.innerHTML += `<tr><td class="piece-id">${p.id.substring(0,25)}...</td><td class="numeric">${p.count}</td><td class="numeric">${formatBytes(p.size)}</td></tr>`; }); } else { piecesBody.innerHTML = '<tr><td colspan="3" style="text-align: center;">No data in this time window.</td></tr>'; } const countriesDlBody = document.getElementById('countries-dl-body'); countriesDlBody.innerHTML = ''; if (data.top_countries_dl && data.top_countries_dl.length > 0) { data.top_countries_dl.forEach(c => countriesDlBody.innerHTML += `<tr><td>${c.country}</td><td class="numeric">${formatBytes(c.size)}</td></tr>`); } else { countriesDlBody.innerHTML = '<tr><td colspan="2" style="text-align: center;">No data in this time window.</td></tr>'; } const countriesUlBody = document.getElementById('countries-ul-body'); countriesUlBody.innerHTML = ''; if (data.top_countries_ul && data.top_countries_ul.length > 0) { data.top_countries_ul.forEach(c => countriesUlBody.innerHTML += `<tr><td>${c.country}</td><td class="numeric">${formatBytes(c.size)}</td></tr>`); } else { countriesUlBody.innerHTML = '<tr><td colspan="2" style="text-align: center;">No data in this time window.</td></tr>'; } }

// --- Active Compactions Logic ---
let activeCompactionTimer = null;
let currentActiveCompactions = [];
function renderActiveCompactions() { const card = document.getElementById('active-compactions-card'); if (!card) return; const tbody = card.querySelector('tbody'); const nodeHeader = card.querySelector('th:first-child'); if (!tbody || !nodeHeader) return; const showNodeColumn = currentNodeView[0] === 'Aggregate' || currentNodeView.length > 1; nodeHeader.style.display = showNodeColumn ? '' : 'none'; if (currentActiveCompactions.length === 0) { const colspan = showNodeColumn ? 5 : 4; tbody.innerHTML = `<tr><td colspan="${colspan}" style="text-align: center;">No compactions currently in progress.</td></tr>`; return; } const now = new Date(); const sortedCompactions = [...currentActiveCompactions].sort((a, b) => new Date(a.start_iso) - new Date(b.start_iso)); let newHtml = ''; sortedCompactions.forEach(c => { const startTime = new Date(c.start_iso); const durationSeconds = (now - startTime) / 1000; const hours = Math.floor(durationSeconds / 3600); const minutes = Math.floor((durationSeconds % 3600) / 60); const seconds = Math.floor(durationSeconds % 60); const durationStr = `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`; let row = '<tr>'; if (showNodeColumn) { row += `<td>${c.node_name}</td>`; } row += `<td>${SATELLITE_NAMES[c.satellite] || c.satellite.substring(0, 12)}</td><td>${c.store}</td><td>${startTime.toLocaleTimeString()}</td><td class="numeric">${durationStr}</td></tr>`; newHtml += row; }); tbody.innerHTML = newHtml; }
function updateActiveCompactions(compactions) { currentActiveCompactions = compactions || []; if (activeCompactionTimer) { clearInterval(activeCompactionTimer); activeCompactionTimer = null; } renderActiveCompactions(); if (currentActiveCompactions.length > 0) { activeCompactionTimer = setInterval(renderActiveCompactions, 1000); } }

// --- Hashstore Panel Logic ---
function updateHashstorePanel(stats) { hashstoreMasterData = stats || []; if (document.getElementById('hashstore-filters').innerHTML.trim() === "<!-- JS populates this -->") { populateHashstoreFilters(); } renderHashstoreView(); }
function populateHashstoreFilters() { const filtersContainer = document.getElementById('hashstore-filters'); if (!filtersContainer) return; const satellites = [...new Set(hashstoreMasterData.map(item => SATELLITE_NAMES[item.satellite] || item.satellite))].sort(); const stores = [...new Set(hashstoreMasterData.map(item => item.store))].sort(); let filtersHTML = `<label>Satellite: <select data-filter="satellite"><option value="all">All</option>${satellites.map(s => `<option value="${s}">${s}</option>`).join('')}</select></label><label>Store: <select data-filter="store"><option value="all">All</option>${stores.map(s => `<option value="${s}">${s}</option>`).join('')}</select></label>`; filtersContainer.innerHTML = filtersHTML; filtersContainer.querySelectorAll('select').forEach(sel => { sel.addEventListener('change', (e) => { hashstoreFilters[e.target.dataset.filter] = e.target.value; requestHashstoreData(); }); }); }
function renderHashstoreView() { if (isCardVisible('hashstore-chart-card')) charts.updateHashstoreChart(hashstoreMasterData); let tableData = hashstoreMasterData.map(item => { const newItem = {...item}; newItem.reclaim_efficiency = newItem.data_rewritten_bytes > 0 ? newItem.data_reclaimed_bytes / newItem.data_rewritten_bytes : Infinity; newItem.throughput = newItem.duration > 0.01 ? (newItem.data_reclaimed_bytes + newItem.data_rewritten_bytes) / newItem.duration : 0; return newItem; }); const { column, direction } = hashstoreSort; tableData.sort((a, b) => { let valA = a[column], valB = b[column]; if (typeof valA === 'number' && typeof valB === 'number') { if (column === 'reclaim_efficiency') { if (valA === Infinity) valA = Number.MAX_SAFE_INTEGER; if (valB === Infinity) valB = Number.MAX_SAFE_INTEGER; } return direction === 'asc' ? valA - valB : valB - valA; } const strA = String(valA || ''), strB = String(valB || ''); return direction === 'asc' ? strA.localeCompare(strB) : strB.localeCompare(strA); }); if (isCardVisible('hashstore-card')) renderHashstoreTable(tableData); }
function renderHashstoreTable(data) { const tbody = document.getElementById('hashstore-body'), tfoot = document.getElementById('hashstore-foot'), headers = document.querySelectorAll('#hashstore-card th[data-column]'); tbody.innerHTML = ''; tfoot.innerHTML = ''; const showNodeColumn = currentNodeView[0] === 'Aggregate' || currentNodeView.length > 1; document.querySelector('#hashstore-card th[data-column="node_name"]').style.display = showNodeColumn ? '' : 'none'; if (!data || data.length === 0) { const colspan = showNodeColumn ? 11 : 10; tbody.innerHTML = `<tr><td colspan="${colspan}" style="text-align: center;">No matching compaction events found.</td></tr>`; return; } let totalReclaimed = 0, totalRewritten = 0, totalDuration = 0, totalBytesProcessed = 0, totalLoad = 0, totalTrash = 0; for (const item of data) { totalReclaimed += item.data_reclaimed_bytes; totalRewritten += item.data_rewritten_bytes; const bytesProcessed = item.data_reclaimed_bytes + item.data_rewritten_bytes; totalBytesProcessed += bytesProcessed; if (bytesProcessed > 0) { totalDuration += item.duration * bytesProcessed; totalLoad += item.table_load * bytesProcessed; totalTrash += item.trash_percent * bytesProcessed; } const row = tbody.insertRow(); let cellIndex = 0; row.insertCell(cellIndex++).textContent = item.node_name; row.insertCell(cellIndex++).textContent = SATELLITE_NAMES[item.satellite] || item.satellite.substring(0,12); row.insertCell(cellIndex++).textContent = item.store; row.insertCell(cellIndex++).textContent = formatCompactionDate(item.last_run_iso); const durationCell = row.insertCell(cellIndex++); durationCell.className = 'numeric'; durationCell.textContent = `${item.duration.toFixed(1)}s`; if (item.duration > 180) durationCell.classList.add('rate-bad'); else if (item.duration > 60) durationCell.classList.add('rate-ok'); row.insertCell(cellIndex++).className = 'numeric'; row.cells[cellIndex-1].textContent = formatBytes(item.data_reclaimed_bytes); row.insertCell(cellIndex++).className = 'numeric'; row.cells[cellIndex-1].textContent = formatBytes(item.data_rewritten_bytes); const efficiencyCell = row.insertCell(cellIndex++); efficiencyCell.className = 'numeric'; if (item.reclaim_efficiency === Infinity) efficiencyCell.textContent = 'Pure Reclaim'; else if (item.data_rewritten_bytes > 0) efficiencyCell.textContent = `${item.reclaim_efficiency.toFixed(1)}x`; else efficiencyCell.textContent = 'N/A'; if (item.reclaim_efficiency > 10) efficiencyCell.classList.add('rate-good'); row.insertCell(cellIndex++).className = 'numeric'; row.cells[cellIndex-1].textContent = formatBytes(item.throughput, 2) + '/s'; if (item.throughput > 50 * 1024 * 1024) row.cells[cellIndex-1].classList.add('rate-good'); const loadCell = row.insertCell(cellIndex++); loadCell.className = 'numeric'; loadCell.textContent = `${item.table_load.toFixed(2)}%`; if (item.table_load > 60) loadCell.classList.add('rate-bad'); else if (item.table_load > 40) loadCell.classList.add('rate-ok'); row.insertCell(cellIndex++).className = 'numeric'; row.cells[cellIndex-1].textContent = `${item.trash_percent.toFixed(2)}%`; row.cells[0].style.display = showNodeColumn ? '' : 'none'; } const avgDuration = totalBytesProcessed > 0 ? totalDuration / totalBytesProcessed : 0; const avgLoad = totalBytesProcessed > 0 ? totalLoad / totalBytesProcessed : 0; const avgTrash = totalBytesProcessed > 0 ? totalTrash / totalBytesProcessed : 0; const avgEfficiency = totalRewritten > 0 ? totalReclaimed / totalRewritten : Infinity; const avgThroughput = data.length > 0 ? data.reduce((sum, item) => sum + item.throughput, 0) / data.length : 0; const footRow = tfoot.insertRow(); const firstCell = footRow.insertCell(); firstCell.colSpan = showNodeColumn ? 4 : 3; firstCell.textContent = `Totals / Weighted Averages (${data.length} entr${data.length > 1 ? 'ies' : 'y'})`; firstCell.style.fontWeight = 'bold'; const addNumericFootCell = (text) => { const cell = footRow.insertCell(); cell.className = 'numeric'; cell.textContent = text; }; addNumericFootCell(`${avgDuration.toFixed(1)}s`); addNumericFootCell(formatBytes(totalReclaimed)); addNumericFootCell(formatBytes(totalRewritten)); const efficiencyVal = (avgEfficiency === Infinity) ? 'Pure Reclaim' : (totalRewritten > 0 ? `${avgEfficiency.toFixed(1)}x` : 'N/A'); addNumericFootCell(efficiencyVal); addNumericFootCell(formatBytes(avgThroughput) + '/s'); addNumericFootCell(`${avgLoad.toFixed(2)}%`); addNumericFootCell(`${avgTrash.toFixed(2)}%`); headers.forEach(th => { th.classList.remove('sort-asc', 'sort-desc'); if (th.dataset.column === hashstoreSort.column) { th.classList.add(hashstoreSort.direction === 'asc' ? 'sort-asc' : 'sort-desc'); } }); }
function requestHashstoreData() { if (ws && ws.readyState === WebSocket.OPEN) { let filters_to_send = { ...hashstoreFilters }; if (currentNodeView.length === 1 && currentNodeView[0] === 'Aggregate') { filters_to_send.node_name = 'all'; } else { filters_to_send.node_name = currentNodeView; } ws.send(JSON.stringify({ type: 'get_hashstore_stats', filters: filters_to_send })); } }

// --- Performance Data Logic ---
function initializePerformanceDataStateForView(view) { const viewKey = Array.isArray(view) ? view.join(',') : view; livePerformanceBins[viewKey] = {}; maxHistoricalTimestampByView[viewKey] = 0; isHistoricalDataLoaded = false; }
function initializePerformanceData(nodes) { livePerformanceBins = {}; maxHistoricalTimestampByView = {}; initializePerformanceDataStateForView(['Aggregate']); nodes.forEach(node => { initializePerformanceDataStateForView([node]); }); }
function isEventVisible(event) { if (!event.node_name) return true; if (currentNodeView.length === 1 && currentNodeView[0] === 'Aggregate') return true; return currentNodeView.includes(event.node_name); }
function processLogEntry(event) { if (!isEventVisible(event)) return; if (isCardVisible('map-card') && event.location && event.location.lat) { const displayTime = event.arrival_time ? new Date(event.arrival_time * 1000).toISOString() : event.timestamp; heatmap.addDataPoint(event.location.lat, event.location.lon, event.size || 1000, event.action, event.action, displayTime); } }
function processBatchedLogEntries(events) { if (!isCardVisible('map-card') || !events || events.length === 0) return; const visibleEvents = events.filter(isEventVisible); if (visibleEvents.length === 0) return; const DISPLAY_WINDOW_MS = 100; const eventCount = visibleEvents.length; visibleEvents.forEach((event, index) => { if (event.location && event.location.lat) { let displayDelay; if (event.arrival_offset_ms && event.arrival_offset_ms < DISPLAY_WINDOW_MS) { displayDelay = event.arrival_offset_ms; } else { const normalizedIndex = index / Math.max(eventCount - 1, 1); displayDelay = normalizedIndex * DISPLAY_WINDOW_MS; } displayDelay += (Math.random() - 0.5) * 30; displayDelay = Math.max(0, Math.min(displayDelay, DISPLAY_WINDOW_MS)); setTimeout(() => { const displayTime = event.arrival_time ? new Date(event.arrival_time * 1000).toISOString() : event.timestamp; heatmap.addDataPoint(event.location.lat, event.location.lon, event.size || 1000, event.action, event.action, displayTime); }, displayDelay); } }); }
function processLivePerformanceUpdate(data) { const { node_name, bins } = data; const updateNodeBins = (targetNodeName) => { const maxTs = maxHistoricalTimestampByView[targetNodeName] || 0; if (!livePerformanceBins[targetNodeName]) { livePerformanceBins[targetNodeName] = {}; } const targetBins = livePerformanceBins[targetNodeName]; for (const ts in bins) { if (parseInt(ts, 10) <= maxTs) continue; if (!targetBins[ts]) { targetBins[ts] = { ingress_bytes: 0, egress_bytes: 0, ingress_pieces: 0, egress_pieces: 0, total_ops: 0 }; } const binData = bins[ts]; targetBins[ts].ingress_bytes += binData.ingress_bytes || 0; targetBins[ts].egress_bytes += binData.egress_bytes || 0; targetBins[ts].ingress_pieces += binData.ingress_pieces || 0; targetBins[ts].egress_pieces += binData.egress_pieces || 0; targetBins[ts].total_ops += binData.total_ops || 0; } const cutoff = Date.now() - (5 * 60 * 1000 + 5000); for (const ts in targetBins) { if (parseInt(ts, 10) < cutoff) delete targetBins[ts]; } }; updateNodeBins(node_name); updateNodeBins('Aggregate'); if (isCardVisible('performance-card') && performanceState.range === '5m') { const viewKey = currentNodeView.join(','); if (currentNodeView.length > 1 && currentNodeView.includes(node_name)) updateNodeBins(viewKey); clearTimeout(chartUpdateTimer); chartUpdateTimer = setTimeout(() => charts.updatePerformanceChart(performanceState, livePerformanceBins, currentNodeView, availableNodes), 250); } }

// --- WebSocket Connection & Data Handling ---
const connectionManager = { overlay: document.getElementById('connection-overlay'), reconnectDelay: 1000, maxReconnectDelay: 30000, connect: function() { const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'; ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws`); ws.onopen = () => { this.overlay.style.display = 'none'; this.reconnectDelay = 1000; console.log("[WebSocket] Connection opened."); requestHashstoreData(); }; ws.onmessage = (event) => { const data = JSON.parse(event.data); if (data.type !== 'log_entry' && data.type !== 'performance_batch_update') { console.log(`[WebSocket] Received message type: ${data.type}`); } handleWebSocketMessage(data); }; ws.onclose = () => { this.overlay.style.display = 'flex'; setTimeout(() => this.connect(), this.reconnectDelay); this.reconnectDelay = Math.min(this.maxReconnectDelay, this.reconnectDelay * 2); console.warn(`[WebSocket] Connection closed. Reconnecting in ${this.reconnectDelay}ms.`); }; ws.onerror = err => { console.error("[WebSocket] Error:", err); ws.close(); }; } };
function handleWebSocketMessage(data) {
    switch(data.type) {
        case 'init':
            availableNodes = data.nodes;
            renderNodeSelector();
            initializePerformanceData(data.nodes);
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'get_historical_performance', view: currentNodeView, points: MAX_PERF_POINTS, interval_sec: PERFORMANCE_INTERVAL_MS / 1000 }));
            }
            break;
        case 'connection_status':
            handleConnectionStatusUpdate(data);
            break;
        case 'log_entry': processLogEntry(data); break;
        case 'log_entry_batch': processBatchedLogEntries(data.events); break;
        case 'performance_batch_update': if (isHistoricalDataLoaded) processLivePerformanceUpdate(data); break;
        case 'historical_performance_data': {
            const viewKey = Array.isArray(data.view) ? data.view.join(',') : data.view;
            const historicalBins = {};
            const binSize = PERFORMANCE_INTERVAL_MS;
            let maxHistoricalTimestamp = 0;
            data.performance_data.forEach(p => {
                const ts = new Date(p.timestamp).getTime();
                const binnedTimestamp = Math.floor(ts / binSize) * binSize;
                historicalBins[binnedTimestamp] = { ingress_bytes: p.ingress_bytes, egress_bytes: p.egress_bytes, ingress_pieces: p.ingress_pieces, egress_pieces: p.egress_pieces, total_ops: p.total_ops };
                if(binnedTimestamp > maxHistoricalTimestamp) maxHistoricalTimestamp = binnedTimestamp;
            });
            maxHistoricalTimestampByView[viewKey] = maxHistoricalTimestamp;
            const existingBins = livePerformanceBins[viewKey] || {};
            for (const ts in existingBins) { if (parseInt(ts, 10) > maxHistoricalTimestamp) historicalBins[ts] = existingBins[ts]; }
            livePerformanceBins[viewKey] = historicalBins;
            isHistoricalDataLoaded = true;
            if (isCardVisible('performance-card') && viewKey === currentNodeView.join(',') && performanceState.range === '5m') {
                charts.updatePerformanceChart(performanceState, livePerformanceBins, currentNodeView, availableNodes);
            }
            break;
        }
        case 'aggregated_performance_data':
            if (isCardVisible('performance-card')) charts.updatePerformanceChart(performanceState, data.performance_data);
            break;
        case 'stats_update': updateAllVisuals(data); break;
        case 'hashstore_updated': console.log("[WebSocket] Hashstore data updated. Requesting new data."); requestHashstoreData(); break;
        case 'hashstore_stats_data': updateHashstorePanel(data.data); break;
        case 'active_compactions_update': updateActiveCompactions(data.compactions); break;
    }
}

// --- Connection Status Management ---
function handleConnectionStatusUpdate(data) {
    const { node_name, state, host, port, error } = data;
    
    // Update state tracking
    nodeConnectionStates[node_name] = {
        state: state,
        host: host,
        port: port,
        error: error,
        timestamp: Date.now()
    };
    
    // Update UI
    renderNodeSelector();
    
    // Show notification for significant state changes
    if (state === 'connected') {
        showConnectionNotification(node_name, `Connected to ${host}:${port}`, 'success');
    } else if (state === 'disconnected' && error) {
        showConnectionNotification(node_name, `Disconnected: ${error}`, 'error');
    } else if (state === 'reconnecting') {
        showConnectionNotification(node_name, `Reconnecting to ${host}:${port}...`, 'warning');
    }
}

function showConnectionNotification(nodeName, message, type) {
    // Create or update notification element
    let notificationContainer = document.getElementById('connection-notifications');
    if (!notificationContainer) {
        notificationContainer = document.createElement('div');
        notificationContainer.id = 'connection-notifications';
        notificationContainer.style.cssText = 'position: fixed; top: 70px; right: 20px; z-index: 9999; max-width: 350px;';
        document.body.appendChild(notificationContainer);
    }
    
    const notification = document.createElement('div');
    notification.className = `connection-notification connection-notification-${type}`;
    notification.style.cssText = `
        background: ${type === 'success' ? '#4caf50' : type === 'error' ? '#f44336' : '#ff9800'};
        color: white;
        padding: 12px 16px;
        margin-bottom: 10px;
        border-radius: 4px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.2);
        animation: slideIn 0.3s ease-out;
    `;
    notification.innerHTML = `<strong>${nodeName}:</strong> ${message}`;
    
    notificationContainer.appendChild(notification);
    
    // Auto-remove after 5 seconds
    setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease-in';
        setTimeout(() => notification.remove(), 300);
    }, 5000);
}

function getConnectionStatusIcon(state) {
    switch(state) {
        case 'connected': return '🟢';
        case 'connecting': return '🟡';
        case 'reconnecting': return '🟠';
        case 'disconnected': return '🔴';
        case 'stopped': return '⚫';
        default: return '⚪';
    }
}

function getConnectionStatusTooltip(nodeName) {
    const status = nodeConnectionStates[nodeName];
    if (!status) return null;
    
    let tooltip = `Status: ${status.state}\nHost: ${status.host}:${status.port}`;
    if (status.error) {
        tooltip += `\nError: ${status.error}`;
    }
    const elapsed = Math.floor((Date.now() - status.timestamp) / 1000);
    tooltip += `\nLast update: ${elapsed}s ago`;
    
    return tooltip;
}

// --- UI Event Listeners ---
function setupEventListeners() {
    document.getElementById('toggle-satellite-view').addEventListener('click', function(e) { e.preventDefault(); charts.toggleSatelliteView(); if(isCardVisible('satellite-card')) charts.updateSatelliteChart(); });
    document.getElementById('size-view-toggles').addEventListener('click', function(e) { e.preventDefault(); const target = e.target; if (target.tagName === 'A' && !target.classList.contains('active')) { charts.setSizeChartViewMode(target.getAttribute('data-view')); document.querySelectorAll('#size-view-toggles .toggle-link').forEach(el => el.classList.remove('active')); target.classList.add('active'); if(isCardVisible('size-charts-card')) charts.updateSizeBarChart(); } });
    document.getElementById('performance-toggles').addEventListener('click', function(e) { e.preventDefault(); if (e.target.tagName === 'A') { performanceState.view = e.target.getAttribute('data-view'); document.querySelectorAll('#performance-toggles .toggle-link').forEach(el => el.classList.remove('active')); e.target.classList.add('active'); charts.updatePerformanceChart(performanceState, livePerformanceBins, currentNodeView, availableNodes); } });
    document.getElementById('time-range-toggles').addEventListener('click', function(e) { e.preventDefault(); const newRange = e.target.getAttribute('data-range'); if (newRange === performanceState.range) return; performanceState.range = newRange; document.querySelectorAll('#time-range-toggles .toggle-link').forEach(el => el.classList.remove('active')); e.target.classList.add('active'); charts.createPerformanceChart(performanceState); if (newRange === '5m') { charts.updatePerformanceChart(performanceState, livePerformanceBins, currentNodeView, availableNodes); } else { const hours = { '30m': 0.5, '1h': 1, '6h': 6, '24h': 24 }[newRange]; if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: 'get_aggregated_performance', view: currentNodeView, hours: hours })); } } });
    document.getElementById('aggregation-toggles').addEventListener('click', function(e) { e.preventDefault(); if (e.target.tagName === 'A') { performanceState.agg = e.target.getAttribute('data-agg'); document.querySelectorAll('#aggregation-toggles .toggle-link').forEach(el => el.classList.remove('active')); e.target.classList.add('active'); charts.updatePerformanceChart(performanceState, livePerformanceBins, currentNodeView, availableNodes); } });
    document.getElementById('node-selector').addEventListener('click', function(e) {
        e.preventDefault();
        if (!e.target.hasAttribute('data-view')) return;
        const clickedView = e.target.getAttribute('data-view');
        initializePerformanceDataStateForView(clickedView === 'Aggregate' ? ['Aggregate'] : [clickedView]);
        const isAggregate = clickedView === 'Aggregate';
        if (isAggregate) { currentNodeView = ['Aggregate']; } else { if (currentNodeView.length === 1 && currentNodeView[0] === 'Aggregate') { currentNodeView = [clickedView]; } else { const index = currentNodeView.indexOf(clickedView); if (index > -1) { currentNodeView.splice(index, 1); } else { currentNodeView.push(clickedView); } } }
        if (!isAggregate) { if (currentNodeView.length === 0 || currentNodeView.length === availableNodes.length) { currentNodeView = ['Aggregate']; } }
        initializePerformanceDataStateForView(currentNodeView);
        renderNodeSelector();
        heatmap.clearData();
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'set_view', view: currentNodeView }));
            if (performanceState.range !== '5m') { document.querySelector('#time-range-toggles [data-range="5m"]').click(); } else { ws.send(JSON.stringify({ type: 'get_historical_performance', view: currentNodeView, points: MAX_PERF_POINTS, interval_sec: PERFORMANCE_INTERVAL_MS / 1000 })); }
            requestHashstoreData();
        }
    });
    const mapCard = document.getElementById('map-card'), toggleMapSizeBtn = document.getElementById('toggle-map-size-btn');
    toggleMapSizeBtn.addEventListener('click', () => { const isMaximized = mapCard.classList.toggle('maximized'); document.body.classList.toggle('map-maximized', isMaximized); toggleMapSizeBtn.innerHTML = isMaximized ? '&#x2924;' : '&#x26F6;'; toggleMapSizeBtn.title = isMaximized ? 'Restore Map Size' : 'Maximize Map'; setTimeout(() => { map.invalidateSize(); }, 150); });
    document.querySelector('#hashstore-card table thead').addEventListener('click', e => {
        const header = e.target.closest('th');
        if (!header || !header.dataset.column) return;
        const column = header.dataset.column;
        if (hashstoreSort.column === column) {
            hashstoreSort.direction = hashstoreSort.direction === 'asc' ? 'desc' : 'asc';
        } else {
            hashstoreSort.column = column;
            hashstoreSort.direction = ['node_name', 'satellite', 'store'].includes(column) ? 'asc' : 'desc';
        }
        renderHashstoreView();
    });
}
function renderNodeSelector() {
    const selector = document.getElementById('node-selector');
    selector.innerHTML = '';
    const aggregateLink = document.createElement('a');
    aggregateLink.href = '#';
    aggregateLink.className = 'node-link';
    aggregateLink.textContent = 'Aggregate';
    aggregateLink.setAttribute('data-view', 'Aggregate');
    if (currentNodeView.length === 1 && currentNodeView[0] === 'Aggregate') aggregateLink.classList.add('active');
    selector.appendChild(aggregateLink);
    availableNodes.forEach(name => {
        const link = document.createElement('a');
        link.href = '#';
        link.className = 'node-link';
        link.setAttribute('data-view', name);
        if (currentNodeView.includes(name)) link.classList.add('active');
        
        // Add connection status indicator for network nodes
        if (nodeConnectionStates[name]) {
            const status = nodeConnectionStates[name];
            const statusIcon = document.createElement('span');
            statusIcon.className = 'connection-status-icon';
            statusIcon.textContent = getConnectionStatusIcon(status.state);
            statusIcon.title = getConnectionStatusTooltip(name);
            statusIcon.style.cssText = 'margin-right: 4px; font-size: 12px;';
            link.appendChild(statusIcon);
        }
        
        link.appendChild(document.createTextNode(name));
        selector.appendChild(link);
    });
}

// --- Dark Mode Chart Handler ---
const darkModeMediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
function handleThemeChange(isDarkMode) {
    const gridColor = isDarkMode ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)';
    const textColor = isDarkMode ? '#e0e0e0' : '#333';
    const tooltipBackgroundColor = isDarkMode ? 'rgba(30, 30, 30, 0.9)' : 'rgba(255, 255, 255, 0.95)';
    const tooltipBorderColor = isDarkMode ? '#555' : '#ccc';
    Chart.defaults.color = textColor;
    Chart.defaults.borderColor = gridColor;
    Object.values(Chart.instances).forEach(chart => {
        if(chart && chart.options.plugins && chart.options.plugins.tooltip) {
            const tooltip = chart.options.plugins.tooltip;
            tooltip.backgroundColor = tooltipBackgroundColor;
            tooltip.borderColor = tooltipBorderColor;
            tooltip.borderWidth = 1;
            tooltip.titleColor = textColor;
            tooltip.bodyColor = textColor;
            tooltip.footerColor = textColor;
        }
        if (chart) {
            chart.update();
        }
    });
}

// --- Main Initializer ---
document.addEventListener('DOMContentLoaded', () => {
    charts.createPerformanceChart(performanceState);
    charts.createSatelliteChart();
    charts.createSizeBarChart();
    charts.createHashstoreChart();
    connectionManager.connect();
    initializeDisplayMenu();
    setupEventListeners();
    handleThemeChange(darkModeMediaQuery.matches);
    darkModeMediaQuery.addEventListener('change', e => handleThemeChange(e.matches));
});
