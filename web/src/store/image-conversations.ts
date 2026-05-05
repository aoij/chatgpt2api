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
const DELETED_IMAGE_CONVERSATIONS_KEY = "deleted_items";
const REMOTE_CACHE_TTL_MS = 60_000;
const REMOTE_SAVE_DEBOUNCE_MS = 800;
const MAX_REFERENCE_DATA_URL_CHARS = 180_000;
const DELETED_TOMBSTONE_TTL_MS = 30 * 24 * 60 * 60 * 1000;
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

async function getScopedDeletedStorageKey() {
  return `${DELETED_IMAGE_CONVERSATIONS_KEY}:${await getScopedStorageKey()}`;
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

function pruneDeletedConversationMap(value: unknown): Record<string, number> {
  const now = Date.now();
  if (!value || typeof value !== "object") {
    return {};
  }
  const result: Record<string, number> = {};
  Object.entries(value as Record<string, unknown>).forEach(([id, deletedAt]) => {
    const timestamp = Number(deletedAt);
    if (id && Number.isFinite(timestamp) && now - timestamp < DELETED_TOMBSTONE_TTL_MS) {
      result[id] = timestamp;
    }
  });
  return result;
}

async function readDeletedConversationMap(): Promise<Record<string, number>> {
  const key = await getScopedDeletedStorageKey();
  const deleted = pruneDeletedConversationMap(await imageConversationStorage.getItem(key));
  void imageConversationStorage.setItem(key, deleted).catch(() => undefined);
  return deleted;
}

async function writeDeletedConversationMap(deleted: Record<string, number>) {
  await imageConversationStorage.setItem(await getScopedDeletedStorageKey(), pruneDeletedConversationMap(deleted));
}

async function markDeletedImageConversations(ids: string[]) {
  const normalizedIds = ids.map((id) => String(id || "").trim()).filter(Boolean);
  if (normalizedIds.length === 0) {
    return;
  }
  const deleted = await readDeletedConversationMap();
  const now = Date.now();
  normalizedIds.forEach((id) => {
    deleted[id] = now;
    pendingRemoteConversationSaves.delete(id);
  });
  await writeDeletedConversationMap(deleted);
}

function filterDeletedConversations(items: ImageConversation[], deleted: Record<string, number>) {
  if (Object.keys(deleted).length === 0) {
    return items;
  }
  return items.filter((item) => !deleted[item.id]);
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
    const deleted = await readDeletedConversationMap();
    const filtered = filterDeletedConversations(localConversationCache.items, deleted);
    if (filtered.length !== localConversationCache.items.length) {
      localConversationCache = { subjectKey: scopedKey, items: filtered };
      void imageConversationStorage.setItem(scopedKey, filtered).catch(() => undefined);
    }
    return filtered;
  }
  const deleted = await readDeletedConversationMap();
  let items =
    (await imageConversationStorage.getItem<Array<ImageConversation & Record<string, unknown>>>(scopedKey)) || [];

  if (items.length === 0 && scopedKey !== IMAGE_CONVERSATIONS_KEY) {
    const legacyItems =
      (await imageConversationStorage.getItem<Array<ImageConversation & Record<string, unknown>>>(
        IMAGE_CONVERSATIONS_KEY,
      )) || [];
    if (legacyItems.length > 0) {
      items = filterDeletedConversations(sortImageConversations(legacyItems.map(normalizeConversation)), deleted);
      await imageConversationStorage.setItem(scopedKey, items);
      await imageConversationStorage.removeItem(IMAGE_CONVERSATIONS_KEY).catch(() => undefined);
    }
  }

  const normalizedItems = filterDeletedConversations(sortImageConversations(items.map(normalizeConversation)), deleted);
  localConversationCache = {
    subjectKey: scopedKey,
    items: normalizedItems,
  };
  void imageConversationStorage.setItem(scopedKey, normalizedItems).catch(() => undefined);
  return normalizedItems;
}

async function writeStoredImageConversations(conversations: ImageConversation[]): Promise<void> {
  const scopedKey = await getScopedStorageKey();
  const deleted = await readDeletedConversationMap();
  const items = filterDeletedConversations(sortImageConversations(conversations.map(normalizeConversation)), deleted);
  localConversationCache = {
    subjectKey: scopedKey,
    items,
  };
  await imageConversationStorage.setItem(scopedKey, items);
  if (scopedKey !== IMAGE_CONVERSATIONS_KEY) {
    await imageConversationStorage.removeItem(IMAGE_CONVERSATIONS_KEY).catch(() => undefined);
  }
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
    const deleted = await readDeletedConversationMap();
    const items = filterDeletedConversations(remoteConversationCache.items, deleted);
    if (items.length !== remoteConversationCache.items.length) {
      remoteConversationCache = { ...remoteConversationCache, items };
    }
    return items;
  }
  try {
    const data = await httpRequest<{ items: Array<ImageConversation & Record<string, unknown>> }>(
      "/api/image-conversations",
      { redirectOnUnauthorized: false },
    );
    const deleted = await readDeletedConversationMap();
    const items = filterDeletedConversations(sortImageConversations((data.items || []).map(normalizeConversation)), deleted);
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
  const deleted = await readDeletedConversationMap();
  const mergedItems = filterDeletedConversations(mergeConversationLists(remoteItems, localItems), deleted);
  await writeStoredImageConversations(mergedItems);
  emitImageConversationsSynced(mergedItems);
}

async function saveRemoteImageConversations(conversations: ImageConversation[]): Promise<void> {
  try {
    const deleted = await readDeletedConversationMap();
    const outgoingItems = filterDeletedConversations(conversations.map(normalizeConversation), deleted);
    if (outgoingItems.length === 0) {
      return;
    }
    const data = await httpRequest<{ items: ImageConversation[] }>("/api/image-conversations", {
      method: "PUT",
      body: { items: outgoingItems },
      redirectOnUnauthorized: false,
    });
    const nextDeleted = await readDeletedConversationMap();
    const remoteItems = filterDeletedConversations(sortImageConversations((data.items || outgoingItems).map(normalizeConversation)), nextDeleted);
    remoteConversationCache = {
      subjectKey: await getScopedSubjectKey(),
      fetchedAt: Date.now(),
      items: remoteItems,
    };
    const localItems = await readStoredImageConversations();
    const mergedItems = filterDeletedConversations(mergeConversationLists(localItems, remoteItems), nextDeleted);
    await writeStoredImageConversations(mergedItems);
    emitImageConversationsSynced(mergedItems);
  } catch {
    // 本地缓存兜底，服务端临时不可用时不影响画图。
  }
}

async function saveRemoteImageConversation(conversation: ImageConversation): Promise<void> {
  const normalized = normalizeConversation(conversation);
  const deleted = await readDeletedConversationMap();
  if (deleted[normalized.id]) {
    return;
  }
  try {
    const data = await httpRequest<{ items: ImageConversation[] }>(`/api/image-conversations/${encodeURIComponent(normalized.id)}`, {
      method: "PUT",
      body: { conversation: normalized },
      redirectOnUnauthorized: false,
    });
    const deleted = await readDeletedConversationMap();
    const remoteItems = filterDeletedConversations(sortImageConversations((data.items || []).map(normalizeConversation)), deleted);
    remoteConversationCache = {
      subjectKey: await getScopedSubjectKey(),
      fetchedAt: Date.now(),
      items: remoteItems,
    };
    const localItems = await readStoredImageConversations();
    const mergedItems = filterDeletedConversations(mergeConversationLists(localItems, remoteItems), deleted);
    await writeStoredImageConversations(mergedItems);
    emitImageConversationsSynced(mergedItems);
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
        const deleted = await readDeletedConversationMap();
        const items = filterDeletedConversations(Array.from(pendingRemoteConversationSaves.values()), deleted);
        for (const id of Object.keys(deleted)) {
          pendingRemoteConversationSaves.delete(id);
        }
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
    remoteSaveQueue = remoteSaveQueue
      .catch(() => undefined)
      .then(async () => {
        const deleted = await readDeletedConversationMap();
        const items = filterDeletedConversations(Array.from(pendingRemoteConversationSaves.values()), deleted);
        for (const id of Object.keys(deleted)) {
          pendingRemoteConversationSaves.delete(id);
        }
        pendingRemoteConversationSaves.clear();
        if (items.length > 0) {
          await saveRemoteImageConversations(items);
        }
      });
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

  const deleted = await readDeletedConversationMap();
  const mergedItems = filterDeletedConversations(mergeConversationLists(remoteItems, localItems), deleted);
  await writeStoredImageConversations(mergedItems);
  return mergedItems;
}

export async function saveImageConversations(conversations: ImageConversation[]): Promise<void> {
  await queueImageConversationWrite(async () => {
    const items = await readStoredImageConversations();
    const deleted = await readDeletedConversationMap();
    const incoming = filterDeletedConversations(conversations.map(normalizeConversation), deleted);
    const nextItems = filterDeletedConversations(mergeConversationLists(items, incoming), deleted);
    await writeStoredImageConversations(nextItems);
    const changedItems = incoming;
    scheduleRemoteConversationSaves(changedItems);
  });
}

export async function saveImageConversation(conversation: ImageConversation): Promise<void> {
  await queueImageConversationWrite(async () => {
    const deleted = await readDeletedConversationMap();
    const items = await readStoredImageConversations();
    const nextConversation = normalizeConversation(conversation);
    if (deleted[nextConversation.id]) {
      await writeStoredImageConversations(items);
      return;
    }
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
    await markDeletedImageConversations([id]);
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
        items: filterDeletedConversations(
          sortImageConversations((data.items || []).map(normalizeConversation)),
          await readDeletedConversationMap(),
        ),
      };
    } catch {
      // 本地缓存兜底。
    }
  });
}

export async function clearImageConversations(): Promise<void> {
  await queueImageConversationWrite(async () => {
    const items = await readStoredImageConversations();
    await markDeletedImageConversations(items.map((item) => item.id));
    pendingRemoteConversationSaves.clear();
    await imageConversationStorage.removeItem(await getScopedStorageKey());
    await imageConversationStorage.removeItem(IMAGE_CONVERSATIONS_KEY).catch(() => undefined);
    localConversationCache = null;
    try {
      const data = await httpRequest<{ items: ImageConversation[] }>("/api/image-conversations", {
        method: "DELETE",
        redirectOnUnauthorized: false,
      });
      remoteConversationCache = {
        subjectKey: await getScopedSubjectKey(),
        fetchedAt: Date.now(),
        items: filterDeletedConversations(
          sortImageConversations((data.items || []).map(normalizeConversation)),
          await readDeletedConversationMap(),
        ),
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
