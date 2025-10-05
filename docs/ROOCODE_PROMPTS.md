# RooCode Implementation Prompts
## Ready-to-Use Prompts for Each Phase

**Purpose:** Copy-paste these prompts into Code mode to implement each phase of the roadmap.

**Prerequisites:** 
- Review [`docs/PHASE_5_TO_11_ROADMAP.md`](PHASE_5_TO_11_ROADMAP.md) before starting
- Ensure you're in Code mode (`/mode code`)
- Work through phases sequentially for best results

---

## Phase 5: Financial Tracking Backend

### 5.1 Initial Setup & Configuration

```
Create the financial tracking infrastructure for the Storj Node Monitor:

1. Add financial tracking configuration to storj_monitor/config.py:
   - Enable flag ENABLE_FINANCIAL_TRACKING = True
   - Pricing configuration: PRICING_EGRESS_PER_TB = 7.00, PRICING_STORAGE_PER_TB_MONTH = 1.50, etc.
   - Operator share percentages: OPERATOR_SHARE_EGRESS = 0.45, OPERATOR_SHARE_STORAGE = 0.50, etc.
   - Held amount percentages: HELD_AMOUNT_MONTHS_1_TO_3 = 0.75, HELD_AMOUNT_MONTHS_4_TO_6 = 0.50, etc.
   - Optional cost tracking: NODE_MONTHLY_COSTS dictionary

2. Update storj_monitor/database.py to add earnings_estimates table:
   CREATE TABLE earnings_estimates (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       timestamp DATETIME NOT NULL,
       node_name TEXT NOT NULL,
       satellite TEXT NOT NULL,
       period TEXT NOT NULL,
       egress_bytes INTEGER, egress_earnings_gross REAL, egress_earnings_net REAL,
       storage_bytes_hour INTEGER, storage_earnings_gross REAL, storage_earnings_net REAL,
       repair_bytes INTEGER, repair_earnings_gross REAL, repair_earnings_net REAL,
       audit_bytes INTEGER, audit_earnings_gross REAL, audit_earnings_net REAL,
       total_earnings_gross REAL, total_earnings_net REAL, held_amount REAL,
       node_age_months INTEGER, held_percentage REAL
   );

3. Add payout_history table and database functions as shown in docs/PHASE_5_TO_11_ROADMAP.md.
```

### 5.2 Financial Tracker Module

```
Create storj_monitor/financial_tracker.py implementing the FinancialTracker class:

1. Implement earnings calculation methods:
   - get_api_earnings() - Fetch from /api/sno/estimated-payout
   - calculate_from_traffic() - Calculate from database events as fallback
   - calculate_storage_earnings() - GB-hours method using storage snapshots
   - calculate_held_percentage() - Based on node age (months 1-3: 75%, 4-6: 50%, etc.)

2. Create background polling function:
   - track_earnings() - Main polling function
   - calculate_monthly_earnings() - Current month estimates
   - forecast_payout() - Month-end forecast with confidence score

3. Use configuration from storj_monitor/config.py for all pricing calculations.

Example calculation logic:
- Storage earnings = (total_gb_hours / (1024 * 720)) * PRICING_STORAGE_PER_TB_MONTH * OPERATOR_SHARE_STORAGE
- Held amount = total_gross * held_percentage (based on node age)
- Fetch actual data from API endpoint /api/sno/estimated-payout when available
```

### 5.3 Integration & Background Tasks

```
Integrate financial tracking into the application:

1. Update storj_monitor/tasks.py:
   - Add financial_tracking_task() that polls every 5 minutes
   - Initialize FinancialTracker instance
   - Poll earnings from API for each node with api_client
   - Store estimates in database
   - Broadcast updates via WebSocket

2. Update storj_monitor/server.py:
   - Add WebSocket handler for "get_earnings_data" message type
   - Add handler for "get_earnings_history" message type
   - Return earnings data with breakdown by category (egress, storage, repair, audit)
   - Include forecast and confidence scores

3. Ensure proper startup/shutdown integration in existing task management.

WebSocket response format:
{
  "type": "earnings_data",
  "data": [{
    "node_name": "My-Node",
    "satellite": "us1",
    "total_net": 45.67,
    "total_gross": 52.50,
    "held_amount": 6.83,
    "breakdown": {"egress": 25.30, "storage": 15.20, "repair": 3.10, "audit": 2.07},
    "forecast_month_end": 78.42,
    "confidence": 0.85
  }]
}
```

---

## Phase 6: Financial Tracking Frontend

### 6.1 EarningsCard HTML Structure

```
Create the EarningsCard UI component in storj_monitor/static/index.html:

1. Add earnings card after the alerts panel (around line 165):
   - Card header with period selector (current/previous/12 months)
   - Earnings summary section with 4 stat boxes (total, forecast, held, days until payout)
   - Breakdown section showing egress/storage/repair/audit with amounts and percentages
   - Per-satellite earnings list
   - Chart container for historical earnings (canvas id="earnings-chart")

2. Make card visibility controlled by show-earnings checkbox option.

Structure should match existing cards with header, stats grid, breakdown list, and chart canvas.
```

### 6.2 Earnings Charts

```
Add earnings visualization functions to storj_monitor/static/js/charts.js:

1. Create createEarningsHistoryChart():
   - Line chart with 3 datasets: net earnings (green), held amount (orange), gross earnings (blue dashed)
   - Time series x-axis showing months
   - Y-axis formatted as currency ($)
   - Responsive with tooltips

2. Create createEarningsBreakdownChart():
   - Doughnut chart showing egress/storage/repair/audit distribution
   - Color-coded (blue/green/orange/purple)
   - Percentage labels in tooltips

Charts should follow existing Chart.js patterns in the codebase, with responsive: true, maintainAspectRatio: false, and appropriate tooltips.
```

### 6.3 Earnings Component Logic

```
Implement earnings card functionality in storj_monitor/static/js/app.js:

1. Add earnings state management:
   - Initialize charts in initEarningsCard()
   - Request data with requestEarningsData(period)
   - Handle WebSocket responses in updateEarningsCard(data)
   - Calculate aggregate earnings across satellites
   - Update summary stats, breakdown, and charts

2. Implement helper functions:
   - aggregateEarnings() - Sum across satellites
   - calculateDaysUntilPayout() - Based on payout day (10th of month)
   - updateEarningsBreakdown() - Update category display
   - updateSatelliteEarnings() - Show per-satellite breakdown

3. Handle period selector changes to refresh data.

Follow existing patterns in app.js for WebSocket message handling and state management.
```

### 6.4 Earnings Card Styling

```
Add earnings card styles to storj_monitor/static/css/style.css:

1. Create earnings card styles:
   - .earnings-summary grid layout (4 columns, responsive)
   - .earnings-stat-value large green numbers
   - .earnings-breakdown-item flex layout with spacing
   - .satellite-earnings-item with left border accent
   - Chart container height (300px)

2. Ensure dark mode compatibility for all new styles.

3. Make layout responsive for mobile (stack on small screens).

Follow existing CSS patterns with dark mode support (use CSS variables like --bg-primary, --text-primary).
```

---

## Phase 7: Notification Channels System

### 7.1 Email Notification Module

```
Create storj_monitor/email_sender.py for email notifications:

1. Implement EmailSender class:
   - __init__() - Load SMTP config, validate settings
   - send_alert_email(alert) - Main send function
   - format_alert_email(alert) - Create HTML email with severity colors
   - format_metadata(metadata) - Format additional info section

2. Use SMTP with TLS support (Gmail, custom servers)

3. Create professional HTML email template:
   - Colored severity badge
   - Alert title and message
   - Metadata in styled box
   - Link to dashboard

4. Handle errors gracefully with logging.

HTML template should use inline styles for email client compatibility with severity colors:
- critical: #ef4444 (red)
- warning: #f59e0b (orange)
- info: #3b82f6 (blue)
```

### 7.2 Webhook Notification Module

```
Create storj_monitor/webhook_sender.py for webhook integrations:

1. Implement WebhookSender class:
   - send_discord(alert) - Discord embed format with color coding
   - send_slack(alert) - Slack attachment format
   - send_custom(url, alert) - Generic JSON format

2. Discord format requirements:
   - Embeds with severity colors (red/yellow/blue)
   - Structured fields (node, severity, time)
   - Metadata as additional fields
   - Custom avatar and username

3. Slack format requirements:
   - Attachments with color (danger/warning/good)
   - Field structure matching Discord
   - Timestamp integration

4. Use aiohttp session for async HTTP requests.

Discord payload example:
{
  "embeds": [{
    "title": "Alert Title",
    "description": "Alert Message",
    "color": 15158332,
    "fields": [{"name": "Node", "value": "My-Node", "inline": true}]
  }]
}
```

### 7.3 Notification Configuration & Integration

```
Complete notification system integration:

1. Add notification config to storj_monitor/config.py:
   - Email settings (SMTP server, port, credentials, recipients)
   - Webhook URLs (Discord, Slack, custom)
   - Enable flags for each channel
   - Rate limiting and quiet hours settings

2. Create storj_monitor/notification_handler.py:
   - NotificationHandler class that coordinates all channels
   - should_send_email(alert) - Filter by severity
   - should_send_webhook(alert) - Decision logic
   - send_notification(alert) - Dispatch to all configured channels

3. Update storj_monitor/alert_manager.py:
   - Initialize NotificationHandler
   - Call notification_handler.send_notification() when generating alerts

4. Update storj_monitor/tasks.py to initialize webhook HTTP sessions on startup.

Follow existing async patterns and error handling used in reputation_tracker.py and storage_tracker.py.
```

---

## Phase 8: Advanced Reporting & Export

### 8.1 Report Generation Module

```
Create storj_monitor/report_generator.py for report generation:

1. Implement report generation functions:
   - generate_daily_summary(node_name, date) - Traffic, success rates, earnings, alerts
   - generate_weekly_performance(node_name, start_date) - Trends, storage growth, top issues
   - generate_monthly_financial(node_name, month) - Earnings breakdown, YoY comparison
   - generate_custom_report(node_name, start_date, end_date, metrics) - User-defined

2. Add PDF generation using ReportLab:
   - Professional formatting with headers/footers
   - Embedded charts (convert Chart.js to images)
   - Multi-page layout
   - Tables for data

3. Include CSV export functions:
   - export_to_csv(data, filename)
   - Proper escaping and formatting

Reports should include timestamp, node name, summary statistics, and detailed data tables. Use existing database query patterns.
```

### 8.2 Export API Endpoints

```
Add export endpoints to storj_monitor/server.py:

1. Create HTTP endpoints (not WebSocket):
   - GET /api/export/events - Export events as CSV
   - GET /api/export/earnings - Export earnings history
   - GET /api/export/reputation - Export reputation data
   - GET /api/export/storage - Export storage snapshots
   - GET /api/export/full_report - Generate and download PDF report

2. Add query parameters:
   - start_date, end_date (date range)
   - node_name (filter by node)
   - format (csv, json, pdf)

3. Implement proper HTTP response headers:
   - Content-Type based on format
   - Content-Disposition for downloads
   - Streaming for large files

4. Add scheduled report management endpoints:
   - POST /api/reports/schedule - Create schedule
   - GET /api/reports/scheduled - List schedules
   - DELETE /api/reports/schedule/{id} - Remove schedule

Use aiohttp for HTTP endpoints (similar to how WebSocket endpoints are structured in server.py). Return appropriate Content-Type headers.
```

---

## Phase 9: Alert Configuration UI

### 9.1 Settings Panel HTML & Structure

```
Create alert configuration UI in storj_monitor/static/index.html:

1. Add settings modal dialog:
   - Modal overlay with backdrop
   - Tabbed interface (Thresholds, Notifications, Advanced)
   - Settings gear icon in header to open modal

2. Thresholds tab content:
   - Reputation section (audit, suspension, online score sliders)
   - Storage section (usage %, forecast days sliders)
   - Performance section (latency thresholds)
   - Reset to defaults button

3. Notifications tab content:
   - Email enable toggle with SMTP settings
   - Webhook toggles (Discord, Slack, Custom)
   - Quiet hours configuration
   - Test notification buttons

4. Advanced tab:
   - Alert cooldown settings
   - Per-node overrides
   - Import/export settings (JSON)

Modal structure should use CSS similar to existing overlays, with z-index layering and backdrop click-to-close.
```

### 9.2 Settings Component Logic

```
Create storj_monitor/static/js/settings.js for configuration management:

1. Implement settings functions:
   - loadSettings() - Fetch current configuration
   - saveSettings() - POST to server
   - resetToDefaults() - Restore default thresholds
   - testNotification(channel) - Send test alert
   - exportSettings() - Download JSON
   - importSettings(file) - Upload JSON

2. Add validation:
   - Range checks for numeric inputs
   - Required fields validation
   - Format validation (email, URLs)

3. Implement settings API in server.py:
   - GET /api/settings - Return current config
   - POST /api/settings - Save new config
   - POST /api/settings/test - Send test notification

4. Store settings in database or config file (user preference).

Validation should prevent invalid ranges (e.g., warning threshold must be less than critical threshold). Use HTML5 input validation where possible.
```

---

## Phase 10: Mobile Optimization & PWA

### 10.1 Responsive CSS & Mobile Layout

```
Optimize storj_monitor/static/css/style.css for mobile:

1. Add mobile-first responsive breakpoints:
   - @media (max-width: 768px) for tablets
   - @media (max-width: 480px) for phones

2. Mobile optimizations:
   - Stack cards vertically (grid-template-columns: 1fr)
   - Collapsible card sections
   - Touch-friendly buttons (min 44px tap targets)
   - Horizontal scrolling for wide tables
   - Simplified charts (single line instead of multi-line)

3. Add mobile navigation:
   - Bottom navigation bar for phones
   - Hamburger menu for settings
   - Sticky header optimization

4. Test on multiple device sizes and adjust spacing/fonts.

Use flexbox and CSS Grid with responsive breakpoints. Test with Chrome DevTools device emulation.
```

### 10.2 Progressive Web App Setup

```
Add PWA capabilities to Storj Node Monitor:

1. Create storj_monitor/static/manifest.json:
   - App name, short_name, description
   - Icons in multiple sizes (192x192, 512x512)
   - start_url, display: "standalone"
   - theme_color, background_color
   - orientation: "portrait-primary"

2. Create storj_monitor/static/service-worker.js:
   - Cache static assets (CSS, JS, images)
   - Implement cache-first strategy for assets
   - Network-first for API calls
   - Offline fallback page
   - Background sync for queued requests

3. Update storj_monitor/static/index.html:
   - Add manifest link in <head>
   - Register service worker in app.js
   - Add install prompt handling
   - Add "Add to Home Screen" button

4. Implement push notification API:
   - Request notification permission
   - Subscribe to push service
   - Handle incoming push messages

Service worker should use workbox patterns or manual cache management. Test offline functionality in DevTools Network tab.
```

---

## Phase 11: Multi-Node Comparison

### 11.1 Comparison Dashboard Component

```
Create multi-node comparison view:

1. Update storj_monitor/static/index.html:
   - Add "Compare Nodes" section/tab
   - Multi-select checkbox list for node selection
   - Synchronized time range picker
   - Comparison metric selectors

2. Add comparison charts to charts.js:
   - createMultiNodePerformanceChart() - Overlaid lines for each node
   - createNodeComparisonBarChart() - Grouped bars by metric
   - createEfficiencyScatterPlot() - Efficiency vs earnings scatter
   - createNodeHealthHeatmap() - Color-coded health matrix

3. Implement comparison logic in app.js:
   - fetchComparisonData(node_names, start_date, end_date)
   - calculateComparativeMetrics() - Normalize and rank
   - updateComparisonView() - Render all comparison elements
   - exportComparison() - CSV export of comparison data

Charts should allow toggling individual nodes on/off. Use consistent color schemes across all comparison charts.
```

### 11.2 Comparison WebSocket API

```
Add comparison endpoints to storj_monitor/server.py:

1. WebSocket message type "get_comparison_data":
   - Accept list of node names
   - Return normalized metrics for fair comparison:
     * Earnings per TB stored
     * Storage efficiency (utilization %)
     * Success rates by operation type
     * Average latency percentiles
     * Reputation scores
     * Uptime percentage

2. Calculate rankings:
   - Best/worst performers per metric
   - Overall health score
   - Problem node identification

3. Support different comparison views:
   - Side-by-side current stats
   - Historical trend comparison
   - Efficiency analysis

4. Optimize queries for multiple nodes (batch processing).

Normalize metrics for fair comparison:
- Earnings per TB stored = total_earnings / (used_bytes / 1024^4)
- Storage efficiency = (used_bytes / total_bytes) * 100
- Average latency = mean of p50 values across time period
```

---

## General Implementation Tips

### Before Starting Any Phase

```
Preparation checklist before implementing a phase:

1. Read the complete phase documentation in docs/PHASE_5_TO_11_ROADMAP.md
2. Review existing similar code (e.g., reputation_tracker.py for financial_tracker.py)
3. Check dependencies are installed (aiohttp, Chart.js, etc.)
4. Create a feature branch (git checkout -b phase-X-feature-name)
5. Run existing tests to ensure baseline functionality
6. Back up database before schema changes
```

### Testing Each Phase

```
After implementing a phase, test thoroughly:

1. Run the application locally:
   storj_monitor --node "Test:/path/to/log:http://localhost:14002"

2. Check logs for errors:
   - Database migration success
   - Background tasks starting
   - API connections established

3. Test UI components:
   - Open browser to http://localhost:8765
   - Verify new cards/features display
   - Check WebSocket messages in browser DevTools
   - Test on different screen sizes

4. Database verification:
   sqlite3 storj_stats.db "SELECT * FROM new_table_name LIMIT 5;"

5. Write unit tests for new modules
6. Update documentation (README, phase completion doc)
```

### Code Review Checklist

```
Before marking a phase complete, verify:

□ All new files have proper docstrings
□ Configuration is documented in config.py with comments
□ Database migrations are backward compatible
□ Error handling covers edge cases
□ Logging is informative but not spammy
□ WebSocket messages follow existing format conventions
□ UI is responsive and accessible
□ Dark mode works for all new components
□ No console errors in browser
□ Memory leaks tested (long-running session)
□ Performance impact is acceptable (<5% CPU increase)
```

---

## Quick Reference: File Locations

**Backend:**
- Configuration: [`storj_monitor/config.py`](../storj_monitor/config.py)
- Database: [`storj_monitor/database.py`](../storj_monitor/database.py)
- Tasks: [`storj_monitor/tasks.py`](../storj_monitor/tasks.py)
- Server: [`storj_monitor/server.py`](../storj_monitor/server.py)

**Frontend:**
- HTML: [`storj_monitor/static/index.html`](../storj_monitor/static/index.html)
- CSS: [`storj_monitor/static/css/style.css`](../storj_monitor/static/css/style.css)
- JavaScript: [`storj_monitor/static/js/app.js`](../storj_monitor/static/js/app.js)
- Charts: [`storj_monitor/static/js/charts.js`](../storj_monitor/static/js/charts.js)

**Documentation:**
- Roadmap: [`docs/PHASE_5_TO_11_ROADMAP.md`](PHASE_5_TO_11_ROADMAP.md)
- This file: [`docs/ROOCODE_PROMPTS.md`](ROOCODE_PROMPTS.md)

---

## Usage Instructions

1. **Switch to Code mode:** `/mode code`
2. **Copy the prompt** for the phase you want to implement
3. **Paste into RooCode** and press Enter
4. **Review the changes** RooCode proposes
5. **Test thoroughly** using the testing checklist above
6. **Move to next prompt** in the phase (e.g., 5.1 → 5.2 → 5.3)
7. **Mark phase complete** when all prompts executed successfully

**Pro Tip:** Work through phases sequentially (5 → 6 → 7...) as later phases may depend on earlier ones.

---

**Happy Coding! 🚀**
