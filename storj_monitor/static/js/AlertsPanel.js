/**
 * AlertsPanel Component - Phase 4
 * Displays active alerts and insights with management capabilities
 */

class AlertsPanel {
    constructor() {
        this.alerts = [];
        this.insights = [];
        this.container = null;
        this.isVisible = false;
        this.autoRefreshInterval = null;
    }

    initialize() {
        // Create alert badge in header
        this.createAlertBadge();
        
        // Create alerts panel
        this.createPanel();
        
        // Set up auto-refresh
        this.startAutoRefresh();
    }

    createAlertBadge() {
        const headerControls = document.querySelector('.header-controls');
        if (!headerControls) return;

        const badge = document.createElement('div');
        badge.id = 'alert-badge';
        badge.className = 'alert-badge';
        badge.innerHTML = `
            <button id="alerts-toggle" class="btn-icon" title="Alerts & Insights">
                🔔 <span id="alert-count" class="badge hidden">0</span>
            </button>
        `;
        
        // Append to header controls (will be to the right of Options button)
        headerControls.appendChild(badge);

        // Add click handler
        document.getElementById('alerts-toggle').addEventListener('click', () => {
            this.togglePanel();
        });
    }

    createPanel() {
        this.container = document.createElement('div');
        this.container.id = 'alerts-panel';
        this.container.className = 'alerts-panel hidden';
        this.container.innerHTML = `
            <div class="alerts-panel-header">
                <h3>🚨 Alerts & Insights</h3>
                <button id="close-alerts" class="btn-icon">✕</button>
            </div>
            
            <div class="alerts-panel-tabs">
                <button class="tab-btn active" data-tab="alerts">
                    Alerts <span id="alerts-tab-count" class="badge">0</span>
                </button>
                <button class="tab-btn" data-tab="insights">
                    Insights <span id="insights-tab-count" class="badge">0</span>
                </button>
            </div>
            
            <div class="alerts-panel-content">
                <!-- Alerts Tab -->
                <div id="alerts-tab" class="tab-content active">
                    <div class="alerts-filter">
                        <label>
                            <input type="checkbox" id="filter-critical" checked> Critical
                        </label>
                        <label>
                            <input type="checkbox" id="filter-warning" checked> Warning
                        </label>
                        <label>
                            <input type="checkbox" id="filter-info" checked> Info
                        </label>
                    </div>
                    <div id="alerts-list" class="alerts-list">
                        <div class="empty-state">No active alerts</div>
                    </div>
                </div>
                
                <!-- Insights Tab -->
                <div id="insights-tab" class="tab-content">
                    <div class="insights-filter">
                        <small>Range:</small>
                        <a href="#" class="toggle-link active" data-hours="24">24h</a> |
                        <a href="#" class="toggle-link" data-hours="72">3d</a> |
                        <a href="#" class="toggle-link" data-hours="168">7d</a>
                    </div>
                    <div id="insights-list" class="insights-list">
                        <div class="empty-state">No insights available</div>
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(this.container);

        // Set up event listeners
        this.setupEventListeners();
    }

    setupEventListeners() {
        // Close button
        document.getElementById('close-alerts').addEventListener('click', () => {
            this.hidePanel();
        });

        // Tab switching
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const tab = e.target.dataset.tab;
                this.switchTab(tab);
            });
        });

        // Filter checkboxes
        ['critical', 'warning', 'info'].forEach(severity => {
            document.getElementById(`filter-${severity}`).addEventListener('change', () => {
                this.renderAlerts();
            });
        });

        // Insights timeframe links
        document.querySelectorAll('#insights-tab .toggle-link').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                const hours = parseInt(e.target.dataset.hours);
                
                // Update active state
                document.querySelectorAll('#insights-tab .toggle-link').forEach(l => {
                    l.classList.remove('active');
                });
                e.target.classList.add('active');
                
                // Fetch insights
                this.fetchInsights(hours);
            });
        });

        // Close on escape
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.isVisible) {
                this.hidePanel();
            }
        });
    }

    togglePanel() {
        if (this.isVisible) {
            this.hidePanel();
        } else {
            this.showPanel();
        }
    }

    showPanel() {
        this.container.classList.remove('hidden');
        this.isVisible = true;
        this.fetchAlerts();
        this.fetchInsights();
    }

    hidePanel() {
        this.container.classList.add('hidden');
        this.isVisible = false;
    }

    switchTab(tabName) {
        // Update tab buttons
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.tab === tabName);
        });

        // Update tab content
        document.querySelectorAll('.tab-content').forEach(content => {
            content.classList.toggle('active', content.id === `${tabName}-tab`);
        });

        // Fetch data if needed
        if (tabName === 'alerts') {
            this.fetchAlerts();
        } else if (tabName === 'insights') {
            this.fetchInsights();
        }
    }

    async fetchAlerts() {
        try {
            window.ws.send(JSON.stringify({
                type: 'get_active_alerts',
                view: window.currentView || ['Aggregate']
            }));
        } catch (error) {
            console.error('Failed to fetch alerts:', error);
        }
    }

    async fetchInsights(hours = 24) {
        try {
            window.ws.send(JSON.stringify({
                type: 'get_insights',
                view: window.currentView || ['Aggregate'],
                hours: hours
            }));
        } catch (error) {
            console.error('Failed to fetch insights:', error);
        }
    }

    updateAlerts(alertsData) {
        this.alerts = alertsData || [];
        this.renderAlerts();
        this.updateBadge();
    }

    updateInsights(insightsData) {
        this.insights = insightsData || [];
        this.renderInsights();
    }

    renderAlerts() {
        const listContainer = document.getElementById('alerts-list');
        
        // Get active filters
        const filters = {
            critical: document.getElementById('filter-critical').checked,
            warning: document.getElementById('filter-warning').checked,
            info: document.getElementById('filter-info').checked
        };

        // Filter alerts
        const filteredAlerts = this.alerts.filter(alert => 
            filters[alert.severity]
        );

        if (filteredAlerts.length === 0) {
            listContainer.innerHTML = '<div class="empty-state">No active alerts</div>';
            return;
        }

        // Sort by severity and timestamp
        const severityOrder = { critical: 0, warning: 1, info: 2 };
        filteredAlerts.sort((a, b) => {
            if (a.severity !== b.severity) {
                return severityOrder[a.severity] - severityOrder[b.severity];
            }
            return new Date(b.timestamp) - new Date(a.timestamp);
        });

        // Render alerts
        listContainer.innerHTML = filteredAlerts.map(alert => this.renderAlert(alert)).join('');

        // Add acknowledge handlers
        listContainer.querySelectorAll('.btn-acknowledge').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const alertId = parseInt(e.target.dataset.alertId);
                this.acknowledgeAlert(alertId);
            });
        });

        // Update tab count
        document.getElementById('alerts-tab-count').textContent = filteredAlerts.length;
    }

    renderAlert(alert) {
        const severityIcons = {
            critical: '🔴',
            warning: '🟡',
            info: '🔵'
        };

        const icon = severityIcons[alert.severity] || '⚪';
        const timestamp = new Date(alert.timestamp).toLocaleString();
        const metadata = alert.metadata ? JSON.parse(alert.metadata) : {};

        return `
            <div class="alert-item severity-${alert.severity}">
                <div class="alert-header">
                    <span class="alert-icon">${icon}</span>
                    <span class="alert-title">${this.escapeHtml(alert.title)}</span>
                    <button class="btn-acknowledge" data-alert-id="${alert.id}" title="Acknowledge">
                        ✓
                    </button>
                </div>
                <div class="alert-message">${this.escapeHtml(alert.message)}</div>
                <div class="alert-meta">
                    <span class="alert-node">${this.escapeHtml(alert.node_name)}</span>
                    <span class="alert-time">${timestamp}</span>
                </div>
                ${this.renderAlertMetadata(metadata)}
            </div>
        `;
    }

    renderAlertMetadata(metadata) {
        if (!metadata || Object.keys(metadata).length === 0) {
            return '';
        }

        const items = Object.entries(metadata)
            .filter(([key]) => !['satellite', 'node_name'].includes(key))
            .map(([key, value]) => {
                const displayValue = typeof value === 'number' ? value.toFixed(2) : value;
                return `<span class="meta-item">${key}: ${displayValue}</span>`;
            })
            .join('');

        return `<div class="alert-metadata">${items}</div>`;
    }

    renderInsights() {
        const listContainer = document.getElementById('insights-list');

        if (this.insights.length === 0) {
            listContainer.innerHTML = '<div class="empty-state">No insights available</div>';
            return;
        }

        // Sort by timestamp (most recent first)
        this.insights.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));

        // Render insights
        listContainer.innerHTML = this.insights.map(insight => this.renderInsight(insight)).join('');

        // Update tab count
        document.getElementById('insights-tab-count').textContent = this.insights.length;
    }

    renderInsight(insight) {
        const categoryIcons = {
            reputation: '🎯',
            storage: '💾',
            performance: '⚡',
            bandwidth: '📊',
            errors: '❌',
            uptime: '🔌'
        };

        const icon = categoryIcons[insight.category] || '💡';
        const timestamp = new Date(insight.timestamp).toLocaleString();
        const confidence = insight.confidence ? (insight.confidence * 100).toFixed(0) + '%' : '';
        const metadata = insight.metadata ? JSON.parse(insight.metadata) : {};

        return `
            <div class="insight-item severity-${insight.severity}">
                <div class="insight-header">
                    <span class="insight-icon">${icon}</span>
                    <span class="insight-title">${this.escapeHtml(insight.title)}</span>
                    ${confidence ? `<span class="insight-confidence">${confidence}</span>` : ''}
                </div>
                <div class="insight-description">${this.escapeHtml(insight.description)}</div>
                <div class="insight-meta">
                    <span class="insight-category">${insight.category}</span>
                    <span class="insight-node">${this.escapeHtml(insight.node_name)}</span>
                    <span class="insight-time">${timestamp}</span>
                </div>
            </div>
        `;
    }

    async acknowledgeAlert(alertId) {
        try {
            window.ws.send(JSON.stringify({
                type: 'acknowledge_alert',
                alert_id: alertId
            }));

            // Remove from local list
            this.alerts = this.alerts.filter(a => a.id !== alertId);
            this.renderAlerts();
            this.updateBadge();

        } catch (error) {
            console.error('Failed to acknowledge alert:', error);
        }
    }

    updateBadge() {
        const criticalCount = this.alerts.filter(a => a.severity === 'critical').length;
        const warningCount = this.alerts.filter(a => a.severity === 'warning').length;
        const totalCount = this.alerts.length;

        const countElement = document.getElementById('alert-count');
        if (countElement) {
            countElement.textContent = totalCount;
            countElement.classList.toggle('has-critical', criticalCount > 0);
            countElement.classList.toggle('has-warning', warningCount > 0 && criticalCount === 0);
            countElement.classList.toggle('hidden', totalCount === 0);
        }
    }

    handleNewAlert(alert) {
        // Add to alerts list
        this.alerts.unshift(alert);
        
        // Update UI
        this.renderAlerts();
        this.updateBadge();
        
        // Show browser notification if enabled
        if (window.notificationsEnabled && alert.severity === 'critical') {
            this.showBrowserNotification(alert);
        }
        
        // Flash the badge
        const badge = document.getElementById('alert-badge');
        if (badge) {
            badge.classList.add('flash');
            setTimeout(() => badge.classList.remove('flash'), 2000);
        }
    }

    showBrowserNotification(alert) {
        if ('Notification' in window && Notification.permission === 'granted') {
            new Notification(`Storj Monitor Alert: ${alert.title}`, {
                body: alert.message,
                icon: '/static/favicon.ico',
                tag: `alert-${alert.alert_type}`,
                requireInteraction: alert.severity === 'critical'
            });
        }
    }

    startAutoRefresh() {
        // Refresh alerts every 60 seconds if panel is visible
        this.autoRefreshInterval = setInterval(() => {
            if (this.isVisible) {
                const activeTab = document.querySelector('.tab-btn.active').dataset.tab;
                if (activeTab === 'alerts') {
                    this.fetchAlerts();
                } else if (activeTab === 'insights') {
                    this.fetchInsights();
                }
            }
        }, 60000);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    destroy() {
        if (this.autoRefreshInterval) {
            clearInterval(this.autoRefreshInterval);
        }
        if (this.container) {
            this.container.remove();
        }
    }
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.alertsPanel = new AlertsPanel();
        window.alertsPanel.initialize();
    });
} else {
    window.alertsPanel = new AlertsPanel();
    window.alertsPanel.initialize();
}