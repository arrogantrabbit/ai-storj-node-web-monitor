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
            // Ensure data is an array before processing
            if (!Array.isArray(data)) {
                console.error('updatePerformanceChart: Expected data to be an array, got:', typeof data);
                return;
            }
            const historicalData = { rate: [[], []], volume: [[], []], pieces: [[], []], concurrency: [[]] };
            data.forEach(point => { const ts = new Date(point.timestamp); historicalData.rate[0].push({x:ts, y:point.ingress_mbps}); historicalData.rate[1].push({x:ts, y:point.egress_mbps}); historicalData.volume[0].push({x:ts, y:point.ingress_bytes / 1e6}); historicalData.volume[1].push({x:ts, y:point.egress_bytes / 1e6}); historicalData.pieces[0].push({x:ts, y:point.ingress_pieces}); historicalData.pieces[1].push({x:ts, y:point.egress_pieces}); historicalData.concurrency[0].push({x:ts, y:point.concurrency}); });
            const dataToShow = historicalData[view];
            const isAvg = performanceState.agg === 'avg' && (currentNodeView.length > 1 || currentNodeView[0] === 'Aggregate');
            const nodeCount = isAvg ? (currentNodeView[0] === 'Aggregate' ? availableNodes.length : currentNodeView.length) : 1;
            if (view === 'concurrency') { datasetsToShow.push({ label: 'Operations (per sec)', data: dataToShow[0].map(p => ({ x: p.x, y: p.y / nodeCount })) }); }
            else { datasetsToShow.push({ label: 'Ingress (Upload)', data: dataToShow[0].map(p => ({ x: p.x, y: p.y / nodeCount })) }); datasetsToShow.push({ label: 'Egress (Download)', data: dataToShow[1].map(p => ({ x: p.x, y: p.y / nodeCount })) }); }
        } else { // Live data from 'livePerformanceBins'
            const binsToRender = data[currentNodeView.join(',')] || {};
            const sortedTimestamps = Object.keys(binsToRender).map(Number).sort((a,b) => a - b);
            const sourceData = sortedTimestamps.map(ts => ({ x: new Date(ts), source: binsToRender[ts] }));
            const interval_sec = 2; // PERFORMANCE_INTERVAL_MS / 1000
            const isAvg = performanceState.agg === 'avg' && (currentNodeView.length > 1 || currentNodeView[0] === 'Aggregate');
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
    satelliteChart = new Chart(document.getElementById('satelliteChart').getContext('2d'), { type: 'bar', data: { labels: [], datasets: [
        { label: 'Upload', data: [], backgroundColor: UPLOAD_COLOR },
        { label: 'Repair Upload', data: [], backgroundColor: '#8b5cf6' },
        { label: 'Download', data: [], backgroundColor: DOWNLOAD_COLOR },
        { label: 'Repair Download', data: [], backgroundColor: '#f59e0b' }
    ] }, options: { responsive: true, maintainAspectRatio: false, scales: { x: { title: { display: true, text: 'Satellite' } }, y: { title: { display: true, text: 'Pieces' } } }, plugins: { tooltip: { callbacks: { label: function(context) { let label = context.dataset.label || ''; if (label) { label += ': '; } if (context.parsed.y !== null) { if (satelliteViewIsBySize) { label += formatBytes(context.parsed.y); } else { label += new Intl.NumberFormat().format(context.parsed.y); } } return label; }, footer: function(tooltipItems) { const context = tooltipItems[0]; if (lastSatelliteData.length === 0 || context.dataIndex >= lastSatelliteData.length) return ''; const satData = lastSatelliteData[context.dataIndex]; const lines = []; const totalUl = satData.uploads; if (totalUl > 0) lines.push(`Upload Success: ${(satData.ul_success/totalUl*100).toFixed(2)}% (${satData.ul_success}/${totalUl})`); const totalPutRepair = satData.put_repair || 0; if (totalPutRepair > 0) lines.push(`Repair Upload Success: ${(satData.put_repair_success/totalPutRepair*100).toFixed(2)}% (${satData.put_repair_success}/${totalPutRepair})`); const totalDl = satData.downloads + satData.audits; if (totalDl > 0) lines.push(`Download Success: ${(satData.dl_success/totalDl*100).toFixed(2)}% (${satData.dl_success}/${totalDl})`); const totalGetRepair = satData.get_repair || 0; if (totalGetRepair > 0) lines.push(`Repair Download Success: ${(satData.get_repair_success/totalGetRepair*100).toFixed(2)}% (${satData.get_repair_success}/${totalGetRepair})`); return lines; } } } } } });
}
export function updateSatelliteChart(satStats) {
    if (!satelliteChart || !satStats) return;
    lastSatelliteData = satStats;
    const yscale = satelliteChart.options.scales.y;
    satelliteChart.data.labels = satStats.map(s => SATELLITE_NAMES[s.satellite_id] || s.satellite_id.substring(0, 12));
    if (satelliteViewIsBySize) {
        yscale.title.text = 'Data Transferred';
        if (!yscale.ticks) yscale.ticks = {};
        yscale.ticks.callback = (value) => formatBytes(value, 1);
        satelliteChart.data.datasets[0].data = satStats.map(s => s.total_upload_size);
        satelliteChart.data.datasets[1].data = satStats.map(s => s.total_put_repair_size || 0);
        satelliteChart.data.datasets[2].data = satStats.map(s => s.total_download_size);
        satelliteChart.data.datasets[3].data = satStats.map(s => s.total_get_repair_size || 0);
        satelliteChart.data.datasets[0].label = 'Upload';
        satelliteChart.data.datasets[1].label = 'Repair Upload';
        satelliteChart.data.datasets[2].label = 'Download';
        satelliteChart.data.datasets[3].label = 'Repair Download';
    } else {
        yscale.title.text = 'Pieces';
        if (yscale.ticks) delete yscale.ticks.callback;
        satelliteChart.data.datasets[0].data = satStats.map(s => s.uploads);
        satelliteChart.data.datasets[1].data = satStats.map(s => s.put_repair || 0);
        satelliteChart.data.datasets[2].data = satStats.map(s => s.downloads + s.audits);
        satelliteChart.data.datasets[3].data = satStats.map(s => s.get_repair || 0);
        satelliteChart.data.datasets[0].label = 'Upload';
        satelliteChart.data.datasets[1].label = 'Repair Upload';
        satelliteChart.data.datasets[2].label = 'Download';
        satelliteChart.data.datasets[3].label = 'Repair Download';
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
    const processedData = allBuckets.map(bucketName => {
        const bucket = transferSizes.find(b => b.bucket === bucketName) || {};
        return {
            dl_s: bucket.downloads_success || 0,
            dl_f: bucket.downloads_failed || 0,
            ul_s: bucket.uploads_success || 0,
            ul_f: bucket.uploads_failed || 0,
            dl_s_size: bucket.downloads_success_size || 0,
            dl_f_size: bucket.downloads_failed_size || 0,
            ul_s_size: bucket.uploads_success_size || 0,
            ul_f_size: bucket.uploads_failed_size || 0
        };
    });
    if (sizeChartViewMode !== sizeBarChart.currentViewMode) {
        switch (sizeChartViewMode) {
            case 'counts':
                Object.assign(sizeBarChart.options.scales.x, { stacked: true });
                Object.assign(sizeBarChart.options.scales.y, { stacked: true, title: { text: 'Count' }, min: undefined, max: undefined, ticks: {} });
                delete sizeBarChart.options.scales.y.ticks.callback;
                sizeBarChart.data.datasets = [
                    { label: 'Successful Downloads', data: [], backgroundColor: DOWNLOAD_COLOR },
                    { label: 'Successful Uploads', data: [], backgroundColor: UPLOAD_COLOR },
                    { label: 'Failed Downloads', data: [], backgroundColor: '#ef4444' },
                    { label: 'Failed Uploads', data: [], backgroundColor: '#f97316' }
                ];
                sizeBarChart.options.plugins.tooltip.callbacks.footer = (items) => `${items[0].parsed.y} transfers`;
                break;
            case 'percentages':
                Object.assign(sizeBarChart.options.scales.x, { stacked: true });
                Object.assign(sizeBarChart.options.scales.y, { stacked: true, title: { text: 'Percentage by Count (%)' }, min: 0, max: 100, ticks: {} });
                delete sizeBarChart.options.scales.y.ticks.callback;
                sizeBarChart.data.datasets = [
                    { label: 'Successful Downloads', data: [], backgroundColor: DOWNLOAD_COLOR },
                    { label: 'Successful Uploads', data: [], backgroundColor: UPLOAD_COLOR },
                    { label: 'Failed Downloads', data: [], backgroundColor: '#ef4444' },
                    { label: 'Failed Uploads', data: [], backgroundColor: '#f97316' }
                ];
                sizeBarChart.options.plugins.tooltip.callbacks.footer = (items) => {
                    const item = items[0];
                    const total = processedData.reduce((sum, d) => sum + d.dl_s + d.dl_f + d.ul_s + d.ul_f, 0);
                    const bucketData = processedData[item.dataIndex];
                    const counts = [bucketData.dl_s, bucketData.ul_s, bucketData.dl_f, bucketData.ul_f];
                    return `${item.parsed.y.toFixed(2)}% (${counts[item.datasetIndex]} transfers)`;
                };
                break;
            case 'percentages-size':
                Object.assign(sizeBarChart.options.scales.x, { stacked: true });
                Object.assign(sizeBarChart.options.scales.y, { stacked: true, title: { text: 'Percentage by Size (%)' }, min: 0, max: 100, ticks: {} });
                delete sizeBarChart.options.scales.y.ticks.callback;
                sizeBarChart.data.datasets = [
                    { label: 'Successful Downloads', data: [], backgroundColor: DOWNLOAD_COLOR },
                    { label: 'Successful Uploads', data: [], backgroundColor: UPLOAD_COLOR },
                    { label: 'Failed Downloads', data: [], backgroundColor: '#ef4444' },
                    { label: 'Failed Uploads', data: [], backgroundColor: '#f97316' }
                ];
                sizeBarChart.options.plugins.tooltip.callbacks.footer = (items) => {
                    const item = items[0];
                    const totalSize = processedData.reduce((sum, d) => sum + d.dl_s_size + d.dl_f_size + d.ul_s_size + d.ul_f_size, 0);
                    const bucketData = processedData[item.dataIndex];
                    const sizes = [bucketData.dl_s_size, bucketData.ul_s_size, bucketData.dl_f_size, bucketData.ul_f_size];
                    return `${item.parsed.y.toFixed(2)}% (${formatBytes(sizes[item.datasetIndex])})`;
                };
                break;
            case 'rates':
                Object.assign(sizeBarChart.options.scales.x, { stacked: false });
                Object.assign(sizeBarChart.options.scales.y, { stacked: false, title: { text: 'Success Rate (%)' }, max: 100, ticks: {} });
                delete sizeBarChart.options.scales.y.ticks.callback;
                sizeBarChart.data.datasets = [
                    { label: 'Download Success Rate', data: [], backgroundColor: DOWNLOAD_COLOR },
                    { label: 'Upload Success Rate', data: [], backgroundColor: UPLOAD_COLOR }
                ];
                sizeBarChart.options.plugins.tooltip.callbacks.footer = (items) => {
                    const item = items[0];
                    const data = processedData[item.dataIndex];
                    return item.datasetIndex === 0 ? `Raw: ${data.dl_s}/${data.dl_s + data.dl_f}` : `Raw: ${data.ul_s}/${data.ul_s + data.ul_f}`;
                };
                break;
            case 'sizes':
                Object.assign(sizeBarChart.options.scales.x, { stacked: true });
                Object.assign(sizeBarChart.options.scales.y, { stacked: true, title: { text: 'Data Size' }, min: undefined, max: undefined });
                if (!sizeBarChart.options.scales.y.ticks) sizeBarChart.options.scales.y.ticks = {};
                sizeBarChart.options.scales.y.ticks.callback = (value) => formatBytes(value, 1);
                sizeBarChart.data.datasets = [
                    { label: 'Successful Downloads', data: [], backgroundColor: DOWNLOAD_COLOR },
                    { label: 'Successful Uploads', data: [], backgroundColor: UPLOAD_COLOR },
                    { label: 'Failed Downloads', data: [], backgroundColor: '#ef4444' },
                    { label: 'Failed Uploads', data: [], backgroundColor: '#f97316' }
                ];
                sizeBarChart.options.plugins.tooltip.callbacks.footer = (items) => {
                    const item = items[0];
                    const bucketData = processedData[item.dataIndex];
                    const sizes = [bucketData.dl_s_size, bucketData.ul_s_size, bucketData.dl_f_size, bucketData.ul_f_size];
                    return `${formatBytes(item.parsed.y)} (${formatBytes(sizes[item.datasetIndex])} in this category)`;
                };
                break;
        }
        sizeBarChart.currentViewMode = sizeChartViewMode;
    }
    switch (sizeChartViewMode) {
        case 'counts':
            sizeBarChart.data.datasets[0].data = processedData.map(d => d.dl_s);
            sizeBarChart.data.datasets[1].data = processedData.map(d => d.ul_s);
            sizeBarChart.data.datasets[2].data = processedData.map(d => d.dl_f);
            sizeBarChart.data.datasets[3].data = processedData.map(d => d.ul_f);
            break;
        case 'percentages':
            const total = processedData.reduce((sum, d) => sum + d.dl_s + d.dl_f + d.ul_s + d.ul_f, 0);
            if (total > 0) {
                sizeBarChart.data.datasets[0].data = processedData.map(d => d.dl_s / total * 100);
                sizeBarChart.data.datasets[1].data = processedData.map(d => d.ul_s / total * 100);
                sizeBarChart.data.datasets[2].data = processedData.map(d => d.dl_f / total * 100);
                sizeBarChart.data.datasets[3].data = processedData.map(d => d.ul_f / total * 100);
            }
            break;
        case 'percentages-size':
            const totalSize = processedData.reduce((sum, d) => sum + d.dl_s_size + d.dl_f_size + d.ul_s_size + d.ul_f_size, 0);
            if (totalSize > 0) {
                sizeBarChart.data.datasets[0].data = processedData.map(d => d.dl_s_size / totalSize * 100);
                sizeBarChart.data.datasets[1].data = processedData.map(d => d.ul_s_size / totalSize * 100);
                sizeBarChart.data.datasets[2].data = processedData.map(d => d.dl_f_size / totalSize * 100);
                sizeBarChart.data.datasets[3].data = processedData.map(d => d.ul_f_size / totalSize * 100);
            }
            break;
        case 'rates':
            const dlRates = processedData.map(d => { const total = d.dl_s + d.dl_f; return total > 0 ? (d.dl_s / total * 100) : 0; });
            const ulRates = processedData.map(d => { const total = d.ul_s + d.ul_f; return total > 0 ? (d.ul_s / total * 100) : 0; });
            const minRate = Math.min(...dlRates.filter(r => r > 0), ...ulRates.filter(r => r > 0), 95);
            sizeBarChart.options.scales.y.min = Math.floor(Math.min(95, minRate < 95 ? minRate - 1 : 95));
            sizeBarChart.data.datasets[0].data = dlRates;
            sizeBarChart.data.datasets[1].data = ulRates;
            break;
        case 'sizes':
            sizeBarChart.data.datasets[0].data = processedData.map(d => d.dl_s_size);
            sizeBarChart.data.datasets[1].data = processedData.map(d => d.ul_s_size);
            sizeBarChart.data.datasets[2].data = processedData.map(d => d.dl_f_size);
            sizeBarChart.data.datasets[3].data = processedData.map(d => d.ul_f_size);
            break;
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
                        unit: 'hour',
                        displayFormats: {
                            hour: 'MMM d, HH:mm',
                            day: 'MMM d'
                        },
                        tooltipFormat: 'PP pp'
                    },
                    title: {
                        display: true,
                        text: 'Date'
                    },
                    ticks: {
                        autoSkip: true,
                        maxTicksLimit: 10,
                        maxRotation: 45,
                        minRotation: 0
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

// Store multiple node histories for aggregation
let storageHistoryByNode = {};

export function clearStorageHistoryCache() {
    storageHistoryByNode = {};
}

export function updateStorageHistoryChart(historyData) {
    if (!storageHistoryChartInstance) createStorageHistoryChart();
    if (!historyData || historyData.length === 0) return;
    
    // Store this node's history
    if (historyData.length > 0) {
        const nodeName = historyData[0].node_name;
        storageHistoryByNode[nodeName] = historyData;
    }
    
    // Determine which nodes to display based on current view
    let nodesToDisplay = [];
    if (window.currentView && window.currentView.length === 1 && window.currentView[0] !== 'Aggregate') {
        // Single node view
        nodesToDisplay = [window.currentView[0]];
    } else {
        // Aggregate or multi-node view - show all stored histories
        nodesToDisplay = Object.keys(storageHistoryByNode);
    }
    
    // Aggregate data across selected nodes by timestamp
    // FIXED: Carry forward last known values for each node to prevent rollercoaster effect
    const BUCKET_SIZE_MS = 5 * 60 * 1000; // 5 minutes
    
    // Step 1: Collect all raw data points per node
    const nodeDataPoints = {};
    nodesToDisplay.forEach(nodeName => {
        const nodeHistory = storageHistoryByNode[nodeName];
        if (!nodeHistory) return;
        
        nodeDataPoints[nodeName] = nodeHistory.map(item => ({
            timestamp: new Date(item.timestamp).getTime(),
            used_bytes: item.used_bytes,
            trash_bytes: item.trash_bytes,
            available_bytes: item.available_bytes
        })).sort((a, b) => a.timestamp - b.timestamp);
    });
    
    // Step 2: Determine time range and create all buckets
    const allTimestamps = [];
    Object.values(nodeDataPoints).forEach(points => {
        points.forEach(p => allTimestamps.push(p.timestamp));
    });
    
    if (allTimestamps.length === 0) return;
    
    const minTimestamp = Math.min(...allTimestamps);
    const maxTimestamp = Math.max(...allTimestamps);
    const minBucket = Math.floor(minTimestamp / BUCKET_SIZE_MS) * BUCKET_SIZE_MS;
    const maxBucket = Math.floor(maxTimestamp / BUCKET_SIZE_MS) * BUCKET_SIZE_MS;
    
    // Step 3: For each node, carry forward last known values across all buckets
    const aggregatedData = {};
    
    for (let bucketTime = minBucket; bucketTime <= maxBucket; bucketTime += BUCKET_SIZE_MS) {
        aggregatedData[bucketTime] = {
            used_bytes: 0,
            trash_bytes: 0,
            available_bytes: 0,
            hasUsedData: false,
            hasAvailableData: false,
            nodeCount: 0
        };
    }
    
    // Step 4: For each node, fill in values with carry-forward
    nodesToDisplay.forEach(nodeName => {
        const points = nodeDataPoints[nodeName];
        if (!points || points.length === 0) return;
        
        let lastKnownValues = null;
        let pointIndex = 0;
        
        for (let bucketTime = minBucket; bucketTime <= maxBucket; bucketTime += BUCKET_SIZE_MS) {
            // Update lastKnownValues if we have a point in or before this bucket
            while (pointIndex < points.length && points[pointIndex].timestamp <= bucketTime + BUCKET_SIZE_MS) {
                lastKnownValues = points[pointIndex];
                pointIndex++;
            }
            
            // If we have data for this node (either in this bucket or carried forward), add it
            if (lastKnownValues && lastKnownValues.timestamp <= bucketTime + BUCKET_SIZE_MS) {
                const bucket = aggregatedData[bucketTime];
                
                if (lastKnownValues.used_bytes != null) {
                    bucket.used_bytes += lastKnownValues.used_bytes;
                    bucket.hasUsedData = true;
                }
                if (lastKnownValues.trash_bytes != null) {
                    bucket.trash_bytes += lastKnownValues.trash_bytes;
                }
                if (lastKnownValues.available_bytes != null) {
                    bucket.available_bytes += lastKnownValues.available_bytes;
                    bucket.hasAvailableData = true;
                }
                bucket.nodeCount++;
            }
        }
    });
    
    // Convert aggregated data to chart format
    const sortedTimestamps = Object.keys(aggregatedData).map(Number).sort((a, b) => a - b);
    
    const usedData = [];
    const trashData = [];
    const availableData = [];
    let hasUsedData = false;
    let hasAvailableData = false;
    
    sortedTimestamps.forEach(timestamp => {
        const bucket = aggregatedData[timestamp];
        
        // Only include buckets where we have data from at least one node
        if (bucket.nodeCount > 0) {
            if (bucket.hasUsedData) {
                usedData.push({ x: new Date(timestamp), y: bucket.used_bytes });
                trashData.push({ x: new Date(timestamp), y: bucket.trash_bytes });
                hasUsedData = true;
            }
            if (bucket.hasAvailableData) {
                availableData.push({ x: new Date(timestamp), y: bucket.available_bytes });
                hasAvailableData = true;
            }
        }
    });
    
    storageHistoryChartInstance.data.datasets[0].data = usedData;
    storageHistoryChartInstance.data.datasets[1].data = trashData;
    storageHistoryChartInstance.data.datasets[2].data = availableData;
    
    // Hide datasets that have no data
    storageHistoryChartInstance.data.datasets[0].hidden = !hasUsedData;
    storageHistoryChartInstance.data.datasets[1].hidden = !hasUsedData;
    storageHistoryChartInstance.data.datasets[2].hidden = !hasAvailableData;
    
    // HYBRID APPROACH: Adaptive axis behavior based on range selection
    if (sortedTimestamps.length > 0) {
        const firstTimestamp = sortedTimestamps[0];
        const lastTimestamp = sortedTimestamps[sortedTimestamps.length - 1];
        const actualRangeMs = lastTimestamp - firstTimestamp;
        const actualRangeDays = actualRangeMs / (1000 * 60 * 60 * 24);
        
        // Get the user's selected range from global state
        const selectedRange = window.storageState?.range || '7d';
        const selectedRangeDays = {
            '1d': 1, '3d': 3, '7d': 7, '14d': 14, '30d': 30
        }[selectedRange] || 7;
        
        // HYBRID STRATEGY:
        // - For 1d: Zoom in on data (better detail)
        // - For 3d+: Show full range (better context)
        const shouldZoomIn = selectedRangeDays <= 1 && actualRangeDays < 0.1;
        
        if (shouldZoomIn) {
            // SHORT RANGE MODE: Zoom in on sparse data for detail
            const centerTime = (firstTimestamp + lastTimestamp) / 2;
            const threeHours = 3 * 60 * 60 * 1000;
            storageHistoryChartInstance.options.scales.x.min = new Date(centerTime - threeHours);
            storageHistoryChartInstance.options.scales.x.max = new Date(centerTime + threeHours);
            storageHistoryChartInstance.options.scales.x.time.unit = 'hour';
            storageHistoryChartInstance.options.scales.x.ticks.stepSize = 1;
        } else if (actualRangeDays < selectedRangeDays * 0.1) {
            // LONG RANGE MODE: Show full selected range for context
            const now = Date.now();
            const rangeMs = selectedRangeDays * 24 * 60 * 60 * 1000;
            storageHistoryChartInstance.options.scales.x.min = new Date(now - rangeMs);
            storageHistoryChartInstance.options.scales.x.max = new Date(now);
            
            // Set appropriate time unit based on selected range
            if (selectedRangeDays <= 3) {
                storageHistoryChartInstance.options.scales.x.time.unit = 'day';
                storageHistoryChartInstance.options.scales.x.ticks.stepSize = 1;
            } else if (selectedRangeDays <= 14) {
                storageHistoryChartInstance.options.scales.x.time.unit = 'day';
                storageHistoryChartInstance.options.scales.x.ticks.stepSize = 2;
            } else {
                storageHistoryChartInstance.options.scales.x.time.unit = 'day';
                storageHistoryChartInstance.options.scales.x.ticks.stepSize = Math.ceil(selectedRangeDays / 7);
            }
        } else {
            // AUTO MODE: Data spans enough of the selected range
            storageHistoryChartInstance.options.scales.x.min = undefined;
            storageHistoryChartInstance.options.scales.x.max = undefined;
            storageHistoryChartInstance.options.scales.x.ticks.stepSize = undefined;
            
            // Adjust time unit based on actual data range
            if (actualRangeDays < 1) {
                storageHistoryChartInstance.options.scales.x.time.unit = 'hour';
            } else if (actualRangeDays < 7) {
                storageHistoryChartInstance.options.scales.x.time.unit = 'day';
            } else {
                storageHistoryChartInstance.options.scales.x.time.unit = 'day';
            }
        }
    }
    
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
    
    if (!histogramData || histogramData.length === 0) {
        // Clear the chart when there's no data
        latencyHistogramChartInstance.data.labels = [];
        latencyHistogramChartInstance.data.datasets[0].data = [];
        latencyHistogramChartInstance.update();
        return;
    }
    
    // histogramData is an array of buckets from backend
    const labels = histogramData.map(bucket => bucket.label || `${bucket.bucket_start_ms}-${bucket.bucket_end_ms}ms`);
    const data = histogramData.map(bucket => bucket.count);
    
    latencyHistogramChartInstance.data.labels = labels;
    latencyHistogramChartInstance.data.datasets[0].data = data;
    latencyHistogramChartInstance.update();
}

// --- Phase 6: Financial Tracking Charts ---

let earningsHistoryChartInstance;
let earningsBreakdownChartInstance;

export function createEarningsHistoryChart() {
    if (earningsHistoryChartInstance) earningsHistoryChartInstance.destroy();
    const ctx = document.getElementById('earnings-chart').getContext('2d');
    earningsHistoryChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [
                {
                    label: 'Net Earnings',
                    data: [],
                    borderColor: '#22c55e',
                    backgroundColor: 'rgba(34, 197, 94, 0.1)',
                    fill: true,
                    tension: 0.3,
                    borderWidth: 2,
                    spanGaps: false  // Don't interpolate through missing data
                },
                {
                    label: 'Held Amount',
                    data: [],
                    borderColor: '#f59e0b',
                    backgroundColor: 'rgba(245, 158, 11, 0.1)',
                    fill: false,
                    tension: 0.3,
                    borderWidth: 2,
                    spanGaps: false  // Don't interpolate through missing data
                },
                {
                    label: 'Gross Earnings',
                    data: [],
                    borderColor: '#0ea5e9',
                    backgroundColor: 'transparent',
                    fill: false,
                    tension: 0.3,
                    borderWidth: 2,
                    borderDash: [5, 5],
                    spanGaps: false  // Don't interpolate through missing data
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
                        unit: 'month',
                        displayFormats: {
                            month: 'MMM yyyy'
                        },
                        tooltipFormat: 'PP'
                    },
                    title: {
                        display: true,
                        text: 'Month'
                    }
                },
                y: {
                    beginAtZero: true,
                    title: {
                        display: true,
                        text: 'Earnings ($)'
                    },
                    ticks: {
                        callback: (value) => {
                            return '$' + value.toFixed(2);
                        }
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
                                label += '$' + context.parsed.y.toFixed(2);
                            }
                            return label;
                        }
                    }
                }
            }
        }
    });
}

// Store history data from multiple nodes for aggregation
let earningsHistoryByNode = {};

export function clearEarningsHistoryCache() {
    earningsHistoryByNode = {};
}

export function updateEarningsHistoryChart(historyData) {
    if (!earningsHistoryChartInstance) createEarningsHistoryChart();
    
    console.log('updateEarningsHistoryChart received:', historyData?.length, 'records');
    
    if (!historyData || historyData.length === 0) {
        console.log('No earnings history data received');
        return;
    }
    
    // Store this node's history
    if (historyData.length > 0) {
        const nodeName = historyData[0].node_name;
        earningsHistoryByNode[nodeName] = historyData;
        console.log(`Stored ${historyData.length} records for node: ${nodeName}`);
    }
    
    // Determine which nodes to display based on current view
    let nodesToDisplay = [];
    if (window.currentView && window.currentView.length === 1 && window.currentView[0] !== 'Aggregate') {
        // Single node view
        nodesToDisplay = [window.currentView[0]];
    } else {
        // Aggregate or multi-node view - show all stored histories
        nodesToDisplay = Object.keys(earningsHistoryByNode);
    }
    
    // Aggregate all nodes' history data
    const allHistoryData = [];
    nodesToDisplay.forEach(nodeName => {
        const nodeHistory = earningsHistoryByNode[nodeName];
        if (nodeHistory) {
            allHistoryData.push(...nodeHistory);
        }
    });
    
    console.log(`Aggregating ${allHistoryData.length} records from ${nodesToDisplay.length} node(s)`);
    
    if (allHistoryData.length === 0) {
        console.log('No earnings history data to display, clearing chart');
        earningsHistoryChartInstance.data.datasets[0].data = [];
        earningsHistoryChartInstance.data.datasets[1].data = [];
        earningsHistoryChartInstance.data.datasets[2].data = [];
        earningsHistoryChartInstance.update();
        return;
    }
    
    // Get current month to use forecast instead of accumulated
    const now = new Date();
    const currentPeriod = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
    const dayOfMonth = now.getDate();
    
    // Group by period and aggregate across satellites AND nodes
    const byPeriod = {};
    allHistoryData.forEach(item => {
        const period = item.period;
        if (!byPeriod[period]) {
            byPeriod[period] = {
                total_earnings_net: 0,
                total_earnings_gross: 0,
                held_amount: 0,
                has_forecast: false,
                forecast_month_end: 0
            };
        }
        byPeriod[period].total_earnings_net += item.total_earnings_net || 0;
        byPeriod[period].total_earnings_gross += item.total_earnings_gross || 0;
        byPeriod[period].held_amount += item.held_amount || 0;
        
        // Track forecast data for current month
        if (item.forecast_month_end && item.forecast_month_end > 0) {
            byPeriod[period].has_forecast = true;
            byPeriod[period].forecast_month_end = Math.max(byPeriod[period].forecast_month_end, item.forecast_month_end);
        }
    });
    
    // Convert to array and sort by date
    let aggregated = Object.keys(byPeriod).map(period => {
        const data = byPeriod[period];
        
        // For current month: extrapolate to get forecast (accumulated * days_in_month / days_elapsed)
        if (period === currentPeriod && dayOfMonth < 25 && data.total_earnings_net > 0) {
            const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
            const extrapolationFactor = daysInMonth / dayOfMonth;
            const forecastNet = data.total_earnings_net * extrapolationFactor;
            const forecastHeld = data.held_amount * extrapolationFactor;
            
            console.log(`Extrapolating ${period}: $${data.total_earnings_net.toFixed(2)} × ${extrapolationFactor.toFixed(2)} = $${forecastNet.toFixed(2)} (day ${dayOfMonth}/${daysInMonth})`);
            
            return {
                period: period,
                total_earnings_net: forecastNet,
                total_earnings_gross: forecastNet + forecastHeld,
                held_amount: forecastHeld,
                is_forecast: true
            };
        }
        
        return {
            period: period,
            ...data,
            is_forecast: false
        };
    }).sort((a, b) => a.period.localeCompare(b.period));
    
    console.log('Aggregated data:', aggregated.length, 'periods', aggregated.filter(a => a.is_forecast).length, 'with forecasts');
    
    // Map data to chart datasets
    // Parse period (YYYY-MM) to date at noon UTC to avoid timezone boundary issues
    const netEarnings = aggregated.map(item => ({
        x: new Date(item.period + '-15T12:00:00Z'),  // 15th at noon UTC avoids timezone issues
        y: item.total_earnings_net
    }));
    
    const heldAmount = aggregated.map(item => ({
        x: new Date(item.period + '-15T12:00:00Z'),  // 15th at noon UTC avoids timezone issues
        y: item.held_amount
    }));
    
    const grossEarnings = aggregated.map(item => ({
        x: new Date(item.period + '-15T12:00:00Z'),  // 15th at noon UTC avoids timezone issues
        y: item.total_earnings_gross
    }));
    
    console.log('Chart data points:', {
        net: netEarnings.length,
        held: heldAmount.length,
        gross: grossEarnings.length
    });
    
    earningsHistoryChartInstance.data.datasets[0].data = netEarnings;
    earningsHistoryChartInstance.data.datasets[1].data = heldAmount;
    earningsHistoryChartInstance.data.datasets[2].data = grossEarnings;
    earningsHistoryChartInstance.update();
    console.log('Chart updated successfully');
}

export function createEarningsBreakdownChart() {
    if (earningsBreakdownChartInstance) earningsBreakdownChartInstance.destroy();
    const ctx = document.getElementById('earnings-breakdown-chart').getContext('2d');
    earningsBreakdownChartInstance = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Egress', 'Storage', 'Repair', 'Audit'],
            datasets: [{
                data: [0, 0, 0, 0],
                backgroundColor: [
                    '#0ea5e9',  // Blue for Egress
                    '#22c55e',  // Green for Storage
                    '#f59e0b',  // Orange for Repair
                    '#a855f7'   // Purple for Audit
                ],
                borderWidth: 2,
                borderColor: '#fff'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom'
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            const label = context.label || '';
                            const value = context.parsed || 0;
                            const total = context.dataset.data.reduce((a, b) => a + b, 0);
                            const percentage = total > 0 ? ((value / total) * 100).toFixed(1) : 0;
                            return `${label}: $${value.toFixed(2)} (${percentage}%)`;
                        }
                    }
                }
            }
        }
    });
}

export function updateEarningsBreakdownChart(breakdownData) {
    if (!earningsBreakdownChartInstance) createEarningsBreakdownChart();
    if (!breakdownData) return;
    
    const data = [
        breakdownData.egress || 0,
        breakdownData.storage || 0,
        breakdownData.repair || 0,
        breakdownData.audit || 0
    ];
    
    earningsBreakdownChartInstance.data.datasets[0].data = data;
    earningsBreakdownChartInstance.update();
}
