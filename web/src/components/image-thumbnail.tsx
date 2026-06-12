"use client";

import type { ImgHTMLAttributes } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/utils";

type ImageThumbnailProps = {
  src: string;
  thumbnailSrc?: string;
  alt?: string;
  className?: string;
  imageClassName?: string;
  fallbackToOriginal?: boolean;
  imgProps?: ImgHTMLAttributes<HTMLImageElement>;
};

const MAX_IMAGE_RETRY_COUNT = 2;
const THUMBNAIL_FALLBACK_DELAY_MS = 2500;

function appendImageRetryQuery(src: string, retry: number) {
  if (!src || src.startsWith("data:") || src.startsWith("blob:")) {
    return src;
  }
  try {
    const url = new URL(src, window.location.href);
    url.searchParams.set("_img_retry", String(retry));
    return url.toString();
  } catch {
    const separator = src.includes("?") ? "&" : "?";
    return `${src}${separator}_img_retry=${retry}`;
  }
}

function stripImageRetryQuery(src: string) {
  if (!src || src.startsWith("data:") || src.startsWith("blob:")) {
    return src;
  }
  try {
    const url = new URL(src, window.location.href);
    url.searchParams.delete("_img_retry");
    return url.toString();
  } catch {
    return src.replace(/([?&])_img_retry=\d+(&)?/, (_match, prefix, suffix) => (suffix ? prefix : ""));
  }
}

export function getImageThumbnailUrl(src: string) {
  const marker = "/images/";
  const index = src.indexOf(marker);
  if (index < 0) return src;
  const relativePath = src.slice(index + marker.length);
  const queryIndex = relativePath.indexOf("?");
  const cleanPath = queryIndex >= 0 ? relativePath.slice(0, queryIndex) : relativePath;
  const query = queryIndex >= 0 ? relativePath.slice(queryIndex) : "";
  const dotIndex = cleanPath.lastIndexOf(".");
  const thumbPath = dotIndex >= 0 ? `${cleanPath.slice(0, dotIndex)}.webp` : `${cleanPath}.webp`;
  return `${src.slice(0, index)}/image-thumbs/${thumbPath}${query}`;
}

export function ImageThumbnail({ src, thumbnailSrc, alt = "", className, imageClassName, fallbackToOriginal = false, imgProps }: ImageThumbnailProps) {
  const initialSrc = useMemo(() => thumbnailSrc || getImageThumbnailUrl(src), [src, thumbnailSrc]);
  const [currentSrc, setCurrentSrc] = useState(initialSrc);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<number | null>(null);
  const fallbackTimerRef = useRef<number | null>(null);

  useEffect(() => {
    const clearRetryTimer = () => {
      if (retryTimerRef.current != null) {
        window.clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    };
    const clearFallbackTimer = () => {
      if (fallbackTimerRef.current != null) {
        window.clearTimeout(fallbackTimerRef.current);
        fallbackTimerRef.current = null;
      }
    };
    retryCountRef.current = 0;
    clearRetryTimer();
    clearFallbackTimer();
    setCurrentSrc(initialSrc);
    const cleanInitial = stripImageRetryQuery(initialSrc);
    const cleanOriginal = stripImageRetryQuery(src);
    if (fallbackToOriginal && cleanInitial !== cleanOriginal) {
      fallbackTimerRef.current = window.setTimeout(() => {
        fallbackTimerRef.current = null;
        setCurrentSrc(src);
      }, THUMBNAIL_FALLBACK_DELAY_MS);
    }
    return () => {
      clearRetryTimer();
      clearFallbackTimer();
    };
  }, [fallbackToOriginal, initialSrc, src]);

  const { onError: externalOnError, onLoad: externalOnLoad, ...restImgProps } = imgProps || {};

  return (
    <span className={cn("block overflow-hidden bg-stone-100", className)}>
      <img
        src={currentSrc}
        alt={alt}
        className={cn("h-full w-full object-cover", imageClassName)}
        loading="lazy"
        decoding="async"
        {...restImgProps}
        onLoad={(event) => {
          const cleanCurrent = stripImageRetryQuery(currentSrc);
          const cleanInitial = stripImageRetryQuery(initialSrc);
          const cleanOriginal = stripImageRetryQuery(src);
          if (
            fallbackToOriginal
            && cleanCurrent === cleanInitial
            && cleanCurrent !== cleanOriginal
            && (event.currentTarget.naturalWidth <= 0 || event.currentTarget.naturalHeight <= 0)
          ) {
            setCurrentSrc(src);
            return;
          }
          retryCountRef.current = 0;
          if (retryTimerRef.current != null) {
            window.clearTimeout(retryTimerRef.current);
            retryTimerRef.current = null;
          }
          if (fallbackTimerRef.current != null) {
            window.clearTimeout(fallbackTimerRef.current);
            fallbackTimerRef.current = null;
          }
          externalOnLoad?.(event);
        }}
        onError={(event) => {
          externalOnError?.(event);
          if (fallbackTimerRef.current != null) {
            window.clearTimeout(fallbackTimerRef.current);
            fallbackTimerRef.current = null;
          }
          const cleanCurrent = stripImageRetryQuery(currentSrc);
          const cleanInitial = stripImageRetryQuery(initialSrc);
          const cleanOriginal = stripImageRetryQuery(src);
          if (fallbackToOriginal && cleanCurrent === cleanInitial && cleanCurrent !== cleanOriginal) {
            retryCountRef.current = 0;
            setCurrentSrc(src);
            return;
          }
          const retrySource = cleanCurrent === cleanOriginal ? src : initialSrc;
          if (retryCountRef.current < MAX_IMAGE_RETRY_COUNT) {
            retryCountRef.current += 1;
            const retry = retryCountRef.current;
            const delay = Math.min(2000, 500 * 2 ** (retry - 1));
            if (retryTimerRef.current != null) {
              window.clearTimeout(retryTimerRef.current);
            }
            retryTimerRef.current = window.setTimeout(() => {
              retryTimerRef.current = null;
              setCurrentSrc(appendImageRetryQuery(retrySource, retry));
            }, delay);
          }
        }}
      />
    </span>
  );
}
