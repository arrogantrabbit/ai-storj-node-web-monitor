# Implementation Roadmap - Technical Monitoring First
## Prioritized Development Plan for Storj Node Monitor

**Last Updated:** 2025-01-04  
**Priority Philosophy:** Technical monitoring and operational reliability before financial tracking

---

## Priority Overview

```
PRIORITY          FEATURE                              RATIONALE
========          =======                              =========
🔴 CRITICAL       Extended Node Config Parsing         Foundation for all enhancements
🔴 CRITICAL       Reputation Monitoring                Prevents suspension/disqualification
🟠 HIGH           Latency Analytics                    Identifies performance issues
🟠 HIGH           API Client Infrastructure            Enables all API-based features
🟡 MEDIUM         Storage Capacity Tracking            Prevents downtime from full disks
🟢 LOW            Financial Tracking                   Nice-to-have, not operational
🔵 FUTURE         Predictive Analytics                 Advanced insights
🔵 FUTURE         Alert & Notification System          Proactive notifications
```

---

## Phase 1: Foundation & Critical Operations (Weeks 1-4)

### Goal
Establish the infrastructure for enhanced monitoring and prevent node suspension/disqualification.

### Deliverables

#### 1.1 Extended Node Configuration Parsing (Week 1)
**Priority:** 🔴 CRITICAL - Foundation for everything else

**Tasks:**
- [ ] Update command-line argument parsing in [`__main__.py`](__main__.py)
- [ ] Implement `parse_node_config()` function
- [ ] Add auto-discovery logic for API endpoints
- [ ] Update help text and documentation
- [ ] Add validation and error handling

**Files to Modify:**
- `storj_monitor/__main__.py` - Argument parsing
- `storj_monitor/config.py` - Add API-related config options

**Testing:**
```bash
# Test cases
storj_monitor --node "Local:/var/log/node.log"
storj_monitor --node "Local:/var/log/node.log:http://localhost:14002"
storj_monitor --node "Remote:192.168.1.100:9999:http://192.168.1.100:14002"
storj_monitor --node "Node1:/var/log/n1.log" --node "Node2:192.168.1.50:9999"
```

**Acceptance Criteria:**
- ✓ Backward compatible with existing syntax
- ✓ Auto-discovery works for localhost
- ✓ Graceful handling of missing API
- ✓ Clear error messages for invalid syntax

**Estimated Effort:** 3-4 days

---

#### 1.2 API Client Infrastructure (Week 1-2)
**Priority:** 🟠 HIGH - Required for all API-based features

**Tasks:**
- [ ] Create [`storj_api_client.py`](storj_api_client.py)
- [ ] Implement `StorjNodeAPIClient` class
- [ ] Add connection testing and health checks
- [ ] Create background polling task
- [ ] Integrate with startup/shutdown sequence

**New Module:** `storj_monitor/storj_api_client.py`

```python
class StorjNodeAPIClient:
    """Client for Storj Node API at localhost:14002"""
    
    # Methods:
    - async start() - Initialize and test connectivity
    - async stop() - Clean up resources
    - async get_dashboard() - Get general stats
    - async get_satellites() - Get per-satellite data
    - async get_satellite_info(sat_id) - Detailed satellite info
    - async get_estimated_payout() - Earnings data
```

**Files to Modify:**
- `storj_monitor/tasks.py` - Add API polling task
- `storj_monitor/server.py` - Initialize API clients

**Testing:**
- [ ] Test with real node API
- [ ] Test connection failure scenarios
- [ ] Test timeout handling
- [ ] Test with multiple nodes

**Acceptance Criteria:**
- ✓ Connects to node API successfully
- ✓ Handles connection failures gracefully
- ✓ Supports multiple concurrent clients
- ✓ Minimal performance impact (<2% CPU)

**Estimated Effort:** 4-5 days

---

#### 1.3 Reputation Monitoring (Week 2-3)
**Priority:** 🔴 CRITICAL - Prevents node suspension/disqualification

**Tasks:**
- [ ] Create [`reputation_tracker.py`](reputation_tracker.py)
- [ ] Implement reputation data polling
- [ ] Create database schema for reputation history
- [ ] Add reputation score calculations
- [ ] Implement threshold-based alerts
- [ ] Create UI card for reputation display

**New Module:** `storj_monitor/reputation_tracker.py`

```python
async def track_reputation(app, node_name: str, api_client):
    """Poll reputation scores and detect issues"""
    
    # Fetch from API
    satellites = await api_client.get_satellites()
    
    # Extract scores per satellite
    for sat_id, sat_data in satellites.items():
        audit_score = sat_data['audit']['score']
        suspension_score = sat_data['suspension']['score']
        online_score = sat_data['online']['score']
        
        # Store in database
        # Check thresholds
        # Generate alerts if needed
```

**Database Schema:**
```sql
CREATE TABLE reputation_history (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME,
    node_name TEXT,
    satellite TEXT,
    audit_score REAL,
    suspension_score REAL,
    online_score REAL,
    INDEX idx_node_time (node_name, timestamp)
);
```

**UI Component:** `ReputationCard.js`
- Display current scores per satellite
- Color-coded indicators (green/yellow/red)
- Trend arrows (↑ improving, → stable, ↓ declining)
- Warning messages for low scores

**Alert Thresholds:**
```python
AUDIT_SCORE_WARNING = 85.0      # Yellow warning
AUDIT_SCORE_CRITICAL = 70.0     # Red alert
SUSPENSION_SCORE_CRITICAL = 60.0  # Red alert - risk of suspension
ONLINE_SCORE_WARNING = 95.0
```

**Testing:**
- [ ] Verify score tracking accuracy
- [ ] Test alert triggering
- [ ] Validate historical data storage
- [ ] Test with multiple satellites

**Acceptance Criteria:**
- ✓ Reputation scores displayed accurately
- ✓ Historical trends visible
- ✓ Alerts trigger at correct thresholds
- ✓ Works with multiple nodes simultaneously

**Estimated Effort:** 5-6 days

---

#### 1.4 Basic Alert System (Week 4)
**Priority:** 🟠 HIGH - Required for reputation alerts

**Tasks:**
- [ ] Create [`alert_manager.py`](alert_manager.py) (basic version)
- [ ] Implement alert evaluation logic
- [ ] Add browser notification support
- [ ] Create alerts UI panel
- [ ] Database schema for alert history

**Database Schema:**
```sql
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME,
    node_name TEXT,
    alert_type TEXT,  -- 'reputation', 'storage', 'performance'
    severity TEXT,    -- 'info', 'warning', 'critical'
    title TEXT,
    message TEXT,
    acknowledged BOOLEAN DEFAULT FALSE,
    INDEX idx_active (acknowledged, timestamp)
);
```

**UI Component:** Basic alerts badge in header
- Show count of active alerts
- Click to view alert panel
- Simple list of current warnings

**Estimated Effort:** 3-4 days

---

### Phase 1 Summary

**Total Duration:** 4 weeks  
**Total Effort:** ~17-19 days of focused development

**Key Outcomes:**
- ✅ Infrastructure ready for all enhanced features
- ✅ Node operators can monitor reputation scores
- ✅ Basic alerting prevents critical issues
- ✅ Foundation for subsequent phases

---

## Phase 2: Performance & Capacity Monitoring (Weeks 5-8)

### Goal
Identify performance issues and prevent capacity-related outages.

### Deliverables

#### 2.1 Latency Analytics (Week 5-6)
**Priority:** 🟠 HIGH - Identifies performance bottlenecks

**Tasks:**
- [ ] Create [`performance_analyzer.py`](performance_analyzer.py)
- [ ] Extract duration data from existing events
- [ ] Calculate percentile metrics (p50, p95, p99)
- [ ] Detect slow operations
- [ ] Add latency visualization

**Implementation:**
```python
# Already have duration data in logs!
# "duration":"1m37.535505102s"
# Currently parsed but not stored

# Modify database schema
ALTER TABLE events ADD COLUMN duration_ms INTEGER;

# Calculate percentiles
def calculate_percentiles(durations: List[int]) -> dict:
    sorted_durations = sorted(durations)
    return {
        'p50': percentile(sorted_durations, 50),
        'p95': percentile(sorted_durations, 95),
        'p99': percentile(sorted_durations, 99)
    }
```

**UI Component:** `LatencyCard.js`
- Display p50/p95/p99 metrics
- Histogram of latency distribution
- List of top 10 slowest recent operations
- Color-coded performance indicators

**Performance Thresholds:**
```python
LATENCY_GOOD_MS = 1000      # < 1s (green)
LATENCY_OK_MS = 5000        # 1-5s (yellow)
LATENCY_SLOW_MS = 10000     # > 10s (red, alert)
```

**Testing:**
- [ ] Verify percentile calculations
- [ ] Test with various operation types
- [ ] Validate slow operation detection

**Acceptance Criteria:**
- ✓ Accurate latency metrics displayed
- ✓ Slow operations identified and alerted
- ✓ Historical latency trends visible
- ✓ Minimal performance impact on processing

**Estimated Effort:** 5-6 days

---

#### 2.2 Storage Capacity Tracking (Week 6-7)
**Priority:** 🟡 MEDIUM - Prevents downtime from full disks

**Tasks:**
- [ ] Create [`storage_tracker.py`](storage_tracker.py)
- [ ] Poll capacity data from node API
- [ ] Implement growth rate calculation
- [ ] Add capacity forecasting
- [ ] Create storage health visualization

**New Module:** `storj_monitor/storage_tracker.py`

```python
async def track_storage(app, node_name: str, api_client):
    """Monitor disk capacity and forecast growth"""
    
    dashboard = await api_client.get_dashboard()
    satellites = await api_client.get_satellites()
    
    # Extract capacity data
    total_space = dashboard['diskSpace']
    used_space = dashboard['diskSpaceUsed']
    available_space = dashboard['diskSpaceAvailable']
    trash_space = dashboard['diskSpaceTrash']
    
    # Calculate growth rate (linear regression on recent data)
    growth_rate_gb_per_day = calculate_growth_rate(node_name)
    
    # Forecast when disk will be full
    days_until_full = forecast_capacity_exhaustion(
        available_space, growth_rate_gb_per_day
    )
    
    # Store snapshot
    # Check thresholds
    # Generate alerts
```

**Database Schema:**
```sql
CREATE TABLE storage_snapshots (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME,
    node_name TEXT,
    satellite TEXT,
    used_bytes INTEGER,
    available_bytes INTEGER,
    trash_bytes INTEGER,
    INDEX idx_node_time (node_name, timestamp)
);
```

**UI Component:** `StorageHealthCard.js`
- Capacity gauge (used/trash/free)
- Per-satellite breakdown
- Growth rate indicator
- Days-until-full forecast
- Historical usage chart

**Alert Thresholds:**
```python
STORAGE_WARNING_PERCENT = 80    # 80% full
STORAGE_CRITICAL_PERCENT = 95   # 95% full
FORECAST_WARNING_DAYS = 30      # Alert if full within 30 days
FORECAST_CRITICAL_DAYS = 7      # Critical if full within 7 days
```

**Testing:**
- [ ] Verify capacity calculations
- [ ] Test growth rate forecasting
- [ ] Validate alert triggers

**Acceptance Criteria:**
- ✓ Current capacity displayed accurately
- ✓ Growth forecast within ±10% accuracy
- ✓ Alerts generated at appropriate thresholds
- ✓ Historical trends visible

**Estimated Effort:** 5-6 days

---

#### 2.3 Enhanced Performance Charts (Week 8)
**Priority:** 🟡 MEDIUM - Better operational visibility

**Tasks:**
- [ ] Add latency metrics to performance chart
- [ ] Create latency histogram visualization
- [ ] Add operation count by latency bucket
- [ ] Improve chart interactivity

**UI Updates:**
- Add "Latency" view to existing performance chart
- New chart: Latency distribution histogram
- Table: Slowest operations with details

**Estimated Effort:** 3-4 days

---

### Phase 2 Summary

**Total Duration:** 4 weeks  
**Total Effort:** ~13-16 days

**Key Outcomes:**
- ✅ Performance issues identified and tracked
- ✅ Capacity planning enabled
- ✅ Proactive alerting before problems occur
- ✅ Comprehensive operational monitoring

---

## Phase 3: Financial Tracking (Weeks 9-11)

### Goal
Provide earnings visibility and payout forecasting (lower priority).

### Deliverables

#### 3.1 Financial Tracking (Week 9-10)
**Priority:** 🟢 LOW - Nice-to-have, not critical for operations

**Tasks:**
- [ ] Create [`financial_tracker.py`](financial_tracker.py)
- [ ] Implement earnings calculations
- [ ] Create pricing configuration
- [ ] Add payout estimation
- [ ] Historical earnings tracking

**Why Lower Priority:**
- Doesn't prevent outages
- Doesn't affect node health
- Can be manually calculated if needed
- Requires regular pricing updates

**Implementation Strategy:**
```python
# Pricing configuration (manual updates required)
PRICING = {
    'egress_per_tb': 7.00,
    'storage_per_tb_month': 1.50,
    'repair_per_tb': 10.00,
    'audit_per_tb': 10.00
}

# Calculate from existing traffic data
def calculate_earnings(traffic_data, pricing):
    egress_earnings = (traffic_data['egress_bytes'] / TB) * pricing['egress_per_tb']
    # ... similar for other categories
    return total_earnings
```

**Database Schema:**
```sql
CREATE TABLE earnings_estimates (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME,
    node_name TEXT,
    satellite TEXT,
    period TEXT,  -- 'day', 'month'
    egress_earnings REAL,
    storage_earnings REAL,
    repair_earnings REAL,
    audit_earnings REAL
);
```

**UI Component:** `EarningsCard.js`
- Current month estimate
- Per-satellite breakdown
- Historical trend chart
- Payout prediction

**Testing:**
- [ ] Verify calculation accuracy
- [ ] Compare with actual payouts
- [ ] Test with multiple nodes

**Acceptance Criteria:**
- ✓ Earnings displayed per satellite
- ✓ Monthly estimates within ±10% of actual
- ✓ Historical trends visible

**Estimated Effort:** 6-7 days

---

#### 3.2 Payout Schedule Tracking (Week 11)
**Priority:** 🟢 LOW

**Tasks:**
- [ ] Add payout date tracking
- [ ] Historical payout records
- [ ] Accuracy tracking

**Estimated Effort:** 2-3 days

---

### Phase 3 Summary

**Total Duration:** 3 weeks  
**Total Effort:** ~8-10 days

**Key Outcomes:**
- ✅ Earnings visibility for node operators
- ✅ Payout forecasting
- ✅ Financial trend analysis

---

## Phase 4: Intelligence & Advanced Features (Weeks 12-16)

### Goal
Add predictive capabilities and comprehensive alerting.

### Deliverables

#### 4.1 Anomaly Detection (Week 12-13)
**Priority:** 🔵 FUTURE

**Tasks:**
- [ ] Create [`anomaly_detector.py`](anomaly_detector.py)
- [ ] Implement statistical analysis
- [ ] Pattern recognition algorithms
- [ ] Anomaly classification

**Estimated Effort:** 5-6 days

---

#### 4.2 Predictive Analytics (Week 14)
**Priority:** 🔵 FUTURE

**Tasks:**
- [ ] Create [`forecasting.py`](forecasting.py)
- [ ] Traffic forecasting
- [ ] Trend analysis
- [ ] Smart recommendations

**Estimated Effort:** 4-5 days

---

#### 4.3 Comprehensive Alert System (Week 15-16)
**Priority:** 🔵 FUTURE

**Tasks:**
- [ ] Enhance [`alert_manager.py`](alert_manager.py)
- [ ] Email notification support
- [ ] Webhook integration (Discord/Slack)
- [ ] Alert configuration UI
- [ ] Alert history and acknowledgment

**Estimated Effort:** 5-6 days

---

### Phase 4 Summary

**Total Duration:** 5 weeks  
**Total Effort:** ~14-17 days

**Key Outcomes:**
- ✅ Intelligent insights
- ✅ Proactive problem detection
- ✅ Multi-channel notifications

---

## Complete Timeline Overview

```
Week  Phase  Focus                          Key Deliverables
====  =====  =====                          ================
1-2   1      Foundation                     Node config parsing, API client
3-4   1      Critical Operations            Reputation monitoring, basic alerts
5-6   2      Performance                    Latency analytics
7-8   2      Capacity                       Storage tracking, enhanced charts
9-11  3      Financial (Low Priority)       Earnings tracking, payouts
12-16 4      Intelligence (Future)          Anomaly detection, predictions, alerts

Total: 16 weeks (~4 months) for complete implementation
```

---

## Effort Summary by Priority

| Priority | Total Effort | Features |
|----------|--------------|----------|
| 🔴 CRITICAL | ~11-13 days | Config parsing, API client, Reputation monitoring |
| 🟠 HIGH | ~15-18 days | Latency analytics, Basic alerts |
| 🟡 MEDIUM | ~8-10 days | Storage tracking, Enhanced charts |
| 🟢 LOW | ~8-10 days | Financial tracking |
| 🔵 FUTURE | ~14-17 days | Anomaly detection, Predictions, Advanced alerts |

**Total Development Effort:** ~56-68 days (~3-3.5 months of focused work)

---

## Quick Start - Minimum Viable Enhancement (2 weeks)

For rapid deployment of the most critical features:

### Week 1: Foundation
1. Extended node config parsing (3 days)
2. API client infrastructure (4 days)

### Week 2: Reputation
3. Reputation monitoring (5 days)
4. Basic alert UI (2 days)

**Result:** Node operators can monitor reputation scores and prevent suspension - the single most critical operational concern.

---

## Dependencies & Prerequisites

### Technical Dependencies
- Python 3.9+
- aiohttp (for API client)
- SQLite 3.x
- Existing dashboard dependencies

### Infrastructure
- Access to node API (localhost:14002 by default)
- No additional external services required
- Minimal resource overhead (<5% CPU increase)

### Data Requirements
- Log files (existing)
- Node API access (new)
- GeoIP database (existing)

---

## Risk Mitigation

### Technical Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| API changes by Storj Labs | High | Version detection, graceful degradation |
| Performance degradation | Medium | Async operations, caching, benchmarking |
| Database growth | Medium | Aggressive pruning, configurable retention |

### Operational Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| False alerts | Medium | Tunable thresholds, smart grouping |
| API unavailability | Low | Graceful fallback to log-only mode |
| Configuration complexity | Low | Auto-discovery, good defaults |

---

## Testing Strategy

### Unit Tests
- [ ] Node config parsing
- [ ] API client methods
- [ ] Reputation calculations
- [ ] Latency percentiles
- [ ] Storage forecasting
- [ ] Alert evaluation logic

### Integration Tests
- [ ] Multi-node scenarios
- [ ] API connection handling
- [ ] Database operations
- [ ] WebSocket updates
- [ ] UI component rendering

### Performance Tests
- [ ] API polling overhead
- [ ] Database query performance
- [ ] Memory usage with large datasets
- [ ] Concurrent node handling

### User Acceptance Tests
- [ ] End-to-end monitoring workflows
- [ ] Alert notification paths
- [ ] Configuration changes
- [ ] Dashboard responsiveness

---

## Documentation Updates

### For Each Phase

#### User Documentation
- [ ] Updated README.md with new features
- [ ] Configuration guide
- [ ] Feature explanations
- [ ] Troubleshooting guide

#### Developer Documentation
- [ ] Architecture updates
- [ ] API client documentation
- [ ] Database schema changes
- [ ] Contributing guide

---

## Success Metrics

### Phase 1 Success Criteria
- [ ] 100% backward compatibility maintained
- [ ] API auto-discovery success rate >95%
- [ ] Reputation scores match node API exactly
- [ ] Alerts trigger within 1 minute of threshold breach

### Phase 2 Success Criteria
- [ ] Latency percentiles accurate within ±5%
- [ ] Storage forecast accurate within ±10%
- [ ] Performance overhead <5% CPU increase
- [ ] Slow operation detection >99% accuracy

### Phase 3 Success Criteria
- [ ] Earnings estimates within ±10% of actual payouts
- [ ] Monthly forecast accuracy >90%
- [ ] Historical data consistency 100%

### Phase 4 Success Criteria
- [ ] Anomaly detection false positive rate <5%
- [ ] Alert notification delivery >99.9%
- [ ] User satisfaction score >4.5/5

---

## Deployment Strategy

### Rolling Deployment
1. Deploy to test environment
2. Run for 1 week with test nodes
3. Deploy to production (single node)
4. Monitor for 2-3 days
5. Gradual rollout to all nodes

### Feature Flags
```python
# config.py
ENABLE_REPUTATION_MONITORING = True
ENABLE_LATENCY_ANALYTICS = True
ENABLE_STORAGE_TRACKING = True
ENABLE_FINANCIAL_TRACKING = False  # Deploy later
ENABLE_ANOMALY_DETECTION = False   # Deploy later
```

### Rollback Plan
- Database migrations are backward compatible
- Feature flags allow instant disable
- Previous version available for quick rollback
- Data export before major updates

---

## Next Steps

### Immediate Actions (This Week)
1. ✅ Review and approve this roadmap
2. ✅ Set up development environment
3. ✅ Create feature branch
4. ✅ Begin Phase 1.1 - Node config parsing

### Short Term (Next 2 Weeks)
5. Complete Phase 1 foundation
6. Test with real nodes
7. Document API integration patterns

### Medium Term (Next Month)
8. Complete reputation monitoring
9. Deploy to test environment
10. Begin latency analytics

---

## Questions & Decisions

### Open Questions
- [ ] Should we support config file in addition to CLI args?
- [ ] Email SMTP server configuration approach?
- [ ] Alert notification preferences per user?
- [ ] Historical data retention policies?

### Decisions Made
- ✅ Technical monitoring prioritized over financial
- ✅ Extended node syntax with optional API endpoint
- ✅ Auto-discovery for localhost
- ✅ Graceful degradation for missing API
- ✅ Phase-based rollout over big-bang deployment

---

## Conclusion

This roadmap provides a clear, prioritized path forward with:

1. **Critical First:** Foundation and reputation monitoring prevent operational failures
2. **Performance Second:** Latency and capacity tracking optimize operations
3. **Financial Last:** Earnings tracking is valuable but not critical
4. **Intelligence Future:** Advanced features add long-term value

**Estimated Timeline:** 16 weeks for complete implementation  
**Minimum Viable:** 2 weeks for reputation monitoring  
**Recommended Start:** Phase 1 - Foundation (4 weeks)

The phased approach allows for:
- Early value delivery
- Risk mitigation through incremental changes
- User feedback incorporation
- Iterative improvement

**Ready to begin? Start with Phase 1.1! 🚀**