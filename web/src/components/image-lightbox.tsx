"use client";

import { useCallback, useEffect, useState } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { ChevronLeft, ChevronRight, Download, RotateCcw, RotateCw, X } from "lucide-react";
import { toast } from "sonner";

import { downloadManagedImage } from "@/lib/api";
import { defaultImageDownloadName, saveBlobAsFile } from "@/lib/download";
import { cn } from "@/lib/utils";

type LightboxImage = {
  id: string;
  src: string;
  sizeLabel?: string;
  dimensions?: string;
  downloadPath?: string;
  filename?: string;
};

type ImageLightboxProps = {
  images: LightboxImage[];
  currentIndex: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onIndexChange: (index: number) => void;
};

export function ImageLightbox({
  images,
  currentIndex,
  open,
  onOpenChange,
  onIndexChange,
}: ImageLightboxProps) {
  const current = images[currentIndex];
  const [rotation, setRotation] = useState(0);
  const [isDownloading, setIsDownloading] = useState(false);
  const hasPrev = currentIndex > 0;
  const hasNext = currentIndex < images.length - 1;

  const goPrev = useCallback(() => {
    if (hasPrev) onIndexChange(currentIndex - 1);
  }, [hasPrev, currentIndex, onIndexChange]);

  const goNext = useCallback(() => {
    if (hasNext) onIndexChange(currentIndex + 1);
  }, [hasNext, currentIndex, onIndexChange]);

  useEffect(() => {
    if (!open) return;
    setRotation(0);

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        goPrev();
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        goNext();
      } else if (e.key.toLowerCase() === "r") {
        e.preventDefault();
        setRotation((value) => value + 90);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, goPrev, goNext, currentIndex]);

  const handleDownload = useCallback(async () => {
    if (!current) return;
    setIsDownloading(true);
    try {
      if (current.downloadPath) {
        const data = await downloadManagedImage(current.downloadPath);
        saveBlobAsFile(data.blob, data.filename || current.filename || defaultImageDownloadName(current.id, "jpg"));
        toast.success("已下载 JPG 图片");
        return;
      }

      if (current.src.startsWith("data:")) {
        const response = await fetch(current.src);
        const blob = await response.blob();
        saveBlobAsFile(blob, current.filename || defaultImageDownloadName(current.id, "png"));
        toast.success("已下载图片");
        return;
      }

      const link = document.createElement("a");
      link.href = current.src;
      link.download = current.filename || defaultImageDownloadName(current.id, "jpg");
      link.rel = "noopener";
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "下载图片失败");
    } finally {
      setIsDownloading(false);
    }
  }, [current]);

  if (!current) return null;

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          className="fixed inset-0 z-50 flex items-center justify-center outline-none"
          onPointerDownOutside={(e) => e.preventDefault()}
        >
          <DialogPrimitive.Title className="sr-only">
            图片预览
          </DialogPrimitive.Title>

          {/* toolbar */}
          <div className="absolute top-4 right-4 z-10 flex items-center gap-2">
            {current.sizeLabel || current.dimensions ? (
              <span className="rounded-full bg-black/50 px-3 py-1.5 text-xs font-medium text-white/90">
                {[current.sizeLabel, current.dimensions].filter(Boolean).join(" · ")}
              </span>
            ) : null}
            {images.length > 1 && (
              <span className="rounded-full bg-black/50 px-3 py-1.5 text-xs font-medium text-white/90">
                {currentIndex + 1} / {images.length}
              </span>
            )}
            <button
              type="button"
              onClick={() => setRotation((value) => value - 90)}
              className="inline-flex size-9 items-center justify-center rounded-full bg-black/50 text-white/90 transition hover:bg-black/70"
              aria-label="向左旋转"
            >
              <RotateCcw className="size-4" />
            </button>
            <button
              type="button"
              onClick={() => setRotation((value) => value + 90)}
              className="inline-flex size-9 items-center justify-center rounded-full bg-black/50 text-white/90 transition hover:bg-black/70"
              aria-label="向右旋转"
            >
              <RotateCw className="size-4" />
            </button>
            <button
              type="button"
              onClick={handleDownload}
              disabled={isDownloading}
              className="inline-flex size-9 items-center justify-center rounded-full bg-black/50 text-white/90 transition hover:bg-black/70"
              aria-label="下载图片"
            >
              <Download className={cn("size-4", isDownloading && "animate-pulse")} />
            </button>
            <DialogPrimitive.Close className="inline-flex size-9 items-center justify-center rounded-full bg-black/50 text-white/90 transition hover:bg-black/70">
              <X className="size-4" />
              <span className="sr-only">关闭</span>
            </DialogPrimitive.Close>
          </div>

          {/* prev */}
          {hasPrev && (
            <button
              type="button"
              onClick={goPrev}
              className="absolute left-4 z-10 inline-flex size-10 items-center justify-center rounded-full bg-black/40 text-white/90 transition hover:bg-black/60"
              aria-label="上一张"
            >
              <ChevronLeft className="size-5" />
            </button>
          )}

          {/* image */}
          <div
            className="flex max-h-[90vh] max-w-[90vw] items-center justify-center"
            onClick={() => onOpenChange(false)}
          >
            <img
              src={current.src}
              alt=""
              className="max-h-[90vh] max-w-[90vw] rounded-lg object-contain transition-transform duration-200"
              style={{ transform: `rotate(${rotation}deg)` }}
              onClick={(e) => e.stopPropagation()}
              draggable={false}
            />
          </div>

          {/* next */}
          {hasNext && (
            <button
              type="button"
              onClick={goNext}
              className="absolute right-4 z-10 inline-flex size-10 items-center justify-center rounded-full bg-black/40 text-white/90 transition hover:bg-black/60"
              aria-label="下一张"
            >
              <ChevronRight className="size-5" />
            </button>
          )}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
