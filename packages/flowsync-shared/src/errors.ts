/**
 * FlowSync Error Model
 * Standardized error codes and HTTP status mappings
 */

export enum ErrorCode {
  // Validation errors
  VALIDATION_ERROR = "VALIDATION_ERROR",
  INVALID_SCHEMA = "INVALID_SCHEMA",
  MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD",
  INVALID_FIELD_FORMAT = "INVALID_FIELD_FORMAT",

  // Authentication errors
  AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR",
  INVALID_TOKEN = "INVALID_TOKEN",
  TOKEN_EXPIRED = "TOKEN_EXPIRED",
  MISSING_AUTH_HEADER = "MISSING_AUTH_HEADER",
  MALFORMED_AUTH_HEADER = "MALFORMED_AUTH_HEADER",

  // Authorization errors
  AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR",
  INSUFFICIENT_PERMISSIONS = "INSUFFICIENT_PERMISSIONS",
  PROJECT_ACCESS_DENIED = "PROJECT_ACCESS_DENIED",

  // Not found errors
  NOT_FOUND = "NOT_FOUND",
  PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND",
  EVENT_NOT_FOUND = "EVENT_NOT_FOUND",
  CONTEXT_NOT_FOUND = "CONTEXT_NOT_FOUND",

  // Conflict errors
  CONFLICT = "CONFLICT",
  DUPLICATE_EVENT = "DUPLICATE_EVENT",
  DUPLICATE_PROJECT = "DUPLICATE_PROJECT",

  // Rate limiting
  RATE_LIMITED = "RATE_LIMITED",
  TOO_MANY_REQUESTS = "TOO_MANY_REQUESTS",

  // External service errors
  EXTERNAL_SERVICE_ERROR = "EXTERNAL_SERVICE_ERROR",
  BEDROCK_ERROR = "BEDROCK_ERROR",
  BEDROCK_THROTTLED = "BEDROCK_THROTTLED",
  DYNAMODB_ERROR = "DYNAMODB_ERROR",
  S3_ERROR = "S3_ERROR",

  // Internal errors
  INTERNAL_ERROR = "INTERNAL_ERROR",
  CONFIGURATION_ERROR = "CONFIGURATION_ERROR",
  SERIALIZATION_ERROR = "SERIALIZATION_ERROR",
}

/**
 * Maps error codes to HTTP status codes
 */
export const ERROR_STATUS_MAP: Record<ErrorCode, number> = {
  [ErrorCode.VALIDATION_ERROR]: 400,
  [ErrorCode.INVALID_SCHEMA]: 400,
  [ErrorCode.MISSING_REQUIRED_FIELD]: 400,
  [ErrorCode.INVALID_FIELD_FORMAT]: 400,

  [ErrorCode.AUTHENTICATION_ERROR]: 401,
  [ErrorCode.INVALID_TOKEN]: 401,
  [ErrorCode.TOKEN_EXPIRED]: 401,
  [ErrorCode.MISSING_AUTH_HEADER]: 401,
  [ErrorCode.MALFORMED_AUTH_HEADER]: 401,

  [ErrorCode.AUTHORIZATION_ERROR]: 403,
  [ErrorCode.INSUFFICIENT_PERMISSIONS]: 403,
  [ErrorCode.PROJECT_ACCESS_DENIED]: 403,

  [ErrorCode.NOT_FOUND]: 404,
  [ErrorCode.PROJECT_NOT_FOUND]: 404,
  [ErrorCode.EVENT_NOT_FOUND]: 404,
  [ErrorCode.CONTEXT_NOT_FOUND]: 404,

  [ErrorCode.CONFLICT]: 409,
  [ErrorCode.DUPLICATE_EVENT]: 409,
  [ErrorCode.DUPLICATE_PROJECT]: 409,

  [ErrorCode.RATE_LIMITED]: 429,
  [ErrorCode.TOO_MANY_REQUESTS]: 429,

  [ErrorCode.EXTERNAL_SERVICE_ERROR]: 502,
  [ErrorCode.BEDROCK_ERROR]: 502,
  [ErrorCode.BEDROCK_THROTTLED]: 503,
  [ErrorCode.DYNAMODB_ERROR]: 500,
  [ErrorCode.S3_ERROR]: 500,

  [ErrorCode.INTERNAL_ERROR]: 500,
  [ErrorCode.CONFIGURATION_ERROR]: 500,
  [ErrorCode.SERIALIZATION_ERROR]: 500,
};

/**
 * FlowSync Application Error
 * Base error class with structured error information
 */
export class FlowSyncError extends Error {
  public readonly code: ErrorCode;
  public readonly statusCode: number;
  public readonly correlationId?: string;
  public readonly details?: Record<string, unknown>;
  public readonly isOperational: boolean;

  constructor(
    code: ErrorCode,
    message: string,
    options?: {
      correlationId?: string;
      details?: Record<string, unknown>;
      isOperational?: boolean;
      cause?: Error;
    }
  ) {
    super(message);
    this.name = "FlowSyncError";
    this.code = code;
    this.statusCode = ERROR_STATUS_MAP[code] ?? 500;
    this.correlationId = options?.correlationId;
    this.details = options?.details;
    this.isOperational = options?.isOperational ?? true;

    // Maintains proper stack trace in V8 environments
    const captureStackTrace = (Error as any).captureStackTrace;
    if (captureStackTrace) {
      captureStackTrace(this, FlowSyncError);
    }

    // Set cause for error chaining
    if (options?.cause) {
      this.cause = options.cause;
    }
  }

  /**
   * Converts error to a safe response object (no stack traces, no sensitive data)
   */
  toResponse(): ErrorResponse {
    const isDevelopment = typeof process !== "undefined" && process.env?.NODE_ENV === "development";
    return {
      error: this.code,
      message: this.message,
      correlationId: this.correlationId,
      // Only include details in development
      ...(isDevelopment && this.details ? { details: this.details } : {}),
    };
  }

  /**
   * Creates a validation error
   */
  static validation(message: string, details?: Record<string, unknown>, correlationId?: string): FlowSyncError {
    return new FlowSyncError(ErrorCode.VALIDATION_ERROR, message, { correlationId, details });
  }

  /**
   * Creates an authentication error
   */
  static authentication(message: string, correlationId?: string): FlowSyncError {
    return new FlowSyncError(ErrorCode.AUTHENTICATION_ERROR, message, { correlationId });
  }

  /**
   * Creates an authorization error
   */
  static authorization(message: string, correlationId?: string): FlowSyncError {
    return new FlowSyncError(ErrorCode.AUTHORIZATION_ERROR, message, { correlationId });
  }

  /**
   * Creates a not found error
   */
  static notFound(resource: string, correlationId?: string): FlowSyncError {
    return new FlowSyncError(ErrorCode.NOT_FOUND, `${resource} not found`, { correlationId });
  }

  /**
   * Creates a conflict error
   */
  static conflict(message: string, correlationId?: string): FlowSyncError {
    return new FlowSyncError(ErrorCode.CONFLICT, message, { correlationId });
  }

  /**
   * Creates an internal error (logs full details, returns safe message)
   */
  static internal(message: string, cause?: Error, correlationId?: string): FlowSyncError {
    return new FlowSyncError(ErrorCode.INTERNAL_ERROR, "An internal error occurred", {
      correlationId,
      details: { internalMessage: message },
      isOperational: false,
      cause,
    });
  }

  /**
   * Creates an external service error
   */
  static externalService(service: string, message: string, correlationId?: string): FlowSyncError {
    return new FlowSyncError(ErrorCode.EXTERNAL_SERVICE_ERROR, `${service}: ${message}`, {
      correlationId,
      details: { service },
    });
  }
}

/**
 * Safe error response for API responses
 */
export interface ErrorResponse {
  error: string;
  message: string;
  correlationId?: string;
  details?: Record<string, unknown>;
}

/**
 * Checks if an error is a FlowSyncError
 */
export function isFlowSyncError(error: unknown): error is FlowSyncError {
  return error instanceof FlowSyncError;
}

/**
 * Converts any error to a FlowSyncError
 */
export function toFlowSyncError(error: unknown, correlationId?: string): FlowSyncError {
  if (isFlowSyncError(error)) {
    // Preserve correlation ID if not already set
    if (correlationId && !error.correlationId) {
      return new FlowSyncError(error.code, error.message, {
        correlationId,
        details: error.details,
        cause: error.cause instanceof Error ? error.cause : undefined,
      });
    }
    return error;
  }

  if (error instanceof Error) {
    // Check for known error patterns
    if (error.name === "ValidationError") {
      return FlowSyncError.validation(error.message, undefined, correlationId);
    }
    if (error.name === "UnauthorizedError" || error.message.includes("401")) {
      return FlowSyncError.authentication(error.message, correlationId);
    }
    if (error.message.includes("404")) {
      return FlowSyncError.notFound("Resource", correlationId);
    }
    if (error.message.includes("409") || error.message.includes("conflict")) {
      return FlowSyncError.conflict(error.message, correlationId);
    }
    if (error.message.includes("429") || error.message.includes("rate limit")) {
      return new FlowSyncError(ErrorCode.RATE_LIMITED, "Rate limited", { correlationId });
    }
    if (error.message.includes("Bedrock") || error.message.includes("throttle")) {
      return new FlowSyncError(ErrorCode.BEDROCK_THROTTLED, "AI service temporarily unavailable", { correlationId });
    }
    if (error.message.includes("DynamoDB") || error.message.includes("dynamodb")) {
      return new FlowSyncError(ErrorCode.DYNAMODB_ERROR, "Database error", { correlationId });
    }
    if (error.message.includes("S3") || error.message.includes("s3")) {
      return new FlowSyncError(ErrorCode.S3_ERROR, "Storage error", { correlationId });
    }

    return FlowSyncError.internal(error.message, error, correlationId);
  }

  return FlowSyncError.internal(String(error), undefined, correlationId);
}