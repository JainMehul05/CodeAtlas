/**
 * FlowSync Shared Package Tests
 */

import {
  FlowSyncEventSchema,
  validateEvent,
  createEvent,
  EventType,
  EventSource,
  isPushPayload,
  isDeveloperNotePayload,
  isAgentReasoningPayload,
  isMergePayload,
} from "./events";

import {
  FlowSyncError,
  ErrorCode,
  toFlowSyncError,
  isFlowSyncError,
} from "./errors";

import {
  generateCorrelationId,
  getOrCreateCorrelationId as extractCorrelationId,
  withCorrelationId,
  runWithCorrelation,
  getCorrelationId,
  extractCorrelationFromApiGateway,
} from "./correlation";

describe("Event Validation", () => {
  const validActor = { id: "user-123", name: "Test User", email: "test@example.com" };
  const validPushPayload = {
    commitHash: "a".repeat(40),
    message: "Test commit",
    diff: "diff --git a/file.txt b/file.txt\n+added line",
    author: "Test User",
  };

  it("should create a valid push event", () => {
    const event = createEvent(EventType.PUSH, EventSource.VSCODE, "project-123", validActor, validPushPayload);
    
    expect(event.eventId).toBeDefined();
    expect(event.eventType).toBe(EventType.PUSH);
    expect(event.schemaVersion).toBe("1");
    expect(event.source).toBe(EventSource.VSCODE);
    expect(event.projectId).toBe("project-123");
    expect(event.actor).toEqual(validActor);
    expect(event.timestamp).toBeDefined();
    expect(event.payload).toEqual(validPushPayload);
  });

  it("should validate a correct push event", () => {
    const event = createEvent(EventType.PUSH, EventSource.VSCODE, "project-123", validActor, validPushPayload);
    const result = validateEvent(event);
    
    expect(result.success).toBe(true);
    expect(result.data).toBeDefined();
  });

  it("should reject event with invalid UUID", () => {
    const event = createEvent(EventType.PUSH, EventSource.VSCODE, "project-123", validActor, validPushPayload);
    event.eventId = "invalid-uuid";
    
    const result = validateEvent(event);
    expect(result.success).toBe(false);
    expect(result.errors).toBeDefined();
    expect(result.errors![0].field).toBe("eventId");
  });

  it("should reject event with missing projectId", () => {
    const event = createEvent(EventType.PUSH, EventSource.VSCODE, "project-123", validActor, validPushPayload);
    event.projectId = "";
    
    const result = validateEvent(event);
    expect(result.success).toBe(false);
  });

  it("should reject event with invalid commit hash", () => {
    const payload = { ...validPushPayload, commitHash: "invalid" };
    const event = createEvent(EventType.PUSH, EventSource.VSCODE, "project-123", validActor, payload);
    
    const result = validateEvent(event);
    expect(result.success).toBe(false);
  });

  it("should reject event with too large diff", () => {
    const payload = { ...validPushPayload, diff: "x".repeat(50001) };
    const event = createEvent(EventType.PUSH, EventSource.VSCODE, "project-123", validActor, payload);
    
    const result = validateEvent(event);
    expect(result.success).toBe(false);
  });
});

describe("Payload Type Guards", () => {
  it("should identify push payload", () => {
    const payload = { commitHash: "a".repeat(40), message: "test", diff: "diff", author: "user" };
    expect(isPushPayload(payload)).toBe(true);
  });

  it("should identify developer note payload", () => {
    const payload = { text: "note", filePath: "src/file.ts", lineNumber: 10 };
    expect(isDeveloperNotePayload(payload)).toBe(true);
  });

  it("should identify agent reasoning payload", () => {
    const payload = { reasoning: "reason", branch: "main", author: "user" };
    expect(isAgentReasoningPayload(payload)).toBe(true);
  });

  it("should identify merge payload", () => {
    const payload = { sourceBranch: "feature", targetBranch: "main", commitHash: "a".repeat(40), author: "user" };
    expect(isMergePayload(payload)).toBe(true);
  });
});

describe("Error Handling", () => {
  it("should create validation error", () => {
    const error = FlowSyncError.validation("Invalid input", { field: "name" }, "corr-123");
    
    expect(error.code).toBe(ErrorCode.VALIDATION_ERROR);
    expect(error.statusCode).toBe(400);
    expect(error.correlationId).toBe("corr-123");
    expect(error.details).toEqual({ field: "name" });
  });

  it("should create authentication error", () => {
    const error = FlowSyncError.authentication("Invalid token", "corr-456");
    
    expect(error.code).toBe(ErrorCode.AUTHENTICATION_ERROR);
    expect(error.statusCode).toBe(401);
  });

  it("should create not found error", () => {
    const error = FlowSyncError.notFound("Project", "corr-789");
    
    expect(error.code).toBe(ErrorCode.NOT_FOUND);
    expect(error.statusCode).toBe(404);
    expect(error.message).toBe("Project not found");
  });

  it("should create internal error", () => {
    const cause = new Error("DB connection failed");
    const error = FlowSyncError.internal("Database error", cause, "corr-999");
    
    expect(error.code).toBe(ErrorCode.INTERNAL_ERROR);
    expect(error.statusCode).toBe(500);
    expect(error.isOperational).toBe(false);
    expect(error.cause).toBe(cause);
  });

  it("should convert unknown error to FlowSyncError", () => {
    const error = toFlowSyncError(new Error("Random error"), "corr-abc");
    
    expect(isFlowSyncError(error)).toBe(true);
    expect(error.code).toBe(ErrorCode.INTERNAL_ERROR);
  });

  it("should preserve FlowSyncError and add correlationId", () => {
    const original = FlowSyncError.validation("test");
    const converted = toFlowSyncError(original, "new-corr");
    
    expect(converted).not.toBe(original); // New instance created with correlationId
    expect(converted.code).toBe(original.code);
    expect(converted.message).toBe(original.message);
    expect(converted.correlationId).toBe("new-corr");
  });

  it("should return same FlowSyncError when no new correlationId", () => {
    const original = FlowSyncError.validation("test", undefined, "existing-corr");
    const converted = toFlowSyncError(original);
    
    expect(converted).toBe(original);
    expect(converted.correlationId).toBe("existing-corr");
  });

  it("should serialize error without stack trace or details in production", () => {
    const error = FlowSyncError.validation("test");
    const response = error.toResponse();
    
    expect(response.error).toBe(ErrorCode.VALIDATION_ERROR);
    expect(response.message).toBe("test");
    expect(response.details).toBeUndefined();
  });
});

describe("Correlation IDs", () => {
  it("should generate valid UUID v4", () => {
    const id = generateCorrelationId();
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  });

  it("should extract correlation ID from headers", () => {
    const headers = { "x-correlation-id": "test-corr-123" };
    expect(extractCorrelationId(headers)).toBe("test-corr-123");
  });

  it("should extract request ID from headers", () => {
    const headers = { "x-request-id": "req-456" };
    expect(extractCorrelationId(headers)).toBe("req-456");
  });

  it("should generate new ID when no headers", () => {
    const id = extractCorrelationId({});
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  });

  it("should add correlation ID to headers", () => {
    const headers = { "content-type": "application/json" };
    const withCorr = withCorrelationId(headers, "test-corr");
    
    expect(withCorr["x-correlation-id"]).toBe("test-corr");
    expect(withCorr["x-request-id"]).toBe("test-corr");
    expect(withCorr["content-type"]).toBe("application/json");
  });

  it("should run with correlation context", () => {
    const result = runWithCorrelation("test-corr", () => {
      expect(getCorrelationId()).toBe("test-corr");
      return "done";
    });
    
    expect(result).toBe("done");
    expect(getCorrelationId()).toBeUndefined();
  });

  it("should extract from API Gateway event", () => {
    const event = {
      headers: { "x-correlation-id": "gw-corr" },
      requestContext: { requestId: "gw-req" },
    };
    expect(extractCorrelationFromApiGateway(event)).toBe("gw-corr");
  });

  it("should fall back to requestContext.requestId", () => {
    const event = {
      headers: {},
      requestContext: { requestId: "gw-req" },
    };
    expect(extractCorrelationFromApiGateway(event)).toBe("gw-req");
  });
});