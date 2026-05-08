"use client";

import type { ImgHTMLAttributes } from "react";
import { useEffect, useMemo, useState } from "react";

import { cn } from "@/lib/utils";

type ImageThumbnailProps = {
  src: string;
  thumbnailSrc?: string;
  alt?: string;
  className?: string;
  imageClassName?: string;
  imgProps?: ImgHTMLAttributes<HTMLImageElement>;
};

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

export function ImageThumbnail({ src, thumbnailSrc, alt = "", className, imageClassName, imgProps }: ImageThumbnailProps) {
  const initialSrc = useMemo(() => thumbnailSrc || getImageThumbnailUrl(src), [src, thumbnailSrc]);
  const [currentSrc, setCurrentSrc] = useState(initialSrc);

  useEffect(() => {
    setCurrentSrc(initialSrc);
  }, [initialSrc]);

  return (
    <span className={cn("block overflow-hidden bg-stone-100", className)}>
      <img
        src={currentSrc}
        alt={alt}
        className={cn("h-full w-full object-cover", imageClassName)}
        loading="lazy"
        decoding="async"
        {...imgProps}
        onError={() => {
          if (currentSrc !== src) {
            setCurrentSrc(src);
          }
        }}
      />
    </span>
  );
}
