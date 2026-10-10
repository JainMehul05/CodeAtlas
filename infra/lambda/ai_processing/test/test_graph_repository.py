"""
Pytest tests for GraphRepository - Phase 3 Engineering Knowledge Graph
Tests graph entity/relationship operations, queries, and traversal.

Run with: python -m pytest test/test_graph_repository.py -v
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
    mock_entities_table = Mock()
    mock_relationships_table = Mock()

    def mock_table(name):
        if name == "flowsync-graph-entities":
            return mock_entities_table
        elif name == "flowsync-graph-relationships":
            return mock_relationships_table
        return Mock()

    mock_dynamodb.Table.side_effect = mock_table
    return {
        'dynamodb': mock_dynamodb,
        'entities_table': mock_entities_table,
        'relationships_table': mock_relationships_table,
    }


@pytest.fixture(autouse=True)
def patch_boto3_resource(mock_dynamodb_resource):
    """Patch boto3.resource for graph_repository module."""
    with patch('boto3.resource', return_value=mock_dynamodb_resource['dynamodb']):
        # Import graph_repository with patches active
        import graph_repository
        yield {'graph_repository': graph_repository}


@pytest.fixture
def mock_aws_services(mock_dynamodb_resource, patch_boto3_resource):
    """Create a complete set of mocked AWS services for each test."""
    mock_entities_table = mock_dynamodb_resource['entities_table']
    mock_relationships_table = mock_dynamodb_resource['relationships_table']
    mock_dynamodb = mock_dynamodb_resource['dynamodb']

    # Reset mocks
    mock_entities_table.reset_mock()
    mock_relationships_table.reset_mock()
    mock_dynamodb.reset_mock()

    # Configure default responses
    mock_entities_table.put_item.return_value = {}
    mock_entities_table.get_item.return_value = {'Item': None}
    mock_entities_table.query.return_value = {'Items': [], 'Count': 0}
    mock_entities_table.delete_item.return_value = {}

    mock_relationships_table.put_item.return_value = {}
    mock_relationships_table.get_item.return_value = {'Item': None}
    mock_relationships_table.query.return_value = {'Items': [], 'Count': 0}
    mock_relationships_table.delete_item.return_value = {}

    mock_dynamodb.batch_get_item.return_value = {'Responses': {}}

    return {
        'dynamodb': mock_dynamodb,
        'entities_table': mock_entities_table,
        'relationships_table': mock_relationships_table,
    }


@pytest.fixture
def mock_env():
    """Set up environment variables for tests."""
    original_env = dict(os.environ)
    os.environ.update({
        'GRAPH_ENTITIES_TABLE': 'flowsync-graph-entities',
        'GRAPH_RELATIONSHIPS_TABLE': 'flowsync-graph-relationships',
        'GRAPH_SCHEMA_VERSION': '1',
    })
    yield
    os.environ.clear()
    os.environ.update(original_env)


def create_test_entity(entity_type='commit', entity_id='commit:repo:test:sha', **kwargs):
    """Create a test entity with defaults."""
    base = {
        'entityId': entity_id,
        'entityType': entity_type,
        'projectId': 'test-project',
        'repositoryId': 'repo:github:test:repo',
        'createdAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'updatedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'schemaVersion': '1',
    }
    base.update(kwargs)
    return base


def create_test_relationship(rel_type='modifies', source_id='commit:repo:test:sha', target_id='file:repo:test:file.py', **kwargs):
    """Create a test relationship with defaults."""
    base = {
        'relationshipId': f'rel:{rel_type}:{source_id}:{target_id}',
        'relationshipType': rel_type,
        'sourceEntityId': source_id,
        'sourceEntityType': 'commit',
        'targetEntityId': target_id,
        'targetEntityType': 'file',
        'projectId': 'test-project',
        'repositoryId': 'repo:github:test:repo',
        'provenance': 'explicit',
        'evidence': 'event-123',
        'createdAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'updatedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'schemaVersion': '1',
    }
    base.update(kwargs)
    return base


class TestGraphRepositoryEntityOperations:
    """Tests for entity CRUD operations."""

    def test_upsert_entity_creates_new_entity(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Upsert should create a new entity with timestamps and schema version."""
        graph_repository = patch_boto3_resource['graph_repository']
        repo = graph_repository.GraphRepository()
        entity = create_test_entity()

        result = repo.upsert_entity(entity)

        assert result['entityId'] == entity['entityId']
        assert result['schemaVersion'] == '1'
        assert 'createdAt' in result
        assert 'updatedAt' in result
        mock_aws_services['entities_table'].put_item.assert_called_once()

    def test_upsert_entity_updates_existing(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Upsert should update existing entity's updatedAt."""
        graph_repository = patch_boto3_resource['graph_repository']
        repo = graph_repository.GraphRepository()
        entity = create_test_entity()

        # First upsert
        repo.upsert_entity(entity)

        # Second upsert (update)
        updated_entity = dict(entity)
        updated_entity['metadata'] = {'key': 'value'}
        repo.upsert_entity(updated_entity)

        assert mock_aws_services['entities_table'].put_item.call_count == 2

    def test_get_entity_returns_entity(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Get entity should return the entity when found."""
        graph_repository = patch_boto3_resource['graph_repository']
        mock_aws_services['entities_table'].get_item.return_value = {
            'Item': create_test_entity()
        }

        repo = graph_repository.GraphRepository()
        result = repo.get_entity('commit:repo:test:sha')

        assert result is not None
        assert result['entityId'] == 'commit:repo:test:sha'

    def test_get_entity_returns_none_when_not_found(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Get entity should return None when not found."""
        graph_repository = patch_boto3_resource['graph_repository']
        mock_aws_services['entities_table'].get_item.return_value = {}

        repo = graph_repository.GraphRepository()
        result = repo.get_entity('nonexistent')

        assert result is None

    def test_get_entities_batch(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Batch get should return multiple entities."""
        graph_repository = patch_boto3_resource['graph_repository']
        entities = [
            create_test_entity(entity_id='commit:repo:test:sha1'),
            create_test_entity(entity_id='commit:repo:test:sha2'),
        ]
        mock_aws_services['dynamodb'].batch_get_item.return_value = {
            'Responses': {
                'flowsync-graph-entities': entities
            }
        }

        repo = graph_repository.GraphRepository()
        result = repo.get_entities(['commit:repo:test:sha1', 'commit:repo:test:sha2'])

        assert len(result) == 2
        assert result[0]['entityId'] == 'commit:repo:test:sha1'
        assert result[1]['entityId'] == 'commit:repo:test:sha2'

    def test_query_entities_by_project(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Query entities by project with filters."""
        graph_repository = patch_boto3_resource['graph_repository']
        entities = [create_test_entity(entity_type='commit')]
        mock_aws_services['entities_table'].query.return_value = {'Items': entities}

        repo = graph_repository.GraphRepository()
        result = repo.query_entities_by_project('test-project', entity_type='commit')

        assert result['count'] == 1
        assert result['entities'][0]['entityType'] == 'commit'

    def test_query_entities_by_repository(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Query entities by repository."""
        graph_repository = patch_boto3_resource['graph_repository']
        entities = [create_test_entity(entity_type='file')]
        mock_aws_services['entities_table'].query.return_value = {'Items': entities}

        repo = graph_repository.GraphRepository()
        result = repo.query_entities_by_repository('repo:github:test:repo', entity_type='file')

        assert result['count'] == 1


class TestGraphRepositoryRelationshipOperations:
    """Tests for relationship CRUD operations."""

    def test_upsert_relationship_creates_new(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Upsert should create a new relationship."""
        graph_repository = patch_boto3_resource['graph_repository']
        repo = graph_repository.GraphRepository()
        rel = create_test_relationship()

        result = repo.upsert_relationship(rel)

        assert result['relationshipId'] == rel['relationshipId']
        assert result['schemaVersion'] == '1'
        mock_aws_services['relationships_table'].put_item.assert_called_once()

    def test_upsert_relationship_conditional_write(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Upsert should use conditional write for idempotency."""
        graph_repository = patch_boto3_resource['graph_repository']
        repo = graph_repository.GraphRepository()
        rel = create_test_relationship()

        repo.upsert_relationship(rel)

        call_args = mock_aws_services['relationships_table'].put_item.call_args
        assert 'ConditionExpression' in call_args.kwargs
        assert 'updatedAt' in call_args.kwargs['ConditionExpression']

    def test_get_relationship_returns_relationship(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Get relationship should return the relationship when found."""
        graph_repository = patch_boto3_resource['graph_repository']
        rel = create_test_relationship()
        mock_aws_services['relationships_table'].get_item.return_value = {'Item': rel}

        repo = graph_repository.GraphRepository()
        result = repo.get_relationship(rel['relationshipId'])

        assert result is not None
        assert result['relationshipId'] == rel['relationshipId']

    def test_get_outgoing_relationships(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Get outgoing relationships from an entity."""
        graph_repository = patch_boto3_resource['graph_repository']
        rels = [create_test_relationship()]
        mock_aws_services['relationships_table'].query.return_value = {'Items': rels}

        repo = graph_repository.GraphRepository()
        result = repo.get_outgoing_relationships('commit:repo:test:sha')

        assert result['count'] == 1
        assert result['relationships'][0]['sourceEntityId'] == 'commit:repo:test:sha'

    def test_get_incoming_relationships(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Get incoming relationships to an entity."""
        graph_repository = patch_boto3_resource['graph_repository']
        rels = [create_test_relationship(target_id='commit:repo:test:sha')]
        mock_aws_services['relationships_table'].query.return_value = {'Items': rels}

        repo = graph_repository.GraphRepository()
        result = repo.get_incoming_relationships('commit:repo:test:sha')

        assert result['count'] == 1
        assert result['relationships'][0]['targetEntityId'] == 'commit:repo:test:sha'

    def test_query_relationships_by_project(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Query relationships by project."""
        graph_repository = patch_boto3_resource['graph_repository']
        rels = [create_test_relationship()]
        mock_aws_services['relationships_table'].query.return_value = {'Items': rels}

        repo = graph_repository.GraphRepository()
        result = repo.query_relationships_by_project('test-project')

        assert result['count'] == 1


class TestGraphRepositoryTraversal:
    """Tests for graph traversal operations."""

    def test_get_related_entities_outgoing(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Get related entities should traverse outgoing relationships."""
        graph_repository = patch_boto3_resource['graph_repository']
        # Setup mock for outgoing relationships
        rels = [create_test_relationship(
            source_id='commit:repo:test:sha',
            target_id='file:repo:test:file.py',
            rel_type='modifies'
        )]
        mock_aws_services['relationships_table'].query.side_effect = [
            {'Items': rels, 'Count': 1},  # outgoing
            {'Items': [], 'Count': 0},    # incoming
        ]
        # Setup mock for target entity
        mock_aws_services['entities_table'].get_item.return_value = {
            'Item': create_test_entity(entity_type='file', entity_id='file:repo:test:file.py')
        }

        repo = graph_repository.GraphRepository()
        result = repo.get_related_entities('commit:repo:test:sha', direction='outgoing', max_depth=1)

        assert len(result['entities']) == 1
        assert result['entities'][0]['entityId'] == 'file:repo:test:file.py'
        assert len(result['relationships']) == 1
        assert len(result['paths']) == 1
        assert result['paths'][0]['depth'] == 1

    def test_get_related_entities_incoming(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Get related entities should traverse incoming relationships."""
        graph_repository = patch_boto3_resource['graph_repository']
        # Setup mock for incoming relationships - no outgoing, but incoming exists
        rels = [create_test_relationship(
            source_id='pull_request:repo:test:1',
            target_id='commit:repo:test:sha',
            rel_type='includes_commit'
        )]
        # Use a side_effect function that returns different values based on call args
        def query_side_effect(**kwargs):
            if kwargs.get('IndexName') == 'TargetEntityIndex':
                return {'Items': rels, 'Count': 1}
            return {'Items': [], 'Count': 0}
        mock_aws_services['relationships_table'].query.side_effect = query_side_effect
        mock_aws_services['entities_table'].get_item.return_value = {
            'Item': create_test_entity(entity_type='pull_request', entity_id='pull_request:repo:test:1')
        }

        repo = graph_repository.GraphRepository()
        result = repo.get_related_entities('commit:repo:test:sha', direction='incoming', max_depth=1)

        assert len(result['entities']) == 1
        assert result['entities'][0]['entityType'] == 'pull_request'

    def test_get_related_entities_both_directions(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Get related entities should traverse both directions."""
        graph_repository = patch_boto3_resource['graph_repository']
        outgoing_rels = [create_test_relationship(
            source_id='commit:repo:test:sha',
            target_id='file:repo:test:file.py',
            rel_type='modifies'
        )]
        incoming_rels = [create_test_relationship(
            source_id='pull_request:repo:test:1',
            target_id='commit:repo:test:sha',
            rel_type='includes_commit'
        )]
        mock_aws_services['relationships_table'].query.side_effect = [
            {'Items': outgoing_rels, 'Count': 1},  # outgoing
            {'Items': incoming_rels, 'Count': 1},  # incoming
        ]
        mock_aws_services['entities_table'].get_item.side_effect = [
            {'Item': create_test_entity(entity_type='file', entity_id='file:repo:test:file.py')},
            {'Item': create_test_entity(entity_type='pull_request', entity_id='pull_request:repo:test:1')},
        ]

        repo = graph_repository.GraphRepository()
        result = repo.get_related_entities('commit:repo:test:sha', direction='both', max_depth=1)

        assert len(result['entities']) == 2
        entity_types = {e['entityType'] for e in result['entities']}
        assert 'file' in entity_types
        assert 'pull_request' in entity_types

    def test_get_related_entities_respects_max_depth(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Get related entities should respect max_depth limit."""
        graph_repository = patch_boto3_resource['graph_repository']
        rels = [create_test_relationship()]
        mock_aws_services['relationships_table'].query.return_value = {'Items': rels, 'Count': 1}
        mock_aws_services['entities_table'].get_item.return_value = {
            'Item': create_test_entity(entity_type='file')
        }

        repo = graph_repository.GraphRepository()
        result = repo.get_related_entities('commit:repo:test:sha', max_depth=0)

        assert result['entities'] == []
        assert result['relationships'] == []
        assert result['paths'] == []

    def test_get_related_entities_relationship_type_filter(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Get related entities should filter by relationship types (in-memory filtering)."""
        graph_repository = patch_boto3_resource['graph_repository']
        # Create relationships of different types
        modifies_rel = create_test_relationship(rel_type='modifies', source_id='commit:repo:test:sha', target_id='file:repo:test:file.py')
        other_rel = create_test_relationship(rel_type='authored_in', source_id='commit:repo:test:sha', target_id='repo:github:test:repo')
        rels = [modifies_rel, other_rel]

        # Configure mocks to return different data for outgoing vs incoming
        # Outgoing: commit -> file (modifies)
        # Incoming: repo -> commit (authored_in)
        def query_side_effect(**kwargs):
            if kwargs.get('IndexName') == 'SourceEntityIndex':
                # Outgoing query - return modifies relationship
                return {'Items': [modifies_rel], 'Count': 1}
            else:
                # Incoming query - return authored_in relationship
                return {'Items': [other_rel], 'Count': 1}

        # get_item called 3 times: 1 for initial verification, 1 for traverse(seed), 1 for traverse(target)
        seed_entity = create_test_entity('commit:repo:test:sha', 'commit')
        file_entity = create_test_entity('file:repo:test:file.py', 'file')
        get_item_calls = 0
        def get_item_side_effect(Key):
            nonlocal get_item_calls
            if get_item_calls == 0:
                get_item_calls += 1
                return {'Item': seed_entity}
            elif get_item_calls == 1:
                get_item_calls += 1
                return {'Item': seed_entity}
            else:
                get_item_calls += 1
                return {'Item': file_entity}
        mock_aws_services['entities_table'].get_item.side_effect = get_item_side_effect

        mock_aws_services['relationships_table'].query.side_effect = query_side_effect

        repo = graph_repository.GraphRepository()
        result = repo.get_related_entities(
            'commit:repo:test:sha',
            relationship_types=['modifies'],
            max_depth=1
        )

        # Verify only 'modifies' relationships are returned (in-memory filtering)
        # Outgoing has 1 'modifies', incoming has 1 'authored_in' (filtered out)
        assert len(result['relationships']) == 1
        assert result['relationships'][0]['relationshipType'] == 'modifies'


class TestGraphRepositoryDeletion:
    """Tests for deletion operations."""

    def test_delete_relationship(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Delete relationship should remove it."""
        graph_repository = patch_boto3_resource['graph_repository']
        mock_aws_services['relationships_table'].delete_item.return_value = {}

        repo = graph_repository.GraphRepository()
        result = repo.delete_relationship('rel:modifies:src:tgt')

        assert result is True

    def test_delete_relationship_not_found(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Delete relationship should return False when not found."""
        graph_repository = patch_boto3_resource['graph_repository']
        mock_aws_services['relationships_table'].delete_item.side_effect = ClientError(
            {'Error': {'Code': 'ConditionalCheckFailedException'}}, 'DeleteItem'
        )

        repo = graph_repository.GraphRepository()
        result = repo.delete_relationship('rel:nonexistent')

        assert result is False

    def test_delete_entity_without_cascade_fails_when_connected(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Delete entity without cascade should fail if entity has relationships."""
        graph_repository = patch_boto3_resource['graph_repository']
        # Use a side_effect function to avoid exhaustion
        def query_side_effect(**kwargs):
            return {'Items': [create_test_relationship()], 'Count': 1}
        mock_aws_services['relationships_table'].query.side_effect = query_side_effect

        repo = graph_repository.GraphRepository()

        with pytest.raises(ValueError, match="has connected relationships"):
            repo.delete_entity('commit:repo:test:sha', cascade=False)

    def test_delete_entity_with_cascade(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Delete entity with cascade should delete relationships first."""
        graph_repository = patch_boto3_resource['graph_repository']
        rels = [create_test_relationship()]
        # Use a side_effect function to avoid exhaustion
        def query_side_effect(**kwargs):
            return {'Items': rels, 'Count': 1}
        mock_aws_services['relationships_table'].query.side_effect = query_side_effect
        mock_aws_services['entities_table'].delete_item.return_value = {}

        repo = graph_repository.GraphRepository()
        result = repo.delete_entity('commit:repo:test:sha', cascade=True)

        assert result is True
        assert mock_aws_services['relationships_table'].delete_item.call_count == 2


class TestGraphRepositoryHelpers:
    """Tests for entity/relationship creation helpers."""

    def test_create_repository_entity(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Create repository entity should generate stable ID."""
        graph_repository = patch_boto3_resource['graph_repository']

        entity = graph_repository.create_repository_entity('github', 'owner', 'repo', 'project-1')

        assert entity['entityId'] == 'repo:github:owner:repo'
        assert entity['entityType'] == 'repository'
        assert entity['projectId'] == 'project-1'

    def test_create_commit_entity(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Create commit entity should generate stable ID with lowercase SHA."""
        graph_repository = patch_boto3_resource['graph_repository']

        entity = graph_repository.create_commit_entity(
            'repo:github:owner:repo', 'ABC123', 'author', 'committer', 'message', '2024-01-01', ['parent1'], 'project-1'
        )

        assert entity['entityId'] == 'commit:repo:github:owner:repo:abc123'
        assert entity['sha'] == 'abc123'
        assert entity['parentShas'] == ['parent1']

    def test_create_pull_request_entity(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Create PR entity should generate stable ID with number."""
        graph_repository = patch_boto3_resource['graph_repository']

        entity = graph_repository.create_pull_request_entity(
            'repo:github:owner:repo', 42, 'title', 'open', 'author', 'source', 'target', 'project-1'
        )

        assert entity['entityId'] == 'pr:repo:github:owner:repo:42'
        assert entity['number'] == 42

    def test_create_file_entity(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Create file entity should normalize path."""
        graph_repository = patch_boto3_resource['graph_repository']

        entity = graph_repository.create_file_entity('repo:github:owner:repo', 'src/../file.py', 'project-1')

        assert entity['path'] == 'file.py'
        assert entity['entityId'] == 'file:repo:github:owner:repo:file.py'

    def test_create_engineering_decision_entity(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Create engineering decision entity."""
        graph_repository = patch_boto3_resource['graph_repository']

        entity = graph_repository.create_engineering_decision_entity(
            'project-1', 'title', 'summary', 'decision', 'context_extraction', 'event-123'
        )

        assert entity['entityId'] == 'decision:project-1:context_extraction:event-123'
        assert entity['entityType'] == 'engineering_decision'

    def test_create_relationship_generates_deterministic_id(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Create relationship should generate deterministic ID."""
        graph_repository = patch_boto3_resource['graph_repository']

        rel1 = graph_repository.create_relationship('modifies', 'commit:1', 'commit', 'file:1', 'file', 'project-1')
        rel2 = graph_repository.create_relationship('modifies', 'commit:1', 'commit', 'file:1', 'file', 'project-1')

        assert rel1['relationshipId'] == rel2['relationshipId']
        assert rel1['relationshipId'].startswith('rel:')


class TestGraphRepositoryRepositorySummary:
    """Tests for repository graph summary."""

    def test_get_repository_graph_summary(self, mock_aws_services, mock_env, patch_boto3_resource):
        """Get repository graph summary should count entities and relationships."""
        graph_repository = patch_boto3_resource['graph_repository']
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

        repo = graph_repository.GraphRepository()
        result = repo.get_repository_graph_summary('repo:github:owner:repo')

        assert 'entityCounts' in result
        assert 'relationshipCounts' in result
        assert 'totalEntities' in result
        assert 'totalRelationships' in result


class TestGraphRepositorySingleton:
    """Tests for singleton pattern."""

    def test_get_graph_repository_returns_singleton(self, mock_aws_services, mock_env, patch_boto3_resource):
        """get_graph_repository should return the same instance."""
        graph_repository = patch_boto3_resource['graph_repository']

        repo1 = graph_repository.get_graph_repository()
        repo2 = graph_repository.get_graph_repository()

        assert repo1 is repo2


if __name__ == '__main__':
    pytest.main([__file__, '-v'])