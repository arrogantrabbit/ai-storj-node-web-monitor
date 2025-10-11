import { map, heatmap } from './map.js';
import * as charts from './charts.js';
import { initComparisonComponent } from './comparison.js';

// --- Constants & State ---
const PERFORMANCE_INTERVAL_MS = 2000;
const MAX_PERF_POINTS = 150;
const SATELLITE_NAMES = { '121RTSDpyNZVcEU84Ticf2L1ntiuUimbWgfATz21tuvgk3vzoA6': 'ap1', '12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S': 'us1', '12L9ZFwhzVpuEKMUNUqkaTLGzwY9G24tbiigLiXpmZWKwmcNDDs': 'eu1', '1wFTAgs9DP5RSnCqKV1eLf6N9wtk4EAtmN5DpSxcs8EjT69tGE': 'saltlake' };
const TOGGLEABLE_CARDS = {
    'map-card': 'Live Traffic Heatmap',
    'stats-card': 'Overall Success Rates & Speed',
    'health-card': 'Node Health & History',
    'reputation-card': 'Node Reputation Scores',
    'storage-health-card': 'Storage Health & Capacity',
    'latency-card': 'Operation Latency Analytics',
    'alerts-panel-card': 'Active Alerts',
    'earnings-card': 'Financial Earnings',
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
    agg: 'sum', // sum, avg
    cachedAggregatedData: null // Cache aggregated performance data for view switching
};
let latencyState = {
    range: '1h' // 30m, 1h, 6h, 12h, 24h
};
let storageState = {
    range: '7d', // 1d, 3d, 7d, 14d, 30d
    cachedData: null // Cache storage data for immediate range switching
};
window.storageState = storageState; // Make globally accessible for charts
let earningsState = {
    period: 'current', // current, previous, 12months
    cachedData: null
};
let cardVisibilityState = {};
let previousCardVisibilityState = {}; // Track previous state to detect transitions
let livePerformanceBins = {};
let maxHistoricalTimestampByView = {};
let isHistoricalDataLoaded = false;
let currentNodeView = ['Aggregate'];
let availableNodes = [];
window.availableNodes = availableNodes; // Make globally accessible for comparison.js
let chartUpdateTimer = null;
let hashstoreMasterData = [];
let hashstoreFilters = { satellite: 'all', store: 'all' };
let hashstoreSort = { column: 'last_run_iso', direction: 'desc' };
let latencyTimeWindow = { firstIso: null, lastIso: null };
let cachedStatsData = null; // Cache the last stats_update data
let cachedStatsViewKey = null; // Track which view the cached stats belong to
let ws;
window.ws = null;  // Global reference for AlertsPanel and comparison.js
window.currentView = ['Aggregate'];  // Global reference for AlertsPanel

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

// --- Loading Indicator Functions ---
function showLoadingIndicator(cardId) {
    const cardElement = document.getElementById(cardId);
    if (!cardElement) return;

    let loadingOverlay = cardElement.querySelector('.loading-overlay');
    if (!loadingOverlay) {
        loadingOverlay = document.createElement('div');
        loadingOverlay.className = 'loading-overlay';
        loadingOverlay.innerHTML = '<div class="loader"></div>';
        cardElement.appendChild(loadingOverlay);
    }
    loadingOverlay.classList.remove('hidden');
}

function hideLoadingIndicator(cardId) {
    const cardElement = document.getElementById(cardId);
    if (!cardElement) return;

    const loadingOverlay = cardElement.querySelector('.loading-overlay');
    if (loadingOverlay) {
        loadingOverlay.classList.add('hidden');
    }
}

// --- Card Visibility & Layout ---
function isCardVisible(cardId) {
    return cardVisibilityState[cardId] !== false;
}

function refreshCardData(cardId) {
    // Apply cached data immediately if available, then request fresh data
    switch(cardId) {
        case 'reputation-card':
            showLoadingIndicator(cardId);
            ws.send(JSON.stringify({
                type: 'get_reputation_data',
                view: currentNodeView
            }));
            break;
        case 'storage-health-card':
            showLoadingIndicator(cardId);
            ws.send(JSON.stringify({
                type: 'get_storage_data',
                view: currentNodeView
            }));
            break;
        case 'latency-card':
            showLoadingIndicator(cardId);
            const latencyHours = { '30m': 0.5, '1h': 1, '6h': 6, '12h': 12, '24h': 24 }[latencyState.range];
            ws.send(JSON.stringify({
                type: 'get_latency_stats',
                view: currentNodeView,
                hours: latencyHours
            }));
            break;
        case 'earnings-card':
            showLoadingIndicator(cardId);
            requestEarningsData();
            break;
        case 'hashstore-card':
        case 'hashstore-chart-card':
            showLoadingIndicator('hashstore-card');
            showLoadingIndicator('hashstore-chart-card');
            requestHashstoreData();
            break;
        case 'active-compactions-card':
            // Active compactions are automatically pushed via WebSocket, no request needed
            break;
        case 'performance-card':
            if (performanceState.range === '5m') {
                ws.send(JSON.stringify({
                    type: 'get_historical_performance',
                    view: currentNodeView,
                    points: MAX_PERF_POINTS,
                    interval_sec: PERFORMANCE_INTERVAL_MS / 1000
                }));
            } else {
                const hours = { '30m': 0.5, '1h': 1, '6h': 6, '24h': 24 }[performanceState.range];
                ws.send(JSON.stringify({
                    type: 'get_aggregated_performance',
                    view: currentNodeView,
                    hours: hours
                }));
            }
            break;
        case 'stats-card': {
            // Apply cached data only if it matches the current view
            const viewKeyNow = Array.isArray(currentNodeView) ? currentNodeView.join(',') : String(currentNodeView);
            if (cachedStatsData && cachedStatsViewKey === viewKeyNow) {
                updateOverallStats(cachedStatsData.overall);
                updateTitles(cachedStatsData.first_event_iso, cachedStatsData.last_event_iso);
            } else {
                showLoadingIndicator('stats-card');
            }
            break;
        }
        case 'satellite-card': {
            const viewKeyNow = Array.isArray(currentNodeView) ? currentNodeView.join(',') : String(currentNodeView);
            if (cachedStatsData && cachedStatsData.satellites && cachedStatsViewKey === viewKeyNow) {
                charts.updateSatelliteChart(cachedStatsData.satellites);
                updateTitles(cachedStatsData.first_event_iso, cachedStatsData.last_event_iso);
            } else {
                showLoadingIndicator('satellite-card');
            }
            break;
        }
        case 'size-charts-card': {
            const viewKeyNow = Array.isArray(currentNodeView) ? currentNodeView.join(',') : String(currentNodeView);
            if (cachedStatsData && cachedStatsData.transfer_sizes && cachedStatsViewKey === viewKeyNow) {
                charts.updateSizeBarChart(cachedStatsData.transfer_sizes);
                updateTitles(cachedStatsData.first_event_iso, cachedStatsData.last_event_iso);
            } else {
                showLoadingIndicator('size-charts-card');
            }
            break;
        }
        case 'health-card': {
            const viewKeyNow = Array.isArray(currentNodeView) ? currentNodeView.join(',') : String(currentNodeView);
            if (cachedStatsData && cachedStatsData.historical_stats && cachedStatsViewKey === viewKeyNow) {
                updateHistoricalTable(cachedStatsData.historical_stats);
            } else {
                showLoadingIndicator('health-card');
            }
            break;
        }
        case 'analysis-card': {
            const viewKeyNow = Array.isArray(currentNodeView) ? currentNodeView.join(',') : String(currentNodeView);
            if (cachedStatsData && cachedStatsViewKey === viewKeyNow) {
                updateAnalysisTables(cachedStatsData);
            } else {
                showLoadingIndicator('analysis-card');
            }
            break;
        }
        case 'alerts-panel-card':
            // Alerts are automatically pushed via WebSocket
            break;
        case 'map-card':
            // Map is automatically updated via live log entries
            break;
    }
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
    // Initialize previous state to current state (no transitions yet)
    previousCardVisibilityState = { ...cardVisibilityState };
    applyCardLayout();
}

function applyCardLayout() {
    for (const cardId in cardVisibilityState) {
        const cardElement = document.getElementById(cardId);
        const checkbox = document.querySelector(`#display-menu-dropdown input[data-card-id="${cardId}"]`);
        if (cardElement && checkbox) {
            const isVisible = cardVisibilityState[cardId];
            const wasVisible = previousCardVisibilityState[cardId];
            cardElement.classList.toggle('is-hidden', !isVisible);
            checkbox.checked = isVisible;
            if (cardId === 'map-card') {
                if (isVisible) heatmap.resume();
                else heatmap.pause();
            }
            
            // Detect transition from hidden to visible and refresh data
            if (isVisible && !wasVisible && ws && ws.readyState === WebSocket.OPEN) {
                refreshCardData(cardId);
            }
        }
    }
    // Update previous state for next comparison
    previousCardVisibilityState = { ...cardVisibilityState };
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
    ['reputation-card', 'storage-health-card', 'latency-card', 'alerts-panel-card', 'earnings-card', 'analysis-card', 'size-charts-card', 'active-compactions-card', 'hashstore-chart-card', 'hashstore-card'].forEach(cardId => {
        if (isVisible(cardId)) { setStyle(cardId, '1 / -1', `${currentRow} / ${currentRow + 1}`); currentRow++; }
    });
    if (mapVisible) {
        setTimeout(() => map.invalidateSize(), 150);
    }
}


// --- UI Update Functions ---
function updateAllVisuals(data) {
    // Cache the data and the view it belongs to
    cachedStatsData = data;
    cachedStatsViewKey = Array.isArray(currentNodeView) ? currentNodeView.join(',') : String(currentNodeView);

    // Only update visible cards
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

// --- Phase 3: Enhanced Monitoring Component Updates ---

function updateReputationCard(data) {
    if (!isCardVisible('reputation-card')) return;
    const container = document.getElementById('reputation-content');
    if (!data || data.length === 0) {
        container.innerHTML = '<p class="no-alerts-message">No reputation data available</p>';
        return;
    }
    
    let html = '';
    data.forEach(item => {
        const getScoreClass = (score) => {
            if (score >= 95) return 'rate-good';
            if (score >= 85) return 'rate-ok';
            return 'rate-bad';
        };
        
        const satName = SATELLITE_NAMES[item.satellite] || item.satellite.substring(0, 12);
        html += `<div class="reputation-satellite">
            <div class="reputation-satellite-header">
                <div class="reputation-satellite-name">${satName}</div>
                <small>Node: ${item.node_name}</small>
            </div>
            <div class="reputation-scores">
                <div class="reputation-score-item">
                    <div class="reputation-score-value ${getScoreClass(item.audit_score)}">
                        ${item.audit_score ? item.audit_score.toFixed(2) : 'N/A'}%
                    </div>
                    <div class="reputation-score-label">Audit Score</div>
                </div>
                <div class="reputation-score-item">
                    <div class="reputation-score-value ${getScoreClass(item.suspension_score)}">
                        ${item.suspension_score ? item.suspension_score.toFixed(2) : 'N/A'}%
                    </div>
                    <div class="reputation-score-label">Suspension Score</div>
                </div>
                <div class="reputation-score-item">
                    <div class="reputation-score-value ${getScoreClass(item.online_score)}">
                        ${item.online_score ? item.online_score.toFixed(2) : 'N/A'}%
                    </div>
                    <div class="reputation-score-label">Online Score</div>
                </div>
            </div>
        </div>`;
    });
    container.innerHTML = html;
}

function updateStorageHealthCard(data) {
    if (!isCardVisible('storage-health-card')) return;
    if (!data || data.length === 0) {
        document.getElementById('storage-used-percent').textContent = 'N/A';
        document.getElementById('storage-available').textContent = 'N/A';
        document.getElementById('storage-growth-rate').textContent = 'N/A';
        document.getElementById('storage-days-until-full').textContent = 'N/A';
        return;
    }
    
    // Aggregate storage data across all nodes in the view
    let totalUsedBytes = 0;
    let totalAvailableBytes = 0;
    let totalTrashBytes = 0;
    let totalAllocatedBytes = 0;
    
    data.forEach(node => {
        totalUsedBytes += node.used_bytes || 0;
        totalAvailableBytes += node.available_bytes || 0;
        totalTrashBytes += node.trash_bytes || 0;
        totalAllocatedBytes += node.allocated_bytes || 0;
    });
    
    // Calculate aggregated metrics
    const totalCapacity = totalUsedBytes + totalAvailableBytes;
    const usedPercent = totalCapacity > 0 ? (totalUsedBytes / totalCapacity * 100) : 0;
    
    document.getElementById('storage-used-percent').textContent = `${usedPercent.toFixed(1)}%`;
    document.getElementById('storage-available').textContent = formatBytes(totalAvailableBytes);
    
    // Map UI range selection to backend time windows
    // Backend provides: 1d, 7d, 30d
    // UI offers: 1d, 3d, 7d, 14d, 30d
    const rangeToWindow = {
        '1d': '1d',
        '3d': '7d',   // Use 7d as closest available
        '7d': '7d',
        '14d': '7d',  // Use 7d as closest available
        '30d': '30d'
    };
    
    const selectedWindow = rangeToWindow[storageState.range] || '7d';
    
    // Calculate aggregate growth rate for the selected window
    let totalGrowthRate = 0;
    let nodesWithData = 0;
    
    data.forEach(node => {
        if (node.growth_rates && node.growth_rates[selectedWindow]) {
            const rate = node.growth_rates[selectedWindow].growth_rate_bytes_per_day;
            if (rate != null && rate > 0) {
                totalGrowthRate += rate;
                nodesWithData++;
            }
        }
    });
    
    if (nodesWithData > 0 && totalGrowthRate > 0) {
        document.getElementById('storage-growth-rate').textContent = `${formatBytes(totalGrowthRate)}/day`;
        
        // Calculate days until full using the selected growth rate
        if (totalAvailableBytes > 0) {
            const daysUntilFull = Math.floor(totalAvailableBytes / totalGrowthRate);
            document.getElementById('storage-days-until-full').textContent =
                daysUntilFull > 365 ? '>365 days' : `~${daysUntilFull} days`;
        } else {
            document.getElementById('storage-days-until-full').textContent = 'N/A';
        }
    } else {
        document.getElementById('storage-growth-rate').textContent = 'N/A';
        document.getElementById('storage-days-until-full').textContent = 'N/A';
    }
    
    // Request storage history for chart using the selected range
    // Map UI range to days
    const rangeToDays = {
        '1d': 1,
        '3d': 3,
        '7d': 7,
        '14d': 14,
        '30d': 30
    };
    const daysToRequest = rangeToDays[storageState.range] || 7;
    
    // For aggregate view or multi-node: request histories for all nodes
    // For single-node view: request history for that one node
    if (ws && ws.readyState === WebSocket.OPEN && data.length > 0) {
        if (currentNodeView.length === 1 && currentNodeView[0] !== 'Aggregate') {
            // Single node view - request that node's history
            ws.send(JSON.stringify({
                type: 'get_storage_history',
                node_name: currentNodeView[0],
                days: daysToRequest
            }));
        } else {
            // Aggregate or multi-node view - request all nodes' histories
            data.forEach(node => {
                ws.send(JSON.stringify({
                    type: 'get_storage_history',
                    node_name: node.node_name,
                    days: daysToRequest
                }));
            });
        }
    }
}

function updateLatencyCard(data) {
    if (!isCardVisible('latency-card')) return;
    
    // Helper to clear stats with message
    const clearStatsWithMessage = (message) => {
        document.getElementById('latency-p50').textContent = 'N/A';
        document.getElementById('latency-p50').className = 'stat-value';
        document.getElementById('latency-p95').textContent = 'N/A';
        document.getElementById('latency-p95').className = 'stat-value';
        document.getElementById('latency-p99').textContent = 'N/A';
        document.getElementById('latency-p99').className = 'stat-value';
        document.getElementById('latency-mean').textContent = 'N/A';
        document.getElementById('latency-mean').className = 'stat-value';
        
        const tbody = document.getElementById('slow-operations-body');
        tbody.innerHTML = message;
    };
    
    if (!data || !data.statistics) {
        clearStatsWithMessage('<tr><td colspan="6" style="text-align: center; padding: 20px;">No latency data available for this time window.<br><small style="color: #888;">Tip: Enable DEBUG logging on Storj nodes for duration tracking.</small></td></tr>');
        return;
    }
    
    // Check if we have any actual data
    const allStats = data.statistics.all || {};
    if (!allStats.count || allStats.count === 0) {
        clearStatsWithMessage('<tr><td colspan="6" style="text-align: center; padding: 20px;">No operations with latency data found in this time window.<br><small style="color: #888;">Operations: ' + (data.total_operations || 0) + ' total, ' + (data.operations_with_latency || 0) + ' with latency data</small></td></tr>');
        return;
    }
    
    // Calculate time window from slow operations data
    const formatTime = date => date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    let timeWindowStr = "(Last 1 Hour)";
    
    if (data.slow_operations && data.slow_operations.length > 0) {
        const timestamps = data.slow_operations.map(op => new Date(op.timestamp)).sort((a, b) => a - b);
        const firstTime = timestamps[0];
        const lastTime = timestamps[timestamps.length - 1];
        timeWindowStr = `(${formatTime(firstTime)} - ${formatTime(lastTime)})`;
        latencyTimeWindow.firstIso = firstTime.toISOString();
        latencyTimeWindow.lastIso = lastTime.toISOString();
    } else if (latencyTimeWindow.firstIso && latencyTimeWindow.lastIso) {
        // Use previously stored time window if no new slow operations
        timeWindowStr = `(${formatTime(new Date(latencyTimeWindow.firstIso))} - ${formatTime(new Date(latencyTimeWindow.lastIso))})`;
    }
    
    // Update title with time interval
    const latencyTitle = document.querySelector('#latency-card .card-title');
    if (latencyTitle) {
        const viewName = currentNodeView.length === 1 && currentNodeView[0] === 'Aggregate' ? '' :
            ` (${currentNodeView.length === 1 ? currentNodeView[0] : currentNodeView.length + ' nodes'})`;
        latencyTitle.textContent = `Operation Latency Analytics${viewName} ${timeWindowStr}`;
    }
    
    const getLatencyClass = (ms) => {
        if (ms < 1000) return 'latency-good';
        if (ms < 5000) return 'latency-ok';
        return 'latency-bad';
    };
    
    const formatMs = (ms) => ms < 1000 ? `${ms.toFixed(0)}ms` : `${(ms/1000).toFixed(2)}s`;
    
    // Use 'all' category statistics
    // Use allStats for display
    const p50Elem = document.getElementById('latency-p50');
    const p95Elem = document.getElementById('latency-p95');
    const p99Elem = document.getElementById('latency-p99');
    const meanElem = document.getElementById('latency-mean');
    
    // Force update by setting values even if they haven't changed
    if (allStats.p50 != null && allStats.p50 > 0) {
        p50Elem.textContent = formatMs(allStats.p50);
        p50Elem.className = `stat-value ${getLatencyClass(allStats.p50)}`;
    } else {
        p50Elem.textContent = 'N/A';
        p50Elem.className = 'stat-value';
    }
    
    if (allStats.p95 != null && allStats.p95 > 0) {
        p95Elem.textContent = formatMs(allStats.p95);
        p95Elem.className = `stat-value ${getLatencyClass(allStats.p95)}`;
    } else {
        p95Elem.textContent = 'N/A';
        p95Elem.className = 'stat-value';
    }
    
    if (allStats.p99 != null && allStats.p99 > 0) {
        p99Elem.textContent = formatMs(allStats.p99);
        p99Elem.className = `stat-value ${getLatencyClass(allStats.p99)}`;
    } else {
        p99Elem.textContent = 'N/A';
        p99Elem.className = 'stat-value';
    }
    
    if (allStats.mean != null && allStats.mean > 0) {
        meanElem.textContent = formatMs(allStats.mean);
        meanElem.className = `stat-value ${getLatencyClass(allStats.mean)}`;
    } else {
        meanElem.textContent = 'N/A';
        meanElem.className = 'stat-value';
    }
    
    // Update slow operations table
    const tbody = document.getElementById('slow-operations-body');
    if (data.slow_operations && data.slow_operations.length > 0) {
        tbody.innerHTML = data.slow_operations.map(op => {
            const satName = SATELLITE_NAMES[op.satellite_id] || op.satellite_id?.substring(0, 12) || 'Unknown';
            const time = new Date(op.timestamp).toLocaleTimeString();
            const pieceId = op.piece_id ? (op.piece_id.length > 30 ? op.piece_id.substring(0, 30) + '...' : op.piece_id) : 'N/A';
            return `<tr>
                <td>${time}</td>
                <td>${op.action}</td>
                <td class="numeric ${getLatencyClass(op.duration_ms)}">${formatMs(op.duration_ms)}</td>
                <td>${satName}</td>
                <td class="piece-id">${pieceId}</td>
                <td>${op.status}</td>
            </tr>`;
        }).join('');
    } else {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center;">No slow operations detected</td></tr>';
    }
}

let activeAlerts = [];

function updateAlertsPanel(alerts, type) {
    if (!isCardVisible('alerts-panel-card')) return;
    
    // Merge new alerts with existing ones
    if (alerts && alerts.length > 0) {
        alerts.forEach(alert => {
            alert.type = type;
            alert.id = `${type}-${Date.now()}-${Math.random()}`;
            alert.timestamp = new Date().toISOString();
            // Check if similar alert already exists
            const exists = activeAlerts.some(a =>
                a.type === alert.type &&
                a.node_name === alert.node_name &&
                a.title === alert.title
            );
            if (!exists) {
                activeAlerts.push(alert);
            }
        });
    }
    
    renderAlertsPanel();
}

function renderAlertsPanel() {
    const container = document.getElementById('alerts-container');
    const badge = document.getElementById('alerts-badge');
    
    badge.textContent = activeAlerts.length;
    badge.className = activeAlerts.length > 0 ? 'alerts-badge' : 'alerts-badge no-alerts';
    
    if (activeAlerts.length === 0) {
        container.innerHTML = '<p class="no-alerts-message">No active alerts - all systems healthy ✓</p>';
        return;
    }
    
    const html = activeAlerts.map(alert => {
        const time = new Date(alert.timestamp).toLocaleTimeString();
        return `<div class="alert-item ${alert.severity}" data-alert-id="${alert.id}">
            <div class="alert-content">
                <div class="alert-title">${alert.title}</div>
                <div class="alert-message">${alert.message}</div>
                <div class="alert-time">${time} - ${alert.node_name}</div>
            </div>
            <button class="alert-dismiss" onclick="dismissAlert('${alert.id}')">×</button>
        </div>`;
    }).join('');
    
    container.innerHTML = html;
}

// --- Phase 6: Financial Tracking Functions ---

function calculateDaysSinceLastPayout() {
    const now = new Date();
    const payoutDay = 10; // Payout on the 10th of each month
    const currentDay = now.getDate();
    
    if (currentDay >= payoutDay) {
        // Last payout was this month
        return currentDay - payoutDay;
    } else {
        // Last payout was last month
        const lastMonth = new Date(now.getFullYear(), now.getMonth(), payoutDay);
        lastMonth.setMonth(lastMonth.getMonth() - 1);
        const diffTime = now - lastMonth;
        return Math.floor(diffTime / (1000 * 60 * 60 * 24));
    }
}

function aggregateEarnings(earningsArray) {
    if (!earningsArray || earningsArray.length === 0) {
        return {
            total_earnings: 0,
            held_amount: 0,
            egress: 0,
            storage: 0,
            repair: 0,
            audit: 0
        };
    }
    
    return earningsArray.reduce((acc, item) => {
        acc.total_earnings += item.total_net || 0;
        acc.held_amount += item.held_amount || 0;
        acc.egress += item.breakdown?.egress || 0;
        acc.storage += item.breakdown?.storage || 0;
        acc.repair += item.breakdown?.repair || 0;
        acc.audit += item.breakdown?.audit || 0;
        return acc;
    }, {
        total_earnings: 0,
        held_amount: 0,
        egress: 0,
        storage: 0,
        repair: 0,
        audit: 0
    });
}

function updateEarningsBreakdown(breakdown, totalEarnings) {
    const total = breakdown.egress + breakdown.storage + breakdown.repair + breakdown.audit;

    if (total === 0 && totalEarnings > 0) {
        // API data provides a total but no breakdown, show a message
        document.querySelectorAll('.earnings-breakdown-item').forEach(item => {
            item.querySelector('.breakdown-fill').style.width = '0%';
            item.querySelector('.breakdown-amount').textContent = 'Pending';
            item.querySelector('.breakdown-percent').textContent = '';
        });
        return;
    }
    
    if (total === 0) {
        document.querySelectorAll('.earnings-breakdown-item').forEach(item => {
            item.querySelector('.breakdown-fill').style.width = '0%';
            item.querySelector('.breakdown-amount').textContent = '$0.00';
            item.querySelector('.breakdown-percent').textContent = '(0%)';
        });
        return;
    }
    
    const categories = [
        { selector: '#earnings-breakdown-list .earnings-breakdown-item:nth-child(1)', value: breakdown.egress, label: 'egress' },
        { selector: '#earnings-breakdown-list .earnings-breakdown-item:nth-child(2)', value: breakdown.storage, label: 'storage' },
        { selector: '#earnings-breakdown-list .earnings-breakdown-item:nth-child(3)', value: breakdown.repair, label: 'repair' },
        { selector: '#earnings-breakdown-list .earnings-breakdown-item:nth-child(4)', value: breakdown.audit, label: 'audit' }
    ];
    
    categories.forEach(cat => {
        const item = document.querySelector(cat.selector);
        if (!item) return;
        
        const percent = (cat.value / total * 100).toFixed(1);
        item.querySelector('.breakdown-fill').style.width = `${percent}%`;
        item.querySelector('.breakdown-amount').textContent = `$${cat.value.toFixed(2)}`;
        item.querySelector('.breakdown-percent').textContent = `(${percent}%)`;
    });
}

function updateSatelliteEarnings(satelliteEarnings) {
    const container = document.getElementById('satellite-earnings-list');
    
    if (!container) {
        console.error('satellite-earnings-list container not found');
        return;
    }
    
    if (!satelliteEarnings || satelliteEarnings.length === 0) {
        container.innerHTML = '<p class="no-alerts-message" style="text-align: center; padding: 20px; color: #888;">No satellite earnings data available</p>';
        return;
    }
    
    // Aggregate earnings by satellite (across multiple nodes)
    const bySatellite = {};
    satelliteEarnings.forEach(sat => {
        const satName = sat.satellite || 'Unknown';
        if (!bySatellite[satName]) {
            bySatellite[satName] = {
                total_net: 0,
                total_gross: 0,
                held_amount: 0,
                nodes: []
            };
        }
        bySatellite[satName].total_net += sat.total_net || 0;
        bySatellite[satName].total_gross += sat.total_gross || 0;
        bySatellite[satName].held_amount += sat.held_amount || 0;
        if (sat.node_name && !bySatellite[satName].nodes.includes(sat.node_name)) {
            bySatellite[satName].nodes.push(sat.node_name);
        }
    });
    
    // Sort by total earnings (descending)
    const sortedSatellites = Object.entries(bySatellite).sort((a, b) => b[1].total_net - a[1].total_net);
    
    let html = '';
    sortedSatellites.forEach(([satName, data]) => {
        const nodeInfo = data.nodes.length > 0 ? ` | ${data.nodes.length} node${data.nodes.length > 1 ? 's' : ''}` : '';
        
        html += `<div class="satellite-earnings-item">
            <div class="satellite-earnings-header">
                <strong>${satName}</strong>
                <span class="satellite-earnings-total">$${data.total_net.toFixed(2)}</span>
            </div>
            <div class="satellite-earnings-details">
                <small>Gross: $${data.total_gross.toFixed(2)} | Held: $${data.held_amount.toFixed(2)}${nodeInfo}</small>
            </div>
        </div>`;
    });
    
    if (html) {
        container.innerHTML = html;
    } else {
        container.innerHTML = '<p class="no-alerts-message" style="text-align: center; padding: 20px; color: #888;">No satellite earnings data available</p>';
    }
}

function updateEarningsCard(data) {
    if (!isCardVisible('earnings-card')) return;
    
    // Backend sends earnings as array directly, not wrapped in {earnings: [...]}
    const earningsArray = Array.isArray(data) ? data : (data?.earnings || []);
    
    if (!earningsArray || earningsArray.length === 0) {
        console.warn('[Earnings] No earnings data to display for current view', { currentView: [...currentNodeView], selectedPeriod: earningsState.period });
        document.getElementById('earnings-total').textContent = '$0.00';
        document.getElementById('earnings-forecast').textContent = '$0.00';
        document.getElementById('earnings-held').textContent = '$0.00';
        document.getElementById('earnings-payout-days').textContent = '-- days';
        document.getElementById('satellite-earnings-list').innerHTML = '<p class="no-alerts-message">No earnings data available</p>';
        // Clear breakdown bars when no data
        updateEarningsBreakdown({ egress: 0, storage: 0, repair: 0, audit: 0 });
        return;
    }
    
    // Aggregate earnings across all satellites
    const aggregated = aggregateEarnings(earningsArray);
    
    // Update summary stats
    document.getElementById('earnings-total').textContent = `$${aggregated.total_earnings.toFixed(2)}`;
    document.getElementById('earnings-held').textContent = `$${aggregated.held_amount.toFixed(2)}`;
    
    // Calculate forecast (estimate for the month based on current progress)
    let forecast = 0;
    if (earningsState.period === 'current' && earningsArray.length > 0) {
        const now = new Date();
        const dayOfMonth = now.getDate();
        const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
        const progressRatio = dayOfMonth / daysInMonth;
        
        if (progressRatio > 0) {
            forecast = aggregated.total_earnings / progressRatio;
        }
    } else {
        forecast = aggregated.total_earnings;
    }
    document.getElementById('earnings-forecast').textContent = `$${forecast.toFixed(2)}`;
    
    // Calculate and update earnings per TB stored
    // Get current storage data to calculate per-TB rate
    if (storageState.cachedData && storageState.cachedData.length > 0) {
        let totalStoredTB = 0;
        storageState.cachedData.forEach(node => {
            const usedBytes = node.used_bytes || 0;
            totalStoredTB += usedBytes / (1024 ** 4); // Convert to TB
        });
        
        if (totalStoredTB > 0) {
            const earningsPerTB = aggregated.total_earnings / totalStoredTB;
            document.getElementById('earnings-per-tb').textContent = `$${earningsPerTB.toFixed(2)}`;
        } else {
            document.getElementById('earnings-per-tb').textContent = 'N/A';
        }
    } else {
        document.getElementById('earnings-per-tb').textContent = 'N/A';
    }
    
    // Update days since last payout
    const daysSinceLastPayout = calculateDaysSinceLastPayout();
    document.getElementById('earnings-payout-days').textContent = `${daysSinceLastPayout} days`;
    
    // Update breakdown bars with actual data
    updateEarningsBreakdown({
        egress: aggregated.egress,
        storage: aggregated.storage,
        repair: aggregated.repair,
        audit: aggregated.audit
    }, aggregated.total_earnings);
    
    // Update breakdown chart
    charts.updateEarningsBreakdownChart({
        egress: aggregated.egress,
        storage: aggregated.storage,
        repair: aggregated.repair,
        audit: aggregated.audit
    });
    
    // Update per-satellite earnings
    updateSatelliteEarnings(earningsArray);
    
    // Request historical chart data
    if (ws && ws.readyState === WebSocket.OPEN && earningsArray.length > 0) {
        // Request history for each unique node
        const uniqueNodes = [...new Set(earningsArray.map(e => e.node_name))];
        uniqueNodes.forEach(nodeName => {
            ws.send(JSON.stringify({
                type: 'get_earnings_history',
                node_name: nodeName,
                days: 30
            }));
        });
    }
}

function requestEarningsData(period) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        showLoadingIndicator('earnings-card');
        try {
            console.debug('[Earnings] request', { view: [...currentNodeView], period: period || earningsState.period });
        } catch (e) {}
        ws.send(JSON.stringify({
            type: 'get_earnings_data',
            view: currentNodeView,
            period: period || earningsState.period
        }));
    }
}


window.dismissAlert = function(alertId) {
    activeAlerts = activeAlerts.filter(a => a.id !== alertId);
    renderAlertsPanel();
};

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
const connectionManager = { overlay: document.getElementById('connection-overlay'), reconnectDelay: 1000, maxReconnectDelay: 30000, connect: function() { const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'; ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws`); window.ws = ws;  // Update global reference for comparison.js and other components
 ws.onopen = () => { this.overlay.style.display = 'none'; this.reconnectDelay = 1000; console.log("[WebSocket] Connection opened."); requestHashstoreData(); setTimeout(() => { if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: 'get_reputation_data', view: currentNodeView })); const latencyHours = { '30m': 0.5, '1h': 1, '6h': 6, '12h': 12, '24h': 24 }[latencyState.range]; ws.send(JSON.stringify({ type: 'get_latency_stats', view: currentNodeView, hours: latencyHours })); ws.send(JSON.stringify({ type: 'get_storage_data', view: currentNodeView })); requestEarningsData(); } }, 1000); }; ws.onmessage = (event) => { const data = JSON.parse(event.data); if (data.type !== 'log_entry' && data.type !== 'performance_batch_update' && data.type !== 'log_entry_batch') { console.log(`[WebSocket] Received message type: ${data.type}`); } handleWebSocketMessage(data); }; ws.onclose = () => { window.ws = null;  // Clear global reference on disconnect
 this.overlay.style.display = 'flex'; setTimeout(() => this.connect(), this.reconnectDelay); this.reconnectDelay = Math.min(this.maxReconnectDelay, this.reconnectDelay * 2); console.warn(`[WebSocket] Connection closed. Reconnecting in ${this.reconnectDelay}ms.`); }; ws.onerror = err => { console.error("[WebSocket] Error:", err); ws.close(); }; } };
function handleWebSocketMessage(data) {
    switch(data.type) {
        case 'init':
            availableNodes = data.nodes;
            window.availableNodes = availableNodes; // Update global reference
            renderNodeSelector();
            initializePerformanceData(data.nodes);
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'get_historical_performance', view: currentNodeView, points: MAX_PERF_POINTS, interval_sec: PERFORMANCE_INTERVAL_MS / 1000 }));
            }
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
                hideLoadingIndicator('performance-card');
            }
            break;
        }
        case 'aggregated_performance_data':
            // Cache the aggregated data for view switching
            performanceState.cachedAggregatedData = data.performance_data;
            if (isCardVisible('performance-card')) {
                charts.updatePerformanceChart(performanceState, data.performance_data, currentNodeView, availableNodes);
                hideLoadingIndicator('performance-card');
            }
            break;
        case 'stats_update':
            updateAllVisuals(data);
            // Track cache ownership by view and clear loaders for dependent panels
            cachedStatsViewKey = Array.isArray(currentNodeView) ? currentNodeView.join(',') : String(currentNodeView);
            hideLoadingIndicator('stats-card');
            hideLoadingIndicator('health-card');
            hideLoadingIndicator('satellite-card');
            hideLoadingIndicator('size-charts-card'); // Hide loading indicator after data update
            hideLoadingIndicator('analysis-card');
            break;
        case 'hashstore_updated': console.log("[WebSocket] Hashstore data updated. Requesting new data."); requestHashstoreData(); break;
        case 'hashstore_stats_data':
            updateHashstorePanel(data.data);
            hideLoadingIndicator('hashstore-card');
            hideLoadingIndicator('hashstore-chart-card');
            break;
        case 'active_compactions_update': updateActiveCompactions(data.compactions); break;
        case 'reputation_data':
            updateReputationCard(data.data);
            hideLoadingIndicator('reputation-card');
            break;
        case 'latency_stats':
            updateLatencyCard(data.data);
            hideLoadingIndicator('latency-card');
            // Request histogram data
            if (ws && ws.readyState === WebSocket.OPEN) {
                const latencyHours = { '30m': 0.5, '1h': 1, '6h': 6, '12h': 12, '24h': 24 }[latencyState.range];
                ws.send(JSON.stringify({
                    type: 'get_latency_histogram',
                    view: currentNodeView,
                    hours: latencyHours
                }));
            }
            break;
        case 'latency_histogram': charts.updateLatencyHistogramChart(data.data); break;
        case 'storage_data':
            // Cache the storage data for immediate range switching
            storageState.cachedData = data.data;
            updateStorageHealthCard(data.data);
            hideLoadingIndicator('storage-health-card');
            break;
        case 'storage_history': charts.updateStorageHistoryChart(data.data); break;
        case 'reputation_alerts': updateAlertsPanel(data.alerts, 'reputation'); break;
        case 'storage_alerts': updateAlertsPanel(data.alerts, 'storage'); break;
        
        // Phase 4: AlertsPanel integration
        case 'active_alerts':
            if (window.alertsPanel) {
                window.alertsPanel.updateAlerts(data.data);
            }
            break;
        case 'new_alert':
            if (window.alertsPanel) {
                window.alertsPanel.handleNewAlert(data.alert);
            }
            break;
        case 'insights_data':
            if (window.alertsPanel) {
                window.alertsPanel.updateInsights(data.data);
            }
            break;
        case 'alert_summary':
            // Optional: Update UI with summary counts
            console.log('Alert summary:', data.data);
            break;
        case 'alert_acknowledge_result':
            console.log('Alert acknowledged:', data.alert_id, data.success);
            break;
        case 'earnings_data': {
            // Debug: log payload summary
            try {
                const count = Array.isArray(data?.data) ? data.data.length : 0;
                console.debug('[Earnings] payload received', {
                    incomingView: data?.view,
                    incomingPeriod: data?.period_name,
                    currentView: [...currentNodeView],
                    selectedPeriod: earningsState.period,
                    itemCount: count
                });
            } catch (e) {
                console.warn('[Earnings] debug-log error', e);
            }

            // Optional: ignore payloads that don't match the current view (when server includes 'view')
            if (data.view) {
                const normalizeView = (v) => Array.isArray(v) ? [...v].sort().join('|') : String(v);
                const payloadViewKey = normalizeView(data.view);
                const currentViewKey = normalizeView(currentNodeView);
                if (payloadViewKey !== currentViewKey) {
                    console.log('[Earnings] Ignoring payload for mismatched view', data.view, 'current is', currentNodeView);
                    break;
                }
            }
            // Ignore updates for a different period than currently selected
            if (data.period_name && data.period_name !== earningsState.period) {
                console.log('[Earnings] Ignoring payload for period', data.period_name, 'while selected is', earningsState.period);
                break;
            }

            // Debug before applying
            try {
                const preview = Array.isArray(data?.data) ? data.data.slice(0, 3) : [];
                console.debug('[Earnings] applying update', { itemCount: Array.isArray(data?.data) ? data.data.length : 0, preview });
            } catch (e) {}

            earningsState.cachedData = data.data;
            updateEarningsCard(data.data);
            hideLoadingIndicator('earnings-card');
            break;
        }
        case 'earnings_history':
            if (data.data && data.data.length > 0 && isCardVisible('earnings-card')) {
                charts.updateEarningsHistoryChart(data.data);
            }
            break;
        case 'comparison_data':
            if (window.updateComparisonDisplay) {
                window.updateComparisonDisplay(data);
            }
            break;
    }
}

// --- UI Event Listeners ---
function setupEventListeners() {
    document.getElementById('toggle-satellite-view').addEventListener('click', function(e) { e.preventDefault(); charts.toggleSatelliteView(); if(isCardVisible('satellite-card')) charts.updateSatelliteChart(); });
    document.getElementById('size-view-toggles').addEventListener('click', function(e) { e.preventDefault(); const target = e.target; if (target.tagName === 'A' && !target.classList.contains('active')) { showLoadingIndicator('size-charts-card'); charts.setSizeChartViewMode(target.getAttribute('data-view')); document.querySelectorAll('#size-view-toggles .toggle-link').forEach(el => el.classList.remove('active')); target.classList.add('active'); if(isCardVisible('size-charts-card')) charts.updateSizeBarChart(); } });
    document.getElementById('performance-toggles').addEventListener('click', function(e) { e.preventDefault(); if (e.target.tagName === 'A') { performanceState.view = e.target.getAttribute('data-view'); document.querySelectorAll('#performance-toggles .toggle-link').forEach(el => el.classList.remove('active')); e.target.classList.add('active'); const isLiveView = performanceState.range === '5m'; const dataToUse = isLiveView ? livePerformanceBins : performanceState.cachedAggregatedData; charts.updatePerformanceChart(performanceState, dataToUse, currentNodeView, availableNodes); } });
    document.getElementById('time-range-toggles').addEventListener('click', function(e) { e.preventDefault(); const newRange = e.target.getAttribute('data-range'); if (newRange === performanceState.range) return; performanceState.range = newRange; performanceState.cachedAggregatedData = null; document.querySelectorAll('#time-range-toggles .toggle-link').forEach(el => el.classList.remove('active')); e.target.classList.add('active'); charts.createPerformanceChart(performanceState); if (newRange === '5m') { charts.updatePerformanceChart(performanceState, livePerformanceBins, currentNodeView, availableNodes); } else { showLoadingIndicator('performance-card'); const hours = { '30m': 0.5, '1h': 1, '6h': 6, '24h': 24 }[newRange]; if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: 'get_aggregated_performance', view: currentNodeView, hours: hours })); } } });
    document.getElementById('aggregation-toggles').addEventListener('click', function(e) { e.preventDefault(); if (e.target.tagName === 'A') { performanceState.agg = e.target.getAttribute('data-agg'); document.querySelectorAll('#aggregation-toggles .toggle-link').forEach(el => el.classList.remove('active')); e.target.classList.add('active'); const isLiveView = performanceState.range === '5m'; const dataToUse = isLiveView ? livePerformanceBins : performanceState.cachedAggregatedData; charts.updatePerformanceChart(performanceState, dataToUse, currentNodeView, availableNodes); } });
    document.getElementById('latency-range-toggles').addEventListener('click', function(e) {
        e.preventDefault();
        const newRange = e.target.getAttribute('data-range');
        if (!newRange || newRange === latencyState.range) return;
        latencyState.range = newRange;
        document.querySelectorAll('#latency-range-toggles .toggle-link').forEach(el => el.classList.remove('active'));
        e.target.classList.add('active');
        showLoadingIndicator('latency-card');
        // Request latency data with new range
        if (ws && ws.readyState === WebSocket.OPEN) {
            const hours = { '30m': 0.5, '1h': 1, '6h': 6, '12h': 12, '24h': 24 }[newRange];
            ws.send(JSON.stringify({
                type: 'get_latency_stats',
                view: currentNodeView,
                hours: hours
            }));
        }
    });
    document.getElementById('storage-range-toggles').addEventListener('click', function(e) {
        e.preventDefault();
        const newRange = e.target.getAttribute('data-range');
        if (!newRange || newRange === storageState.range) return;
        storageState.range = newRange;
        document.querySelectorAll('#storage-range-toggles .toggle-link').forEach(el => el.classList.remove('active'));
        e.target.classList.add('active');
        showLoadingIndicator('storage-health-card');
        
        // Immediately update display with cached data if available
        if (storageState.cachedData) {
            updateStorageHealthCard(storageState.cachedData);
        }
        
        // Request fresh data from server and chart history with new range
        if (ws && ws.readyState === WebSocket.OPEN) {
            // Request storage data (for the summary stats)
            ws.send(JSON.stringify({
                type: 'get_storage_data',
                view: currentNodeView
            }));
            
            // Request storage history for the chart with the new range
            const rangeToDays = {
                '1d': 1,
                '3d': 3,
                '7d': 7,
                '14d': 14,
                '30d': 30
            };
            const daysToRequest = rangeToDays[newRange] || 7;
            
            // Clear storage history cache to force fresh data
            if (typeof charts !== 'undefined' && charts.clearStorageHistoryCache) {
                charts.clearStorageHistoryCache();
            }
            
            if (currentNodeView.length === 1 && currentNodeView[0] !== 'Aggregate') {
                // Single node view
                ws.send(JSON.stringify({
                    type: 'get_storage_history',
                    node_name: currentNodeView[0],
                    days: daysToRequest
                }));
            } else {
                // Aggregate or multi-node view
                const nodesToQuery = currentNodeView[0] === 'Aggregate' ?
                    availableNodes : currentNodeView;
                nodesToQuery.forEach(nodeName => {
                    ws.send(JSON.stringify({
                        type: 'get_storage_history',
                        node_name: nodeName,
                        days: daysToRequest
                    }));
                });
            }
        }
    });
    document.getElementById('node-selector').addEventListener('click', function(e) {
        e.preventDefault();
        if (!e.target.hasAttribute('data-view')) return;
        const clickedView = e.target.getAttribute('data-view');
        initializePerformanceDataStateForView(clickedView === 'Aggregate' ? ['Aggregate'] : [clickedView]);
        const isAggregate = clickedView === 'Aggregate';
        if (isAggregate) { currentNodeView = ['Aggregate']; } else { if (currentNodeView.length === 1 && currentNodeView[0] === 'Aggregate') { currentNodeView = [clickedView]; } else { const index = currentNodeView.indexOf(clickedView); if (index > -1) { currentNodeView.splice(index, 1); } else { currentNodeView.push(clickedView); } } }
        if (!isAggregate) { if (currentNodeView.length === 0 || currentNodeView.length === availableNodes.length) { currentNodeView = ['Aggregate']; } }
        window.currentView = currentNodeView;  // Update global reference for AlertsPanel
        initializePerformanceDataStateForView(currentNodeView);
        renderNodeSelector();
        heatmap.clearData();
        
        // Ensure stats-related panels don't momentarily show stale (unfiltered) cached data
        cachedStatsData = null;
        cachedStatsViewKey = null;

        // Show loading indicators for cards that will be updated
        showLoadingIndicator('stats-card');
        showLoadingIndicator('health-card');
        showLoadingIndicator('satellite-card');
        showLoadingIndicator('size-charts-card');
        showLoadingIndicator('analysis-card');

        showLoadingIndicator('performance-card');
        showLoadingIndicator('reputation-card');
        showLoadingIndicator('latency-card');
        showLoadingIndicator('storage-health-card');
        showLoadingIndicator('earnings-card');
        showLoadingIndicator('hashstore-card');
        showLoadingIndicator('hashstore-chart-card');
        
        // Clear storage and earnings history caches when switching views to force fresh data
        if (typeof charts !== 'undefined') {
            if (charts.clearStorageHistoryCache) {
                charts.clearStorageHistoryCache();
            }
            if (charts.clearEarningsHistoryCache) {
                charts.clearEarningsHistoryCache();
            }
        }
        
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'set_view', view: currentNodeView }));
            if (performanceState.range !== '5m') { document.querySelector('#time-range-toggles [data-range="5m"]').click(); } else { ws.send(JSON.stringify({ type: 'get_historical_performance', view: currentNodeView, points: MAX_PERF_POINTS, interval_sec: PERFORMANCE_INTERVAL_MS / 1000 })); }
            requestHashstoreData();
            
            // Request Phase 3 enhanced monitoring data for new view
            ws.send(JSON.stringify({
                type: 'get_reputation_data',
                view: currentNodeView
            }));
            
            const latencyHours = { '30m': 0.5, '1h': 1, '6h': 6, '12h': 12, '24h': 24 }[latencyState.range];
            ws.send(JSON.stringify({
                type: 'get_latency_stats',
                view: currentNodeView,
                hours: latencyHours
            }));
            
            const storageDays = { '1d': 1, '3d': 3, '7d': 7, '14d': 14, '30d': 30 }[storageState.range];
            ws.send(JSON.stringify({
                type: 'get_storage_data',
                view: currentNodeView,
                days: storageDays
            }));
            
            // Request earnings data when switching nodes
            requestEarningsData();
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
    
    // Earnings period toggle
    document.getElementById('earnings-period-toggles').addEventListener('click', function(e) {
        e.preventDefault();
        const newPeriod = e.target.getAttribute('data-period');
        if (!newPeriod || newPeriod === earningsState.period) return;
        earningsState.period = newPeriod;
        document.querySelectorAll('#earnings-period-toggles .toggle-link').forEach(el => el.classList.remove('active'));
        e.target.classList.add('active');
        requestEarningsData(newPeriod);
    });
    
    // CSV Export button handler
    document.getElementById('export-earnings-csv-btn').addEventListener('click', function(e) {
        e.preventDefault();
        exportEarningsToCSV();
    });
    
    // ROI Calculator event listeners
    document.getElementById('roi-monthly-costs').addEventListener('input', calculateROI);
    document.getElementById('roi-initial-investment').addEventListener('input', calculateROI);
}

function calculateROI() {
    const monthlyCosts = parseFloat(document.getElementById('roi-monthly-costs').value) || 0;
    const initialInvestment = parseFloat(document.getElementById('roi-initial-investment').value) || 0;
    
    // Get current monthly earnings (use forecast if available)
    const forecastText = document.getElementById('earnings-forecast').textContent;
    const monthlyEarnings = parseFloat(forecastText.replace('$', '')) || 0;
    
    // Calculate metrics
    const monthlyProfit = monthlyEarnings - monthlyCosts;
    const profitMargin = monthlyEarnings > 0 ? (monthlyProfit / monthlyEarnings * 100) : 0;
    const paybackMonths = monthlyProfit > 0 ? (initialInvestment / monthlyProfit) : null;
    
    // Update display
    document.getElementById('roi-monthly-profit').textContent = `$${monthlyProfit.toFixed(2)}`;
    document.getElementById('roi-monthly-profit').className = `stat-value ${monthlyProfit >= 0 ? 'rate-good' : 'rate-bad'}`;
    
    document.getElementById('roi-margin').textContent = `${profitMargin.toFixed(1)}%`;
    document.getElementById('roi-margin').className = `stat-value ${profitMargin >= 0 ? 'rate-good' : 'rate-bad'}`;
    
    if (paybackMonths !== null && initialInvestment > 0) {
        if (paybackMonths > 120) {
            document.getElementById('roi-payback-months').textContent = '>10 years';
        } else if (paybackMonths > 12) {
            document.getElementById('roi-payback-months').textContent = `${(paybackMonths / 12).toFixed(1)} years`;
        } else {
            document.getElementById('roi-payback-months').textContent = `${paybackMonths.toFixed(1)} months`;
        }
    } else {
        document.getElementById('roi-payback-months').textContent = '-- months';
    }
}

async function updatePayoutAccuracy() {
    // This would fetch payout history from database
    // For now, show placeholder values
    // In a full implementation, this would query the payout_history table
    document.getElementById('payout-accuracy-rate').textContent = 'N/A';
    document.getElementById('payout-last-variance').textContent = 'N/A';
    document.getElementById('payout-history-count').textContent = '0';
}

function exportEarningsToCSV() {
    // Get current earnings state
    if (!earningsState.cachedData) {
        alert('No earnings data available to export');
        return;
    }
    
    // Prepare CSV data
    const csvRows = [];
    
    // Header
    csvRows.push([
        'Node Name',
        'Satellite',
        'Period',
        'Total Net ($)',
        'Total Gross ($)',
        'Held Amount ($)',
        'Egress Earnings ($)',
        'Storage Earnings ($)',
        'Repair Earnings ($)',
        'Audit Earnings ($)',
        'Forecast Month End ($)',
        'Confidence'
    ].join(','));
    
    // Get period name for filename
    const periodName = earningsState.period === 'current' ? 'current' :
                      earningsState.period === 'previous' ? 'previous' :
                      earningsState.period === '12months' ? '12months' : earningsState.period;
    
    // Data rows from cached data
    const data = Array.isArray(earningsState.cachedData) ? earningsState.cachedData :
                 earningsState.cachedData?.data || [];
    
    data.forEach(item => {
        const breakdown = item.breakdown || {};
        csvRows.push([
            item.node_name || '',
            item.satellite || '',
            periodName,
            item.total_net || 0,
            item.total_gross || 0,
            item.held_amount || 0,
            breakdown.egress || 0,
            breakdown.storage || 0,
            breakdown.repair || 0,
            breakdown.audit || 0,
            item.forecast_month_end || '',
            item.confidence || ''
        ].join(','));
    });
    
    // Create CSV blob and download
    const csvContent = csvRows.join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    
    // Generate filename with timestamp
    const now = new Date();
    const timestamp = now.toISOString().replace(/[:.]/g, '-').substring(0, 19);
    const viewName = currentNodeView.length === 1 && currentNodeView[0] === 'Aggregate' ? 'aggregate' :
                     currentNodeView.length === 1 ? currentNodeView[0] : 'multi-node';
    const filename = `storj-earnings-${viewName}-${periodName}-${timestamp}.csv`;
    
    link.setAttribute('href', url);
    link.setAttribute('download', filename);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    
    log.info(`Exported earnings data to ${filename}`);
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
        link.textContent = name;
        link.setAttribute('data-view', name);
        if (currentNodeView.includes(name)) link.classList.add('active');
        selector.appendChild(link);
    });
    
    // Initialize comparison feature after nodes are rendered (Phase 9)
    if (availableNodes.length > 1) {
        initComparisonComponent();
    }
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
    charts.createStorageHistoryChart();
    charts.createLatencyHistogramChart();
    charts.createEarningsHistoryChart();
    charts.createEarningsBreakdownChart();
    connectionManager.connect();
    initializeDisplayMenu();
    setupEventListeners();
    
    
    // Request Phase 3 data periodically
    setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            // Request reputation data every 5 minutes
            ws.send(JSON.stringify({
                type: 'get_reputation_data',
                view: currentNodeView
            }));
            
            // Request latency stats every minute
            const latencyHours = { '30m': 0.5, '1h': 1, '6h': 6, '12h': 12, '24h': 24 }[latencyState.range];
            ws.send(JSON.stringify({
                type: 'get_latency_stats',
                view: currentNodeView,
                hours: latencyHours
            }));
            
            // Request storage data every 5 minutes
            const storageDays = { '1d': 1, '3d': 3, '7d': 7, '14d': 14, '30d': 30 }[storageState.range];
            ws.send(JSON.stringify({
                type: 'get_storage_data',
                view: currentNodeView,
                days: storageDays
            }));
        }
    }, 60000); // Every minute
    
    handleThemeChange(darkModeMediaQuery.matches);
    darkModeMediaQuery.addEventListener('change', e => handleThemeChange(e.matches));
});
