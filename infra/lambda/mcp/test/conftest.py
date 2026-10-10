"""
Pytest configuration for MCP Lambda tests.
This runs BEFORE test collection to ensure mocks are in place.
"""

import sys
import os
from decimal import Decimal
from unittest.mock import MagicMock

# Add paths before any imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared', 'python'))


def make_respond():
    """Create a real respond function for testing."""
    def convert_decimals(obj):
        if isinstance(obj, list):
            return [convert_decimals(item) for item in obj]
        elif isinstance(obj, dict):
            return {key: convert_decimals(value) for key, value in obj.items()}
        elif isinstance(obj, Decimal):
            if obj % 1 == 0:
                return int(obj)
            else:
                return float(obj)
        return obj
    
    def respond(status_code, body):
        body = convert_decimals(body)
        return {
            'statusCode': status_code,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            },
            'body': __import__('json').dumps(body)
        }
    return respond


# Patch flowsync_common modules BEFORE any test imports them
from unittest.mock import MagicMock

mock_helpers = MagicMock()
mock_helpers.respond = make_respond()
mock_helpers.convert_decimals = lambda x: x
mock_helpers.strip_embeddings = lambda x: x
mock_helpers.call_titan_embedding = lambda text, client: [0.1] * 1536
mock_helpers.cosine_similarity = lambda a, b: 0.5
mock_helpers.convert_floats_to_decimal = lambda x: x
mock_helpers.search_context_rag = MagicMock(return_value={'answer': 'Test answer', 'answerGrounded': True, 'sources': []})
mock_helpers.search_context_rag_with_graph = MagicMock(return_value={'answer': 'Test answer', 'answerGrounded': True, 'sources': []})

mock_auth = MagicMock()
mock_auth.authenticate = MagicMock(return_value={'success': True, 'project': {'projectId': 'test-project', 'apiTokenHash': 'salt:hash'}})

# Patch sys.modules BEFORE any test imports
import sys
sys.modules['flowsync_common'] = MagicMock(helpers=MagicMock(), auth=MagicMock())
sys.modules['flowsync_common.helpers'] = MagicMock(
    respond=make_respond(),
    convert_decimals=lambda x: x,
    strip_embeddings=lambda x: x,
    call_titan_embedding=lambda text, client: [0.1] * 1536,
    cosine_similarity=lambda a, b: 0.5,
    convert_floats_to_decimal=lambda x: x,
    search_context_rag=MagicMock(return_value={'answer': 'Test answer', 'answerGrounded': True, 'sources': []}),
    search_context_rag_with_graph=MagicMock(return_value={'answer': 'Test answer', 'answerGrounded': True, 'sources': []}),
)
sys.modules['flowsync_common.auth'] = MagicMock(
    authenticate=MagicMock(return_value={'success': True, 'project': {'projectId': 'test-project', 'apiTokenHash': 'salt:hash'}})
)

# Also patch boto3 before any imports
# We'll patch boto3 in the test fixtures instead of here to avoid issues