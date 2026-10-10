"""
Pytest configuration for Graph-Aware RAG tests.
Provides common fixtures for test isolation.
"""

import json
import os
import sys
import pytest
from decimal import Decimal
from unittest.mock import Mock, MagicMock, patch

# Set AWS region
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')

# Add shared python to path
_shared_python_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared', 'python'))
sys.path.insert(0, _shared_python_path)

# Set environment variables
os.environ.setdefault('CONTEXT_TABLE', 'flowsync-context')
os.environ.setdefault('GRAPH_ENTITIES_TABLE', 'flowsync-graph-entities')
os.environ.setdefault('GRAPH_RELATIONSHIPS_TABLE', 'flowsync-graph-relationships')
os.environ.setdefault('FALLBACK_MODEL_ID', 'us.amazon.nova-lite-v1:0')


# ─── Pytest fixtures for test isolation ───

@pytest.fixture
def mock_dynamodb_resource():
    """Create a fresh mock DynamoDB resource for each test."""
    mock_dynamodb = MagicMock()
    mock_context_table = Mock()
    mock_graph_entities_table = Mock()
    mock_graph_relationships_table = Mock()

    def mock_table(name):
        if name == "flowsync-context":
            return mock_context_table
        elif name == "flowsync-graph-entities":
            return mock_graph_entities_table
        elif name == "flowsync-graph-relationships":
            return mock_graph_relationships_table
        return Mock()

    mock_dynamodb.Table.side_effect = mock_table
    return {
        'dynamodb': mock_dynamodb,
        'context_table': mock_context_table,
        'graph_entities_table': mock_graph_entities_table,
        'graph_relationships_table': mock_graph_relationships_table,
        'dynamodb_resource': mock_dynamodb,
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


@pytest.fixture
def mock_aws_services(mock_dynamodb_resource, mock_bedrock_client):
    """Create a complete set of mocked AWS services for each test."""
    # Extract the individual tables
    mock_context_table = mock_dynamodb_resource['context_table']
    mock_graph_entities_table = mock_dynamodb_resource['graph_entities_table']
    mock_graph_relationships_table = mock_dynamodb_resource['graph_relationships_table']
    mock_dynamodb = mock_dynamodb_resource['dynamodb_resource']

    # Reset mocks
    for table in [mock_context_table, mock_graph_entities_table, mock_graph_relationships_table]:
        table.put_item.reset_mock()
        table.get_item.reset_mock()
        table.query.reset_mock()
        table.update_item.reset_mock()
        table.delete_item.reset_mock()
        table.batch_get_item.reset_mock()

    # Configure default responses
    mock_context_table.put_item.return_value = {}
    mock_context_table.get_item.return_value = {'Item': None}
    mock_context_table.query.return_value = {'Items': [], 'Count': 0}
    mock_context_table.update_item.return_value = {'Attributes': {}}

    mock_graph_entities_table.put_item.return_value = {}
    mock_graph_entities_table.get_item.return_value = {'Item': None}
    mock_graph_entities_table.query.return_value = {'Items': [], 'Count': 0}
    mock_graph_entities_table.batch_get_item.return_value = {'Responses': {}}

    mock_graph_relationships_table.put_item.return_value = {}
    mock_graph_relationships_table.get_item.return_value = {'Item': None}
    mock_graph_relationships_table.query.return_value = {'Items': [], 'Count': 0}

    mock_dynamodb.batch_get_item.return_value = {'Responses': {}}

    return {
        'context_table': mock_context_table,
        'graph_entities_table': mock_graph_entities_table,
        'graph_relationships_table': mock_graph_relationships_table,
        'dynamodb': mock_dynamodb,
        'bedrock': mock_bedrock_client,
    }


@pytest.fixture
def mock_env():
    """Set up environment variables for tests."""
    original_env = dict(os.environ)
    os.environ.update({
        'CONTEXT_TABLE': 'flowsync-context',
        'GRAPH_ENTITIES_TABLE': 'flowsync-graph-entities',
        'GRAPH_RELATIONSHIPS_TABLE': 'flowsync-graph-relationships',
        'FALLBACK_MODEL_ID': 'us.amazon.nova-lite-v1:0',
    })
    yield
    os.environ.clear()
    os.environ.update(original_env)


# ─── Test data helpers ───

def create_test_context_record(entity_id='evt-1', feature='Test Feature', decision='Test decision', 
                               commit_hash='abc123', branch='main', project_id='test-project'):
    """Create a test context record."""
    return {
        'eventId': entity_id,
        'projectId': project_id,
        'branch': branch,
        'branchExtractedAt': f"{branch}#2024-01-01T00:00:00Z",
        'commitHash': commit_hash,
        'status': 'complete',
        'feature': feature,
        'decision': decision,
        'tasks': ['task1'],
        'stage': 'Feature Development',
        'risk': 'Test risk',
        'confidence': Decimal('0.85'),
        'entities': ['entity1', 'entity2'],
        'author': 'Test Author',
        'agentReasoning': None,
        'modelVersion': 'us.amazon.nova-pro-v1:0',
        'embedding': [0.1] * 1536,
        'extractedAt': '2024-01-01T00:00:00Z',
        'processingDuration': 1000,
    }


def create_test_graph_entity(entity_id, entity_type='commit', project_id='test-project', **kwargs):
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


def pytest_configure(config):
    """Configure pytest."""
    config.addinivalue_line("markers", "slow: marks tests as slow")