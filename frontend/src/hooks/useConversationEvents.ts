import { useEffect, useRef, useState } from "react";

import { apiUrl } from "../lib/api";
import type { ConnectionState, EventEnvelope } from "../types";

interface UseConversationEventsResult {
  connectionState: ConnectionState;
  highestContiguousSequence: number;
}

interface SseFrame {
  id: string | null;
  data: string;
}

function parseFrame(frame: string): SseFrame | null {
  let id: string | null = null;
  const data: string[] = [];

  for (const line of frame.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    let value = separator === -1 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "id") id = value;
    if (field === "data") data.push(value);
  }

  return data.length > 0 ? { id, data: data.join("\n") } : null;
}

function reconnectDelay(attempt: number): number {
  return Math.min(8000, 750 * 2 ** Math.min(attempt, 4));
}

export function useConversationEvents(
  conversationId: string | null,
  onEvent: (event: EventEnvelope) => void,
): UseConversationEventsResult {
  const [connectionState, setConnectionState] = useState<ConnectionState>("idle");
  const [highestContiguousSequence, setHighestContiguousSequence] = useState(0);
  const onEventRef = useRef(onEvent);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    if (!conversationId) {
      setConnectionState("idle");
      setHighestContiguousSequence(0);
      return;
    }

    const abortController = new AbortController();
    const seenEventIds = new Set<string>();
    const receivedSequences = new Set<number>();
    let contiguousSequence = 0;
    let lastEventId: string | null = null;
    let reconnectAttempt = 0;
    setConnectionState("connecting");

    const handleFrame = (frame: SseFrame) => {
      let parsed: EventEnvelope;
      try {
        parsed = JSON.parse(frame.data) as EventEnvelope;
      } catch {
        return;
      }
      if (!parsed.event_id || seenEventIds.has(parsed.event_id)) return;

      seenEventIds.add(parsed.event_id);
      lastEventId = frame.id ?? parsed.event_id;
      receivedSequences.add(parsed.sequence);
      const hadGap = parsed.sequence > contiguousSequence + 1;
      while (receivedSequences.has(contiguousSequence + 1)) {
        contiguousSequence += 1;
      }
      setHighestContiguousSequence(contiguousSequence);
      setConnectionState(hadGap ? "recovering" : "connected");
      onEventRef.current(parsed);
    };

    const waitToReconnect = async () => {
      await new Promise<void>((resolve) => {
        const timeout = window.setTimeout(resolve, reconnectDelay(reconnectAttempt));
        abortController.signal.addEventListener(
          "abort",
          () => {
            window.clearTimeout(timeout);
            resolve();
          },
          { once: true },
        );
      });
    };

    const connect = async () => {
      while (!abortController.signal.aborted) {
        try {
          const response = await fetch(
            apiUrl(`/v1/conversations/${encodeURIComponent(conversationId)}/events`),
            {
              headers: {
                Accept: "text/event-stream",
                ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
              },
              cache: "no-store",
              signal: abortController.signal,
            },
          );

          if (!response.ok || !response.body) {
            throw new Error(`SSE connection failed with HTTP ${response.status}`);
          }

          reconnectAttempt = 0;
          setConnectionState("connected");
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";

          while (!abortController.signal.aborted) {
            const { done, value } = await reader.read();
            buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
            let boundary = buffer.indexOf("\n\n");
            while (boundary !== -1) {
              const frame = parseFrame(buffer.slice(0, boundary));
              buffer = buffer.slice(boundary + 2);
              if (frame) handleFrame(frame);
              boundary = buffer.indexOf("\n\n");
            }
            if (done) break;
          }
        } catch (error) {
          if (abortController.signal.aborted) break;
          if (error instanceof DOMException && error.name === "AbortError") break;
        }

        if (abortController.signal.aborted) break;
        setConnectionState(lastEventId ? "recovering" : "connecting");
        reconnectAttempt += 1;
        await waitToReconnect();
      }
    };

    void connect();

    return () => {
      abortController.abort();
    };
  }, [conversationId]);

  return { connectionState, highestContiguousSequence };
}
