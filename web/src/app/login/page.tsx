"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { LoaderCircle, LockKeyhole, QrCode } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { login, loginWithPassword } from "@/lib/api";
import { useRedirectIfAuthenticated } from "@/lib/use-auth-guard";
import { cn } from "@/lib/utils";
import { getDefaultRouteForSession, setStoredAuthSession, type StoredAuthSession } from "@/store/auth";

type LoginMode = "password" | "key";

export default function LoginPage() {
  const router = useRouter();
  const [loginMode, setLoginMode] = useState<LoginMode>("password");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [authKey, setAuthKey] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const { isCheckingAuth } = useRedirectIfAuthenticated();

  const handleLogin = async () => {
    const normalizedUsername = username.trim();
    const normalizedPassword = password.trim();
    const normalizedAuthKey = authKey.trim();

    if (loginMode === "password" && (!normalizedUsername || !normalizedPassword)) {
      toast.error("请输入账号和密码");
      return;
    }
    if (loginMode === "key" && !normalizedAuthKey) {
      toast.error("请输入密钥");
      return;
    }

    setIsSubmitting(true);
    try {
      const data = loginMode === "password"
        ? await loginWithPassword(normalizedUsername, normalizedPassword)
        : await login(normalizedAuthKey);
      const sessionKey = data.key || normalizedAuthKey;
      if (!sessionKey) {
        throw new Error("登录响应缺少会话凭证");
      }
      const session: StoredAuthSession = {
        key: sessionKey,
        role: data.role,
        subjectId: data.subject_id,
        name: data.name,
        quota: data.quota,
        scope: data.scope === "image" || sessionKey.startsWith("lk-") ? "image" : "full",
        authMode: data.auth_mode || (loginMode === "password" ? "password" : sessionKey.startsWith("lk-") ? "link" : "key"),
      };
      await setStoredAuthSession(session);
      router.replace(getDefaultRouteForSession(session));
    } catch (error) {
      const message = error instanceof Error ? error.message : "登录失败";
      toast.error(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isCheckingAuth) {
    return (
      <div className="grid min-h-[calc(100dvh-1rem)] w-full place-items-center px-4 py-6">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  return (
    <div className="grid min-h-[calc(100dvh-1rem)] w-full place-items-center px-3 py-5 sm:px-4 sm:py-8">
      <Card className="w-full max-w-[506px] rounded-[28px] border border-white/90 bg-white shadow-[0_28px_90px_rgba(64,51,36,0.10)] sm:rounded-[30px]">
        <CardContent className="space-y-6 p-5 sm:space-y-7 sm:p-8">
          <div className="space-y-4 text-center sm:space-y-5">
            <div className="mx-auto grid size-14 place-items-center rounded-[18px] bg-black text-white shadow-[0_12px_28px_rgba(0,0,0,0.18)]">
              <LockKeyhole className="size-6" strokeWidth={2.2} />
            </div>
            <div className="space-y-2">
              <h1 className="text-[30px] font-extrabold tracking-tight text-black sm:text-[32px]">欢迎回来</h1>
              <p className="mx-auto max-w-[420px] text-sm leading-7 text-stone-600">
                管理员和普通用户都可使用账号密码登录；普通用户也可继续使用密钥或小程序免登链路。
              </p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-1 rounded-[18px] bg-stone-100/80 p-1 shadow-inner shadow-stone-200/40">
            {([
              ["password", "账号密码"],
              ["key", "密钥登录"],
            ] as const).map(([mode, label]) => (
              <button
                key={mode}
                type="button"
                className={cn(
                  "h-10 rounded-[15px] text-sm font-medium transition",
                  loginMode === mode ? "bg-white text-black shadow-[0_2px_8px_rgba(0,0,0,0.08)]" : "text-stone-500 hover:text-stone-800",
                )}
                onClick={() => setLoginMode(mode)}
                aria-pressed={loginMode === mode}
              >
                {label}
              </button>
            ))}
          </div>

          {loginMode === "password" ? (
            <div className="space-y-5">
              <div className="space-y-2">
                <label htmlFor="username" className="block text-sm font-medium text-black">
                  账号
                </label>
                <Input
                  id="username"
                  autoComplete="username"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      void handleLogin();
                    }
                  }}
                  placeholder="请输入管理员账号或小程序用户名"
                  className="h-[52px] rounded-2xl border-stone-200 bg-white px-4 text-[16px] text-black shadow-[0_1px_4px_rgba(0,0,0,0.08)] placeholder:text-stone-400 focus-visible:border-stone-300 focus-visible:ring-stone-200/80"
                />
              </div>
              <div className="space-y-2">
                <label htmlFor="password" className="block text-sm font-medium text-black">
                  密码
                </label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      void handleLogin();
                    }
                  }}
                  placeholder="请输入登录密码"
                  className="h-[52px] rounded-2xl border-stone-200 bg-white px-4 text-[16px] text-black shadow-[0_1px_4px_rgba(0,0,0,0.08)] placeholder:text-stone-400 focus-visible:border-stone-300 focus-visible:ring-stone-200/80"
                />
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <label htmlFor="auth-key" className="block text-sm font-medium text-black">
                密钥
              </label>
              <Input
                id="auth-key"
                type="password"
                value={authKey}
                onChange={(event) => setAuthKey(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    void handleLogin();
                  }
                }}
                placeholder="请输入管理员密钥或用户密钥"
                className="h-[52px] rounded-2xl border-stone-200 bg-white px-4 text-[16px] text-black shadow-[0_1px_4px_rgba(0,0,0,0.08)] placeholder:text-stone-400 focus-visible:border-stone-300 focus-visible:ring-stone-200/80"
              />
            </div>
          )}

          <Button
            className="h-[52px] w-full rounded-2xl bg-black text-[15px] font-semibold text-white shadow-[0_12px_26px_rgba(0,0,0,0.14)] hover:bg-stone-900"
            onClick={() => void handleLogin()}
            disabled={isSubmitting}
          >
            {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : null}
            登录
          </Button>

          <div className="rounded-[22px] border border-stone-200 bg-white p-4 text-left shadow-[0_1px_8px_rgba(0,0,0,0.04)] sm:p-5">
            <div className="flex items-center gap-2 text-base font-bold text-black">
              <QrCode className="size-4 text-stone-600" />
              还没有账号？
            </div>
            <p className="mt-2 text-sm leading-6 text-stone-600">
              可前往微信小程序「图灵画板」注册并设置登录密码，或关注公众号「身边风向」获取最新提醒。
            </p>
            <div className="mt-4 flex flex-col gap-4 sm:flex-row sm:items-center">
              <img
                src="/miniapp-qrcode.png"
                alt="图灵画板小程序二维码"
                className="size-24 shrink-0 rounded-2xl border border-stone-200 bg-white object-cover p-1 shadow-[0_10px_24px_rgba(0,0,0,0.08)]"
              />
              <div className="space-y-2 text-sm leading-6 text-stone-600">
                <p>微信扫码进入小程序，首次登录会自动创建并绑定账号。</p>
                <p>在小程序「我的」页面可修改昵称、头像和 chatgpt2api 登录密码。</p>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
