"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Copy, KeyRound, LoaderCircle, Settings2, Terminal } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { fetchCurrentUser } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { getStoredAuthSession, type StoredAuthSession } from "@/store/auth";

type CurrentUser = Awaited<ReturnType<typeof fetchCurrentUser>>;

function maskKey(value: string) {
  const key = String(value || "").trim();
  if (key.length <= 16) {
    return key;
  }
  return `${key.slice(0, 8)}…${key.slice(-8)}`;
}

function CopyButton({ value, label = "复制" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      toast.success("已复制到剪贴板");
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("复制失败，请手动复制");
    }
  };

  return (
    <Button
      type="button"
      variant="outline"
      className="h-9 shrink-0 rounded-xl border-stone-200 bg-white px-3 text-stone-700 hover:bg-stone-50"
      onClick={() => void handleCopy()}
      disabled={!value}
    >
      {copied ? <Check className="size-4 text-emerald-600" /> : <Copy className="size-4" />}
      {copied ? "已复制" : label}
    </Button>
  );
}

function CodeBlock({ title, value }: { title: string; value: string }) {
  return (
    <div className="space-y-2">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="text-sm font-medium text-stone-800">{title}</div>
        <CopyButton value={value} label="复制示例" />
      </div>
      <pre className="max-h-[360px] overflow-x-auto rounded-2xl border border-stone-200 bg-stone-950 p-4 text-xs leading-6 text-stone-50 shadow-inner sm:text-[13px]">
        <code>{value}</code>
      </pre>
    </div>
  );
}

export default function ProfilePage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin", "user"]);
  const [storedSession, setStoredSession] = useState<StoredAuthSession | null>(null);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [isLoadingUser, setIsLoadingUser] = useState(false);

  useEffect(() => {
    let active = true;
    getStoredAuthSession().then((value) => {
      if (active) {
        setStoredSession(value);
      }
    });
    return () => {
      active = false;
    };
  }, [session?.key]);

  useEffect(() => {
    let active = true;
    if (!session) {
      setCurrentUser(null);
      return () => {
        active = false;
      };
    }
    setIsLoadingUser(true);
    fetchCurrentUser()
      .then((value) => {
        if (active) {
          setCurrentUser(value);
        }
      })
      .catch(() => undefined)
      .finally(() => {
        if (active) {
          setIsLoadingUser(false);
        }
      });
    return () => {
      active = false;
    };
  }, [session]);

  const apiBaseUrl = useMemo(() => {
    if (typeof window === "undefined") {
      return "";
    }
    return window.location.origin.replace(/\/$/, "");
  }, []);

  const apiKey = storedSession?.key || session?.key || "";
  const displayUser = currentUser || session;
  const quotaText = displayUser?.quota == null ? "不限额度" : `${displayUser.quota} 张`;
  const chatCurl = `curl ${apiBaseUrl}/v1/chat/completions \\
  -H "Authorization: Bearer ${apiKey || "YOUR_KEY"}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "gpt-5-5",
    "messages": [
      {"role": "user", "content": "你好，帮我写一句介绍"}
    ]
  }'`;

  const imageCurl = `curl ${apiBaseUrl}/v1/images/generations \\
  -H "Authorization: Bearer ${apiKey || "YOUR_KEY"}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "gpt-image-2",
    "prompt": "生成一张阳光下的猫咪照片",
    "n": 1,
    "size": "1024x1024",
    "response_format": "url"
  }'`;

  if (isCheckingAuth || !session) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  return (
    <div className="space-y-4 sm:space-y-6">
      <div className="flex flex-col gap-2">
        <div className="text-xs font-semibold tracking-[0.18em] text-stone-500 uppercase">Profile / API</div>
        <h1 className="text-2xl font-semibold tracking-tight text-stone-950">个人设置与 API 使用</h1>
        <p className="text-sm leading-6 text-stone-500">
          这里可以复制当前登录 Key，并按 OpenAI 兼容格式调用聊天和图片接口。
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
          <CardContent className="space-y-4 p-4 sm:p-6">
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-xl bg-stone-100">
                <Settings2 className="size-5 text-stone-600" />
              </div>
              <div>
                <h2 className="text-lg font-semibold tracking-tight">当前账号</h2>
                <p className="text-sm text-stone-500">名称、额度和登录方式</p>
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
              <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
                <div className="text-xs text-stone-500">令牌名称</div>
                <div className="mt-1 truncate text-base font-semibold text-stone-950">
                  {displayUser?.name || "未命名用户"}
                </div>
              </div>
              <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
                <div className="text-xs text-stone-500">剩余额度</div>
                <div className="mt-1 text-base font-semibold text-stone-950">{quotaText}</div>
              </div>
              <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
                <div className="text-xs text-stone-500">账号角色</div>
                <div className="mt-1 text-base font-semibold text-stone-950">
                  {displayUser?.role === "admin" ? "管理员" : "普通用户"}
                </div>
              </div>
              <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
                <div className="text-xs text-stone-500">登录方式</div>
                <div className="mt-1 text-base font-semibold text-stone-950">
                  {displayUser?.auth_mode || session.authMode || "key"}
                  {isLoadingUser ? <LoaderCircle className="ml-2 inline size-3 animate-spin text-stone-400" /> : null}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
          <CardContent className="space-y-4 p-4 sm:p-6">
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-xl bg-stone-100">
                <KeyRound className="size-5 text-stone-600" />
              </div>
              <div>
                <h2 className="text-lg font-semibold tracking-tight">API Key</h2>
                <p className="text-sm text-stone-500">请求时放到 Authorization Bearer 里</p>
              </div>
            </div>

            <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3">
              <div className="text-xs font-medium text-red-600">当前 Key</div>
              <div className="mt-2 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <code className="break-all font-mono text-sm font-semibold text-red-600">{apiKey || "未读取到 Key"}</code>
                <CopyButton value={apiKey} label="复制 Key" />
              </div>
              <p className="mt-2 text-xs leading-5 text-red-500">
                请勿把 Key 发给无关人员；如果泄露，请联系管理员重置。
              </p>
            </div>

            <div className="rounded-2xl border border-stone-200 bg-stone-50/70 px-4 py-3">
              <div className="text-xs text-stone-500">API 请求地址</div>
              <div className="mt-2 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <code className="break-all font-mono text-sm text-stone-900">{apiBaseUrl || "当前域名"}</code>
                <CopyButton value={apiBaseUrl} label="复制地址" />
              </div>
            </div>

            <div className="rounded-2xl border border-stone-200 bg-stone-50/70 px-4 py-3 text-sm leading-6 text-stone-600">
              <div className="font-medium text-stone-900">常用接口</div>
              <div className="mt-1 space-y-1">
                <div>
                  聊天：<code className="font-mono text-stone-900">POST {apiBaseUrl}/v1/chat/completions</code>
                </div>
                <div>
                  生图：<code className="font-mono text-stone-900">POST {apiBaseUrl}/v1/images/generations</code>
                </div>
                <div>
                  模型：<code className="font-mono text-stone-900">GET {apiBaseUrl}/v1/models</code>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="space-y-5 p-4 sm:p-6">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-xl bg-stone-100">
              <Terminal className="size-5 text-stone-600" />
            </div>
            <div>
              <h2 className="text-lg font-semibold tracking-tight">OpenAI 兼容调用示例</h2>
              <p className="text-sm text-stone-500">直接复制到终端或代码里使用，按需替换 model 和 prompt。</p>
            </div>
          </div>
          <CodeBlock title="聊天接口示例" value={chatCurl} />
          <CodeBlock title="图片生成接口示例" value={imageCurl} />
        </CardContent>
      </Card>
    </div>
  );
}
