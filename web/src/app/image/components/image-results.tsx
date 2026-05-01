"use client";

import { useState } from "react";
import { Clock3, LoaderCircle, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getManagedImagePathFromUrl, type PublicConfig } from "@/lib/api";
import type { ImageConversation, ImageTurnStatus, StoredImage, StoredReferenceImage } from "@/store/image-conversations";

export type ImageLightboxItem = {
  id: string;
  src: string;
  sizeLabel?: string;
  dimensions?: string;
  downloadPath?: string;
  filename?: string;
};

type ImageResultsProps = {
  selectedConversation: ImageConversation | null;
  onOpenLightbox: (images: ImageLightboxItem[], index: number) => void;
  onContinueEdit: (conversationId: string, image: StoredImage | StoredReferenceImage) => void;
  formatConversationTime: (value: string) => string;
  publicConfig?: PublicConfig | null;
};

function getStoredImageSrc(image: StoredImage) {
  if (image.b64_json) {
    return `data:image/png;base64,${image.b64_json}`;
  }
  return image.url || "";
}

export function ImageResults({
  selectedConversation,
  onOpenLightbox,
  onContinueEdit,
  formatConversationTime,
  publicConfig,
}: ImageResultsProps) {
  const [imageDimensions, setImageDimensions] = useState<Record<string, string>>({});

  const updateImageDimensions = (id: string, width: number, height: number) => {
    const dimensions = formatImageDimensions(width, height);
    setImageDimensions((current) => {
      if (current[id] === dimensions) {
        return current;
      }
      return { ...current, [id]: dimensions };
    });
  };

  if (!selectedConversation) {
    return (
      <div className="flex h-full min-h-[260px] items-center justify-center text-center sm:min-h-[420px]">
        <div className="w-full max-w-4xl px-2">
          <h1
            className="text-2xl font-semibold tracking-tight text-stone-950 sm:text-3xl md:text-5xl"
            style={{
              fontFamily: '"Palatino Linotype","Book Antiqua","URW Palladio L","Times New Roman",serif',
            }}
          >
            {publicConfig?.image_page_title || "Turn ideas into images"}
          </h1>
          <p
            className="mx-auto mt-3 max-w-[300px] text-sm italic tracking-[0.01em] text-stone-500 sm:mt-4 sm:max-w-none sm:text-[15px]"
            style={{
              fontFamily: '"Palatino Linotype","Book Antiqua","URW Palladio L","Times New Roman",serif',
            }}
          >
            {publicConfig?.image_page_subtitle || "在同一窗口里保留本地历史与任务状态，并从已有结果图继续发起新的无状态编辑。"}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-[980px] flex-col gap-4 sm:gap-8">
      {selectedConversation.turns.map((turn, turnIndex) => {
        const referenceLightboxImages = turn.referenceImages.map((image, index) => ({
          id: `${turn.id}-reference-${index}`,
          src: image.dataUrl,
        }));
        const successfulTurnImages = turn.images.flatMap((image) => {
          const src = image.status === "success" ? getStoredImageSrc(image) : "";
          return src
            ? [
                {
                  id: image.id,
                  src,
                  sizeLabel: image.b64_json ? formatBase64ImageSize(image.b64_json) : undefined,
                  dimensions: imageDimensions[image.id],
                  downloadPath: image.url ? getManagedImagePathFromUrl(image.url) : undefined,
                  filename: image.url ? `${image.id}.jpg` : undefined,
                },
              ]
            : [];
        });

        return (
          <div key={turn.id} className="rounded-[28px] border border-white/80 bg-white/75 p-3 shadow-sm sm:rounded-none sm:border-0 sm:bg-transparent sm:p-0 sm:shadow-none">
            <div className="flex flex-col gap-3 sm:gap-4">
              <div className="flex justify-end">
                <div className="w-full max-w-full rounded-[24px] bg-stone-50/90 px-4 py-3 text-[14px] leading-6 text-stone-900 shadow-sm sm:max-w-[82%] sm:rounded-none sm:bg-transparent sm:px-1 sm:py-1 sm:text-[15px] sm:leading-7 sm:shadow-none">
                  <div className="mb-2 flex flex-wrap justify-end gap-2 text-[11px] text-stone-400 sm:mb-2">
                    <span>第 {turnIndex + 1} 轮</span>
                    <span>{turn.mode === "edit" ? "编辑图" : "文生图"}</span>
                    <span>{getTurnStatusLabel(turn.status)}</span>
                    <span>{formatConversationTime(turn.createdAt)}</span>
                  </div>
                  <div className="text-right">{turn.prompt}</div>
                </div>
              </div>

              <div className="flex justify-start">
                <div className="w-full">
                  {turn.referenceImages.length > 0 ? (
                    <div className="mb-4 rounded-[24px] border border-stone-200/70 bg-stone-50/70 p-3 sm:rounded-none sm:border-0 sm:bg-transparent sm:p-0">
                      <div className="mb-3 text-xs font-medium text-stone-500">本轮参考图</div>
                      <div className="grid grid-cols-2 gap-3 sm:flex sm:flex-wrap sm:justify-end">
                        {turn.referenceImages.map((image, index) => (
                          <div key={`${turn.id}-${image.name}-${index}`} className="flex flex-col gap-2">
                            <button
                              type="button"
                              onClick={() => onOpenLightbox(referenceLightboxImages, index)}
                              className="group relative aspect-square w-full overflow-hidden rounded-2xl border border-stone-200/80 bg-stone-100/60 text-left transition hover:border-stone-300 sm:h-24 sm:w-24 sm:rounded-none"
                              aria-label={`预览参考图 ${image.name || index + 1}`}
                            >
                              <img
                                src={image.dataUrl}
                                alt={image.name || `参考图 ${index + 1}`}
                                className="absolute inset-0 h-full w-full object-cover transition duration-200 group-hover:scale-[1.02]"
                              />
                            </button>
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-9 w-full rounded-full border-stone-200 bg-white text-stone-700 hover:bg-stone-50 sm:w-auto"
                              onClick={() => onContinueEdit(selectedConversation.id, image)}
                            >
                              <Sparkles className="size-4" />
                              加入编辑
                            </Button>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  <div className="mb-3 flex flex-wrap items-center gap-1.5 text-[11px] text-stone-500 sm:mb-4 sm:gap-2 sm:text-xs">
                    <span className="rounded-full bg-stone-100 px-3 py-1">{turn.count} 张</span>
                    <span className="rounded-full bg-stone-100 px-3 py-1">{getTurnStatusLabel(turn.status)}</span>
                    {turn.status === "queued" ? (
                      <span className="rounded-full bg-amber-50 px-3 py-1 text-amber-700">等待当前对话中的前序任务完成</span>
                    ) : null}
                  </div>

                  <div className="columns-1 gap-3 space-y-3 sm:columns-2 sm:gap-4 sm:space-y-4 xl:columns-3">
                    {turn.images.map((image, index) => {
                      const imageSrc = image.status === "success" ? getStoredImageSrc(image) : "";
                      if (image.status === "success" && imageSrc) {
                        const currentIndex = successfulTurnImages.findIndex((item) => item.id === image.id);
                        const sizeLabel = image.b64_json ? formatBase64ImageSize(image.b64_json) : "";
                        const dimensions = imageDimensions[image.id];
                        const imageMeta = [sizeLabel, dimensions].filter(Boolean).join(" · ");

                        return (
                          <div
                            key={image.id}
                            className="break-inside-avoid overflow-hidden rounded-[26px] border border-stone-200/70 bg-white shadow-sm sm:rounded-none sm:border-0 sm:bg-transparent sm:shadow-none"
                          >
                            <button
                              type="button"
                              onClick={() => onOpenLightbox(successfulTurnImages, currentIndex)}
                              className="group block w-full cursor-zoom-in"
                            >
                              <img
                                src={imageSrc}
                                alt={`Generated result ${index + 1}`}
                                className="block h-auto w-full transition duration-200 group-hover:brightness-90"
                                onLoad={(event) => {
                                  updateImageDimensions(
                                    image.id,
                                    event.currentTarget.naturalWidth,
                                    event.currentTarget.naturalHeight,
                                  );
                                }}
                              />
                            </button>
                            <div className="flex flex-col gap-3 px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
                              <div className="min-w-0 text-xs text-stone-500">
                                <span>结果 {index + 1}</span>
                                {imageMeta ? <span className="ml-2 text-stone-400">{imageMeta}</span> : null}
                              </div>
                              <Button
                                variant="outline"
                                size="sm"
                                className="h-9 w-full rounded-full border-stone-200 bg-white text-stone-700 hover:bg-stone-50 sm:w-auto"
                                onClick={() => onContinueEdit(selectedConversation.id, image)}
                              >
                                <Sparkles className="size-4" />
                                加入编辑
                              </Button>
                            </div>
                          </div>
                        );
                      }

                      if (image.status === "error") {
                        return (
                          <div
                            key={image.id}
                            className={cn(
                              "break-inside-avoid overflow-hidden rounded-[26px] border border-rose-200 bg-rose-50",
                              turn.size === "1:1" && "sm:aspect-square",
                              turn.size === "16:9" && "sm:aspect-video",
                              turn.size === "9:16" && "sm:aspect-[9/16]",
                              turn.size === "4:3" && "sm:aspect-[4/3]",
                              turn.size === "3:4" && "sm:aspect-[3/4]",
                              !["1:1", "16:9", "9:16", "4:3", "3:4"].includes(turn.size) && "sm:aspect-square",
                            )}
                          >
                            <div className="flex h-full min-h-24 items-center justify-center px-4 py-5 text-center text-sm leading-6 text-rose-600 sm:min-h-16 sm:px-6 sm:py-8">
                              {image.error || "生成失败"}
                            </div>
                          </div>
                        );
                      }

                      return (
                        <div
                          key={image.id}
                          className={cn(
                            "break-inside-avoid overflow-hidden rounded-[26px] border border-stone-200/80 bg-stone-100/80",
                            turn.size === "1:1" && "aspect-square",
                            turn.size === "16:9" && "aspect-video",
                            turn.size === "9:16" && "aspect-[9/16]",
                            turn.size === "4:3" && "aspect-[4/3]",
                            turn.size === "3:4" && "aspect-[3/4]",
                            !["1:1", "16:9", "9:16", "4:3", "3:4"].includes(turn.size) && "aspect-square",
                          )}
                        >
                          <div className="flex h-full flex-col items-center justify-center gap-3 px-6 py-8 text-center text-stone-500">
                            <div className="rounded-full bg-white p-3 shadow-sm">
                              {turn.status === "queued" ? (
                                <Clock3 className="size-5" />
                              ) : (
                                <LoaderCircle className="size-5 animate-spin" />
                              )}
                            </div>
                            <p className="text-sm">{turn.status === "queued" ? "已加入当前对话队列..." : "正在处理图片..."}</p>
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  {turn.status === "error" && turn.error ? (
                    <div className="mt-4 rounded-2xl border-l-4 border-amber-300 bg-amber-50/70 px-4 py-3 text-sm leading-6 text-amber-700">
                      {turn.error}
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function getTurnStatusLabel(status: ImageTurnStatus) {
  if (status === "queued") {
    return "排队中";
  }
  if (status === "generating") {
    return "处理中";
  }
  if (status === "success") {
    return "已完成";
  }
  return "失败";
}

function formatBase64ImageSize(base64: string) {
  const normalized = base64.replace(/\s/g, "");
  const padding = normalized.endsWith("==") ? 2 : normalized.endsWith("=") ? 1 : 0;
  const bytes = Math.max(0, Math.floor((normalized.length * 3) / 4) - padding);

  if (bytes >= 1024 * 1024) {
    return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  }
  if (bytes >= 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${bytes} B`;
}

function formatImageDimensions(width: number, height: number) {
  return `${width} × ${height}`;
}
