"use client";

import { getStoredAuthKey } from "@/store/auth";

export function saveBlobAsFile(blob: Blob, filename: string) {
  const fallbackName = filename || "download";
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fallbackName;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => window.URL.revokeObjectURL(url), 1000);
}

export function defaultImageDownloadName(id: string, extension = "jpg") {
  const safeId = String(id || "image").replace(/[^\w.-]+/g, "-").slice(0, 80) || "image";
  return `image-${safeId}.${extension}`;
}

export async function openDirectDownload(path: string, params: Record<string, string | number | boolean | string[] | undefined> = {}) {
  if (typeof window === "undefined") return;
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === "" || value === false) return;
    if (Array.isArray(value)) {
      value.forEach((item) => {
        if (item) url.searchParams.append(key, item);
      });
      return;
    }
    url.searchParams.set(key, String(value));
  });
  const authKey = await getStoredAuthKey();
  if (authKey) {
    url.searchParams.set("download_token", authKey);
  }
  const link = document.createElement("a");
  link.href = url.toString();
  link.download = "";
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

export function openUrlDownload(url: string, filename: string) {
  if (typeof window === "undefined" || !url) return;
  const link = document.createElement("a");
  link.href = url;
  link.download = filename || "download";
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}
