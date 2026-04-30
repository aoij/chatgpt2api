"use client";

import { ArrowUp, Check, ChevronDown, ImagePlus, LoaderCircle, X } from "lucide-react";
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
  referenceImages: Array<{ name: string; dataUrl: string }>;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  fileInputRef: RefObject<HTMLInputElement | null>;
  onPromptChange: (value: string) => void;
  onImageCountChange: (value: string) => void;
  onImageSizeChange: (value: string) => void;
  onSubmit: () => void | Promise<void>;
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
  referenceImages,
  textareaRef,
  fileInputRef,
  onPromptChange,
  onImageCountChange,
  onImageSizeChange,
  onSubmit,
  onPickReferenceImage,
  onReferenceImageChange,
  onRemoveReferenceImage,
}: ImageComposerProps) {
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [isSizeMenuOpen, setIsSizeMenuOpen] = useState(false);
  const [isMobilePanelExpanded, setIsMobilePanelExpanded] = useState(false);
  const sizeMenuRef = useRef<HTMLDivElement>(null);
  const lightboxImages = useMemo(
    () => referenceImages.map((image, index) => ({ id: `${image.name}-${index}`, src: image.dataUrl })),
    [referenceImages],
  );
  const imageSizeLabel = imageSizeOptions.find((option) => option.value === imageSize)?.label || "未指定";
  const submitLabel = referenceImages.length > 0 ? "开始编辑" : "开始生图";
  const hasPrompt = Boolean(prompt.trim());

  const expandMobilePanel = () => {
    setIsMobilePanelExpanded(true);
    window.setTimeout(() => textareaRef.current?.focus(), 0);
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
    if (referenceImages.length > 0) {
      setIsMobilePanelExpanded(true);
    }
  }, [referenceImages.length]);

  useEffect(() => {
    if (!isSizeMenuOpen) {
      return;
    }
    const handlePointerDown = (event: MouseEvent) => {
      if (!sizeMenuRef.current?.contains(event.target as Node)) {
        setIsSizeMenuOpen(false);
      }
    };
    window.addEventListener("mousedown", handlePointerDown);
    return () => {
      window.removeEventListener("mousedown", handlePointerDown);
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

  return (
    <div className="flex shrink-0 justify-center px-0.5 pb-[env(safe-area-inset-bottom)] sm:px-0 sm:pb-0">
      <div className="w-full max-w-[980px]">
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
          <div className="mb-2 sm:mb-3">
            <div className="mb-2 px-1 text-xs font-medium text-stone-500 sm:hidden">
              已添加参考图 {referenceImages.length} 张
            </div>
            <div className="hide-scrollbar flex gap-2 overflow-x-auto px-1 pb-1 sm:flex-wrap sm:overflow-visible sm:pb-0">
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
                  额度 {availableQuota} · 图片保存 10 天
                </div>
              </div>
              <span className="shrink-0 rounded-full bg-white px-3 py-1 text-xs font-medium text-stone-600 shadow-sm">
                展开
              </span>
            </button>
            <div className="mt-2 grid grid-cols-[minmax(0,1fr)_auto] gap-2">
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
            className="relative cursor-text"
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
              className="min-h-[78px] resize-none rounded-[24px] border-0 bg-transparent px-4 pt-4 pb-3 text-[16px] leading-6 text-stone-900 shadow-none placeholder:text-stone-400 focus-visible:ring-0 sm:min-h-[148px] sm:rounded-[32px] sm:px-6 sm:pt-6 sm:pb-20 sm:text-[15px] sm:leading-7"
            />

            <div
              className="border-t border-stone-100 bg-white px-3 pb-3 pt-3 sm:absolute sm:inset-x-0 sm:bottom-0 sm:border-t-0 sm:bg-gradient-to-t sm:from-white sm:via-white/95 sm:to-transparent sm:px-6 sm:pb-4 sm:pt-6"
              onClick={(event) => event.stopPropagation()}
            >
              <div className="flex flex-wrap gap-2 sm:hidden">
                <div className="rounded-full bg-stone-100/90 px-3 py-1.5 text-xs text-stone-500">
                  剩余额度 <span className="font-semibold text-stone-900">{availableQuota}</span>
                </div>
                <div className="max-w-full rounded-full bg-stone-100/90 px-3 py-1.5 text-xs text-stone-500">
                  当前令牌 <span className="font-semibold text-stone-900">{tokenName || "-"}</span>
                </div>
                <div className="rounded-full bg-amber-50 px-3 py-1.5 text-xs text-amber-700">
                  图片保存 10 天
                </div>
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
                  >
                    <ImagePlus className="size-4" />
                    <span>{referenceImages.length > 0 ? "添加参考图" : "上传图片"}</span>
                  </Button>

                  <div className="hidden shrink-0 rounded-full bg-stone-100 px-3 py-2 text-xs font-medium text-stone-600 sm:block">
                    剩余额度 {availableQuota}
                  </div>
                  <div className="hidden max-w-[260px] shrink-0 items-center rounded-full bg-stone-100 px-3 py-2 text-xs font-medium text-stone-600 sm:flex">
                    <span className="mr-1">当前令牌</span>
                    <span className="truncate">{tokenName || "-"}</span>
                  </div>
                  <div className="hidden shrink-0 rounded-full bg-amber-50 px-3 py-2 text-xs font-medium text-amber-700 sm:block">
                    图片保存 10 天
                  </div>
                  {activeTaskCount > 0 ? (
                    <div className="hidden shrink-0 items-center gap-1.5 rounded-full bg-amber-50 px-3 py-2 text-xs font-medium text-amber-700 sm:flex">
                      <LoaderCircle className="size-3 animate-spin" />
                      {activeTaskCount} 个任务处理中
                    </div>
                  ) : null}

                  <div className="grid grid-cols-[108px_minmax(0,1fr)] gap-2 sm:flex sm:min-w-0 sm:items-center sm:gap-3">
                    <div className="flex h-11 items-center justify-between rounded-2xl border border-stone-200 bg-white px-3 sm:h-10 sm:min-w-[112px] sm:rounded-full">
                      <span className="text-xs font-medium text-stone-500">张数</span>
                      <Input
                        type="number"
                        inputMode="numeric"
                        min="1"
                        max="100"
                        step="1"
                        value={imageCount}
                        onChange={(event) => onImageCountChange(event.target.value)}
                        className="h-8 w-12 border-0 bg-transparent px-0 text-right text-sm font-semibold text-stone-800 shadow-none focus-visible:ring-0 sm:h-7 sm:w-[52px] sm:text-center sm:text-sm"
                      />
                    </div>

                    <div ref={sizeMenuRef} className="relative min-w-0 sm:min-w-[148px]">
                      <button
                        type="button"
                        className="flex h-11 w-full items-center justify-between rounded-2xl border border-stone-200 bg-white px-3 text-left shadow-none sm:h-10 sm:rounded-full"
                        onClick={() => setIsSizeMenuOpen((open) => !open)}
                      >
                        <span className="min-w-0">
                          <span className="block text-[11px] leading-none text-stone-500 sm:hidden">比例</span>
                          <span className="block truncate text-sm font-semibold text-stone-800 sm:text-xs">{imageSizeLabel}</span>
                        </span>
                        <ChevronDown className={cn("size-4 shrink-0 opacity-60 transition", isSizeMenuOpen && "rotate-180")} />
                      </button>
                      {isSizeMenuOpen ? (
                        <div className="fixed inset-x-4 bottom-[calc(env(safe-area-inset-bottom)+4.75rem)] z-[80] max-h-[45dvh] overflow-y-auto rounded-3xl border border-white/80 bg-white p-2 shadow-[0_24px_80px_-32px_rgba(15,23,42,0.35)] sm:absolute sm:inset-x-auto sm:bottom-[calc(100%+10px)] sm:left-0 sm:w-[210px]">
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
                                onClick={() => {
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
                      ) : null}
                    </div>
                  </div>
                </div>

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
