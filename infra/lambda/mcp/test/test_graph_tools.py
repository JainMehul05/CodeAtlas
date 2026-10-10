"""
Pytest tests for MCP Graph Tools - Phase 3
Tests the MCP server graph tools functionality.

Run with: python -m pytest test/test_graph_tools.py -v
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

# Set AWS region before importing
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')

# ─── Mock boto3 clients ───
mock_dynamodb_resource = MagicMock()
mock_entities_table = Mock()
mock_relationships_table = Mock()
mock_context_table = Mock()
mock_projects_table = Mock()
mock_audit_table = Mock()
mock_cache_table = Mock()

def mock_table(name):
    if name == "flowsync-graph-entities":
        return mock_entities_table
    elif name == "flowsync-graph-relationships":
        return mock_relationships_table
    elif name == "flowsync-context":
        return mock_context_table
    elif name == "flowsync-projects":
        return mock_projects_table
    elif name == "flowsync-audit":
        return mock_audit_table
    elif name == "flowsync-cache":
        return mock_cache_table
    return Mock()

mock_dynamodb_resource.Table.side_effect = mock_table

# Mock Bedrock client
mock_bedrock_client = MagicMock()
mock_bedrock_client.converse.return_value = {
    'output': {'message': {'content': [{'text': json.dumps({
        'answer': 'Test answer',
        'answerGrounded': True,
        'citedSources': ['abc123']
    })}]}},
    'usage': {'inputTokens': 100, 'outputTokens': 50}
}
mock_bedrock_client.invoke_model.return_value = {
    'body': Mock(read=Mock(return_value=json.dumps({'embedding': [0.1] * 1536}).encode()))
}

# Start patches BEFORE importing mcp handler
boto3_resource_patcher = patch('boto3.resource', return_value=mock_dynamodb_resource)
boto3_client_patcher = patch('boto3.client', return_value=mock_bedrock_client)

boto3_resource_patcher.start()
boto3_client_patcher.start()

# Add mcp handler path and shared python path to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared', 'python'))

# Now import mcp handler
import handler

# ─── Test fixtures ───

@pytest.fixture(autouse=True)
def mock_aws_services():
    """Mock AWS services for all tests."""
    # Reset mocks
    mock_entities_table.reset_mock()
    mock_relationships_table.reset_mock()
    mock_context_table.reset_mock()
    mock_projects_table.reset_mock()
    mock_audit_table.reset_mock()
    mock_cache_table.reset_mock()
    mock_dynamodb_resource.reset_mock()
    
    # Clear any lingering side_effects
    for table in [mock_entities_table, mock_relationships_table, mock_context_table, 
                  mock_projects_table, mock_audit_table, mock_cache_table]:
        table.put_item.side_effect = None
        table.get_item.side_effect = None
        table.query.side_effect = None
        table.update_item.side_effect = None
        table.delete_item.side_effect = None
        table.batch_get_item.side_effect = None
    
    # Configure default responses
    mock_entities_table.put_item.return_value = {}
    mock_entities_table.get_item.return_value = {'Item': None}
    mock_entities_table.query.return_value = {'Items': [], 'Count': 0}
    mock_entities_table.delete_item.return_value = {}
    mock_entities_table.batch_get_item.return_value = {'Responses': {}}
    
    mock_relationships_table.put_item.return_value = {}
    mock_relationships_table.get_item.return_value = {'Item': None}
    mock_relationships_table.query.return_value = {'Items': [], 'Count': 0}
    mock_relationships_table.delete_item.return_value = {}
    mock_relationships_table.batch_get_item.return_value = {'Responses': {}}
    
    mock_context_table.put_item.return_value = {}
    mock_context_table.get_item.return_value = {'Item': None}
    mock_context_table.query.return_value = {'Items': []}
    mock_context_table.update_item.return_value = {'Attributes': {}}
    
    mock_projects_table.put_item.return_value = {}
    mock_projects_table.get_item.return_value = {'Item': None}
    mock_projects_table.query.return_value = {'Items': []}
    mock_projects_table.update_item.return_value = {'Attributes': {}}
    
    mock_audit_table.put_item.return_value = {}
    mock_audit_table.get_item.return_value = {'Item': None}
    mock_audit_table.query.return_value = {'Items': []}
    
    mock_cache_table.put_item.return_value = {}
    mock_cache_table.get_item.return_value = {'Item': None}
    
    mock_dynamodb_resource.batch_get_item.return_value = {'Responses': {}}
    
    yield {
        'entities_table': mock_entities_table,
        'relationships_table': mock_relationships_table,
        'context_table': mock_context_table,
        'projects_table': mock_projects_table,
        'audit_table': mock_audit_table,
        'cache_table': mock_cache_table,
        'dynamodb': mock_dynamodb_resource,
    }


@pytest.fixture
def mock_env():
    """Set up environment variables for tests."""
    original_env = dict(os.environ)
    os.environ.update({
        'CONTEXT_TABLE': 'flowsync-context',
        'PROJECTS_TABLE': 'flowsync-projects',
        'AUDIT_TABLE': 'flowsync-audit',
        'CACHE_TABLE': 'flowsync-cache',
        'GRAPH_ENTITIES_TABLE': 'flowsync-graph-entities',
        'GRAPH_RELATIONSHIPS_TABLE': 'flowsync-graph-relationships',
        'FALLBACK_MODEL_ID': 'us.amazon.nova-lite-v1:0',
    })
    yield
    os.environ.clear()
    os.environ.update(original_env)


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


class TestMCPGraphTools:
    """Tests for MCP graph tools."""

    def test_query_knowledge_graph_with_entity_id(self, mock_aws_services, mock_env):
        """Test querying knowledge graph with specific entity ID."""
        from handler import query_knowledge_graph
        
        seed_entity = create_test_entity('commit:repo:test:sha', 'commit')
        file_entity = create_test_entity('file:repo:test:file.py', 'file')
        
        # Use a counter to track get_item calls
        get_item_calls = 0
        def get_item_side_effect(Key):
            nonlocal get_item_calls
            if get_item_calls == 0:
                get_item_calls += 1
                return {'Item': seed_entity}
            else:
                get_item_calls += 1
                return {'Item': file_entity}
        mock_aws_services['entities_table'].get_item.side_effect = get_item_side_effect
        
        rels = [create_test_relationship(rel_type='modifies')]
        query_calls = 0
        def query_side_effect(**kwargs):
            nonlocal query_calls
            if query_calls == 0:
                query_calls += 1
                return {'Items': rels, 'Count': 1}  # outgoing
            else:
                query_calls += 1
                return {'Items': [], 'Count': 0}  # incoming
        mock_aws_services['relationships_table'].query.side_effect = query_side_effect
        
        result = query_knowledge_graph({
            'projectId': 'test-project',
            'entityId': 'commit:repo:test:sha',
            'direction': 'both',
            'maxDepth': 1,
            'maxResults': 10,
        })
        
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert body['count'] >= 1
        assert len(body['entities']) >= 1
        assert len(body['relationships']) >= 1

    def test_query_knowledge_graph_with_entity_type(self, mock_aws_services, mock_env):
        """Test querying knowledge graph by entity type."""
        from handler import query_knowledge_graph
        
        entities = [create_test_entity(f'commit:repo:test:sha{i}', 'commit') for i in range(3)]
        mock_aws_services['entities_table'].query.return_value = {'Items': entities}
        
        result = query_knowledge_graph({
            'projectId': 'test-project',
            'entityType': 'commit',
            'maxResults': 10,
        })
        
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert body['count'] == 3
        assert len(body['entities']) == 3

    def test_query_knowledge_graph_missing_params(self, mock_aws_services, mock_env):
        """Test querying knowledge graph with missing required params."""
        from handler import query_knowledge_graph
        
        result = query_knowledge_graph({
            'projectId': 'test-project',
        })
        
        assert result['statusCode'] == 400
        body = json.loads(result['body'])
        assert 'Either entityId or entityType must be provided' in body['message']

    def test_query_knowledge_graph_project_isolation(self, mock_aws_services, mock_env):
        """Test that query respects project isolation."""
        from handler import query_knowledge_graph
        
        entity = create_test_entity('commit:repo:test:sha', 'commit', project_id='other-project')
        mock_aws_services['entities_table'].get_item.return_value = {'Item': entity}
        
        result = query_knowledge_graph({
            'projectId': 'test-project',
            'entityId': 'commit:repo:test:sha',
        })
        
        assert result['statusCode'] == 403
        body = json.loads(result['body'])
        assert 'different project' in body['message']

    def test_get_related_changes(self, mock_aws_services, mock_env):
        """Test getting related changes for an entity."""
        from handler import get_related_changes
        
        entity = create_test_entity('commit:repo:test:sha', 'commit')
        mock_aws_services['entities_table'].get_item.return_value = {'Item': entity}
        
        rels = [create_test_relationship(rel_type='modifies')]
        mock_aws_services['relationships_table'].query.side_effect = [
            {'Items': rels, 'Count': 1},  # outgoing
            {'Items': [], 'Count': 0},    # incoming
        ]
        mock_aws_services['dynamodb'].batch_get_item.return_value = {
            'Responses': {
                'flowsync-graph-entities': [create_test_entity('file:repo:test:file.py', 'file')]
            }
        }
        
        result = get_related_changes({
            'projectId': 'test-project',
            'entityType': 'commit',
            'entityId': 'commit:repo:test:sha',
            'includeGraph': True,
        })
        
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert 'relatedCommits' in body
        assert 'relatedPRs' in body
        assert 'relatedFiles' in body
        assert 'relatedDecisions' in body
        assert 'relationships' in body
        assert 'counts' in body

    def test_get_related_changes_not_found(self, mock_aws_services, mock_env):
        """Test getting related changes for non-existent entity."""
        from handler import get_related_changes
        
        mock_aws_services['entities_table'].get_item.return_value = {'Item': None}
        
        result = get_related_changes({
            'projectId': 'test-project',
            'entityType': 'commit',
            'entityId': 'nonexistent',
        })
        
        assert result['statusCode'] == 404
        body = json.loads(result['body'])
        assert 'not found' in body['message']

    def test_get_engineering_decisions_by_project(self, mock_aws_services, mock_env):
        """Test getting engineering decisions for a project."""
        from handler import get_engineering_decisions
        
        decisions = [create_test_entity(f'decision:test-project:ctx:evt{i}', 'engineering_decision') for i in range(2)]
        mock_aws_services['entities_table'].query.return_value = {'Items': decisions}
        
        result = get_engineering_decisions({
            'projectId': 'test-project',
            'limit': 10,
        })
        
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert body['count'] == 2
        assert len(body['decisions']) == 2

    def test_get_engineering_decisions_by_entity(self, mock_aws_services, mock_env):
        """Test getting engineering decisions related to an entity."""
        from handler import get_engineering_decisions
        
        rels = [create_test_relationship(rel_type='relates_to', source_id='decision:test-project:ctx:evt1', target_id='commit:repo:test:sha')]
        mock_aws_services['relationships_table'].query.return_value = {'Items': rels}
        
        decision = create_test_entity('decision:test-project:ctx:evt1', 'engineering_decision')
        mock_aws_services['dynamodb'].batch_get_item.return_value = {
            'Responses': {
                'flowsync-graph-entities': [decision]
            }
        }
        
        result = get_engineering_decisions({
            'projectId': 'test-project',
            'entityId': 'commit:repo:test:sha',
        })
        
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert body['count'] == 1

    def test_get_repository_graph_summary(self, mock_aws_services, mock_env):
        """Test getting repository graph summary."""
        from handler import get_repository_graph_summary
        
        # Entities table: 5 entity type queries (all return Count), then 1 recent activity query
        entity_query_counts = [5, 0, 0, 0, 0]  # repository, commit, pull_request, file, engineering_decision
        entity_call_count = 0
        def entity_query_side_effect(**kwargs):
            nonlocal entity_call_count
            if kwargs.get('Select') == 'COUNT':
                count = entity_query_counts[entity_call_count] if entity_call_count < len(entity_query_counts) else 0
                entity_call_count += 1
                return {'Count': count}
            else:
                # Recent activity query
                return {'Items': [create_test_entity('commit:repo:test:sha', 'commit')]}
        
        # Relationships table: 9 relationship type queries (sum = 12)
        rel_query_counts = [3, 2, 1, 0, 1, 1, 1, 1, 2]  # contains, authored_in, modifies, has_parent, includes_commit, targets_file, has_decision, relates_to, derived_from
        rel_call_count = 0
        def rel_query_side_effect(**kwargs):
            nonlocal rel_call_count
            count = rel_query_counts[rel_call_count] if rel_call_count < len(rel_query_counts) else 0
            rel_call_count += 1
            return {'Count': count}
        
        mock_aws_services['entities_table'].query.side_effect = entity_query_side_effect
        mock_aws_services['relationships_table'].query.side_effect = rel_query_side_effect
        
        result = get_repository_graph_summary({
            'projectId': 'test-project',
            'repositoryId': 'repo:github:owner:repo',
        })
        
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert 'summary' in body
        assert 'entityCounts' in body
        assert 'relationshipCounts' in body
        assert 'recentActivity' in body
        assert body['summary']['totalEntities'] == 5
        assert body['summary']['totalRelationships'] == 12

    def test_find_related_context_with_graph(self, mock_aws_services, mock_env):
        """Test finding related context with graph traversal."""
        from handler import find_related_context
        
        entity = create_test_entity('commit:repo:test:sha', 'commit')
        file_entity = create_test_entity('file:repo:test:file.py', 'file')
        # get_item called 3 times: 1 for initial verification, 1 for traverse(seed), 1 for traverse(target)
        mock_aws_services['entities_table'].get_item.side_effect = [
            {'Item': entity},  # initial verification
            {'Item': entity},  # traverse(seed)
            {'Item': file_entity},  # traverse(target)
        ]
        mock_aws_services['relationships_table'].query.side_effect = [
            {'Items': [create_test_relationship()], 'Count': 1},  # outgoing
            {'Items': [], 'Count': 0},  # incoming
        ]
        
        result = find_related_context({
            'projectId': 'test-project',
            'entityType': 'commit',
            'entityId': 'commit:repo:test:sha',
            'maxDepth': 1,
            'maxResults': 10,
        })
        
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert 'graphContext' in body
        assert 'relationships' in body
        assert 'paths' in body
        assert 'counts' in body

    def test_find_related_context_with_semantic_search(self, mock_aws_services, mock_env):
        """Test finding related context with semantic search."""
        from handler import find_related_context
        
        entity = create_test_entity('commit:repo:test:sha', 'commit')
        # get_item called 2 times: 1 for initial verification, 1 for traverse(seed)
        mock_aws_services['entities_table'].get_item.side_effect = [
            {'Item': entity},  # initial verification
            {'Item': entity},  # traverse(seed)
        ]
        mock_aws_services['relationships_table'].query.side_effect = [
            {'Items': [], 'Count': 0},  # outgoing
            {'Items': [], 'Count': 0},  # incoming
        ]
        
        # Mock the RAG call
        with patch('handler.search_context_rag_with_graph', return_value={
            'answer': 'Test answer',
            'answerGrounded': True,
            'sources': []
        }):
            result = find_related_context({
                'projectId': 'test-project',
                'entityType': 'commit',
                'entityId': 'commit:repo:test:sha',
                'query': 'Why was this implemented?',
                'maxDepth': 1,
                'maxResults': 10,
            })
        
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert 'graphContext' in body
        assert 'semanticAnswer' in body
        assert 'combinedSources' in body


if __name__ == '__main__':
    pytest.main([__file__, '-v'])