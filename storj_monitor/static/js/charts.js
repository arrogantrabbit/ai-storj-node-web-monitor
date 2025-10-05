let performanceChartInstance;
let satelliteChart;
let sizeBarChart;
let hashstoreChartInstance;

let satelliteViewIsBySize = false;
let lastSatelliteData = [];
let lastTransferSizes = [];
let sizeChartViewMode = 'counts';

const DOWNLOAD_COLOR = '#0ea5e9';
const UPLOAD_COLOR = '#22c55e';
const SATELLITE_NAMES = { '121RTSDpyNZVcEU84Ticf2L1ntiuUimbWgfATz21tuvgk3vzoA6': 'ap1', '12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S': 'us1', '12L9ZFwhzVpuEKMUNUqkaTLGzwY9G24tbiigLiXpmZWKwmcNDDs': 'eu1', '1wFTAgs9DP5RSnCqKV1eLf6N9wtk4EAtmN5DpSxcs8EjT69tGE': 'saltlake' };

function formatBytes(bytes, decimals = 2) { if (!bytes || bytes === 0) return '0 Bytes'; const k = 1024; const dm = decimals < 0 ? 0 : decimals; const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB']; let i = Math.floor(Math.log(bytes) / Math.log(k)); i = Math.max(0, Math.min(i, sizes.length - 1)); return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i]; }

export function createPerformanceChart(performanceState) {
    if (performanceChartInstance) {
        performanceChartInstance.destroy();
    }
    const ctx = document.getElementById('performanceChart').getContext('2d');
    const isLiveView = performanceState.range === '5m';
    const chartType = isLiveView ? 'line' : 'bar';
    const options = { responsive: true, maintainAspectRatio: false, scales: { x: { type: 'time', time: { tooltipFormat: 'PP pp' } }, y: { beginAtZero: true, title: { display: true } } } };
    if (isLiveView) {
        options.scales.x.time.unit = 'minute';
    } else {
        options.scales.x.stacked = true;
        options.scales.y.stacked = true;
        options.plugins = {
            tooltip: {
                callbacks: {
                    title: function(context) {
                        try {
                            if (!context || context.length === 0) return '';
                            const d = new Date(context[0].parsed.x);
                            const dataIndex = context[0].dataIndex;
                            const dataset = context[0].chart.data.datasets[context[0].datasetIndex];
                            let intervalMs = 0;
                            if (dataIndex + 1 < dataset.data.length) {
                                const nextPointX = dataset.data[dataIndex + 1].x;
                                intervalMs = new Date(nextPointX).getTime() - d.getTime();
                            } else if (dataIndex > 0) {
                                const prevPointX = dataset.data[dataIndex - 1].x;
                                intervalMs = d.getTime() - new Date(prevPointX).getTime();
                            }
                            if (intervalMs > 0) {
                                const next_d = new Date(d.getTime() + intervalMs);
                                const timeFormat = { hour: '2-digit', minute:'2-digit', second: '2-digit' };
                                return `Interval: ${d.toLocaleTimeString([], timeFormat)} - ${next_d.toLocaleTimeString([], timeFormat)}`;
                            }
                            return d.toLocaleString();
                        } catch (e) {
                            console.error("Tooltip title error:", e);
                            return "Error";
                        }
                    }
                }
            }
        };
    }
    performanceChartInstance = new Chart(ctx, { type: chartType, data: { datasets: [] }, options: options });
}

export function updatePerformanceChart(performanceState, data, currentNodeView, availableNodes) {
    if (!performanceChartInstance) return;
    try {
        const isLiveView = performanceState.range === '5m';
        const view = performanceState.view;
        let datasetsToShow = [];
        if (!isLiveView) { // Historical data from 'aggregated_performance_data'
            const historicalData = { rate: [[], []], volume: [[], []], pieces: [[], []], concurrency: [[]] };
            data.forEach(point => { const ts = new Date(point.timestamp); historicalData.rate[0].push({x:ts, y:point.ingress_mbps}); historicalData.rate[1].push({x:ts, y:point.egress_mbps}); historicalData.volume[0].push({x:ts, y:point.ingress_bytes / 1e6}); historicalData.volume[1].push({x:ts, y:point.egress_bytes / 1e6}); historicalData.pieces[0].push({x:ts, y:point.ingress_pieces}); historicalData.pieces[1].push({x:ts, y:point.egress_pieces}); historicalData.concurrency[0].push({x:ts, y:point.concurrency}); });
            const dataToShow = historicalData[view];
            if (view === 'concurrency') { datasetsToShow.push({ label: 'Operations (per sec)', data: dataToShow[0] }); }
            else { const isAvg = performanceState.agg === 'avg' && currentNodeView.length > 1; const nodeCount = isAvg ? currentNodeView.length : 1; datasetsToShow.push({ label: 'Ingress (Upload)', data: dataToShow[0].map(p => ({ x: p.x, y: p.y / nodeCount })) }); datasetsToShow.push({ label: 'Egress (Download)', data: dataToShow[1].map(p => ({ x: p.x, y: p.y / nodeCount })) }); }
        } else { // Live data from 'livePerformanceBins'
            const binsToRender = data[currentNodeView.join(',')] || {};
            const sortedTimestamps = Object.keys(binsToRender).map(Number).sort((a,b) => a - b);
            const sourceData = sortedTimestamps.map(ts => ({ x: new Date(ts), source: binsToRender[ts] }));
            const interval_sec = 2; // PERFORMANCE_INTERVAL_MS / 1000
            const isAvg = performanceState.agg === 'avg' && currentNodeView.length > 1;
            const nodeCount = isAvg ? (currentNodeView[0] === 'Aggregate' ? availableNodes.length : currentNodeView.length) : 1;

            if (view === 'concurrency') { const chartData = sourceData.map(p => ({ x: p.x, y: (p.source.total_ops / interval_sec) / nodeCount })); datasetsToShow.push({ label: 'Operations (per sec)', data: chartData }); }
            else {
                const chartDataIngress = sourceData.map(p => { let y; if (view === 'rate') y = ((p.source.ingress_bytes * 8) / (interval_sec * 1e6)) / nodeCount; else if (view === 'volume') y = (p.source.ingress_bytes / 1e6) / nodeCount; else y = p.source.ingress_pieces / nodeCount; return { x: p.x, y: y }; });
                const chartDataEgress = sourceData.map(p => { let y; if (view === 'rate') y = ((p.source.egress_bytes * 8) / (interval_sec * 1e6)) / nodeCount; else if (view === 'volume') y = (p.source.egress_bytes / 1e6) / nodeCount; else y = p.source.egress_pieces / nodeCount; return { x: p.x, y: y }; });
                datasetsToShow.push({ label: 'Ingress (Upload)', data: chartDataIngress });
                datasetsToShow.push({ label: 'Egress (Download)', data: chartDataEgress });
            }
        }
        const baseStyle = { tension: 0.2 };
        datasetsToShow[0] = {...datasetsToShow[0], ...baseStyle, borderColor: UPLOAD_COLOR, backgroundColor: UPLOAD_COLOR};
        if (datasetsToShow.length > 1) { datasetsToShow[1] = {...datasetsToShow[1], ...baseStyle, borderColor: DOWNLOAD_COLOR, backgroundColor: DOWNLOAD_COLOR}; }
        performanceChartInstance.data.datasets = datasetsToShow;
        performanceChartInstance.update(isLiveView ? 'none' : undefined);
    } catch (error) { console.error("Error in updatePerformanceChart:", error); }
}


export function createSatelliteChart() {
    satelliteChart = new Chart(document.getElementById('satelliteChart').getContext('2d'), { type: 'bar', data: { labels: [], datasets: [{ label: 'Uploads', data: [], backgroundColor: UPLOAD_COLOR }, { label: 'Downloads/Audits', data: [], backgroundColor: DOWNLOAD_COLOR }] }, options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, scales: { x: { stacked: true, title: { display: true, text: 'Pieces' } }, y: { stacked: true } }, plugins: { tooltip: { callbacks: { label: function(context) { let label = context.dataset.label || ''; if (label) { label += ': '; } if (context.parsed.x !== null) { if (satelliteViewIsBySize) { label += formatBytes(context.parsed.x); } else { label += new Intl.NumberFormat().format(context.parsed.x); } } return label; }, footer: function(tooltipItems) { const context = tooltipItems[0]; if (lastSatelliteData.length === 0 || context.dataIndex >= lastSatelliteData.length) return ''; const satData = lastSatelliteData[context.dataIndex]; const lines = []; const totalDl = satData.downloads + satData.audits; if (totalDl > 0) lines.push(`DL Success: ${(satData.dl_success/totalDl*100).toFixed(2)}% (${satData.dl_success}/${totalDl})`); const totalUl = satData.uploads; if (totalUl > 0) lines.push(`UL Success: ${(satData.ul_success/totalUl*100).toFixed(2)}% (${satData.ul_success}/${totalUl})`); return lines; } } } } } });
}
export function updateSatelliteChart(satStats) {
    if (!satelliteChart || !satStats) return;
    lastSatelliteData = satStats;
    const xscale = satelliteChart.options.scales.x;
    satelliteChart.data.labels = satStats.map(s => SATELLITE_NAMES[s.satellite_id] || s.satellite_id.substring(0, 12));
    if (satelliteViewIsBySize) {
        xscale.title.text = 'Data Transferred';
        if (!xscale.ticks) xscale.ticks = {};
        xscale.ticks.callback = (value) => formatBytes(value, 1);
        satelliteChart.data.datasets[0].data = satStats.map(s => s.total_upload_size);
        satelliteChart.data.datasets[1].data = satStats.map(s => s.total_download_size);
        satelliteChart.data.datasets[0].label = 'Upload Size';
        satelliteChart.data.datasets[1].label = 'Download Size';
    } else {
        xscale.title.text = 'Pieces';
        if (xscale.ticks) delete xscale.ticks.callback;
        satelliteChart.data.datasets[0].data = satStats.map(s => s.uploads);
        satelliteChart.data.datasets[1].data = satStats.map(s => s.downloads + s.audits);
        satelliteChart.data.datasets[0].label = 'Uploads';
        satelliteChart.data.datasets[1].label = 'Downloads/Audits';
    }
    satelliteChart.update();
}
export function toggleSatelliteView() { satelliteViewIsBySize = !satelliteViewIsBySize; document.getElementById('toggle-satellite-view').textContent = satelliteViewIsBySize ? 'Show by Pieces' : 'Show by Size'; updateSatelliteChart(lastSatelliteData); }


export function createSizeBarChart() {
    sizeBarChart = new Chart(document.getElementById('sizeBarChart').getContext('2d'), { type: 'bar', data: { labels: [], datasets: [{ label: 'Successful Downloads', data: [], backgroundColor: DOWNLOAD_COLOR }, { label: 'Successful Uploads', data: [], backgroundColor: UPLOAD_COLOR }, { label: 'Failed Downloads', data: [], backgroundColor: '#ef4444' }, { label: 'Failed Uploads', data: [], backgroundColor: '#f97316' }] }, options: { responsive: true, maintainAspectRatio: false, scales: { x: { title: { display: true, text: 'Size Bucket' }, stacked: true }, y: { title: { display: true, text: 'Count' }, stacked: true } }, plugins: { legend: { position: 'top' }, tooltip: { callbacks: { footer: function(tooltipItems) { const item = tooltipItems[0]; const datasetLabels = ['successful downloads', 'successful uploads', 'failed downloads', 'failed uploads']; return `${item.parsed.y} ${datasetLabels[item.datasetIndex]} in this size range`; } } } } } });
    sizeBarChart.currentViewMode = 'counts';
}
export function setSizeChartViewMode(mode) { sizeChartViewMode = mode; }
export function updateSizeBarChart(transferSizes) {
    if (!sizeBarChart || !transferSizes) return;
    lastTransferSizes = transferSizes;
    const allBuckets = ["< 1 KB", "1-4 KB", "4-16 KB", "16-64 KB", "64-256 KB", "256 KB - 1 MB", "> 1 MB"];
    sizeBarChart.data.labels = allBuckets;
    const processedData = allBuckets.map(bucketName => { const bucket = transferSizes.find(b => b.bucket === bucketName) || {}; return { dl_s: bucket.downloads_success || 0, dl_f: bucket.downloads_failed || 0, ul_s: bucket.uploads_success || 0, ul_f: bucket.uploads_failed || 0, }; });
    if (sizeChartViewMode !== sizeBarChart.currentViewMode) {
        switch (sizeChartViewMode) {
            case 'counts': Object.assign(sizeBarChart.options.scales.x, { stacked: true }); Object.assign(sizeBarChart.options.scales.y, { stacked: true, title: { text: 'Count' }, min: undefined, max: undefined }); sizeBarChart.data.datasets = [{ label: 'Successful Downloads', data: [], backgroundColor: DOWNLOAD_COLOR }, { label: 'Successful Uploads', data: [], backgroundColor: UPLOAD_COLOR }, { label: 'Failed Downloads', data: [], backgroundColor: '#ef4444' }, { label: 'Failed Uploads', data: [], backgroundColor: '#f97316' }]; sizeBarChart.options.plugins.tooltip.callbacks.footer = (items) => `${items[0].parsed.y} transfers`; break;
            case 'percentages': Object.assign(sizeBarChart.options.scales.x, { stacked: true }); Object.assign(sizeBarChart.options.scales.y, { stacked: true, title: { text: 'Percentage (%)' }, min: 0, max: 100 }); sizeBarChart.data.datasets = [{ label: 'Successful Downloads', data: [], backgroundColor: DOWNLOAD_COLOR }, { label: 'Successful Uploads', data: [], backgroundColor: UPLOAD_COLOR }, { label: 'Failed Downloads', data: [], backgroundColor: '#ef4444' }, { label: 'Failed Uploads', data: [], backgroundColor: '#f97316' }]; sizeBarChart.options.plugins.tooltip.callbacks.footer = (items) => { const item = items[0]; const total = processedData.reduce((sum, d) => sum + d.dl_s + d.dl_f + d.ul_s + d.ul_f, 0); const bucketData = processedData[item.dataIndex]; const counts = [bucketData.dl_s, bucketData.ul_s, bucketData.dl_f, bucketData.ul_f]; return `${item.parsed.y.toFixed(2)}% (${counts[item.datasetIndex]} transfers)`; }; break;
            case 'rates': Object.assign(sizeBarChart.options.scales.x, { stacked: false }); Object.assign(sizeBarChart.options.scales.y, { stacked: false, title: { text: 'Success Rate (%)' }, max: 100 }); sizeBarChart.data.datasets = [{ label: 'Download Success Rate', data: [], backgroundColor: DOWNLOAD_COLOR }, { label: 'Upload Success Rate', data: [], backgroundColor: UPLOAD_COLOR }]; sizeBarChart.options.plugins.tooltip.callbacks.footer = (items) => { const item = items[0]; const data = processedData[item.dataIndex]; return item.datasetIndex === 0 ? `Raw: ${data.dl_s}/${data.dl_s + data.dl_f}` : `Raw: ${data.ul_s}/${data.ul_s + data.ul_f}`; }; break;
        }
        sizeBarChart.currentViewMode = sizeChartViewMode;
    }
    switch (sizeChartViewMode) {
        case 'counts': sizeBarChart.data.datasets[0].data = processedData.map(d => d.dl_s); sizeBarChart.data.datasets[1].data = processedData.map(d => d.ul_s); sizeBarChart.data.datasets[2].data = processedData.map(d => d.dl_f); sizeBarChart.data.datasets[3].data = processedData.map(d => d.ul_f); break;
        case 'percentages': const total = processedData.reduce((sum, d) => sum + d.dl_s + d.dl_f + d.ul_s + d.ul_f, 0); if (total > 0) { sizeBarChart.data.datasets[0].data = processedData.map(d => d.dl_s / total * 100); sizeBarChart.data.datasets[1].data = processedData.map(d => d.ul_s / total * 100); sizeBarChart.data.datasets[2].data = processedData.map(d => d.dl_f / total * 100); sizeBarChart.data.datasets[3].data = processedData.map(d => d.ul_f / total * 100); } break;
        case 'rates': const dlRates = processedData.map(d => { const total = d.dl_s + d.dl_f; return total > 0 ? (d.dl_s / total * 100) : 0; }); const ulRates = processedData.map(d => { const total = d.ul_s + d.ul_f; return total > 0 ? (d.ul_s / total * 100) : 0; }); const minRate = Math.min(...dlRates.filter(r => r > 0), ...ulRates.filter(r => r > 0), 95); sizeBarChart.options.scales.y.min = Math.floor(Math.min(95, minRate < 95 ? minRate - 1 : 95)); sizeBarChart.data.datasets[0].data = dlRates; sizeBarChart.data.datasets[1].data = ulRates; break;
    }
    sizeBarChart.update();
}

export function createHashstoreChart() {
    if (hashstoreChartInstance) hashstoreChartInstance.destroy();
    const ctx = document.getElementById('hashstoreChart').getContext('2d');
    hashstoreChartInstance = new Chart(ctx, {
        type: 'bar', data: { labels: [], datasets: [] },
        options: {
            responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
            scales: { x: { title: { display: true, text: 'Compaction Date' } }, 'y-data': { type: 'linear', position: 'left', title: { display: true, text: 'Data Size' }, stacked: true, ticks: { callback: (value) => formatBytes(value) } }, 'y-duration': { type: 'linear', position: 'right', title: { display: true, text: 'Duration (s)' }, grid: { drawOnChartArea: false } } },
            plugins: {
                tooltip: {
                    callbacks: {
                        beforeBody: (tooltipItems) => { const item = tooltipItems[0]; if (!item) return []; const dataPoint = item.chart.sortedData?.[item.dataIndex]; if (!dataPoint) return []; const satelliteName = SATELLITE_NAMES[dataPoint.satellite] || dataPoint.satellite.substring(0, 12); return [`Node: ${dataPoint.node_name}`, `Satellite: ${satelliteName}`, `Store: ${dataPoint.store}`, '']; },
                        label: (context) => { let label = context.dataset.label || ''; if (label) { label += ': '; } if (context.parsed.y !== null) { label += context.dataset.yAxisID === 'y-data' ? formatBytes(context.parsed.y) : `${context.parsed.y.toFixed(1)}s`; } return label; }
                    }
                }
            }
        }
    });
}
export function updateHashstoreChart(data) {
    if (!hashstoreChartInstance) createHashstoreChart();
    const sortedData = [...data].sort((a, b) => new Date(a.last_run_iso) - new Date(b.last_run_iso));
    hashstoreChartInstance.sortedData = sortedData;
    hashstoreChartInstance.data.labels = sortedData.map(item => new Date(item.last_run_iso).toLocaleString());
    hashstoreChartInstance.data.datasets = [
        { type: 'line', label: 'Duration', data: sortedData.map(item => item.duration), borderColor: '#f59e0b', backgroundColor: '#f59e0b', yAxisID: 'y-duration', tension: 0.2 },
        { type: 'bar', label: 'Data Reclaimed', data: sortedData.map(item => item.data_reclaimed_bytes), backgroundColor: UPLOAD_COLOR, yAxisID: 'y-data', stack: 'data' },
        { type: 'bar', label: 'Data Rewritten', data: sortedData.map(item => item.data_rewritten_bytes), backgroundColor: DOWNLOAD_COLOR, yAxisID: 'y-data', stack: 'data' }
    ];
    hashstoreChartInstance.update('none');
}

// --- Phase 3: Enhanced Monitoring Charts ---

let storageHistoryChartInstance;
let latencyHistogramChartInstance;

export function createStorageHistoryChart() {
    if (storageHistoryChartInstance) storageHistoryChartInstance.destroy();
    const ctx = document.getElementById('storageHistoryChart').getContext('2d');
    storageHistoryChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [
                {
                    label: 'Used Space',
                    data: [],
                    borderColor: '#0ea5e9',
                    backgroundColor: 'rgba(14, 165, 233, 0.1)',
                    fill: true,
                    tension: 0.3
                },
                {
                    label: 'Trash Space',
                    data: [],
                    borderColor: '#f59e0b',
                    backgroundColor: 'rgba(245, 158, 11, 0.1)',
                    fill: true,
                    tension: 0.3
                },
                {
                    label: 'Available Space',
                    data: [],
                    borderColor: '#22c55e',
                    backgroundColor: 'rgba(34, 197, 94, 0.1)',
                    fill: true,
                    tension: 0.3
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    type: 'time',
                    time: {
                        unit: 'day',
                        tooltipFormat: 'PP'
                    },
                    title: {
                        display: true,
                        text: 'Date'
                    }
                },
                y: {
                    beginAtZero: true,
                    title: {
                        display: true,
                        text: 'Storage'
                    },
                    ticks: {
                        callback: (value) => formatBytes(value, 0)
                    }
                }
            },
            plugins: {
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            let label = context.dataset.label || '';
                            if (label) label += ': ';
                            if (context.parsed.y !== null) {
                                label += formatBytes(context.parsed.y);
                            }
                            return label;
                        }
                    }
                }
            }
        }
    });
}

export function updateStorageHistoryChart(historyData) {
    if (!storageHistoryChartInstance) createStorageHistoryChart();
    if (!historyData || historyData.length === 0) return;
    
    // Check if we have API data (used_bytes) or log data (available_bytes)
    const hasUsedData = historyData.some(item => item.used_bytes != null);
    const hasAvailableData = historyData.some(item => item.available_bytes != null);
    
    const usedData = historyData
        .filter(item => item.used_bytes != null)
        .map(item => ({
            x: new Date(item.timestamp),
            y: item.used_bytes
        }));
    
    const trashData = historyData
        .filter(item => item.trash_bytes != null)
        .map(item => ({
            x: new Date(item.timestamp),
            y: item.trash_bytes
        }));
    
    const availableData = historyData
        .filter(item => item.available_bytes != null)
        .map(item => ({
            x: new Date(item.timestamp),
            y: item.available_bytes
        }));
    
    storageHistoryChartInstance.data.datasets[0].data = usedData;
    storageHistoryChartInstance.data.datasets[1].data = trashData;
    storageHistoryChartInstance.data.datasets[2].data = availableData;
    
    // Hide datasets that have no data
    storageHistoryChartInstance.data.datasets[0].hidden = !hasUsedData;
    storageHistoryChartInstance.data.datasets[1].hidden = !hasUsedData;
    storageHistoryChartInstance.data.datasets[2].hidden = !hasAvailableData;
    
    storageHistoryChartInstance.update();
}

export function createLatencyHistogramChart() {
    if (latencyHistogramChartInstance) latencyHistogramChartInstance.destroy();
    const ctx = document.getElementById('latencyHistogramChart').getContext('2d');
    latencyHistogramChartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: [],
            datasets: [{
                label: 'Operations',
                data: [],
                backgroundColor: '#0ea5e9'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    title: {
                        display: true,
                        text: 'Latency Range (ms)'
                    }
                },
                y: {
                    beginAtZero: true,
                    title: {
                        display: true,
                        text: 'Number of Operations'
                    }
                }
            },
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            return `${context.parsed.y} operations`;
                        }
                    }
                }
            }
        }
    });
}

export function updateLatencyHistogramChart(histogramData) {
    if (!latencyHistogramChartInstance) createLatencyHistogramChart();
    if (!histogramData || histogramData.length === 0) return;
    
    // histogramData is an array of buckets from backend
    const labels = histogramData.map(bucket => bucket.label || `${bucket.bucket_start_ms}-${bucket.bucket_end_ms}ms`);
    const data = histogramData.map(bucket => bucket.count);
    
    latencyHistogramChartInstance.data.labels = labels;
    latencyHistogramChartInstance.data.datasets[0].data = data;
    latencyHistogramChartInstance.update();
}
