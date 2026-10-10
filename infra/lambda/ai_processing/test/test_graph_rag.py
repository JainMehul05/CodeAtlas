"""
Pytest tests for Graph-Aware RAG - Phase 3
Tests the graph-aware RAG functionality in the shared helpers.

Run with: python -m pytest test/test_graph_rag.py -v
"""

import json
import os
import sys
import pytest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock, patch, MagicMock
from botocore.exceptions import ClientError

# Set AWS region before importing helpers
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


@pytest.fixture(autouse=True)
def patch_boto3(mock_dynamodb_resource, mock_bedrock_client):
    """Patch boto3 globally for the test."""
    with patch('boto3.resource', return_value=mock_dynamodb_resource['dynamodb_resource']), \
         patch('boto3.client', return_value=mock_bedrock_client):
        # Import helpers with patches active
        from flowsync_common import helpers
        yield {'helpers': helpers}


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


# ─── Helper functions for creating test data ───

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


class TestGraphRAGHelpers:
    """Tests for graph-aware RAG helper functions."""

    def test_fetch_graph_entity(self, mock_aws_services, patch_boto3):
        """Test fetching a single graph entity."""
        from flowsync_common import helpers
        
        entity = create_test_graph_entity('commit:repo:test:sha')
        mock_aws_services['graph_entities_table'].get_item.return_value = {'Item': entity}
        
        result = helpers.fetch_graph_entity(mock_aws_services['dynamodb'], 'commit:repo:test:sha')
        
        assert result == entity
        mock_aws_services['graph_entities_table'].get_item.assert_called_once_with(Key={'entityId': 'commit:repo:test:sha'})

    def test_fetch_graph_entity_not_found(self, mock_aws_services, patch_boto3):
        """Test fetching a non-existent graph entity."""
        mock_aws_services['graph_entities_table'].get_item.return_value = {}
        
        from flowsync_common import helpers
        result = helpers.fetch_graph_entity(mock_aws_services['dynamodb'], 'nonexistent')
        
        assert result is None

    def test_fetch_graph_entities_batch(self, mock_aws_services, patch_boto3):
        """Test batch fetching multiple graph entities."""
        entities = [
            create_test_graph_entity('commit:repo:test:sha1'),
            create_test_graph_entity('commit:repo:test:sha2'),
        ]
        mock_aws_services['dynamodb'].batch_get_item.return_value = {
            'Responses': {
                'flowsync-graph-entities': entities
            }
        }
        
        from flowsync_common import helpers
        result = helpers.fetch_graph_entities_batch(mock_aws_services['dynamodb'], ['commit:repo:test:sha1', 'commit:repo:test:sha2'])
        
        assert len(result) == 2
        assert result[0]['entityId'] == 'commit:repo:test:sha1'
        assert result[1]['entityId'] == 'commit:repo:test:sha2'

    def test_fetch_outgoing_relationships(self, mock_aws_services, patch_boto3):
        """Test fetching outgoing relationships from an entity."""
        rels = [create_test_relationship(rel_type='modifies')]
        mock_aws_services['graph_relationships_table'].query.return_value = {'Items': rels}
        
        from flowsync_common import helpers
        result = helpers.fetch_outgoing_relationships(mock_aws_services['dynamodb'], 'commit:repo:test:sha')
        
        assert len(result) == 1
        assert result[0]['relationshipType'] == 'modifies'

    def test_fetch_incoming_relationships(self, mock_aws_services, patch_boto3):
        """Test fetching incoming relationships to an entity."""
        rels = [create_test_relationship(rel_type='authored_in')]
        mock_aws_services['graph_relationships_table'].query.return_value = {'Items': rels}
        
        from flowsync_common import helpers
        result = helpers.fetch_incoming_relationships(mock_aws_services['dynamodb'], 'commit:repo:test:sha')
        
        assert len(result) == 1
        assert result[0]['relationshipType'] == 'authored_in'

    def test_expand_context_with_graph(self, mock_aws_services, patch_boto3):
        """Test expanding context records with graph entities."""
        # Create a context record with commit hash
        context_record = create_test_context_record()
        top_records = [(context_record, 0.9)]
        
        # Setup mocks for graph traversal
        commit_entity = create_test_graph_entity('commit:repo:project:test-project:abc123', 'commit')
        file_entity = create_test_graph_entity('file:repo:project:test-project:file.py', 'file')
        rel = create_test_relationship('modifies', 'commit:repo:project:test-project:abc123', 'file:repo:project:test-project:file.py')
        
        mock_aws_services['graph_entities_table'].get_item.side_effect = [
            {'Item': create_test_graph_entity('commit:repo:project:test-project:abc123', 'commit')},  # fetch_graph_entity for commit
            {'Item': create_test_graph_entity('file:repo:project:test-project:file.py', 'file')},    # fetch_graph_entity for file
        ]
        mock_aws_services['graph_relationships_table'].query.side_effect = [
            {'Items': [create_test_relationship('modifies', 'commit:repo:project:test-project:abc123', 'file:repo:project:test-project:file.py')], 'Count': 1},  # outgoing from commit
            {'Items': [], 'Count': 0},     # incoming to commit
        ]
        
        from flowsync_common import helpers
        result_top, graph_context, graph_relationships = helpers.expand_context_with_graph(
            mock_aws_services['dynamodb'],
            'test-project',
            top_records,
            max_depth=1,
            max_entities=10,
            max_relationships=10
        )
        
        # Should return original records plus graph context
        assert len(result_top) == 1
        assert len(graph_context) >= 1
        assert len(graph_relationships) >= 1

    def test_search_context_rag_with_graph_no_sources(self, mock_aws_services, patch_boto3):
        """Test RAG with graph when no semantic sources found."""
        mock_aws_services['context_table'].query.return_value = {'Items': []}
        
        from flowsync_common import helpers
        result = helpers.search_context_rag_with_graph(
            project_id='test-project',
            query='test query',
            branch='main',
            bedrock_client=mock_aws_services['bedrock'],
            dynamodb=mock_aws_services['dynamodb'],
            context_table_name='flowsync-context',
            cache_table_name=None,
            use_graph=True
        )
        
        assert result['answer'] == 'No context records found for this project.'
        assert result['answerGrounded'] == False
        assert result['sources'] == []

    def test_search_context_rag_with_graph_no_use_graph(self, mock_aws_services, patch_boto3):
        """Test RAG with graph disabled."""
        context_record = create_test_context_record()
        mock_aws_services['context_table'].query.return_value = {'Items': [context_record]}
        
        from flowsync_common import helpers
        with patch('flowsync_common.helpers.call_titan_embedding', return_value=[0.1] * 1536), \
             patch.object(mock_aws_services['bedrock'], 'converse', return_value={
                 'output': {'message': {'content': [{'text': json.dumps({
                     'answer': 'Test',
                     'answerGrounded': True,
                     'citedSources': []
                 })}]}},
                 'usage': {'inputTokens': 100, 'outputTokens': 50}
             }):
            
            result = helpers.search_context_rag_with_graph(
                project_id='test-project',
                query='test query',
                branch='main',
                bedrock_client=mock_aws_services['bedrock'],
                dynamodb=mock_aws_services['dynamodb'],
                context_table_name='flowsync-context',
                cache_table_name=None,
                use_graph=False
            )
        
        assert 'answer' in result


if __name__ == '__main__':
    pytest.main([__file__, '-v'])