/**
 * FlowSync Correlation ID Utilities
 * Request tracing and correlation ID propagation
 */

import { randomUUID } from "crypto";

/**
 * Header names for correlation ID propagation
 */
export const CORRELATION_HEADER = "x-correlation-id";
export const REQUEST_ID_HEADER = "x-request-id";

/**
 * Generates a new correlation ID (UUID v4)
 */
export function generateCorrelationId(): string {
  return randomUUID();
}

/**
 * Extracts correlation ID from headers, generates one if not present
 */
export function getOrCreateCorrelationId(headers: Record<string, string | undefined>): string {
  // Check standard headers (case-insensitive)
  const headerKeys = Object.keys(headers).map((k) => k.toLowerCase());
  const correlationIndex = headerKeys.indexOf(CORRELATION_HEADER.toLowerCase());
  const requestIdIndex = headerKeys.indexOf(REQUEST_ID_HEADER.toLowerCase());

  if (correlationIndex !== -1) {
    const originalKey = Object.keys(headers)[correlationIndex];
    return headers[originalKey] || generateCorrelationId();
  }

  if (requestIdIndex !== -1) {
    const originalKey = Object.keys(headers)[requestIdIndex];
    return headers[originalKey] || generateCorrelationId();
  }

  return generateCorrelationId();
}

/**
 * Adds correlation ID to headers
 */
export function withCorrelationId(
  headers: Record<string, string>,
  correlationId: string
): Record<string, string> {
  return {
    ...headers,
    [CORRELATION_HEADER]: correlationId,
    [REQUEST_ID_HEADER]: correlationId,
  };
}

/**
 * Correlation context for async operations
 */
interface CorrelationContext {
  correlationId: string;
  startTime: number;
  metadata: Record<string, unknown>;
}

// Async local storage for correlation context (Node.js 14+)
let correlationContext: CorrelationContext | null = null;

/**
 * Runs a function with a correlation context
 */
export function runWithCorrelation<T>(
  correlationId: string,
  fn: () => T | Promise<T>,
  metadata?: Record<string, unknown>
): T | Promise<T> {
  const previousContext = correlationContext;
  correlationContext = {
    correlationId,
    startTime: Date.now(),
    metadata: metadata || {},
  };

  try {
    const result = fn();
    if (result instanceof Promise) {
      return result.finally(() => {
        correlationContext = previousContext;
      });
    }
    correlationContext = previousContext;
    return result;
  } catch (error) {
    correlationContext = previousContext;
    throw error;
  }
}

/**
 * Gets the current correlation ID from context
 */
export function getCorrelationId(): string | undefined {
  return correlationContext?.correlationId;
}

/**
 * Gets the current correlation context
 */
export function getCorrelationContext(): CorrelationContext | null {
  return correlationContext;
}

/**
 * Adds metadata to the current correlation context
 */
export function addCorrelationMetadata(key: string, value: unknown): void {
  if (correlationContext) {
    correlationContext.metadata[key] = value;
  }
}

/**
 * Creates a child correlation context (for sub-operations)
 */
export function createChildCorrelation(
  parentCorrelationId: string,
  operation: string
): string {
  // Create a deterministic child ID based on parent + operation
  // This maintains traceability while allowing distinct IDs for sub-operations
  const childSuffix = Buffer.from(`${parentCorrelationId}:${operation}:${Date.now()}`)
    .toString("base64url")
    .slice(0, 12);
  return `${parentCorrelationId}-${childSuffix}`;
}

// Ensure Buffer is available (Node.js global)
declare const Buffer: {
  from(str: string, encoding?: string): Buffer;
  new(str: string, encoding?: string): Buffer;
};
interface Buffer {
  toString(encoding?: string): string;
  slice(start?: number, end?: number): Buffer;
}

/**
 * Extracts correlation ID from API Gateway event
 */
export function extractCorrelationFromApiGateway(event: {
  headers?: Record<string, string | undefined>;
  requestContext?: { requestId?: string };
}): string {
  // Check headers first
  if (event.headers) {
    const headerKeys = Object.keys(event.headers).map(k => k.toLowerCase());
    const corrIdx = headerKeys.indexOf(CORRELATION_HEADER.toLowerCase());
    const reqIdx = headerKeys.indexOf(REQUEST_ID_HEADER.toLowerCase());
    const originalKeys = Object.keys(event.headers);
    
    if (corrIdx !== -1 && event.headers[originalKeys[corrIdx]]) {
      return event.headers[originalKeys[corrIdx]]!;
    }
    if (reqIdx !== -1 && event.headers[originalKeys[reqIdx]]) {
      return event.headers[originalKeys[reqIdx]]!;
    }
  }

  // Fall back to API Gateway request ID
  if (event.requestContext?.requestId) {
    return event.requestContext.requestId;
  }

  return generateCorrelationId();
}

/**
 * Lambda-compatible correlation ID extraction
 */
export function extractCorrelationFromLambdaEvent(event: {
  headers?: Record<string, string | undefined>;
  requestContext?: { requestId?: string };
}): string {
  return extractCorrelationFromApiGateway(event);
}