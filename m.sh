set -euo pipefail

# Ensure archive directory exists
mkdir -p docs/archive

# Remove earlier duplicates in archive (created previously)
rm -f docs/archive/ARCHITECTURE_DIAGRAM.md
rm -f docs/archive/TESTING.md

# Move design/older/reference docs to archive
git mv -f docs/API_INTEGRATION_DESIGN.md docs/archive/
git mv -f docs/ARCHITECTURE_DIAGRAM.md docs/archive/
git mv -f docs/COMPARISON_PERFORMANCE_OPTIMIZATIONS.md docs/archive/
git mv -f docs/DATABASE_CONCURRENCY_FIX.md docs/archive/
git mv -f docs/ENHANCEMENT_PROPOSALS.md docs/archive/
git mv -f docs/PERFORMANCE_OPTIMIZATIONS.md docs/archive/
git mv -f docs/PHASE_8_PROMPTS.md docs/archive/
git mv -f docs/PHASE_9_PROMPTS.md docs/archive/
git mv -f docs/STARTUP_EARNINGS_OPTIMIZATION.md docs/archive/
git mv -f docs/STARTUP_PERFORMANCE_OPTIMIZATIONS.md docs/archive/
git mv -f docs/STORAGE_FROM_LOGS_SOLUTION.md docs/archive/
git mv -f docs/TESTING.md docs/archive/

# Optional: commit changes
git add -A
git commit -m "docs: archive older design/prompt docs; keep only roadmap, status, and active prompts; add Phase 13"

