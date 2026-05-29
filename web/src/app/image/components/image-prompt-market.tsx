"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ClipboardCopy, ExternalLink, LoaderCircle, RefreshCcw, Search, SlidersHorizontal, Star } from "lucide-react";
import { toast } from "sonner";

import {
  AWESOME_GPT_IMAGE_2_PROMPTS_SOURCE_URL,
  BANANA_PROMPTS_SOURCE_URL,
  PROMPT_MARKET_SOURCE_OPTIONS,
  fetchPromptMarketPrompts,
  type BananaPrompt,
  type BananaPromptMode,
  type PromptMarketLanguage,
  type PromptMarketLocalization,
  type PromptMarketSourceId,
} from "@/app/image/banana-prompts";
import {
  createPromptFavorite,
  deletePromptFavorite,
  fetchPromptFavorites,
  promptFavoriteKey,
  promptFavoriteRecordKey,
  promptFavoriteToBananaPrompt,
  type PromptFavorite,
} from "@/app/image/prompt-favorites";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

type PromptMarketModeFilter = "all" | BananaPromptMode;
type PromptMarketNsfwFilter = "safe" | "include" | "only";
type PromptMarketSourceFilter = "all" | PromptMarketSourceId;
type PromptMarketFavoriteFilter = "all" | "favorites";

type ImagePromptMarketProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onApplyPrompt: (prompt: BananaPrompt) => void | Promise<void>;
};

const ALL_CATEGORY_VALUE = "__all__";
const INITIAL_VISIBLE_COUNT = 48;
const VISIBLE_COUNT_STEP = 48;

function includesKeyword(value: string | undefined, keyword: string) {
  return Boolean(value && value.toLowerCase().includes(keyword));
}

function formatPromptDate(value?: string) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function getPromptLocalization(
  prompt: BananaPrompt,
  language: PromptMarketLanguage,
): PromptMarketLocalization | undefined {
  return prompt.localizations?.[language] ?? prompt.localizations?.["zh-CN"] ?? prompt.localizations?.en;
}

function getLocalizedPrompt(prompt: BananaPrompt, language: PromptMarketLanguage): BananaPrompt {
  const localization = getPromptLocalization(prompt, language);
  if (!localization) {
    return prompt;
  }

  return {
    ...prompt,
    title: localization.title,
    prompt: localization.prompt,
    category: localization.category,
    subCategory: localization.subCategory,
  };
}

function getPromptReferenceImageUrls(prompt: BananaPrompt) {
  const urls = prompt.referenceImageUrls.length > 0 ? prompt.referenceImageUrls : [prompt.preview];
  return Array.from(new Set(urls.map((url) => url.trim()).filter(Boolean)));
}

function buildPromptJSON(prompt: BananaPrompt) {
  return JSON.stringify(
    {
      title: prompt.title,
      prompt: prompt.prompt,
      mode: prompt.mode,
      category: prompt.category,
      sub_category: prompt.subCategory || undefined,
      reference_image_urls: getPromptReferenceImageUrls(prompt),
      preview: prompt.preview,
      source: prompt.source,
      source_label: prompt.sourceLabel,
      author: prompt.author || undefined,
      link: prompt.link || undefined,
    },
    null,
    2,
  );
}

function PromptPreviewImage({ prompt }: { prompt: BananaPrompt }) {
  const [failed, setFailed] = useState(false);

  if (failed) {
    return (
      <div className="absolute inset-0 flex items-center justify-center px-4 text-center text-sm font-medium text-[#8e8e93]">
        {prompt.title}
      </div>
    );
  }

  return (
    <img
      src={prompt.preview}
      alt={prompt.title}
      loading="lazy"
      className="h-full w-full object-cover transition duration-300 group-hover:scale-[1.03]"
      onError={() => setFailed(true)}
    />
  );
}

export function ImagePromptMarket({ open, onOpenChange, onApplyPrompt }: ImagePromptMarketProps) {
  const [prompts, setPrompts] = useState<BananaPrompt[]>([]);
  const [favoriteItems, setFavoriteItems] = useState<PromptFavorite[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingFavorites, setIsLoadingFavorites] = useState(false);
  const [error, setError] = useState("");
  const [favoriteError, setFavoriteError] = useState("");
  const [keyword, setKeyword] = useState("");
  const [favoriteFilter, setFavoriteFilter] = useState<PromptMarketFavoriteFilter>("all");
  const [source, setSource] = useState<PromptMarketSourceFilter>("all");
  const [promptLanguage, setPromptLanguage] = useState<PromptMarketLanguage>("zh-CN");
  const [category, setCategory] = useState(ALL_CATEGORY_VALUE);
  const [mode, setMode] = useState<PromptMarketModeFilter>("all");
  const [nsfwFilter, setNsfwFilter] = useState<PromptMarketNsfwFilter>("safe");
  const [isMobileFiltersOpen, setIsMobileFiltersOpen] = useState(false);
  const [visibleCount, setVisibleCount] = useState(INITIAL_VISIBLE_COUNT);
  const [favoriteBusyIds, setFavoriteBusyIds] = useState<Set<string>>(() => new Set());
  const scrollAreaRef = useRef<HTMLDivElement>(null);

  const updateFavoriteItems = (items: PromptFavorite[]) => {
    setFavoriteItems(Array.isArray(items) ? items : []);
  };

  const loadPromptData = () => {
    setIsLoading(true);
    setError("");

    void fetchPromptMarketPrompts()
      .then((items) => {
        setPrompts(items);
      })
      .catch((loadError: unknown) => {
        setError(loadError instanceof Error ? loadError.message : "读取提示词市场失败");
      })
      .finally(() => {
        setIsLoading(false);
      });
  };

  const loadFavoriteData = () => {
    setIsLoadingFavorites(true);
    setFavoriteError("");

    void fetchPromptFavorites()
      .then((data) => {
        updateFavoriteItems(data.items);
      })
      .catch((loadError: unknown) => {
        const message = loadError instanceof Error ? loadError.message : "读取收藏失败";
        setFavoriteError(message);
        toast.error(message);
      })
      .finally(() => {
        setIsLoadingFavorites(false);
      });
  };

  useEffect(() => {
    if (!open || prompts.length > 0) {
      return;
    }

    setIsLoading(true);
    setError("");
    const controller = new AbortController();

    void fetchPromptMarketPrompts(controller.signal)
      .then((items) => {
        setPrompts(items);
      })
      .catch((loadError: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setError(loadError instanceof Error ? loadError.message : "读取提示词市场失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });

    return () => controller.abort();
  }, [open, prompts.length]);

  useEffect(() => {
    if (!open || favoriteItems.length > 0) {
      return;
    }

    setIsLoadingFavorites(true);
    setFavoriteError("");
    const controller = new AbortController();

    void fetchPromptFavorites(controller.signal)
      .then((data) => {
        updateFavoriteItems(data.items);
      })
      .catch((loadError: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        const message = loadError instanceof Error ? loadError.message : "读取收藏失败";
        setFavoriteError(message);
        toast.error(message);
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoadingFavorites(false);
        }
      });

    return () => controller.abort();
  }, [favoriteItems.length, open]);

  useEffect(() => {
    setVisibleCount(INITIAL_VISIBLE_COUNT);
    scrollAreaRef.current?.scrollTo({ top: 0 });
  }, [keyword, source, promptLanguage, category, mode, nsfwFilter, favoriteFilter]);

  useEffect(() => {
    if (open) {
      scrollAreaRef.current?.scrollTo({ top: 0 });
      return;
    }
    setIsMobileFiltersOpen(false);
  }, [open]);

  const favoritePrompts = useMemo(
    () => favoriteItems.map((item) => promptFavoriteToBananaPrompt(item)),
    [favoriteItems],
  );

  const favoriteIds = useMemo(() => new Set(favoriteItems.map((item) => promptFavoriteRecordKey(item))), [favoriteItems]);

  const favoriteByPromptKey = useMemo(() => {
    const items = new Map<string, PromptFavorite>();
    favoriteItems.forEach((item) => {
      items.set(promptFavoriteRecordKey(item), item);
    });
    return items;
  }, [favoriteItems]);

  const promptPool = favoriteFilter === "favorites" ? favoritePrompts : prompts;

  const sourceFilteredPrompts = useMemo(() => {
    if (source === "all") {
      return promptPool;
    }
    return promptPool.filter((prompt) => prompt.source === source);
  }, [promptPool, source]);

  const categories = useMemo(() => {
    const values = new Set<string>();
    sourceFilteredPrompts.forEach((prompt) => {
      values.add(getLocalizedPrompt(prompt, promptLanguage).category);
    });
    return [...values].sort((a, b) => a.localeCompare(b, "zh-CN"));
  }, [promptLanguage, sourceFilteredPrompts]);

  useEffect(() => {
    if (category !== ALL_CATEGORY_VALUE && !categories.includes(category)) {
      setCategory(ALL_CATEGORY_VALUE);
    }
  }, [categories, category]);

  const filteredPrompts = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();

    return sourceFilteredPrompts.filter((prompt) => {
      const localizedPrompt = getLocalizedPrompt(prompt, promptLanguage);
      if (nsfwFilter === "safe" && prompt.isNsfw) {
        return false;
      }
      if (nsfwFilter === "only" && !prompt.isNsfw) {
        return false;
      }
      if (category !== ALL_CATEGORY_VALUE && localizedPrompt.category !== category) {
        return false;
      }
      if (mode !== "all" && localizedPrompt.mode !== mode) {
        return false;
      }
      if (!normalizedKeyword) {
        return true;
      }

      return (
        includesKeyword(localizedPrompt.title, normalizedKeyword) ||
        includesKeyword(localizedPrompt.prompt, normalizedKeyword) ||
        includesKeyword(localizedPrompt.author, normalizedKeyword) ||
        includesKeyword(localizedPrompt.category, normalizedKeyword) ||
        includesKeyword(localizedPrompt.subCategory, normalizedKeyword) ||
        includesKeyword(localizedPrompt.sourceLabel, normalizedKeyword)
      );
    });
  }, [category, keyword, mode, nsfwFilter, promptLanguage, sourceFilteredPrompts]);

  const visiblePrompts = filteredPrompts.slice(0, visibleCount);
  const hasMore = visiblePrompts.length < filteredPrompts.length;
  const selectedSourceLabel =
    source === "all" ? "" : PROMPT_MARKET_SOURCE_OPTIONS.find((item) => item.value === source)?.label || source;
  const selectedLanguageLabel = promptLanguage === "zh-CN" ? "" : "English";
  const selectedCategoryLabel = category === ALL_CATEGORY_VALUE ? "" : category;
  const selectedModeLabel = mode === "all" ? "" : mode === "edit" ? "编辑" : "文生图";
  const selectedNsfwLabel =
    nsfwFilter === "safe" ? "" : nsfwFilter === "include" ? "包含 NSFW" : "仅 NSFW";
  const selectedFavoriteLabel = favoriteFilter === "favorites" ? "已收藏" : "";
  const activeFilterLabels = [
    selectedFavoriteLabel,
    selectedSourceLabel,
    selectedLanguageLabel,
    selectedCategoryLabel,
    selectedModeLabel,
    selectedNsfwLabel,
  ].filter(Boolean);
  const activeFilterCount = activeFilterLabels.length;

  const resetFilters = () => {
    setFavoriteFilter("all");
    setSource("all");
    setPromptLanguage("zh-CN");
    setCategory(ALL_CATEGORY_VALUE);
    setMode("all");
    setNsfwFilter("safe");
  };

  const setFavoriteBusy = (id: string, busy: boolean) => {
    setFavoriteBusyIds((current) => {
      const next = new Set(current);
      if (busy) {
        next.add(id);
      } else {
        next.delete(id);
      }
      return next;
    });
  };

  const toggleFavorite = async (prompt: BananaPrompt) => {
    const key = promptFavoriteKey(prompt);
    if (favoriteBusyIds.has(key)) {
      return;
    }

    const existing = favoriteByPromptKey.get(key);
    setFavoriteBusy(key, true);
    try {
      if (existing) {
        const data = await deletePromptFavorite(existing.id);
        updateFavoriteItems(data.items);
        toast.success("已取消收藏");
      } else {
        const data = await createPromptFavorite(prompt);
        updateFavoriteItems(data.items);
        toast.success("已收藏");
      }
    } catch (toggleError) {
      toast.error(toggleError instanceof Error ? toggleError.message : "收藏操作失败");
    } finally {
      setFavoriteBusy(key, false);
    }
  };

  const copyPromptJSON = async (prompt: BananaPrompt) => {
    try {
      await navigator.clipboard.writeText(buildPromptJSON(prompt));
      toast.success("已复制 JSON");
    } catch {
      toast.error("复制失败，请手动复制");
    }
  };

  const renderFavoriteTabs = (className?: string) => (
    <div className={cn("flex h-10 rounded-[18px] bg-stone-100/85 p-1 shadow-inner shadow-stone-200/50", className)}>
      <button
        type="button"
        className={cn(
          "inline-flex min-w-0 flex-1 items-center justify-center rounded-[14px] px-3 text-xs font-semibold text-stone-600 transition hover:text-stone-900",
          favoriteFilter === "all" && "bg-white text-stone-950 shadow-sm",
        )}
        onClick={() => setFavoriteFilter("all")}
      >
        全部
      </button>
      <button
        type="button"
        className={cn(
          "inline-flex min-w-0 flex-1 items-center justify-center gap-1.5 rounded-[14px] px-3 text-xs font-semibold text-stone-600 transition hover:text-stone-900",
          favoriteFilter === "favorites" && "bg-white text-emerald-700 shadow-sm",
        )}
        onClick={() => setFavoriteFilter("favorites")}
      >
        <Star className={cn("size-3.5", favoriteFilter === "favorites" && "fill-current")} />
        {favoriteItems.length > 0 ? `收藏 ${favoriteItems.length}` : "收藏"}
      </button>
    </div>
  );

  const renderFilterControls = (triggerClassName?: string) => {
    const filterTriggerClassName = cn("h-11 min-w-0 rounded-2xl border-stone-200 bg-white shadow-sm", triggerClassName);

    return (
      <>
        <Select
        value={source}
        onValueChange={(value) => setSource(value as PromptMarketSourceFilter)}
      >
        <SelectTrigger className={filterTriggerClassName}>
          <SelectValue placeholder="来源" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value="all">全部</SelectItem>
            {PROMPT_MARKET_SOURCE_OPTIONS.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
      <Select
        value={promptLanguage}
        onValueChange={(value) => setPromptLanguage(value as PromptMarketLanguage)}
      >
        <SelectTrigger className={filterTriggerClassName}>
          <SelectValue placeholder="语言" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value="zh-CN">中文</SelectItem>
            <SelectItem value="en">English</SelectItem>
          </SelectGroup>
        </SelectContent>
      </Select>
      <Select value={category} onValueChange={setCategory}>
        <SelectTrigger className={filterTriggerClassName}>
          <SelectValue placeholder="分类" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value={ALL_CATEGORY_VALUE}>全部分类</SelectItem>
            {categories.map((item) => (
              <SelectItem key={item} value={item}>
                {item}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
      <Select value={mode} onValueChange={(value) => setMode(value as PromptMarketModeFilter)}>
        <SelectTrigger className={filterTriggerClassName}>
          <SelectValue placeholder="模式" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value="all">全部模式</SelectItem>
            <SelectItem value="generate">文生图</SelectItem>
            <SelectItem value="edit">编辑</SelectItem>
          </SelectGroup>
        </SelectContent>
      </Select>
      <Select
        value={nsfwFilter}
        onValueChange={(value) => setNsfwFilter(value as PromptMarketNsfwFilter)}
      >
        <SelectTrigger className={filterTriggerClassName}>
          <SelectValue placeholder="NSFW" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value="safe">隐藏 NSFW</SelectItem>
            <SelectItem value="include">包含 NSFW</SelectItem>
            <SelectItem value="only">仅 NSFW</SelectItem>
          </SelectGroup>
        </SelectContent>
      </Select>
      </>
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="top-auto bottom-0 left-0 flex h-[92dvh] max-h-[92dvh] w-full max-w-none translate-x-0 translate-y-0 grid-rows-none flex-col gap-0 overflow-hidden rounded-b-none rounded-t-[28px] border-white/80 bg-white p-0 shadow-[0_-24px_90px_-44px_rgba(15,23,42,0.55)] sm:top-[50%] sm:bottom-auto sm:left-[50%] sm:h-[min(92dvh,900px)] sm:max-h-[min(92dvh,900px)] sm:w-[min(96vw,1320px)] sm:translate-x-[-50%] sm:translate-y-[-50%] sm:rounded-[34px] sm:shadow-[0_36px_120px_-45px_rgba(16,24,40,0.4)] xl:w-[min(92vw,1440px)]">
        <DialogHeader className="shrink-0 border-b border-stone-100 bg-white/95 px-4 pt-4 pr-12 pb-3 backdrop-blur sm:px-8 sm:pt-7 sm:pr-16 sm:pb-5">
          <div className="flex items-start justify-between gap-3 sm:gap-6">
            <div className="min-w-0">
              <DialogTitle className="text-xl leading-tight tracking-tight text-stone-950 sm:text-3xl">Prompts 提示词市场</DialogTitle>
              <DialogDescription className="mt-2 max-w-[760px] text-sm leading-6 text-stone-500 sm:block">
                来自{" "}
                <a
                  href={BANANA_PROMPTS_SOURCE_URL}
                  target="_blank"
                  rel="noreferrer"
                  className="font-medium text-[#1456f0] hover:underline"
                >
                  glidea/banana-prompt-quicker
                </a>
                {" "}和{" "}
                <a
                  href={AWESOME_GPT_IMAGE_2_PROMPTS_SOURCE_URL}
                  target="_blank"
                  rel="noreferrer"
                  className="font-medium text-[#1456f0] hover:underline"
                >
                  EvoLinkAI/awesome-gpt-image-2-prompts
                </a>
                ，可按来源筛选并一键套用到当前生图输入框。
              </DialogDescription>
            </div>
            <div className="flex shrink-0 items-center gap-2 pt-0.5 text-xs text-stone-500">
              <span className="rounded-full bg-emerald-50 px-2.5 py-1 font-semibold text-emerald-700 ring-1 ring-emerald-100 sm:px-3">
                {favoriteFilter === "favorites"
                  ? isLoadingFavorites
                    ? "读取收藏"
                    : `已收藏 ${filteredPrompts.length}`
                  : prompts.length > 0
                    ? `${filteredPrompts.length} / ${sourceFilteredPrompts.length}`
                    : "远程市场"}
              </span>
            </div>
          </div>
        </DialogHeader>

        <div className="shrink-0 border-b border-stone-100 bg-white/95 px-4 py-3 backdrop-blur sm:px-8 sm:py-4">
          <div className="md:hidden">
            {renderFavoriteTabs()}
            <div className="mt-2 flex items-center gap-2">
              <div className="relative min-w-0 flex-1">
                <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-[#8e8e93]" />
                <Input
                  value={keyword}
                  onChange={(event) => setKeyword(event.target.value)}
                  placeholder="搜索提示词"
                  className="h-11 rounded-2xl border-stone-200 bg-white pl-9 text-[15px] shadow-sm"
                />
              </div>
              <button
                type="button"
                className={cn(
                  "relative inline-flex size-11 shrink-0 items-center justify-center rounded-2xl border border-stone-200 bg-white text-stone-600 shadow-sm transition hover:bg-stone-50",
                  isMobileFiltersOpen && "border-emerald-200 bg-emerald-50 text-emerald-700",
                )}
                onClick={() => setIsMobileFiltersOpen((open) => !open)}
                aria-label={isMobileFiltersOpen ? "收起筛选项" : "展开筛选项"}
                aria-expanded={isMobileFiltersOpen}
                title={isMobileFiltersOpen ? "收起筛选" : "筛选"}
              >
                <SlidersHorizontal className="size-4" />
                {activeFilterCount > 0 ? (
                  <span className="absolute -right-0.5 -top-0.5 inline-flex size-4 items-center justify-center rounded-full bg-emerald-600 text-[10px] font-semibold text-white">
                    {activeFilterCount}
                  </span>
                ) : null}
              </button>
            </div>

            {activeFilterLabels.length > 0 && !isMobileFiltersOpen ? (
              <div className="hide-scrollbar mt-2 flex gap-1.5 overflow-x-auto">
                {activeFilterLabels.map((label) => (
                  <span
                    key={label}
                    className="shrink-0 rounded-full bg-stone-100 px-2.5 py-1 text-[11px] font-medium text-stone-600"
                  >
                    {label}
                  </span>
                ))}
                <button
                  type="button"
                  className="shrink-0 rounded-full px-2.5 py-1 text-[11px] font-semibold text-emerald-700"
                  onClick={resetFilters}
                >
                  清除
                </button>
              </div>
            ) : null}

            {isMobileFiltersOpen ? (
              <div className="mt-3 grid grid-cols-1 gap-2 min-[420px]:grid-cols-2">
                {renderFilterControls()}
                <Button
                  type="button"
                  variant="outline"
                  className="h-10 rounded-2xl border-stone-200 bg-white text-xs text-stone-600 shadow-none min-[420px]:col-span-2"
                  onClick={resetFilters}
                  disabled={activeFilterCount === 0}
                >
                  重置筛选
                </Button>
              </div>
            ) : null}
          </div>

          <div className="hidden md:flex md:flex-col md:gap-3">
            <div className="flex min-w-0 gap-3">
              <div className="relative min-w-0 flex-1">
                <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-[#8e8e93]" />
                <Input
                  value={keyword}
                  onChange={(event) => setKeyword(event.target.value)}
                  placeholder="搜索标题、作者、分类或提示词"
                  className="h-11 rounded-2xl border-stone-200 bg-white pl-9 shadow-sm"
                />
              </div>
              {renderFavoriteTabs("w-[180px] shrink-0")} 
            </div>
            <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-[minmax(220px,1fr)_140px_minmax(220px,1fr)_140px_150px]">
              {renderFilterControls("h-11 min-w-0 rounded-2xl border-stone-200 bg-white shadow-sm")} 
            </div>
          </div>
          {favoriteError ? (
            <div className="mt-2 flex items-center justify-between gap-3 rounded-[12px] bg-[#fff7ed] px-3 py-2 text-xs text-[#9a3412]">
              <span>{favoriteError}</span>
              <button type="button" className="font-semibold text-[#1456f0]" onClick={loadFavoriteData}>
                重试
              </button>
            </div>
          ) : null}
        </div>

        <div ref={scrollAreaRef} className="min-h-0 flex-1 overflow-y-auto bg-gradient-to-b from-stone-50/70 to-white px-3 py-3 sm:px-8 sm:py-5">
          {favoriteFilter !== "favorites" && isLoading ? (
            <div className="flex h-full min-h-[320px] flex-col items-center justify-center gap-3 text-[#45515e]">
              <LoaderCircle className="size-6 animate-spin text-[#1456f0]" />
              <p className="text-sm">正在读取远程提示词市场...</p>
            </div>
          ) : favoriteFilter !== "favorites" && error ? (
            <div className="flex h-full min-h-[320px] flex-col items-center justify-center gap-4 text-center">
              <div className="max-w-[420px] text-sm leading-6 text-[#45515e]">{error}</div>
              <Button
                type="button"
                variant="outline"
                className="rounded-2xl border-stone-200 bg-white px-5 shadow-sm"
                onClick={loadPromptData}
              >
                <RefreshCcw className="size-4" />
                重新加载
              </Button>
            </div>
          ) : favoriteFilter === "favorites" && isLoadingFavorites && favoriteItems.length === 0 ? (
            <div className="flex h-full min-h-[320px] flex-col items-center justify-center gap-3 text-[#45515e]">
              <LoaderCircle className="size-6 animate-spin text-[#1456f0]" />
              <p className="text-sm">正在读取收藏...</p>
            </div>
          ) : visiblePrompts.length === 0 ? (
            <div className="flex h-full min-h-[320px] items-center justify-center text-sm text-[#8e8e93]">
              {favoriteFilter === "favorites"
                ? favoriteItems.length === 0
                  ? "还没有收藏提示词"
                  : "没有匹配的收藏提示词"
                : "没有找到匹配的提示词"}
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              <div className="grid grid-cols-1 items-stretch gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
                {visiblePrompts.map((prompt) => {
                  const localizedPrompt = getLocalizedPrompt(prompt, promptLanguage);
                  const dateLabel = formatPromptDate(prompt.created);
                  const promptMetaLabels = [localizedPrompt.subCategory, dateLabel].filter(
                    (label): label is string => Boolean(label),
                  );
                  const favoriteKey = promptFavoriteKey(prompt);
                  const isFavorite = favoriteIds.has(favoriteKey);
                  const isFavoriteBusy = favoriteBusyIds.has(favoriteKey);
                  return (
                    <article
                      key={prompt.id}
                      className="group flex h-full min-h-[0] flex-col overflow-hidden rounded-[24px] border border-white/80 bg-white shadow-[0_10px_30px_-22px_rgba(15,23,42,0.55)] ring-1 ring-stone-200/70 transition hover:-translate-y-0.5 hover:shadow-[0_20px_55px_-32px_rgba(15,23,42,0.65)]"
                    >
                      <div className="relative aspect-[16/9] overflow-hidden bg-stone-100">
                        <PromptPreviewImage prompt={localizedPrompt} />
                        {localizedPrompt.author ? (
                          <div className="absolute left-3 top-3 z-10 flex max-w-[calc(100%-1.5rem)]">
                            <span
                              className="min-w-0 truncate rounded-full bg-black/45 px-2.5 py-1 text-[11px] font-medium text-white shadow-sm backdrop-blur-sm"
                              title={`作者：${localizedPrompt.author}`}
                            >
                              {localizedPrompt.author}
                            </span>
                          </div>
                        ) : null}
                        <div className="absolute inset-x-0 bottom-0 flex flex-wrap items-center gap-1.5 bg-gradient-to-t from-black/75 via-black/25 to-transparent px-3 pt-9 pb-2.5">
                          <Badge className="bg-white/92 text-[#18181b] shadow-sm">
                            {localizedPrompt.mode === "edit" ? "编辑" : "文生图"}
                          </Badge>
                          <Badge className="bg-white/18 text-white shadow-sm backdrop-blur">
                            {localizedPrompt.category}
                          </Badge>
                          {prompt.isNsfw ? (
                            <Badge className="bg-white/18 text-white shadow-sm backdrop-blur">
                              NSFW
                            </Badge>
                          ) : null}
                          {prompt.referenceImageUrls.length > 0 ? (
                            <Badge className="bg-white/18 text-white shadow-sm backdrop-blur">
                              {prompt.referenceImageUrls.length} 张参考图
                            </Badge>
                          ) : null}
                        </div>
                      </div>
                      <div className="flex min-h-[188px] flex-1 flex-col gap-3 p-4 sm:min-h-[206px]">
                        <div className="flex min-w-0 items-start justify-between gap-3">
                          <div className="min-w-0">
                            <h3 className="line-clamp-2 font-display text-base font-semibold leading-6 text-stone-950">
                              {localizedPrompt.title}
                            </h3>
                            {promptMetaLabels.length > 0 ? (
                              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-stone-500">
                                {promptMetaLabels.map((label) => (
                                  <span key={label}>/{label}</span>
                                ))}
                              </div>
                            ) : null}
                          </div>
                          <div className="flex shrink-0 items-center gap-1.5">
                            <button
                              type="button"
                              className={cn(
                                "inline-flex size-8 items-center justify-center rounded-full border border-stone-200 bg-white/90 text-stone-600 transition hover:bg-stone-50 hover:text-stone-900 disabled:cursor-not-allowed disabled:opacity-60",
                                isFavorite && "border-emerald-200 bg-emerald-50 text-emerald-700",
                              )}
                              onClick={() => void toggleFavorite(prompt)}
                              disabled={isFavoriteBusy}
                              aria-label={isFavorite ? "取消收藏提示词" : "收藏提示词"}
                              title={isFavorite ? "取消收藏" : "收藏"}
                            >
                              {isFavoriteBusy ? (
                                <LoaderCircle className="size-3.5 animate-spin" />
                              ) : (
                                <Star className={cn("size-3.5", isFavorite && "fill-current")} />
                              )}
                            </button>
                            {prompt.link ? (
                              <a
                                href={prompt.link}
                                target="_blank"
                                rel="noreferrer"
                                className="inline-flex size-8 items-center justify-center rounded-full border border-stone-200 bg-white/90 text-stone-600 transition hover:bg-stone-50 hover:text-stone-900"
                                aria-label="查看来源"
                                title="查看来源"
                              >
                                <ExternalLink className="size-3.5" />
                              </a>
                            ) : null}
                          </div>
                        </div>
                        <p className="line-clamp-4 text-sm leading-6 text-stone-600 sm:line-clamp-5">{localizedPrompt.prompt}</p>
                        <div className="mt-auto grid grid-cols-2 gap-2 border-t border-stone-100 pt-3 sm:flex sm:flex-wrap sm:justify-end">
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            className="h-9 rounded-2xl border-stone-200 bg-white px-3 text-xs text-stone-600 shadow-none hover:bg-stone-50 hover:text-stone-900"
                            onClick={() => void copyPromptJSON(localizedPrompt)}
                          >
                            <ClipboardCopy className="size-3.5" />
                            复制 JSON
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            className="h-9 rounded-2xl bg-stone-950 px-4 text-xs text-white shadow-sm hover:bg-stone-800"
                            onClick={() => void onApplyPrompt(localizedPrompt)}
                          >
                            套用
                          </Button>
                        </div>
                      </div>
                    </article>
                  );
                })}
              </div>
              {hasMore ? (
                <div className="flex justify-center pt-1">
                  <Button
                    type="button"
                    variant="outline"
                    className="rounded-2xl border-stone-200 bg-white px-5 shadow-sm"
                    onClick={() => setVisibleCount((current) => current + VISIBLE_COUNT_STEP)}
                  >
                    加载更多 ({visiblePrompts.length}/{filteredPrompts.length})
                  </Button>
                </div>
              ) : null}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
