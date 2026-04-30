"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Copy,
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
import { deleteManagedImages, fetchManagedImages, type ManagedImage, type ManagedImageUploader } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

type GroupMode = "none" | "uploader";

function formatSize(size: number) {
  return size > 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(2)} MB` : `${Math.ceil(size / 1024)} KB`;
}

function imageKey(item: ManagedImage) {
  return item.path || item.url;
}

function imageDimensions(item: ManagedImage) {
  return item.width && item.height ? `${item.width} x ${item.height}` : "-";
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

function ImageManagerContent() {
  const [items, setItems] = useState<ManagedImage[]>([]);
  const [uploaders, setUploaders] = useState<ManagedImageUploader[]>([]);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [uploader, setUploader] = useState("");
  const [groupMode, setGroupMode] = useState<GroupMode>("uploader");
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [isDeleting, setIsDeleting] = useState(false);
  const [selectedPaths, setSelectedPaths] = useState<string[]>([]);
  const [deleteMode, setDeleteMode] = useState<"selected" | "filtered" | null>(null);

  const pageSize = 12;
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const currentRows = items.slice((safePage - 1) * pageSize, safePage * pageSize);
  const selectedSet = useMemo(() => new Set(selectedPaths), [selectedPaths]);
  const selectedCount = deleteMode === "filtered" ? items.length : selectedPaths.length;
  const currentPageSelected = currentRows.length > 0 && currentRows.every((item) => selectedSet.has(imageKey(item)));
  const allSelected = items.length > 0 && items.every((item) => selectedSet.has(imageKey(item)));
  const hasActiveFilter = Boolean(startDate || endDate || uploader);
  const activeUploaderName = uploaders.find((item) => item.key === uploader)?.name || uploader;

  const lightboxImages = items.map((item) => ({
    id: imageKey(item),
    src: item.url,
    sizeLabel: formatSize(item.size),
    dimensions: imageDimensions(item),
  }));

  const groupedCurrentRows = useMemo(() => {
    if (groupMode !== "uploader") {
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
  }, [currentRows, groupMode]);

  const loadImages = async () => {
    setIsLoading(true);
    try {
      const data = await fetchManagedImages({ start_date: startDate, end_date: endDate, uploader });
      setItems(data.items);
      setUploaders((current) => uploader ? mergeUploaders(current, data.uploaders || []) : data.uploaders || []);
      setSelectedPaths((current) => current.filter((path) => data.items.some((item) => imageKey(item) === path)));
      setPage(1);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载图片失败");
    } finally {
      setIsLoading(false);
    }
  };

  const clearFilters = () => {
    setStartDate("");
    setEndDate("");
    setUploader("");
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
          ? { start_date: startDate, end_date: endDate, uploader, all_matching: true }
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

  useEffect(() => {
    void loadImages();
  }, [startDate, endDate, uploader]);

  const renderImageCard = (item: ManagedImage) => {
    const imageIndex = items.findIndex((row) => row.url === item.url);
    return (
      <div key={item.url} className="group border-r border-b border-stone-100 p-4 transition hover:bg-stone-50">
        <button
          type="button"
          className="relative block aspect-square w-full cursor-zoom-in overflow-hidden rounded-lg bg-stone-100 text-left"
          onClick={() => {
            setLightboxIndex(imageIndex);
            setLightboxOpen(true);
          }}
        >
          <ImageThumbnail
            src={item.url}
            thumbnailSrc={item.thumbnail_url}
            alt={item.name}
            className="h-full w-full"
            imageClassName="transition group-hover:scale-[1.02]"
          />
          <span className="absolute right-2 bottom-2 rounded-full bg-black/50 p-2 text-white opacity-0 transition group-hover:opacity-100">
            <Maximize2 className="size-4" />
          </span>
        </button>
        <div className="mt-3 space-y-1 text-xs text-stone-500">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1 font-medium text-stone-700">
              <CalendarDays className="size-3.5" />
              {item.created_at}
            </div>
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="icon"
                className="size-8 rounded-lg text-stone-400 hover:bg-stone-100 hover:text-stone-700"
                onClick={() => {
                  void navigator.clipboard.writeText(item.url);
                  toast.success("图片地址已复制");
                }}
              >
                <Copy className="size-4" />
              </Button>
              <Checkbox checked={selectedSet.has(imageKey(item))} onCheckedChange={(checked) => togglePaths([imageKey(item)], Boolean(checked))} />
            </div>
          </div>
          <div className="flex items-center gap-1 text-stone-600">
            <Users className="size-3.5" />
            <span className="truncate" title={uploaderLabel(item)}>上传人：{uploaderLabel(item)}</span>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span>{formatSize(item.size)}</span>
            <span>{imageDimensions(item)}</span>
          </div>
        </div>
      </div>
    );
  };

  return (
    <section className="space-y-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <div className="text-xs font-semibold tracking-[0.18em] text-stone-500 uppercase">Images</div>
          <h1 className="text-2xl font-semibold tracking-tight">图片管理</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <DateRangeFilter startDate={startDate} endDate={endDate} onChange={(start, end) => { setStartDate(start); setEndDate(end); }} />
          <select
            value={uploader}
            onChange={(event) => setUploader(event.target.value)}
            className="h-10 rounded-xl border border-stone-200 bg-white px-3 text-sm text-stone-700 shadow-sm outline-none transition focus:border-stone-300"
          >
            <option value="">全部上传人</option>
            {uploaders.map((item) => (
              <option key={item.key} value={item.key}>{item.name}（{item.count}）</option>
            ))}
          </select>
          <select
            value={groupMode}
            onChange={(event) => setGroupMode(event.target.value as GroupMode)}
            className="h-10 rounded-xl border border-stone-200 bg-white px-3 text-sm text-stone-700 shadow-sm outline-none transition focus:border-stone-300"
          >
            <option value="uploader">按上传人分组</option>
            <option value="none">不分组</option>
          </select>
          <Button variant="outline" onClick={clearFilters} className="h-10 rounded-xl border-stone-200 bg-white px-4 text-stone-700">
            清除筛选条件
          </Button>
          <Button onClick={() => void loadImages()} disabled={isLoading} className="h-10 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800">
            {isLoading ? <LoaderCircle className="size-4 animate-spin" /> : <Search className="size-4" />}
            查询
          </Button>
          <Button variant="outline" onClick={() => setDeleteMode("filtered")} disabled={isDeleting || items.length === 0 || !hasActiveFilter} className="h-10 rounded-xl border-rose-200 bg-white px-4 text-rose-600 hover:bg-rose-50">
            <Trash2 className="size-4" />
            删除匹配筛选
          </Button>
        </div>
      </div>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-0">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-stone-100 px-5 py-4">
            <div className="flex flex-wrap items-center gap-3 text-sm text-stone-600">
              <ImageIcon className="size-4" />
              共 {items.length} 张
              {uploader ? <span className="rounded-full bg-stone-100 px-2 py-1 text-xs">上传人：{activeUploaderName}</span> : null}
              <label className="flex items-center gap-2">
                <Checkbox checked={currentPageSelected} onCheckedChange={(checked) => togglePaths(currentRows.map(imageKey), Boolean(checked))} />
                本页全选
              </label>
              <label className="flex items-center gap-2">
                <Checkbox checked={allSelected} onCheckedChange={(checked) => togglePaths(items.map(imageKey), Boolean(checked))} />
                全选结果
              </label>
              {selectedPaths.length > 0 ? <span>已选 {selectedPaths.length} 张</span> : null}
            </div>
            <div className="flex items-center gap-2">
              <Button variant="ghost" className="h-8 rounded-lg px-3 text-stone-500" onClick={() => void loadImages()} disabled={isLoading}>
                <RefreshCw className={`size-4 ${isLoading ? "animate-spin" : ""}`} />
                刷新
              </Button>
              <button type="button" className="text-sm text-stone-500 hover:text-stone-900 disabled:text-stone-300" onClick={() => setSelectedPaths([])} disabled={selectedPaths.length === 0 || isDeleting}>
                取消选择
              </button>
              <Button variant="outline" className="h-8 rounded-lg border-rose-200 bg-white px-3 text-rose-600 hover:bg-rose-50" onClick={() => setDeleteMode("selected")} disabled={selectedPaths.length === 0 || isDeleting}>
                <Trash2 className="size-4" />
                删除所选
              </Button>
            </div>
          </div>

          {groupedCurrentRows.map((group) => (
            <div key={group.key}>
              {groupMode === "uploader" ? (
                <div className="flex items-center gap-2 border-b border-stone-100 bg-stone-50/80 px-5 py-3 text-sm font-medium text-stone-700">
                  <Users className="size-4" />
                  {group.name}
                  <span className="text-xs font-normal text-stone-400">本页 {group.items.length} 张</span>
                </div>
              ) : null}
              <div className="grid gap-0 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {group.items.map(renderImageCard)}
              </div>
            </div>
          ))}

          <div className="flex items-center justify-end gap-2 border-t border-stone-100 px-4 py-3 text-sm text-stone-500">
            <span>第 {safePage} / {pageCount} 页，共 {items.length} 张</span>
            <Button variant="outline" size="icon" className="size-9 rounded-lg border-stone-200 bg-white" disabled={safePage <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>
              <ChevronLeft className="size-4" />
            </Button>
            <Button variant="outline" size="icon" className="size-9 rounded-lg border-stone-200 bg-white" disabled={safePage >= pageCount} onClick={() => setPage((value) => Math.min(pageCount, value + 1))}>
              <ChevronRight className="size-4" />
            </Button>
          </div>
          {!isLoading && items.length === 0 ? <div className="px-6 py-14 text-center text-sm text-stone-500">没有找到图片</div> : null}
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
            <DialogTitle>{deleteMode === "filtered" ? "删除匹配筛选的图片" : "删除所选图片"}</DialogTitle>
            <DialogDescription className="text-sm leading-6">
              确认删除 {selectedCount} 张图片吗？删除后无法恢复。
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
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);
  if (isCheckingAuth || !session || session.role !== "admin") {
    return <div className="flex min-h-[40vh] items-center justify-center"><LoaderCircle className="size-5 animate-spin text-stone-400" /></div>;
  }
  return <ImageManagerContent />;
}
