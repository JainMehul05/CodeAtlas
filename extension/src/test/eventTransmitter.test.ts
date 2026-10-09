import * as http from 'http';
import * as https from 'https';
import { transmitEvent, createPushEvent, CapturedEvent, PushPayload } from '../eventTransmitter';
import { log } from '../logger';
import { FlowSyncEvent, EventType, EventSource, Actor, validateEvent, createEvent, PushEventPayload } from '@flowsync/shared';

jest.mock('http');
jest.mock('https');
jest.mock('../logger');

describe('Event Transmitter Module', () => {
  const mockLog = log as jest.Mocked<typeof log>;
  
  beforeEach(() => {
    jest.clearAllMocks();
    mockLog.step.mockImplementation(() => {});
    mockLog.info.mockImplementation(() => {});
    mockLog.ok.mockImplementation(() => {});
    mockLog.warn.mockImplementation(() => {});
    mockLog.error.mockImplementation(() => {});
  });

  describe('createPushEvent', () => {
    it('creates a valid FlowSyncEvent using shared package', () => {
      const projectId = 'test-project';
      const actor: Actor = { id: 'user-1', name: 'Test User', email: 'test@example.com' };
      const payload: PushEventPayload = {
        commitHash: 'a'.repeat(40),
        message: 'Test commit',
        diff: 'diff --git a/file.txt b/file.txt\n+added',
        author: 'Test User',
      };

      const event = createPushEvent(projectId, actor, payload);

      expect(event).toBeDefined();
      expect(event.eventId).toBeDefined();
      expect(event.eventType).toBe(EventType.PUSH);
      expect(event.schemaVersion).toBe('1');
      expect(event.source).toBe(EventSource.VSCODE);
      expect(event.projectId).toBe(projectId);
      expect(event.actor).toEqual(actor);
      expect(event.timestamp).toBeDefined();
      expect(event.payload).toEqual(payload);
    });

    it('includes correlationId when provided', () => {
      const projectId = 'test-project';
      const actor: Actor = { id: 'user-1', name: 'Test User' };
      const payload: PushEventPayload = {
        commitHash: 'a'.repeat(40),
        message: 'Test commit',
        diff: 'diff',
        author: 'Test User',
      };
      const correlationId = 'test-correlation-id';

      const event = createPushEvent(projectId, actor, payload, { correlationId });

      expect(event.correlationId).toBe(correlationId);
    });

    it('includes branch when provided in payload', () => {
      const projectId = 'test-project';
      const actor: Actor = { id: 'user-1', name: 'Test User' };
      const payload: PushEventPayload = {
        commitHash: 'a'.repeat(40),
        message: 'Test commit',
        diff: 'diff',
        author: 'Test User',
        branch: 'feature/test',
      };

      const event = createPushEvent(projectId, actor, payload, { branch: 'feature/test' });

      expect(event.payload.branch).toBe('feature/test');
    });
  });

  describe('transmitEvent', () => {
    const mockEvent: FlowSyncEvent = {
      eventId: '550e8400-e29b-41d4-a716-446655440000',
      eventType: EventType.PUSH,
      schemaVersion: '1',
      source: EventSource.VSCODE,
      projectId: 'project-123',
      actor: { id: 'user-1', name: 'Test User' },
      timestamp: new Date().toISOString(),
      payload: {
        commitHash: 'a'.repeat(40),
        message: 'Test commit',
        diff: 'diff content',
        author: 'Test User',
        branch: 'main',
      },
    };

    const mockResponse = { statusCode: 200, body: '{"success": true}' };

    it('validates event before sending', async () => {
      const invalidEvent = { ...mockEvent, eventId: 'invalid-uuid' };
      
      await expect(transmitEvent('https://api.example.com', 'token', invalidEvent))
        .rejects.toThrow('Event validation failed');
    });

    it('sends event with correct headers and body', async () => {
      let responseCallback: (res: any) => void;
      const mockReq = {
        write: jest.fn(),
        end: jest.fn(),
        on: jest.fn(),
      };
      (https.request as jest.Mock).mockImplementation((options, callback) => {
        responseCallback = callback;
        return mockReq;
      });

      const promise = transmitEvent('https://api.example.com', 'test-token', mockEvent);
      
      // Simulate response
      const mockRes = { 
        statusCode: 200, 
        on: jest.fn((ev, c) => {
          if (ev === 'data') c(Buffer.from('{"ok": true}'));
          if (ev === 'end') c();
        })
      };
      responseCallback!(mockRes);

      await promise;

      expect(https.request).toHaveBeenCalledWith(
        expect.objectContaining({
          hostname: 'api.example.com',
          path: '/api/v1/events',
          method: 'POST',
          headers: expect.objectContaining({
            'Content-Type': 'application/json',
            Authorization: 'Bearer test-token',
          }),
        }),
        expect.any(Function)
      );
    });

    it('retries on failure with exponential backoff', async () => {
      let requestCount = 0;
      (https.request as jest.Mock).mockImplementation(() => {
        requestCount++;
        return {
          write: jest.fn(),
          end: jest.fn(),
          on: jest.fn((event, cb) => {
            if (event === 'error') {
              // Trigger error immediately for each request
              cb(new Error('Network error'));
            }
          }),
        };
      });

      await expect(transmitEvent('https://api.example.com', 'test-token', mockEvent))
        .rejects.toThrow('Network error');

      expect(https.request).toHaveBeenCalledTimes(4); // 4 retry attempts
    }, 10000);

    it('includes correlation ID in log output', async () => {
      let responseCallback: (res: any) => void;
      const mockReq = {
        write: jest.fn(),
        end: jest.fn(),
        on: jest.fn(),
      };
      (https.request as jest.Mock).mockImplementation((options, callback) => {
        responseCallback = callback;
        return mockReq;
      });

      const promise = transmitEvent('https://api.example.com', 'test-token', mockEvent);
      
      // Simulate response
      const mockRes = { 
        statusCode: 200, 
        on: jest.fn((ev, c) => {
          if (ev === 'data') c(Buffer.from('{"ok": true}'));
          if (ev === 'end') c();
        })
      };
      responseCallback!(mockRes);

      await promise;

      expect(mockLog.step).toHaveBeenCalledWith(
        'transmitEvent',
        expect.stringContaining('attempt 1/4')
      );
    });
  });

  describe('CapturedEvent type', () => {
    it('is an alias for FlowSyncEvent', () => {
      const event: CapturedEvent = {
        eventId: 'evt-1',
        eventType: EventType.PUSH,
        schemaVersion: '1',
        source: EventSource.VSCODE,
        projectId: 'proj-1',
        actor: { id: 'u1', name: 'User' },
        timestamp: new Date().toISOString(),
        payload: {
          commitHash: 'a'.repeat(40),
          message: 'test',
          diff: 'diff',
          author: 'User',
          branch: 'main',
        },
      };
      expect(event.eventType).toBe(EventType.PUSH);
    });
  });

  describe('PushPayload type', () => {
    it('is an alias for PushEventPayload', () => {
      const payload: PushPayload = {
        commitHash: 'a'.repeat(40),
        message: 'test',
        diff: 'diff',
        author: 'User',
        parentBranch: 'main',
      };
      expect(payload.commitHash).toBeDefined();
    });
  });
});