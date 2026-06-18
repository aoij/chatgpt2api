"use client";

import { ArrowUp, Check, ChevronDown, ImagePlus, LoaderCircle, Store, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ClipboardEvent, type RefObject } from "react";

import { ImageLightbox } from "@/components/image-lightbox";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

type ImageComposerProps = {
  prompt: string;
  imageCount: string;
  imageSize: string;
  availableQuota: string;
  tokenName: string;
  activeTaskCount: number;
  batchLimit: number;
  systemProcessingCount: number;
  systemQueuedCount: number;
  systemRunningCount: number;
  systemEstimatedWaitText: string;
  systemAverageDurationText: string;
  systemUpstreamConcurrency?: number;
  systemActiveUpstreamSlots?: number;
  accountCooldownCount?: number;
  accountInvalidCachedCount?: number;
  recentQuotaUsageText?: string;
  expandSignal?: number;
  referenceImages: Array<{ name: string; dataUrl: string }>;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  fileInputRef: RefObject<HTMLInputElement | null>;
  onPromptChange: (value: string) => void;
  onImageCountChange: (value: string) => void;
  onImageSizeChange: (value: string) => void;
  onSubmit: () => void | Promise<void>;
  showPromptMarket?: boolean;
  onOpenPromptMarket: () => void;
  onPickReferenceImage: () => void;
  onReferenceImageChange: (files: File[]) => void | Promise<void>;
  onRemoveReferenceImage: (index: number) => void;
};

const imageSizeOptions = [
  { value: "", label: "未指定" },
  { value: "1:1", label: "1:1（正方形）" },
  { value: "16:9", label: "16:9（横版）" },
  { value: "4:3", label: "4:3（横版）" },
  { value: "3:4", label: "3:4（竖版）" },
  { value: "9:16", label: "9:16（竖版）" },
];

export function ImageComposer({
  prompt,
  imageCount,
  imageSize,
  availableQuota,
  tokenName,
  activeTaskCount,
  batchLimit,
  systemProcessingCount,
  systemQueuedCount,
  systemRunningCount,
  systemEstimatedWaitText,
  systemAverageDurationText,
  systemUpstreamConcurrency = 0,
  systemActiveUpstreamSlots = 0,
  accountCooldownCount = 0,
  accountInvalidCachedCount = 0,
  recentQuotaUsageText = "",
  expandSignal = 0,
  referenceImages,
  textareaRef,
  fileInputRef,
  onPromptChange,
  onImageCountChange,
  onImageSizeChange,
  onSubmit,
  showPromptMarket = true,
  onOpenPromptMarket,
  onPickReferenceImage,
  onReferenceImageChange,
  onRemoveReferenceImage,
}: ImageComposerProps) {
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [isSizeMenuOpen, setIsSizeMenuOpen] = useState(false);
  const [isMobilePanelExpanded, setIsMobilePanelExpanded] = useState(false);
  const [isMobileViewport, setIsMobileViewport] = useState(false);
  const [isDesktopComposerCollapsed, setIsDesktopComposerCollapsed] = useState(false);
  const [sizeMenuPos, setSizeMenuPos] = useState<{ top: number; left: number; width: number }>({ top: 0, left: 0, width: 210 });
  const sizeMenuRef = useRef<HTMLDivElement>(null);
  const sizeMenuBtnRef = useRef<HTMLButtonElement>(null);
  const composerBodyRef = useRef<HTMLDivElement>(null);
  const lightboxImages = useMemo(
    () => referenceImages.map((image, index) => ({ id: `${image.name}-${index}`, src: image.dataUrl })),
    [referenceImages],
  );
  const imageSizeLabel = imageSizeOptions.find((option) => option.value === imageSize)?.label || "未指定";
  const submitLabel = referenceImages.length > 0 ? "开始编辑" : "开始生图";
  const hasPrompt = Boolean(prompt.trim());
  const isMobileExpandedSheet = isMobileViewport && isMobilePanelExpanded;

  const focusPromptTextarea = (scrollToTop = false) => {
    if (typeof window === "undefined") {
      return;
    }

    window.setTimeout(() => {
      if (scrollToTop) {
        composerBodyRef.current?.scrollTo({ top: 0 });
      }
      textareaRef.current?.focus();
      textareaRef.current?.scrollIntoView({ block: "nearest" });
    }, 24);
  };

  const expandMobilePanel = () => {
    setIsMobilePanelExpanded(true);
    focusPromptTextarea(true);
  };

  const submitAndCollapseMobilePanel = async () => {
    if (!hasPrompt) {
      expandMobilePanel();
      return;
    }
    await onSubmit();
    setIsMobilePanelExpanded(false);
  };

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const mediaQuery = window.matchMedia("(max-width: 639px)");
    const updateViewportState = () => setIsMobileViewport(mediaQuery.matches);
    updateViewportState();

    if (typeof mediaQuery.addEventListener === "function") {
      mediaQuery.addEventListener("change", updateViewportState);
      return () => {
        mediaQuery.removeEventListener("change", updateViewportState);
      };
    }

    mediaQuery.addListener(updateViewportState);
    return () => {
      mediaQuery.removeListener(updateViewportState);
    };
  }, []);

  useEffect(() => {
    if (referenceImages.length > 0) {
      setIsDesktopComposerCollapsed(false);
      setIsMobilePanelExpanded(true);
      focusPromptTextarea(true);
    }
  }, [referenceImages.length]);

  useEffect(() => {
    if (!isMobileExpandedSheet) {
      return;
    }
    focusPromptTextarea(true);
  }, [isMobileExpandedSheet]);

  useEffect(() => {
    if (!expandSignal) {
      return;
    }
    setIsDesktopComposerCollapsed(false);
    setIsMobilePanelExpanded(true);
    setIsSizeMenuOpen(false);
    focusPromptTextarea(true);
  }, [expandSignal]);

  useEffect(() => {
    if (!isMobileViewport) {
      setIsMobilePanelExpanded(true);
    }
  }, [isMobileViewport]);

  const updateSizeMenuPosition = () => {
    if (typeof window === "undefined" || !sizeMenuBtnRef.current) {
      return;
    }
    const rect = sizeMenuBtnRef.current.getBoundingClientRect();
    const menuWidth = isMobileViewport ? Math.max(260, window.innerWidth - 32) : Math.min(210, window.innerWidth - 32);
    const left = isMobileViewport
      ? 16
      : Math.max(16, Math.min(rect.left, window.innerWidth - menuWidth - 16));
    const menuHeight = isMobileViewport ? 280 : Math.min(320, Math.max(180, window.innerHeight * 0.45));
    const top = Math.max(12, rect.top - menuHeight - 10);
    setSizeMenuPos({ top, left, width: menuWidth });
  };

  useEffect(() => {
    if (!isSizeMenuOpen || isMobileViewport) {
      return;
    }
    updateSizeMenuPosition();
    window.addEventListener("resize", updateSizeMenuPosition);
    window.visualViewport?.addEventListener("resize", updateSizeMenuPosition);
    window.visualViewport?.addEventListener("scroll", updateSizeMenuPosition);
    return () => {
      window.removeEventListener("resize", updateSizeMenuPosition);
      window.visualViewport?.removeEventListener("resize", updateSizeMenuPosition);
      window.visualViewport?.removeEventListener("scroll", updateSizeMenuPosition);
    };
  }, [isSizeMenuOpen, isMobileViewport]);

  useEffect(() => {
    if (!isSizeMenuOpen) {
      return;
    }
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!sizeMenuRef.current?.contains(target) && !sizeMenuBtnRef.current?.contains(target)) {
        setIsSizeMenuOpen(false);
      }
    };
    window.addEventListener("pointerdown", handlePointerDown);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [isSizeMenuOpen]);

  const handleTextareaPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const imageFiles = Array.from(event.clipboardData.files).filter((file) => file.type.startsWith("image/"));
    if (imageFiles.length === 0) {
      return;
    }

    event.preventDefault();
    void onReferenceImageChange(imageFiles);
  };

  if (isDesktopComposerCollapsed && !isMobileViewport) {
    return (
      <button
        type="button"
        className="fixed bottom-6 right-6 z-40 hidden h-12 items-center gap-2 rounded-full border border-stone-200 bg-white/95 px-5 text-sm font-medium text-stone-700 shadow-[0_18px_65px_-34px_rgba(15,23,42,0.35)] backdrop-blur transition hover:border-stone-300 hover:bg-white hover:text-stone-950 sm:inline-flex"
        onClick={() => {
          setIsDesktopComposerCollapsed(false);
          setIsSizeMenuOpen(false);
          focusPromptTextarea(false);
        }}
        aria-label="打开创作区"
      >
        打开创作区
        <ArrowUp className="size-4" />
      </button>
    );
  }

  return (
    <div
      className={cn(
        "flex shrink-0 justify-center px-0.5 pb-[env(safe-area-inset-bottom)] sm:px-0 sm:pb-0",
        isMobileExpandedSheet && "fixed inset-x-0 bottom-0 z-40 px-2 pb-[calc(env(safe-area-inset-bottom)+0.5rem)]",
      )}
    >
      <div
        className={cn(
          "w-full max-w-[980px]",
          isMobileExpandedSheet &&
            "flex max-h-[88dvh] flex-col overflow-hidden rounded-[28px] border border-stone-200 bg-white shadow-[0_28px_120px_-44px_rgba(15,23,42,0.45)]",
        )}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          onChange={(event) => {
            void onReferenceImageChange(Array.from(event.target.files || []));
          }}
        />

        {referenceImages.length > 0 ? (
          <div className={cn("mb-2 sm:mb-3", isMobileExpandedSheet && "mb-0 shrink-0 border-b border-stone-100 px-3 pt-3 pb-2")}>
            <div className={cn("mb-2 px-1 text-xs font-medium text-stone-500 sm:hidden", isMobileExpandedSheet && "px-0")}>
              已添加参考图 {referenceImages.length} 张
            </div>
            <div className={cn("hide-scrollbar flex gap-2 overflow-x-auto px-1 pb-1 sm:flex-wrap sm:overflow-visible sm:pb-0", isMobileExpandedSheet && "px-0 pb-0")}>
              {referenceImages.map((image, index) => (
                <div key={`${image.name}-${index}`} className="relative size-16 shrink-0 sm:size-16">
                  <button
                    type="button"
                    onClick={() => {
                      setLightboxIndex(index);
                      setLightboxOpen(true);
                    }}
                    className="group size-16 overflow-hidden rounded-2xl border border-stone-200 bg-stone-50 transition hover:border-stone-300 sm:size-16"
                    aria-label={`预览参考图 ${image.name || index + 1}`}
                  >
                    <img
                      src={image.dataUrl}
                      alt={image.name || `参考图 ${index + 1}`}
                      className="h-full w-full object-cover"
                    />
                  </button>
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onRemoveReferenceImage(index);
                    }}
                    className="absolute -right-1 -top-1 inline-flex size-5 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-stone-300 hover:text-stone-800"
                    aria-label={`移除参考图 ${image.name || index + 1}`}
                  >
                    <X className="size-3" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {!isMobilePanelExpanded ? (
          <div className="rounded-[24px] border border-stone-200 bg-white/95 p-2 shadow-[0_18px_65px_-42px_rgba(15,23,42,0.45)] sm:hidden">
            <button
              type="button"
              className="flex h-12 w-full items-center justify-between gap-3 rounded-2xl bg-stone-50 px-4 text-left"
              onClick={expandMobilePanel}
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-stone-800">
                  {prompt.trim() || (referenceImages.length > 0 ? `已添加 ${referenceImages.length} 张参考图` : "输入提示词 / 上传图片")}
                </div>
                <div className="mt-0.5 text-[11px] text-stone-500">
                  额度 {availableQuota} · 单次最多 {batchLimit} 张 · 图片保存 10 天
                </div>
              </div>
              <span className="shrink-0 rounded-full bg-white px-3 py-1 text-xs font-medium text-stone-600 shadow-sm">
                展开
              </span>
            </button>
            <div className="mt-2 grid grid-cols-[minmax(0,1fr)_auto_auto] gap-2">
              <Button
                type="button"
                variant="outline"
                className="h-10 justify-center rounded-2xl border-stone-200 bg-white text-sm font-medium text-stone-700 shadow-none"
                onClick={() => {
                  setIsMobilePanelExpanded(true);
                  onPickReferenceImage();
                }}
              >
                <ImagePlus className="size-4" />
                上传图片
              </Button>
              {showPromptMarket ? (
                <Button
                  type="button"
                  variant="outline"
                  className="h-10 rounded-2xl border-stone-200 bg-white px-3 text-sm font-medium text-stone-700 shadow-none"
                  onClick={onOpenPromptMarket}
                  aria-label="打开提示词市场"
                  title="提示词市场"
                >
                  <Store className="size-4" />
                  <span className="sr-only">市场</span>
                </Button>
              ) : null}
              <button
                type="button"
                onClick={() => void submitAndCollapseMobilePanel()}
                disabled={!hasPrompt}
                className="inline-flex h-10 min-w-[104px] items-center justify-center gap-2 rounded-2xl bg-stone-950 px-4 text-sm font-medium text-white transition hover:bg-stone-800 disabled:cursor-not-allowed disabled:bg-stone-300"
              >
                <ArrowUp className="size-4" />
                {submitLabel}
              </button>
            </div>
          </div>
        ) : null}

        <div className={cn(
          "rounded-[24px] border border-stone-200 bg-white/95 shadow-[0_18px_65px_-42px_rgba(15,23,42,0.45)] sm:rounded-[32px] sm:shadow-none",
          !isMobilePanelExpanded && "hidden sm:block",
          isMobileExpandedSheet && "flex min-h-0 flex-1 flex-col overflow-hidden rounded-none border-0 bg-transparent shadow-none",
        )}>
          <div className="flex items-center justify-between gap-2 border-b border-stone-100 px-3 py-2 sm:hidden">
            <div className="min-w-0">
              <div className="text-xs font-semibold text-stone-700">创作面板</div>
              <div className="mt-0.5 truncate text-[11px] text-stone-500">可随时收起，方便查看上方图片</div>
            </div>
            <button
              type="button"
              className="inline-flex h-8 shrink-0 items-center gap-1 rounded-full bg-stone-100 px-3 text-xs font-medium text-stone-600"
              onClick={() => {
                setIsMobilePanelExpanded(false);
                setIsSizeMenuOpen(false);
              }}
            >
              收起
              <ChevronDown className="size-3.5" />
            </button>
          </div>
          <div
            ref={composerBodyRef}
            className={cn("relative cursor-text", isMobileExpandedSheet && "min-h-0 flex-1 overflow-y-auto")}
            onClick={() => {
              textareaRef.current?.focus();
            }}
          >
            <ImageLightbox
              images={lightboxImages}
              currentIndex={lightboxIndex}
              open={lightboxOpen}
              onOpenChange={setLightboxOpen}
              onIndexChange={setLightboxIndex}
            />
            <Textarea
              ref={textareaRef}
              value={prompt}
              onChange={(event) => onPromptChange(event.target.value)}
              onPaste={handleTextareaPaste}
              placeholder={
                referenceImages.length > 0
                  ? "描述你希望如何修改参考图"
                  : "输入你想要生成的画面，也可直接粘贴图片"
              }
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void submitAndCollapseMobilePanel();
                }
              }}
              className={cn(
                "min-h-[78px] resize-none rounded-[24px] border-0 bg-transparent px-4 pt-4 pb-3 text-[16px] leading-6 text-stone-900 shadow-none placeholder:text-stone-400 focus-visible:ring-0 sm:min-h-[148px] sm:rounded-[32px] sm:px-6 sm:pt-6 sm:pb-6 sm:text-[15px] sm:leading-7",
                isMobileExpandedSheet && "min-h-[180px] rounded-none px-4 pt-4 pb-4",
              )}
            />

            <div
              className={cn(
                "border-t border-stone-100 bg-white px-3 pb-3 pt-3 sm:px-6 sm:pb-4 sm:pt-4",
                isMobileExpandedSheet && "sticky bottom-0 mt-3 border-t border-stone-100 bg-white/98 backdrop-blur supports-[backdrop-filter]:bg-white/90",
              )}
              onClick={(event) => event.stopPropagation()}
            >
              <div className="flex flex-wrap gap-2 sm:hidden">
                <div className="rounded-full bg-stone-100/90 px-3 py-1.5 text-xs text-stone-500">
                  剩余额度 <span className="font-semibold text-stone-900">{availableQuota}</span>
                </div>
                <div className="rounded-full bg-stone-100/90 px-3 py-1.5 text-xs text-stone-500">
                  单次最多 <span className="font-semibold text-stone-900">{batchLimit}</span> 张
                </div>
                <div className="max-w-full rounded-full bg-stone-100/90 px-3 py-1.5 text-xs text-stone-500">
                  当前令牌 <span className="font-semibold text-stone-900">{tokenName || "-"}</span>
                </div>
                <div className="rounded-full bg-amber-50 px-3 py-1.5 text-xs text-amber-700">
                  图片保存 10 天
                </div>
                <div className="rounded-2xl bg-sky-50 px-3 py-1.5 text-xs leading-5 text-sky-700">
                  系统处理中 {systemProcessingCount} 个（运行 {systemRunningCount} / 排队 {systemQueuedCount}）
                  {systemUpstreamConcurrency ? ` · 上游并发 ${systemActiveUpstreamSlots}/${systemUpstreamConcurrency}` : ""}
                  {accountCooldownCount ? ` · 冷却账号 ${accountCooldownCount}` : ""}
                  {accountInvalidCachedCount ? ` · 已跳过失效账号 ${accountInvalidCachedCount}` : ""}
                  {systemEstimatedWaitText ? ` · 预计等待 ${systemEstimatedWaitText}` : ""}
                  {systemAverageDurationText ? ` · 平均每张 ${systemAverageDurationText}` : ""}
                </div>
                {recentQuotaUsageText ? (
                  <div className="rounded-2xl bg-emerald-50 px-3 py-1.5 text-xs leading-5 text-emerald-700">
                    {recentQuotaUsageText}
                  </div>
                ) : null}
                {activeTaskCount > 0 ? (
                  <div className="flex items-center gap-2 rounded-full bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-700">
                    <LoaderCircle className="size-3.5 animate-spin" />
                    {activeTaskCount} 个任务处理中
                  </div>
                ) : null}
              </div>

              <div className="mt-3 flex flex-col gap-2 sm:mt-0 sm:flex-row sm:items-end sm:justify-between sm:gap-3">
                <div className="flex min-w-0 flex-col gap-2 sm:flex-1 sm:flex-row sm:flex-wrap sm:items-center sm:gap-3">
                  <Button
                    type="button"
                    variant="outline"
                    className="h-11 justify-start rounded-2xl border-stone-200 bg-white px-4 text-sm font-medium text-stone-700 shadow-none sm:h-10 sm:w-auto sm:justify-center sm:rounded-full sm:px-4 sm:text-sm"
                    onClick={onPickReferenceImage}
                    aria-label={referenceImages.length > 0 ? "添加参考图" : "上传"}
                  >
                    <ImagePlus className="size-4" />
                    <span>{referenceImages.length > 0 ? "添加参考图" : "上传图片"}</span>
                  </Button>
                  {showPromptMarket ? (
                    <Button
                      type="button"
                      variant="outline"
                      className="h-11 justify-start rounded-2xl border-stone-200 bg-white px-4 text-sm font-medium text-stone-700 shadow-none sm:h-10 sm:w-auto sm:justify-center sm:rounded-full sm:px-4 sm:text-sm"
                      onClick={onOpenPromptMarket}
                      aria-label="打开提示词市场"
                      title="提示词市场"
                    >
                      <Store className="size-4" />
                      <span>市场</span>
                    </Button>
                  ) : null}

                  <div className="hidden shrink-0 rounded-full bg-stone-100 px-3 py-2 text-xs font-medium text-stone-600 sm:block">
                    剩余额度 {availableQuota}
                  </div>
                  <div className="hidden shrink-0 rounded-full bg-stone-100 px-3 py-2 text-xs font-medium text-stone-600 sm:block">
                    单次最多 {batchLimit} 张
                  </div>
                  <div className="hidden max-w-[260px] shrink-0 items-center rounded-full bg-stone-100 px-3 py-2 text-xs font-medium text-stone-600 sm:flex">
                    <span className="mr-1">当前令牌</span>
                    <span className="truncate">{tokenName || "-"}</span>
                  </div>
                  <div className="hidden shrink-0 rounded-full bg-amber-50 px-3 py-2 text-xs font-medium text-amber-700 sm:block">
                    图片保存 10 天
                  </div>
                  <div className="hidden max-w-full shrink-0 rounded-full bg-sky-50 px-3 py-2 text-xs font-medium text-sky-700 sm:block">
                    系统处理中 {systemProcessingCount} 个（运行 {systemRunningCount} / 排队 {systemQueuedCount}）
                    {systemUpstreamConcurrency ? ` · 上游并发 ${systemActiveUpstreamSlots}/${systemUpstreamConcurrency}` : ""}
                    {accountCooldownCount ? ` · 冷却账号 ${accountCooldownCount}` : ""}
                    {accountInvalidCachedCount ? ` · 已跳过失效账号 ${accountInvalidCachedCount}` : ""}
                    {systemEstimatedWaitText ? ` · 预计等待 ${systemEstimatedWaitText}` : ""}
                    {systemAverageDurationText ? ` · 平均每张 ${systemAverageDurationText}` : ""}
                  </div>
                  {recentQuotaUsageText ? (
                    <div className="hidden max-w-full shrink-0 rounded-full bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-700 sm:block">
                      {recentQuotaUsageText}
                    </div>
                  ) : null}
                  {activeTaskCount > 0 ? (
                    <div className="hidden shrink-0 items-center gap-1.5 rounded-full bg-amber-50 px-3 py-2 text-xs font-medium text-amber-700 sm:flex">
                      <LoaderCircle className="size-3 animate-spin" />
                      {activeTaskCount} 个任务处理中
                    </div>
                  ) : null}

                  <div className="grid grid-cols-[108px_minmax(0,1fr)] gap-2 sm:flex sm:min-w-0 sm:items-center sm:gap-3">
                    <div className="flex h-11 items-center justify-between rounded-2xl border border-stone-200 bg-white px-3 sm:h-10 sm:min-w-[112px] sm:rounded-full">
                      <span className="hidden text-xs font-medium text-stone-500 sm:inline">张数</span>
                      <Input
                        type="number"
                        inputMode="numeric"
                        min="1"
                        max={String(Math.max(1, batchLimit))}
                        step="1"
                        value={imageCount}
                        onChange={(event) => onImageCountChange(event.target.value)}
                        className="h-8 w-12 border-0 bg-transparent px-0 text-right text-sm font-semibold text-stone-800 shadow-none focus-visible:ring-0 sm:h-7 sm:w-[52px] sm:text-center sm:text-sm"
                      />
                    </div>

                    <div className="relative min-w-0 sm:min-w-[148px]">
                      <button
                        ref={sizeMenuBtnRef}
                        type="button"
                        className="flex h-11 w-full items-center justify-between rounded-2xl border border-stone-200 bg-white px-3 text-left shadow-none sm:h-10 sm:rounded-full"
                        onClick={(event) => {
                          event.stopPropagation();
                          if (!isSizeMenuOpen && !isMobileViewport) {
                            updateSizeMenuPosition();
                          }
                          setIsSizeMenuOpen((open) => !open);
                        }}
                      >
                        <span className="min-w-0">
                          <span className="block text-[11px] leading-none text-stone-500 sm:hidden">比例</span>
                          <span className="block truncate text-sm font-semibold text-stone-800 sm:text-xs">{imageSizeLabel}</span>
                        </span>
                        <ChevronDown className={cn("size-4 shrink-0 opacity-60 transition", isSizeMenuOpen && "rotate-180")} />
                      </button>
                      {isSizeMenuOpen ? (
                        isMobileViewport ? (
                          <div
                            className="fixed inset-0 z-[120] flex min-h-[100svh] items-center justify-center bg-stone-950/35 px-4 py-8 backdrop-blur-[2px]"
                            onClick={() => setIsSizeMenuOpen(false)}
                          >
                            <div
                              ref={sizeMenuRef}
                              role="dialog"
                              aria-modal="true"
                              aria-label="选择图片比例"
                              className="w-full max-w-[360px] rounded-[30px] border border-white/80 bg-white p-3 shadow-[0_30px_100px_-40px_rgba(15,23,42,0.55)]"
                              onClick={(event) => event.stopPropagation()}
                              onMouseDown={(event) => event.stopPropagation()}
                            >
                              <div className="mb-2 flex items-center justify-between gap-3 px-2 py-1">
                                <div>
                                  <div className="text-base font-semibold text-stone-950">选择图片比例</div>
                                  <div className="mt-0.5 text-xs text-stone-500">手机端使用弹窗选择，避免被底部工具栏遮挡</div>
                                </div>
                                <button
                                  type="button"
                                  className="inline-flex size-9 shrink-0 items-center justify-center rounded-full bg-stone-100 text-stone-500"
                                  onClick={() => setIsSizeMenuOpen(false)}
                                  aria-label="关闭尺寸选择"
                                >
                                  <X className="size-4" />
                                </button>
                              </div>
                              <div className="grid grid-cols-2 gap-2">
                                {imageSizeOptions.map((option) => {
                                  const active = option.value === imageSize;
                                  return (
                                    <button
                                      key={option.label}
                                      type="button"
                                      className={cn(
                                        "flex min-h-16 items-center justify-between gap-2 rounded-3xl border px-4 py-3 text-left transition",
                                        active
                                          ? "border-stone-950 bg-stone-950 text-white shadow-[0_16px_40px_-24px_rgba(15,23,42,0.7)]"
                                          : "border-stone-200 bg-stone-50 text-stone-800 active:bg-stone-100",
                                      )}
                                      onClick={(event) => {
                                        event.stopPropagation();
                                        onImageSizeChange(option.value);
                                        setIsSizeMenuOpen(false);
                                      }}
                                    >
                                      <span>
                                        <span className="block text-sm font-semibold">{option.value || "默认"}</span>
                                        <span className={cn("mt-1 block text-[11px]", active ? "text-white/70" : "text-stone-500")}>
                                          {option.label.replace(option.value || "未指定", "").replace(/[（）]/g, "") || "不指定比例"}
                                        </span>
                                      </span>
                                      {active ? <Check className="size-5 shrink-0" /> : null}
                                    </button>
                                  );
                                })}
                              </div>
                            </div>
                          </div>
                        ) : (
                          <div
                            ref={sizeMenuRef}
                            className="fixed z-[90] max-h-[45dvh] overflow-y-auto rounded-3xl border border-white/80 bg-white p-2 shadow-[0_24px_80px_-32px_rgba(15,23,42,0.35)]"
                            style={{
                              top: sizeMenuPos.top,
                              left: sizeMenuPos.left,
                              width: "min(210px, calc(100vw - 2rem))",
                            }}
                            onClick={(event) => event.stopPropagation()}
                            onMouseDown={(event) => event.stopPropagation()}
                          >
                            {imageSizeOptions.map((option) => {
                              const active = option.value === imageSize;
                              return (
                                <button
                                  key={option.label}
                                  type="button"
                                  className={cn(
                                    "flex w-full items-center justify-between rounded-2xl px-3 py-2 text-left text-sm text-stone-700 transition hover:bg-stone-100",
                                    active && "bg-stone-100 font-medium text-stone-950",
                                  )}
                                  onMouseDown={(event) => event.preventDefault()}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    onImageSizeChange(option.value);
                                    setIsSizeMenuOpen(false);
                                  }}
                                >
                                  <span>{option.label}</span>
                                  {active ? <Check className="size-4" /> : null}
                                </button>
                              );
                            })}
                          </div>
                        )
                      ) : null}
                    </div>
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => {
                    setIsSizeMenuOpen(false);
                    setIsDesktopComposerCollapsed(true);
                  }}
                  className="hidden h-10 shrink-0 items-center justify-center rounded-full border border-stone-200 bg-white px-3 text-xs font-medium text-stone-500 transition hover:border-stone-300 hover:text-stone-900 sm:inline-flex"
                  aria-label="收起"
                >
                  收起
                  <ChevronDown className="ml-1 size-4" />
                </button>

                <button
                  type="button"
                  onClick={() => void submitAndCollapseMobilePanel()}
                  disabled={!hasPrompt}
                  className="inline-flex h-11 w-full shrink-0 items-center justify-center gap-2 rounded-2xl bg-stone-950 px-4 text-sm font-medium text-white transition hover:bg-stone-800 disabled:cursor-not-allowed disabled:bg-stone-300 sm:size-11 sm:w-11 sm:rounded-full sm:px-0"
                  aria-label={referenceImages.length > 0 ? "编辑图片" : "生成图片"}
                >
                  <ArrowUp className="size-4" />
                  <span className="sm:hidden">{submitLabel}</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
