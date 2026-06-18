"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Ban, CheckCircle2, Copy, KeyRound, LoaderCircle, Pencil, Plus, RefreshCw, RotateCcwKey, Search, Trash2, UsersRound } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { createUserKey, deleteUserKey, fetchAccounts, fetchUserKeys, updateUserKey, type Account, type UserKey } from "@/lib/api";

const PAGE_SIZE_OPTIONS = [20, 50, 100, 200] as const;

type StatusFilter = "all" | "enabled" | "disabled" | "empty";

function createRandomUserKey() {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  const token = btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  return `sk-${token}`;
}

function formatDateTime(value?: string | null) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function UserKeysCard({ standalone = false }: { standalone?: boolean } = {}) {
  const didLoadRef = useRef(false);
  const [items, setItems] = useState<UserKey[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [name, setName] = useState("");
  const [quota, setQuota] = useState("30");
  const [customKey, setCustomKey] = useState("");
  const [selectedAccountId, setSelectedAccountId] = useState("");
  const [loginUsername, setLoginUsername] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [openId, setOpenId] = useState("");
  const [avatarUrl, setAvatarUrl] = useState("");
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [isCreating, setIsCreating] = useState(false);
  const [pendingIds, setPendingIds] = useState<Set<string>>(() => new Set());
  const [revealedKey, setRevealedKey] = useState("");
  const [revealedLinkToken, setRevealedLinkToken] = useState("");
  const [deletingItem, setDeletingItem] = useState<UserKey | null>(null);
  const [editingItem, setEditingItem] = useState<UserKey | null>(null);
  const [editName, setEditName] = useState("");
  const [editKey, setEditKey] = useState("");
  const [editSelectedAccountId, setEditSelectedAccountId] = useState("");
  const [editUsername, setEditUsername] = useState("");
  const [editPassword, setEditPassword] = useState("");
  const [editOpenId, setEditOpenId] = useState("");
  const [editAvatarUrl, setEditAvatarUrl] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [pageSize, setPageSize] = useState<(typeof PAGE_SIZE_OPTIONS)[number]>(50);
  const [page, setPage] = useState(1);

  const load = async (silent = false) => {
    if (!silent) {
      setIsLoading(true);
    }
    try {
      const [data, accountData] = await Promise.all([fetchUserKeys(), fetchAccounts({ page_size: 200 })]);
      setItems(data.items);
      setAccounts(accountData.items || []);
      if (silent) {
        toast.success("用户列表已刷新");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载用户密钥失败");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (didLoadRef.current) {
      return;
    }
    didLoadRef.current = true;
    void load();
  }, []);

  useEffect(() => {
    setPage(1);
  }, [query, statusFilter, pageSize]);

  const stats = useMemo(() => {
    const total = items.length;
    const enabled = items.filter((item) => item.enabled).length;
    const disabled = total - enabled;
    const empty = items.filter((item) => item.enabled && item.quota !== null && Number(item.quota || 0) <= 0).length;
    const quota = items.reduce((sum, item) => sum + (item.quota == null ? 0 : Math.max(0, Number(item.quota) || 0)), 0);
    return { total, enabled, disabled, empty, quota };
  }, [items]);

  const filteredItems = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return items.filter((item) => {
      const matchesKeyword = keyword
        ? [item.name, item.id, item.link_token, item.username, item.open_id, item.selected_account_id, item.created_at, item.last_used_at]
            .filter(Boolean)
            .some((value) => String(value).toLowerCase().includes(keyword))
        : true;
      const matchesStatus =
        statusFilter === "all" ||
        (statusFilter === "enabled" && item.enabled) ||
        (statusFilter === "disabled" && !item.enabled) ||
        (statusFilter === "empty" && item.enabled && item.quota !== null && Number(item.quota || 0) <= 0);
      return matchesKeyword && matchesStatus;
    });
  }, [items, query, statusFilter]);


  const accountOptions = useMemo(() => {
    return accounts.map((account) => ({
      id: String(account.id || ""),
      label: [account.email || account.user_id || account.account_id || account.id, account.type, account.status]
        .filter(Boolean)
        .join(" · "),
    })).filter((account) => account.id);
  }, [accounts]);

  const accountLabel = (accountId?: string | null) => {
    const normalized = String(accountId || "").trim();
    if (!normalized) return "未指定";
    return accountOptions.find((item) => item.id === normalized)?.label || normalized;
  };
  const pageCount = Math.max(1, Math.ceil(filteredItems.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const pagedItems = filteredItems.slice((safePage - 1) * pageSize, safePage * pageSize);

  const handleCreate = async () => {
    setIsCreating(true);
    try {
      const data = await createUserKey({
        name: name.trim(),
        quota: Math.max(0, Number(quota) || 0),
        ...(customKey.trim() ? { key: customKey.trim() } : {}),
        ...(selectedAccountId.trim() ? { selected_account_id: selectedAccountId.trim() } : {}),
        ...(loginUsername.trim() ? { username: loginUsername.trim() } : {}),
        ...(loginPassword.trim() ? { password: loginPassword.trim() } : {}),
        ...(openId.trim() ? { open_id: openId.trim() } : {}),
        ...(avatarUrl.trim() ? { avatar_url: avatarUrl.trim() } : {}),
      });
      setItems(data.items);
      setRevealedKey(data.key);
      setRevealedLinkToken(String(data.item.link_token || ""));
      setName("");
      setQuota("30");
      setCustomKey("");
      setSelectedAccountId("");
      setLoginUsername("");
      setLoginPassword("");
      setOpenId("");
      setAvatarUrl("");
      setIsDialogOpen(false);
      toast.success("用户密钥已创建");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "创建用户密钥失败");
    } finally {
      setIsCreating(false);
    }
  };

  const setItemPending = (id: string, isPending: boolean) => {
    setPendingIds((current) => {
      const next = new Set(current);
      if (isPending) {
        next.add(id);
      } else {
        next.delete(id);
      }
      return next;
    });
  };

  const handleToggle = async (item: UserKey) => {
    setItemPending(item.id, true);
    try {
      const data = await updateUserKey(item.id, { enabled: !item.enabled });
      setItems(data.items);
      toast.success(item.enabled ? "用户密钥已禁用" : "用户密钥已启用");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新用户密钥失败");
    } finally {
      setItemPending(item.id, false);
    }
  };

  const handleQuotaChange = async (item: UserKey, value: string) => {
    const nextQuota = Math.max(0, Math.floor(Number(value) || 0));
    setItemPending(item.id, true);
    try {
      const data = await updateUserKey(item.id, { quota: nextQuota });
      setItems(data.items);
      toast.success("额度已更新");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新额度失败");
    } finally {
      setItemPending(item.id, false);
    }
  };

  const handleDelete = async () => {
    if (!deletingItem) {
      return;
    }
    const item = deletingItem;
    setItemPending(item.id, true);
    try {
      const data = await deleteUserKey(item.id);
      setItems(data.items);
      setDeletingItem(null);
      toast.success("用户密钥已删除");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除用户密钥失败");
    } finally {
      setItemPending(item.id, false);
    }
  };

  const openEditDialog = (item: UserKey) => {
    setEditingItem(item);
    setEditName(item.name);
    setEditKey("");
    setEditSelectedAccountId(String(item.selected_account_id || ""));
    setEditUsername(String(item.username || ""));
    setEditPassword("");
    setEditOpenId(String(item.open_id || ""));
    setEditAvatarUrl(String(item.avatar_url || ""));
  };

  const handleEdit = async () => {
    if (!editingItem) {
      return;
    }
    const item = editingItem;
    const trimmedName = editName.trim();
    const trimmedKey = editKey.trim();
    if (
      trimmedName === item.name
      && !trimmedKey
      && editSelectedAccountId.trim() === String(item.selected_account_id || "")
      && editUsername.trim() === String(item.username || "")
      && !editPassword.trim()
      && editOpenId.trim() === String(item.open_id || "")
      && editAvatarUrl.trim() === String(item.avatar_url || "")
    ) {
      setEditingItem(null);
      return;
    }
    setItemPending(item.id, true);
    try {
      const data = await updateUserKey(item.id, {
        ...(trimmedName !== item.name ? { name: trimmedName } : {}),
        ...(trimmedKey ? { key: trimmedKey } : {}),
        ...(editSelectedAccountId.trim() !== String(item.selected_account_id || "") ? { selected_account_id: editSelectedAccountId.trim() } : {}),
        ...(editUsername.trim() !== String(item.username || "") ? { username: editUsername.trim() } : {}),
        ...(editPassword.trim() ? { password: editPassword.trim() } : {}),
        ...(editOpenId.trim() !== String(item.open_id || "") ? { open_id: editOpenId.trim() } : {}),
        ...(editAvatarUrl.trim() !== String(item.avatar_url || "") ? { avatar_url: editAvatarUrl.trim() } : {}),
      });
      setItems(data.items);
      if (trimmedKey) {
        setRevealedKey(trimmedKey);
        setRevealedLinkToken(String(item.link_token || ""));
      }
      setEditingItem(null);
      setEditKey("");
      setEditPassword("");
      toast.success(trimmedKey ? "用户密钥已更新" : "用户名称已更新");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新用户密钥失败");
    } finally {
      setItemPending(item.id, false);
    }
  };

  const handleCopy = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success("已复制到剪贴板");
    } catch {
      toast.error("复制失败，请手动复制");
    }
  };

  const handleResetAndCopyKey = async (item: UserKey) => {
    const nextKey = createRandomUserKey();
    setItemPending(item.id, true);
    try {
      const data = await updateUserKey(item.id, { key: nextKey });
      setItems(data.items);
      await handleCopy(nextKey);
      setRevealedKey(nextKey);
      setRevealedLinkToken(String(item.link_token || ""));
      toast.success("已重置并复制新的 API Key，旧 Key 已失效");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "重置 API Key 失败");
    } finally {
      setItemPending(item.id, false);
    }
  };

  return (
    <>
      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="space-y-5 p-4 sm:space-y-6 sm:p-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-xl bg-stone-100">
                {standalone ? <UsersRound className="size-5 text-stone-600" /> : <KeyRound className="size-5 text-stone-600" />}
              </div>
              <div>
                <h2 className="text-lg font-semibold tracking-tight">{standalone ? "普通用户管理" : "用户密钥管理"}</h2>
                <p className="text-sm text-stone-500">
                  {standalone
                    ? "支持搜索、状态筛选、分页管理、调额度和复制免登录链接。"
                    : "为普通用户创建专用密钥；普通用户只能进入画图页，不能查看设置和号池。"}
                </p>
              </div>
            </div>
            <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row">
              <Button
                type="button"
                variant="outline"
                className="h-9 rounded-xl border-stone-200 bg-white px-4 text-stone-700"
                onClick={() => void load(true)}
                disabled={isLoading}
              >
                {isLoading ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                刷新
              </Button>
              <Button className="h-9 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800" onClick={() => setIsDialogOpen(true)}>
                <Plus className="size-4" />
                创建用户密钥
              </Button>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {[
              ["用户总数", stats.total],
              ["已启用", stats.enabled],
              ["已禁用", stats.disabled],
              ["剩余额度", stats.quota],
            ].map(([label, value]) => (
              <div key={label} className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
                <div className="text-xs text-stone-500">{label}</div>
                <div className="mt-1 text-xl font-semibold text-stone-950">{value}</div>
              </div>
            ))}
          </div>

          {revealedKey ? (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-4 text-sm text-emerald-900">
              <div className="font-medium">新密钥仅展示一次，请立即保存：</div>
              <div className="mt-3 flex flex-col gap-3 rounded-lg border border-emerald-200 bg-white/80 p-3 md:flex-row md:items-center md:justify-between">
                <code className="break-all font-mono text-[13px]">{revealedKey}</code>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    className="h-9 rounded-xl border-emerald-200 bg-white px-4 text-emerald-700"
                    onClick={() => void handleCopy(revealedKey)}
                  >
                    <Copy className="size-4" />
                    复制密钥
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    className="h-9 rounded-xl border-emerald-200 bg-white px-4 text-emerald-700"
                    onClick={() => revealedLinkToken ? void handleCopy(`${window.location.origin}/image/?key=${encodeURIComponent(revealedLinkToken)}`) : undefined}
                    disabled={!revealedLinkToken}
                  >
                    <Copy className="size-4" />
                    复制免登录链接
                  </Button>
                </div>
              </div>
            </div>
          ) : null}

          <div className="grid gap-3 lg:grid-cols-[minmax(220px,1fr)_160px_150px]">
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-stone-400" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索名称、ID、用户名、OpenID、绑定账号、令牌、时间"
                className="h-11 rounded-xl border-stone-200 bg-white pl-9"
              />
            </div>
            <Select value={statusFilter} onValueChange={(value) => setStatusFilter(value as StatusFilter)}>
              <SelectTrigger className="h-11 rounded-xl border-stone-200 bg-white">
                <SelectValue placeholder="状态筛选" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部状态</SelectItem>
                <SelectItem value="enabled">仅已启用</SelectItem>
                <SelectItem value="disabled">仅已禁用</SelectItem>
                <SelectItem value="empty">启用但额度为 0</SelectItem>
              </SelectContent>
            </Select>
            <Select value={String(pageSize)} onValueChange={(value) => setPageSize(Number(value) as (typeof PAGE_SIZE_OPTIONS)[number])}>
              <SelectTrigger className="h-11 rounded-xl border-stone-200 bg-white">
                <SelectValue placeholder="每页数量" />
              </SelectTrigger>
              <SelectContent>
                {PAGE_SIZE_OPTIONS.map((item) => (
                  <SelectItem key={item} value={String(item)}>
                    每页 {item} 条
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {stats.empty > 0 ? (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
              当前有 {stats.empty} 个已启用用户额度为 0，可通过筛选快速定位后补额度或禁用。
            </div>
          ) : null}

          {isLoading ? (
            <div className="flex items-center justify-center py-10">
              <LoaderCircle className="size-5 animate-spin text-stone-400" />
            </div>
          ) : items.length === 0 ? (
            <div className="rounded-xl bg-stone-50 px-6 py-10 text-center text-sm text-stone-500">
              暂无普通用户密钥。点击右上角按钮后即可创建并分发给其他人。
            </div>
          ) : filteredItems.length === 0 ? (
            <div className="rounded-xl bg-stone-50 px-6 py-10 text-center text-sm text-stone-500">
              没有找到符合条件的用户，可清空搜索或切换状态筛选。
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex flex-col gap-2 text-sm text-stone-500 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  当前显示 <span className="font-medium text-stone-900">{filteredItems.length}</span> / {items.length} 个用户
                </div>
                <div>
                  第 <span className="font-medium text-stone-900">{safePage}</span> / {pageCount} 页
                </div>
              </div>
              {pagedItems.map((item) => {
                const isPending = pendingIds.has(item.id);
                return (
                  <div key={item.id} className="flex flex-col gap-3 rounded-xl border border-stone-200 bg-white px-3 py-4 md:flex-row md:items-center md:justify-between md:px-4">
                    <div className="min-w-0 space-y-2">
                      <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center">
                        <div className="truncate text-sm font-medium text-stone-800">{item.name}</div>
                        <Badge variant={item.enabled ? "success" : "secondary"} className="rounded-md">
                          {item.enabled ? "已启用" : "已禁用"}
                        </Badge>
                      </div>
                      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-stone-500">
                        <span>创建时间 {formatDateTime(item.created_at)}</span>
                        <span>最近使用 {formatDateTime(item.last_used_at)}</span>
                        <span>登录账号 {item.username || "未设置"}</span>
                        <span>绑定生图账号 {accountLabel(item.selected_account_id)}</span>
                        {item.open_id ? <span>OpenID {item.open_id}</span> : null}
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-2">
                      <div className="flex h-9 items-center gap-2 rounded-xl border border-stone-200 bg-white px-3 text-sm text-stone-700">
                        <span className="text-xs text-stone-500">额度</span>
                        <Input
                          type="number"
                          min="0"
                          defaultValue={String(item.quota ?? 0)}
                          className="h-7 w-20 border-0 bg-transparent px-0 text-center shadow-none focus-visible:ring-0"
                          disabled={isPending}
                          onBlur={(event) => void handleQuotaChange(item, event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") {
                              event.currentTarget.blur();
                            }
                          }}
                        />
                      </div>
                      <Button
                        type="button"
                        variant="outline"
                        className="h-9 rounded-xl border-stone-200 bg-white px-4 text-stone-700"
                        onClick={() => item.key ? void handleCopy(item.key) : void handleResetAndCopyKey(item)}
                        disabled={isPending}
                      >
                        {isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Copy className="size-4" />}
                        复制 Key
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        className="h-9 rounded-xl border-stone-200 bg-white px-4 text-stone-700"
                        onClick={() => void handleResetAndCopyKey(item)}
                        disabled={isPending}
                        title="重新生成 API Key；旧 Key 会立即失效"
                      >
                        {isPending ? <LoaderCircle className="size-4 animate-spin" /> : <RotateCcwKey className="size-4" />}
                        重置 Key
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        className="h-9 rounded-xl border-stone-200 bg-white px-4 text-stone-700"
                        onClick={() => item.link_token ? void handleCopy(`${window.location.origin}/image/?key=${encodeURIComponent(item.link_token)}`) : undefined}
                        disabled={isPending || !item.link_token}
                      >
                        <Copy className="size-4" />
                        免登录链接
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        className="h-9 rounded-xl border-stone-200 bg-white px-4 text-stone-700"
                        onClick={() => openEditDialog(item)}
                        disabled={isPending}
                      >
                        {isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Pencil className="size-4" />}
                        编辑
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        className="h-9 rounded-xl border-stone-200 bg-white px-4 text-stone-700"
                        onClick={() => void handleToggle(item)}
                        disabled={isPending}
                      >
                        {isPending ? (
                          <LoaderCircle className="size-4 animate-spin" />
                        ) : item.enabled ? (
                          <Ban className="size-4" />
                        ) : (
                          <CheckCircle2 className="size-4" />
                        )}
                        {item.enabled ? "禁用" : "启用"}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        className="h-9 rounded-xl border-rose-200 bg-white px-4 text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                        onClick={() => setDeletingItem(item)}
                        disabled={isPending}
                      >
                        {isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Trash2 className="size-4" />}
                        删除
                      </Button>
                    </div>
                  </div>
                );
              })}
              <div className="flex flex-col gap-3 border-t border-stone-100 pt-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="text-sm text-stone-500">
                  显示第 {(safePage - 1) * pageSize + 1} - {Math.min(safePage * pageSize, filteredItems.length)} 条，共 {filteredItems.length} 条
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    className="h-9 rounded-xl border-stone-200 bg-white px-4 text-stone-700"
                    onClick={() => setPage((current) => Math.max(1, current - 1))}
                    disabled={safePage <= 1}
                  >
                    上一页
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    className="h-9 rounded-xl border-stone-200 bg-white px-4 text-stone-700"
                    onClick={() => setPage((current) => Math.min(pageCount, current + 1))}
                    disabled={safePage >= pageCount}
                  >
                    下一页
                  </Button>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
        <DialogContent className="rounded-2xl p-4 sm:p-6">
          <DialogHeader className="gap-2">
            <DialogTitle>创建用户密钥</DialogTitle>
            <DialogDescription className="text-sm leading-6">
              可选填写一个备注名称，方便区分不同使用者；创建后会生成一条只能查看一次的原始密钥。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <label className="text-sm font-medium text-stone-700">名称（可选）</label>
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：设计同学 A、运营临时账号"
              className="h-11 rounded-xl border-stone-200 bg-white"
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium text-stone-700">生成额度（张）</label>
            <Input
              type="number"
              min="0"
              value={quota}
              onChange={(event) => setQuota(event.target.value)}
              placeholder="例如：30"
              className="h-11 rounded-xl border-stone-200 bg-white"
            />
            <p className="text-xs text-stone-500">普通用户每提交 1 张图片消耗 1 张额度；额度为 0 时不能继续生成。</p>
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium text-stone-700">自定义 API Key（可选）</label>
            <Input
              value={customKey}
              onChange={(event) => setCustomKey(event.target.value)}
              placeholder="例如：sk-your-custom-user-key；留空自动生成"
              className="h-11 rounded-xl border-stone-200 bg-white font-mono"
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium text-stone-700">绑定生图账号（可选）</label>
            <Select value={selectedAccountId || "__none"} onValueChange={(value) => setSelectedAccountId(value === "__none" ? "" : value)}>
              <SelectTrigger className="h-11 rounded-xl border-stone-200 bg-white">
                <SelectValue placeholder="不指定，使用可用号池" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none">不指定，使用可用号池</SelectItem>
                {accountOptions.map((account) => (
                  <SelectItem key={account.id} value={account.id}>{account.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-stone-500">指定后，这个用户用自己的 Key 生图会优先走该账号。</p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <label className="text-sm font-medium text-stone-700">登录账号 / 小程序昵称</label>
              <Input value={loginUsername} onChange={(event) => setLoginUsername(event.target.value)} placeholder="用于账号密码登录，必须唯一" className="h-11 rounded-xl border-stone-200 bg-white" />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium text-stone-700">登录密码</label>
              <Input type="password" value={loginPassword} onChange={(event) => setLoginPassword(event.target.value)} placeholder="可后续修改" className="h-11 rounded-xl border-stone-200 bg-white" />
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <label className="text-sm font-medium text-stone-700">微信 OpenID（可选）</label>
              <Input value={openId} onChange={(event) => setOpenId(event.target.value)} placeholder="用于小程序唯一绑定" className="h-11 rounded-xl border-stone-200 bg-white" />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium text-stone-700">头像 URL（可选）</label>
              <Input value={avatarUrl} onChange={(event) => setAvatarUrl(event.target.value)} placeholder="小程序头像地址" className="h-11 rounded-xl border-stone-200 bg-white" />
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              className="h-10 rounded-xl bg-stone-100 px-5 text-stone-700 hover:bg-stone-200"
              onClick={() => setIsDialogOpen(false)}
              disabled={isCreating}
            >
              取消
            </Button>
            <Button
              type="button"
              className="h-10 rounded-xl bg-stone-950 px-5 text-white hover:bg-stone-800"
              onClick={() => void handleCreate()}
              disabled={isCreating}
            >
              {isCreating ? <LoaderCircle className="size-4 animate-spin" /> : <Plus className="size-4" />}
              创建
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(deletingItem)} onOpenChange={(open) => (!open ? setDeletingItem(null) : null)}>
        <DialogContent className="rounded-2xl p-4 sm:p-6">
          <DialogHeader className="gap-2">
            <DialogTitle>删除用户密钥</DialogTitle>
            <DialogDescription className="text-sm leading-6">
              确认删除用户密钥「{deletingItem?.name}」吗？删除后该密钥将无法继续调用接口。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              className="h-10 rounded-xl bg-stone-100 px-5 text-stone-700 hover:bg-stone-200"
              onClick={() => setDeletingItem(null)}
              disabled={deletingItem ? pendingIds.has(deletingItem.id) : false}
            >
              取消
            </Button>
            <Button
              type="button"
              className="danger-confirm-button h-10 rounded-xl px-5"
              onClick={() => void handleDelete()}
              disabled={deletingItem ? pendingIds.has(deletingItem.id) : false}
            >
              {deletingItem && pendingIds.has(deletingItem.id) ? <LoaderCircle className="size-4 animate-spin" /> : <Trash2 className="size-4" />}
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(editingItem)}
        onOpenChange={(open) => {
          if (!open) {
            setEditingItem(null);
            setEditKey("");
            setEditPassword("");
          }
        }}
      >
        <DialogContent className="rounded-2xl p-6">
          <DialogHeader className="gap-2">
            <DialogTitle>编辑用户密钥</DialogTitle>
            <DialogDescription className="text-sm leading-6">
              可以修改备注名称；如需更换专用密钥，直接填写新的原始密钥即可。留空则保持当前密钥不变。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium text-stone-700">名称</label>
              <Input
                value={editName}
                onChange={(event) => setEditName(event.target.value)}
                placeholder="例如：设计同学 A、运营临时账号"
                className="h-11 rounded-xl border-stone-200 bg-white"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium text-stone-700">新的专用密钥（可选）</label>
              <Input
                value={editKey}
                onChange={(event) => setEditKey(event.target.value)}
                placeholder="例如：sk-your-custom-user-key"
                className="h-11 rounded-xl border-stone-200 bg-white font-mono"
              />
              <p className="text-xs leading-5 text-stone-500">
                保存后旧密钥会立即失效，新密钥生效。系统仍只保存哈希，不会回显当前密钥。
              </p>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium text-stone-700">绑定生图账号</label>
              <Select value={editSelectedAccountId || "__none"} onValueChange={(value) => setEditSelectedAccountId(value === "__none" ? "" : value)}>
                <SelectTrigger className="h-11 rounded-xl border-stone-200 bg-white">
                  <SelectValue placeholder="不指定，使用可用号池" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none">不指定，使用可用号池</SelectItem>
                  {accountOptions.map((account) => (
                    <SelectItem key={account.id} value={account.id}>{account.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-stone-500">当前：{accountLabel(editingItem?.selected_account_id)}</p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-2">
                <label className="text-sm font-medium text-stone-700">登录账号 / 小程序昵称</label>
                <Input value={editUsername} onChange={(event) => setEditUsername(event.target.value)} placeholder="用于账号密码登录，必须唯一" className="h-11 rounded-xl border-stone-200 bg-white" />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-stone-700">重置登录密码（可选）</label>
                <Input type="password" value={editPassword} onChange={(event) => setEditPassword(event.target.value)} placeholder="留空不修改" className="h-11 rounded-xl border-stone-200 bg-white" />
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-2">
                <label className="text-sm font-medium text-stone-700">微信 OpenID</label>
                <Input value={editOpenId} onChange={(event) => setEditOpenId(event.target.value)} placeholder="用于小程序唯一绑定" className="h-11 rounded-xl border-stone-200 bg-white" />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-stone-700">头像 URL</label>
                <Input value={editAvatarUrl} onChange={(event) => setEditAvatarUrl(event.target.value)} placeholder="小程序头像地址" className="h-11 rounded-xl border-stone-200 bg-white" />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              className="h-10 rounded-xl bg-stone-100 px-5 text-stone-700 hover:bg-stone-200"
              onClick={() => {
                setEditingItem(null);
                setEditKey("");
                setEditPassword("");
              }}
              disabled={editingItem ? pendingIds.has(editingItem.id) : false}
            >
              取消
            </Button>
            <Button
              type="button"
              className="h-10 rounded-xl bg-stone-950 px-5 text-white hover:bg-stone-800"
              onClick={() => void handleEdit()}
              disabled={editingItem ? pendingIds.has(editingItem.id) : false}
            >
              {editingItem && pendingIds.has(editingItem.id) ? <LoaderCircle className="size-4 animate-spin" /> : <Pencil className="size-4" />}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
