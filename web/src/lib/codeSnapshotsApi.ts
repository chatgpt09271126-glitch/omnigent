import { authenticatedFetch } from "./identity";
import { apiErrorFromResponse } from "./sessionsApi";

export type SnapshotCaptureType =
  "region_capture" | "mobile_quick_capture" | "uploaded_image" | "clipboard_image";

export interface CodeSnapshot {
  id: string;
  conversation_id: string;
  response_id: string;
  item_id: string;
  code_block_start_offset: number;
  language: string | null;
  created_by: string | null;
  created_at: number;
  capture_type: SnapshotCaptureType;
  content_type: string;
  bytes: number;
  content_url: string;
}

export interface CodeSnapshotOrigin {
  conversationId: string;
  responseId: string;
  itemId: string;
  codeBlockStartOffset: number;
  language?: string | null;
  canEdit: boolean;
}

export function codeSnapshotsQueryKey(origin: CodeSnapshotOrigin) {
  return [
    "code-snapshots",
    origin.conversationId,
    origin.responseId,
    origin.itemId,
    origin.codeBlockStartOffset,
  ] as const;
}

export async function fetchCodeSnapshots(origin: CodeSnapshotOrigin): Promise<CodeSnapshot[]> {
  const params = new URLSearchParams({
    response_id: origin.responseId,
    item_id: origin.itemId,
    code_block_start_offset: String(origin.codeBlockStartOffset),
  });
  const response = await authenticatedFetch(
    `/v1/sessions/${encodeURIComponent(origin.conversationId)}/code-snapshots?${params}`,
  );
  if (!response.ok) throw await apiErrorFromResponse(response);
  const payload = (await response.json()) as { data: CodeSnapshot[] };
  return payload.data;
}

export async function createCodeSnapshot(
  origin: CodeSnapshotOrigin,
  file: File | Blob,
  captureType: SnapshotCaptureType,
): Promise<CodeSnapshot> {
  const form = new FormData();
  form.append("file", file, file instanceof File && file.name ? file.name : "snapshot.png");
  form.append("response_id", origin.responseId);
  form.append("item_id", origin.itemId);
  form.append("code_block_start_offset", String(origin.codeBlockStartOffset));
  form.append("capture_type", captureType);
  if (origin.language) form.append("language", origin.language);
  const response = await authenticatedFetch(
    `/v1/sessions/${encodeURIComponent(origin.conversationId)}/code-snapshots`,
    { method: "POST", body: form },
  );
  if (!response.ok) throw await apiErrorFromResponse(response);
  return (await response.json()) as CodeSnapshot;
}

export async function deleteCodeSnapshot(
  conversationId: string,
  snapshotId: string,
): Promise<void> {
  const response = await authenticatedFetch(
    `/v1/sessions/${encodeURIComponent(conversationId)}/code-snapshots/${encodeURIComponent(snapshotId)}`,
    { method: "DELETE" },
  );
  if (!response.ok) throw await apiErrorFromResponse(response);
}
