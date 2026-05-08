"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Copy,
  Download,
  ImageIcon,
  LoaderCircle,
  Maximize2,
  RefreshCw,
  Search,
  Trash2,
  Users,
} from "lucide-react";
import { toast } from "sonner";

import { DateRangeFilter } from "@/components/date-range-filter";
import { ImageLightbox } from "@/components/image-lightbox";
import { ImageThumbnail } from "@/components/image-thumbnail";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { fetchManagedImages, deleteManagedImages, downloadManagedImage, type ManagedImage, type ManagedImageUploader } from "@/lib/api";
import { defaultImageDownloadName, saveBlobAsFile } from "@/lib/download";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { cn } from "@/lib/utils";
import type { StoredAuthSession } from "@/store/auth";

type GroupMode = "none" | "uploader";

function formatSize(size: number) {
  return size > 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(2)} MB` : `${Math.ceil(size / 1024)} KB`;
}

function imageKey(item: ManagedImage) {
  return item.path || item.url;
}

function imageDimensions(item: ManagedImage) {
  return item.width && item.height ? `${item.width} × ${item.height}` : "-";
}

function uploaderLabel(item: Pick<ManagedImage, "uploader_name" | "uploader_id">) {
  return item.uploader_name || item.uploader_id || "未知上传人";
}

function mergeUploaders(current: ManagedImageUploader[], incoming: ManagedImageUploader[]) {
  const map = new Map<string, ManagedImageUploader>();
  [...current, ...incoming].forEach((item) => {
    const key = item.key || item.id || item.name;
    if (!key) return;
    map.set(key, { ...item, key });
  });
  return Array.from(map.values()).sort((a, b) => (b.count || 0) - (a.count || 0) || a.name.localeCompare(b.name));
}

function ImageManagerContent({ session }: { session: StoredAuthSession }) {
  const isAdmin = session.role === "admin" && session.scope !== "image";
  const isSelfMode = !isAdmin;

  const [items, setItems] = useState<ManagedImage[]>([]);
  const [uploaders, setUploaders] = useState<ManagedImageUploader[]>([]);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [uploader, setUploader] = useState("");
  const [groupMode, setGroupMode] = useState<GroupMode>(isSelfMode ? "none" : "uploader");
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [selectedPaths, setSelectedPaths] = useState<string[]>([]);
  const [deleteMode, setDeleteMode] = useState<"selected" | "filtered" | null>(null);

  const effectiveGroupMode: GroupMode = isAdmin ? groupMode : "none";
  const pageSize = 12;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, pageCount);
  const currentRows = items;
  const selectedSet = useMemo(() => new Set(selectedPaths), [selectedPaths]);
  const selectedCount = deleteMode === "filtered" ? total : selectedPaths.length;
  const currentPageSelected = currentRows.length > 0 && currentRows.every((item) => selectedSet.has(imageKey(item)));
  const hasActiveFilter = Boolean(startDate || endDate || (isAdmin && uploader));
  const activeUploaderName = uploaders.find((item) => item.key === uploader)?.name || uploader;
  const fixedViewerLabel = session.name || "当前令牌";
  const filterDeleteDisabled = isDeleting || total === 0 || !hasActiveFilter;

  const lightboxImages = items.map((item) => ({
    id: imageKey(item),
    src: item.url,
    sizeLabel: formatSize(item.size),
    dimensions: imageDimensions(item),
    downloadPath: item.path || imageKey(item),
    filename: item.name ? `${item.name.replace(/\.[^.]+$/, "")}.jpg` : undefined,
  }));

  const groupedCurrentRows = useMemo(() => {
    if (effectiveGroupMode !== "uploader") {
      return [{ key: "all", name: "全部图片", items: currentRows }];
    }
    const groups = new Map<string, { key: string; name: string; items: ManagedImage[] }>();
    currentRows.forEach((item) => {
      const key = item.uploader_key || item.uploader_id || item.uploader_name || "unknown";
      const group = groups.get(key) || { key, name: uploaderLabel(item), items: [] };
      group.items.push(item);
      groups.set(key, group);
    });
    return Array.from(groups.values());
  }, [currentRows, effectiveGroupMode]);

  const loadImages = async () => {
    setIsLoading(true);
    try {
      const data = await fetchManagedImages({
        start_date: startDate,
        end_date: endDate,
        ...(isAdmin && uploader ? { uploader } : {}),
        limit: pageSize,
        offset: (safePage - 1) * pageSize,
      });
      const nextPageCount = Math.max(1, Math.ceil(Number(data.total || 0) / pageSize));
      if (safePage > nextPageCount) {
        setPage(nextPageCount);
        return;
      }
      setItems(data.items);
      setTotal(Number(data.total || 0));
      if (isAdmin) {
        setUploaders((current) => uploader ? mergeUploaders(current, data.uploaders || []) : data.uploaders || []);
      } else {
        setUploaders(data.uploaders || []);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载图片失败");
    } finally {
      setIsLoading(false);
    }
  };

  const clearFilters = () => {
    setStartDate("");
    setEndDate("");
    if (isAdmin) {
      setUploader("");
      setGroupMode("uploader");
    }
  };

  const togglePaths = (paths: string[], checked: boolean) => {
    setSelectedPaths((current) => checked ? Array.from(new Set([...current, ...paths])) : current.filter((path) => !paths.includes(path)));
  };

  const confirmDelete = async () => {
    if (!deleteMode || selectedCount === 0) return;
    setIsDeleting(true);
    try {
      const data = await deleteManagedImages(
        deleteMode === "filtered"
          ? {
              start_date: startDate,
              end_date: endDate,
              ...(isAdmin && uploader ? { uploader } : {}),
              all_matching: true,
            }
          : { paths: selectedPaths },
      );
      toast.success(`已删除 ${data.removed} 张图片`);
      setDeleteMode(null);
      setSelectedPaths([]);
      await loadImages();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除图片失败");
    } finally {
      setIsDeleting(false);
    }
  };

  const downloadOne = async (path: string, filename?: string) => {
    if (!path) return;
    setIsDownloading(true);
    try {
      const data = await downloadManagedImage(path);
      saveBlobAsFile(data.blob, data.filename || filename || defaultImageDownloadName(path, "jpg"));
      toast.success("已下载 JPG 图片，手机端可直接保存/查看");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "下载图片失败");
    } finally {
      setIsDownloading(false);
    }
  };

  const downloadSelected = async () => {
    if (selectedPaths.length === 0) return;
    setIsDownloading(true);
    try {
      const selectedMap = new Map(items.map((item) => [imageKey(item), item]));
      for (let index = 0; index < selectedPaths.length; index += 1) {
        const path = selectedPaths[index];
        const selected = selectedMap.get(path);
        const data = await downloadManagedImage(path);
        saveBlobAsFile(
          data.blob,
          data.filename || (selected?.name ? `${selected.name.replace(/\.[^.]+$/, "")}.jpg` : defaultImageDownloadName(path, "jpg")),
        );
        if (index < selectedPaths.length - 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 180));
        }
      }
      toast.success(`已开始逐张下载 ${selectedPaths.length} 张图片`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "下载图片失败");
    } finally {
      setIsDownloading(false);
    }
  };

  useEffect(() => {
    setPage(1);
  }, [startDate, endDate, uploader, isAdmin]);

  useEffect(() => {
    void loadImages();
  }, [page, startDate, endDate, uploader, isAdmin]);

  const renderImageCard = (item: ManagedImage) => {
    const key = imageKey(item);
    const imageIndex = items.findIndex((row) => imageKey(row) === key);
    const actorLabel = isSelfMode ? fixedViewerLabel : uploaderLabel(item);

    return (
      <div
        key={key}
        className={cn(
          "group rounded-[24px] border bg-white/95 p-2.5 shadow-sm transition sm:p-3",
          selectedSet.has(key) ? "border-stone-900 ring-1 ring-stone-900/10" : "border-stone-200/80 hover:border-stone-300",
        )}
      >
        <div className="mb-2 flex items-center justify-between gap-2">
          <div className="min-w-0 truncate text-[11px] font-medium text-stone-500">
            {item.created_at}
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <Button
              variant="ghost"
              size="icon"
              className="size-8 rounded-xl text-stone-400 hover:bg-stone-100 hover:text-stone-700"
              onClick={() => {
                void navigator.clipboard.writeText(item.url);
                toast.success("图片地址已复制");
              }}
            >
              <Copy className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-8 rounded-xl text-stone-400 hover:bg-stone-100 hover:text-stone-700"
              disabled={isDownloading}
              onClick={() => void downloadOne(key, item.name ? `${item.name.replace(/\.[^.]+$/, "")}.jpg` : undefined)}
              title="下载 JPG 图片"
            >
              <Download className="size-4" />
            </Button>
            <span className="flex size-8 items-center justify-center rounded-xl border border-stone-200 bg-white">
              <Checkbox checked={selectedSet.has(key)} onCheckedChange={(checked) => togglePaths([key], Boolean(checked))} />
            </span>
          </div>
        </div>

        <button
          type="button"
          className="relative block aspect-square w-full cursor-zoom-in overflow-hidden rounded-[20px] bg-stone-100 text-left"
          onClick={() => {
            setLightboxIndex(Math.max(0, imageIndex));
            setLightboxOpen(true);
          }}
        >
          <ImageThumbnail
            src={item.url}
            thumbnailSrc={item.thumbnail_url}
            alt={item.name}
            className="h-full w-full"
            imageClassName="transition duration-200 group-hover:scale-[1.02]"
          />
          <span className="absolute right-2 bottom-2 rounded-full bg-black/50 p-2 text-white opacity-100 transition sm:opacity-0 sm:group-hover:opacity-100">
            <Maximize2 className="size-4" />
          </span>
        </button>

        <div className="mt-3 space-y-2 text-xs text-stone-500">
          <div className="flex items-center gap-1.5 text-stone-600">
            <Users className="size-3.5" />
            <span className="truncate" title={actorLabel}>
              {isSelfMode ? "令牌" : "上传人"}：{actorLabel}
            </span>
          </div>
          <div className="flex items-center gap-1.5 text-stone-600">
            <CalendarDays className="size-3.5" />
            <span className="truncate">{item.date}</span>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span>{formatSize(item.size)}</span>
            <span>{imageDimensions(item)}</span>
          </div>
        </div>
      </div>
    );
  };

  const deleteDialogTitle = deleteMode === "filtered"
    ? (isSelfMode ? "删除当前筛选的图片" : "删除匹配筛选的图片")
    : "删除所选图片";
  const deleteDialogDescription = deleteMode === "filtered"
    ? (isSelfMode
      ? `确认删除当前令牌下匹配筛选的 ${selectedCount} 张图片吗？删除后无法恢复。`
      : `确认删除匹配筛选的 ${selectedCount} 张图片吗？删除后无法恢复。`)
    : `确认删除 ${selectedCount} 张图片吗？删除后无法恢复。`;

  return (
    <section className="space-y-4 sm:space-y-5">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div className="space-y-1.5">
          <div className="text-xs font-semibold tracking-[0.18em] text-stone-500 uppercase">
            {isSelfMode ? "My Images" : "Images"}
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {isSelfMode ? "我的图片" : "图片管理"}
          </h1>
          <p className="text-sm leading-6 text-stone-500">
            {isSelfMode
              ? "这里只显示当前令牌生成的图片，支持按日期筛选、预览、复制、下载和删除；支持直接下载到手机更友好的 JPG，图片仅保存 10 天，请及时保存。"
              : "支持按日期和上传人筛选图片，分组查看、批量选择、逐张直接下载并执行删除；下载会自动转成 JPG，图片仅保存 10 天。"}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full bg-white/90 px-3 py-1.5 text-xs font-medium text-stone-600 shadow-sm ring-1 ring-stone-200/70">
            共 {total} 张
          </span>
          <span className="rounded-full bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-700 shadow-sm ring-1 ring-amber-200/70">
            图片保存 10 天
          </span>
          {isSelfMode ? (
            <span className="rounded-full bg-white/90 px-3 py-1.5 text-xs font-medium text-stone-600 shadow-sm ring-1 ring-stone-200/70">
              当前令牌：<span className="font-semibold text-rose-600">{fixedViewerLabel}</span>
            </span>
          ) : null}
          {isAdmin && uploader ? (
            <span className="rounded-full bg-white/90 px-3 py-1.5 text-xs font-medium text-stone-600 shadow-sm ring-1 ring-stone-200/70">
              当前上传人：{activeUploaderName}
            </span>
          ) : null}
        </div>
      </div>

      <Card className="rounded-[28px] border-white/80 bg-white/90 shadow-sm">
        <CardContent className="space-y-4 p-3 sm:p-5">
          <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center">
            <div className="col-span-2 sm:col-span-1">
              <DateRangeFilter
                startDate={startDate}
                endDate={endDate}
                onChange={(start, end) => {
                  setStartDate(start);
                  setEndDate(end);
                }}
              />
            </div>

            {isAdmin ? (
              <select
                value={uploader}
                onChange={(event) => setUploader(event.target.value)}
                className="h-10 w-full rounded-xl border border-stone-200 bg-white px-3 text-sm text-stone-700 shadow-sm outline-none transition focus:border-stone-300 sm:w-auto"
              >
                <option value="">全部上传人</option>
                {uploaders.map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.name}（{item.count}）
                  </option>
                ))}
              </select>
            ) : null}

            {isAdmin ? (
              <select
                value={groupMode}
                onChange={(event) => setGroupMode(event.target.value as GroupMode)}
                className="h-10 w-full rounded-xl border border-stone-200 bg-white px-3 text-sm text-stone-700 shadow-sm outline-none transition focus:border-stone-300 sm:w-auto"
              >
                <option value="uploader">按上传人分组</option>
                <option value="none">不分组</option>
              </select>
            ) : null}

            <Button variant="outline" onClick={clearFilters} className="h-10 rounded-xl border-stone-200 bg-white px-3 text-stone-700 sm:px-4">
              清除筛选条件
            </Button>

            <Button onClick={() => void loadImages()} disabled={isLoading} className="h-10 rounded-xl bg-stone-950 px-3 text-white hover:bg-stone-800 sm:px-4">
              {isLoading ? <LoaderCircle className="size-4 animate-spin" /> : <Search className="size-4" />}
              查询
            </Button>

            <Button
              variant="outline"
              onClick={() => setDeleteMode("filtered")}
              disabled={filterDeleteDisabled}
              className="col-span-2 h-10 rounded-xl border-rose-200 bg-white px-3 text-rose-600 hover:bg-rose-50 sm:col-span-1 sm:px-4"
            >
              <Trash2 className="size-4" />
              {isSelfMode ? "删除当前筛选" : "删除匹配筛选"}
            </Button>
          </div>

          <div className="rounded-[24px] border border-stone-200/70 bg-stone-50/70 p-3 sm:p-4">
            <div className="flex flex-wrap items-center gap-2 text-sm text-stone-600">
              <ImageIcon className="size-4" />
              <span>第 {safePage} / {pageCount} 页</span>
              {selectedPaths.length > 0 ? <span>已选 {selectedPaths.length} 张（可跨页累计）</span> : null}
            </div>

            <div className="mt-3 grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center">
              <label className="flex items-center gap-2 rounded-xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700 shadow-sm">
                <Checkbox checked={currentPageSelected} onCheckedChange={(checked) => togglePaths(currentRows.map(imageKey), Boolean(checked))} />
                本页全选
              </label>
              <Button variant="ghost" className="h-10 rounded-xl px-3 text-stone-500 sm:h-9" onClick={() => void loadImages()} disabled={isLoading}>
                <RefreshCw className={cn("size-4", isLoading && "animate-spin")} />
                刷新
              </Button>
              <Button variant="outline" className="h-10 rounded-xl border-stone-200 bg-white px-3 text-stone-700 sm:h-9" onClick={() => setSelectedPaths([])} disabled={selectedPaths.length === 0 || isDeleting}>
                取消选择
              </Button>
              <Button
                variant="outline"
                className="h-10 rounded-xl border-stone-200 bg-white px-3 text-stone-700 hover:bg-stone-100 sm:h-9"
                onClick={() => void downloadSelected()}
                disabled={selectedPaths.length === 0 || isDownloading}
              >
                {isDownloading ? <LoaderCircle className="size-4 animate-spin" /> : <Download className="size-4" />}
                下载所选
              </Button>
              <Button
                variant="outline"
                className="h-10 rounded-xl border-rose-200 bg-white px-3 text-rose-600 hover:bg-rose-50 sm:h-9"
                onClick={() => setDeleteMode("selected")}
                disabled={selectedPaths.length === 0 || isDeleting}
              >
                <Trash2 className="size-4" />
                删除所选
              </Button>
            </div>
          </div>

          {isLoading ? (
            <div className="flex min-h-[320px] items-center justify-center rounded-[24px] border border-dashed border-stone-200 bg-stone-50/60">
              <LoaderCircle className="size-5 animate-spin text-stone-400" />
            </div>
          ) : total === 0 ? (
            <div className="rounded-[24px] border border-dashed border-stone-200 bg-stone-50/60 px-6 py-14 text-center">
              <div className="text-base font-medium text-stone-700">
                {isSelfMode ? "还没有找到你的图片" : "没有找到图片"}
              </div>
              <p className="mt-2 text-sm leading-6 text-stone-500">
                {isSelfMode ? "先去画图页生成几张图片，随后就能在这里查看和删除。" : "可以尝试调整日期或上传人筛选条件后重新查询。"}
              </p>
            </div>
          ) : (
            <>
              <div className="space-y-4">
                {groupedCurrentRows.map((group) => (
                  <div key={group.key} className="space-y-3">
                    {effectiveGroupMode === "uploader" ? (
                      <div className="flex items-center gap-2 px-1 text-sm font-medium text-stone-700">
                        <Users className="size-4" />
                        {group.name}
                        <span className="text-xs font-normal text-stone-400">本页 {group.items.length} 张</span>
                      </div>
                    ) : null}
                    <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-4">
                      {group.items.map(renderImageCard)}
                    </div>
                  </div>
                ))}
              </div>

              <div className="flex items-center justify-between gap-2 border-t border-stone-100 pt-1 text-sm text-stone-500 sm:justify-end">
                <span>
                  第 {safePage} / {pageCount} 页，共 {total} 张
                </span>
                <Button
                  variant="outline"
                  size="icon"
                  className="size-9 rounded-xl border-stone-200 bg-white"
                  disabled={safePage <= 1}
                  onClick={() => setPage((value) => Math.max(1, value - 1))}
                >
                  <ChevronLeft className="size-4" />
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  className="size-9 rounded-xl border-stone-200 bg-white"
                  disabled={safePage >= pageCount}
                  onClick={() => setPage((value) => Math.min(pageCount, value + 1))}
                >
                  <ChevronRight className="size-4" />
                </Button>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <ImageLightbox
        images={lightboxImages}
        currentIndex={lightboxIndex}
        open={lightboxOpen}
        onOpenChange={setLightboxOpen}
        onIndexChange={setLightboxIndex}
      />

      <Dialog open={Boolean(deleteMode)} onOpenChange={(open) => (!open ? setDeleteMode(null) : null)}>
        <DialogContent showCloseButton={false} className="rounded-2xl p-6">
          <DialogHeader className="gap-2">
            <DialogTitle>{deleteDialogTitle}</DialogTitle>
            <DialogDescription className="text-sm leading-6">
              {deleteDialogDescription}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" className="rounded-xl" onClick={() => setDeleteMode(null)} disabled={isDeleting}>
              取消
            </Button>
            <Button className="rounded-xl bg-rose-600 text-white hover:bg-rose-700" onClick={() => void confirmDelete()} disabled={isDeleting || selectedCount === 0}>
              {isDeleting ? <LoaderCircle className="size-4 animate-spin" /> : null}
              确认删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

export default function ImageManagerPage() {
  const { isCheckingAuth, session } = useAuthGuard();

  if (isCheckingAuth || !session) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  return <ImageManagerContent session={session} />;
}
