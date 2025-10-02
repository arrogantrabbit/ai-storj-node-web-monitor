// --- Advanced Heatmap Implementation ---
class AdvancedHeatmap {
    constructor(map) {
        this.map = map;
        this.dataPoints = [];
        this.persistentHeatData = {}; // Grid-based accumulation
        this.canvas = null;
        this.ctx = null;
        this.viewMode = 'size'; // 'size' or 'pieces'
        this.maxDataAge = 5 * 60 * 1000; // 5 minutes for full visibility
        this.animationFrame = null;
        this.particleSystem = [];
        this.lastRenderTime = Date.now();
        this.gridSize = 0.5; // Degrees for heatmap grid
        this.isPaused = false;

        this.setupCanvas();
        this.startAnimation();
    }

    setupCanvas() {
        if (!this.map.getPane('heatmapPane')) {
            this.map.createPane('heatmapPane');
            this.map.getPane('heatmapPane').style.zIndex = 650;
        }
        this.canvas = L.DomUtil.create('canvas', 'leaflet-heatmap-layer');
        this.ctx = this.canvas.getContext('2d');
        this.canvasLayer = L.canvasLayer({ pane: 'heatmapPane' }).addTo(this.map);
        this.canvasLayer.delegate({ onDrawLayer: (info) => this.render(info) });
        this.map.on('moveend zoomend resize', () => this.canvasLayer.needRedraw());
    }

    processEvent(event) {
        const now = Date.now();
        this.dataPoints.push({ lat: event.lat, lon: event.lon, size: event.size, type: event.type, timestamp: now, pieces: 1, isNew: true });
        const gridKey = `${Math.floor(event.lat / this.gridSize) * this.gridSize}_${Math.floor(event.lon / this.gridSize) * this.gridSize}`;
        if (!this.persistentHeatData[gridKey]) {
            this.persistentHeatData[gridKey] = { lat: Math.floor(event.lat / this.gridSize) * this.gridSize, lon: Math.floor(event.lon / this.gridSize) * this.gridSize, value: 0, timestamp: now };
        }
        this.persistentHeatData[gridKey].value += this.viewMode === 'size' ? event.size : 1;
        this.persistentHeatData[gridKey].timestamp = now;
        this.addParticle(event.lat, event.lon, event.type);
        const cutoffTime = now - this.maxDataAge * 2;
        this.dataPoints = this.dataPoints.filter(p => p.timestamp > cutoffTime);
        for (let key in this.persistentHeatData) {
            if (now - this.persistentHeatData[key].timestamp > this.maxDataAge * 4) delete this.persistentHeatData[key];
        }
        this.canvasLayer.needRedraw();
    }

    clearData() {
        this.dataPoints = [];
        this.persistentHeatData = {};
        this.particleSystem = [];
        this.canvasLayer.needRedraw();
    }

    addDataPoint(lat, lon, size, type, action, timestamp) {
         const event = { lat, lon, size, type: action.includes('GET') ? (action === 'GET_AUDIT' ? 'audit' : 'download') : 'upload', action, eventTimestamp: timestamp ? new Date(timestamp).getTime() : Date.now() };
         this.processEvent(event);
    }

    addParticle(lat, lon, type) {
        const colors = { download: 'rgba(0, 200, 255, 0.8)', upload: 'rgba(200, 255, 0, 0.8)', audit: 'rgba(255, 150, 255, 0.8)' };
        this.particleSystem.push({ lat, lon, color: colors[type], size: 15, opacity: 1, timestamp: Date.now() });
    }

    render(info) {
        const ctx = info.canvas.getContext('2d');
        const bounds = this.map.getBounds();
        const zoom = this.map.getZoom();
        const now = Date.now();
        ctx.clearRect(0, 0, info.canvas.width, info.canvas.height);
        this.renderPersistentHeatmap(ctx, info, bounds, zoom, now);
        this.renderLivePoints(ctx, info, bounds, zoom, now);
        this.renderParticles(ctx, info);
        ctx.globalAlpha = 1;
    }

    renderPersistentHeatmap(ctx, info, bounds, zoom, now) {
        if (zoom < 5) return;
        const heatmapRadius = this.getRadiusForZoom(zoom) * 2;
        const tempCanvas = document.createElement('canvas');
        tempCanvas.width = info.canvas.width; tempCanvas.height = info.canvas.height;
        const tempCtx = tempCanvas.getContext('2d');
        for (let key in this.persistentHeatData) {
            const heat = this.persistentHeatData[key];
            if (!bounds.contains([heat.lat, heat.lon])) continue;
            const point = info.layer._map.latLngToContainerPoint([heat.lat, heat.lon]);
            const age = now - heat.timestamp;
            const ageFactor = Math.max(0.1, 1 - (age / (this.maxDataAge * 4)));
            const maxValue = this.viewMode === 'size' ? 10000000 : 100;
            const intensity = Math.min(1, Math.sqrt(heat.value / maxValue));
            const gradient = tempCtx.createRadialGradient(point.x, point.y, 0, point.x, point.y, heatmapRadius);
            gradient.addColorStop(0, `rgba(255, 255, 0, ${intensity * ageFactor * 0.75})`);
            gradient.addColorStop(0.3, `rgba(255, 200, 0, ${intensity * ageFactor * 0.5})`);
            gradient.addColorStop(0.6, `rgba(255, 100, 0, ${intensity * ageFactor * 0.25})`);
            gradient.addColorStop(1, 'rgba(0, 0, 0, 0)');
            tempCtx.fillStyle = gradient;
            tempCtx.fillRect(point.x - heatmapRadius, point.y - heatmapRadius, heatmapRadius * 2, heatmapRadius * 2);
        }
        const isDarkMode = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
        ctx.globalCompositeOperation = isDarkMode ? 'screen' : 'lighter';
        ctx.globalAlpha = isDarkMode ? 1.0 : 0.7;
        ctx.drawImage(tempCanvas, 0, 0);
        ctx.globalCompositeOperation = 'source-over';
    }

    renderLivePoints(ctx, info, bounds, zoom, now) {
        const clusterThreshold = this.getClusterThreshold(zoom);
        const clusters = this.clusterPoints(this.dataPoints, clusterThreshold, bounds);
        const isDarkMode = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
        if (zoom <= 4) {
            ctx.globalCompositeOperation = isDarkMode ? 'screen' : 'lighter';
            clusters.forEach(cluster => {
                const point = info.layer._map.latLngToContainerPoint([cluster.lat, cluster.lon]);
                const age = now - cluster.avgTimestamp;
                const ageFactor = Math.max(0, 1 - (age / this.maxDataAge));
                const value = this.viewMode === 'size' ? cluster.totalSize : cluster.count;
                const maxValue = this.viewMode === 'size' ? 50000000 : 500;
                const intensity = Math.min(1, Math.sqrt(value / maxValue));
                const radius = 50 + intensity * 50;
                const gradient = ctx.createRadialGradient(point.x, point.y, 0, point.x, point.y, radius);
                if (intensity > 0.7) { gradient.addColorStop(0, `rgba(255, 0, 0, ${ageFactor * 0.6})`); gradient.addColorStop(0.5, `rgba(255, 255, 0, ${ageFactor * 0.4})`); gradient.addColorStop(1, 'rgba(0, 0, 0, 0)'); }
                else if (intensity > 0.4) { gradient.addColorStop(0, `rgba(255, 255, 0, ${ageFactor * 0.5})`); gradient.addColorStop(0.5, `rgba(0, 255, 0, ${ageFactor * 0.25})`); gradient.addColorStop(1, 'rgba(0, 0, 0, 0)'); }
                else { gradient.addColorStop(0, `rgba(0, 255, 255, ${ageFactor * 0.4})`); gradient.addColorStop(0.5, `rgba(0, 0, 255, ${ageFactor * 0.2})`); gradient.addColorStop(1, 'rgba(0, 0, 0, 0)'); }
                ctx.fillStyle = gradient;
                ctx.fillRect(point.x - radius, point.y - radius, radius * 2, radius * 2);
            });
            ctx.globalCompositeOperation = 'source-over';
        } else {
            clusters.forEach(cluster => {
                const point = info.layer._map.latLngToContainerPoint([cluster.lat, cluster.lon]);
                const age = now - cluster.avgTimestamp;
                const ageFactor = Math.max(0, 1 - (age / this.maxDataAge));
                const value = this.viewMode === 'size' ? cluster.totalSize : cluster.count;
                const maxValue = this.viewMode === 'size' ? 1000000 : 10;
                const intensity = Math.min(1, Math.sqrt(value / maxValue));
                const radius = 3 + intensity * 5;
                const typeColors = { download: '#0099FF', upload: '#00FF00', audit: '#CC00FF' };
                ctx.globalAlpha = ageFactor * 0.9;
                ctx.fillStyle = typeColors[cluster.dominantType];
                ctx.beginPath(); ctx.arc(point.x, point.y, radius, 0, Math.PI * 2); ctx.fill();
                if (cluster.hasNew) {
                    ctx.globalAlpha = ageFactor * 0.5; ctx.strokeStyle = typeColors[cluster.dominantType]; ctx.lineWidth = 2;
                    ctx.beginPath(); ctx.arc(point.x, point.y, radius + 3, 0, Math.PI * 2); ctx.stroke();
                }
            });
        }
    }

    renderParticles(ctx, info) {
        const now = Date.now();
        const particleLifetime = 2000;
        this.particleSystem = this.particleSystem.filter(particle => {
            const age = now - particle.timestamp; if (age > particleLifetime) return false;
            const point = info.layer._map.latLngToContainerPoint([particle.lat, particle.lon]);
            const progress = age / particleLifetime; const size = particle.size * (1 + progress * 3); const opacity = particle.opacity * (1 - progress);
            ctx.save(); ctx.globalAlpha = opacity; ctx.strokeStyle = particle.color; ctx.lineWidth = 1.5;
            ctx.beginPath(); ctx.arc(point.x, point.y, size, 0, Math.PI * 2); ctx.stroke(); ctx.restore(); return true;
        });
    }

    clusterPoints(points, threshold, bounds) {
        const clusters = [];
        points.forEach(point => {
            if (!bounds.contains([point.lat, point.lon])) return;
            let merged = false;
            for (let cluster of clusters) {
                const dist = this.getDistance(point.lat, point.lon, cluster.lat, cluster.lon);
                if (dist < threshold) {
                    cluster.count++; cluster.totalSize += point.size;
                    cluster.lat = (cluster.lat * (cluster.count - 1) + point.lat) / cluster.count;
                    cluster.lon = (cluster.lon * (cluster.count - 1) + point.lon) / cluster.count;
                    cluster.avgTimestamp = (cluster.avgTimestamp * (cluster.count - 1) + point.timestamp) / cluster.count;
                    cluster.types[point.type] = (cluster.types[point.type] || 0) + 1;
                    cluster.dominantType = Object.keys(cluster.types).reduce((a, b) => cluster.types[a] > cluster.types[b] ? a : b);
                    if (point.isNew) cluster.hasNew = true;
                    merged = true; break;
                }
            }
            if (!merged) clusters.push({ lat: point.lat, lon: point.lon, count: 1, totalSize: point.size, avgTimestamp: point.timestamp, types: { [point.type]: 1 }, dominantType: point.type, hasNew: point.isNew });
        });
        return clusters;
    }

    getDistance(lat1, lon1, lat2, lon2) { const R = 6371; const dLat = (lat2 - lat1) * Math.PI / 180; const dLon = (lon2 - lon1) * Math.PI / 180; const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) * Math.sin(dLon / 2); const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a)); return R * c; }
    getRadiusForZoom = zoom => (zoom <= 2) ? 15 : (zoom <= 4) ? 12 : (zoom <= 6) ? 10 : (zoom <= 8) ? 8 : (zoom <= 10) ? 6 : (zoom <= 12) ? 5 : 4;
    getClusterThreshold = zoom => (zoom <= 2) ? 1000 : (zoom <= 4) ? 500 : (zoom <= 6) ? 200 : (zoom <= 8) ? 100 : (zoom <= 10) ? 50 : (zoom <= 12) ? 20 : 10;
    setViewMode(mode) { this.viewMode = mode; this.canvasLayer.needRedraw(); }

    startAnimation() {
        const animate = () => {
            if (this.isPaused) { this.animationFrame = null; return; }
            const now = Date.now();
            this.dataPoints.forEach(p => { if (p.isNew && now - p.timestamp > 500) p.isNew = false; });
            if (this.particleSystem.length > 0 || this.dataPoints.some(p => p.isNew)) { this.canvasLayer.needRedraw(); }
            this.animationFrame = requestAnimationFrame(animate);
        };
        if (!this.animationFrame) this.animationFrame = requestAnimationFrame(animate);
    }
    pause() { this.isPaused = true; }
    resume() { if (this.isPaused) { this.isPaused = false; this.startAnimation(); this.canvasLayer.needRedraw(); } }
    destroy() { this.isPaused = true; if (this.canvasLayer) this.map.removeLayer(this.canvasLayer); }
}

L.CanvasLayer = L.Layer.extend({ options: { pane: 'overlayPane' }, initialize: function(options) { L.setOptions(this, options); }, onAdd: function(map) { this._map = map; this._canvas = L.DomUtil.create('canvas', 'leaflet-canvas-layer'); const size = this._map.getSize(); this._canvas.width = size.x; this._canvas.height = size.y; const animated = this._map.options.zoomAnimation && L.Browser.any3d; L.DomUtil.addClass(this._canvas, 'leaflet-zoom-' + (animated ? 'animated' : 'hide')); map.getPane(this.options.pane).appendChild(this._canvas); map.on('move moveend resize zoomend', this._reset, this); if (map.options.zoomAnimation && L.Browser.any3d) { map.on('zoomanim', this._animateZoom, this); } this._reset(); }, onRemove: function(map) { L.DomUtil.remove(this._canvas); map.off('moveend resize zoomend', this._reset, this); if (map.options.zoomAnimation) { map.off('zoomanim', this._animateZoom, this); } }, delegate: function(del) { this._delegate = del; return this; }, needRedraw: function() { if (!this._frame) { this._frame = L.Util.requestAnimFrame(this._redraw, this); } return this; }, _redraw: function() { if (this._delegate && this._delegate.onDrawLayer) { const info = { layer: this, canvas: this._canvas, bounds: this._map.getBounds(), size: this._map.getSize(), topLeft: this._map.latLngToLayerPoint(this._map.getBounds().getNorthWest()) }; this._delegate.onDrawLayer(info); } this._frame = null; }, _reset: function() { const topLeft = this._map.containerPointToLayerPoint([0, 0]); L.DomUtil.setPosition(this._canvas, topLeft); const size = this._map.getSize(); this._canvas.width = size.x; this._canvas.height = size.y; this._redraw(); }, _animateZoom: function(e) { const scale = this._map.getZoomScale(e.zoom); const offset = this._map._latLngBoundsToNewLayerBounds(this._map.getBounds(), e.zoom, e.center).min; L.DomUtil.setTransform(this._canvas, offset, scale); } });
L.canvasLayer = options => new L.CanvasLayer(options);

// --- Map Setup ---
export const map = L.map('map').setView([20, 0], 2);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', maxZoom: 18 }).addTo(map);

export const heatmap = new AdvancedHeatmap(map);

const HeatmapControl = L.Control.extend({
    options: { position: 'topright' },
    onAdd: function(map) { const container = L.DomUtil.create('div', 'leaflet-bar leaflet-control heatmap-control'); const isDarkMode = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches; container.innerHTML = `<style>.heatmap-control{background-color:${isDarkMode?'#1e1e1e':'white'};color:${isDarkMode?'#e0e0e0':'#333'};padding:10px;border-radius:4px;border:1px solid ${isDarkMode?'#444':'#ccc'}}.heatmap-control h4{margin:0 0 8px 0;font-size:13px;font-weight:bold}.heatmap-control label{display:block;cursor:pointer;margin:4px 0;font-size:12px}.legend-section{margin-top:12px;padding-top:8px;border-top:1px solid ${isDarkMode?'#444':'#ddd'}}.legend-item{display:flex;align-items:center;margin:3px 0;font-size:11px}.legend-color{width:16px;height:16px;margin-right:6px;border-radius:50%;border:1px solid ${isDarkMode?'#666':'#ccc'}}.legend-gradient{width:100%;height:10px;margin:4px 0;border-radius:2px;background:linear-gradient(to right,rgba(0,0,255,0.2),rgba(0,255,255,0.4),rgba(0,255,0,0.6),rgba(255,255,0,0.8),rgba(255,0,0,1))}</style><div><h4>Heatmap View</h4><label><input type="radio" name="heatview" value="size" checked> Data Size</label><label><input type="radio" name="heatview" value="pieces"> Piece Count</label></div><div class="legend-section"><h4>Legend</h4><div class="legend-item"><span class="legend-color" style="background:#0099FF"></span> <span>Downloads</span></div><div class="legend-item"><span class="legend-color" style="background:#00FF00"></span> <span>Uploads</span></div><div class="legend-item"><span class="legend-color" style="background:#CC00FF"></span> <span>Audits</span></div><div style="margin-top:8px"><div style="font-size:11px;margin-bottom:2px">Activity Heat:</div><div class="legend-gradient"></div><div style="font-size:10px;display:flex;justify-content:space-between"><span>Low</span> <span>High</span></div></div></div>`; container.addEventListener('change', e => { if (e.target.name === 'heatview') heatmap.setViewMode(e.target.value); }); window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => { const isDark = e.matches; container.style.backgroundColor = isDark ? '#1e1e1e' : 'white'; container.style.color = isDark ? '#e0e0e0' : '#333'; container.style.borderColor = isDark ? '#444' : '#ccc'; }); L.DomEvent.disableClickPropagation(container); L.DomEvent.disableScrollPropagation(container); return container; }
});
map.addControl(new HeatmapControl());
