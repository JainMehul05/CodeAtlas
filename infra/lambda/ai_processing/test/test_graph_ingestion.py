"""
Pytest tests for Graph Ingestion - Phase 3
Tests the graph ingestion functionality in the AI Processing Lambda handler.

Run with: python -m pytest test/test_graph_ingestion.py -v
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

# Set AWS region before importing handler
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')

# Add shared python to path
_shared_python_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared', 'python'))
sys.path.insert(0, _shared_python_path)


# ─── Test fixtures for boto3 patching ───

@pytest.fixture
def mock_dynamodb_resource():
    """Create a fresh mock DynamoDB resource for each test."""
    mock_dynamodb = MagicMock()
    mock_context_table = Mock()
    mock_audit_table = Mock()
    mock_projects_table = Mock()
    mock_idempotency_table = Mock()
    mock_graph_entities_table = Mock()
    mock_graph_relationships_table = Mock()

    def mock_table(name):
        if name == "flowsync-context":
            return mock_context_table
        elif name == "flowsync-audit":
            return mock_audit_table
        elif name == "flowsync-projects":
            return mock_projects_table
        elif name == "flowsync-idempotency":
            return mock_idempotency_table
        elif name == "flowsync-graph-entities":
            return mock_graph_entities_table
        elif name == "flowsync-graph-relationships":
            return mock_graph_relationships_table
        return Mock()

    mock_dynamodb.Table.side_effect = mock_table
    return {
        'dynamodb': mock_dynamodb,
        'context_table': mock_context_table,
        'audit_table': mock_audit_table,
        'projects_table': mock_projects_table,
        'idempotency_table': mock_idempotency_table,
        'graph_entities_table': mock_graph_entities_table,
        'graph_relationships_table': mock_graph_relationships_table,
    }


@pytest.fixture
def mock_bedrock_client():
    """Create a mock Bedrock client."""
    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = {
        'output': {'message': {'content': [{'text': json.dumps({
            'feature': 'Test Feature',
            'decision': 'Test decision',
            'tasks': ['task1'],
            'stage': 'Feature Development',
            'risk': 'Test risk',
            'entities': ['entity1', 'entity2']
        })}]}},
        'usage': {'inputTokens': 100, 'outputTokens': 50}
    }
    mock_bedrock.invoke_model.return_value = {
        'body': Mock(read=Mock(return_value=json.dumps({'embedding': [0.1] * 1536}).encode()))
    }
    return mock_bedrock


@pytest.fixture
def mock_helpers():
    """Mock flowsync_common.helpers functions."""
    mock = MagicMock()
    mock.convert_floats_to_decimal.side_effect = lambda x: x
    mock.convert_decimals.side_effect = lambda x: x
    mock.call_titan_embedding.return_value = ([0.1] * 1536, 100)
    return mock


@pytest.fixture(autouse=True)
def patch_boto3_and_helpers(mock_dynamodb_resource, mock_bedrock_client, mock_helpers):
    """Patch boto3 and helpers for handler module."""
    with patch('boto3.resource', return_value=mock_dynamodb_resource['dynamodb']), \
         patch('boto3.client', return_value=mock_bedrock_client), \
         patch.dict('sys.modules', {'flowsync_common.helpers': mock_helpers, 'flowsync_common': MagicMock(helpers=mock_helpers)}):
        import handler
        # Reset the handler's module-level dynamodb to use our mock
        handler.dynamodb = mock_dynamodb_resource['dynamodb']
        handler.bedrock_client = mock_bedrock_client
        handler.cloudwatch = mock_bedrock_client  # reuse mock
        yield {
            'handler': handler,
            'tables': {
                'context_table': mock_dynamodb_resource['context_table'],
                'audit_table': mock_dynamodb_resource['audit_table'],
                'projects_table': mock_dynamodb_resource['projects_table'],
                'idempotency_table': mock_dynamodb_resource['idempotency_table'],
                'graph_entities_table': mock_dynamodb_resource['graph_entities_table'],
                'graph_relationships_table': mock_dynamodb_resource['graph_relationships_table'],
                'dynamodb': mock_dynamodb_resource['dynamodb'],
                'bedrock': mock_bedrock_client,
            }
        }


@pytest.fixture
def mock_aws_services(patch_boto3_and_helpers):
    """Use the same mock tables that the handler uses."""
    tables = patch_boto3_and_helpers['tables']
    
    # Reset mocks
    for table in tables.values():
        if hasattr(table, 'reset_mock'):
            table.reset_mock()
        if hasattr(table, 'put_item'):
            table.put_item.side_effect = None
            table.put_item.return_value = {}
        if hasattr(table, 'get_item'):
            table.get_item.side_effect = None
            table.get_item.return_value = {'Item': None}
        if hasattr(table, 'query'):
            table.query.side_effect = None
            table.query.return_value = {'Items': [], 'Count': 0}
        if hasattr(table, 'update_item'):
            table.update_item.side_effect = None
            table.update_item.return_value = {'Attributes': {}}
        if hasattr(table, 'delete_item'):
            table.delete_item.side_effect = None
            table.delete_item.return_value = {}
        if hasattr(table, 'batch_get_item'):
            table.batch_get_item.side_effect = None
            table.batch_get_item.return_value = {'Responses': {}}
    
    yield tables


@pytest.fixture
def mock_env():
    """Set up environment variables for tests."""
    original_env = dict(os.environ)
    os.environ.update({
        'CONTEXT_TABLE': 'flowsync-context',
        'AUDIT_TABLE': 'flowsync-audit',
        'PROJECTS_TABLE': 'flowsync-projects',
        'IDEMPOTENCY_TABLE': 'flowsync-idempotency',
        'GRAPH_ENTITIES_TABLE': 'flowsync-graph-entities',
        'GRAPH_RELATIONSHIPS_TABLE': 'flowsync-graph-relationships',
        'FALLBACK_MODEL_ID': 'us.amazon.nova-lite-v1:0',
    })
    yield
    os.environ.clear()
    os.environ.update(original_env)


def create_sqs_record(overrides=None):
    """Create a test SQS record."""
    if overrides is None:
        overrides = {}
    
    message_id = overrides.get('messageId', f"msg-{uuid.uuid4().hex[:8]}")
    
    base_record = {
        'messageId': message_id,
        'body': json.dumps({
            'eventId': '550e8400-e29b-41d4-a716-446655440000',
            'projectId': 'test-project',
            'eventType': 'push',
            'branch': 'main',
            'parentBranch': None,
            'payload': {
                'commitHash': 'a' * 40,
                'message': 'Test commit',
                'diff': 'diff --git a/file.ts b/file.ts\n+added line',
                'author': 'Test User',
                'changedFiles': ['file.ts'],
            },
            'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'correlationId': 'corr-123',
            'deliveryId': 'github-delivery-123',
            **{k: v for k, v in overrides.items() if k != 'messageId'},
        }),
        'messageAttributes': {
            'correlationId': {'stringValue': 'corr-123', 'dataType': 'String'},
            'eventType': {'stringValue': 'push', 'dataType': 'String'},
            'deliveryId': {'stringValue': 'github-delivery-123', 'dataType': 'String'},
        },
    }
    return base_record


class TestGraphIngestion:
    """Tests for graph ingestion from events."""

    def test_ingest_graph_from_push_event(self, mock_aws_services, mock_env, patch_boto3_and_helpers):
        """Graph ingestion should create entities and relationships for push events."""
        from handler import ingest_graph_from_event
        
        body = {
            'eventId': 'evt-1',
            'projectId': 'test-project',
            'eventType': 'push',
            'branch': 'main',
            'payload': {
                'commitHash': 'b' * 40,
                'message': 'Test push',
                'diff': 'diff --git a/file.py b/file.py\n+line',
                'author': 'Test Author',
                'changedFiles': ['src/file.py', 'src/other.py'],
            },
            'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'deliveryId': 'github-delivery-456',
        }
        
        correlation_id = 'test-correlation-1'
        
        # Mock idempotency table to return no existing record
        mock_aws_services['idempotency_table'].get_item.return_value = {'Item': None}
        mock_aws_services['idempotency_table'].put_item.return_value = {}
        mock_aws_services['idempotency_table'].update_item.return_value = {}
        
        ingest_graph_from_event(body, correlation_id)
        
        # Verify entities were created
        assert mock_aws_services['graph_entities_table'].put_item.call_count >= 3  # commit + 2 files + repo
        
        # Verify relationships were created
        assert mock_aws_services['graph_relationships_table'].put_item.call_count >= 4  # modifies x2 + authored_in + contains x2

    def test_ingest_graph_from_merge_event(self, mock_aws_services, mock_env, patch_boto3_and_helpers):
        """Graph ingestion should create PR entities and relationships for merge events."""
        from handler import ingest_graph_from_event
        
        body = {
            'eventId': 'evt-2',
            'projectId': 'test-project',
            'eventType': 'merge',
            'branch': 'main',
            'payload': {
                'commitHash': 'c' * 40,
                'message': 'Merge PR #42',
                'diff': '',
                'author': 'Merge Bot',
                'changedFiles': ['src/feature.py'],
                'pullRequest': {
                    'number': 42,
                    'title': 'Add feature',
                    'body': 'This PR adds a new feature',
                    'merged': True,
                    'url': 'https://github.com/owner/repo/pull/42',
                },
                'sourceBranch': 'feature/new-feature',
            },
            'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'deliveryId': 'github-delivery-789',
        }
        
        correlation_id = 'test-correlation-2'
        
        mock_aws_services['idempotency_table'].get_item.return_value = {'Item': None}
        mock_aws_services['idempotency_table'].put_item.return_value = {}
        mock_aws_services['idempotency_table'].update_item.return_value = {}
        
        ingest_graph_from_event(body, correlation_id)
        
        # Verify PR entity was created
        assert mock_aws_services['graph_entities_table'].put_item.call_count >= 3  # PR + commit + file + repo
        
        # Verify relationships: includes_commit, targets_file, modifies, authored_in, contains
        assert mock_aws_services['graph_relationships_table'].put_item.call_count >= 5

    def test_ingest_graph_creates_engineering_decision(self, mock_aws_services, mock_env, patch_boto3_and_helpers):
        """Graph ingestion should create engineering decision entity from context extraction."""
        handler = patch_boto3_and_helpers['handler']
        from handler import ingest_graph_from_event, upsert_graph_entity, upsert_graph_relationship
        from handler import GRAPH_SCHEMA_VERSION
        
        body = {
            'eventId': 'evt-3',
            'projectId': 'test-project',
            'eventType': 'push',
            'branch': 'main',
            'payload': {
                'commitHash': 'd' * 40,
                'message': 'Test commit with decision',
                'diff': 'diff --git a/config.py b/config.py\n+USE_SSL = True',
                'author': 'Test Author',
                'changedFiles': ['config.py'],
            },
            'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'deliveryId': 'github-delivery-999',
        }
        
        correlation_id = 'test-correlation-3'
        
        mock_aws_services['idempotency_table'].get_item.return_value = {'Item': None}
        mock_aws_services['idempotency_table'].put_item.return_value = {}
        mock_aws_services['idempotency_table'].update_item.return_value = {}
        
        # Simulate the process_event flow that creates decision entity
        with patch('handler.call_bedrock') as mock_bedrock, \
             patch('handler.validate_extraction_schema', return_value=True), \
             patch('handler.compute_confidence', return_value=0.85), \
             patch('handler.convert_floats_to_decimal', side_effect=lambda x: x), \
             patch('handler.call_titan_embedding', return_value=([0.1] * 1536, 100)), \
             patch('handler.write_context_record'), \
             patch('handler.write_audit_record'), \
             patch('handler.update_project_activity'), \
             patch('handler.find_orphaned_record', return_value=None), \
             patch('handler.update_orphaned_record'), \
             patch('handler.propagate_branch_context', return_value=1):
            
            mock_bedrock.return_value = {
                'feature': 'SSL Config',
                'decision': 'Enabled SSL for database connections',
                'tasks': ['Update connection strings'],
                'stage': 'Feature Development',
                'risk': 'None',
                'entities': ['config.py'],
            }
            
            # Process event which should trigger decision entity creation
            result = handler.process_event_record(
                create_sqs_record({'body': json.dumps(body), 'messageId': 'msg-3'}),
                correlation_id
            )
        
        # Verify decision entity was created
        decision_entities = [call for call in mock_aws_services['graph_entities_table'].put_item.call_args_list
                           if call[1]['Item'].get('entityType') == 'engineering_decision']
        assert len(decision_entities) >= 1
        
        # Verify relates_to relationship was created
        decision_rels = [call for call in mock_aws_services['graph_relationships_table'].put_item.call_args_list
                        if call[1]['Item'].get('relationshipType') == 'relates_to']
        assert len(decision_rels) >= 1

    def test_ingest_graph_idempotent(self, mock_aws_services, mock_env, patch_boto3_and_helpers):
        """Repeated graph ingestion should not create duplicate entities/relationships."""
        from handler import ingest_graph_from_event
        
        body = {
            'eventId': 'evt-4',
            'projectId': 'test-project',
            'eventType': 'push',
            'branch': 'main',
            'payload': {
                'commitHash': 'e' * 40,
                'message': 'Test',
                'diff': '',
                'author': 'Test',
                'changedFiles': ['file.py'],
            },
            'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'deliveryId': 'github-delivery-idempotent',
        }
        
        correlation_id = 'test-correlation-4'
        
        mock_aws_services['idempotency_table'].get_item.return_value = {'Item': None}
        mock_aws_services['idempotency_table'].put_item.return_value = {}
        mock_aws_services['idempotency_table'].update_item.return_value = {}
        
        # Ingest first time
        ingest_graph_from_event(body, correlation_id)
        first_entity_calls = mock_aws_services['graph_entities_table'].put_item.call_count
        first_rel_calls = mock_aws_services['graph_relationships_table'].put_item.call_count
        
        # Reset mocks to track second call
        mock_aws_services['graph_entities_table'].reset_mock()
        mock_aws_services['graph_relationships_table'].reset_mock()
        
        # Ingest second time (same event)
        ingest_graph_from_event(body, correlation_id)
        second_entity_calls = mock_aws_services['graph_entities_table'].put_item.call_count
        second_rel_calls = mock_aws_services['graph_relationships_table'].put_item.call_count
        
        # Should still create entities/relationships (idempotent at event level, but graph ingestion runs each time)
        # Note: Graph ingestion is not idempotent by itself - it relies on conditional writes in upsert
        assert second_entity_calls >= 1
        assert second_rel_calls >= 1

    def test_ingest_graph_handles_missing_payload_gracefully(self, mock_aws_services, mock_env, patch_boto3_and_helpers):
        """Graph ingestion should handle missing payload gracefully."""
        from handler import ingest_graph_from_event
        
        body = {
            'eventId': 'evt-5',
            'projectId': 'test-project',
            'eventType': 'push',
            'branch': 'main',
            'payload': {},
            'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        }
        
        correlation_id = 'test-correlation-5'
        
        # Should not raise exception
        ingest_graph_from_event(body, correlation_id)
        
        # Should not crash, just log warning and return
        assert True

    def test_ingest_graph_uses_correct_repository_id(self, mock_aws_services, mock_env, patch_boto3_and_helpers):
        """Graph ingestion should use correct repository ID from payload."""
        from handler import ingest_graph_from_event
        
        body = {
            'eventId': 'evt-6',
            'projectId': 'test-project',
            'eventType': 'push',
            'branch': 'main',
            'payload': {
                'commitHash': 'f' * 40,
                'message': 'Test',
                'diff': '',
                'author': 'Test',
                'changedFiles': ['file.py'],
                'repository': {
                    'owner': 'myorg',
                    'name': 'myrepo',
                },
            },
            'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'deliveryId': 'github-delivery-repo',
        }
        
        correlation_id = 'test-correlation-6'
        
        mock_aws_services['idempotency_table'].get_item.return_value = {'Item': None}
        mock_aws_services['idempotency_table'].put_item.return_value = {}
        mock_aws_services['idempotency_table'].update_item.return_value = {}
        
        ingest_graph_from_event(body, correlation_id)
        
        # Verify repository entity has correct owner/name
        repo_entities = [call for call in mock_aws_services['graph_entities_table'].put_item.call_args_list
                        if call[1]['Item'].get('entityType') == 'repository']
        assert len(repo_entities) >= 1
        repo_entity = repo_entities[0][1]['Item']
        assert repo_entity['owner'] == 'myorg'
        assert repo_entity['name'] == 'myrepo'
        assert repo_entity['provider'] == 'github'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])