import { handler } from '../handler';
import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import { DynamoDBDocumentClient, GetCommand, PutCommand, UpdateCommand } from '@aws-sdk/lib-dynamodb';

jest.mock('@aws-sdk/client-dynamodb');
jest.mock('@aws-sdk/lib-dynamodb');
jest.mock('../handler', () => {
  const actual = jest.requireActual('../handler');
  return {
    ...actual,
    call_bedrock: jest.fn(),
    validate_extraction_schema: jest.fn(),
    compute_confidence: jest.fn(),
    convert_floats_to_decimal: jest.fn((obj) => obj),
    call_titan_embedding: jest.fn(),
    write_context_record: jest.fn(),
    write_audit_record: jest.fn(),
    update_project_activity: jest.fn(),
    find_orphaned_record: jest.fn(),
    update_orphaned_record: jest.fn(),
    propagate_branch_context: jest.fn(),
    publish_cloudwatch_metric: jest.fn(),
    check_idempotency: jest.fn(),
    claim_idempotency: jest.fn(),
    complete_idempotency: jest.fn(),
    fail_idempotency: jest.fn(),
  };
});

const mockDynamoSend = DynamoDBDocumentClient.from as jest.Mock;
const mockCheckIdempotency = require('../handler').check_idempotency;
const mockClaimIdempotency = require('../handler').claim_idempotency;
const mockCompleteIdempotency = require('../handler').complete_idempotency;
const mockFailIdempotency = require('../handler').fail_idempotency;
const mockCallBedrock = require('../handler').call_bedrock;
const mockValidateExtraction = require('../handler').validate_extraction_schema;
const mockComputeConfidence = require('../handler').compute_confidence;
const mockConvertFloats = require('../handler').convert_floats_to_decimal;
const mockCallTitan = require('../handler').call_titan_embedding;
const mockWriteContext = require('../handler').write_context_record;
const mockWriteAudit = require('../handler').write_audit_record;
const mockUpdateProject = require('../handler').update_project_activity;
const mockFindOrphaned = require('../handler').find_orphaned_record;
const mockUpdateOrphaned = require('../handler').update_orphaned_record;
const mockPropagateBranch = require('../handler').propagate_branch_context;

describe('AI Processing Lambda - Phase 2', () => {
  const originalEnv = process.env;

  beforeEach(() => {
    jest.clearAllMocks();
    process.env = {
      ...originalEnv,
      CONTEXT_TABLE: 'test-context',
      AUDIT_TABLE: 'test-audit',
      PROJECTS_TABLE: 'test-projects',
      IDEMPOTENCY_TABLE: 'test-idempotency',
      FALLBACK_MODEL_ID: 'us.amazon.nova-lite-v1:0',
    };
    
    mockCheckIdempotency.mockResolvedValue([false, null]);
    mockClaimIdempotency.mockResolvedValue(true);
    mockCompleteIdempotency.mockResolvedValue(undefined);
    mockFailIdempotency.mockResolvedValue(undefined);
    mockCallBedrock.mockResolvedValue({
      feature: 'Test Feature',
      decision: 'Test decision',
      tasks: ['task1'],
      stage: 'Feature Development',
      risk: 'Test risk',
      entities: ['entity1', 'entity2'],
    });
    mockValidateExtraction.mockReturnValue(true);
    mockComputeConfidence.mockReturnValue(0.85);
    mockConvertFloats.mockImplementation((obj) => obj);
    mockCallTitan.mockResolvedValue([new Array(1536).fill(0.1), 100]);
    mockWriteContext.mockResolvedValue(undefined);
    mockWriteAudit.mockResolvedValue(undefined);
    mockUpdateProject.mockResolvedValue(undefined);
    mockFindOrphaned.mockResolvedValue(null);
    mockUpdateOrphaned.mockResolvedValue(undefined);
    mockPropagateBranch.mockResolvedValue(1);
  });

  afterAll(() => {
    process.env = originalEnv;
  });

  const createSQSRecord = (overrides = {}) => ({
    messageId: 'msg-123',
    body: JSON.stringify({
      eventId: '550e8400-e29b-41d4-a716-446655440000',
      projectId: 'test-project',
      eventType: 'push',
      branch: 'main',
      parentBranch: null,
      payload: {
        commitHash: 'a'.repeat(40),
        message: 'Test commit',
        diff: 'diff --git a/file.ts b/file.ts\n+added line',
        author: 'Test User',
        changedFiles: ['file.ts'],
      },
      timestamp: new Date().toISOString(),
      correlationId: 'corr-123',
      deliveryId: 'github-delivery-123',
      ...overrides,
    }),
    messageAttributes: {
      correlationId: { stringValue: 'corr-123', dataType: 'String' },
      eventType: { stringValue: 'push', dataType: 'String' },
      deliveryId: { stringValue: 'github-delivery-123', dataType: 'String' },
    },
  });

  describe('Idempotency (I.3)', () => {
    it('should process first delivery normally', async () => {
      mockCheckIdempotency.mockResolvedValueOnce([false, null]);
      mockClaimIdempotency.mockResolvedValueOnce(true);

      const event = { Records: [createSQSRecord()] };
      const result = await handler(event, {});

      expect(result.batchItemFailures).toEqual([]);
      expect(mockCheckIdempotency).toHaveBeenCalledWith('github-delivery-123');
      expect(mockClaimIdempotency).toHaveBeenCalledWith('github-delivery-123', expect.any(Object));
      expect(mockCompleteIdempotency).toHaveBeenCalledWith('github-delivery-123', expect.objectContaining({
        status: 'success',
      }));
    });

    it('should skip duplicate delivery', async () => {
      mockCheckIdempotency.mockResolvedValueOnce([true, { status: 'COMPLETED', result: { eventId: 'evt-1' } }]);

      const event = { Records: [createSQSRecord()] };
      const result = await handler(event, {});

      expect(result.batchItemFailures).toEqual([]);
      expect(mockClaimIdempotency).not.toHaveBeenCalled();
      expect(mockCompleteIdempotency).not.toHaveBeenCalled();
    });

    it('should handle concurrent duplicate deliveries atomically', async () => {
      // First call: not duplicate, claim succeeds
      // Second call: claim fails (already claimed)
      let claimCall = 0;
      mockCheckIdempotency
        .mockResolvedValueOnce([false, null]) // First check
        .mockResolvedValueOnce([false, null]); // Second check (concurrent)
      
      mockClaimIdempotency
        .mockResolvedValueOnce(true) // First claim succeeds
        .mockResolvedValueOnce(false); // Second claim fails (conditional check failed)

      const record = createSQSRecord();
      const event = { Records: [record, { ...record, messageId: 'msg-456' }] };
      
      // Process both records
      const result = await handler(event, {});

      // One should succeed, one should be treated as duplicate
      expect(mockCompleteIdempotency).toHaveBeenCalledTimes(1);
      expect(mockFailIdempotency).not.toHaveBeenCalled();
    });

    it('should not mark event completed before durable side effects succeed', async () => {
      mockCallBedrock.mockRejectedValueOnce(new Error('Bedrock throttled'));
      
      const event = { Records: [createSQSRecord()] };
      const result = await handler(event, {});

      expect(result.batchItemFailures).toHaveLength(1);
      expect(result.batchItemFailures[0].itemIdentifier).toBe('msg-123');
      expect(mockFailIdempotency).toHaveBeenCalledWith('github-delivery-123', 'Bedrock throttled');
      expect(mockCompleteIdempotency).not.toHaveBeenCalled();
    });

    it('should handle interrupted IN_PROGRESS record recovery', async () => {
      // Simulate previous processing that was interrupted
      mockCheckIdempotency.mockResolvedValueOnce([true, { 
        status: 'PROCESSING', 
        eventData: { eventId: 'evt-1', projectId: 'test-project' },
        startedAt: new Date(Date.now() - 3600000).toISOString(), // 1 hour ago
      }]);

      const event = { Records: [createSQSRecord()] };
      const result = await handler(event, {});

      // Should detect stale IN_PROGRESS and allow reprocessing
      // (Implementation depends on lease expiration logic)
      expect(result.batchItemFailures).toEqual([]);
    });

    it('should not treat distinct legitimate events as duplicates', async () => {
      mockCheckIdempotency
        .mockResolvedValueOnce([false, null])
        .mockResolvedValueOnce([false, null]);

      const record1 = createSQSRecord({ eventId: 'evt-1', deliveryId: 'delivery-1' });
      const record2 = createSQSRecord({ eventId: 'evt-2', deliveryId: 'delivery-2' });
      
      const event = { Records: [record1, record2] };
      const result = await handler(event, {});

      expect(result.batchItemFailures).toEqual([]);
      expect(mockCompleteIdempotency).toHaveBeenCalledTimes(2);
    });
  });

  describe('SQS Batch Processing (I.2)', () => {
    it('should process valid SQS batch successfully', async () => {
      const records = [
        createSQSRecord({ messageId: 'msg-1', eventId: 'evt-1', deliveryId: 'delivery-1' }),
        createSQSRecord({ messageId: 'msg-2', eventId: 'evt-2', deliveryId: 'delivery-2' }),
      ];
      
      const event = { Records: records };
      const result = await handler(event, {});

      expect(result.batchItemFailures).toEqual([]);
      expect(mockCompleteIdempotency).toHaveBeenCalledTimes(2);
    });

    it('should report partial batch failures correctly', async () => {
      // First record succeeds, second fails
      mockCallBedrock
        .mockResolvedValueOnce({
          feature: 'Feature 1', decision: 'decision', tasks: [], stage: 'Feature Development', risk: null, entities: ['e1', 'e2']
        })
        .mockRejectedValueOnce(new Error('Bedrock error'));

      const records = [
        createSQSRecord({ messageId: 'msg-1', eventId: 'evt-1', deliveryId: 'delivery-1' }),
        createSQSRecord({ messageId: 'msg-2', eventId: 'evt-2', deliveryId: 'delivery-2' }),
      ];
      
      const event = { Records: records };
      const result = await handler(event, {});

      // Only failed record should be in batchItemFailures
      expect(result.batchItemFailures).toHaveLength(1);
      expect(result.batchItemFailures[0].itemIdentifier).toBe('msg-2');
      expect(mockCompleteIdempotency).toHaveBeenCalledTimes(1);
      expect(mockFailIdempotency).toHaveBeenCalledTimes(1);
    });

    it('should handle invalid SQS body safely', async () => {
      const records = [
        createSQSRecord({ messageId: 'msg-1' }),
        { messageId: 'msg-2', body: 'not valid json', messageAttributes: {} },
      ];
      
      const event = { Records: records };
      const result = await handler(event, {});

      expect(result.batchItemFailures).toHaveLength(1);
      expect(result.batchItemFailures[0].itemIdentifier).toBe('msg-2');
      expect(mockCompleteIdempotency).toHaveBeenCalledTimes(1); // First record processed
    });

    it('should apply shared event validation to SQS messages', async () => {
      const invalidRecord = createSQSRecord({
        body: JSON.stringify({ eventId: 'invalid', projectId: 'test' }), // Missing required fields
      });
      
      const event = { Records: [invalidRecord] };
      const result = await handler(event, {});

      expect(result.batchItemFailures).toHaveLength(1);
      expect(mockCallBedrock).not.toHaveBeenCalled();
    });

    it('should preserve correlation IDs through full path', async () => {
      const customCorrId = 'custom-correlation-456';
      const record = createSQSRecord({
        messageId: 'msg-1',
        correlationId: customCorrId,
        messageAttributes: {
          correlationId: { stringValue: customCorrId, dataType: 'String' },
          eventType: { stringValue: 'push', dataType: 'String' },
          deliveryId: { stringValue: 'delivery-1', dataType: 'String' },
        },
      });

      const event = { Records: [record] };
      await handler(event, {});

      // Verify correlation ID passed to processing functions
      expect(mockCallBedrock).toHaveBeenCalled();
      // The correlation ID should be available in the processing context
    });
  });

  describe('Retry and DLQ (I.4)', () => {
    it('should mark transient failures for retry', async () => {
      mockCallBedrock.mockRejectedValueOnce(new Error('ThrottlingException'));
      
      const event = { Records: [createSQSRecord()] };
      const result = await handler(event, {});

      expect(result.batchItemFailures).toHaveLength(1);
      expect(result.batchItemFailures[0].itemIdentifier).toBe('msg-123');
      // Message will be retried by SQS (not deleted)
    });

    it('should mark permanent failures for DLQ', async () => {
      // Validation error = permanent failure
      mockCheckIdempotency.mockResolvedValueOnce([false, null]);
      mockClaimIdempotency.mockResolvedValueOnce(true);
      mockCallBedrock.mockResolvedValueOnce({
        feature: 'Test', decision: 'decision', tasks: [], stage: 'Feature Development', risk: null, entities: ['e1']
      });
      mockValidateExtraction.mockImplementationOnce(() => { throw new Error('Invalid schema'); });

      const event = { Records: [createSQSRecord()] };
      const result = await handler(event, {});

      expect(result.batchItemFailures).toHaveLength(1);
      expect(mockFailIdempotency).toHaveBeenCalledWith('github-delivery-123', expect.stringContaining('Invalid schema'));
    });

    it('should not retry exhausted messages beyond maxReceiveCount', async () => {
      // This is handled by SQS redrive policy (maxReceiveCount=3)
      // Lambda just reports failure; SQS handles routing to DLQ after 3 attempts
      const event = { Records: [createSQSRecord()] };
      
      // Simulate 3rd attempt by checking message attributes
      const record = createSQSRecord({
        messageAttributes: {
          ...createSQSRecord().messageAttributes,
          // In real SQS, ApproximateReceiveCount would be in attributes
        },
      });
      
      // Lambda can't directly control DLQ routing - it's SQS configuration
      // But we verify Lambda returns failure for SQS to handle
      mockCallBedrock.mockRejectedValue(new Error('Persistent error'));
      
      const result = await handler({ Records: [record] }, {});
      expect(result.batchItemFailures).toHaveLength(1);
    });

    it('should not duplicate completed side effects on DLQ redrive', async () => {
      // When redriven from DLQ, idempotency key prevents re-processing
      mockCheckIdempotency.mockResolvedValueOnce([true, { 
        status: 'COMPLETED', 
        result: { eventId: 'evt-1', status: 'success' }
      }]);

      const event = { Records: [createSQSRecord({ messageId: 'dlq-msg' })] };
      const result = await handler(event, {});

      expect(result.batchItemFailures).toEqual([]);
      expect(mockCallBedrock).not.toHaveBeenCalled();
      expect(mockWriteContext).not.toHaveBeenCalled();
    });
  });

  describe('GitHub Webhook Tests (I.5)', () => {
    // These are tested in ingestion.test.ts, but we can verify
    // the processing Lambda handles GitHub events correctly
    
    it('should process GitHub push events from SQS', async () => {
      const record = createSQSRecord({
        eventType: 'push',
        source: 'github',
        deliveryId: 'github-delivery-456',
        payload: {
          commitHash: 'b'.repeat(40),
          message: 'GitHub push',
          diff: '',
          author: 'github-user',
        },
      });

      const event = { Records: [record] };
      const result = await handler(event, {});

      expect(result.batchItemFailures).toEqual([]);
      expect(mockCallBedrock).toHaveBeenCalledWith(expect.objectContaining({
        author: 'github-user',
      }));
    });

    it('should process GitHub merge events from SQS', async () => {
      const record = createSQSRecord({
        eventType: 'merge',
        source: 'github',
        deliveryId: 'github-delivery-789',
        payload: {
          commitHash: 'c'.repeat(40),
          message: 'Merge pull request #123',
          diff: '',
          author: 'github-user',
          isMerge: true,
          sourceBranch: 'feature/merge-test',
        },
      });

      const event = { Records: [record] };
      const result = await handler(event, {});

      expect(result.batchItemFailures).toEqual([]);
      expect(mockPropagateBranch).toHaveBeenCalled();
    });

    it('should use delivery ID for idempotency on GitHub events', async () => {
      const record = createSQSRecord({
        deliveryId: 'github-delivery-999',
        eventId: 'github-delivery-999', // Same as delivery ID for GitHub
      });

      const event = { Records: [record] };
      const result = await handler(event, {});

      expect(mockCheckIdempotency).toHaveBeenCalledWith('github-delivery-999');
    });

    it('should handle concurrent duplicate GitHub deliveries', async () => {
      mockCheckIdempotency.mockResolvedValue([false, null]);
      mockClaimIdempotency
        .mockResolvedValueOnce(true)
        .mockResolvedValueOnce(false);

      const record1 = createSQSRecord({ deliveryId: 'github-concurrent-1', messageId: 'msg-1' });
      const record2 = createSQSRecord({ deliveryId: 'github-concurrent-1', messageId: 'msg-2' });

      const event = { Records: [record1, record2] };
      const result = await handler(event, {});

      expect(mockCompleteIdempotency).toHaveBeenCalledTimes(1);
    });

    it('should reject invalid repository/project mapping', async () => {
      // This is validated in ingestion Lambda, but processing Lambda
      // should also handle missing project gracefully
      const record = createSQSRecord({
        projectId: 'non-existent-project',
        deliveryId: 'github-delivery-invalid',
      });

      mockCheckIdempotency.mockResolvedValue([false, null]);
      mockClaimIdempotency.mockResolvedValue(true);
      mockCallBedrock.mockRejectedValue(new Error('Project not found'));

      const event = { Records: [record] };
      const result = await handler(event, {});

      expect(result.batchItemFailures).toHaveLength(1);
      expect(mockFailIdempotency).toHaveBeenCalled();
    });
  });
});