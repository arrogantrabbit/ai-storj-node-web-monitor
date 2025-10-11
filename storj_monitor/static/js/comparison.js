// Multi-Node Comparison Component
import {
    createComparisonBarChart,
    updateComparisonBarChart,
    createComparisonRadarChart,
    updateComparisonRadarChart
} from './charts.js';

let comparisonChart = null;
let radarChart = null;
let latestComparisonData = null;

/**
 * Show loading indicator while comparison is being generated
 */
function showComparisonLoading() {
    const resultsSection = document.getElementById('comparison-results');
    const refreshBtn = document.getElementById('comparison-refresh-btn');
    
    // Keep results section visible to avoid page jump during loading
    // resultsSection.style.display = 'none';
    
    // Create or show loading overlay
    let loadingOverlay = document.getElementById('comparison-loading-overlay');
    if (!loadingOverlay) {
        loadingOverlay = document.createElement('div');
        loadingOverlay.id = 'comparison-loading-overlay';
        loadingOverlay.className = 'loading-overlay';
        loadingOverlay.innerHTML = '<div class="loader"></div><p style="margin-top: 15px; font-size: 1.1em;">Generating comparison...</p>';
        
        const card = document.getElementById('comparison-card');
        card.appendChild(loadingOverlay);
    }
    loadingOverlay.classList.remove('hidden');
    
    // Disable refresh button while loading
    refreshBtn.disabled = true;
    refreshBtn.textContent = 'Generating comparison...';
}

/**
 * Hide loading indicator when comparison is ready
 */
function hideComparisonLoading() {
    const loadingOverlay = document.getElementById('comparison-loading-overlay');
    const refreshBtn = document.getElementById('comparison-refresh-btn');
    
    if (loadingOverlay) {
        loadingOverlay.classList.add('hidden');
    }
    
    // Re-enable refresh button
    const checkboxes = document.querySelectorAll('#comparison-node-checkboxes input[type="checkbox"]:checked');
    refreshBtn.disabled = false;
    refreshBtn.textContent = `Compare ${checkboxes.length} Nodes`;

    // UX: ensure the top of the comparison card is visible after results render
    const card = document.getElementById('comparison-card');
    if (card) {
        card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

/**
 * Initialize comparison component
 */
export function initComparisonComponent() {
    // Prevent double-initialization (renderNodeSelector can be called multiple times)
    if (window.comparisonInitialized) {
        return;
    }
    window.comparisonInitialized = true;

    console.log('[Comparison] Initializing comparison component');
    
    // Show comparison button only if multiple nodes exist
    if (window.availableNodes && window.availableNodes.length > 1) {
        const btn = document.getElementById('comparison-toggle-btn');
        if (btn) {
            btn.style.display = 'inline-block';
            console.log('[Comparison] Button made visible, nodes:', window.availableNodes);
        } else {
            console.error('[Comparison] Button element not found');
        }
    } else {
        console.log('[Comparison] Not enough nodes:', window.availableNodes);
    }
    
    // Event listeners (attach once)
    const toggleBtn = document.getElementById('comparison-toggle-btn');
    const closeBtn = document.getElementById('comparison-close-btn');
    const refreshBtn = document.getElementById('comparison-refresh-btn');
    const exportBtn = document.getElementById('export-comparison-btn');
    
    if (toggleBtn) toggleBtn.addEventListener('click', openComparisonPanel);
    if (closeBtn) closeBtn.addEventListener('click', closeComparisonPanel);
    if (refreshBtn) refreshBtn.addEventListener('click', refreshComparison);
    if (exportBtn) exportBtn.addEventListener('click', exportComparisonToCSV);
    
    // Populate node checkboxes
    populateNodeCheckboxes();
    
    // Initialize charts
    initializeComparisonCharts();
}

/**
 * Populate node checkboxes for selection
 */
function populateNodeCheckboxes() {
    const container = document.getElementById('comparison-node-checkboxes');
    const nodeSelector = document.getElementById('node-selector');
    
    if (!container || !nodeSelector) return;
    
    container.innerHTML = '';
    
    // Get all node names except "Aggregate"
    const nodeLinks = nodeSelector.querySelectorAll('.node-link');
    nodeLinks.forEach((link, i) => {
        const nodeName = link.textContent.trim();
        if (nodeName !== 'Aggregate') {
            const checkboxItem = document.createElement('div');
            checkboxItem.className = 'node-checkbox-item';
            
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.id = `compare-node-${i}`;
            checkbox.value = nodeName;
            checkbox.addEventListener('change', validateNodeSelection);
            
            const label = document.createElement('label');
            label.htmlFor = `compare-node-${i}`;
            label.textContent = nodeName;
            label.style.cursor = 'pointer';
            label.style.flex = '1';
            
            checkboxItem.appendChild(checkbox);
            checkboxItem.appendChild(label);
            container.appendChild(checkboxItem);
        }
    });
}

/**
 * Validate node selection (2-6 nodes)
 */
function validateNodeSelection() {
    const checkboxes = document.querySelectorAll('#comparison-node-checkboxes input[type="checkbox"]:checked');
    const refreshBtn = document.getElementById('comparison-refresh-btn');
    
    if (checkboxes.length < 2) {
        refreshBtn.disabled = true;
        refreshBtn.textContent = 'Select at least 2 nodes';
    } else if (checkboxes.length > 6) {
        refreshBtn.disabled = true;
        refreshBtn.textContent = 'Maximum 6 nodes allowed';
    } else {
        refreshBtn.disabled = false;
        refreshBtn.textContent = `Compare ${checkboxes.length} Nodes`;
    }
}

/**
 * Open comparison panel
 */
function openComparisonPanel() {
    document.getElementById('comparison-card').style.display = 'block';
    
    // Auto-select first 2 nodes if none selected
    const checkboxes = document.querySelectorAll('#comparison-node-checkboxes input[type="checkbox"]');
    const checked = document.querySelectorAll('#comparison-node-checkboxes input[type="checkbox"]:checked');
    
    if (checked.length === 0 && checkboxes.length >= 2) {
        checkboxes[0].checked = true;
        checkboxes[1].checked = true;
        validateNodeSelection();
    }
    
    // Scroll to comparison card
    document.getElementById('comparison-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/**
 * Close comparison panel
 */
function closeComparisonPanel() {
    document.getElementById('comparison-card').style.display = 'none';
}

/**
 * Refresh comparison with selected nodes
 */
function refreshComparison() {
    const selectedNodes = Array.from(
        document.querySelectorAll('#comparison-node-checkboxes input[type="checkbox"]:checked')
    ).map(cb => cb.value);
    
    if (selectedNodes.length < 2 || selectedNodes.length > 6) {
        alert('Please select 2-6 nodes to compare');
        return;
    }
    
    const comparisonType = document.getElementById('comparison-type').value;
    const timeRange = document.getElementById('comparison-timerange').value;
    
    // Show loading indicator
    showComparisonLoading();
    
    // Request comparison data via WebSocket
    if (window.ws && window.ws.readyState === WebSocket.OPEN) {
        console.log('[Comparison] Sending comparison request:', {
            node_names: selectedNodes,
            comparison_type: comparisonType,
            time_range: timeRange
        });
        window.ws.send(JSON.stringify({
            type: 'get_comparison_data',
            node_names: selectedNodes,
            comparison_type: comparisonType,
            time_range: timeRange
        }));
    } else {
        console.error('[Comparison] WebSocket not connected, state:', window.ws?.readyState);
        hideComparisonLoading();
        alert('Connection error. Please refresh the page.');
    }
}

/**
 * Update comparison display with received data
 */
function updateComparisonDisplay(data) {
    console.log('[Comparison] Updating display with data:', data);
    
    // Hide loading indicator
    hideComparisonLoading();
    
    // Validate data structure
    if (!data || !data.nodes || !Array.isArray(data.nodes)) {
        console.error('[Comparison] Invalid data structure:', data);
        alert('Invalid comparison data received from server');
        return;
    }
    
    if (data.nodes.length === 0) {
        console.warn('[Comparison] No node data received');
        alert('No data available for selected nodes');
        return;
    }
    
    latestComparisonData = data;
    
    // Show results section
    document.getElementById('comparison-results').style.display = 'block';
    
    // Update summary cards
    updateComparisonSummary(data);
    
    // Update metrics table
    updateComparisonTable(data);
    
    // Update charts
    updateComparisonCharts(data);
}

// Make globally accessible for WebSocket handler in app.js
window.updateComparisonDisplay = updateComparisonDisplay;

/**
 * Update comparison summary cards
 */
function updateComparisonSummary(data) {
    const container = document.getElementById('comparison-summary-cards');
    container.innerHTML = '';
    
    // Create winner cards for key metrics
    const keyMetrics = [
        { key: 'success_rate_download', label: 'Download Success', suffix: '%' },
        { key: 'avg_latency_p50', label: 'Fastest (P50)', suffix: 'ms', invert: true },
        { key: 'total_earnings', label: 'Highest Earnings', prefix: '$' },
        { key: 'storage_utilization', label: 'Storage Utilization', suffix: '%' }
    ];
    
    keyMetrics.forEach(metric => {
        const winner = findWinner(data.nodes, metric.key, metric.invert);
        if (winner) {
            const card = document.createElement('div');
            card.className = 'comparison-card-item winner';
            
            const value = formatMetricValue(winner.metrics[metric.key], metric);
            
            card.innerHTML = `
                <div class="winner-badge">🏆 Winner</div>
                <div style="font-size: 0.85em; color: #666; margin-bottom: 5px;">${metric.label}</div>
                <div style="font-size: 1.3em; font-weight: 600; color: #22c55e;">${value}</div>
                <div style="font-size: 0.9em; margin-top: 5px;">${winner.node_name}</div>
            `;
            
            container.appendChild(card);
        }
    });
}

/**
 * Update comparison metrics table
 */
function updateComparisonTable(data) {
    const table = document.getElementById('comparison-metrics-table');
    const thead = table.querySelector('thead tr');
    const tbody = table.querySelector('tbody');
    
    // Clear existing content
    thead.innerHTML = '<th>Metric</th>';
    tbody.innerHTML = '';
    
    // Add node columns
    data.nodes.forEach(node => {
        const th = document.createElement('th');
        th.textContent = node.node_name;
        thead.appendChild(th);
    });
    
    // Get all unique metrics
    const allMetrics = new Set();
    data.nodes.forEach(node => {
        Object.keys(node.metrics).forEach(key => allMetrics.add(key));
    });
    
    // Create rows for each metric
    allMetrics.forEach(metricKey => {
        const tr = document.createElement('tr');
        
        // Metric name column
        const tdMetric = document.createElement('td');
        tdMetric.textContent = formatMetricName(metricKey);
        tdMetric.style.fontWeight = '600';
        tr.appendChild(tdMetric);
        
        // Get ranking for this metric
        const ranking = data.rankings[metricKey] || [];
        
        // Value columns for each node
        data.nodes.forEach(node => {
            const td = document.createElement('td');
            const value = node.metrics[metricKey];
            
            if (value !== undefined && value !== null) {
                td.textContent = formatMetricValue(value, { key: metricKey });
                
                // Apply ranking styles
                const rank = ranking.indexOf(node.node_name);
                if (rank === 0) {
                    td.classList.add('rank-1');
                } else if (rank === 1) {
                    td.classList.add('rank-2');
                } else if (rank === ranking.length - 1 && ranking.length > 2) {
                    td.classList.add('rank-last');
                }
            } else {
                td.textContent = 'N/A';
                td.style.color = '#999';
            }
            
            tr.appendChild(td);
        });
        
        tbody.appendChild(tr);
    });
}

/**
 * Initialize comparison charts
 */
function initializeComparisonCharts() {
    const barCanvas = document.getElementById('comparison-bar-chart');
    const radarCanvas = document.getElementById('comparison-radar-chart');
    
    if (!barCanvas || !radarCanvas) return;

    // If charts already exist (e.g., due to re-render), destroy them first
    if (comparisonChart && typeof comparisonChart.destroy === 'function') {
        try { comparisonChart.destroy(); } catch (e) { console.warn('Destroy bar chart warning:', e); }
        comparisonChart = null;
    }
    if (radarChart && typeof radarChart.destroy === 'function') {
        try { radarChart.destroy(); } catch (e) { console.warn('Destroy radar chart warning:', e); }
        radarChart = null;
    }
    
    comparisonChart = createComparisonBarChart(barCanvas);
    radarChart = createComparisonRadarChart(radarCanvas);
}

/**
 * Update comparison charts
 */
function updateComparisonCharts(data) {
    // Update bar chart
    if (comparisonChart) {
        updateComparisonBarChart(comparisonChart, data);
    }
    
    // Update radar chart
    if (radarChart) {
        updateComparisonRadarChart(radarChart, data);
    }
}

/**
 * Find winner for a metric
 */
function findWinner(nodes, metricKey, invert = false) {
    let winner = null;
    let bestValue = invert ? Infinity : -Infinity;
    
    nodes.forEach(node => {
        const value = node.metrics[metricKey];
        if (value !== undefined && value !== null && value > 0) {
            if (invert ? value < bestValue : value > bestValue) {
                bestValue = value;
                winner = node;
            }
        }
    });
    
    return winner;
}

/**
 * Format metric name for display
 */
function formatMetricName(key) {
    return key
        .replace(/_/g, ' ')
        .replace(/\b\w/g, l => l.toUpperCase());
}

/**
 * Format metric value for display
 */
function formatMetricValue(value, metric) {
    if (value === null || value === undefined) return 'N/A';
    
    const prefix = metric.prefix || '';
    const suffix = metric.suffix || '';
    
    // Format based on metric type
    if (metric.key && metric.key.includes('latency')) {
        return `${Math.round(value)} ms`;
    } else if (metric.key && (metric.key.includes('rate') || metric.key.includes('percent') || metric.key.includes('utilization'))) {
        return `${value.toFixed(1)}%`;
    } else if (metric.key && metric.key.includes('earnings')) {
        return `$${value.toFixed(2)}`;
    } else if (metric.key && metric.key.includes('score')) {
        // Reputation scores are percentage-based
        return `${value.toFixed(1)}%`;
    } else if (metric.key && (metric.key.includes('operations') || metric.key === 'total_operations' || metric.key.includes('count'))) {
        return `${Math.round(value)}`;
    } else if (typeof value === 'number') {
        if (value > 1000) {
            return `${prefix}${value.toFixed(0)}${suffix}`;
        }
        return `${prefix}${value.toFixed(2)}${suffix}`;
    }
    
    return value.toString();
}

/**
 * Export comparison to CSV
 */
function exportComparisonToCSV() {
    if (!latestComparisonData) {
        alert('No comparison data available');
        return;
    }
    
    // Build CSV content
    let csv = 'Metric';
    latestComparisonData.nodes.forEach(node => {
        csv += `,${node.node_name}`;
    });
    csv += '\n';
    
    // Get all metrics
    const allMetrics = new Set();
    latestComparisonData.nodes.forEach(node => {
        Object.keys(node.metrics).forEach(key => allMetrics.add(key));
    });
    
    // Add data rows
    allMetrics.forEach(metricKey => {
        csv += `"${formatMetricName(metricKey)}"`;
        latestComparisonData.nodes.forEach(node => {
            const value = node.metrics[metricKey];
            csv += `,${value !== undefined && value !== null ? value : 'N/A'}`;
        });
        csv += '\n';
    });
    
    // Download
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `node-comparison-${new Date().toISOString().split('T')[0]}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
}

// Note: initComparisonComponent() is called from app.js after nodes are loaded