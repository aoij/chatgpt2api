import type {
  BananaPrompt,
  BananaPromptMode,
  PromptMarketLanguage,
  PromptMarketLocalization,
  PromptMarketSourceId,
} from "@/app/image/banana-prompts";

export type PromptFavorite = {
  id: string;
  prompt_id: string;
  source: PromptMarketSourceId;
  title: string;
  preview: string;
  reference_image_urls: string[];
  prompt: string;
  author: string;
  link?: string;
  mode: BananaPromptMode;
  category: string;
  sub_category?: string;
  created?: string;
  source_label: string;
  is_nsfw: boolean;
  localizations?: Partial<
    Record<
      PromptMarketLanguage,
      PromptMarketLocalization & {
        sub_category?: string;
      }
    >
  >;
  favorited_at: string;
  updated_at?: string;
};

const PROMPT_FAVORITES_STORAGE_KEY = "chatgpt2api:prompt_favorites";

export function promptFavoriteKey(prompt: Pick<BananaPrompt, "id" | "source">) {
  return `${prompt.source}:${prompt.id}`;
}

export function promptFavoriteRecordKey(favorite: Pick<PromptFavorite, "prompt_id" | "source">) {
  return `${favorite.source}:${favorite.prompt_id}`;
}

export function promptFavoriteToBananaPrompt(favorite: PromptFavorite): BananaPrompt {
  return {
    id: favorite.prompt_id,
    title: favorite.title,
    preview: favorite.preview,
    referenceImageUrls: favorite.reference_image_urls,
    prompt: favorite.prompt,
    author: favorite.author,
    link: favorite.link,
    mode: favorite.mode,
    category: favorite.category,
    subCategory: favorite.sub_category,
    created: favorite.created,
    source: favorite.source,
    sourceLabel: favorite.source_label,
    isNsfw: favorite.is_nsfw,
    localizations: normalizeFavoriteLocalizations(favorite.localizations),
  };
}

export function bananaPromptToFavoritePayload(prompt: BananaPrompt) {
  return {
    prompt_id: prompt.id,
    source: prompt.source,
    title: prompt.title,
    preview: prompt.preview,
    reference_image_urls: prompt.referenceImageUrls,
    prompt: prompt.prompt,
    author: prompt.author,
    link: prompt.link,
    mode: prompt.mode,
    category: prompt.category,
    sub_category: prompt.subCategory,
    created: prompt.created,
    source_label: prompt.sourceLabel,
    is_nsfw: prompt.isNsfw,
    localizations: prompt.localizations ? localizationsToPayload(prompt.localizations) : undefined,
  };
}

export async function fetchPromptFavorites(signal?: AbortSignal): Promise<{ items: PromptFavorite[] }> {
  if (signal?.aborted) {
    return { items: [] };
  }
  return { items: readLocalPromptFavorites() };
}

export async function createPromptFavorite(prompt: BananaPrompt): Promise<{ item: PromptFavorite; items: PromptFavorite[] }> {
  const now = new Date().toISOString();
  const payload = bananaPromptToFavoritePayload(prompt);
  const item: PromptFavorite = {
    ...payload,
    id: promptFavoriteKey(prompt),
    favorited_at: now,
    updated_at: now,
  };
  const key = promptFavoriteRecordKey(item);
  const items = readLocalPromptFavorites().filter((favorite) => promptFavoriteRecordKey(favorite) !== key);
  const nextItems = [item, ...items];
  writeLocalPromptFavorites(nextItems);
  return { item, items: nextItems };
}

export async function deletePromptFavorite(favoriteId: string): Promise<{ items: PromptFavorite[] }> {
  const normalizedId = String(favoriteId || "").trim();
  const nextItems = readLocalPromptFavorites().filter((favorite) => {
    return favorite.id !== normalizedId && promptFavoriteRecordKey(favorite) !== normalizedId;
  });
  writeLocalPromptFavorites(nextItems);
  return { items: nextItems };
}

function normalizeFavoriteLocalizations(value: PromptFavorite["localizations"]): BananaPrompt["localizations"] {
  if (!value) {
    return undefined;
  }
  const localizations: BananaPrompt["localizations"] = {};
  for (const language of ["zh-CN", "en"] satisfies PromptMarketLanguage[]) {
    const item = value[language];
    if (!item) {
      continue;
    }
    localizations[language] = {
      title: item.title,
      prompt: item.prompt,
      category: item.category,
      subCategory: item.subCategory ?? item.sub_category,
    };
  }
  return Object.keys(localizations).length > 0 ? localizations : undefined;
}

function localizationsToPayload(localizations: NonNullable<BananaPrompt["localizations"]>) {
  const payload: Record<string, unknown> = {};
  for (const [language, item] of Object.entries(localizations)) {
    if (!item) {
      continue;
    }
    payload[language] = {
      title: item.title,
      prompt: item.prompt,
      category: item.category,
      sub_category: item.subCategory,
    };
  }
  return payload;
}

function readLocalPromptFavorites(): PromptFavorite[] {
  if (typeof window === "undefined") {
    return [];
  }
  try {
    const raw = window.localStorage.getItem(PROMPT_FAVORITES_STORAGE_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed.filter(isPromptFavorite).map(normalizePromptFavorite);
  } catch {
    return [];
  }
}

function writeLocalPromptFavorites(items: PromptFavorite[]) {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(PROMPT_FAVORITES_STORAGE_KEY, JSON.stringify(items.slice(0, 500)));
  } catch {
    // localStorage may be unavailable in private mode; ignore and keep UI usable.
  }
}

function isPromptFavorite(value: unknown): value is PromptFavorite {
  if (!value || typeof value !== "object") {
    return false;
  }
  const item = value as Record<string, unknown>;
  return Boolean(item.prompt_id && item.source && item.title && item.prompt);
}

function normalizePromptFavorite(item: PromptFavorite): PromptFavorite {
  const key = promptFavoriteRecordKey(item);
  return {
    ...item,
    id: item.id || key,
    reference_image_urls: Array.isArray(item.reference_image_urls) ? item.reference_image_urls : [],
    author: item.author || "",
    mode: item.mode === "edit" ? "edit" : "generate",
    category: item.category || "未分类",
    source_label: item.source_label || item.source,
    is_nsfw: Boolean(item.is_nsfw),
    favorited_at: item.favorited_at || new Date().toISOString(),
  };
}
