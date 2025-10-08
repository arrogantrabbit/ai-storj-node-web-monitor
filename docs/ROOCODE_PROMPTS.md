# RooCode Implementation Prompts - Master Guide

**Last Updated:** 2025-10-08  
**Current Status:** Phases 1-7 Complete, Phase 8+ Remaining

---

## 📋 Quick Navigation

- **Master Roadmap:** [`MASTER_ROADMAP.md`](MASTER_ROADMAP.md) - Complete project overview
- **Phase 8 Prompts:** [`PHASE_8_PROMPTS.md`](PHASE_8_PROMPTS.md) - Testing & Code Quality (PRIORITY #1)
- **Phase 9 Prompts:** [`PHASE_9_PROMPTS.md`](PHASE_9_PROMPTS.md) - Multi-Node Comparison (PRIORITY #2)
- **Completed Work:** [`completed/`](completed/) - All completed phase documentation

---

## 🎯 Current Priorities

### Priority #1: Phase 8 - Testing & Code Quality
**Status:** Not Started  
**Duration:** 1-2 weeks

Before implementing new features, establish comprehensive test coverage and code quality standards.

**Key Deliverables:**
- pytest framework with >80% coverage
- Comprehensive unit tests for all modules
- Integration tests for critical paths
- Ruff linting and formatting configured
- Pre-commit hooks and CI/CD pipeline
- Testing documentation

**Full Implementation Guide:** [`PHASE_8_PROMPTS.md`](PHASE_8_PROMPTS.md)

### Priority #2: Phase 9 - Multi-Node Comparison
**Status:** Not Started  
**Duration:** 1-1.5 weeks

Enable operators with multiple nodes to compare performance, earnings, and efficiency.

**Key Deliverables:**
- Backend comparison data model and calculations
- Frontend comparison UI with node selection
- Comparative metrics and rankings
- Visual comparison charts (bar, radar)
- Export functionality

**Full Implementation Guide:** [`PHASE_9_PROMPTS.md`](PHASE_9_PROMPTS.md)

---

## 📚 What's Been Completed (Phases 1-7)

All completed phases are fully documented in the [`completed/`](completed/) directory:

### ✅ Phase 1: Foundation & Critical Operations
- Extended node configuration with API auto-discovery
- API client infrastructure (`storj_api_client.py`)
- Reputation monitoring to prevent suspension
- Basic alert system with WebSocket

**Documentation:** [`completed/PHASE_1_COMPLETE.md`](completed/PHASE_1_COMPLETE.md)

### ✅ Phase 2: Performance & Capacity Monitoring
- Latency analytics with percentile calculations
- Storage capacity tracking and forecasting
- Duration extraction (sub-second precision)
- Proactive capacity alerts

**Documentation:** [`completed/PHASE_2_COMPLETE.md`](completed/PHASE_2_COMPLETE.md)

### ✅ Phase 3: Frontend UI Components
- ReputationCard with satellite-level scoring
- StorageHealthCard with historical trends
- LatencyCard with percentile analytics
- AlertsPanel with severity indicators

**Documentation:** [`completed/PHASE_3_COMPLETE.md`](completed/PHASE_3_COMPLETE.md)

### ✅ Phase 4: Intelligence & Advanced Features
- Analytics engine with statistical analysis
- Anomaly detection (Z-score based)
- Predictive analytics
- Enhanced alert manager with deduplication
- Insights generation

**Documentation:** [`completed/PHASE_4_COMPLETE.md`](completed/PHASE_4_COMPLETE.md)

### ✅ Phase 5-6: Financial Tracking
- Complete backend implementation (`financial_tracker.py`)
- API-based earnings with database fallback
- Per-satellite breakdown
- Historical payout import
- Month-end forecasting

**Documentation:** Backend complete (Phase 5-6), Frontend in Phase 5.5

### ✅ Phase 5.5: Financial Frontend Completion
- 12-month historical aggregation
- Earnings breakdown doughnut chart
- CSV export functionality
- ROI calculator
- Payout accuracy framework

**Documentation:** [`completed/PHASE_5.5_COMPLETE.md`](completed/PHASE_5.5_COMPLETE.md)

### ✅ Phase 7: Notification Channels
- Email notifications (SMTP)
- Discord/Slack webhook support
- Custom webhook integration
- Multi-channel routing

**Documentation:** [`completed/PHASE_7_COMPLETE.md`](completed/PHASE_7_COMPLETE.md)

---

## 🚧 Testing Requirements for All Future Phases

**IMPORTANT:** Starting with Phase 8, ALL phases must include comprehensive testing:

### Required for Each Phase:

1. **Unit Tests**
   - Target: >80% code coverage
   - Test all new functions and classes
   - Test edge cases and error handling
   - Use pytest with async support

2. **Integration Tests**
   - Test component interactions
   - Test API endpoint changes
   - Test WebSocket communication
   - Test database operations

3. **Code Quality**
   - All code must pass `ruff check` with no errors
   - Consistent formatting with `ruff format`
   - No unused imports or variables
   - Proper docstrings for all functions

4. **Documentation**
   - Update docstrings for new code
   - Create/update phase completion document
   - Update main README if needed
   - Document any new configuration options

### Test File Structure:
```
tests/
├── test_[module_name].py          # Unit tests
├── integration/
│   └── test_[feature]_flow.py     # Integration tests
├── fixtures/
│   └── [test_data_files]          # Test data
└── conftest.py                     # Shared fixtures
```

---

## 🎓 How to Use These Prompts

### For Phase 8 (Testing):
1. Read [`PHASE_8_PROMPTS.md`](PHASE_8_PROMPTS.md) completely
2. Execute prompts 8.1 through 8.9 in sequence
3. Each prompt builds on the previous one
4. Verify tests pass before moving to next prompt
5. Create completion document when done

### For Phase 9 (Multi-Node):
1. Ensure Phase 8 is complete first (testing infrastructure required)
2. Read [`PHASE_9_PROMPTS.md`](PHASE_9_PROMPTS.md) completely
3. Execute prompts 9.1 through 9.5 in sequence
4. Write tests as you implement (not after)
5. Verify all tests pass and coverage >80%
6. Create completion document when done

### For Future Phases:
Detailed prompts for Phases 10-12 will be created after Phase 9 completion. They will follow the same structure with integrated testing requirements.

---

## 📖 Prompt Structure

Each phase has detailed prompts organized as:

### Prompt X.Y: [Component Name]
- **Overview:** What this prompt accomplishes
- **Code Examples:** Specific implementation code
- **Testing:** Required tests for this component
- **Success Criteria:** How to verify completion

Example format:
```
Implement X functionality:

1. Create new file/modify existing file
2. Add specific functions/classes
3. Write corresponding tests
4. Verify tests pass
5. Check code quality with ruff

Success criteria:
- [ ] Feature works as expected
- [ ] Tests pass with >80% coverage
- [ ] Ruff check returns no errors
- [ ] Documentation updated
```

---

## 🔧 Development Workflow

### Standard Workflow for Each Phase:

1. **Planning**
   - Review phase prompts completely
   - Understand dependencies and prerequisites
   - Create feature branch: `git checkout -b phase-X-[feature]`

2. **Implementation**
   - Follow prompts in sequence
   - Write tests alongside code (TDD approach)
   - Run tests frequently: `pytest -v`
   - Check code quality: `ruff check storj_monitor/`

3. **Testing**
   - Verify all unit tests pass
   - Run integration tests
   - Check coverage: `pytest --cov=storj_monitor --cov-report=term-missing`
   - Ensure >80% coverage achieved

4. **Quality Checks**
   - Run `ruff check --fix storj_monitor/` to auto-fix issues
   - Run `ruff format storj_monitor/` to format code
   - Verify all checks pass: `ruff check storj_monitor/ --no-fix`
   - Test in browser (for frontend changes)

5. **Documentation**
   - Update docstrings
   - Create phase completion document
   - Update README if needed
   - Update MASTER_ROADMAP.md progress

6. **Completion**
   - Create `docs/completed/PHASE_X_COMPLETE.md`
   - Merge feature branch to main
   - Tag release: `git tag v0.X.0`

---

## 🎯 Quick Command Reference

### Running Tests
```bash
# All tests
pytest

# With coverage
pytest --cov=storj_monitor --cov-report=html

# Specific test file
pytest tests/test_database.py -v

# Watch mode (requires pytest-watch)
ptw -- -v
```

### Code Quality
```bash
# Check code
ruff check storj_monitor/ tests/

# Auto-fix issues
ruff check --fix storj_monitor/ tests/

# Format code
ruff format storj_monitor/ tests/

# Check without fixing
ruff check storj_monitor/ tests/ --no-fix
```

### Pre-commit
```bash
# Install hooks
pre-commit install

# Run on all files
pre-commit run --all-files

# Update hooks
pre-commit autoupdate
```

---

## 📊 Phase Status Overview

| Phase | Status | Priority | Duration | Documentation |
|-------|--------|----------|----------|---------------|
| 1 | ✅ Complete | - | - | [PHASE_1_COMPLETE.md](completed/PHASE_1_COMPLETE.md) |
| 2 | ✅ Complete | - | - | [PHASE_2_COMPLETE.md](completed/PHASE_2_COMPLETE.md) |
| 3 | ✅ Complete | - | - | [PHASE_3_COMPLETE.md](completed/PHASE_3_COMPLETE.md) |
| 4 | ✅ Complete | - | - | [PHASE_4_COMPLETE.md](completed/PHASE_4_COMPLETE.md) |
| 5-6 | ✅ Complete | - | - | Backend complete |
| 5.5 | ✅ Complete | - | - | [PHASE_5.5_COMPLETE.md](completed/PHASE_5.5_COMPLETE.md) |
| 7 | ✅ Complete | - | - | [PHASE_7_COMPLETE.md](completed/PHASE_7_COMPLETE.md) |
| **8** | 🚧 Not Started | 🔴 Critical | 1-2 weeks | [PHASE_8_PROMPTS.md](PHASE_8_PROMPTS.md) |
| **9** | 📋 Planned | 🟠 High | 1-1.5 weeks | [PHASE_9_PROMPTS.md](PHASE_9_PROMPTS.md) |
| 10 | 📋 Planned | 🟡 Medium | 1.5-2 weeks | To be created |
| 11 | 📋 Planned | 🟡 Medium | 1-1.5 weeks | To be created |
| 12 | 📋 Planned | 🟢 Low | 1.5-2 weeks | To be created |

**Overall Progress:** ~70% Complete (Phases 1-7) | ~30% Remaining (Phases 8-12)

---

## 🎉 Success Stories

### What's Working Well

- **Complete Monitoring:** Reputation, storage, performance fully implemented
- **Intelligent Alerts:** Anomaly detection and predictive analytics operational
- **Financial Tracking:** Complete earnings visualization with ROI calculator
- **Multi-Channel Notifications:** Email, Discord, Slack all working
- **Real-time Updates:** WebSocket communication reliable
- **Dark Mode:** Full dark mode support across all components

### What's Next

1. **Testing Foundation** (Phase 8) - Ensure reliability and maintainability
2. **Multi-Node Features** (Phase 9) - Fleet management capabilities
3. **Advanced Reporting** (Phase 10) - Professional reports and exports
4. **User Configuration** (Phase 11) - Self-service settings management
5. **Mobile Experience** (Phase 12) - PWA and mobile optimization

---

## 🆘 Getting Help

### Common Issues

**Issue:** Tests fail with "database is locked"
**Solution:** Use `temp_db` fixture from conftest.py for each test

**Issue:** Async tests not running
**Solution:** Add `@pytest.mark.asyncio` decorator and ensure `asyncio_mode = auto` in pytest.ini

**Issue:** Coverage below 80%
**Solution:** Run `pytest --cov-report=term-missing` to see uncovered lines

**Issue:** Ruff errors after implementation
**Solution:** Run `ruff check --fix` then manually fix remaining issues

### Resources

- **pytest documentation:** https://docs.pytest.org/
- **ruff documentation:** https://docs.astral.sh/ruff/
- **Chart.js documentation:** https://www.chartjs.org/docs/
- **aiohttp documentation:** https://docs.aiohttp.org/

---

## 📝 Notes

- All prompts are designed to be copy-pasted into Code mode
- Each prompt is self-contained with full context
- Testing is integrated into all future phases
- Code quality is enforced via ruff and pre-commit
- Documentation is updated with each phase

---

## 🚀 Ready to Start?

1. **For Phase 8:** Open [`PHASE_8_PROMPTS.md`](PHASE_8_PROMPTS.md) and start with Prompt 8.1
2. **For Phase 9:** Wait until Phase 8 is complete, then open [`PHASE_9_PROMPTS.md`](PHASE_9_PROMPTS.md)
3. **For Phase 10+:** These will be created after Phase 9 completion

**Remember:** Quality over speed. Take time to write good tests and maintain clean code!

---

**Last Updated:** 2025-10-08  
**Maintained By:** Project Architect Mode  
**Next Review:** After Phase 9 completion
