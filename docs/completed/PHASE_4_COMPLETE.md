# Phase 4 Complete: Intelligence & Advanced Features 🎉

**Status:** Core implementation complete  
**Date:** January 2025  
**Implementation Time:** ~2 weeks

---

## 🎯 Overview

Phase 4 adds **intelligent monitoring, anomaly detection, predictive analytics, and comprehensive alerting** to the Storj Node Monitor. The system can now automatically detect unusual patterns, predict future issues, and proactively alert operators before problems become critical.

---

## ✅ Completed Features

### 4.1 Analytics Engine (`analytics_engine.py`)

**Purpose:** Provides statistical analysis and pattern recognition capabilities.

**Key Features:**
- ✅ Baseline calculation for metrics (mean, std dev, min, max)
- ✅ Z-score calculation for anomaly detection
- ✅ Trend detection using linear regression
- ✅ Percentile calculations (p50, p95, p99)
- ✅ Rate of change calculations
- ✅ Linear forecasting for capacity planning
- ✅ Reputation health analysis
- ✅ Storage health analysis with growth forecasting

**Database Tables:**
- `analytics_baselines` - Stores statistical baselines for metrics

**Configuration:**
```python
ANOMALY_BASELINE_DAYS = 7  # Days of historical data for baseline
ANALYTICS_BASELINE_UPDATE_HOURS = 24  # How often to update baselines
```

---

### 4.2 Anomaly Detection (`anomaly_detector.py`)

**Purpose:** Detects unusual patterns and behaviors in node operations.

**Detection Methods:**
1. **Statistical Anomaly Detection** - Z-score based detection (>3σ from baseline)
2. **Traffic Anomaly Detection** - Unusual success/failure rates
3. **Latency Anomaly Detection** - Performance spikes or degradation
4. **Bandwidth Anomaly Detection** - Unusual egress/ingress patterns

**Key Features:**
- ✅ Configurable Z-score threshold (default: 3.0)
- ✅ Automatic baseline learning from historical data
- ✅ Confidence scoring for detected anomalies
- ✅ Recent anomalies cache for quick access
- ✅ Context-aware anomaly classification (spike vs drop)

**Configuration:**
```python
ANOMALY_ZSCORE_THRESHOLD = 3.0  # Z-score threshold for anomaly
ENABLE_ANOMALY_DETECTION = True  # Enable/disable detection
```

---

### 4.3 Predictive Analytics

**Purpose:** Forecast future issues before they occur.

**Capabilities:**
- ✅ **Storage Capacity Forecasting**
  - Calculates growth rate from historical data
  - Predicts days until disk full
  - Generates early warnings (30 days, 7 days)
  
- ✅ **Linear Trend Forecasting**
  - Forecasts metric values into the future
  - Uses recent trend data for predictions
  - Configurable forecast window

- ✅ **Trend Analysis**
  - Detects increasing, decreasing, or stable trends
  - Uses linear regression for trend calculation
  - Normalized slope for consistent analysis

**Example Output:**
```
Storage will be full in approximately 45.2 days at current growth rate
Growth rate: 12.5 GB/day
Confidence: 70%
```

---

### 4.4 Enhanced Alert Manager (`alert_manager.py`)

**Purpose:** Central alert management with intelligent deduplication and routing.

**Key Features:**
- ✅ **Smart Alert Generation**
  - Automatic severity classification (info, warning, critical)
  - Alert deduplication with configurable cooldown
  - Context-rich metadata for each alert
  
- ✅ **Alert Types:**
  - Node disqualified/suspended
  - Critical/low audit scores
  - Suspension risk
  - Low uptime scores
  - Storage capacity warnings
  - High latency alerts
  - Anomaly-based alerts

- ✅ **Alert Management:**
  - Acknowledge alerts
  - Resolve alerts
  - Alert history tracking
  - Active alerts filtering

- ✅ **Real-time Broadcasting:**
  - WebSocket push notifications
  - Browser notifications support
  - Alert badge with count indicator

**Configuration:**
```python
ALERT_EVALUATION_INTERVAL_MINUTES = 5  # How often to check
ALERT_COOLDOWN_MINUTES = 15  # Min time between duplicate alerts
ENABLE_BROWSER_NOTIFICATIONS = True
```

**Alert Thresholds:**
```python
# Reputation
AUDIT_SCORE_WARNING = 85.0
AUDIT_SCORE_CRITICAL = 70.0
SUSPENSION_SCORE_CRITICAL = 60.0
ONLINE_SCORE_WARNING = 95.0

# Storage
STORAGE_WARNING_PERCENT = 80
STORAGE_CRITICAL_PERCENT = 95
STORAGE_FORECAST_WARNING_DAYS = 30
STORAGE_FORECAST_CRITICAL_DAYS = 7

# Performance
LATENCY_WARNING_MS = 5000
LATENCY_CRITICAL_MS = 10000
```

---

### 4.5 Frontend Integration

#### AlertsPanel Component (`AlertsPanel.js`)

**Features:**
- ✅ **Floating Alerts Panel**
  - Toggleable panel in top-right corner
  - Alert badge with count and severity indicators
  - Flash animation for new alerts
  
- ✅ **Tabbed Interface:**
  - **Alerts Tab** - Active alerts requiring attention
  - **Insights Tab** - AI-generated insights and patterns
  
- ✅ **Alert Management:**
  - One-click acknowledge
  - Severity filtering (critical, warning, info)
  - Per-node filtering
  - Automatic refresh every 60 seconds
  
- ✅ **Insights Display:**
  - Confidence scores for AI insights
  - Category-based organization
  - Time range filtering (24h, 3d, 7d)
  - Visual severity indicators

**UI Elements:**
```
🔔 [Badge with count] → Opens panel
  ├── Alerts Tab (filtered by severity)
  │   └── Alert items with acknowledge button
  └── Insights Tab (filtered by timeframe)
      └── Insight items with confidence scores
```

---

### 4.6 Database Schema Updates

**New Tables:**

#### `alerts`
```sql
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    node_name TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,  -- 'info', 'warning', 'critical'
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    acknowledged INTEGER DEFAULT 0,
    acknowledged_at DATETIME,
    resolved INTEGER DEFAULT 0,
    resolved_at DATETIME,
    metadata TEXT  -- JSON
);
```

#### `insights`
```sql
CREATE TABLE insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    node_name TEXT NOT NULL,
    insight_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT,  -- 'reputation', 'storage', 'performance', etc.
    confidence REAL,  -- 0.0 to 1.0
    acknowledged INTEGER DEFAULT 0,
    metadata TEXT  -- JSON
);
```

#### `analytics_baselines`
```sql
CREATE TABLE analytics_baselines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_name TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    window_hours INTEGER NOT NULL,
    mean_value REAL,
    std_dev REAL,
    min_value REAL,
    max_value REAL,
    sample_count INTEGER,
    last_updated DATETIME NOT NULL,
    UNIQUE(node_name, metric_name, window_hours)
);
```

**Indexes:**
- `idx_alerts_node_time` - Fast alert queries per node
- `idx_alerts_active` - Quick active alert lookup
- `idx_alerts_severity` - Severity-based filtering
- `idx_insights_node_time` - Time-based insight queries
- `idx_insights_type` - Type-based insight filtering
- `idx_baselines_node_metric` - Fast baseline lookup

---

### 4.7 WebSocket API Extensions

**New Message Types:**

#### Get Active Alerts
```javascript
// Request
{ 
  type: 'get_active_alerts',
  view: ['Aggregate'] or ['NodeName']
}

// Response
{
  type: 'active_alerts',
  data: [
    {
      id: 1,
      timestamp: '2025-01-15T10:30:00Z',
      node_name: 'My-Node',
      alert_type: 'audit_score_critical',
      severity: 'critical',
      title: 'Critical Audit Score: 68.50%',
      message: 'Audit score on us1 is critically low...',
      metadata: '{"satellite": "us1", "score": 68.5}'
    }
  ]
}
```

#### Acknowledge Alert
```javascript
// Request
{
  type: 'acknowledge_alert',
  alert_id: 1
}

// Response
{
  type: 'alert_acknowledge_result',
  success: true,
  alert_id: 1
}
```

#### Get Insights
```javascript
// Request
{
  type: 'get_insights',
  view: ['Aggregate'],
  hours: 24
}

// Response
{
  type: 'insights_data',
  data: [
    {
      id: 1,
      timestamp: '2025-01-15T10:30:00Z',
      node_name: 'My-Node',
      insight_type: 'storage_forecast_warning',
      severity: 'warning',
      title: 'Storage Capacity Warning',
      description: 'Storage will be full in approximately 25.3 days...',
      category: 'storage',
      confidence: 0.7,
      metadata: '{"days_until_full": 25.3, "growth_rate_gb_per_day": 15.2}'
    }
  ]
}
```

#### New Alert Broadcast
```javascript
// Server → All Clients (when new alert generated)
{
  type: 'new_alert',
  alert: {
    timestamp: '2025-01-15T10:30:00Z',
    node_name: 'My-Node',
    alert_type: 'latency_critical',
    severity: 'critical',
    title: 'Critical Latency: 12000ms',
    message: 'P99 latency is critically high...',
    metadata: {p99_ms: 12000}
  }
}
```

#### Alert Summary
```javascript
// Request
{ type: 'get_alert_summary' }

// Response
{
  type: 'alert_summary',
  data: {
    critical: 2,
    warning: 5,
    info: 3,
    total: 10
  }
}
```

---

## 🔧 Configuration

### Analytics & Detection
```python
# storj_monitor/config.py

# Anomaly Detection
ENABLE_ANOMALY_DETECTION = True
ANOMALY_ZSCORE_THRESHOLD = 3.0  # Threshold for anomaly (3 sigma)
ANOMALY_BASELINE_DAYS = 7  # Historical data for baseline

# Alert System
ALERT_EVALUATION_INTERVAL_MINUTES = 5  # Evaluation frequency
ALERT_COOLDOWN_MINUTES = 15  # Deduplication window
ANALYTICS_BASELINE_UPDATE_HOURS = 24  # Baseline update frequency

# Notifications
ENABLE_BROWSER_NOTIFICATIONS = True
ENABLE_EMAIL_NOTIFICATIONS = False  # Not yet implemented
ENABLE_WEBHOOK_NOTIFICATIONS = False  # Not yet implemented

# Data Retention
DB_ALERTS_RETENTION_DAYS = 90
DB_INSIGHTS_RETENTION_DAYS = 90
DB_ANALYTICS_RETENTION_DAYS = 180
```

---

## 📊 Usage Examples

### Viewing Alerts

1. **Alert Badge** - Shows count of active alerts in header
2. **Click Badge** - Opens alerts panel
3. **Filter Alerts** - Use checkboxes to filter by severity
4. **Acknowledge** - Click ✓ button to acknowledge alert
5. **View Details** - Hover over alert for full context

### Analyzing Insights

1. **Switch to Insights Tab** in alerts panel
2. **Select Time Range** - 24h, 3 days, or 7 days
3. **Review AI-Generated Insights:**
   - Anomaly detections
   - Trend predictions
   - Capacity forecasts
   - Performance recommendations

### Understanding Severity Levels

- **🔴 Critical** - Immediate action required (node at risk)
- **🟡 Warning** - Attention needed (potential issue developing)
- **🔵 Info** - Informational (unusual but not concerning)

---

## 🎨 Visual Indicators

### Alert Badge States
- **Red pulsing** - Critical alerts active
- **Orange** - Warnings only (no critical)
- **Hidden** - No active alerts
- **Flash animation** - New alert just received

### Alert Card Colors
- **Red border** - Critical severity
- **Orange border** - Warning severity
- **Blue border** - Info severity

### Confidence Scores
- **>80%** - High confidence in prediction
- **60-80%** - Moderate confidence
- **<60%** - Low confidence (informational only)

---

## 🚀 Background Tasks

### Alert Evaluation Task
- **Frequency:** Every 5 minutes
- **Actions:**
  - Fetches latest reputation data
  - Fetches latest storage data
  - Analyzes traffic patterns for anomalies
  - Generates insights from analytics
  - Creates alerts when thresholds breached
  - Writes insights to database
  - Broadcasts real-time updates via WebSocket

**Task Flow:**
```
Every 5 minutes:
1. For each node:
   a. Get latest reputation → evaluate → generate alerts
   b. Get latest storage → forecast → generate insights
   c. Analyze traffic → detect anomalies → generate alerts
2. Broadcast updates to connected clients
```

---

## 📈 Metrics Analyzed

### Automatic Baseline Tracking
The system automatically builds baselines for:
- Success rates (download, upload, audit)
- Bandwidth (egress/ingress in Mbps)
- Latency percentiles (p50, p95, p99)
- Error rates by type
- Storage growth rates
- Operation counts

### Anomaly Detection Metrics
- Traffic success rates
- Error patterns and frequency
- Latency spikes
- Bandwidth unusual activity
- Storage growth acceleration

---

## 🔮 Predictive Capabilities

### Storage Forecasting
```
Current: 450 GB used (75%)
Growth Rate: 12.5 GB/day
Forecast: Full in 48 days
Confidence: 75%
```

### Trend Detection
```
Metric: Download Success Rate
Current: 98.2%
Trend: Decreasing (-0.5% per day)
Severity: Warning
```

### Capacity Planning
```
Alert: Storage Warning
Days Until Full: 25.3
Recommended Action: Add capacity within 14 days
Growth Acceleration: Normal
```

---

## 🛠️ API Functions

### Analytics Engine Methods
```python
# Calculate baseline for a metric
await analytics.calculate_baseline(
    node_name='My-Node',
    metric_name='success_rate',
    values=[0.98, 0.97, 0.99, ...],
    window_hours=168  # 7 days
)

# Get stored baseline
baseline = await analytics.get_baseline(
    node_name='My-Node',
    metric_name='success_rate',
    window_hours=168
)

# Calculate Z-score
z_score = analytics.calculate_z_score(
    value=0.85,
    baseline=baseline
)

# Detect trend
trend, slope = analytics.detect_trend(
    values=[1.0, 1.1, 1.2, 1.3, ...]
)

# Forecast future value
forecast = analytics.forecast_linear(
    values=[(time1, val1), (time2, val2), ...],
    forecast_hours=24
)
```

### Anomaly Detector Methods
```python
# Detect anomaly for a metric
anomaly = await anomaly_detector.detect_anomalies(
    node_name='My-Node',
    metric_name='latency_p99',
    current_value=8500.0,
    window_hours=168
)

# Analyze traffic for anomalies
anomalies = await anomaly_detector.detect_traffic_anomalies(
    node_name='My-Node',
    recent_events=[event1, event2, ...]
)

# Get recent anomalies from cache
recent = anomaly_detector.get_recent_anomalies(
    node_name='My-Node',
    minutes=60
)
```

### Alert Manager Methods
```python
# Generate alert
alert = await alert_manager.generate_alert(
    node_name='My-Node',
    alert_type='storage_critical',
    severity='critical',
    title='Storage Critical: 97.5% Full',
    message='Storage is critically full...',
    metadata={'used_percent': 97.5}
)

# Acknowledge alert
success = await alert_manager.acknowledge_alert(alert_id=1)

# Get active alerts
alerts = await alert_manager.get_active_alerts(
    node_names=['My-Node', 'Node2']
)

# Get alert summary
summary = alert_manager.get_alert_summary()
# Returns: {critical: 2, warning: 5, info: 3, total: 10}
```

---

## 📝 Database Functions

```python
# Write alert
from storj_monitor.database import blocking_write_alert
blocking_write_alert(DATABASE_FILE, alert_dict)

# Get active alerts
from storj_monitor.database import blocking_get_active_alerts
alerts = blocking_get_active_alerts(DATABASE_FILE, ['Node1'])

# Acknowledge alert
from storj_monitor.database import blocking_acknowledge_alert
success = blocking_acknowledge_alert(DATABASE_FILE, alert_id)

# Write insight
from storj_monitor.database import blocking_write_insight
blocking_write_insight(DATABASE_FILE, insight_dict)

# Get insights
from storj_monitor.database import blocking_get_insights
insights = blocking_get_insights(DATABASE_FILE, ['Node1'], hours=24)

# Update baseline
from storj_monitor.database import blocking_update_baseline
blocking_update_baseline(
    DATABASE_FILE, 'Node1', 'metric_name', 168, stats_dict
)

# Get baseline
from storj_monitor.database import blocking_get_baseline
baseline = blocking_get_baseline(
    DATABASE_FILE, 'Node1', 'metric_name', 168
)
```

---

## 🎯 Alert Types Reference

| Alert Type | Severity | Threshold | Description |
|-----------|----------|-----------|-------------|
| `node_disqualified` | Critical | - | Node permanently disqualified |
| `node_suspended` | Critical | - | Node temporarily suspended |
| `audit_score_critical` | Critical | <70% | Risk of disqualification |
| `audit_score_warning` | Warning | <85% | Monitor closely |
| `suspension_risk` | Critical | <60% | May be suspended soon |
| `uptime_warning` | Warning | <95% | Connectivity issues |
| `storage_critical` | Critical | ≥95% | Disk critically full |
| `storage_warning` | Warning | ≥80% | Approaching capacity |
| `storage_forecast_critical` | Critical | <7 days | Full within week |
| `storage_forecast_warning` | Warning | <30 days | Full within month |
| `latency_critical` | Critical | ≥10000ms | P99 extremely high |
| `latency_warning` | Warning | ≥5000ms | P99 elevated |
| `latency_spike` | Warning/Critical | Z>3 | Unusual latency |
| `traffic_anomaly` | Warning/Critical | Z>3 | Abnormal traffic pattern |
| `error_pattern` | Warning | >10% | High error rate |
| `bandwidth_spike` | Info | Z>3 | Unusual bandwidth |
| `bandwidth_drop` | Warning | Z<-3 | Unusually low bandwidth |

---

## 🔄 System Integration

### Startup Sequence
1. Database schema validated (new tables created if needed)
2. Background tasks initialized
3. API clients started (Phase 1-3 features)
4. **Alert evaluation task started** (Phase 4)
   - Waits 30 seconds for system stabilization
   - Initializes analytics engine
   - Initializes anomaly detector
   - Initializes alert manager
   - Begins 5-minute evaluation loop

### Evaluation Loop
```
Every 5 minutes:
├── For each node:
│   ├── Fetch latest reputation data
│   │   ├── Evaluate against thresholds
│   │   ├── Generate alerts if needed
│   │   └── Analyze health trends
│   ├── Fetch latest storage data
│   │   ├── Check capacity thresholds
│   │   ├── Calculate growth rate
│   │   ├── Forecast days until full
│   │   └── Generate insights/alerts
│   └── Analyze recent traffic
│       ├── Detect anomalies
│       ├── Identify error patterns
│       └── Generate alerts if significant
└── Broadcast updates to WebSocket clients
```

---

## 🎓 Best Practices

### Alert Management
1. **Acknowledge alerts promptly** to reduce noise
2. **Review insights daily** for proactive maintenance
3. **Adjust thresholds** based on your node's characteristics
4. **Enable browser notifications** for critical alerts only

### Capacity Planning
1. Monitor **storage forecast warnings** (30-day horizon)
2. Plan capacity upgrades **before critical alerts** (7-day horizon)
3. Review **growth rate trends** monthly
4. Consider seasonal variations in traffic

### Performance Monitoring
1. Investigate **latency spikes** immediately
2. Check system resources when **anomalies detected**
3. Review **error patterns** for network issues
4. Monitor **bandwidth anomalies** for traffic changes

---

## 🐛 Troubleshooting

### Alerts Not Appearing
1. Check alert evaluation task is running (logs: `[ALERT_EVAL]`)
2. Verify `ENABLE_ANOMALY_DETECTION = True` in config
3. Ensure node API connection is working
4. Check database permissions

### False Positives
1. Increase `ANOMALY_ZSCORE_THRESHOLD` (default: 3.0)
2. Increase `ALERT_COOLDOWN_MINUTES` (default: 15)
3. Wait for more baseline data (7+ days)
4. Review and adjust severity thresholds

### Missing Insights
1. Verify sufficient historical data (>24 hours)
2. Check database has reputation/storage data
3. Review logs for analytics errors
4. Ensure alert evaluation task running

### Performance Impact
1. Adjust `ALERT_EVALUATION_INTERVAL_MINUTES` (default: 5)
2. Reduce `ANOMALY_BASELINE_DAYS` if database large
3. Monitor `[ALERT_EVAL]` task duration in logs
4. Check database query performance

---

## 📚 Next Steps (Future Enhancements)

### Phase 4.5: Notification Channels (Planned)
- [ ] Email notifications via SMTP
- [ ] Discord webhook integration
- [ ] Slack webhook integration
- [ ] Custom webhook endpoints
- [ ] SMS notifications (Twilio)
- [ ] Notification scheduling/quiet hours

### Phase 4.6: Advanced Analytics (Planned)
- [ ] Machine learning anomaly detection
- [ ] Seasonal pattern recognition
- [ ] Multi-metric correlation analysis
- [ ] Custom metric definitions
- [ ] Alert rule customization UI

### Phase 4.7: Reporting (Planned)
- [ ] Weekly summary reports
- [ ] Monthly performance reports
- [ ] Custom report generation
- [ ] PDF export functionality
- [ ] Historical comparison views

---

## 📖 Related Documentation

- [`PHASE_1_COMPLETE.md`](PHASE_1_COMPLETE.md) - Foundation & API Integration
- [`PHASE_2_COMPLETE.md`](PHASE_2_COMPLETE.md) - Performance & Capacity Monitoring
- [`PHASE_3_COMPLETE.md`](PHASE_3_COMPLETE.md) - Frontend UI Components
- [`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md) - Overall project plan
- [`ARCHITECTURE_DIAGRAM.md`](ARCHITECTURE_DIAGRAM.md) - System architecture

---

## 🎉 Summary

Phase 4 transforms the Storj Node Monitor from a **reactive** monitoring tool into a **proactive** intelligence platform. The system now:

✅ Automatically detects anomalies using statistical methods  
✅ Predicts future issues before they become critical  
✅ Generates intelligent alerts with context  
✅ Provides AI-powered insights and recommendations  
✅ Learns from historical data to improve accuracy  
✅ Delivers real-time notifications to operators  

**Result:** Operators can now prevent issues instead of just reacting to them, leading to higher uptime, better reputation scores, and more efficient operations.

---

**Implementation Complete!** 🚀