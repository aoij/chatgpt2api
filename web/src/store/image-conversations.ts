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

export const IMAGE_CONVERSATIONS_SYNC_EVENT = "chatgpt2api:image-conversations-sync";

const IMAGE_CONVERSATIONS_KEY = "items";
const REMOTE_CACHE_TTL_MS = 60_000;
const REMOTE_SAVE_DEBOUNCE_MS = 800;
const MAX_REFERENCE_DATA_URL_CHARS = 180_000;
let imageConversationWriteQueue: Promise<void> = Promise.resolve();
let remoteConversationCache:
  | {
      subjectKey: string;
      fetchedAt: number;
      items: ImageConversation[];
    }
  | null = null;
let localConversationCache:
  | {
      subjectKey: string;
      items: ImageConversation[];
    }
  | null = null;
let remoteSaveTimer: number | null = null;
let remoteSaveQueue: Promise<void> = Promise.resolve();
const pendingRemoteConversationSaves = new Map<string, ImageConversation>();

async function getScopedStorageKey() {
  const session = await getStoredAuthSession();
  const subjectId = String(session?.subjectId || "").trim();
  if (subjectId) {
    return `${IMAGE_CONVERSATIONS_KEY}:${subjectId}`;
  }
  return IMAGE_CONVERSATIONS_KEY;
}

async function getScopedSubjectKey() {
  return getScopedStorageKey();
}

function normalizeStoredImage(image: StoredImage): StoredImage {
  const normalized = {
    ...image,
    taskId: typeof image.taskId === "string" && image.taskId ? image.taskId : undefined,
    url: typeof image.url === "string" && image.url ? image.url : undefined,
    b64_json: typeof image.b64_json === "string" && image.b64_json ? image.b64_json : undefined,
    revised_prompt: typeof image.revised_prompt === "string" ? image.revised_prompt : undefined,
  };
  if (normalized.url && normalized.b64_json) {
    normalized.b64_json = undefined;
  }
  if (image.status === "loading" || image.status === "error" || image.status === "success") {
    return normalized;
  }
  return {
    ...normalized,
    status: image.b64_json || image.url ? "success" : "loading",
  };
}

function normalizeReferenceImage(image: StoredReferenceImage): StoredReferenceImage | null {
  if (!image.dataUrl || image.dataUrl.length > MAX_REFERENCE_DATA_URL_CHARS) {
    return null;
  }
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
      .map(normalizeReferenceImage)
      .filter((image): image is StoredReferenceImage => image !== null);
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
      ].map(normalizeReferenceImage).filter((item): item is StoredReferenceImage => item !== null);
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
  if (localConversationCache?.subjectKey === scopedKey) {
    return localConversationCache.items;
  }
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

  const normalizedItems = sortImageConversations(items.map(normalizeConversation));
  localConversationCache = {
    subjectKey: scopedKey,
    items: normalizedItems,
  };
  void imageConversationStorage.setItem(scopedKey, normalizedItems).catch(() => undefined);
  return normalizedItems;
}

async function writeStoredImageConversations(conversations: ImageConversation[]): Promise<void> {
  const scopedKey = await getScopedStorageKey();
  const items = sortImageConversations(conversations.map(normalizeConversation));
  localConversationCache = {
    subjectKey: scopedKey,
    items,
  };
  await imageConversationStorage.setItem(scopedKey, items);
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
  const subjectKey = await getScopedSubjectKey();
  if (
    remoteConversationCache &&
    remoteConversationCache.subjectKey === subjectKey &&
    Date.now() - remoteConversationCache.fetchedAt < REMOTE_CACHE_TTL_MS
  ) {
    return remoteConversationCache.items;
  }
  try {
    const data = await httpRequest<{ items: Array<ImageConversation & Record<string, unknown>> }>(
      "/api/image-conversations",
      { redirectOnUnauthorized: false },
    );
    const items = sortImageConversations((data.items || []).map(normalizeConversation));
    remoteConversationCache = {
      subjectKey,
      fetchedAt: Date.now(),
      items,
    };
    return items;
  } catch {
    return null;
  }
}

function emitImageConversationsSynced(items: ImageConversation[]) {
  if (typeof window === "undefined") {
    return;
  }
  window.dispatchEvent(new CustomEvent(IMAGE_CONVERSATIONS_SYNC_EVENT, { detail: { items } }));
}

async function syncRemoteImageConversations(localItems: ImageConversation[]) {
  const remoteItems = await fetchRemoteImageConversations();
  if (remoteItems === null) {
    return;
  }
  const mergedItems = mergeConversationLists(remoteItems, localItems);
  await writeStoredImageConversations(mergedItems);
  emitImageConversationsSynced(mergedItems);
}

async function saveRemoteImageConversations(conversations: ImageConversation[]): Promise<void> {
  try {
    const data = await httpRequest<{ items: ImageConversation[] }>("/api/image-conversations", {
      method: "PUT",
      body: { items: conversations.map(normalizeConversation) },
      redirectOnUnauthorized: false,
    });
    remoteConversationCache = {
      subjectKey: await getScopedSubjectKey(),
      fetchedAt: Date.now(),
      items: sortImageConversations((data.items || conversations).map(normalizeConversation)),
    };
  } catch {
    // 本地缓存兜底，服务端临时不可用时不影响画图。
  }
}

async function saveRemoteImageConversation(conversation: ImageConversation): Promise<void> {
  const normalized = normalizeConversation(conversation);
  try {
    const data = await httpRequest<{ items: ImageConversation[] }>(`/api/image-conversations/${encodeURIComponent(normalized.id)}`, {
      method: "PUT",
      body: { conversation: normalized },
      redirectOnUnauthorized: false,
    });
    remoteConversationCache = {
      subjectKey: await getScopedSubjectKey(),
      fetchedAt: Date.now(),
      items: sortImageConversations((data.items || []).map(normalizeConversation)),
    };
  } catch {
    // 本地缓存兜底，服务端临时不可用时不影响画图。
  }
}

function scheduleRemoteConversationSaves(conversations: ImageConversation[]) {
  conversations.map(normalizeConversation).forEach((conversation) => {
    pendingRemoteConversationSaves.set(conversation.id, conversation);
  });
  if (typeof window === "undefined") {
    remoteSaveQueue = remoteSaveQueue
      .catch(() => undefined)
      .then(async () => {
        const items = Array.from(pendingRemoteConversationSaves.values());
        pendingRemoteConversationSaves.clear();
        if (items.length > 0) {
          await saveRemoteImageConversations(items);
        }
      });
    return;
  }
  if (remoteSaveTimer !== null) {
    window.clearTimeout(remoteSaveTimer);
  }
  remoteSaveTimer = window.setTimeout(() => {
    remoteSaveTimer = null;
    const items = Array.from(pendingRemoteConversationSaves.values());
    pendingRemoteConversationSaves.clear();
    if (items.length === 0) {
      return;
    }
    remoteSaveQueue = remoteSaveQueue
      .catch(() => undefined)
      .then(() => saveRemoteImageConversations(items));
  }, REMOTE_SAVE_DEBOUNCE_MS);
}

export async function listImageConversations(): Promise<ImageConversation[]> {
  const localItems = await readStoredImageConversations();
  if (localItems.length > 0) {
    void syncRemoteImageConversations(localItems);
    return sortImageConversations(localItems);
  }

  const remoteItems = await fetchRemoteImageConversations();
  if (remoteItems === null) {
    return sortImageConversations(localItems);
  }

  const mergedItems = mergeConversationLists(remoteItems, localItems);
  await writeStoredImageConversations(mergedItems);
  return mergedItems;
}

export async function saveImageConversations(conversations: ImageConversation[]): Promise<void> {
  await queueImageConversationWrite(async () => {
    const items = await readStoredImageConversations();
    const nextItems = mergeConversationLists(items, conversations);
    await writeStoredImageConversations(nextItems);
    const changedItems = conversations.map(normalizeConversation);
    scheduleRemoteConversationSaves(changedItems);
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
    scheduleRemoteConversationSaves([persistedConversation]);
  });
}

export async function deleteImageConversation(id: string): Promise<void> {
  await queueImageConversationWrite(async () => {
    pendingRemoteConversationSaves.delete(id);
    const items = await readStoredImageConversations();
    await writeStoredImageConversations(items.filter((item) => item.id !== id));
    try {
      const data = await httpRequest<{ items: ImageConversation[] }>(`/api/image-conversations/${encodeURIComponent(id)}`, {
        method: "DELETE",
        redirectOnUnauthorized: false,
      });
      remoteConversationCache = {
        subjectKey: await getScopedSubjectKey(),
        fetchedAt: Date.now(),
        items: sortImageConversations((data.items || []).map(normalizeConversation)),
      };
    } catch {
      // 本地缓存兜底。
    }
  });
}

export async function clearImageConversations(): Promise<void> {
  await queueImageConversationWrite(async () => {
    pendingRemoteConversationSaves.clear();
    await imageConversationStorage.removeItem(await getScopedStorageKey());
    localConversationCache = null;
    try {
      const data = await httpRequest<{ items: ImageConversation[] }>("/api/image-conversations", {
        method: "DELETE",
        redirectOnUnauthorized: false,
      });
      remoteConversationCache = {
        subjectKey: await getScopedSubjectKey(),
        fetchedAt: Date.now(),
        items: sortImageConversations((data.items || []).map(normalizeConversation)),
      };
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
