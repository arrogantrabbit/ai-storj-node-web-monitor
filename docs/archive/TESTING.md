# Testing Guide - Storj Node Monitor

## Overview

This project uses [`pytest`](https://docs.pytest.org/) for testing with a >80% code coverage requirement. The test suite includes comprehensive unit tests, integration tests, and code quality checks to ensure reliability and maintainability.

## Test Infrastructure

### Testing Stack
- pytest - Test framework
- pytest-asyncio - Async test support
- pytest-cov - Coverage reporting
- pytest-mock - Mocking utilities
- ruff - Code formatting and linting

### Configuration
Test configuration is defined in [pytest.ini](../pytest.ini):
- Test discovery in [tests/](../tests/) directory
- Automatic async mode for asyncio tests
- Coverage reporting with 80% minimum threshold
- HTML and terminal coverage reports

## Running Tests

### All Tests
Run the entire test suite:
```bash
pytest
```

### With Coverage Report
Generate coverage report (HTML + terminal):
```bash
pytest --cov=storj_monitor --cov-report=html --cov-report=term-missing
```

View HTML coverage report:
```bash
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

### Specific Test File
Run tests from a specific file:
```bash
pytest tests/test_database.py -v
```

### Specific Test Function
Run a single test:
```bash
pytest tests/test_database.py::test_database_init -v
```

### Integration Tests Only
Run integration tests:
```bash
pytest tests/integration/ -v
```

### By Test Pattern
Run tests matching a pattern:
```bash
pytest -k "database" -v
pytest -k "not integration" -v
```

## Test Structure

```
tests/
├── __init__.py
├── conftest.py                          # Shared fixtures
├── test_config.py                       # Configuration tests
├── test_database.py                     # Database module tests
├── test_log_processor.py                # Log parsing tests
├── test_storj_api_client.py            # API client tests
├── test_reputation_tracker.py           # Reputation tracking tests
├── test_storage_tracker.py              # Storage tracking tests
├── test_performance_analyzer.py         # Performance analysis tests
├── test_analytics_engine.py             # Analytics engine tests
├── test_anomaly_detector.py             # Anomaly detection tests
├── test_alert_manager.py                # Alert management tests
├── test_financial_tracker.py            # Financial tracking tests
├── test_email_sender.py                 # Email notification tests
├── test_webhook_sender.py               # Webhook notification tests
├── test_notification_handler.py         # Notification routing tests
├── fixtures/                            # Test data
│   ├── sample_logs.txt                  # Sample log lines
│   └── sample_api_responses.json        # Mock API responses
└── integration/                         # Integration tests
    ├── test_end_to_end_monitoring.py    # E2E monitoring flow
    ├── test_database_migrations.py      # Database schema tests
    └── test_websocket_communication.py  # WebSocket tests
```

## Writing Tests

### Unit Test Example
```python
import pytest
from storj_monitor import config

def test_default_configuration():
    """Test that default config values are set."""
    assert config.DATABASE_FILE is not None
    assert config.NODE_API_DEFAULT_PORT == 14002
```

### Async Test Example
```python
import pytest
from storj_monitor.storj_api_client import StorjNodeAPIClient

@pytest.mark.asyncio
async def test_api_client_initialization():
    """Test API client initializes correctly."""
    client = StorjNodeAPIClient("Test-Node", "http://localhost:14002")
    assert client.node_name == "Test-Node"
```

### Using Fixtures
Fixtures are defined in [conftest.py](../tests/conftest.py):

```python
def test_with_database(temp_db):
    """Test using temporary database fixture."""
    # temp_db is a path to a temporary SQLite database
    # with full schema initialized
    pass

def test_with_sample_data(sample_event, sample_reputation_data):
    """Test using sample data fixtures."""
    # Fixtures provide realistic test data
    assert sample_event["action"] == "GET"
    assert sample_reputation_data["audit_score"] == 1.0
```

### Mocking External Dependencies
```python
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_with_mock_api():
    """Test with mocked API client."""
    with patch('storj_monitor.storj_api_client.StorjNodeAPIClient') as mock:
        mock.get_dashboard = AsyncMock(return_value={"diskSpace": {...}})
        # Test code here
```

## Available Fixtures

### Database Fixtures
- temp_db - Temporary SQLite database with schema
- sample_event - Sample traffic event data
- sample_reputation_data - Sample reputation data
- sample_storage_snapshot - Sample storage snapshot
- sample_alert - Sample alert data

### Mock Fixtures
- mock_api_client - Mock Storj API client
- mock_aiohttp_session - Mock aiohttp session
- mock_geoip_reader - Mock GeoIP reader
- mock_email_sender - Mock email sender
- mock_webhook_sender - Mock webhook sender

### Configuration Fixtures
- test_config - Test configuration overrides
- nodes_config - Sample nodes configuration

See [conftest.py](../tests/conftest.py) for complete fixture list and documentation.

## Code Quality

### Ruff Check
Check code for linting issues:
```bash
uv run ruff check storj_monitor/ tests/
```

### Auto-fix Issues
Automatically fix issues where possible:
```bash
uv run ruff check --fix storj_monitor/ tests/
```

### Format Code
Format code according to project style:
```bash
uv run ruff format storj_monitor/ tests/
```

### Check Formatting
Verify code is properly formatted:
```bash
uv run ruff format --check storj_monitor/ tests/
```

## Coverage Requirements

### Minimum Coverage
- Overall Project: 80% minimum
- Tests will fail if coverage drops below 80%

### Checking Coverage
View coverage by module:
```bash
pytest --cov=storj_monitor --cov-report=term-missing
```

### Coverage Report
HTML coverage report shows:
- Line-by-line coverage
- Uncovered lines highlighted
- Branch coverage statistics
- Module-level summaries

### Improving Coverage
To improve coverage:
1. Run tests with --cov-report=term-missing to see uncovered lines
2. Write tests for uncovered code paths
3. Focus on critical paths and edge cases
4. Add integration tests for complex flows

Example:
```bash
pytest --cov=storj_monitor.database --cov-report=term-missing
```

## Test Categories

### Unit Tests
Test individual functions and classes in isolation:
- Fast execution
- Isolated dependencies
- Clear failure messages
- Located in tests/test_*.py

### Integration Tests
Test multiple components working together:
- End-to-end workflows
- Database interactions
- WebSocket communication
- Located in tests/integration/

### Key Integration Test Areas
1. End-to-End Monitoring ([test_end_to_end_monitoring.py](../tests/integration/test_end_to_end_monitoring.py))
   - Log parsing → Database → Statistics → Alerts → Notifications
   
2. Database Migrations ([test_database_migrations.py](../tests/integration/test_database_migrations.py))
   - Schema creation and upgrades
   - Data integrity
   - Index creation
   
3. WebSocket Communication ([test_websocket_communication.py](../tests/integration/test_websocket_communication.py))
   - Message handling
   - Real-time updates
   - Connection management

## Continuous Testing

### Watch Mode
Run tests automatically on file changes:
```bash
pytest-watch
```

Or using pytest's built-in watch:
```bash
pytest --looponfail
```

### Quick Tests
Run only tests that failed in the last run:
```bash
pytest --lf
```

Run failed tests first, then all:
```bash
pytest --ff
```

## Common Testing Patterns

### Testing Database Operations
```python
def test_database_operation(temp_db):
    """Test database write and read."""
    from storj_monitor.database import blocking_write_event, blocking_get_events
    
    event = {
        "timestamp": datetime.now(timezone.utc),
        "node_name": "test-node",
        "action": "GET",
        ...
    }
    
    blocking_write_event(temp_db, event)
    events = blocking_get_events(temp_db, ["test-node"], hours=1)
    
    assert len(events) == 1
    assert events[0]["action"] == "GET"
```

### Testing Async Functions
```python
@pytest.mark.asyncio
async def test_async_operation():
    """Test async function."""
    result = await some_async_function()
    assert result is not None
```

### Testing with Mocks
```python
@pytest.mark.asyncio
async def test_with_mock():
    """Test with mocked external dependency."""
    with patch('module.external_function') as mock:
        mock.return_value = "test"
        result = await function_using_external()
        assert mock.called
```

### Testing Error Handling
```python
def test_error_handling():
    """Test error handling."""
    with pytest.raises(ValueError, match="Invalid input"):
        function_that_should_raise("invalid")
```

## Debugging Tests

### Verbose Output
```bash
pytest -v
```

### Show Print Statements
```bash
pytest -s
```

### Stop on First Failure
```bash
pytest -x
```

### Debug with PDB
```bash
pytest --pdb
```

Or insert breakpoint in code:
```python
def test_something():
    breakpoint()  # Python 3.7+
    # test code
```

### Show Locals on Failure
```bash
pytest -l
```

## Performance Testing

### Test Execution Time
Show slowest tests:
```bash
pytest --durations=10
```

### Parallel Execution
Install pytest-xdist:
```bash
pip install pytest-xdist
```

Run tests in parallel:
```bash
pytest -n auto
```

## Best Practices

### Test Naming
- Use descriptive names: test_function_does_expected_thing
- Include test type in integration tests: test_end_to_end_workflow
- Group related tests in classes: class TestDatabaseOperations:

### Test Structure
Follow AAA pattern:
```python
def test_example():
    # Arrange - Setup test data
    data = prepare_test_data()
    
    # Act - Execute the function
    result = function_under_test(data)
    
    # Assert - Verify the result
    assert result == expected_value
```

### Test Independence
- Each test should be independent
- Use fixtures for setup/teardown
- Don't rely on test execution order
- Clean up resources in fixtures

### Assertions
- Use specific assertions
- Include helpful error messages
- Test one concept per test
```python
assert result == expected, f"Expected {expected}, got {result}"
```

### Mock Usage
- Mock external dependencies
- Don't mock code under test
- Verify mock was called correctly
```python
mock_function.assert_called_once_with(expected_arg)
```

## Troubleshooting

### Tests Pass Locally But Fail in CI
- Check for timing issues in async tests
- Verify all dependencies are installed
- Check for file system differences
- Look for environment-specific config

### Database Locked Errors
- Use separate temp database per test
- Ensure connections are closed
- Check for concurrent writes

### Async Test Issues
- Verify @pytest.mark.asyncio decorator
- Check asyncio_mode = auto in pytest.ini
- Ensure async fixtures use async def

### Coverage Not Meeting Threshold
```bash
pytest --cov=storj_monitor --cov-report=term-missing
```
Look for lines marked with !!!! (not covered)

## Additional Resources

- pytest Documentation
- pytest-asyncio Documentation
- pytest-cov Documentation
- Ruff Documentation

## Summary

The Storj Node Monitor project maintains high code quality through:
- ✅ Comprehensive test suite (>80% coverage)
- ✅ Unit and integration tests
- ✅ Automated code quality checks (Ruff)
- ✅ Continuous testing during development
- ✅ Clear testing documentation

Happy testing! 🧪