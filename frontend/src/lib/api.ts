import type {
  ApiErrorBody,
  ConversationCreated,
  ConversationRead,
  DemoCatalogView,
  EvalReport,
  ProposalTransitionAccepted,
  RunAccepted,
} from "../types";
import { ApiClientError } from "../types";

const configuredBase = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export function apiUrl(path: string): string {
  return `${configuredBase}${path}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let body: ApiErrorBody;
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      body = {
        error: {
          code: "UNEXPECTED_RESPONSE",
          message: `服务返回了无法识别的错误（HTTP ${response.status}）。`,
          retryable: response.status >= 500,
          trace_id: "unavailable",
        },
      };
    }
    throw new ApiClientError(response.status, body);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function createConversation(fixtureCustomerKey: string): Promise<ConversationCreated> {
  return request<ConversationCreated>("/v1/conversations", {
    method: "POST",
    body: JSON.stringify({ fixture_customer_key: fixtureCustomerKey }),
  });
}

export function getConversation(conversationId: string): Promise<ConversationRead> {
  return request<ConversationRead>(
    `/v1/conversations/${encodeURIComponent(conversationId)}`,
  );
}

export function sendCustomerMessage(
  conversationId: string,
  content: string,
): Promise<RunAccepted> {
  return request<RunAccepted>(`/v1/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export function confirmProposal(
  proposalId: string,
  proposalVersion: number,
): Promise<ProposalTransitionAccepted> {
  return request<ProposalTransitionAccepted>(
    `/v1/action-proposals/${encodeURIComponent(proposalId)}/confirm`,
    {
      method: "POST",
      body: JSON.stringify({ proposal_version: proposalVersion }),
    },
  );
}

export function declineProposal(
  proposalId: string,
  proposalVersion: number,
): Promise<ProposalTransitionAccepted> {
  return request<ProposalTransitionAccepted>(
    `/v1/action-proposals/${encodeURIComponent(proposalId)}/decline`,
    {
      method: "POST",
      body: JSON.stringify({ proposal_version: proposalVersion }),
    },
  );
}

export function retryCase(caseId: string): Promise<RunAccepted> {
  return request<RunAccepted>(`/v1/investigation-cases/${encodeURIComponent(caseId)}/retry`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function resetSyntheticDemo(): Promise<void> {
  return request<void>("/v1/demo/reset", { method: "POST" });
}

export function getDemoCatalog(): Promise<DemoCatalogView> {
  return request<DemoCatalogView>("/v1/demo/catalog");
}

export function getLatestEval(): Promise<EvalReport> {
  return request<EvalReport>("/v1/evals/latest");
}
