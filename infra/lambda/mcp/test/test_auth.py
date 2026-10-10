"""
Pytest tests for MCP Lambda Authentication
Tests authentication and authorization for MCP tools.

Run with: python -m pytest test/test_auth.py -v
"""

import json
import os
import sys
import pytest
import uuid
import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock, patch, MagicMock
from botocore.exceptions import ClientError

# Set AWS region before importing
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')

# Add mcp handler path and shared python path to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared', 'python'))


# ─── Helper functions ───

def make_token_hash(token: str, salt: str) -> str:
    """Create a token hash using the same algorithm as the auth module."""
    derived = hashlib.scrypt(
        token.encode(),
        salt=salt.encode('utf-8'),
        n=16384,
        r=8,
        p=1,
        dklen=64
    )
    return f"{salt}:{derived.hex()}"


def make_api_gateway_event(method='POST', path='/mcp', body=None, headers=None, path_params=None, query_params=None):
    """Create a mock API Gateway event."""
    return {
        'httpMethod': method,
        'resource': path,
        'path': path,
        'body': json.dumps(body) if body else '{}',
        'headers': headers or {},
        'pathParameters': path_params,
        'queryStringParameters': query_params,
        'requestContext': {
            'requestId': 'test-request-id',
            'authorizer': {}
        }
    }


def make_token_hash_for_test(token: str) -> str:
    """Create a token hash using a fixed salt for testing."""
    salt = 'abcdef1234567890'  # 16 bytes = 32 hex chars
    derived = hashlib.scrypt(
        token.encode(),
        salt=salt.encode('utf-8'),
        n=16384,
        r=8,
        p=1,
        dklen=64
    )
    return f"{salt}:{derived.hex()}"


# ─── Helper functions for mocks ───

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
            'body': json.dumps(body)
        }
    return respond


# ─── Session-scoped module patching ───

@pytest.fixture(scope="session", autouse=True)
def patch_flowsync_common_modules():
    """
    Patch flowsync_common modules at session level so all tests use mocks.
    This runs once before any tests and stays active for the entire session.
    """
    # Store original modules if they exist
    original_modules = {}
    for mod_name in ['flowsync_common', 'flowsync_common.helpers', 'flowsync_common.auth']:
        if mod_name in sys.modules:
            original_modules[mod_name] = sys.modules[mod_name]
    
    # Create mocks
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
    
    # Replace with mocks
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
    
    yield
    
    # Restore original modules
    for mod_name, mod in original_modules.items():
        sys.modules[mod_name] = mod
    for mod_name in ['flowsync_common', 'flowsync_common.helpers', 'flowsync_common.auth']:
        if mod_name in sys.modules and mod_name not in original_modules:
            del sys.modules[mod_name]


# ─── Helper functions ───

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
            'body': json.dumps(body)
        }
    return respond


def make_token_hash(token: str, salt: str) -> str:
    """Create a token hash using the same algorithm as the auth module."""
    derived = hashlib.scrypt(
        token.encode(),
        salt=salt.encode('utf-8'),
        n=16384,
        r=8,
        p=1,
        dklen=64
    )
    return f"{salt}:{derived.hex()}"


def make_api_gateway_event(method='POST', path='/mcp', body=None, headers=None, path_params=None, query_params=None):
    """Create a mock API Gateway event."""
    return {
        'httpMethod': method,
        'resource': path,
        'path': path,
        'body': json.dumps(body) if body else '{}',
        'headers': headers or {},
        'pathParameters': path_params,
        'queryStringParameters': query_params,
        'requestContext': {
            'requestId': 'test-request-id',
            'authorizer': {}
        }
    }


def make_token_hash_for_test(token: str) -> str:
    """Create a token hash using a fixed salt for testing."""
    salt = 'abcdef1234567890'  # 16 bytes = 32 hex chars
    derived = hashlib.scrypt(
        token.encode(),
        salt=salt.encode('utf-8'),
        n=16384,
        r=8,
        p=1,
        dklen=64
    )
    return f"{salt}:{derived.hex()}"


# ─── Test fixtures for boto3 patching ───

@pytest.fixture
def mock_dynamodb_resource():
    """Create a fresh mock DynamoDB resource for each test."""
    mock_dynamodb = MagicMock()
    mock_context_table = Mock()
    mock_projects_table = Mock()
    mock_audit_table = Mock()
    mock_cache_table = Mock()
    mock_entities_table = Mock()
    mock_relationships_table = Mock()

    def mock_table(name):
        if name == "flowsync-context":
            return mock_context_table
        elif name == "flowsync-projects":
            return mock_projects_table
        elif name == "flowsync-audit":
            return mock_audit_table
        elif name == "flowsync-cache":
            return mock_cache_table
        elif name == "flowsync-graph-entities":
            return mock_entities_table
        elif name == "flowsync-graph-relationships":
            return mock_relationships_table
        return Mock()

    mock_dynamodb.Table.side_effect = mock_table
    return {
        'dynamodb': mock_dynamodb,
        'context_table': mock_context_table,
        'projects_table': mock_projects_table,
        'audit_table': mock_audit_table,
        'cache_table': mock_cache_table,
        'entities_table': mock_entities_table,
        'relationships_table': mock_relationships_table,
        'dynamodb': mock_dynamodb,
    }


@pytest.fixture
def mock_bedrock_client():
    """Create a mock Bedrock client."""
    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = {
        'output': {'message': {'content': [{'text': json.dumps({
            'answer': 'Test answer',
            'answerGrounded': True,
            'citedSources': ['abc123']
        })}]}},
        'usage': {'inputTokens': 100, 'outputTokens': 50}
    }
    mock_bedrock.invoke_model.return_value = {
        'body': Mock(read=Mock(return_value=json.dumps({'embedding': [0.1] * 1536}).encode()))
    }
    return mock_bedrock


# ─── Per-test fixtures for boto3 patching ───

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
            'body': json.dumps(body)
        }
    return respond


def make_mock_helpers():
    """Create a mock helpers module with real respond function."""
    mock_helpers = MagicMock()
    mock_helpers.respond = make_respond()
    mock_helpers.convert_decimals = lambda x: x
    mock_helpers.strip_embeddings = lambda x: x
    mock_helpers.call_titan_embedding = lambda text, client: [0.1] * 1536
    mock_helpers.cosine_similarity = lambda a, b: 0.5
    mock_helpers.convert_floats_to_decimal = lambda x: x
    mock_helpers.search_context_rag = MagicMock(return_value={'answer': 'Test answer', 'answerGrounded': True, 'sources': []})
    mock_helpers.search_context_rag_with_graph = MagicMock(return_value={'answer': 'Test answer', 'answerGrounded': True, 'sources': []})
    return mock_helpers


def make_mock_auth():
    """Create a mock auth module with configurable authenticate function."""
    mock_auth = MagicMock()
    mock_auth.authenticate = MagicMock()
    return mock_auth


# ─── Per-test fixtures for boto3 patching ───

@pytest.fixture
def mock_dynamodb_resource():
    """Create a fresh mock DynamoDB resource for each test."""
    mock_dynamodb = MagicMock()
    mock_context_table = Mock()
    mock_projects_table = Mock()
    mock_audit_table = Mock()
    mock_cache_table = Mock()
    mock_entities_table = Mock()
    mock_relationships_table = Mock()

    def mock_table(name):
        if name == "flowsync-context":
            return mock_context_table
        elif name == "flowsync-projects":
            return mock_projects_table
        elif name == "flowsync-audit":
            return mock_audit_table
        elif name == "flowsync-cache":
            return mock_cache_table
        elif name == "flowsync-graph-entities":
            return mock_entities_table
        elif name == "flowsync-graph-relationships":
            return mock_relationships_table
        return Mock()

    mock_dynamodb.Table.side_effect = mock_table
    return {
        'dynamodb_resource': mock_dynamodb,
        'context_table': mock_context_table,
        'projects_table': mock_projects_table,
        'audit_table': mock_audit_table,
        'cache_table': mock_cache_table,
        'entities_table': mock_entities_table,
        'relationships_table': mock_relationships_table,
        'dynamodb': mock_dynamodb,
    }


@pytest.fixture
def mock_bedrock_client():
    """Create a mock Bedrock client."""
    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = {
        'output': {'message': {'content': [{'text': json.dumps({
            'answer': 'Test answer',
            'answerGrounded': True,
            'citedSources': ['abc123']
        })}]}},
        'usage': {'inputTokens': 100, 'outputTokens': 50}
    }
    mock_bedrock.invoke_model.return_value = {
        'body': Mock(read=Mock(return_value=json.dumps({'embedding': [0.1] * 1536}).encode()))
    }
    return mock_bedrock


# ─── Per-test fixtures for boto3 patching ───

@pytest.fixture(autouse=True)
def patch_boto3_and_import(mock_dynamodb_resource, mock_bedrock_client, monkeypatch):
    """Patch boto3 and import handler module with proper mocks."""
    monkeypatch.setattr('flowsync_common.helpers', MagicMock(
        respond=make_respond(),
        convert_decimals=lambda x: x,
        strip_embeddings=lambda x: x,
        call_titan_embedding=lambda text, client: [0.1] * 1536,
        cosine_similarity=lambda a, b: 0.5,
        convert_floats_to_decimal=lambda x: x,
        search_context_rag=MagicMock(return_value={'answer': 'Test answer', 'answerGrounded': True, 'sources': []}),
        search_context_rag_with_graph=MagicMock(return_value={'answer': 'Test answer', 'answerGrounded': True, 'sources': []}),
    ))
    # Don't override flowsync_common.auth - use the session-scoped mock
    monkeypatch.setattr('boto3.resource', lambda *args, **kwargs: mock_dynamodb_resource['dynamodb'])
    monkeypatch.setattr('boto3.client', lambda *args, **kwargs: mock_bedrock_client)
    
    # Import handler with patches active
    import handler
    handler.dynamodb = mock_dynamodb_resource['dynamodb_resource']
    handler.bedrock_client = mock_bedrock_client
    handler.cloudwatch = MagicMock()
    yield {'handler': handler, 'tables': {
        'context_table': mock_dynamodb_resource['context_table'],
        'projects_table': mock_dynamodb_resource['projects_table'],
        'audit_table': mock_dynamodb_resource['audit_table'],
        'cache_table': mock_dynamodb_resource['cache_table'],
        'entities_table': mock_dynamodb_resource['entities_table'],
        'relationships_table': mock_dynamodb_resource['relationships_table'],
        'dynamodb': mock_dynamodb_resource['dynamodb'],
        'dynamodb_resource': mock_dynamodb_resource['dynamodb_resource'],
    }}


@pytest.fixture
def mock_env():
    """Set up environment variables for tests."""
    original_env = dict(os.environ)
    os.environ.update({
        'CONTEXT_TABLE': 'flowsync-context',
        'PROJECTS_TABLE': 'flowsync-projects',
        'AUDIT_TABLE': 'flowsync-audit',
        'CACHE_TABLE': 'flowsync-cache',
        'FALLBACK_MODEL_ID': 'us.amazon.nova-lite-v1:0',
        'GRAPH_ENTITIES_TABLE': 'flowsync-graph-entities',
        'GRAPH_RELATIONSHIPS_TABLE': 'flowsync-graph-relationships',
    })
    yield
    os.environ.clear()
    os.environ.update(original_env)


def create_api_gateway_event(method='POST', path='/mcp', body=None, headers=None, path_params=None, query_params=None):
    """Create a mock API Gateway event."""
    return {
        'httpMethod': method,
        'resource': path,
        'path': path,
        'body': json.dumps(body) if body else '{}',
        'headers': headers or {},
        'pathParameters': path_params,
        'queryStringParameters': query_params,
        'requestContext': {
            'requestId': 'test-request-id',
            'authorizer': {}
        }
    }


def make_token_hash(token: str, salt: str = 'abcdef1234567890') -> str:
    """Create a token hash using the same algorithm as the auth module."""
    derived = hashlib.scrypt(
        token.encode(),
        salt=salt.encode('utf-8'),
        n=16384,
        r=8,
        p=1,
        dklen=64
    )
    return f"{salt}:{derived.hex()}"


def create_test_entity(entity_id, entity_type='commit', project_id='test-project', **kwargs):
    """Create a test graph entity."""
    base = {
        'entityId': entity_id,
        'entityType': entity_type,
        'projectId': project_id,
        'repositoryId': 'repo:github:owner:repo',
        'createdAt': '2024-01-01T00:00:00Z',
        'updatedAt': '2024-01-01T00:00:00Z',
        'schemaVersion': '1',
    }
    base.update(kwargs)
    return base


def create_test_relationship(rel_type='modifies', source_id='commit:repo:test:sha', target_id='file:repo:test:file.py', **kwargs):
    """Create a test graph relationship."""
    base = {
        'relationshipId': f'rel:{rel_type}:{source_id}:{target_id}',
        'relationshipType': rel_type,
        'sourceEntityId': source_id,
        'sourceEntityType': 'commit',
        'targetEntityId': target_id,
        'targetEntityType': 'file',
        'projectId': 'test-project',
        'repositoryId': 'repo:github:owner:repo',
        'provenance': 'explicit',
        'evidence': 'evt-1',
        'createdAt': '2024-01-01T00:00:00Z',
        'updatedAt': '2024-01-01T00:00:00Z',
        'schemaVersion': '1',
    }
    base.update(kwargs)
    return base


def create_api_gateway_event(method='POST', path='/mcp', body=None, headers=None, path_params=None, query_params=None):
    """Create a mock API Gateway event."""
    return {
        'httpMethod': method,
        'resource': path,
        'path': path,
        'body': json.dumps(body) if body else '{}',
        'headers': headers or {},
        'pathParameters': path_params,
        'queryStringParameters': query_params,
        'requestContext': {
            'requestId': 'test-request-id',
            'authorizer': {}
        }
    }


def make_token_hash(token: str, salt: str = 'abcdef1234567890') -> str:
    """Create a token hash using the same algorithm as the auth module."""
    derived = hashlib.scrypt(
        token.encode(),
        salt=salt.encode('utf-8'),
        n=16384,
        r=8,
        p=1,
        dklen=64
    )
    return f"{salt}:{derived.hex()}"


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
            'body': json.dumps(body)
        }
    return respond


def make_token_hash(token: str, salt: str = 'abcdef1234567890') -> str:
    """Create a token hash using the same algorithm as the auth module."""
    derived = hashlib.scrypt(
        token.encode(),
        salt=salt.encode('utf-8'),
        n=16384,
        r=8,
        p=1,
        dklen=64
    )
    return f"{salt}:{derived.hex()}"


def create_test_entity(entity_id, entity_type='commit', project_id='test-project', **kwargs):
    """Create a test graph entity."""
    base = {
        'entityId': entity_id,
        'entityType': entity_type,
        'projectId': project_id,
        'repositoryId': 'repo:github:owner:repo',
        'createdAt': '2024-01-01T00:00:00Z',
        'updatedAt': '2024-01-01T00:00:00Z',
        'schemaVersion': '1',
    }
    base.update(kwargs)
    return base


def create_test_relationship(rel_type='modifies', source_id='commit:repo:test:sha', target_id='file:repo:test:file.py', **kwargs):
    """Create a test graph relationship."""
    base = {
        'relationshipId': f'rel:{rel_type}:{source_id}:{target_id}',
        'relationshipType': rel_type,
        'sourceEntityId': source_id,
        'sourceEntityType': 'commit',
        'targetEntityId': target_id,
        'targetEntityType': 'file',
        'projectId': 'test-project',
        'repositoryId': 'repo:github:owner:repo',
        'provenance': 'explicit',
        'evidence': 'evt-1',
        'createdAt': '2024-01-01T00:00:00Z',
        'updatedAt': '2024-01-01T00:00:00Z',
        'schemaVersion': '1',
    }
    base.update(kwargs)
    return base


def create_api_gateway_event(method='POST', path='/mcp', body=None, headers=None, path_params=None, query_params=None):
    """Create a mock API Gateway event."""
    return {
        'httpMethod': method,
        'resource': path,
        'path': path,
        'body': json.dumps(body) if body else '{}',
        'headers': headers or {},
        'pathParameters': path_params,
        'queryStringParameters': query_params,
        'requestContext': {
            'requestId': 'test-request-id',
            'authorizer': {}
        }
    }


def make_token_hash(token: str, salt: str = 'abcdef1234567890') -> str:
    """Create a token hash using the same algorithm as the auth module."""
    derived = hashlib.scrypt(
        token.encode(),
        salt=salt.encode('utf-8'),
        n=16384,
        r=8,
        p=1,
        dklen=64
    )
    return f"{salt}:{derived.hex()}"


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
            'body': json.dumps(body)
        }
    return respond


def make_token_hash(token: str, salt: str = 'abcdef1234567890') -> str:
    """Create a token hash using the same algorithm as the auth module."""
    derived = hashlib.scrypt(
        token.encode(),
        salt=salt.encode('utf-8'),
        n=16384,
        r=8,
        p=1,
        dklen=64
    )
    return f"{salt}:{derived.hex()}"


def create_test_entity(entity_id, entity_type='commit', project_id='test-project', **kwargs):
    """Create a test graph entity."""
    base = {
        'entityId': entity_id,
        'entityType': entity_type,
        'projectId': project_id,
        'repositoryId': 'repo:github:owner:repo',
        'createdAt': '2024-01-01T00:00:00Z',
        'updatedAt': '2024-01-01T00:00:00Z',
        'schemaVersion': '1',
    }
    base.update(kwargs)
    return base


def create_test_relationship(rel_type='modifies', source_id='commit:repo:test:sha', target_id='file:repo:test:file.py', **kwargs):
    """Create a test graph relationship."""
    base = {
        'relationshipId': f'rel:{rel_type}:{source_id}:{target_id}',
        'relationshipType': rel_type,
        'sourceEntityId': source_id,
        'sourceEntityType': 'commit',
        'targetEntityId': target_id,
        'targetEntityType': 'file',
        'projectId': 'test-project',
        'repositoryId': 'repo:github:owner:repo',
        'provenance': 'explicit',
        'evidence': 'evt-1',
        'createdAt': '2024-01-01T00:00:00Z',
        'updatedAt': '2024-01-01T00:00:00Z',
        'schemaVersion': '1',
    }
    base.update(kwargs)
    return base


def create_api_gateway_event(method='POST', path='/mcp', body=None, headers=None, path_params=None, query_params=None):
    """Create a mock API Gateway event."""
    return {
        'httpMethod': method,
        'resource': path,
        'path': path,
        'body': json.dumps(body) if body else '{}',
        'headers': headers or {},
        'pathParameters': path_params,
        'queryStringParameters': query_params,
        'requestContext': {
            'requestId': 'test-request-id',
            'authorizer': {}
        }
    }


def make_token_hash(token: str, salt: str = 'abcdef1234567890') -> str:
    """Create a token hash using the same algorithm as the auth module."""
    derived = hashlib.scrypt(
        token.encode(),
        salt=salt.encode('utf-8'),
        n=16384,
        r=8,
        p=1,
        dklen=64
    )
    return f"{salt}:{derived.hex()}"


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
            'body': json.dumps(body)
        }
    return respond


def make_token_hash(token: str, salt: str = 'abcdef1234567890') -> str:
    """Create a token hash using the same algorithm as the auth module."""
    derived = hashlib.scrypt(
        token.encode(),
        salt=salt.encode('utf-8'),
        n=16384,
        r=8,
        p=1,
        dklen=64
    )
    return f"{salt}:{derived.hex()}"


def create_test_entity(entity_id, entity_type='commit', project_id='test-project', **kwargs):
    """Create a test graph entity."""
    base = {
        'entityId': entity_id,
        'entityType': entity_type,
        'projectId': project_id,
        'repositoryId': 'repo:github:owner:repo',
        'createdAt': '2024-01-01T00:00:00Z',
        'updatedAt': '2024-01-01T00:00:00Z',
        'schemaVersion': '1',
    }
    base.update(kwargs)
    return base


def create_test_relationship(rel_type='modifies', source_id='commit:repo:test:sha', target_id='file:repo:test:file.py', **kwargs):
    """Create a test graph relationship."""
    base = {
        'relationshipId': f'rel:{rel_type}:{source_id}:{target_id}',
        'relationshipType': rel_type,
        'sourceEntityId': source_id,
        'sourceEntityType': 'commit',
        'targetEntityId': target_id,
        'targetEntityType': 'file',
        'projectId': 'test-project',
        'repositoryId': 'repo:github:owner:repo',
        'provenance': 'explicit',
        'evidence': 'evt-1',
        'createdAt': '2024-01-01T00:00:00Z',
        'updatedAt': '2024-01-01T00:00:00Z',
        'schemaVersion': '1',
    }
    base.update(kwargs)
    return base


def create_api_gateway_event(method='POST', path='/mcp', body=None, headers=None, path_params=None, query_params=None):
    """Create a mock API Gateway event."""
    return {
        'httpMethod': method,
        'resource': path,
        'path': path,
        'body': json.dumps(body) if body else '{}',
        'headers': headers or {},
        'pathParameters': path_params,
        'queryStringParameters': query_params,
        'requestContext': {
            'requestId': 'test-request-id',
            'authorizer': {}
        }
    }


def make_token_hash(token: str, salt: str = 'abcdef1234567890') -> str:
    """Create a token hash using the same algorithm as the auth module."""
    derived = hashlib.scrypt(
        token.encode(),
        salt=salt.encode('utf-8'),
        n=16384,
        r=8,
        p=1,
        dklen=64
    )
    return f"{salt}:{derived.hex()}"


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
            'body': json.dumps(body)
        }
    return respond


def make_token_hash(token: str, salt: str = 'abcdef1234567890') -> str:
    """Create a token hash using the same algorithm as the auth module."""
    derived = hashlib.scrypt(
        token.encode(),
        salt=salt.encode('utf-8'),
        n=16384,
        r=8,
        p=1,
        dklen=64
    )
    return f"{salt}:{derived.hex()}"


def create_test_entity(entity_id, entity_type='commit', project_id='test-project', **kwargs):
    """Create a test graph entity."""
    base = {
        'entityId': entity_id,
        'entityType': entity_type,
        'projectId': project_id,
        'repositoryId': 'repo:github:owner:repo',
        'createdAt': '2024-01-01T00:00:00Z',
        'updatedAt': '2024-01-01T00:00:00Z',
        'schemaVersion': '1',
    }
    base.update(kwargs)
    return base


def create_test_relationship(rel_type='modifies', source_id='commit:repo:test:sha', target_id='file:repo:test:file.py', **kwargs):
    """Create a test graph relationship."""
    base = {
        'relationshipId': f'rel:{rel_type}:{source_id}:{target_id}',
        'relationshipType': rel_type,
        'sourceEntityId': source_id,
        'sourceEntityType': 'commit',
        'targetEntityId': target_id,
        'targetEntityType': 'file',
        'projectId': 'test-project',
        'repositoryId': 'repo:github:owner:repo',
        'provenance': 'explicit',
        'evidence': 'evt-1',
        'createdAt': '2024-01-01T00:00:00Z',
        'updatedAt': '2024-01-01T00:00:00Z',
        'schemaVersion': '1',
    }
    base.update(kwargs)
    return base


def create_api_gateway_event(method='POST', path='/mcp', body=None, headers=None, path_params=None, query_params=None):
    """Create a mock API Gateway event."""
    return {
        'httpMethod': method,
        'resource': path,
        'path': path,
        'body': json.dumps(body) if body else '{}',
        'headers': headers or {},
        'pathParameters': path_params,
        'queryStringParameters': query_params,
        'requestContext': {
            'requestId': 'test-request-id',
            'authorizer': {}
        }
    }


def make_token_hash(token: str, salt: str = 'abcdef1234567890') -> str:
    """Create a token hash using the same algorithm as the auth module."""
    derived = hashlib.scrypt(
        token.encode(),
        salt=salt.encode('utf-8'),
        n=16384,
        r=8,
        p=1,
        dklen=64
    )
    return f"{salt}:{derived.hex()}"


# ─── Tests ───

class TestMCPAuthentication:
    """Tests for MCP Lambda authentication."""

    def test_missing_authorization_header(self, patch_boto3_and_import):
        """Request without Authorization header should return 401."""
        handler = patch_boto3_and_import['handler']
        
        # Mock the authenticate function to return failure for missing token
        import handler as handler_module
        handler_module.authenticate.return_value = {
            'success': False,
            'error': {'error': 'invalid_token', 'message': 'Missing Authorization header'},
            'statusCode': 401
        }
        
        event = create_api_gateway_event(
            body={'tool': 'get_project_context', 'params': {'projectId': 'test-project', 'branch': 'main'}},
            headers={'Content-Type': 'application/json'}  # No Authorization header
        )
        
        result = handler.handler(event, {})
        
        assert result['statusCode'] == 401
        body = json.loads(result['body'])
        assert body['error'] == 'invalid_token'
        assert 'Missing Authorization header' in body['message']

    def test_invalid_token(self, patch_boto3_and_import):
        """Request with invalid token should return 401."""
        handler = patch_boto3_and_import['handler']
        
        # Mock the authenticate function to return failure
        import handler as handler_module
        handler_module.authenticate.return_value = {
            'success': False,
            'error': {'error': 'invalid_token', 'message': 'Token verification failed'},
            'statusCode': 401
        }
        
        event = create_api_gateway_event(
            body={'tool': 'get_project_context', 'params': {'projectId': 'test-project', 'branch': 'main'}},
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer invalid-token'}
        )
        
        result = handler.handler(event, {})
        
        assert result['statusCode'] == 401
        body = json.loads(result['body'])
        assert body['error'] == 'invalid_token'

    def test_valid_token(self, patch_boto3_and_import):
        """Request with valid token should succeed."""
        handler = patch_boto3_and_import['handler']
        
        # Mock the authenticate function to return success
        import handler as handler_module
        handler_module.authenticate.return_value = {
            'success': True,
            'project': {'projectId': 'test-project', 'apiTokenHash': 'salt:hash'}
        }
        
        # Mock context table for get_project_context
        tables = patch_boto3_and_import['tables']
        tables['context_table'].query.return_value = {
            'Items': [],
            'Count': 0
        }
        
        event = create_api_gateway_event(
            body={'tool': 'get_project_context', 'params': {'projectId': 'test-project', 'branch': 'main'}},
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer valid-token'}
        )
        
        result = handler.handler(event, {})
        
        # Should succeed (200) - the tool will execute
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        # The tool returns recentContext array (possibly empty but no error)
        assert 'error' not in body

    def test_unauthorized_project_access(self, patch_boto3_and_import):
        """Request for a project that doesn't exist should return 404."""
        handler = patch_boto3_and_import['handler']
        
        import handler as handler_module
        handler_module.authenticate.return_value = {
            'success': False,
            'error': {'error': 'project_not_found', 'message': 'Project nonexistent-project not found'},
            'statusCode': 404
        }
        
        event = create_api_gateway_event(
            body={'tool': 'get_project_context', 'params': {'projectId': 'nonexistent-project', 'branch': 'main'}},
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer some-token'}
        )
        
        result = handler.handler(event, {})
        
        assert result['statusCode'] == 404
        body = json.loads(result['body'])
        assert body['error'] == 'project_not_found'

    def test_project_without_api_token(self, patch_boto3_and_import):
        """Project without apiTokenHash should return 500."""
        handler = patch_boto3_and_import['handler']
        
        import handler as handler_module
        handler_module.authenticate.return_value = {
            'success': False,
            'error': {'error': 'invalid_configuration', 'message': 'Project has no API token configured'},
            'statusCode': 500
        }
        
        event = create_api_gateway_event(
            body={'tool': 'get_project_context', 'params': {'projectId': 'test-project', 'branch': 'main'}},
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer some-token'}
        )
        
        result = handler.handler(event, {})
        
        assert result['statusCode'] == 500
        body = json.loads(result['body'])
        assert body['error'] == 'invalid_configuration'

    def test_tool_without_project_id_skips_auth(self, patch_boto3_and_import):
        """Tools that don't have projectId should fail with 400 (not 401)."""
        handler = patch_boto3_and_import['handler']
        
        event = create_api_gateway_event(
            body={'tool': 'get_project_context', 'params': {'branch': 'main'}},  # No projectId
            headers={'Content-Type': 'application/json'}  # No auth header
        )
        
        result = handler.handler(event, {})
        
        # Should fail with bad_request for missing projectId, not 401
        assert result['statusCode'] == 400
        body = json.loads(result['body'])
        assert body['error'] == 'bad_request'
        assert 'projectId is required' in body['message'] or 'Missing projectId' in body['message']

    def test_cross_project_access_blocked(self, patch_boto3_and_import):
        """Caller authenticated for project A cannot access project B's data."""
        handler = patch_boto3_and_import['handler']
        
        import handler as handler_module
        handler_module.authenticate.return_value = {
            'success': False,
            'error': {'error': 'invalid_token', 'message': 'Token verification failed'},
            'statusCode': 401
        }
        
        event = create_api_gateway_event(
            body={'tool': 'get_project_context', 'params': {'projectId': 'project-B', 'branch': 'main'}},
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer test-token-123'}
        )
        
        result = handler.handler(event, {})
        
        # Should fail because the token doesn't match project-B
        assert result['statusCode'] == 401
        body = json.loads(result['body'])
        assert body['error'] == 'invalid_token'


class TestMCPAuthorization:
    """Tests for MCP tool-level authorization (project isolation)."""

    def test_get_project_context_validates_project(self, patch_boto3_and_import):
        """get_project_context should validate project access."""
        handler = patch_boto3_and_import['handler']
        
        import handler as handler_module
        handler_module.authenticate.return_value = {
            'success': True,
            'project': {'projectId': 'test-project', 'apiTokenHash': 'salt:hash'}
        }
        
        tables = patch_boto3_and_import['tables']
        tables['context_table'].query.return_value = {
            'Items': [],
            'Count': 0
        }
        
        event = create_api_gateway_event(
            body={'tool': 'get_project_context', 'params': {'projectId': 'test-project', 'branch': 'main'}},
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer valid-token'}
        )
        
        result = handler.handler(event, {})
        
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert 'recentContext' in body

    def test_get_recent_changes_validates_project(self, patch_boto3_and_import):
        """get_recent_changes should validate project access."""
        handler = patch_boto3_and_import['handler']
        
        import handler as handler_module
        handler_module.authenticate.return_value = {
            'success': True,
            'project': {'projectId': 'test-project', 'apiTokenHash': 'salt:hash'}
        }
        
        tables = patch_boto3_and_import['tables']
        tables['context_table'].query.return_value = {
            'Items': [],
            'Count': 0
        }
        
        event = create_api_gateway_event(
            body={'tool': 'get_recent_changes', 'params': {'projectId': 'test-project', 'branch': 'main'}},
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer valid-token'}
        )
        
        result = handler.handler(event, {})
        
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert 'changes' in body

    def test_search_context_validates_project(self, patch_boto3_and_import):
        """search_context should validate project access."""
        handler = patch_boto3_and_import['handler']
        
        import handler as handler_module
        handler_module.authenticate.return_value = {
            'success': True,
            'project': {'projectId': 'test-project', 'apiTokenHash': 'salt:hash'}
        }
        
        tables = patch_boto3_and_import['tables']
        tables['context_table'].query.return_value = {
            'Items': [],
            'Count': 0
        }
        
        event = create_api_gateway_event(
            body={'tool': 'search_context', 'params': {'projectId': 'test-project', 'query': 'test query'}},
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer valid-token'}
        )
        
        result = handler.handler(event, {})
        
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert 'answer' in body

    def test_log_context_validates_project(self, patch_boto3_and_import):
        """log_context should validate project access."""
        handler = patch_boto3_and_import['handler']
        
        import handler as handler_module
        handler_module.authenticate.return_value = {
            'success': True,
            'project': {'projectId': 'test-project', 'apiTokenHash': 'salt:hash'}
        }
        
        tables = patch_boto3_and_import['tables']
        tables['context_table'].query.return_value = {
            'Items': [],
            'Count': 0
        }
        tables['context_table'].put_item.return_value = {}
        tables['audit_table'].put_item.return_value = {}
        
        event = create_api_gateway_event(
            body={'tool': 'log_context', 'params': {'projectId': 'test-project', 'branch': 'main', 'author': 'test-user', 'reasoning': 'Test reasoning'}},
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer valid-token'}
        )
        
        result = handler.handler(event, {})
        
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert body['success'] == True

    def test_graph_tools_validate_project(self, patch_boto3_and_import):
        """Graph tools should validate project access."""
        handler = patch_boto3_and_import['handler']
        
        import handler as handler_module
        handler_module.authenticate.return_value = {
            'success': True,
            'project': {'projectId': 'test-project', 'apiTokenHash': 'salt:hash'}
        }
        
        tables = patch_boto3_and_import['tables']
        tables['entities_table'].get_item.return_value = {'Item': None}
        tables['relationships_table'].query.return_value = {'Items': [], 'Count': 0}
        tables['entities_table'].query.return_value = {'Items': [], 'Count': 0}
        
        event = create_api_gateway_event(
            body={'tool': 'query_knowledge_graph', 'params': {'projectId': 'test-project', 'entityType': 'commit'}},
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer valid-token'}
        )
        
        result = handler.handler(event, {})
        
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert 'entities' in body


class TestMCPAuthorizationCrossProject:
    """Tests for cross-project access prevention."""

    def test_cannot_access_other_project_via_entity_id(self, patch_boto3_and_import):
        """Caller authenticated for project-A cannot access project-B's entities via entityId."""
        handler = patch_boto3_and_import['handler']
        
        import handler as handler_module
        handler_module.authenticate.return_value = {
            'success': True,
            'project': {'projectId': 'project-A', 'apiTokenHash': 'salt:hash'}
        }
        
        tables = patch_boto3_and_import['tables']
        # Entity belongs to project-B
        entity = {'entityId': 'commit:repo:test:sha', 'entityType': 'commit', 'projectId': 'project-B'}
        tables['entities_table'].get_item.return_value = {'Item': entity}
        
        event = create_api_gateway_event(
            body={'tool': 'query_knowledge_graph', 'params': {'projectId': 'project-A', 'entityId': 'commit:repo:test:sha'}},
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer valid-token'}
        )
        
        result = handler.handler(event, {})
        
        # Should return 403 because entity belongs to different project
        assert result['statusCode'] == 403
        body = json.loads(result['body'])
        assert body['error'] == 'forbidden'
        assert 'different project' in body['message']

    def test_cannot_access_other_project_via_get_related_changes(self, patch_boto3_and_import):
        """Caller authenticated for project-A cannot access project-B's entities via get_related_changes."""
        handler = patch_boto3_and_import['handler']
        
        import handler as handler_module
        handler_module.authenticate.return_value = {
            'success': True,
            'project': {'projectId': 'project-A', 'apiTokenHash': 'salt:hash'}
        }
        
        tables = patch_boto3_and_import['tables']
        # Entity belongs to project-B
        entity = {'entityId': 'commit:repo:test:sha', 'entityType': 'commit', 'projectId': 'project-B'}
        tables['entities_table'].get_item.return_value = {'Item': entity}
        
        event = create_api_gateway_event(
            body={'tool': 'get_related_changes', 'params': {'projectId': 'project-A', 'entityType': 'commit', 'entityId': 'commit:repo:test:sha'}},
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer valid-token'}
        )
        
        result = handler.handler(event, {})
        
        assert result['statusCode'] == 403
        body = json.loads(result['body'])
        assert body['error'] == 'forbidden'
        assert 'different project' in body['message']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])