"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { History, LoaderCircle, Plus, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import type { BananaPrompt } from "@/app/image/banana-prompts";
import { ImageComposer } from "@/app/image/components/image-composer";
import { ImagePromptMarket } from "@/app/image/components/image-prompt-market";
import { ImageResults, type ImageLightboxItem } from "@/app/image/components/image-results";
import { ImageSidebar } from "@/app/image/components/image-sidebar";
import { ImageLightbox } from "@/components/image-lightbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  createImageEditTask,
  createImageGenerationTask,
  fetchAccountSummary,
  fetchCurrentUser,
  fetchImageTaskRuntime,
  fetchImageTasks,
  fetchPublicConfig,
  type ImageTaskRuntime,
  type PublicConfig,
  type ImageTask,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import {
  clearImageConversations,
  deleteImageConversation,
  fetchImageConversation,
  getImageConversationStats,
  listImageConversations,
  renameImageConversation,
  saveImageConversation,
  saveImageConversations,
  subscribeImageConversationSync,
  summarizeImageConversation,
  type ImageConversation,
  type ImageConversationMode,
  type ImageConversationSummary,
  type ImageTurn,
  type ImageTurnStatus,
  type StoredImage,
  type StoredReferenceImage,
} from "@/store/image-conversations";
import { cn } from "@/lib/utils";

const ACTIVE_CONVERSATION_STORAGE_KEY = "chatgpt2api:image_active_conversation_id";
const DRAFT_CONVERSATION_STORAGE_VALUE = "__draft__";
const IMAGE_SIZE_STORAGE_KEY = "chatgpt2api:image_last_size";
const IMAGE_COUNT_STORAGE_KEY = "chatgpt2api:image_last_count";
const EDIT_TASK_SUBMIT_CONCURRENCY = 2;
const GENERATE_TASK_SUBMIT_CONCURRENCY = 8;
const TASK_SUBMIT_RETRY = 2;
const TASK_POLL_RETRY = 4;
const TASK_STATUS_REPAIR_LOOKBACK_MS = 24 * 60 * 60 * 1000;
const MISSING_EDIT_REFERENCE_ERROR = "历史编辑任务缺少参考图，请重新选择图片后再编辑";
const MAX_REFERENCE_IMAGE_EDGE = 1600;
const REFERENCE_IMAGE_JPEG_QUALITY = 0.88;
const CONVERSATION_QUEUE_LOCK_STORAGE_KEY = "chatgpt2api:image_conversation_queue_lock";
const CONVERSATION_QUEUE_LOCK_TTL_MS = 90_000;
const CONVERSATION_QUEUE_RETRY_INTERVAL_MS = 10_000;
const MAX_POLL_TASK_IDS = 12;
const MISSING_TASK_REQUEUE_LIMIT = 2;

function clampImageCount(value: string) {
  return String(Math.min(100, Math.max(1, Math.floor(Number(value) || 1))));
}

function clampImageCountWithLimit(value: string, limit: number) {
  const normalizedLimit = Math.max(1, Math.floor(Number(limit) || 1));
  return String(Math.min(normalizedLimit, Math.max(1, Math.floor(Number(value) || 1))));
}
const activeConversationQueueIds = new Set<string>();
const tabInstanceId = createId();
const AUTO_SCROLL_BOTTOM_THRESHOLD = 80;

function getActiveConversationStorage() {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.sessionStorage;
  } catch {
    try {
      return window.localStorage;
    } catch {
      return null;
    }
  }
}

function clearLegacyActiveConversationSelection() {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.removeItem(ACTIVE_CONVERSATION_STORAGE_KEY);
  } catch {
    // ignore storage cleanup failures
  }
}

function readPersistedActiveConversationSelection() {
  const storage = getActiveConversationStorage();
  const scopedValue = storage?.getItem(ACTIVE_CONVERSATION_STORAGE_KEY) ?? null;
  if (scopedValue) {
    return scopedValue;
  }
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const legacyValue = window.localStorage.getItem(ACTIVE_CONVERSATION_STORAGE_KEY);
    if (legacyValue && storage && storage !== window.localStorage) {
      storage.setItem(ACTIVE_CONVERSATION_STORAGE_KEY, legacyValue);
      clearLegacyActiveConversationSelection();
    }
    return legacyValue;
  } catch {
    return scopedValue;
  }
}

function buildConversationTitle(prompt: string) {
  const trimmed = prompt.trim();
  if (trimmed.length <= 12) {
    return trimmed;
  }
  return `${trimmed.slice(0, 12)}...`;
}

function formatConversationTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatAvailableQuotaSummary(summary: Awaited<ReturnType<typeof fetchAccountSummary>>) {
  if (summary.available_unlimited) {
    return "∞";
  }
  if (summary.available_unknown) {
    return "未知";
  }
  return String(Math.max(0, Number(summary.available_quota) || 0));
}

function formatRuntimeDuration(ms: number) {
  const value = Math.max(0, Math.floor(Number(ms) || 0));
  if (!value) {
    return "";
  }
  const totalSeconds = Math.max(1, Math.round(value / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes <= 0) {
    return `${totalSeconds} 秒`;
  }
  if (seconds === 0) {
    return `${minutes} 分钟`;
  }
  return `${minutes} 分 ${seconds} 秒`;
}

function createId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function readFileAsDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("读取参考图失败"));
    reader.readAsDataURL(file);
  });
}

function dataUrlToFile(dataUrl: string, fileName: string, mimeType?: string) {
  const [header, content] = dataUrl.split(",", 2);
  const matchedMimeType = header.match(/data:(.*?);base64/)?.[1];
  const binary = atob(content || "");
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new File([bytes], fileName, { type: mimeType || matchedMimeType || "image/png" });
}

function friendlyImageError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error || "");
  if (!message || message === "Network Error") {
    return "网络请求失败：可能是移动网络不稳定、服务刚重启，或一次上传图片过大，请稍后重试";
  }
  if (/cloudflare|origin web server|invalid or incomplete response|proxy read timeout|error 52[024]/i.test(message)) {
    return "上游图片服务临时返回 Cloudflare 错误，可能是节点/账号或上游服务拥堵，请稍后重试；如连续出现请切换账号/节点";
  }
  if (/network|failed to fetch|connection|reset|abort|timeout/i.test(message)) {
    return `网络请求失败：${message}`;
  }
  return message;
}

function isRetryableTaskError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error || "");
  return /network|failed to fetch|connection|reset|abort|timeout|cloudflare|origin web server|invalid or incomplete response|proxy read timeout|error 52[024]/i.test(message);
}

async function imageElementFromObjectUrl(url: string) {
  return new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("读取参考图失败"));
    image.src = url;
  });
}

async function canvasToBlob(canvas: HTMLCanvasElement, type: string, quality: number) {
  return new Promise<Blob | null>((resolve) => {
    canvas.toBlob((blob) => resolve(blob), type, quality);
  });
}

async function optimizeReferenceFile(file: File) {
  const fallback = async () => ({
    file,
    referenceImage: {
      name: file.name,
      type: file.type || "image/png",
      dataUrl: await readFileAsDataUrl(file),
    },
  });

  if (!file.type.startsWith("image/") || /svg|gif|heic|heif/i.test(file.type)) {
    return fallback();
  }

  const objectUrl = URL.createObjectURL(file);
  try {
    const image = await imageElementFromObjectUrl(objectUrl);
    const scale = Math.min(1, MAX_REFERENCE_IMAGE_EDGE / Math.max(image.naturalWidth || image.width, image.naturalHeight || image.height));
    const width = Math.max(1, Math.round((image.naturalWidth || image.width) * scale));
    const height = Math.max(1, Math.round((image.naturalHeight || image.height) * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) {
      return fallback();
    }
    context.fillStyle = "#fff";
    context.fillRect(0, 0, width, height);
    context.drawImage(image, 0, 0, width, height);
    const blob = await canvasToBlob(canvas, "image/jpeg", REFERENCE_IMAGE_JPEG_QUALITY);
    if (!blob) {
      return fallback();
    }
    const shouldUseOptimized = scale < 1 || blob.size < file.size * 0.95 || file.type !== "image/jpeg";
    if (!shouldUseOptimized) {
      return fallback();
    }
    const optimizedName = `${file.name.replace(/\.[^.]+$/, "") || "reference"}.jpg`;
    const optimizedFile = new File([blob], optimizedName, { type: "image/jpeg" });
    return {
      file: optimizedFile,
      referenceImage: {
        name: optimizedName,
        type: "image/jpeg",
        dataUrl: await readFileAsDataUrl(optimizedFile),
      },
    };
  } catch {
    return fallback();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

async function runWithConcurrency<T, R>(
  items: T[],
  limit: number,
  worker: (item: T, index: number) => Promise<R>,
) {
  const results: Array<{ item: T; index: number; value?: R; error?: unknown }> = [];
  let nextIndex = 0;
  const concurrency = Math.max(1, Math.min(limit, items.length || 1));

  await Promise.all(
    Array.from({ length: concurrency }, async () => {
      while (nextIndex < items.length) {
        const currentIndex = nextIndex;
        nextIndex += 1;
        const item = items[currentIndex];
        try {
          results[currentIndex] = { item, index: currentIndex, value: await worker(item, currentIndex) };
        } catch (error) {
          results[currentIndex] = { item, index: currentIndex, error };
        }
      }
    }),
  );

  return results;
}

function buildReferenceImageFromResult(image: StoredImage, fileName: string): StoredReferenceImage | null {
  if (!image.b64_json) {
    return null;
  }

  return {
    name: fileName,
    type: "image/png",
    dataUrl: `data:image/png;base64,${image.b64_json}`,
  };
}

async function fetchImageAsFile(url: string, fileName: string) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error("读取结果图失败");
  }
  const blob = await response.blob();
  return new File([blob], fileName, { type: blob.type || "image/png" });
}

function buildReferenceFileName(url: string, index: number, fallbackPrefix: string) {
  const path = url.split(/[?#]/, 1)[0] || "";
  const rawName = path.split("/").filter(Boolean).pop() || "";
  try {
    const decodedName = decodeURIComponent(rawName).trim();
    if (decodedName && /\.[a-z0-9]{2,8}$/i.test(decodedName)) {
      return decodedName;
    }
  } catch {
    // ignore malformed URI
  }
  return `${fallbackPrefix}-${index + 1}.png`;
}

async function buildReferenceImageFromUrl(url: string, index: number, fallbackPrefix: string) {
  const file = await fetchImageAsFile(url, buildReferenceFileName(url, index, fallbackPrefix));
  return optimizeReferenceFile(file);
}

function getPromptReferenceImageUrls(prompt: BananaPrompt) {
  const urls = prompt.referenceImageUrls.length > 0 ? prompt.referenceImageUrls : [prompt.preview];
  return Array.from(new Set(urls.map((url) => url.trim()).filter(Boolean)));
}

async function buildReferenceImageFromStoredImage(image: StoredImage, fileName: string) {
  const direct = buildReferenceImageFromResult(image, fileName);
  if (direct) {
    return {
      referenceImage: direct,
      file: dataUrlToFile(direct.dataUrl, direct.name, direct.type),
    };
  }

  if (!image.url) {
    return null;
  }
  const file = await fetchImageAsFile(image.url, fileName);
  return optimizeReferenceFile(file);
}

function isManagedImageUrl(value?: string) {
  return Boolean(value && value.includes("/images/"));
}

function taskDataToStoredImage(image: StoredImage, task: ImageTask): StoredImage {
  let nextImage: StoredImage;

  if (task.status === "success") {
    const first = task.data?.[0];
    if (!first?.b64_json && !first?.url) {
      nextImage = {
        ...image,
        taskId: task.id,
        status: "error",
        error: "未返回图片数据",
      };
    } else {
      const persistPending = Number(task.persist_summary?.pending || 0) > 0;
      const persistFailed = Number(task.persist_summary?.failed || 0) > 0;
      const nextUrl = typeof first.url === "string" ? first.url : undefined;
      const isLocalStoredUrl = isManagedImageUrl(nextUrl);
      if (persistPending && nextUrl && !isLocalStoredUrl && !first.b64_json) {
        nextImage = {
          ...image,
          taskId: task.id,
          status: "loading",
          b64_json: undefined,
          url: undefined,
          revised_prompt: first.revised_prompt,
          error: "图片已生成，正在转存本地…",
          persistStatus: "pending",
        };
      } else if (persistFailed && nextUrl && !isLocalStoredUrl && !first.b64_json) {
        nextImage = {
          ...image,
          taskId: task.id,
          status: "error",
          b64_json: undefined,
          url: undefined,
          revised_prompt: first.revised_prompt,
          error: "图片已生成，但转存本地失败，远程临时链接无法稳定展示，请重新生成或检查号池下载链路",
          persistStatus: "error",
        };
      } else {
        const transientError = persistFailed ? "图片已生成，转存本地失败，暂时使用远程链接展示" : undefined;
        nextImage = {
          ...image,
          taskId: task.id,
          status: "success",
          b64_json: nextUrl ? undefined : first.b64_json,
          url: nextUrl,
          revised_prompt: first.revised_prompt,
          error: persistPending ? "图片已生成，正在转存本地…" : transientError,
          persistStatus: persistPending ? "pending" : persistFailed ? "error" : "done",
        };
      }
    }
  } else if (task.status === "error") {
    nextImage = {
      ...image,
      taskId: task.id,
      status: "error",
      error: friendlyImageError(task.error || "生成失败"),
      persistStatus: undefined,
    };
  } else {
    nextImage = {
      ...image,
      taskId: task.id,
      status: "loading",
      error: undefined,
      persistStatus: undefined,
    };
  }

  if (
    nextImage.taskId === image.taskId &&
    nextImage.status === image.status &&
    nextImage.b64_json === image.b64_json &&
    nextImage.url === image.url &&
    nextImage.revised_prompt === image.revised_prompt &&
    nextImage.error === image.error &&
    nextImage.persistStatus === image.persistStatus
  ) {
    return image;
  }

  return nextImage;
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function pickFallbackConversationId(conversations: ImageConversation[]) {
  const activeConversation = conversations.find((conversation) =>
    conversation.turns.some((turn) => turn.status === "queued" || turn.status === "generating"),
  );
  return activeConversation?.id ?? conversations[0]?.id ?? null;
}

function readConversationQueueLocks(): Record<string, { owner: string; expiresAt: number }> {
  if (typeof window === "undefined") {
    return {};
  }
  try {
    const raw = window.localStorage.getItem(CONVERSATION_QUEUE_LOCK_STORAGE_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as Record<string, { owner?: string; expiresAt?: number }>;
    const now = Date.now();
    const next: Record<string, { owner: string; expiresAt: number }> = {};
    Object.entries(parsed || {}).forEach(([key, value]) => {
      const owner = String(value?.owner || "").trim();
      const expiresAt = Number(value?.expiresAt || 0);
      if (owner && Number.isFinite(expiresAt) && expiresAt > now) {
        next[key] = { owner, expiresAt };
      }
    });
    return next;
  } catch {
    return {};
  }
}

function writeConversationQueueLocks(locks: Record<string, { owner: string; expiresAt: number }>) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(CONVERSATION_QUEUE_LOCK_STORAGE_KEY, JSON.stringify(locks));
}

function acquireConversationQueueLock(conversationId: string) {
  if (typeof window === "undefined") {
    return true;
  }
  const locks = readConversationQueueLocks();
  const current = locks[conversationId];
  const now = Date.now();
  if (current && current.owner !== tabInstanceId && current.expiresAt > now) {
    return false;
  }
  locks[conversationId] = {
    owner: tabInstanceId,
    expiresAt: now + CONVERSATION_QUEUE_LOCK_TTL_MS,
  };
  writeConversationQueueLocks(locks);
  const confirmed = readConversationQueueLocks()[conversationId];
  return confirmed?.owner === tabInstanceId;
}

function refreshConversationQueueLock(conversationId: string) {
  if (typeof window === "undefined") {
    return;
  }
  const locks = readConversationQueueLocks();
  const current = locks[conversationId];
  if (!current || current.owner !== tabInstanceId) {
    return;
  }
  locks[conversationId] = {
    owner: tabInstanceId,
    expiresAt: Date.now() + CONVERSATION_QUEUE_LOCK_TTL_MS,
  };
  writeConversationQueueLocks(locks);
}

function releaseConversationQueueLock(conversationId: string) {
  if (typeof window === "undefined") {
    return;
  }
  const locks = readConversationQueueLocks();
  if (locks[conversationId]?.owner === tabInstanceId) {
    delete locks[conversationId];
    writeConversationQueueLocks(locks);
  }
}

function sortImageConversations(conversations: ImageConversation[]) {
  return [...conversations].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

function deriveTurnStatus(turn: ImageTurn): Pick<ImageTurn, "status" | "error"> {
  const loadingCount = turn.images.filter((image) => image.status === "loading").length;
  const failedCount = turn.images.filter((image) => image.status === "error").length;
  const successCount = turn.images.filter((image) => image.status === "success").length;
  if (loadingCount > 0) {
    return { status: turn.status === "queued" ? "queued" : "generating", error: undefined };
  }
  if (failedCount > 0) {
    return { status: "error", error: `其中 ${failedCount} 张未成功生成` };
  }
  if (successCount > 0) {
    return { status: "success", error: undefined };
  }
  return { status: "queued", error: undefined };
}

function collectPendingTaskIdsFromTurn(turn: ImageTurn | null | undefined, limit = MAX_POLL_TASK_IDS) {
  if (!turn || turn.resultsDeleted) {
    return [];
  }
  const ids: string[] = [];
  for (const image of turn.images) {
    if (image.status === "loading" && image.taskId && !ids.includes(image.taskId)) {
      ids.push(image.taskId);
      if (ids.length >= limit) {
        return ids;
      }
    }
  }
  return ids;
}

async function syncConversationImageTasks(items: ImageConversation[]) {
  const repairCutoff = Date.now() - TASK_STATUS_REPAIR_LOOKBACK_MS;
  const taskIds = Array.from(
    new Set(
      items.flatMap((conversation) => {
        const conversationUpdatedAt = new Date(conversation.updatedAt || conversation.createdAt || 0).getTime();
        const shouldRepairRecentSuccess =
          Number.isFinite(conversationUpdatedAt) && conversationUpdatedAt >= repairCutoff;
        return conversation.turns.flatMap((turn) =>
          turn.images.flatMap((image) =>
            image.taskId &&
            (image.status !== "success" ||
              (!image.url && !image.b64_json) ||
              image.persistStatus === "pending" ||
              (shouldRepairRecentSuccess && Boolean(image.url) && !isManagedImageUrl(String(image.url))))
              ? [image.taskId]
              : [],
          ),
        );
      }),
    ),
  ).slice(0, MAX_POLL_TASK_IDS);
  if (taskIds.length === 0) {
    return items;
  }

  let taskList: Awaited<ReturnType<typeof fetchImageTasks>>;
  try {
    taskList = await fetchImageTasks(taskIds);
  } catch {
    return items;
  }
  const taskMap = new Map(taskList.items.map((task) => [task.id, task]));
  let changed = false;
  const normalized = items.map((conversation) => {
    const conversationUpdatedAt = new Date(conversation.updatedAt || conversation.createdAt || 0).getTime();
    const shouldRepairRecentSuccess =
      Number.isFinite(conversationUpdatedAt) && conversationUpdatedAt >= repairCutoff;
    const turns = conversation.turns.map((turn) => {
      let turnChanged = false;
      const images = turn.images.map((image) => {
        if (
          !image.taskId ||
          (
            !shouldRepairRecentSuccess &&
            image.status === "success" &&
            image.persistStatus !== "pending" &&
            (image.url || image.b64_json)
          )
        ) {
          return image;
        }
        const task = taskMap.get(image.taskId);
        if (!task) {
          return image;
        }
        const nextImage = taskDataToStoredImage(image, task);
        if (nextImage !== image) {
          turnChanged = true;
        }
        return nextImage;
      });
      if (!turnChanged) {
        return turn;
      }
      changed = true;
      const derived = deriveTurnStatus({ ...turn, images });
      return {
        ...turn,
        ...derived,
        images,
      };
    });
    if (turns === conversation.turns || !turns.some((turn, index) => turn !== conversation.turns[index])) {
      return conversation;
    }
    return {
      ...conversation,
      turns,
      updatedAt: new Date().toISOString(),
    };
  });

  if (changed) {
    await saveImageConversations(normalized);
  }
  return normalized;
}

async function recoverConversationHistory(items: ImageConversation[]) {
  let changed = false;
  const normalized = items.map((conversation) => {
    const turns = conversation.turns.map((turn) => {
      if (turn.status !== "queued" && turn.status !== "generating") {
        return turn;
      }

      let turnChanged = false;
      const images = turn.images.map((image) => {
        if (image.status !== "loading" || image.taskId) {
          return image;
        }
        turnChanged = true;
        return {
          ...image,
          status: "error" as const,
          error: "页面刷新或任务中断，未找到可恢复的任务 ID",
        };
      });
      const derived = deriveTurnStatus({ ...turn, images });
      if (!turnChanged && derived.status === turn.status && derived.error === turn.error) {
        return turn;
      }
      changed = true;
      return {
        ...turn,
        ...derived,
        images,
      };
    });

    if (!turns.some((turn, index) => turn !== conversation.turns[index])) {
      return conversation;
    }

    return {
      ...conversation,
      turns,
      updatedAt: new Date().toISOString(),
    };
  });

  if (changed) {
    await saveImageConversations(normalized);
  }

  return syncConversationImageTasks(normalized);
}


function ImagePageContent({
  isAdmin,
  isCompactUserView,
  initialTokenName,
}: {
  isAdmin: boolean;
  isCompactUserView: boolean;
  initialTokenName: string;
}) {
  const didLoadQuotaRef = useRef(false);
  const conversationsRef = useRef<ImageConversation[]>([]);
  const conversationSummariesRef = useRef<ImageConversationSummary[]>([]);
  const resultsViewportRef = useRef<HTMLDivElement>(null);
  const shouldStickToBottomRef = useRef(true);
  const forceAutoScrollRef = useRef(false);
  const previousSelectedConversationIdRef = useRef<string | null>(null);
  const selectedConversationIdRef = useRef<string | null>(null);
  const loadingConversationDetailIdRef = useRef<string | null>(null);
  const draftModeRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const promptApplyRequestIdRef = useRef(0);

  const [imagePrompt, setImagePrompt] = useState("");
  const [imageCount, setImageCount] = useState("1");
  const [imageSize, setImageSize] = useState("");
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [isPromptMarketOpen, setIsPromptMarketOpen] = useState(false);
  const [referenceImageFiles, setReferenceImageFiles] = useState<File[]>([]);
  const [referenceImages, setReferenceImages] = useState<StoredReferenceImage[]>([]);
  const [conversations, setConversations] = useState<ImageConversation[]>([]);
  const [conversationSummaries, setConversationSummaries] = useState<ImageConversationSummary[]>([]);
  const [isLoadingConversationDetail, setIsLoadingConversationDetail] = useState(false);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [composerExpandSignal, setComposerExpandSignal] = useState(0);
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [availableQuota, setAvailableQuota] = useState("加载中...");
  const [tokenName, setTokenName] = useState(initialTokenName || "-");
  const [publicConfig, setPublicConfig] = useState<PublicConfig | null>(null);
  const [runtimeStats, setRuntimeStats] = useState<ImageTaskRuntime | null>(null);
  const [recentQuotaUsageText, setRecentQuotaUsageText] = useState("");
  const [lightboxImages, setLightboxImages] = useState<ImageLightboxItem[]>([]);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [deleteConfirm, setDeleteConfirm] = useState<
    | { type: "one"; id: string }
    | { type: "prompt"; conversationId: string; turnId: string }
    | { type: "results"; conversationId: string; turnId: string }
    | { type: "all" }
    | null
  >(null);

  const parsedCount = useMemo(() => Number(clampImageCount(imageCount)), [imageCount]);
  const batchLimit = useMemo(
    () => Math.max(1, Number(publicConfig?.image_batch_limit || runtimeStats?.frontend_batch_limit || 3) || 3),
    [publicConfig?.image_batch_limit, runtimeStats?.frontend_batch_limit],
  );
  const effectiveParsedCount = useMemo(() => Number(clampImageCountWithLimit(imageCount, batchLimit)), [batchLimit, imageCount]);
  const selectedConversation = useMemo(
    () => conversations.find((item) => item.id === selectedConversationId) ?? null,
    [conversations, selectedConversationId],
  );
  const activeTaskCount = useMemo(
    () =>
      conversations.reduce((sum, conversation) => {
        const stats = getImageConversationStats(conversation);
        return sum + stats.queued + stats.running;
      }, 0),
    [conversations],
  );

  const mergeConversationSummaries = useCallback((items: ImageConversation[] | ImageConversationSummary[]) => {
    if (items.length === 0) {
      return;
    }
    let changed = false;
    const summaryMap = new Map(conversationSummariesRef.current.map((item) => [item.id, item]));
    items.forEach((item) => {
      const summary = "turns" in item ? summarizeImageConversation(item) : item;
      const current = summaryMap.get(summary.id);
      if (
        !current ||
        current.updatedAt !== summary.updatedAt ||
        current.title !== summary.title ||
        current.turnCount !== summary.turnCount ||
        current.queuedCount !== summary.queuedCount ||
        current.runningCount !== summary.runningCount
      ) {
        summaryMap.set(summary.id, summary);
        changed = true;
      }
    });
    if (!changed) {
      return;
    }
    const summaries = Array.from(summaryMap.values()).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    conversationSummariesRef.current = summaries;
    setConversationSummaries(summaries);
  }, []);

  const ensureConversationDetailLoaded = useCallback(
    async (conversationId: string | null, options: { silent?: boolean } = {}) => {
      const normalizedConversationId = conversationId ? String(conversationId).trim() || null : null;
      if (!normalizedConversationId) {
        return null;
      }
      const existing = conversationsRef.current.find((item) => item.id === normalizedConversationId) ?? null;
      if (existing) {
        return existing;
      }
      if (!options.silent) {
        loadingConversationDetailIdRef.current = normalizedConversationId;
        setIsLoadingConversationDetail(true);
      }
      try {
        const item = await fetchImageConversation(normalizedConversationId);
        if (!item) {
          return null;
        }
        const currentItem = conversationsRef.current.find((conversation) => conversation.id === item.id) ?? null;
        if (currentItem === item) {
          mergeConversationSummaries([item]);
          return item;
        }
        const nextConversations = sortImageConversations([
          item,
          ...conversationsRef.current.filter((conversation) => conversation.id !== item.id),
        ]);
        conversationsRef.current = nextConversations;
        setConversations(nextConversations);
        mergeConversationSummaries([item]);
        return item;
      } finally {
        if (!options.silent && loadingConversationDetailIdRef.current === normalizedConversationId) {
          loadingConversationDetailIdRef.current = null;
          setIsLoadingConversationDetail(false);
        }
      }
    },
    [mergeConversationSummaries],
  );
  const systemEstimatedWaitText = useMemo(
    () => formatRuntimeDuration(Number(runtimeStats?.estimated_wait_ms || 0)),
    [runtimeStats?.estimated_wait_ms],
  );
  const systemAverageDurationText = useMemo(
    () => formatRuntimeDuration(Number(runtimeStats?.recent_avg_duration_ms || 0)),
    [runtimeStats?.recent_avg_duration_ms],
  );

  const persistActiveConversationSelection = useCallback((conversationId: string | null, draft = false) => {
    if (typeof window === "undefined") {
      return;
    }
    const storage = getActiveConversationStorage();
    const shouldClearLegacy = storage && storage !== window.localStorage;
    if (conversationId) {
      storage?.setItem(ACTIVE_CONVERSATION_STORAGE_KEY, conversationId);
      if (shouldClearLegacy) {
        clearLegacyActiveConversationSelection();
      }
      return;
    }
    if (draft) {
      storage?.setItem(ACTIVE_CONVERSATION_STORAGE_KEY, DRAFT_CONVERSATION_STORAGE_VALUE);
      if (shouldClearLegacy) {
        clearLegacyActiveConversationSelection();
      }
      return;
    }
    storage?.removeItem(ACTIVE_CONVERSATION_STORAGE_KEY);
    if (shouldClearLegacy || storage === window.localStorage) {
      clearLegacyActiveConversationSelection();
    }
  }, []);

  const setConversationSelection = useCallback((conversationId: string | null, options: { draft?: boolean } = {}) => {
    const normalizedConversationId = conversationId ? String(conversationId).trim() || null : null;
    const isDraft = options.draft === true && !normalizedConversationId;
    draftModeRef.current = isDraft;
    selectedConversationIdRef.current = normalizedConversationId;
    setSelectedConversationId(normalizedConversationId);
    persistActiveConversationSelection(normalizedConversationId, isDraft);
  }, [persistActiveConversationSelection]);

  const handleSelectConversation = useCallback(
    (conversationId: string) => {
      const normalizedConversationId = String(conversationId || "").trim();
      if (!normalizedConversationId) {
        return;
      }
      setConversationSelection(normalizedConversationId);
      void ensureConversationDetailLoaded(normalizedConversationId);
    },
    [ensureConversationDetailLoaded, setConversationSelection],
  );

  const applyConversationSummaries = useCallback(
    (items: ImageConversationSummary[], options: { background?: boolean } = {}) => {
      conversationSummariesRef.current = items;
      setConversationSummaries(items);
      const storedConversationId = readPersistedActiveConversationSelection();
      const storedDraftSelection = storedConversationId === DRAFT_CONVERSATION_STORAGE_VALUE;
      const nextSelectedConversationId =
        (storedConversationId && items.some((conversation) => conversation.id === storedConversationId)
          ? storedConversationId
          : null) ?? items[0]?.id ?? null;
      const currentSelection = selectedConversationIdRef.current;
      const currentSelectionStillExists = Boolean(currentSelection && items.some((item) => item.id === currentSelection));
      if (storedDraftSelection) {
        setConversationSelection(null, { draft: true });
        return;
      }
      if (draftModeRef.current) {
        return;
      }
      if (!options.background || (!currentSelection && !draftModeRef.current) || !currentSelectionStillExists) {
        setConversationSelection(nextSelectedConversationId);
      }
    },
    [setConversationSelection],
  );

  const handleRefreshConversations = useCallback(async () => {
    setIsLoadingHistory(true);
    try {
      const items = await listImageConversations({ forceRemote: true });
      applyConversationSummaries(items, { background: true });
      const activeId = selectedConversationIdRef.current;
      if (activeId && items.some((item) => item.id === activeId)) {
        await ensureConversationDetailLoaded(activeId, { silent: true });
      }
      toast.success(`已刷新当前登录人的 ${items.length} 个会话`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "刷新会话失败";
      toast.error(message);
    } finally {
      setIsLoadingHistory(false);
    }
  }, [applyConversationSummaries, ensureConversationDetailLoaded]);
  const deleteConfirmTitle =
    deleteConfirm?.type === "all"
      ? "清空历史记录"
      : deleteConfirm?.type === "prompt"
        ? "删除提示词记录"
        : deleteConfirm?.type === "results"
          ? "删除生成结果"
          : deleteConfirm?.type === "one"
            ? "删除对话"
            : "";
  const deleteConfirmDescription =
    deleteConfirm?.type === "all"
      ? "确认删除全部图片历史记录吗？删除后无法恢复。"
      : deleteConfirm?.type === "prompt"
        ? "确认删除这条提示词记录吗？对应生成结果会保留。"
        : deleteConfirm?.type === "results"
          ? "确认删除这条生成结果吗？对应提示词记录会保留。"
          : deleteConfirm?.type === "one"
            ? "确认删除这条图片对话吗？删除后无法恢复。"
            : "";

  useEffect(() => {
    conversationsRef.current = conversations;
  }, [conversations]);

  useEffect(() => {
    selectedConversationIdRef.current = selectedConversationId;
  }, [selectedConversationId]);

  useEffect(() => {
    if (!selectedConversationId || draftModeRef.current) {
      loadingConversationDetailIdRef.current = null;
      setIsLoadingConversationDetail(false);
      return;
    }
    let cancelled = false;
    void (async () => {
      const detail = await ensureConversationDetailLoaded(selectedConversationId);
      if (!detail || cancelled) {
        return;
      }
      const recovered = await recoverConversationHistory([detail]);
      if (cancelled || recovered.length === 0) {
        return;
      }
      const item = recovered[0];
      const nextConversations = sortImageConversations([
        item,
        ...conversationsRef.current.filter((conversation) => conversation.id !== item.id),
      ]);
      conversationsRef.current = nextConversations;
      setConversations(nextConversations);
      mergeConversationSummaries([item]);
    })();
    return () => {
      cancelled = true;
    };
  }, [ensureConversationDetailLoaded, mergeConversationSummaries, selectedConversationId]);

  useEffect(() => {
    let cancelled = false;

    const loadHistory = async () => {
      try {
        const storedSize = typeof window !== "undefined" ? window.localStorage.getItem(IMAGE_SIZE_STORAGE_KEY) : null;
        setImageSize(storedSize || "");
        setImageCount("1");

        const items = await listImageConversations();
        if (!cancelled) {
          applyConversationSummaries(items);
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : "读取会话记录失败";
        toast.error(message);
      } finally {
        if (!cancelled) {
          setIsLoadingHistory(false);
        }
      }
    };

    const unsubscribeSync = subscribeImageConversationSync((items, summaries) => {
      if (summaries?.length) {
        mergeConversationSummaries(summaries);
      } else if (items.length > 0) {
        mergeConversationSummaries(items);
      }
      if (items.length === 0) {
        return;
      }
      const selectedId = selectedConversationIdRef.current;
      const selectedItem = selectedId ? items.find((item) => item.id === selectedId) : null;
      if (!selectedItem) {
        return;
      }
      const currentSelected = conversationsRef.current.find((item) => item.id === selectedItem.id);
      if (currentSelected === selectedItem) {
        return;
      }
      const nextConversations = sortImageConversations([
        selectedItem,
        ...conversationsRef.current.filter((item) => item.id !== selectedItem.id),
      ]);
      conversationsRef.current = nextConversations;
      setConversations(nextConversations);
    });
    void loadHistory();
    return () => {
      cancelled = true;
      unsubscribeSync();
    };
  }, [applyConversationSummaries, mergeConversationSummaries]);

  const loadQuota = useCallback(async () => {
    try {
      if (!isAdmin) {
        const data = await fetchCurrentUser();
        setTokenName(data.name || initialTokenName || "-");
      setAvailableQuota(data.quota == null ? "不限" : String(Math.max(0, Number(data.quota) || 0)));
        return;
      }
      fetchCurrentUser()
        .then((data) => setTokenName(data.name || initialTokenName || "-"))
        .catch(() => undefined);
      const data = await fetchAccountSummary();
      setAvailableQuota(formatAvailableQuotaSummary(data));
    } catch {
      setAvailableQuota((prev) => (prev === "加载中..." ? "--" : prev));
    }
  }, [initialTokenName, isAdmin]);
  useEffect(() => {
    let active = true;
    fetchPublicConfig()
      .then((config) => {
        if (!active) return;
        setPublicConfig(config);
        if (config.page_title) {
          document.title = config.page_title;
        }
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    let timer: number | null = null;

    const loadRuntime = async () => {
      try {
        const runtime = await fetchImageTaskRuntime();
        if (active) {
          setRuntimeStats(runtime);
        }
      } catch {
        // ignore transient runtime fetch errors
      }
    };

    void loadRuntime();
    timer = window.setInterval(() => {
      void loadRuntime();
    }, 8000);
    window.addEventListener("focus", loadRuntime);
    return () => {
      active = false;
      if (timer != null) {
        window.clearInterval(timer);
      }
      window.removeEventListener("focus", loadRuntime);
    };
  }, []);

  useEffect(() => {
    if (didLoadQuotaRef.current) {
      return;
    }
    didLoadQuotaRef.current = true;

    const handleFocus = () => {
      void loadQuota();
    };

    void loadQuota();
    window.addEventListener("focus", handleFocus);
    return () => {
      window.removeEventListener("focus", handleFocus);
    };
  }, [isAdmin, loadQuota]);

  useEffect(() => {
    const viewport = resultsViewportRef.current;
    if (!viewport) {
      return;
    }

    const updateStickiness = () => {
      const distanceToBottom = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
      shouldStickToBottomRef.current = distanceToBottom <= AUTO_SCROLL_BOTTOM_THRESHOLD;
    };

    updateStickiness();
    viewport.addEventListener("scroll", updateStickiness, { passive: true });
    return () => {
      viewport.removeEventListener("scroll", updateStickiness);
    };
  }, [selectedConversationId]);

  useEffect(() => {
    if (previousSelectedConversationIdRef.current !== selectedConversationId) {
      previousSelectedConversationIdRef.current = selectedConversationId;
      forceAutoScrollRef.current = true;
      shouldStickToBottomRef.current = true;
    }
  }, [selectedConversationId]);

  useEffect(() => {
    if (!selectedConversation) {
      return;
    }

    const viewport = resultsViewportRef.current;
    if (!viewport) {
      return;
    }
    if (!forceAutoScrollRef.current && !shouldStickToBottomRef.current) {
      return;
    }

    viewport.scrollTo({
      top: viewport.scrollHeight,
      behavior: forceAutoScrollRef.current ? "auto" : "smooth",
    });
    shouldStickToBottomRef.current = true;
    forceAutoScrollRef.current = false;
  }, [selectedConversation?.updatedAt, selectedConversation?.turns.length, selectedConversation]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    if (imageSize) {
      window.localStorage.setItem(IMAGE_SIZE_STORAGE_KEY, imageSize);
      return;
    }
    window.localStorage.removeItem(IMAGE_SIZE_STORAGE_KEY);
  }, [imageSize]);

  useEffect(() => {
    if (typeof window !== "undefined" && effectiveParsedCount > 0) {
      window.localStorage.setItem(IMAGE_COUNT_STORAGE_KEY, String(effectiveParsedCount));
    }
  }, [effectiveParsedCount]);

  useEffect(() => {
    setImageCount((current) => clampImageCountWithLimit(current || "1", batchLimit));
  }, [batchLimit]);

  useEffect(() => {
    if (draftModeRef.current) {
      return;
    }
    if (
      selectedConversationId &&
      !conversations.some((conversation) => conversation.id === selectedConversationId) &&
      !conversationSummaries.some((conversation) => conversation.id === selectedConversationId) &&
      !isLoadingConversationDetail &&
      loadingConversationDetailIdRef.current !== selectedConversationId
    ) {
      setConversationSelection(pickFallbackConversationId(conversations));
    }
  }, [conversationSummaries, conversations, isLoadingConversationDetail, selectedConversationId, setConversationSelection]);

  const persistConversation = async (conversation: ImageConversation) => {
    const nextConversations = sortImageConversations([
      conversation,
      ...conversationsRef.current.filter((item) => item.id !== conversation.id),
    ]);
    conversationsRef.current = nextConversations;
    setConversations(nextConversations);
    mergeConversationSummaries([conversation]);
    await saveImageConversation(conversation);
  };

  const updateConversation = useCallback(
    async (
      conversationId: string,
      updater: (current: ImageConversation | null) => ImageConversation,
      options: { persist?: boolean } = {},
    ) => {
      const current = conversationsRef.current.find((item) => item.id === conversationId) ?? null;
      const nextConversation = updater(current);
      const nextConversations = sortImageConversations([
        nextConversation,
        ...conversationsRef.current.filter((item) => item.id !== conversationId),
      ]);
      conversationsRef.current = nextConversations;
      setConversations(nextConversations);
      mergeConversationSummaries([nextConversation]);
      if (options.persist !== false) {
        await saveImageConversation(nextConversation);
      }
    },
    [],
  );

  const clearComposerInputs = useCallback(() => {
    promptApplyRequestIdRef.current += 1;
    setImagePrompt("");
    setReferenceImageFiles([]);
    setReferenceImages([]);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }, []);

  const resetComposer = useCallback(() => {
    clearComposerInputs();
  }, [clearComposerInputs]);

  const handleCreateDraft = () => {
    setConversationSelection(null, { draft: true });
    resetComposer();
    setComposerExpandSignal((value) => value + 1);
    textareaRef.current?.focus();
  };

  const handleDeleteConversation = async (id: string) => {
    const nextConversations = conversations.filter((item) => item.id !== id);
    conversationsRef.current = nextConversations;
    setConversations(nextConversations);
    const summaries = nextConversations.map((item) => summarizeImageConversation(item));
    conversationSummariesRef.current = summaries;
    setConversationSummaries(summaries);
    if (selectedConversationId === id) {
      setConversationSelection(pickFallbackConversationId(nextConversations));
      resetComposer();
    }

    try {
      await deleteImageConversation(id);
    } catch (error) {
      const message = error instanceof Error ? error.message : "删除会话失败";
      toast.error(message);
      const items = await listImageConversations();
      applyConversationSummaries(items, { background: true });
    }
  };

  const handleDeleteTurnPart = async (conversationId: string, turnId: string, part: "prompt" | "results") => {
    const conversation = conversationsRef.current.find((item) => item.id === conversationId);
    if (!conversation) {
      return;
    }

    const turns = conversation.turns
      .map((turn) => {
        if (turn.id !== turnId) {
          return turn;
        }
        const nextTurn = {
          ...turn,
          prompt: part === "prompt" ? "" : turn.prompt,
          promptDeleted: part === "prompt" ? true : turn.promptDeleted,
          resultsDeleted: part === "results" ? true : turn.resultsDeleted,
          status: part === "results" && turn.status === "generating" ? "error" as const : turn.status,
          images:
            part === "results"
              ? turn.images.map((image) => ({ id: image.id, status: "error" as const, error: "生成结果已删除" }))
              : turn.images,
        };
        return nextTurn.promptDeleted && nextTurn.resultsDeleted ? null : nextTurn;
      })
      .filter((turn): turn is ImageTurn => Boolean(turn));

    if (turns.length === 0) {
      await handleDeleteConversation(conversationId);
      return;
    }

    const nextConversation = {
      ...conversation,
      updatedAt: new Date().toISOString(),
      turns,
    };
    await persistConversation(nextConversation);
  };

  const handleClearHistory = async () => {
    try {
      await clearImageConversations();
      conversationsRef.current = [];
      setConversations([]);
      conversationSummariesRef.current = [];
      setConversationSummaries([]);
      setConversationSelection(null, { draft: true });
      resetComposer();
      toast.success("已清空历史记录");
    } catch (error) {
      const message = error instanceof Error ? error.message : "清空历史记录失败";
      toast.error(message);
    }
  };

  const handleRenameConversation = async (id: string, title: string) => {
    const nextConversations = conversations.map((item) =>
      item.id === id ? { ...item, title, updatedAt: new Date().toISOString() } : item,
    );
    conversationsRef.current = sortImageConversations(nextConversations);
    setConversations(conversationsRef.current);
    try {
      await renameImageConversation(id, title);
    } catch (error) {
      const message = error instanceof Error ? error.message : "重命名失败";
      toast.error(message);
    }
  };

  const openDeleteConversationConfirm = (id: string) => {
    setIsHistoryOpen(false);
    setDeleteConfirm({ type: "one", id });
  };

  const openDeletePromptConfirm = (conversationId: string, turnId: string) => {
    setDeleteConfirm({ type: "prompt", conversationId, turnId });
  };

  const openDeleteResultsConfirm = (conversationId: string, turnId: string) => {
    setDeleteConfirm({ type: "results", conversationId, turnId });
  };

  const openClearHistoryConfirm = () => {
    setIsHistoryOpen(false);
    setDeleteConfirm({ type: "all" });
  };

  const handleConfirmDelete = async () => {
    const target = deleteConfirm;
    setDeleteConfirm(null);
    if (!target) {
      return;
    }
    if (target.type === "all") {
      await handleClearHistory();
      return;
    }
    if (target.type === "prompt" || target.type === "results") {
      await handleDeleteTurnPart(target.conversationId, target.turnId, target.type);
      return;
    }
    await handleDeleteConversation(target.id);
  };

  const appendReferenceImages = useCallback(async (files: File[]) => {
    if (files.length === 0) {
      return;
    }
    promptApplyRequestIdRef.current += 1;

    try {
        const prepared = await Promise.all(files.map((file) => optimizeReferenceFile(file)));

        setReferenceImageFiles((prev) => [...prev, ...prepared.map((item) => item.file)]);
        setReferenceImages((prev) => [...prev, ...prepared.map((item) => item.referenceImage)]);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "读取参考图失败";
      toast.error(message);
    }
  }, []);

  const handleReferenceImageChange = useCallback(
    async (files: File[]) => {
      if (files.length === 0) {
        return;
      }

      await appendReferenceImages(files);
    },
    [appendReferenceImages],
  );

  const handleApplyMarketPrompt = useCallback(async (prompt: BananaPrompt) => {
    const referenceImageUrls = getPromptReferenceImageUrls(prompt);
    const requestId = promptApplyRequestIdRef.current + 1;
    promptApplyRequestIdRef.current = requestId;

    setConversationSelection(null, { draft: true });
    setImagePrompt(prompt.prompt);
    setImageCount("1");
    setImageSize("");
    setReferenceImageFiles([]);
    setReferenceImages([]);
    setIsPromptMarketOpen(false);
    setComposerExpandSignal((value) => value + 1);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
    textareaRef.current?.focus();

    if (referenceImageUrls.length === 0) {
      toast.success("已套用提示词");
      return;
    }

    const toastId = toast.loading(`正在读取 ${referenceImageUrls.length} 张参考图`);
    const results = await Promise.allSettled(
      referenceImageUrls.map((url, index) => buildReferenceImageFromUrl(url, index, "prompt-reference")),
    );
    const loadedReferences = results.flatMap((result) => (result.status === "fulfilled" ? [result.value] : []));

    toast.dismiss(toastId);
    if (promptApplyRequestIdRef.current !== requestId) {
      return;
    }
    if (loadedReferences.length > 0) {
      setReferenceImages(loadedReferences.map((item) => item.referenceImage));
      setReferenceImageFiles(loadedReferences.map((item) => item.file));
      toast.success(
        loadedReferences.length === referenceImageUrls.length
          ? "已套用提示词和参考图"
          : `已套用提示词，成功读取 ${loadedReferences.length}/${referenceImageUrls.length} 张参考图`,
      );
    } else {
      toast.warning("已套用提示词，但参考图读取失败，可直接文生图或手动上传参考图");
    }
  }, [setConversationSelection]);

  const handleRemoveReferenceImage = useCallback((index: number) => {
    setReferenceImageFiles((prev) => {
      const next = prev.filter((_, currentIndex) => currentIndex !== index);
      if (next.length === 0 && fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      return next;
    });
    setReferenceImages((prev) => prev.filter((_, currentIndex) => currentIndex !== index));
  }, []);

  const handleContinueEdit = useCallback(
    async (conversationId: string, image: StoredImage | StoredReferenceImage) => {
      try {
        const nextReference =
          "dataUrl" in image
            ? {
                referenceImage: image,
                file: dataUrlToFile(image.dataUrl, image.name, image.type),
              }
            : await buildReferenceImageFromStoredImage(image, `conversation-${conversationId}-${Date.now()}.png`);
        if (!nextReference) {
          return;
        }

        setConversationSelection(conversationId);

        setReferenceImages((prev) => [...prev, nextReference.referenceImage]);
        setReferenceImageFiles((prev) => [...prev, nextReference.file]);
        setImagePrompt("");
        textareaRef.current?.focus();
        toast.success("已加入当前参考图，继续输入描述即可编辑");
      } catch (error) {
        const message = error instanceof Error ? error.message : "读取结果图失败";
        toast.error(message);
      }
    },
    [],
  );

  const handleReuseTurnConfig = useCallback(async (conversationId: string, turnId: string) => {
    const conversation = conversationsRef.current.find((item) => item.id === conversationId);
    const turn = conversation?.turns.find((item) => item.id === turnId);
    if (!conversation || !turn || !turn.prompt.trim()) {
      return;
    }

    setConversationSelection(conversationId);
    setImagePrompt(turn.prompt);
    setImageCount(String(Math.max(1, turn.count || turn.images.length || 1)));
    setImageSize(turn.size);
    setReferenceImages(turn.referenceImages);
    setReferenceImageFiles(
      turn.referenceImages.map((image) => dataUrlToFile(image.dataUrl, image.name, image.type)),
    );
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
    textareaRef.current?.focus();
    toast.success("已复用这条提示词配置");
  }, []);

  const openLightbox = useCallback((images: ImageLightboxItem[], index: number) => {
    if (images.length === 0) {
      return;
    }

    setLightboxImages(images);
    setLightboxIndex(Math.max(0, Math.min(index, images.length - 1)));
    setLightboxOpen(true);
  }, []);

  const createLoadingImages = (turnId: string, count: number) =>
    Array.from({ length: count }, (_, index) => {
      const imageId = `${turnId}-${index}`;
      return {
        id: imageId,
        taskId: imageId,
        status: "loading" as const,
      };
    });

  const buildQuotaUsageSummary = useCallback((success: number, failed: number) => {
    const safeSuccess = Math.max(0, Number(success) || 0);
    const safeFailed = Math.max(0, Number(failed) || 0);
    if (safeSuccess > 0 && safeFailed > 0) {
      return `本次成功 ${safeSuccess} 张，失败 ${safeFailed} 张；实际扣减 ${safeSuccess} 张，已返还 ${safeFailed} 张`;
    }
    if (safeSuccess > 0) {
      return `本次成功 ${safeSuccess} 张；实际扣减 ${safeSuccess} 张`;
    }
    if (safeFailed > 0) {
      return `本次全部失败 ${safeFailed} 张；已返还 ${safeFailed} 张`;
    }
    return "";
  }, []);

  /* eslint-disable react-hooks/preserve-manual-memoization */
  const runConversationQueue = useCallback(
    async (conversationId: string) => {
      if (activeConversationQueueIds.has(conversationId)) {
        return;
      }
      if (!acquireConversationQueueLock(conversationId)) {
        return;
      }

      const snapshot = conversationsRef.current.find((conversation) => conversation.id === conversationId);
      const activeTurn = snapshot?.turns.find(
        (turn) =>
          (turn.status === "queued" || turn.status === "generating") &&
          turn.images.some((image) => image.status === "loading"),
      );
      if (!snapshot || !activeTurn) {
        releaseConversationQueueLock(conversationId);
        return;
      }

      activeConversationQueueIds.add(conversationId);
      let hasFailedImagesInCurrentRun = false;
      let quotaFailureToastShown = false;
      const getTurnOutcomeSummary = () => {
        const latestConversation = conversationsRef.current.find((conversation) => conversation.id === conversationId);
        const latestTurn = latestConversation?.turns.find((turn) => turn.id === activeTurn.id);
        const images = latestTurn?.images || [];
        const success = images.filter((image) => image.status === "success").length;
        const failed = images.filter((image) => image.status === "error").length;
        const loading = images.filter((image) => image.status === "loading").length;
        return { success, failed, loading };
      };
      const notifyQuotaRefundResult = () => {
        if (quotaFailureToastShown || !hasFailedImagesInCurrentRun) {
          return;
        }
        const { success, failed } = getTurnOutcomeSummary();
        if (failed <= 0) {
          return;
        }
        quotaFailureToastShown = true;
        if (success > 0) {
          setRecentQuotaUsageText(buildQuotaUsageSummary(success, failed));
          toast.warning(`本轮 ${success} 张成功，${failed} 张失败；仅扣成功张数，失败额度已自动返还`);
          return;
        }
        setRecentQuotaUsageText(buildQuotaUsageSummary(success, failed));
        toast.warning("本轮图片全部生成失败；额度已全部返还");
      };
      const applyTasks = async (tasks: ImageTask[]) => {
        refreshConversationQueueLock(conversationId);
        const taskMap = new Map(tasks.map((task) => [task.id, task]));
        if (tasks.some((task) => task.status === "error")) {
          hasFailedImagesInCurrentRun = true;
        }
        await updateConversation(conversationId, (current) => {
          const conversation = current ?? snapshot;
          let turnChanged = false;
          const turns = conversation.turns.map((turn) => {
            if (turn.id !== activeTurn.id) {
              return turn;
            }
            let imageChanged = false;
            const images = turn.images.map((image) => {
              const taskId = image.taskId || image.id;
              const task = taskMap.get(taskId);
              const nextImage = task ? taskDataToStoredImage({ ...image, taskId }, task) : image;
              if (nextImage !== image) {
                imageChanged = true;
              }
              return nextImage;
            });
            const derived = deriveTurnStatus({ ...turn, status: "generating", images });
            const nextTurn = {
              ...turn,
              ...derived,
              images,
            };
            if (
              imageChanged ||
              nextTurn.status !== turn.status ||
              nextTurn.error !== turn.error ||
              nextTurn.images !== turn.images
            ) {
              turnChanged = true;
              return nextTurn;
            }
            return turn;
          });
          if (!turnChanged) {
            return conversation;
          }
          return {
            ...conversation,
            updatedAt: new Date().toISOString(),
            turns,
          };
        });
      };

      const missingTaskRetryCounts = new Map<string, number>();
      const markImagesError = async (errors: Array<{ image: StoredImage; error: unknown }>) => {
        refreshConversationQueueLock(conversationId);
        if (errors.length === 0) {
          return;
        }
        hasFailedImagesInCurrentRun = true;
        await updateConversation(conversationId, (current) => {
          const conversation = current ?? snapshot;
          const errorMap = new Map(errors.map(({ image, error }) => [image.id, friendlyImageError(error)]));
          let turnChanged = false;
          const turns = conversation.turns.map((turn) => {
            if (turn.id !== activeTurn.id) {
              return turn;
            }
            let imageChanged = false;
            const images = turn.images.map((image) => {
              if (!errorMap.has(image.id)) {
                return image;
              }
              imageChanged = true;
              return {
                ...image,
                status: "error" as const,
                error: errorMap.get(image.id),
              };
            });
            const derived = deriveTurnStatus({ ...turn, status: "generating", images });
            const nextTurn = {
              ...turn,
              ...derived,
              images,
            };
            if (
              imageChanged ||
              nextTurn.status !== turn.status ||
              nextTurn.error !== turn.error ||
              nextTurn.images !== turn.images
            ) {
              turnChanged = true;
              return nextTurn;
            }
            return turn;
          });
          if (!turnChanged) {
            return conversation;
          }
          return {
            ...conversation,
            updatedAt: new Date().toISOString(),
            turns,
          };
        });
      };

      const submitTaskWithRetry = async (image: StoredImage, referenceFiles: File[]) => {
        const taskId = image.taskId || image.id;
        let lastError: unknown = null;
        for (let attempt = 0; attempt <= TASK_SUBMIT_RETRY; attempt += 1) {
          try {
            return activeTurn.mode === "edit"
              ? await createImageEditTask(taskId, referenceFiles, activeTurn.prompt, activeTurn.model, activeTurn.size)
              : await createImageGenerationTask(taskId, activeTurn.prompt, activeTurn.model, activeTurn.size);
          } catch (error) {
            lastError = error;
            if (!isRetryableTaskError(error) || attempt >= TASK_SUBMIT_RETRY) {
              throw error;
            }
            await sleep(800 * (attempt + 1));
          }
        }
        throw lastError || new Error("提交图片任务失败");
      };

      const submitPendingTasks = async (images: StoredImage[], referenceFiles: File[]) => {
        const results = await runWithConcurrency(
          images,
          activeTurn.mode === "edit" ? EDIT_TASK_SUBMIT_CONCURRENCY : GENERATE_TASK_SUBMIT_CONCURRENCY,
          (image) => submitTaskWithRetry(image, referenceFiles),
        );
        const submitted = results.flatMap((result) => (result.value ? [result.value] : []));
        const failed = results.flatMap((result) => (result.error ? [{ image: result.item, error: result.error }] : []));
        const terminalFailed = failed.filter((result) => !isRetryableTaskError(result.error));
        const retryableFailed = failed.length - terminalFailed.length;
        if (submitted.length > 0) {
          await applyTasks(submitted);
        }
        if (terminalFailed.length > 0) {
          await markImagesError(terminalFailed);
        }
        if (retryableFailed > 0) {
          toast.warning("网络临时中断，图片任务会继续自动查询，请稍等");
        }
        return submitted;
      };

      try {
        await updateConversation(conversationId, (current) => {
          const conversation = current ?? snapshot;
          return {
            ...conversation,
            updatedAt: new Date().toISOString(),
            turns: conversation.turns.map((turn) =>
              turn.id === activeTurn.id
                ? {
                    ...turn,
                    status: "generating",
                    error: undefined,
                    images: turn.images.map((image) =>
                      image.status === "loading" ? { ...image, taskId: image.taskId || image.id } : image,
                    ),
                  }
                : turn,
            ),
          };
        });

        const referenceFiles = activeTurn.referenceImages.map((image, index) =>
          dataUrlToFile(image.dataUrl, image.name || `${activeTurn.id}-${index + 1}.png`, image.type),
        );
        const canSubmitMissingEditTasks = activeTurn.mode !== "edit" || referenceFiles.length > 0;
        let pendingImages = activeTurn.images.filter((image) => image.status === "loading");
        if (pendingImages.length > 0 && canSubmitMissingEditTasks) {
          refreshConversationQueueLock(conversationId);
          await submitPendingTasks(pendingImages, referenceFiles);
          await loadQuota();
        }

        let pollFailureCount = 0;
        while (true) {
          refreshConversationQueueLock(conversationId);
          const latestConversation = conversationsRef.current.find((conversation) => conversation.id === conversationId);
          const latestTurn = latestConversation?.turns.find((turn) => turn.id === activeTurn.id);
          const loadingTaskIds = collectPendingTaskIdsFromTurn(latestTurn);
          pendingImages = latestTurn?.images.filter((image) => image.status === "loading") ?? [];
          if (pendingImages.length === 0) {
            break;
          }
          if (loadingTaskIds.length === 0) {
            await markImagesError(
              pendingImages.map((image) => ({
                image,
                error: "任务缺少后台编号，已自动跳过，请重试这一张",
              })),
            );
            break;
          }

          await sleep(2000);
          let taskList: Awaited<ReturnType<typeof fetchImageTasks>>;
          try {
            taskList = await fetchImageTasks(loadingTaskIds);
            pollFailureCount = 0;
          } catch (error) {
            pollFailureCount += 1;
            if (pollFailureCount < TASK_POLL_RETRY && isRetryableTaskError(error)) {
              continue;
            }
            throw error;
          }
          if (taskList.items.length > 0) {
            await applyTasks(taskList.items);
          }
          if (taskList.missing_ids.length > 0 && latestTurn) {
            const missingImages = latestTurn.images.filter(
              (image) => image.status === "loading" && image.taskId && taskList.missing_ids.includes(image.taskId),
            );
            if (missingImages.length > 0) {
              if (canSubmitMissingEditTasks) {
                const retryableImages: StoredImage[] = [];
                const exhaustedImages: StoredImage[] = [];
                for (const image of missingImages) {
                  const taskId = image.taskId || image.id;
                  const nextCount = (missingTaskRetryCounts.get(taskId) || 0) + 1;
                  missingTaskRetryCounts.set(taskId, nextCount);
                  if (nextCount > MISSING_TASK_REQUEUE_LIMIT) {
                    exhaustedImages.push(image);
                  } else {
                    retryableImages.push(image);
                  }
                }
                if (retryableImages.length > 0) {
                  await submitPendingTasks(retryableImages, referenceFiles);
                }
                if (exhaustedImages.length > 0) {
                  await markImagesError(
                    exhaustedImages.map((image) => ({
                      image,
                      error: "任务提交后未在后台队列中找到，已自动跳过，请重试这一张",
                    })),
                  );
                }
              } else {
                await updateConversation(conversationId, (current) => {
                  const conversation = current ?? snapshot;
                  return {
                    ...conversation,
                    updatedAt: new Date().toISOString(),
                    turns: conversation.turns.map((turn) =>
                      turn.id === activeTurn.id
                        ? {
                            ...turn,
                            ...deriveTurnStatus({
                              ...turn,
                              images: turn.images.map((image) =>
                                image.status === "loading" && image.taskId && taskList.missing_ids.includes(image.taskId)
                                  ? { ...image, status: "error" as const, error: MISSING_EDIT_REFERENCE_ERROR }
                                  : image,
                              ),
                            }),
                            images: turn.images.map((image) =>
                              image.status === "loading" && image.taskId && taskList.missing_ids.includes(image.taskId)
                                ? { ...image, status: "error" as const, error: MISSING_EDIT_REFERENCE_ERROR }
                                : image,
                            ),
                          }
                        : turn,
                    ),
                  };
                });
              }
            }
          }
        }

        await loadQuota();
        const finalSummary = getTurnOutcomeSummary();
        setRecentQuotaUsageText(buildQuotaUsageSummary(finalSummary.success, finalSummary.failed));
        notifyQuotaRefundResult();
      } catch (error) {
        const message = friendlyImageError(error) || "生成图片失败";
        if (isRetryableTaskError(error)) {
          const latestConversation = conversationsRef.current.find((conversation) => conversation.id === conversationId);
          const latestTurn = latestConversation?.turns.find((turn) => turn.id === activeTurn.id);
          const taskIds = collectPendingTaskIdsFromTurn(latestTurn);
          if (taskIds.length > 0) {
            try {
              const taskList = await fetchImageTasks(taskIds);
              if (taskList.items.length > 0) {
                await applyTasks(taskList.items);
              }
              const refreshedConversation = conversationsRef.current.find((conversation) => conversation.id === conversationId);
              const refreshedTurn = refreshedConversation?.turns.find((turn) => turn.id === activeTurn.id);
              const stillLoading = refreshedTurn?.images.some((image) => image.status === "loading") ?? false;
              if (!stillLoading) {
                await loadQuota();
                const finalSummary = getTurnOutcomeSummary();
                setRecentQuotaUsageText(buildQuotaUsageSummary(finalSummary.success, finalSummary.failed));
                notifyQuotaRefundResult();
                return;
              }
            } catch {
              // Do not mark a background image task as failed when only the browser polling request failed.
            }
          }
          toast.warning("网络查询临时失败，任务仍在后台继续，会自动重试同步结果");
          return;
        }
        await updateConversation(conversationId, (current) => {
          const conversation = current ?? snapshot;
          return {
            ...conversation,
            updatedAt: new Date().toISOString(),
            turns: conversation.turns.map((turn) =>
              turn.id === activeTurn.id
                ? {
                    ...turn,
                    status: "error",
                    error: message,
                    images: turn.images.map((image) =>
                      image.status === "loading" ? { ...image, status: "error", error: message } : image,
                    ),
                  }
                : turn,
            ),
          };
        });
        await loadQuota();
        const finalSummary = getTurnOutcomeSummary();
        setRecentQuotaUsageText(buildQuotaUsageSummary(finalSummary.success, finalSummary.failed));
        notifyQuotaRefundResult();
        if (!quotaFailureToastShown) {
          toast.error(`${message}；失败额度已自动返还`);
          quotaFailureToastShown = true;
        }
      } finally {
        activeConversationQueueIds.delete(conversationId);
        releaseConversationQueueLock(conversationId);
        for (const conversation of conversationsRef.current) {
          if (
            !activeConversationQueueIds.has(conversation.id) &&
            conversation.turns.some(
              (turn) =>
                (turn.status === "queued" || turn.status === "generating") &&
                turn.images.some((image) => image.status === "loading"),
            )
          ) {
            void runConversationQueue(conversation.id);
          }
        }
      }
    },
    [buildQuotaUsageSummary, loadQuota, updateConversation],
  );
  /* eslint-enable react-hooks/preserve-manual-memoization */

  const handleRegenerateTurn = useCallback(
    async (conversationId: string, turnId: string) => {
      const conversation = conversationsRef.current.find((item) => item.id === conversationId);
      const sourceTurn = conversation?.turns.find((turn) => turn.id === turnId);
      if (!conversation || !sourceTurn || !sourceTurn.prompt.trim()) {
        return;
      }

      const now = new Date().toISOString();
      const nextTurnId = createId();
      const count = Math.max(1, sourceTurn.count || sourceTurn.images.length || 1);
      const nextTurn: ImageTurn = {
        id: nextTurnId,
        prompt: sourceTurn.prompt,
        model: sourceTurn.model,
        mode: sourceTurn.mode,
        referenceImages: sourceTurn.referenceImages,
        count,
        size: sourceTurn.size,
        images: createLoadingImages(nextTurnId, count),
        createdAt: now,
        status: "queued",
      };
      const nextConversation = {
        ...conversation,
        updatedAt: now,
        turns: [...conversation.turns, nextTurn],
      };

      setConversationSelection(conversationId);
      await persistConversation(nextConversation);
      void runConversationQueue(conversationId);
      toast.success("已加入重新生成队列");
    },
    [runConversationQueue],
  );

  const handleRetryImage = useCallback(
    async (conversationId: string, turnId: string, imageId: string) => {
      const conversation = conversationsRef.current.find((item) => item.id === conversationId);
      if (!conversation) {
        return;
      }

      const now = new Date().toISOString();
      const retryImageId = `${turnId}-${createId()}`;
      const nextConversation = {
        ...conversation,
        updatedAt: now,
        turns: conversation.turns.map((turn) => {
          if (turn.id !== turnId) {
            return turn;
          }
          if (!turn.prompt.trim()) {
            return turn;
          }

          const images = turn.images.map((image) =>
            image.id === imageId
              ? {
                  id: retryImageId,
                  taskId: retryImageId,
                  status: "loading" as const,
                }
              : image,
          );
          const derived = deriveTurnStatus({ ...turn, status: "queued", images });
          return {
            ...turn,
            ...derived,
            images,
          };
        }),
      };

      setConversationSelection(conversationId);
      await persistConversation(nextConversation);
      void runConversationQueue(conversationId);
    },
    [runConversationQueue],
  );

  useEffect(() => {
    for (const conversation of conversations) {
      if (
        !activeConversationQueueIds.has(conversation.id) &&
        conversation.turns.some(
          (turn) =>
            !turn.resultsDeleted &&
            (turn.status === "queued" || turn.status === "generating") &&
            turn.images.some((image) => image.status === "loading"),
        )
      ) {
        void runConversationQueue(conversation.id);
      }
    }
  }, [conversations, runConversationQueue]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const retryPendingQueues = () => {
      for (const conversation of conversationsRef.current) {
        if (
          !activeConversationQueueIds.has(conversation.id) &&
          conversation.turns.some(
            (turn) =>
              !turn.resultsDeleted &&
              (turn.status === "queued" || turn.status === "generating") &&
              turn.images.some((image) => image.status === "loading"),
          )
        ) {
          void runConversationQueue(conversation.id);
        }
      }
    };

    const timer = window.setInterval(retryPendingQueues, CONVERSATION_QUEUE_RETRY_INTERVAL_MS);
    window.addEventListener("focus", retryPendingQueues);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", retryPendingQueues);
    };
  }, [runConversationQueue]);

  const handleSubmit = async () => {
    const prompt = imagePrompt.trim();
    if (!prompt) {
      toast.error("请输入提示词");
      return;
    }

    const effectiveImageMode: ImageConversationMode = referenceImageFiles.length > 0 ? "edit" : "generate";
    setRecentQuotaUsageText("");
    const numericQuota = Number(availableQuota);
    if (!isAdmin && Number.isFinite(numericQuota) && numericQuota >= 0 && effectiveParsedCount > numericQuota) {
      toast.error(`剩余额度不足，当前还剩 ${numericQuota} 张`);
      return;
    }
    if (effectiveParsedCount > batchLimit) {
      toast.error(`单次最多提交 ${batchLimit} 张`);
      setImageCount(String(batchLimit));
      return;
    }

    const targetConversation = selectedConversationId
      ? conversationsRef.current.find((conversation) => conversation.id === selectedConversationId) ?? null
      : null;
    const now = new Date().toISOString();
    const conversationId = targetConversation?.id ?? createId();
    const turnId = createId();
    const draftTurn: ImageTurn = {
      id: turnId,
      prompt,
      model: "gpt-image-2",
      mode: effectiveImageMode,
      referenceImages: effectiveImageMode === "edit" ? referenceImages : [],
      count: effectiveParsedCount,
      size: imageSize,
      images: createLoadingImages(turnId, effectiveParsedCount),
      createdAt: now,
      status: "queued",
    };

    const baseConversation: ImageConversation = targetConversation
      ? {
          ...targetConversation,
          updatedAt: now,
          turns: [...targetConversation.turns, draftTurn],
        }
      : {
          id: conversationId,
          title: buildConversationTitle(prompt),
          createdAt: now,
          updatedAt: now,
          turns: [draftTurn],
        };

    setConversationSelection(conversationId);
    clearComposerInputs();

    await persistConversation(baseConversation);
    void runConversationQueue(conversationId);

    const targetStats = getImageConversationStats(baseConversation);
    if (targetStats.running > 0 || targetStats.queued > 1) {
      toast.success("已加入当前对话队列");
    } else if (!targetConversation) {
      toast.success("已创建新对话并开始处理");
    } else {
      toast.success("已发送到当前对话");
    }
  };

  return (
    <>
      <section className={cn("image-page-shell mx-auto grid min-h-0 w-full max-w-[1380px] grid-cols-1 gap-2 px-0 pb-[calc(env(safe-area-inset-bottom)+0.35rem)] sm:h-[calc(100dvh-5rem)] sm:gap-3 sm:px-3 sm:pb-6", isCompactUserView ? "h-[calc(100dvh-5rem)]" : "h-[calc(100dvh-6.75rem)]")}>
        <div className="image-page-desktop-sidebar hidden h-full min-h-0 border-r border-stone-200/70 pr-3">
          <ImageSidebar
            conversations={conversationSummaries}
            isLoadingHistory={isLoadingHistory}
            selectedConversationId={selectedConversationId}
            onCreateDraft={handleCreateDraft}
            onClearHistory={openClearHistoryConfirm}
            onRefreshConversations={handleRefreshConversations}
            onSelectConversation={handleSelectConversation}
            onDeleteConversation={openDeleteConversationConfirm}
            onRenameConversation={handleRenameConversation}
            formatConversationTime={formatConversationTime}
          />
        </div>

        <Dialog open={isHistoryOpen} onOpenChange={setIsHistoryOpen}>
          <DialogContent className="flex h-[min(88dvh,760px)] w-[92vw] max-w-[460px] flex-col overflow-hidden rounded-[32px] border-white/80 bg-white p-0 shadow-[0_32px_110px_-38px_rgba(15,23,42,0.45)] max-sm:top-auto max-sm:bottom-0 max-sm:left-0 max-sm:h-[min(88dvh,760px)] max-sm:w-full max-sm:translate-x-0 max-sm:translate-y-0 max-sm:rounded-b-none max-sm:rounded-t-[28px] sm:rounded-[36px]">
            <DialogHeader className="px-6 pt-7 pb-4 sm:px-8">
              <DialogTitle className="flex items-center gap-2 text-xl font-bold tracking-tight">
                <History className="size-5" />
                历史记录
              </DialogTitle>
              <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-2 pt-2 sm:hidden">
                <Button
                  className="h-10 min-w-0 rounded-2xl bg-stone-950 text-white shadow-sm"
                  onClick={() => {
                    handleCreateDraft();
                    setIsHistoryOpen(false);
                  }}
                >
                  <Plus className="size-4" />
                  新建对话
                </Button>
                <Button
                  variant="outline"
                  className="h-10 rounded-2xl border-stone-200 bg-white px-3 text-stone-600 shadow-sm"
                  onClick={() => void handleRefreshConversations()}
                  disabled={isLoadingHistory}
                  title="刷新当前登录人的全部会话"
                  aria-label="刷新当前登录人的全部会话"
                >
                  <RefreshCw className={cn("size-4", isLoadingHistory && "animate-spin")} />
                </Button>
                <Button
                  variant="outline"
                  className="h-10 rounded-2xl border-stone-200 bg-white px-3 text-stone-600 shadow-sm"
                  onClick={openClearHistoryConfirm}
                  disabled={conversationSummaries.length === 0}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </DialogHeader>
            <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-8 sm:px-8">
              <ImageSidebar
                conversations={conversationSummaries}
                isLoadingHistory={isLoadingHistory}
                selectedConversationId={selectedConversationId}
                onCreateDraft={() => {
                  handleCreateDraft();
                  setIsHistoryOpen(false);
                }}
                onClearHistory={openClearHistoryConfirm}
                onRefreshConversations={handleRefreshConversations}
                onSelectConversation={(id) => {
                  handleSelectConversation(id);
                  setIsHistoryOpen(false);
                }}
                onDeleteConversation={openDeleteConversationConfirm}
                onRenameConversation={handleRenameConversation}
                formatConversationTime={formatConversationTime}
                hideActionButtons
              />
            </div>
          </DialogContent>
        </Dialog>

        <div className="flex min-h-0 flex-col gap-2 sm:gap-4">
          <div className="image-page-mobile-actions grid grid-cols-[minmax(0,1fr)_auto_auto_auto] items-center gap-2 px-1">
            <Button
              variant="outline"
              className="h-10 min-w-0 rounded-2xl border-stone-200 bg-white/90 px-3 text-stone-700 shadow-sm"
              onClick={() => setIsHistoryOpen(true)}
            >
              <History className="mr-2 size-4" />
              历史记录 ({conversationSummaries.length})
            </Button>
            <Button
              className="h-10 rounded-2xl bg-stone-950 text-white shadow-sm"
              onClick={handleCreateDraft}
            >
              <Plus className="size-4" />
              新建
            </Button>
            <Button
              variant="outline"
              className="h-10 rounded-2xl border-stone-200 bg-white/85 px-3 text-stone-600 shadow-sm"
              onClick={() => void handleRefreshConversations()}
              disabled={isLoadingHistory}
              title="刷新当前登录人的全部会话"
              aria-label="刷新当前登录人的全部会话"
            >
              <RefreshCw className={cn("size-4", isLoadingHistory && "animate-spin")} />
            </Button>
            <Button
              variant="outline"
              className="h-10 rounded-2xl border-stone-200 bg-white/85 px-3 text-stone-600 shadow-sm"
              onClick={openClearHistoryConfirm}
              disabled={conversationSummaries.length === 0}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>

          <div
            ref={resultsViewportRef}
            className="hide-scrollbar min-h-0 flex-1 overscroll-contain overflow-y-auto px-1 py-2 sm:px-4 sm:py-4"
          >
            <ImageResults
              selectedConversationId={selectedConversationId}
              selectedConversation={selectedConversation}
              isLoadingConversationDetail={Boolean(selectedConversationId && !selectedConversation && isLoadingConversationDetail)}
              onOpenLightbox={openLightbox}
              onContinueEdit={handleContinueEdit}
              onDeletePrompt={openDeletePromptConfirm}
              onDeleteResults={openDeleteResultsConfirm}
              onReuseTurnConfig={handleReuseTurnConfig}
              onRegenerateTurn={handleRegenerateTurn}
              onRetryImage={handleRetryImage}
              formatConversationTime={formatConversationTime}
              publicConfig={publicConfig}
            />
          </div>

          <ImageComposer
            prompt={imagePrompt}
            imageCount={imageCount}
            imageSize={imageSize}
            availableQuota={availableQuota}
            tokenName={tokenName}
            activeTaskCount={activeTaskCount}
            batchLimit={batchLimit}
            systemProcessingCount={Math.max(0, Number(runtimeStats?.processing || 0))}
            systemQueuedCount={Math.max(0, Number(runtimeStats?.queued || 0))}
            systemRunningCount={Math.max(0, Number(runtimeStats?.running || 0))}
            systemEstimatedWaitText={systemEstimatedWaitText}
            systemAverageDurationText={systemAverageDurationText}
            systemUpstreamConcurrency={Math.max(0, Number(runtimeStats?.upstream_concurrency || 0))}
            systemActiveUpstreamSlots={Math.max(0, Number(runtimeStats?.active_upstream_slots || 0))}
            accountCooldownCount={Math.max(0, Number(runtimeStats?.account_cooldown_accounts || 0))}
            accountInvalidCachedCount={Math.max(0, Number(runtimeStats?.account_invalid_cached_accounts || 0))}
            recentQuotaUsageText={recentQuotaUsageText}
            expandSignal={composerExpandSignal}
            referenceImages={referenceImages}
            textareaRef={textareaRef}
            fileInputRef={fileInputRef}
            onPromptChange={setImagePrompt}
            onImageCountChange={(value) => setImageCount(value ? clampImageCountWithLimit(value, batchLimit) : "")}
            onImageSizeChange={setImageSize}
            onSubmit={handleSubmit}
            showPromptMarket={isAdmin}
            onOpenPromptMarket={() => {
              if (isAdmin) {
                setIsPromptMarketOpen(true);
              }
            }}
            onPickReferenceImage={() => fileInputRef.current?.click()}
            onReferenceImageChange={handleReferenceImageChange}
            onRemoveReferenceImage={handleRemoveReferenceImage}
          />
        </div>
      </section>

      <ImageLightbox
        images={lightboxImages}
        currentIndex={lightboxIndex}
        open={lightboxOpen}
        onOpenChange={setLightboxOpen}
        onIndexChange={setLightboxIndex}
      />

      {isAdmin ? (
        <ImagePromptMarket
          open={isPromptMarketOpen}
          onOpenChange={setIsPromptMarketOpen}
          onApplyPrompt={handleApplyMarketPrompt}
        />
      ) : null}

      {deleteConfirm ? (
        <Dialog open onOpenChange={(open) => (!open ? setDeleteConfirm(null) : null)}>
          <DialogContent showCloseButton={false} className="rounded-2xl p-6">
            <DialogHeader className="gap-2">
              <DialogTitle>{deleteConfirmTitle}</DialogTitle>
              <DialogDescription className="text-sm leading-6">
                {deleteConfirmDescription}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDeleteConfirm(null)}>
                取消
              </Button>
              <Button className="danger-confirm-button" onClick={() => void handleConfirmDelete()}>
                确认删除
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : null}
    </>
  );
}

export default function ImagePage() {
  const { isCheckingAuth, session } = useAuthGuard();

  if (isCheckingAuth || !session) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  return (
    <ImagePageContent
      isAdmin={session.role === "admin"}
      isCompactUserView={session.scope === "image"}
      initialTokenName={session.name || "-"}
    />
  );
}
