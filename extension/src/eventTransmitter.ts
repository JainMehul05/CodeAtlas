import * as https from "https";
import * as http from "http";
import { log } from "./logger";
import {
  FlowSyncEvent,
  EventType,
  EventSource,
  PushEventPayload,
  createEvent,
  validateEvent,
  isPushPayload,
} from "@flowsync/shared";

export type CapturedEvent = FlowSyncEvent;

export type PushPayload = PushEventPayload;

export function createPushEvent(
  projectId: string,
  actor: { id: string; name: string; email?: string; avatarUrl?: string },
  payload: PushEventPayload,
  options?: {
    branch?: string;
    correlationId?: string;
  }
): FlowSyncEvent {
  return createEvent(
    EventType.PUSH,
    EventSource.VSCODE,
    projectId,
    actor,
    payload,
    {
      correlationId: options?.correlationId,
    }
  );
}

export async function transmitEvent(
  backendUrl: string,
  apiToken: string,
  event: FlowSyncEvent
): Promise<Record<string, unknown>> {
  const validation = validateEvent(event);
  if (!validation.success) {
    throw new Error(`Event validation failed: ${validation.errors?.map((e) => e.message).join(", ")}`);
  }

  const retryDelays = [0, 1000, 2000, 4000];

  // Extract branch from payload for push events
  const branch = isPushPayload(event.payload) ? event.payload.branch : undefined;

  for (let attempt = 0; attempt < retryDelays.length; attempt++) {
    if (retryDelays[attempt] > 0) {
      log.info("transmitEvent", `waiting ${retryDelays[attempt]}ms before retry attempt ${attempt + 1}`);
      await sleep(retryDelays[attempt]);
    }

    const commitHash = isPushPayload(event.payload) ? event.payload.commitHash : undefined;
    const author = isPushPayload(event.payload) ? event.payload.author : undefined;
    
    log.step("transmitEvent", `attempt ${attempt + 1}/${retryDelays.length} → POST ${backendUrl}/api/v1/events`);
    log.info("transmitEvent", `payload summary: eventId=${event.eventId} projectId=${event.projectId} branch=${branch ?? 'N/A'} commitHash=${commitHash?.slice(0, 8)} author="${author ?? 'unknown'}"`);

    try {
      const result = await postJson(
        `${backendUrl}/api/v1/events`,
        apiToken,
        event as unknown as Record<string, unknown>
      );
      log.ok("transmitEvent", `HTTP 2xx — response: ${JSON.stringify(result)}`);
      return result;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (attempt === retryDelays.length - 1) {
        log.error("transmitEvent", `all retries exhausted — last error: ${msg}`);
        throw err;
      }
      log.warn("transmitEvent", `attempt ${attempt + 1} failed: ${msg} — will retry`);
    }
  }

  throw new Error("FlowSync: transmit failed after all retries");
}

function postJson(
  url: string,
  token: string,
  body: Record<string, unknown>
): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const parsedUrl = new URL(url);
    const transport = parsedUrl.protocol === "https:" ? https : http;

    const req = transport.request(
      {
        hostname: parsedUrl.hostname,
        port: parsedUrl.port,
        path: parsedUrl.pathname,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(data),
          Authorization: `Bearer ${token}`,
        },
      },
      (res) => {
        let responseBody = "";
        res.on("data", (chunk: Buffer) => { responseBody += chunk.toString(); });
        res.on("end", () => {
          log.info("postJson", `response: HTTP ${res.statusCode} — ${responseBody.slice(0, 300)}`);
          if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
            try { resolve(JSON.parse(responseBody)); }
            catch { resolve({}); }
          } else {
            reject(new Error(`HTTP ${res.statusCode}: ${responseBody}`));
          }
        });
      }
    );

    req.on("error", (err) => {
      log.error("postJson", `network error: ${err.message}`);
      reject(err);
    });
    req.write(data);
    req.end();
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}