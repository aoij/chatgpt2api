"use client";

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
