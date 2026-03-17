export interface StoryOption {
  story_code: string;
  title: string;
  opening_scene: string;
}

export interface RuleOption {
  rule_code: string;
  stories: StoryOption[];
}

export interface CatalogResponse {
  rules: RuleOption[];
}

export interface SessionStateSummary {
  turn_id: number;
  game_time: {
    day: number;
    hour: number;
    minute: number;
  };
  player: {
    name: string;
    status: string[];
    inventory: string[];
    known_clues: string[];
    short_term_goals: string[];
    relationship_notes: string[];
  };
  scene: {
    location: string;
    description: string;
    visible_npcs: string[];
    interactive_objects: string[];
    hazards: string[];
  };
  recent_events: string[];
  scenario: {
    title: string;
    brief: string;
    opening_scene: string;
  };
  rule_family: string;
}

export interface SessionResponse {
  session_id: string;
  rule_code: string;
  story_code: string;
  player_name: string;
  max_turns: number;
  turns_used: number;
  turns_remaining: number;
  is_finished: boolean;
  opening: string;
  state: SessionStateSummary;
}

export interface TurnResponse {
  session: SessionResponse;
  turn: {
    player_text: string;
    narration: string;
    action: {
      raw_text: string;
      intent: string;
      target?: string | null;
      approach?: string | null;
      tags: string[];
    };
    turn_id: number;
  };
}

export interface StreamTurnChunkEvent {
  event: "narration_chunk";
  delta: string;
}

export interface StreamTurnStartEvent {
  event: "turn_start";
  player_text: string;
  session_id: string;
}

export interface StreamTurnResultEvent {
  event: "turn_result";
  session: SessionResponse;
  turn: TurnResponse["turn"];
}

export interface StreamTurnErrorEvent {
  event: "error";
  error: string;
}

export type StreamTurnEvent = StreamTurnStartEvent | StreamTurnChunkEvent | StreamTurnResultEvent | StreamTurnErrorEvent;

export interface StreamSessionLogEvent {
  event: "runtime_log";
  phase: string;
  stage: string;
  message: string;
}

export interface StreamSessionReadyEvent {
  event: "session_ready";
  session: SessionResponse;
}

export type StreamSessionEvent = StreamSessionLogEvent | StreamSessionReadyEvent | StreamTurnErrorEvent;

export interface CreateSessionRequest {
  rule_code: string;
  story_code: string;
  player_name: string;
  max_turns: number;
}

const baseEndpoint = (import.meta.env.VITE_TRPG_ENDPOINT ?? "http://127.0.0.1:8788").replace(/\/+$/, "");

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseEndpoint}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  const data = (await response.json().catch(() => ({}))) as { error?: string };
  if (!response.ok) {
    throw new Error(data.error ?? `HTTP ${response.status}`);
  }
  return data as T;
}

export function fetchCatalog(): Promise<CatalogResponse> {
  return requestJson<CatalogResponse>("/api/trpg/catalog");
}

export function createSession(payload: CreateSessionRequest): Promise<SessionResponse> {
  return requestJson<SessionResponse>("/api/trpg/session", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function streamCreateSession(
  payload: CreateSessionRequest,
  onEvent: (event: StreamSessionEvent) => void
): Promise<void> {
  const response = await fetch(`${baseEndpoint}/api/trpg/session/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const data = (await response.json().catch(() => ({}))) as { error?: string };
    throw new Error(data.error ?? `HTTP ${response.status}`);
  }

  if (!response.body) {
    throw new Error("Streaming response body is unavailable");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let lineBreak = buffer.indexOf("\n");
      while (lineBreak >= 0) {
        const line = buffer.slice(0, lineBreak).trim();
        buffer = buffer.slice(lineBreak + 1);
        if (line) {
          onEvent(JSON.parse(line) as StreamSessionEvent);
        }
        lineBreak = buffer.indexOf("\n");
      }
    }

    const tail = buffer.trim();
    if (tail) {
      onEvent(JSON.parse(tail) as StreamSessionEvent);
    }
  } finally {
    reader.releaseLock();
  }
}

export function runTurn(sessionId: string, playerText: string): Promise<TurnResponse> {
  return requestJson<TurnResponse>(`/api/trpg/session/${sessionId}/turn`, {
    method: "POST",
    body: JSON.stringify({ player_text: playerText }),
  });
}

export async function streamTurn(
  sessionId: string,
  playerText: string,
  onEvent: (event: StreamTurnEvent) => void
): Promise<void> {
  const response = await fetch(`${baseEndpoint}/api/trpg/session/${sessionId}/turn/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ player_text: playerText }),
  });

  if (!response.ok) {
    const data = (await response.json().catch(() => ({}))) as { error?: string };
    throw new Error(data.error ?? `HTTP ${response.status}`);
  }

  if (!response.body) {
    throw new Error("Streaming response body is unavailable");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let lineBreak = buffer.indexOf("\n");
      while (lineBreak >= 0) {
        const line = buffer.slice(0, lineBreak).trim();
        buffer = buffer.slice(lineBreak + 1);
        if (line) {
          onEvent(JSON.parse(line) as StreamTurnEvent);
        }
        lineBreak = buffer.indexOf("\n");
      }
    }

    const tail = buffer.trim();
    if (tail) {
      onEvent(JSON.parse(tail) as StreamTurnEvent);
    }
  } finally {
    reader.releaseLock();
  }
}

export function deleteSession(sessionId: string): Promise<{ ok: boolean; session_id: string }> {
  return requestJson<{ ok: boolean; session_id: string }>(`/api/trpg/session/${sessionId}`, {
    method: "DELETE",
  });
}
