"use client";

import localforage from "localforage";

import type { ImageModel } from "@/lib/api";
import { httpRequest } from "@/lib/request";
import { getStoredAuthSession } from "@/store/auth";

export type ImageConversationMode = "generate" | "edit";

export type StoredReferenceImage = {
  name: string;
  type: string;
  dataUrl: string;
};

export type StoredImage = {
  id: string;
  taskId?: string;
  status?: "loading" | "success" | "error";
  b64_json?: string;
  url?: string;
  revised_prompt?: string;
  error?: string;
};

export type ImageTurnStatus = "queued" | "generating" | "success" | "error";

export type ImageTurn = {
  id: string;
  prompt: string;
  model: ImageModel;
  mode: ImageConversationMode;
  referenceImages: StoredReferenceImage[];
  count: number;
  size: string;
  images: StoredImage[];
  createdAt: string;
  status: ImageTurnStatus;
  error?: string;
};

export type ImageConversation = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  turns: ImageTurn[];
};

export type ImageConversationStats = {
  queued: number;
  running: number;
};

const imageConversationStorage = localforage.createInstance({
  name: "chatgpt2api",
  storeName: "image_conversations",
});

const IMAGE_CONVERSATIONS_KEY = "items";
let imageConversationWriteQueue: Promise<void> = Promise.resolve();

async function getScopedStorageKey() {
  const session = await getStoredAuthSession();
  const subjectId = String(session?.subjectId || "").trim();
  if (subjectId) {
    return `${IMAGE_CONVERSATIONS_KEY}:${subjectId}`;
  }
  return IMAGE_CONVERSATIONS_KEY;
}

function normalizeStoredImage(image: StoredImage): StoredImage {
  const normalized = {
    ...image,
    taskId: typeof image.taskId === "string" && image.taskId ? image.taskId : undefined,
    url: typeof image.url === "string" && image.url ? image.url : undefined,
    revised_prompt: typeof image.revised_prompt === "string" ? image.revised_prompt : undefined,
  };
  if (image.status === "loading" || image.status === "error" || image.status === "success") {
    return normalized;
  }
  return {
    ...normalized,
    status: image.b64_json || image.url ? "success" : "loading",
  };
}

function normalizeReferenceImage(image: StoredReferenceImage): StoredReferenceImage {
  return {
    name: image.name || "reference.png",
    type: image.type || "image/png",
    dataUrl: image.dataUrl,
  };
}

function dataUrlMimeType(dataUrl: string) {
  const match = dataUrl.match(/^data:(.*?);base64,/);
  return match?.[1] || "image/png";
}

function getLegacyReferenceImages(source: Record<string, unknown>): StoredReferenceImage[] {
  if (Array.isArray(source.referenceImages)) {
    return source.referenceImages
      .filter((image): image is StoredReferenceImage => {
        if (!image || typeof image !== "object") {
          return false;
        }
        const candidate = image as StoredReferenceImage;
        return typeof candidate.dataUrl === "string" && candidate.dataUrl.length > 0;
      })
      .map(normalizeReferenceImage);
  }

  if (source.sourceImage && typeof source.sourceImage === "object") {
    const image = source.sourceImage as { dataUrl?: unknown; fileName?: unknown };
    if (typeof image.dataUrl === "string" && image.dataUrl) {
      return [
        {
          name: typeof image.fileName === "string" && image.fileName ? image.fileName : "reference.png",
          type: dataUrlMimeType(image.dataUrl),
          dataUrl: image.dataUrl,
        },
      ];
    }
  }

  return [];
}

function normalizeTurn(turn: ImageTurn & Record<string, unknown>): ImageTurn {
  const normalizedImages = Array.isArray(turn.images) ? turn.images.map(normalizeStoredImage) : [];
  const derivedStatus: ImageTurnStatus =
    normalizedImages.some((image) => image.status === "loading")
      ? "generating"
      : normalizedImages.some((image) => image.status === "error")
        ? "error"
        : "success";

  return {
    id: String(turn.id || `${Date.now()}`),
    prompt: String(turn.prompt || ""),
    model: (turn.model as ImageModel) || "gpt-image-2",
    mode: turn.mode === "edit" ? "edit" : "generate",
    referenceImages: getLegacyReferenceImages(turn),
    count: Math.max(1, Number(turn.count || normalizedImages.length || 1)),
    size: typeof turn.size === "string" ? turn.size : "",
    images: normalizedImages,
    createdAt: String(turn.createdAt || new Date().toISOString()),
    status:
      turn.status === "queued" ||
      turn.status === "generating" ||
      turn.status === "success" ||
      turn.status === "error"
        ? turn.status
        : derivedStatus,
    error: typeof turn.error === "string" ? turn.error : undefined,
  };
}

function normalizeConversation(conversation: ImageConversation & Record<string, unknown>): ImageConversation {
  const turns = Array.isArray(conversation.turns)
    ? conversation.turns.map((turn) => normalizeTurn(turn as ImageTurn & Record<string, unknown>))
    : [
        normalizeTurn({
          id: String(conversation.id || `${Date.now()}`),
          prompt: String(conversation.prompt || ""),
          model: (conversation.model as ImageModel) || "gpt-image-2",
          mode: conversation.mode === "edit" ? "edit" : "generate",
          referenceImages: getLegacyReferenceImages(conversation),
          count: Number(conversation.count || 1),
          size: typeof conversation.size === "string" ? conversation.size : "",
          images: Array.isArray(conversation.images) ? (conversation.images as StoredImage[]) : [],
          createdAt: String(conversation.createdAt || new Date().toISOString()),
          status:
            conversation.status === "generating" || conversation.status === "success" || conversation.status === "error"
              ? conversation.status
              : "success",
          error: typeof conversation.error === "string" ? conversation.error : undefined,
        }),
      ];
  const lastTurn = turns.length > 0 ? turns[turns.length - 1] : null;

  return {
    id: String(conversation.id || `${Date.now()}`),
    title: String(conversation.title || ""),
    createdAt: String(conversation.createdAt || lastTurn?.createdAt || new Date().toISOString()),
    updatedAt: String(conversation.updatedAt || lastTurn?.createdAt || new Date().toISOString()),
    turns,
  };
}

function sortImageConversations(conversations: ImageConversation[]): ImageConversation[] {
  return [...conversations].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

function getTimestamp(value: string) {
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : 0;
}

function pickLatestConversation(current: ImageConversation, next: ImageConversation) {
  return getTimestamp(next.updatedAt) >= getTimestamp(current.updatedAt) ? next : current;
}

function queueImageConversationWrite<T>(operation: () => Promise<T>): Promise<T> {
  const result = imageConversationWriteQueue.then(operation);
  imageConversationWriteQueue = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

async function readStoredImageConversations(): Promise<ImageConversation[]> {
  const scopedKey = await getScopedStorageKey();
  let items =
    (await imageConversationStorage.getItem<Array<ImageConversation & Record<string, unknown>>>(scopedKey)) || [];

  if (items.length === 0 && scopedKey !== IMAGE_CONVERSATIONS_KEY) {
    const legacyItems =
      (await imageConversationStorage.getItem<Array<ImageConversation & Record<string, unknown>>>(
        IMAGE_CONVERSATIONS_KEY,
      )) || [];
    if (legacyItems.length > 0) {
      items = legacyItems;
      await imageConversationStorage.setItem(scopedKey, legacyItems);
    }
  }

  return items.map(normalizeConversation);
}

async function writeStoredImageConversations(conversations: ImageConversation[]): Promise<void> {
  await imageConversationStorage.setItem(await getScopedStorageKey(), sortImageConversations(conversations));
}

function mergeConversationLists(current: ImageConversation[], incoming: ImageConversation[]) {
  const conversationMap = new Map(current.map((item) => [item.id, item]));
  for (const conversation of incoming.map(normalizeConversation)) {
    const existing = conversationMap.get(conversation.id);
    conversationMap.set(conversation.id, existing ? pickLatestConversation(existing, conversation) : conversation);
  }
  return sortImageConversations([...conversationMap.values()]);
}

async function fetchRemoteImageConversations(): Promise<ImageConversation[] | null> {
  try {
    const data = await httpRequest<{ items: Array<ImageConversation & Record<string, unknown>> }>(
      "/api/image-conversations",
      { redirectOnUnauthorized: false },
    );
    return sortImageConversations((data.items || []).map(normalizeConversation));
  } catch {
    return null;
  }
}

async function saveRemoteImageConversations(conversations: ImageConversation[]): Promise<void> {
  try {
    await httpRequest<{ items: ImageConversation[] }>("/api/image-conversations", {
      method: "PUT",
      body: { items: conversations.map(normalizeConversation) },
      redirectOnUnauthorized: false,
    });
  } catch {
    // 本地缓存兜底，服务端临时不可用时不影响画图。
  }
}

async function saveRemoteImageConversation(conversation: ImageConversation): Promise<void> {
  const normalized = normalizeConversation(conversation);
  try {
    await httpRequest<{ items: ImageConversation[] }>(`/api/image-conversations/${encodeURIComponent(normalized.id)}`, {
      method: "PUT",
      body: { conversation: normalized },
      redirectOnUnauthorized: false,
    });
  } catch {
    // 本地缓存兜底，服务端临时不可用时不影响画图。
  }
}

export async function listImageConversations(): Promise<ImageConversation[]> {
  const localItems = await readStoredImageConversations();
  const remoteItems = await fetchRemoteImageConversations();
  if (remoteItems === null) {
    return sortImageConversations(localItems);
  }

  const mergedItems = mergeConversationLists(remoteItems, localItems);
  await writeStoredImageConversations(mergedItems);
  if (localItems.length > 0) {
    await saveRemoteImageConversations(mergedItems);
  }
  return mergedItems;
}

export async function saveImageConversations(conversations: ImageConversation[]): Promise<void> {
  await queueImageConversationWrite(async () => {
    const items = await readStoredImageConversations();
    const nextItems = mergeConversationLists(items, conversations);
    await writeStoredImageConversations(nextItems);
    await saveRemoteImageConversations(nextItems);
  });
}

export async function saveImageConversation(conversation: ImageConversation): Promise<void> {
  await queueImageConversationWrite(async () => {
    const items = await readStoredImageConversations();
    const nextConversation = normalizeConversation(conversation);
    const current = items.find((item) => item.id === nextConversation.id);
    const persistedConversation = current ? pickLatestConversation(current, nextConversation) : nextConversation;
    const nextItems = sortImageConversations([
      persistedConversation,
      ...items.filter((item) => item.id !== persistedConversation.id),
    ]);
    await writeStoredImageConversations(nextItems);
    await saveRemoteImageConversation(persistedConversation);
  });
}

export async function deleteImageConversation(id: string): Promise<void> {
  await queueImageConversationWrite(async () => {
    const items = await readStoredImageConversations();
    await writeStoredImageConversations(items.filter((item) => item.id !== id));
    try {
      await httpRequest<{ items: ImageConversation[] }>(`/api/image-conversations/${encodeURIComponent(id)}`, {
        method: "DELETE",
        redirectOnUnauthorized: false,
      });
    } catch {
      // 本地缓存兜底。
    }
  });
}

export async function clearImageConversations(): Promise<void> {
  await queueImageConversationWrite(async () => {
    await imageConversationStorage.removeItem(await getScopedStorageKey());
    try {
      await httpRequest<{ items: ImageConversation[] }>("/api/image-conversations", {
        method: "DELETE",
        redirectOnUnauthorized: false,
      });
    } catch {
      // 本地缓存兜底。
    }
  });
}

export function getImageConversationStats(conversation: ImageConversation | null): ImageConversationStats {
  if (!conversation) {
    return { queued: 0, running: 0 };
  }

  return conversation.turns.reduce(
    (acc, turn) => {
      if (turn.status === "queued") {
        acc.queued += 1;
      } else if (turn.status === "generating") {
        acc.running += 1;
      }
      return acc;
    },
    { queued: 0, running: 0 },
  );
}
