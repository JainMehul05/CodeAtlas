# Test configuration for AI Processing Lambda
# Run with: python -m pytest test/ -v

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Test constants
IDEMPOTENCY_TTL_SECONDS = 7 * 24 * 60 * 60