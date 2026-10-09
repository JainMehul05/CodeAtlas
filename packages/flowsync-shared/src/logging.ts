/**
 * FlowSync Structured Logging
 * Consistent, structured logging with correlation ID support
 */

import { getCorrelationId } from "./correlation";

export enum LogLevel {
  DEBUG = 0,
  INFO = 1,
  WARN = 2,
  ERROR = 3,
}

export interface LogEntry {
  timestamp: string;
  level: LogLevel;
  levelName: string;
  service: string;
  function?: string;
  correlationId?: string;
  eventId?: string;
  projectId?: string;
  eventType?: string;
  message: string;
  metadata?: Record<string, unknown>;
  error?: {
    name: string;
    message: string;
    stack?: string;
  };
}

const LEVEL_NAMES: Record<LogLevel, string> = {
  [LogLevel.DEBUG]: "DEBUG",
  [LogLevel.INFO]: "INFO",
  [LogLevel.WARN]: "WARN",
  [LogLevel.ERROR]: "ERROR",
};

const SENSITIVE_FIELDS = [
  "token",
  "password",
  "secret",
  "apikey",
  "api_key",
  "authorization",
  "bearer",
  "credential",
  "privatekey",
  "private_key",
];

/**
 * Sanitizes an object by removing sensitive fields
 */
export function sanitizeForLogging(obj: Record<string, unknown>): Record<string, unknown> {
  const sanitized: Record<string, unknown> = {};

  for (const [key, value] of Object.entries(obj)) {
    const lowerKey = key.toLowerCase();
    const isSensitive = SENSITIVE_FIELDS.some((field) => lowerKey.includes(field));

    if (isSensitive) {
      sanitized[key] = "[REDACTED]";
    } else if (typeof value === "object" && value !== null && !Array.isArray(value)) {
      sanitized[key] = sanitizeForLogging(value as Record<string, unknown>);
    } else if (Array.isArray(value)) {
      sanitized[key] = value.map((item) =>
        typeof item === "object" && item !== null
          ? sanitizeForLogging(item as Record<string, unknown>)
          : item
      );
    } else {
      sanitized[key] = value;
    }
  }

  return sanitized;
}

/**
 * Creates a base log entry with common fields
 */
function createLogEntry(
  level: LogLevel,
  service: string,
  message: string,
  metadata?: Record<string, unknown>,
  error?: Error
): LogEntry {
  const entry: LogEntry = {
    timestamp: new Date().toISOString(),
    level,
    levelName: LEVEL_NAMES[level],
    service,
    correlationId: getCorrelationId(),
    message,
    metadata: metadata ? sanitizeForLogging(metadata) : undefined,
  };

  if (error) {
    entry.error = {
      name: error.name,
      message: error.message,
      stack: error.stack,
    };
  }

  return entry;
}

/**
 * Logger interface
 */
export interface Logger {
  debug(message: string, metadata?: Record<string, unknown>): void;
  info(message: string, metadata?: Record<string, unknown>): void;
  warn(message: string, metadata?: Record<string, unknown>): void;
  error(message: string, metadata?: Record<string, unknown>, error?: Error): void;

  // Convenience methods with common fields
  event(eventId: string, eventType: string, projectId: string, message: string, metadata?: Record<string, unknown>): void;
  project(projectId: string, message: string, metadata?: Record<string, unknown>): void;
  api(functionName: string, message: string, metadata?: Record<string, unknown>): void;
}

/**
 * Creates a logger for a specific service
 */
export function createLogger(service: string, functionName?: string): Logger {
  const baseMetadata = functionName ? { function: functionName } : {};

  const log = (level: LogLevel, message: string, metadata?: Record<string, unknown>, error?: Error) => {
    const entry = createLogEntry(level, service, message, metadata, error);
    entry.function = functionName;

    // Add base metadata
    if (Object.keys(baseMetadata).length > 0) {
      entry.metadata = { ...baseMetadata, ...entry.metadata };
    }

    // Output as JSON for structured logging
    console.log(JSON.stringify(entry));
  };

  return {
    debug: (message: string, metadata?: Record<string, unknown>) => log(LogLevel.DEBUG, message, metadata),
    info: (message: string, metadata?: Record<string, unknown>) => log(LogLevel.INFO, message, metadata),
    warn: (message: string, metadata?: Record<string, unknown>) => log(LogLevel.WARN, message, metadata),
    error: (message: string, metadata?: Record<string, unknown>, error?: Error) =>
      log(LogLevel.ERROR, message, metadata, error),

    event: (eventId: string, eventType: string, projectId: string, message: string, metadata?: Record<string, unknown>) =>
      log(LogLevel.INFO, message, { eventId, eventType, projectId, ...metadata }),

    project: (projectId: string, message: string, metadata?: Record<string, unknown>) =>
      log(LogLevel.INFO, message, { projectId, ...metadata }),

    api: (fn: string, message: string, metadata?: Record<string, unknown>) =>
      log(LogLevel.INFO, message, { function: fn, ...metadata }),
  };
}

/**
 * Default logger instance (for backwards compatibility)
 */
export const defaultLogger = createLogger("flowsync");

/**
 * Logs an API request
 */
export function logRequest(
  logger: Logger,
  method: string,
  path: string,
  correlationId: string,
  metadata?: Record<string, unknown>
): void {
  logger.info(`${method} ${path}`, { method, path, correlationId, ...metadata });
}

/**
 * Logs an API response
 */
export function logResponse(
  logger: Logger,
  statusCode: number,
  durationMs: number,
  correlationId: string,
  metadata?: Record<string, unknown>
): void {
  const level = statusCode >= 400 ? LogLevel.WARN : LogLevel.INFO;
  logger[level === LogLevel.WARN ? "warn" : "info"](`${statusCode} ${durationMs}ms`, {
    statusCode,
    durationMs,
    correlationId,
    ...metadata,
  });
}

/**
 * Logs an error with full context
 */
export function logError(
  logger: Logger,
  error: Error,
  message: string,
  metadata?: Record<string, unknown>
): void {
  logger.error(message, metadata, error);
}