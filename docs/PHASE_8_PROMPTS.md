# Phase 8: Testing & Code Quality - Implementation Prompts

**Priority:** 🔴 CRITICAL  
**Duration:** 1-2 weeks  
**Goal:** Establish comprehensive test coverage and code quality standards

---

## Overview

These prompts will guide you through implementing a complete testing infrastructure and achieving >80% code coverage for the Storj Node Monitor. Each prompt builds on the previous one and should be executed in sequence.

**Prerequisites:**
- All previous phases (1-7) are complete
- Python 3.9+ installed
- Project is functional and running

---

## Prompt 8.1: Testing Infrastructure Setup

```
Set up the testing infrastructure for the Storj Node Monitor project:

1. Install testing dependencies:
   - Add to pyproject.toml dependencies: pytest, pytest-asyncio, pytest-cov, pytest-mock
   - Add development dependencies: ruff, pre-commit

2. Create pytest configuration (pytest.ini):
   ```ini
   [pytest]
   testpaths = tests
   python_files = test_*.py
   python_classes = Test*
   python_functions = test_*
   asyncio_mode = auto
   addopts = 
       --cov=storj_monitor
       --cov-report=html
       --cov-report=term-missing
       --cov-fail-under=80
   ```

3. Create test directory structure:
   ```
   tests/
   ├── __init__.py
   ├── conftest.py           # Shared fixtures
   ├── test_config.py
   ├── test_database.py
   ├── test_log_processor.py
   ├── test_storj_api_client.py
   ├── test_reputation_tracker.py
   ├── test_storage_tracker.py
   ├── test_performance_analyzer.py
   ├── test_analytics_engine.py
   ├── test_anomaly_detector.py
   ├── test_alert_manager.py
   ├── test_financial_tracker.py
   ├── test_email_sender.py
   ├── test_webhook_sender.py
   ├── test_notification_handler.py
   └── fixtures/
       ├── sample_logs.txt
       ├── sample_api_responses.json
       └── test_database.db
   ```

4. Create conftest.py with shared fixtures:
   - Temporary database fixture
   - Mock aiohttp session fixture
   - Sample log data fixture
   - Mock API client fixture
   - Test configuration fixture

Example conftest.py structure:
```python
import pytest
import asyncio
import tempfile
import os
from pathlib import Path
from storj_monitor.database import blocking_init_database

@pytest.fixture
def temp_db():
    """Create temporary test database"""
    fd, path = tempfile.mkstemp(suffix='.db')
    blocking_init_database(path)
    yield path
    os.close(fd)
    os.unlink(path)

@pytest.fixture
def sample_log_lines():
    """Load sample log data"""
    fixture_path = Path(__file__).parent / 'fixtures' / 'sample_logs.txt'
    with open(fixture_path) as f:
        return f.readlines()

@pytest.fixture
async def mock_api_client():
    """Create mock API client"""
    # Return mock that simulates StorjNodeAPIClient
    pass

@pytest.fixture
def test_config():
    """Test configuration overrides"""
    return {
        'DATABASE_FILE': ':memory:',
        'ENABLE_ANOMALY_DETECTION': True,
        # ... other config
    }
```

5. Create sample test data files in tests/fixtures/:
   - sample_logs.txt with various log line types
   - sample_api_responses.json with mock API responses

Success criteria:
- Test infrastructure is set up
- pytest runs successfully (even with no tests)
- Fixtures are accessible from test files
```

---

## Prompt 8.2: Core Module Tests (Database, Config, Log Processor)

```
Write comprehensive unit tests for the core modules:

1. Test storj_monitor/config.py (tests/test_config.py):
   - Test all configuration variables are accessible
   - Test default values are correct
   - Test configuration validation (if any)
   - Test environment variable overrides (if supported)

Example test structure:
```python
import pytest
from storj_monitor import config

def test_default_configuration():
    """Test that default config values are set"""
    assert config.DATABASE_FILE is not None
    assert config.NODE_API_DEFAULT_PORT == 14002
    assert config.AUDIT_SCORE_CRITICAL == 70.0
    # ... test all critical config values

def test_threshold_values_are_logical():
    """Test that threshold values make sense"""
    assert config.AUDIT_SCORE_WARNING > config.AUDIT_SCORE_CRITICAL
    assert config.STORAGE_WARNING_PERCENT < config.STORAGE_CRITICAL_PERCENT
    # ... other logical checks
```

2. Test storj_monitor/database.py (tests/test_database.py):
   - Test database initialization
   - Test all blocking_write_* functions
   - Test all blocking_get_* functions
   - Test database schema creation
   - Test index creation
   - Test error handling for invalid data
   - Test concurrent access patterns

Example test structure:
```python
import pytest
from storj_monitor.database import (
    blocking_init_database,
    blocking_write_event,
    blocking_get_events,
    blocking_write_reputation_history,
    blocking_get_latest_reputation
)

def test_database_initialization(temp_db):
    """Test database creates all required tables"""
    import sqlite3
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    
    # Check all tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    
    required_tables = {
        'events', 'reputation_history', 'storage_snapshots',
        'alerts', 'insights', 'analytics_baselines'
    }
    assert required_tables.issubset(tables)
    conn.close()

def test_write_and_retrieve_event(temp_db):
    """Test writing and retrieving events"""
    event = {
        'timestamp': '2025-10-08T10:00:00Z',
        'node_name': 'Test-Node',
        'action': 'GET',
        'satellite_id': 'test-sat',
        'status': 'success',
        'duration_ms': 1234
    }
    
    blocking_write_event(temp_db, event)
    events = blocking_get_events(temp_db, ['Test-Node'], hours=1)
    
    assert len(events) == 1
    assert events[0]['action'] == 'GET'
    assert events[0]['duration_ms'] == 1234

# Add tests for all other database functions
```

3. Test storj_monitor/log_processor.py (tests/test_log_processor.py):
   - Test log line parsing for all event types (GET, PUT, DELETE, GET_AUDIT, GET_REPAIR)
   - Test duration extraction from various formats
   - Test satellite ID extraction
   - Test piece ID extraction
   - Test JSON parsing
   - Test error handling for malformed logs
   - Test duration calculation from DEBUG log pairs

Example test structure:
```python
import pytest
from storj_monitor.log_processor import (
    parse_log_line,
    extract_duration_ms,
    calculate_duration_from_debug_logs
)

def test_parse_download_success():
    """Test parsing successful download log"""
    log_line = '2025-10-08T10:00:00.123Z INFO piecestore downloaded...'
    result = parse_log_line(log_line, 'Test-Node', 1728384000.0)
    
    assert result['action'] == 'GET'
    assert result['status'] == 'success'
    assert 'satellite_id' in result

def test_parse_duration_from_json():
    """Test duration extraction from JSON log"""
    log_line = '{"duration":"1m37.535505102s","Action":"GET",...}'
    duration = extract_duration_ms(log_line)
    
    assert duration == 97536  # 97.536 seconds in ms

def test_malformed_log_handling():
    """Test that malformed logs don't crash parser"""
    bad_log = 'This is not a valid log line'
    result = parse_log_line(bad_log, 'Test-Node', 1728384000.0)
    
    assert result is None  # or appropriate error handling

# Add tests for all log types and edge cases
```

Target: >80% coverage for these three core modules

Run tests with:
```bash
uv run pytest tests/test_config.py tests/test_database.py tests/test_log_processor.py -v --cov=storj_monitor.config --cov=storj_monitor.database --cov=storj_monitor.log_processor
```
```

---

## Prompt 8.3: API Client and Monitoring Module Tests

```
Write comprehensive unit tests for API client and monitoring modules:

1. Test storj_monitor/storj_api_client.py (tests/test_storj_api_client.py):
   - Test API client initialization
   - Test connection testing
   - Test get_dashboard() with mock responses
   - Test get_satellites() with mock responses
   - Test get_satellite_info() with mock responses
   - Test get_estimated_payout() with mock responses
   - Test timeout handling
   - Test connection error handling
   - Test invalid JSON response handling

Example test structure:
```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from storj_monitor.storj_api_client import StorjNodeAPIClient

@pytest.mark.asyncio
async def test_api_client_initialization():
    """Test API client initializes correctly"""
    client = StorjNodeAPIClient('http://localhost:14002', 'Test-Node')
    assert client.base_url == 'http://localhost:14002'
    assert client.node_name == 'Test-Node'

@pytest.mark.asyncio
async def test_get_dashboard_success():
    """Test successful dashboard retrieval"""
    with patch('aiohttp.ClientSession.get') as mock_get:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            'diskSpace': 2000000000000,
            'diskSpaceUsed': 1600000000000,
            'diskSpaceAvailable': 400000000000
        })
        mock_get.return_value.__aenter__.return_value = mock_response
        
        client = StorjNodeAPIClient('http://localhost:14002', 'Test-Node')
        result = await client.get_dashboard()
        
        assert result is not None
        assert 'diskSpace' in result
        assert result['diskSpaceUsed'] == 1600000000000

@pytest.mark.asyncio
async def test_get_dashboard_connection_error():
    """Test dashboard retrieval with connection error"""
    with patch('aiohttp.ClientSession.get') as mock_get:
        mock_get.side_effect = Exception('Connection refused')
        
        client = StorjNodeAPIClient('http://localhost:14002', 'Test-Node')
        result = await client.get_dashboard()
        
        assert result is None

# Add tests for all other API methods
```

2. Test storj_monitor/reputation_tracker.py (tests/test_reputation_tracker.py):
   - Test track_reputation() function
   - Test reputation score extraction
   - Test alert generation for low scores
   - Test alert generation for disqualified nodes
   - Test alert generation for suspended nodes
   - Test database storage of reputation history

3. Test storj_monitor/storage_tracker.py (tests/test_storage_tracker.py):
   - Test track_storage() function
   - Test storage data extraction
   - Test growth rate calculation
   - Test capacity forecasting
   - Test alert generation for high usage
   - Test alert generation for forecast warnings

4. Test storj_monitor/performance_analyzer.py (tests/test_performance_analyzer.py):
   - Test percentile calculations
   - Test latency statistics computation
   - Test slow operation detection
   - Test histogram generation

Target: >80% coverage for all monitoring modules

Run tests with:
```bash
uv run pytest tests/test_storj_api_client.py tests/test_reputation_tracker.py tests/test_storage_tracker.py tests/test_performance_analyzer.py -v
```
```

---

## Prompt 8.4: Intelligence and Analytics Module Tests

```
Write comprehensive unit tests for intelligence and analytics modules:

1. Test storj_monitor/analytics_engine.py (tests/test_analytics_engine.py):
   - Test baseline calculation
   - Test Z-score calculation
   - Test trend detection (increasing/decreasing/stable)
   - Test percentile calculations
   - Test linear forecasting
   - Test baseline storage and retrieval

Example test structure:
```python
import pytest
from storj_monitor.analytics_engine import AnalyticsEngine

@pytest.fixture
async def analytics(temp_db):
    """Create analytics engine instance"""
    return AnalyticsEngine(temp_db)

@pytest.mark.asyncio
async def test_calculate_baseline(analytics):
    """Test baseline calculation from values"""
    values = [100, 105, 98, 102, 99, 101, 103]
    baseline = await analytics.calculate_baseline(
        'Test-Node', 'test_metric', values, 168
    )
    
    assert baseline['mean_value'] == pytest.approx(101.14, rel=0.01)
    assert baseline['std_dev'] > 0
    assert baseline['min_value'] == 98
    assert baseline['max_value'] == 105

@pytest.mark.asyncio
async def test_z_score_calculation(analytics):
    """Test Z-score calculation"""
    baseline = {
        'mean_value': 100.0,
        'std_dev': 5.0
    }
    
    z_score = analytics.calculate_z_score(115.0, baseline)
    assert z_score == pytest.approx(3.0, rel=0.01)

@pytest.mark.asyncio
async def test_trend_detection(analytics):
    """Test trend detection"""
    # Increasing trend
    values = [100, 105, 110, 115, 120]
    trend, slope = analytics.detect_trend(values)
    assert trend == 'increasing'
    assert slope > 0
    
    # Decreasing trend
    values = [120, 115, 110, 105, 100]
    trend, slope = analytics.detect_trend(values)
    assert trend == 'decreasing'
    assert slope < 0
    
    # Stable
    values = [100, 101, 100, 99, 100]
    trend, slope = analytics.detect_trend(values)
    assert trend == 'stable'

# Add more tests for forecasting, storage health analysis, etc.
```

2. Test storj_monitor/anomaly_detector.py (tests/test_anomaly_detector.py):
   - Test anomaly detection with various Z-scores
   - Test traffic anomaly detection
   - Test latency anomaly detection
   - Test bandwidth anomaly detection
   - Test anomaly cache management

3. Test storj_monitor/alert_manager.py (tests/test_alert_manager.py):
   - Test alert generation for various conditions
   - Test alert deduplication
   - Test alert acknowledgment
   - Test alert resolution
   - Test alert summary generation
   - Test alert broadcasting

Target: >80% coverage for intelligence modules

Run tests with:
```bash
uv run pytest tests/test_analytics_engine.py tests/test_anomaly_detector.py tests/test_alert_manager.py -v
```
```

---

## Prompt 8.5: Financial and Notification Module Tests

```
Write comprehensive unit tests for financial tracking and notification modules:

1. Test storj_monitor/financial_tracker.py (tests/test_financial_tracker.py):
   - Test earnings calculation from API data
   - Test earnings calculation from database
   - Test storage earnings calculation (GB-hours)
   - Test held amount calculation based on node age
   - Test month-end forecast
   - Test confidence scoring
   - Test per-satellite aggregation

Example test structure:
```python
import pytest
from storj_monitor.financial_tracker import FinancialTracker

@pytest.fixture
async def financial_tracker(temp_db):
    """Create financial tracker instance"""
    return FinancialTracker(temp_db)

@pytest.mark.asyncio
async def test_storage_earnings_calculation(financial_tracker):
    """Test storage earnings from GB-hours"""
    gb_hours = 720000  # 1TB for 30 days
    earnings = financial_tracker.calculate_storage_earnings(gb_hours)
    
    # Expected: (720000 / (1024 * 720)) * 1.50 * 0.50
    # = 0.977 * 1.50 * 0.50 = ~0.73
    assert earnings == pytest.approx(0.73, rel=0.1)

@pytest.mark.asyncio
async def test_held_amount_calculation(financial_tracker):
    """Test held amount based on node age"""
    # Node 2 months old (75% held)
    held = financial_tracker.calculate_held_amount(100.0, 2)
    assert held == pytest.approx(75.0, rel=0.01)
    
    # Node 5 months old (50% held)
    held = financial_tracker.calculate_held_amount(100.0, 5)
    assert held == pytest.approx(50.0, rel=0.01)
    
    # Node 12 months old (0% held)
    held = financial_tracker.calculate_held_amount(100.0, 12)
    assert held == pytest.approx(0.0, rel=0.01)

# Add more tests for API earnings, forecasting, etc.
```

2. Test storj_monitor/email_sender.py (tests/test_email_sender.py):
   - Test email formatting
   - Test SMTP connection (mocked)
   - Test email sending success
   - Test email sending failure handling
   - Test HTML email generation
   - Test metadata formatting

3. Test storj_monitor/webhook_sender.py (tests/test_webhook_sender.py):
   - Test Discord webhook formatting
   - Test Slack webhook formatting
   - Test custom webhook formatting
   - Test webhook sending (mocked)
   - Test concurrent webhook delivery
   - Test webhook error handling

4. Test storj_monitor/notification_handler.py (tests/test_notification_handler.py):
   - Test notification routing logic
   - Test channel selection
   - Test multi-channel delivery
   - Test notification filtering by severity

Target: >80% coverage for financial and notification modules

Run tests with:
```bash
uv run pytest tests/test_financial_tracker.py tests/test_email_sender.py tests/test_webhook_sender.py tests/test_notification_handler.py -v
```
```

---

## Prompt 8.6: Integration Tests

```
Write integration tests that test multiple components working together:

Create tests/integration/ directory with the following tests:

1. tests/integration/test_end_to_end_monitoring.py:
   - Test complete monitoring flow (log parsing → database → WebSocket)
   - Test reputation monitoring with mock API
   - Test storage tracking with mock API
   - Test alert generation and notification delivery

Example test structure:
```python
import pytest
import asyncio
from storj_monitor import tasks, server, database

@pytest.mark.asyncio
async def test_complete_monitoring_flow(temp_db, mock_api_client):
    """Test end-to-end monitoring flow"""
    # 1. Parse sample logs
    # 2. Store in database
    # 3. Generate statistics
    # 4. Check alerts are generated
    # 5. Verify WebSocket broadcasts
    pass

@pytest.mark.asyncio
async def test_reputation_to_alert_flow(temp_db):
    """Test reputation monitoring generates alerts correctly"""
    # Mock API response with low audit score
    # Run reputation tracker
    # Verify alert is generated
    # Verify alert is stored in database
    pass
```

2. tests/integration/test_database_migrations.py:
   - Test database creation from scratch
   - Test all tables are created
   - Test all indexes are created
   - Test database is functional after creation

3. tests/integration/test_websocket_communication.py:
   - Test WebSocket message handling
   - Test request/response cycles
   - Test data broadcasting
   - Test error handling

Target: Critical integration paths tested

Run tests with:
```bash
uv run pytest tests/integration/ -v
```
```

---

## Prompt 8.7: Code Quality with Ruff

```
Set up and configure Ruff for code quality enforcement:

1. Create ruff.toml configuration file:
```toml
# Ruff configuration for Storj Node Monitor

[lint]
# Enable pycodestyle (E), pyflakes (F), isort (I), and more
select = [
    "E",    # pycodestyle errors
    "F",    # pyflakes
    "I",    # isort
    "N",    # pep8-naming
    "W",    # pycodestyle warnings
    "UP",   # pyupgrade
    "B",    # flake8-bugbear
    "C4",   # flake8-comprehensions
    "SIM",  # flake8-simplify
    "TCH",  # flake8-type-checking
]

# Ignore specific rules
ignore = [
    "E501",  # Line too long (let formatter handle)
    "B008",  # Do not perform function call in argument defaults
]

# Exclude directories
exclude = [
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "build",
    "dist",
    "*.egg-info",
]

# Line length
line-length = 100

[lint.per-file-ignores]
# Ignore import errors in __init__.py
"__init__.py" = ["F401", "F403"]

[format]
# Use double quotes
quote-style = "double"

# Indent with spaces
indent-style = "space"

# Line endings
line-ending = "auto"
```

2. Run ruff check and fix issues:
```bash
# Check all Python files
uv run ruff check storj_monitor/ tests/

# Auto-fix issues where possible
uv run ruff check --fix storj_monitor/ tests/

# Format code
uv run ruff format storj_monitor/ tests/
```

3. Fix common issues:
   - Remove unused imports
   - Fix import ordering (stdlib, third-party, local)
   - Fix naming conventions (snake_case for functions/variables)
   - Fix line length issues (refactor long lines)
   - Fix docstring formatting
   - Remove commented-out code

4. Verify all checks pass:
```bash
uv run ruff check storj_monitor/ tests/ --no-fix
```

Success criteria:
- All ruff checks pass with zero errors
- Code is consistently formatted
- Import order is standardized
```

---

## Prompt 8.8: Pre-commit Hooks and CI/CD

```
Set up pre-commit hooks and CI/CD pipeline:

1. Create .pre-commit-config.yaml:
```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.1.9
    hooks:
      # Run the linter
      - id: ruff
        args: [ --fix ]
      # Run the formatter
      - id: ruff-format

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
      - id: check-json
      - id: check-toml
```

2. Install pre-commit hooks:
```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files  # Test on all files
```

3. Create .github/workflows/tests.yml for GitHub Actions:
```yaml
name: Tests

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main, develop ]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.9", "3.10", "3.11", "3.12"]

    steps:
    - uses: actions/checkout@v4
    
    - name: Set up Python ${{ matrix.python-version }}
      uses: actions/setup-python@v4
      with:
        python-version: ${{ matrix.python-version }}
    
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -e ".[dev]"
    
    - name: Run ruff check
      run: ruff check storj_monitor/ tests/
    
    - name: Run tests with coverage
      run: pytest --cov=storj_monitor --cov-report=xml --cov-report=term
    
    - name: Upload coverage to Codecov
      uses: codecov/codecov-action@v3
      with:
        file: ./coverage.xml
        flags: unittests
        name: codecov-umbrella
```

4. Update pyproject.toml with dev dependencies:
```toml
[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.0",
    "pytest-cov>=4.1.0",
    "pytest-mock>=3.11.0",
    "ruff>=0.1.9",
    "pre-commit>=3.5.0",
]
```

Success criteria:
- Pre-commit hooks run on every commit
- CI pipeline passes on all Python versions
- Coverage reports are generated
```

---

## Prompt 8.9: Documentation and Final Verification

```
Create testing documentation and verify complete Phase 8 implementation:

1. Create docs/TESTING.md:
```markdown
# Testing Guide - Storj Node Monitor

## Overview

This project uses pytest for testing with >80% code coverage requirement.

## Running Tests

### All Tests
```bash
pytest
```

### With Coverage Report
```bash
pytest --cov=storj_monitor --cov-report=html
```

### Specific Test File
```bash
pytest tests/test_database.py -v
```

### Integration Tests Only
```bash
pytest tests/integration/ -v
```

## Test Structure

- `tests/` - Unit tests for individual modules
- `tests/integration/` - Integration tests
- `tests/fixtures/` - Test data and sample files
- `conftest.py` - Shared pytest fixtures

## Writing Tests

### Unit Test Example
```python
import pytest

def test_example():
    assert 1 + 1 == 2
```

### Async Test Example
```python
import pytest

@pytest.mark.asyncio
async def test_async_function():
    result = await some_async_function()
    assert result is not None
```

### Using Fixtures
```python
def test_with_database(temp_db):
    # temp_db is provided by conftest.py
    # Use temporary database for testing
    pass
```

## Code Quality

### Run Ruff Check
```bash
ruff check storj_monitor/ tests/
```

### Auto-fix Issues
```bash
ruff check --fix storj_monitor/ tests/
```

### Format Code
```bash
ruff format storj_monitor/ tests/
```

## Pre-commit Hooks

Pre-commit hooks run automatically before each commit:
```bash
pre-commit install
pre-commit run --all-files
```

## Coverage Requirements

- Minimum coverage: 80%
- Tests fail if coverage drops below 80%
- View coverage report: `htmlcov/index.html`

## CI/CD

- GitHub Actions runs tests on every push
- Tests run on Python 3.9, 3.10, 3.11, 3.12
- Coverage reports uploaded to Codecov
```

2. Verify complete implementation:

Run final checks:
```bash
# 1. Run all tests
pytest -v

# 2. Check coverage
pytest --cov=storj_monitor --cov-report=term-missing

# 3. Verify >80% coverage
# (Should pass if --cov-fail-under=80 is in pytest.ini)

# 4. Run ruff check
ruff check storj_monitor/ tests/

# 5. Run ruff format check
ruff format --check storj_monitor/ tests/

# 6. Test pre-commit hooks
pre-commit run --all-files

# 7. Simulate CI pipeline
python -m pytest --cov=storj_monitor --cov-report=xml
```

Success criteria:
- All tests pass (100% pass rate)
- Code coverage >80%
- Ruff checks pass with zero errors
- Pre-commit hooks configured and working
- CI/CD pipeline configured (if using GitHub)
- Documentation is complete
```

---

## Phase 8 Completion Checklist

Before marking Phase 8 as complete, verify:

- [ ] pytest configuration created (pytest.ini)
- [ ] Test directory structure created with all test files
- [ ] conftest.py with shared fixtures created
- [ ] Core module tests written (config, database, log_processor)
- [ ] API and monitoring tests written
- [ ] Intelligence and analytics tests written
- [ ] Financial and notification tests written
- [ ] Integration tests written
- [ ] Code coverage >80% achieved
- [ ] ruff.toml configuration created
- [ ] All ruff checks pass
- [ ] Code is formatted consistently
- [ ] Pre-commit hooks configured
- [ ] CI/CD pipeline configured (if applicable)
- [ ] Testing documentation (TESTING.md) created
- [ ] All tests pass in CI environment
- [ ] Phase completion document created

---

## Common Issues and Solutions

### Issue: Tests fail with "database is locked"
**Solution:** Use separate temp database for each test with `temp_db` fixture

### Issue: Async tests fail
**Solution:** Add `@pytest.mark.asyncio` decorator and ensure `asyncio_mode = auto` in pytest.ini

### Issue: Coverage below 80%
**Solution:** Identify uncovered lines with `--cov-report=term-missing` and add tests

### Issue: Ruff formatting conflicts with existing code
**Solution:** Run `ruff format` on entire codebase, commit formatting changes separately

### Issue: Mock API calls not working
**Solution:** Use `unittest.mock.patch` or `pytest-mock` to properly mock aiohttp calls

---

## Next Steps

After Phase 8 completion:
1. Create `docs/completed/PHASE_8_COMPLETE.md` documenting achievements
2. Update `docs/MASTER_ROADMAP.md` to mark Phase 8 complete
3. Proceed to Phase 9: Multi-Node Comparison with testing requirements

---

**Testing is the foundation of reliable software. Take time to do this properly!** 🧪