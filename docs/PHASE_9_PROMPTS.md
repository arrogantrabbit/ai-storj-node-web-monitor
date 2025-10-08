# Phase 9: Multi-Node Comparison - Implementation Prompts

**Priority:** 🟠 HIGH - Top user-requested feature  
**Duration:** 1-1.5 weeks  
**Goal:** Enable comparison and ranking of multiple nodes

---

## Overview

These prompts will guide you through implementing comprehensive multi-node comparison features. Users with multiple nodes will be able to compare performance, earnings, and efficiency across their fleet.

**Prerequisites:**
- Phases 1-8 complete (especially Phase 8 for testing)
- Multiple nodes monitored in the system
- Historical data available for comparison

---

## Prompt 9.1: Backend Comparison Data Model

```
Create the backend infrastructure for multi-node comparison:

1. Add comparison data model to storj_monitor/server.py:

Add new WebSocket message handler for comparison data:
```python
# Handle "get_comparison_data" message type

async def handle_comparison_request(app, data):
    """
    Handle multi-node comparison data request
    
    Request format:
    {
        "type": "get_comparison_data",
        "node_names": ["Node1", "Node2", "Node3"],
        "comparison_type": "performance",  # or "earnings", "efficiency", "overall"
        "time_range": "24h",  # or "7d", "30d", "custom"
        "start_date": "2025-10-01",  # optional for custom range
        "end_date": "2025-10-08"  # optional for custom range
    }
    
    Response format:
    {
        "type": "comparison_data",
        "comparison_type": "performance",
        "nodes": [
            {
                "node_name": "Node1",
                "metrics": {
                    "success_rate_download": 98.5,
                    "success_rate_upload": 97.2,
                    "success_rate_audit": 100.0,
                    "avg_latency_p50": 245.67,
                    "avg_latency_p95": 892.30,
                    "avg_latency_p99": 1456.78,
                    "total_operations": 15234,
                    "uptime_percent": 99.8
                }
            },
            {
                "node_name": "Node2",
                "metrics": { ... }
            }
        ],
        "rankings": {
            "success_rate_download": ["Node1", "Node2", "Node3"],
            "avg_latency_p50": ["Node3", "Node1", "Node2"],
            ...
        }
    }
    """
    node_names = data.get('node_names', [])
    comparison_type = data.get('comparison_type', 'overall')
    time_range = data.get('time_range', '24h')
    
    # Calculate comparison metrics
    comparison_data = await calculate_comparison_metrics(
        app, node_names, comparison_type, time_range
    )
    
    return {
        'type': 'comparison_data',
        'comparison_type': comparison_type,
        'nodes': comparison_data['nodes'],
        'rankings': comparison_data['rankings']
    }
```

2. Implement metric calculation functions:

```python
async def calculate_comparison_metrics(app, node_names, comparison_type, time_range):
    """Calculate normalized metrics for comparison"""
    
    # Parse time range
    hours = parse_time_range(time_range)
    
    # Gather data for each node
    nodes_data = []
    for node_name in node_names:
        node_metrics = await gather_node_metrics(
            app, node_name, hours, comparison_type
        )
        nodes_data.append({
            'node_name': node_name,
            'metrics': node_metrics
        })
    
    # Calculate rankings
    rankings = calculate_rankings(nodes_data, comparison_type)
    
    return {
        'nodes': nodes_data,
        'rankings': rankings
    }

async def gather_node_metrics(app, node_name, hours, comparison_type):
    """Gather all metrics for a single node"""
    
    metrics = {}
    
    if comparison_type in ['performance', 'overall']:
        # Get performance metrics
        events = await get_events_async(app['db_path'], [node_name], hours)
        
        # Calculate success rates
        downloads = [e for e in events if e['action'] == 'GET']
        uploads = [e for e in events if e['action'] == 'PUT']
        audits = [e for e in events if e['action'] == 'GET_AUDIT']
        
        metrics['success_rate_download'] = calculate_success_rate(downloads)
        metrics['success_rate_upload'] = calculate_success_rate(uploads)
        metrics['success_rate_audit'] = calculate_success_rate(audits)
        
        # Calculate latency metrics
        durations = [e['duration_ms'] for e in events if e.get('duration_ms')]
        if durations:
            metrics['avg_latency_p50'] = calculate_percentile(durations, 50)
            metrics['avg_latency_p95'] = calculate_percentile(durations, 95)
            metrics['avg_latency_p99'] = calculate_percentile(durations, 99)
        
        metrics['total_operations'] = len(events)
    
    if comparison_type in ['earnings', 'overall']:
        # Get earnings data
        earnings = await get_latest_earnings_async(app, [node_name])
        if earnings:
            metrics['total_earnings'] = earnings[0].get('total_earnings', 0)
            metrics['earnings_per_tb'] = calculate_earnings_per_tb(earnings[0])
    
    if comparison_type in ['efficiency', 'overall']:
        # Get storage efficiency
        storage = await get_latest_storage_async(app['db_path'], [node_name])
        if storage:
            metrics['storage_utilization'] = storage[0].get('used_percent', 0)
            metrics['storage_efficiency'] = calculate_storage_efficiency(storage[0])
    
    # Get reputation scores
    reputation = await get_latest_reputation_async(app['db_path'], [node_name])
    if reputation:
        metrics['avg_audit_score'] = calculate_avg_score(reputation, 'audit_score')
        metrics['avg_online_score'] = calculate_avg_score(reputation, 'online_score')
    
    return metrics

def calculate_rankings(nodes_data, comparison_type):
    """Calculate rankings for each metric (higher is better)"""
    
    rankings = {}
    
    # Get all metric keys
    if nodes_data:
        metric_keys = nodes_data[0]['metrics'].keys()
        
        for metric_key in metric_keys:
            # Extract values for this metric from all nodes
            metric_values = []
            for node in nodes_data:
                value = node['metrics'].get(metric_key, 0)
                metric_values.append((node['node_name'], value))
            
            # Sort by value (descending for most metrics, ascending for latency)
            if 'latency' in metric_key:
                # Lower latency is better
                metric_values.sort(key=lambda x: x[1])
            else:
                # Higher is better
                metric_values.sort(key=lambda x: x[1], reverse=True)
            
            # Store ranking as list of node names
            rankings[metric_key] = [name for name, _ in metric_values]
    
    return rankings
```

3. Add helper functions for normalization:

```python
def calculate_earnings_per_tb(earnings_data):
    """Calculate earnings per TB stored"""
    total_earnings = earnings_data.get('total_earnings', 0)
    used_space_tb = earnings_data.get('used_space_tb', 0)
    
    if used_space_tb > 0:
        return total_earnings / used_space_tb
    return 0

def calculate_storage_efficiency(storage_data):
    """Calculate storage efficiency score (0-100)"""
    used_percent = storage_data.get('used_percent', 0)
    trash_percent = storage_data.get('trash_percent', 0)
    
    # Efficiency = used space - excessive trash
    # Penalize if trash > 10%
    efficiency = used_percent
    if trash_percent > 10:
        efficiency -= (trash_percent - 10)
    
    return max(0, min(100, efficiency))

def calculate_success_rate(events):
    """Calculate success rate for a list of events"""
    if not events:
        return 0
    
    successful = sum(1 for e in events if e.get('status') == 'success')
    return (successful / len(events)) * 100
```

4. Write tests for comparison logic (tests/test_comparison.py):

```python
import pytest
from storj_monitor.server import (
    calculate_comparison_metrics,
    calculate_rankings,
    calculate_earnings_per_tb
)

@pytest.mark.asyncio
async def test_calculate_comparison_metrics(temp_db):
    """Test comparison metrics calculation"""
    # Create mock app
    app = {'db_path': temp_db}
    
    # Test with sample nodes
    result = await calculate_comparison_metrics(
        app,
        ['Node1', 'Node2'],
        'performance',
        '24h'
    )
    
    assert 'nodes' in result
    assert 'rankings' in result
    assert len(result['nodes']) == 2

def test_calculate_rankings():
    """Test ranking calculation"""
    nodes_data = [
        {'node_name': 'Node1', 'metrics': {'success_rate': 98.5, 'latency': 200}},
        {'node_name': 'Node2', 'metrics': {'success_rate': 99.2, 'latency': 150}},
        {'node_name': 'Node3', 'metrics': {'success_rate': 97.8, 'latency': 250}}
    ]
    
    rankings = calculate_rankings(nodes_data, 'performance')
    
    # Node2 should rank first in success rate
    assert rankings['success_rate'][0] == 'Node2'
    
    # Node2 should rank first in latency (lower is better)
    assert rankings['latency'][0] == 'Node2'

def test_calculate_earnings_per_tb():
    """Test earnings per TB calculation"""
    earnings_data = {
        'total_earnings': 100.0,
        'used_space_tb': 2.0
    }
    
    per_tb = calculate_earnings_per_tb(earnings_data)
    assert per_tb == 50.0
```

Success criteria:
- Comparison data model implemented
- Metrics calculation functions working
- Ranking logic correct
- Tests passing with >80% coverage
```

---

## Prompt 9.2: Frontend Comparison UI Component

```
Create the frontend comparison interface:

1. Add comparison UI structure to storj_monitor/static/index.html:

Insert after the alerts panel (around line 320):
```html
<!-- Multi-Node Comparison Section -->
<div id="comparison-card" class="card" style="display: none;">
    <div class="card-header-flex">
        <h3>Multi-Node Comparison</h3>
        <button id="comparison-close-btn" class="close-btn">×</button>
    </div>
    
    <!-- Node Selection -->
    <div class="comparison-selector">
        <div class="selector-group">
            <label>Select Nodes to Compare (2-6):</label>
            <div id="comparison-node-checkboxes" class="checkbox-group">
                <!-- Dynamically populated with checkboxes -->
            </div>
        </div>
        
        <div class="selector-group">
            <label>Comparison Type:</label>
            <select id="comparison-type" class="comparison-select">
                <option value="overall">Overall Performance</option>
                <option value="performance">Performance Metrics</option>
                <option value="earnings">Earnings & Financial</option>
                <option value="efficiency">Storage Efficiency</option>
            </select>
        </div>
        
        <div class="selector-group">
            <label>Time Range:</label>
            <select id="comparison-timerange" class="comparison-select">
                <option value="24h">Last 24 Hours</option>
                <option value="7d">Last 7 Days</option>
                <option value="30d">Last 30 Days</option>
            </select>
        </div>
        
        <button id="comparison-refresh-btn" class="primary-btn">
            Compare Selected Nodes
        </button>
    </div>
    
    <!-- Comparison Results -->
    <div id="comparison-results" style="display: none;">
        <!-- Summary Cards -->
        <div class="comparison-summary">
            <h4>Comparison Summary</h4>
            <div id="comparison-summary-cards" class="comparison-cards-grid">
                <!-- Populated with summary cards -->
            </div>
        </div>
        
        <!-- Detailed Metrics Table -->
        <div class="comparison-table-container">
            <h4>Detailed Metrics</h4>
            <table id="comparison-metrics-table" class="comparison-table">
                <thead>
                    <tr>
                        <th>Metric</th>
                        <!-- Node columns added dynamically -->
                    </tr>
                </thead>
                <tbody>
                    <!-- Rows added dynamically -->
                </tbody>
            </table>
        </div>
        
        <!-- Comparison Charts -->
        <div class="comparison-charts">
            <h4>Visual Comparison</h4>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
                <div style="position: relative; height: 300px;">
                    <canvas id="comparison-bar-chart"></canvas>
                </div>
                <div style="position: relative; height: 300px;">
                    <canvas id="comparison-radar-chart"></canvas>
                </div>
            </div>
        </div>
        
        <!-- Export Button -->
        <div style="margin-top: 20px; text-align: right;">
            <button id="export-comparison-btn" class="export-btn">
                Export Comparison to CSV
            </button>
        </div>
    </div>
</div>

<!-- Comparison Toggle Button (in header) -->
<button id="comparison-toggle-btn" class="header-btn" style="display: none;">
    📊 Compare Nodes
</button>
```

2. Add comparison styling to storj_monitor/static/css/style.css:

```css
/* Multi-Node Comparison Styles */
.comparison-selector {
    padding: 20px;
    background: rgba(14, 165, 233, 0.05);
    border-radius: 8px;
    margin-bottom: 20px;
}

.selector-group {
    margin-bottom: 15px;
}

.selector-group label {
    display: block;
    margin-bottom: 8px;
    font-weight: 600;
    color: var(--text-primary);
}

.checkbox-group {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 10px;
}

.node-checkbox-item {
    display: flex;
    align-items: center;
    padding: 8px 12px;
    background: var(--bg-secondary);
    border: 1px solid var(--border-color);
    border-radius: 4px;
    cursor: pointer;
    transition: all 0.2s;
}

.node-checkbox-item:hover {
    background: var(--bg-tertiary);
    border-color: var(--accent-color);
}

.node-checkbox-item input[type="checkbox"] {
    margin-right: 8px;
    cursor: pointer;
}

.comparison-select {
    width: 100%;
    padding: 8px 12px;
    border: 1px solid var(--border-color);
    border-radius: 4px;
    background: var(--bg-primary);
    color: var(--text-primary);
    font-size: 1em;
}

.comparison-summary {
    margin: 20px 0;
}

.comparison-cards-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 15px;
    margin-top: 15px;
}

.comparison-card-item {
    padding: 15px;
    background: var(--bg-secondary);
    border: 2px solid var(--border-color);
    border-radius: 8px;
    text-align: center;
}

.comparison-card-item.winner {
    border-color: var(--success-color);
    background: rgba(34, 197, 94, 0.1);
}

.comparison-card-item .winner-badge {
    display: inline-block;
    padding: 2px 8px;
    background: var(--success-color);
    color: white;
    border-radius: 12px;
    font-size: 0.75em;
    font-weight: 600;
    margin-bottom: 8px;
}

.comparison-table-container {
    margin: 20px 0;
    overflow-x: auto;
}

.comparison-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9em;
}

.comparison-table th,
.comparison-table td {
    padding: 12px;
    text-align: left;
    border-bottom: 1px solid var(--border-color);
}

.comparison-table th {
    background: var(--bg-secondary);
    font-weight: 600;
    position: sticky;
    top: 0;
}

.comparison-table td {
    background: var(--bg-primary);
}

.comparison-table .rank-1 {
    background: rgba(34, 197, 94, 0.1);
    font-weight: 600;
}

.comparison-table .rank-2 {
    background: rgba(14, 165, 233, 0.05);
}

.comparison-table .rank-last {
    background: rgba(239, 68, 68, 0.05);
}

.comparison-charts {
    margin: 20px 0;
}

/* Dark mode */
@media (prefers-color-scheme: dark) {
    .comparison-selector {
        background: rgba(14, 165, 233, 0.1);
    }
}
```

Success criteria:
- Comparison UI displays correctly
- Node selection works (2-6 nodes)
- All dropdowns functional
- Styling consistent with app theme
- Dark mode support
```

---

## Prompt 9.3: Comparison Component Logic

```
Implement the comparison logic in storj_monitor/static/js/comparison.js:

Create new file storj_monitor/static/js/comparison.js:

```javascript
// Multi-Node Comparison Component

let comparisonChart = null;
let radarChart = null;
let latestComparisonData = null;

/**
 * Initialize comparison component
 */
function initComparisonComponent() {
    // Show comparison button only if multiple nodes exist
    const nodeSelector = document.getElementById('node-selector');
    if (nodeSelector && nodeSelector.options.length > 1) {
        document.getElementById('comparison-toggle-btn').style.display = 'inline-block';
    }
    
    // Event listeners
    document.getElementById('comparison-toggle-btn').addEventListener('click', openComparisonPanel);
    document.getElementById('comparison-close-btn').addEventListener('click', closeComparisonPanel);
    document.getElementById('comparison-refresh-btn').addEventListener('click', refreshComparison);
    document.getElementById('export-comparison-btn').addEventListener('click', exportComparisonToCSV);
    
    // Populate node checkboxes
    populateNodeCheckboxes();
    
    // Initialize charts
    comparisonChart = createComparisonBarChart();
    radarChart = createComparisonRadarChart();
}

/**
 * Populate node checkboxes for selection
 */
function populateNodeCheckboxes() {
    const container = document.getElementById('comparison-node-checkboxes');
    const nodeSelector = document.getElementById('node-selector');
    
    container.innerHTML = '';
    
    // Get all node names except "Aggregate"
    for (let i = 0; i < nodeSelector.options.length; i++) {
        const nodeName = nodeSelector.options[i].value;
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
            
            checkboxItem.appendChild(checkbox);
            checkboxItem.appendChild(label);
            container.appendChild(checkboxItem);
        }
    }
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
    
    // Request comparison data via WebSocket
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: 'get_comparison_data',
            node_names: selectedNodes,
            comparison_type: comparisonType,
            time_range: timeRange
        }));
    }
}

/**
 * Update comparison display with received data
 */
function updateComparisonDisplay(data) {
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
                <div style="font-size: 1.3em; font-weight: 600; color: var(--success-color);">${value}</div>
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
                } else if (rank === ranking.length - 1) {
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
 * Update comparison charts
 */
function updateComparisonCharts(data) {
    // Update bar chart
    updateComparisonBarChart(comparisonChart, data);
    
    // Update radar chart
    updateComparisonRadarChart(radarChart, data);
}

/**
 * Find winner for a metric
 */
function findWinner(nodes, metricKey, invert = false) {
    let winner = null;
    let bestValue = invert ? Infinity : -Infinity;
    
    nodes.forEach(node => {
        const value = node.metrics[metricKey];
        if (value !== undefined && value !== null) {
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
    } else if (metric.key && (metric.key.includes('rate') || metric.key.includes('percent'))) {
        return `${value.toFixed(1)}%`;
    } else if (metric.key && metric.key.includes('earnings')) {
        return `$${value.toFixed(2)}`;
    } else if (typeof value === 'number') {
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
        csv += formatMetricName(metricKey);
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

// Initialize on load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initComparisonComponent);
} else {
    initComparisonComponent();
}
```

Success criteria:
- Comparison logic works correctly
- Node selection validated
- Data displays in table format
- Export to CSV functional
- All event handlers working
```

---

## Prompt 9.4: Comparison Charts

```
Add comparison chart functions to storj_monitor/static/js/charts.js:

Add these functions at the end of the file:

```javascript
/**
 * Create comparison bar chart
 */
function createComparisonBarChart() {
    const ctx = document.getElementById('comparison-bar-chart');
    if (!ctx) return null;
    
    return new Chart(ctx, {
        type: 'bar',
        data: {
            labels: [],
            datasets: []
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                title: {
                    display: true,
                    text: 'Success Rates Comparison',
                    color: isDarkMode() ? '#e0e0e0' : '#333'
                },
                legend: {
                    display: true,
                    position: 'bottom',
                    labels: {
                        color: isDarkMode() ? '#e0e0e0' : '#333'
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    max: 100,
                    ticks: {
                        color: isDarkMode() ? '#e0e0e0' : '#333',
                        callback: function(value) {
                            return value + '%';
                        }
                    },
                    grid: {
                        color: isDarkMode() ? '#444' : '#e0e0e0'
                    }
                },
                x: {
                    ticks: {
                        color: isDarkMode() ? '#e0e0e0' : '#333'
                    },
                    grid: {
                        color: isDarkMode() ? '#444' : '#e0e0e0'
                    }
                }
            }
        }
    });
}

/**
 * Update comparison bar chart
 */
function updateComparisonBarChart(chart, data) {
    if (!chart || !data) return;
    
    // Extract success rates for each node
    const labels = ['Download', 'Upload', 'Audit'];
    const datasets = [];
    
    const colors = [
        '#0ea5e9', '#22c55e', '#f59e0b',
        '#a855f7', '#ef4444', '#14b8a6'
    ];
    
    data.nodes.forEach((node, index) => {
        datasets.push({
            label: node.node_name,
            data: [
                node.metrics.success_rate_download || 0,
                node.metrics.success_rate_upload || 0,
                node.metrics.success_rate_audit || 0
            ],
            backgroundColor: colors[index % colors.length],
            borderColor: colors[index % colors.length],
            borderWidth: 1
        });
    });
    
    chart.data.labels = labels;
    chart.data.datasets = datasets;
    chart.update();
}

/**
 * Create comparison radar chart
 */
function createComparisonRadarChart() {
    const ctx = document.getElementById('comparison-radar-chart');
    if (!ctx) return null;
    
    return new Chart(ctx, {
        type: 'radar',
        data: {
            labels: [],
            datasets: []
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                title: {
                    display: true,
                    text: 'Overall Performance',
                    color: isDarkMode() ? '#e0e0e0' : '#333'
                },
                legend: {
                    display: true,
                    position: 'bottom',
                    labels: {
                        color: isDarkMode() ? '#e0e0e0' : '#333'
                    }
                }
            },
            scales: {
                r: {
                    beginAtZero: true,
                    max: 100,
                    ticks: {
                        stepSize: 20,
                        color: isDarkMode() ? '#e0e0e0' : '#333',
                        backdropColor: 'transparent'
                    },
                    grid: {
                        color: isDarkMode() ? '#444' : '#e0e0e0'
                    },
                    pointLabels: {
                        color: isDarkMode() ? '#e0e0e0' : '#333',
                        font: {
                            size: 11
                        }
                    }
                }
            }
        }
    });
}

/**
 * Update comparison radar chart
 */
function updateComparisonRadarChart(chart, data) {
    if (!chart || !data) return;
    
    // Normalized metrics for radar (0-100 scale)
    const labels = [
        'Download Success',
        'Upload Success',
        'Audit Success',
        'Reputation',
        'Storage Efficiency'
    ];
    
    const datasets = [];
    const colors = [
        '#0ea5e9', '#22c55e', '#f59e0b',
        '#a855f7', '#ef4444', '#14b8a6'
    ];
    
    data.nodes.forEach((node, index) => {
        const metrics = node.metrics;
        
        datasets.push({
            label: node.node_name,
            data: [
                metrics.success_rate_download || 0,
                metrics.success_rate_upload || 0,
                metrics.success_rate_audit || 0,
                metrics.avg_audit_score || 0,
                metrics.storage_efficiency || 0
            ],
            backgroundColor: colors[index % colors.length] + '20',
            borderColor: colors[index % colors.length],
            borderWidth: 2,
            pointBackgroundColor: colors[index % colors.length],
            pointBorderColor: '#fff',
            pointHoverBackgroundColor: '#fff',
            pointHoverBorderColor: colors[index % colors.length]
        });
    });
    
    chart.data.labels = labels;
    chart.data.datasets = datasets;
    chart.update();
}
```

Success criteria:
- Bar chart displays success rates
- Radar chart shows overall performance
- Charts update with comparison data
- Colors distinguish nodes clearly
- Dark mode support
```

---

## Prompt 9.5: WebSocket Integration and Testing

```
Complete WebSocket integration and write comprehensive tests:

1. Update storj_monitor/server.py to handle comparison WebSocket messages:

In the WebSocket handler, add:
```python
elif msg_type == 'get_comparison_data':
    # Handle comparison data request
    response = await handle_comparison_request(app, data)
    await ws.send_json(response)
```

2. Update storj_monitor/static/js/app.js WebSocket handler:

In the WebSocket message handler, add:
```javascript
case 'comparison_data':
    updateComparisonDisplay(parsed.data);
    break;
```

3. Include comparison.js in index.html:

Add before closing </body>:
```html
<script src="/static/js/comparison.js"></script>
```

4. Write comprehensive tests (tests/test_comparison.py):

```python
import pytest
from storj_monitor.server import (
    calculate_comparison_metrics,
    calculate_rankings,
    calculate_earnings_per_tb,
    calculate_storage_efficiency,
    calculate_success_rate
)

@pytest.mark.asyncio
async def test_calculate_comparison_metrics_performance(temp_db, sample_events):
    """Test performance comparison metrics"""
    app = {'db_path': temp_db}
    
    # Add sample events to database
    for event in sample_events:
        blocking_write_event(temp_db, event)
    
    result = await calculate_comparison_metrics(
        app,
        ['Node1', 'Node2'],
        'performance',
        '24h'
    )
    
    assert 'nodes' in result
    assert 'rankings' in result
    assert len(result['nodes']) == 2
    
    # Check metrics exist
    for node in result['nodes']:
        assert 'success_rate_download' in node['metrics']
        assert 'avg_latency_p50' in node['metrics']

def test_calculate_rankings_success_rate():
    """Test ranking by success rate"""
    nodes_data = [
        {'node_name': 'Node1', 'metrics': {'success_rate': 98.5}},
        {'node_name': 'Node2', 'metrics': {'success_rate': 99.2}},
        {'node_name': 'Node3', 'metrics': {'success_rate': 97.8}}
    ]
    
    rankings = calculate_rankings(nodes_data, 'performance')
    
    # Node2 should rank first
    assert rankings['success_rate'][0] == 'Node2'
    assert rankings['success_rate'][1] == 'Node1'
    assert rankings['success_rate'][2] == 'Node3'

def test_calculate_rankings_latency():
    """Test ranking by latency (lower is better)"""
    nodes_data = [
        {'node_name': 'Node1', 'metrics': {'latency': 200}},
        {'node_name': 'Node2', 'metrics': {'latency': 150}},
        {'node_name': 'Node3', 'metrics': {'latency': 250}}
    ]
    
    rankings = calculate_rankings(nodes_data, 'performance')
    
    # Node2 should rank first (lowest latency)
    assert rankings['latency'][0] == 'Node2'
    assert rankings['latency'][1] == 'Node1'
    assert rankings['latency'][2] == 'Node3'

def test_calculate_storage_efficiency():
    """Test storage efficiency calculation"""
    # Good efficiency: high usage, low trash
    storage_data = {'used_percent': 80.0, 'trash_percent': 5.0}
    efficiency = calculate_storage_efficiency(storage_data)
    assert efficiency == 80.0
    
    # Penalized: high trash
    storage_data = {'used_percent': 80.0, 'trash_percent': 15.0}
    efficiency = calculate_storage_efficiency(storage_data)
    assert efficiency == 75.0  # 80 - (15 - 10)

def test_calculate_success_rate():
    """Test success rate calculation"""
    events = [
        {'status': 'success'},
        {'status': 'success'},
        {'status': 'failed'},
        {'status': 'success'}
    ]
    
    rate = calculate_success_rate(events)
    assert rate == 75.0  # 3 out of 4

def test_calculate_success_rate_empty():
    """Test success rate with no events"""
    rate = calculate_success_rate([])
    assert rate == 0

@pytest.mark.asyncio
async def test_comparison_with_missing_data(temp_db):
    """Test comparison handles missing data gracefully"""
    app = {'db_path': temp_db}
    
    result = await calculate_comparison_metrics(
        app,
        ['NonExistent1', 'NonExistent2'],
        'overall',
        '24h'
    )
    
    # Should return structure even with no data
    assert 'nodes' in result
    assert len(result['nodes']) == 2

# Integration test
@pytest.mark.asyncio
async def test_websocket_comparison_request(aiohttp_client):
    """Test WebSocket comparison request/response"""
    # Create test client
    # Send comparison request
    # Verify response format
    pass
```

5. Write frontend tests (if using Jest/Cypress):

Create tests/frontend/test_comparison.js:
```javascript
describe('Multi-Node Comparison', () => {
    test('opens comparison panel', () => {
        const btn = document.getElementById('comparison-toggle-btn');
        btn.click();
        const panel = document.getElementById('comparison-card');
        expect(panel.style.display).toBe('block');
    });
    
    test('validates node selection', () => {
        // Select only 1 node
        // Verify button is disabled
        // Select 2 nodes
        // Verify button is enabled
    });
    
    test('updates comparison display', () => {
        const mockData = {
            nodes: [
                {node_name: 'Node1', metrics: {success_rate: 98.5}},
                {node_name: 'Node2', metrics: {success_rate: 99.2}}
            ],
            rankings: {success_rate: ['Node2', 'Node1']}
        };
        
        updateComparisonDisplay(mockData);
        
        // Verify table is populated
        // Verify charts are updated
    });
});
```

Success criteria:
- All unit tests pass with >80% coverage
- Integration tests pass
- WebSocket communication works
- Frontend tests pass (if applicable)
- No console errors in browser
```

---

## Phase 9 Completion Checklist

Before marking Phase 9 as complete, verify:

- [ ] Backend comparison data model implemented
- [ ] Metric calculation functions working correctly
- [ ] Ranking logic accurate for all metric types
- [ ] Frontend comparison UI displays properly
- [ ] Node selection works (2-6 nodes)
- [ ] Comparison type selector functional
- [ ] Time range selector functional
- [ ] Summary cards show winners correctly
- [ ] Detailed metrics table populates
- [ ] Bar chart displays success rates
- [ ] Radar chart shows overall performance
- [ ] Export to CSV works
- [ ] Dark mode support complete
- [ ] All unit tests pass (>80% coverage)
- [ ] Integration tests pass
- [ ] WebSocket communication tested
- [ ] Performance tested with 6 nodes
- [ ] Documentation updated
- [ ] Phase completion document created

---

## Next Steps

After Phase 9 completion:
1. Create `docs/completed/PHASE_9_COMPLETE.md` documenting achievements
2. Update `docs/MASTER_ROADMAP.md` to mark Phase 9 complete
3. Proceed to Phase 10: Advanced Reporting & Export

---

**Multi-node comparison empowers operators to optimize their entire fleet! 📊**