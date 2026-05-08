"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { LoaderCircle, LockKeyhole } from "lucide-react";
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
      <div className="grid min-h-[calc(100vh-1rem)] w-full place-items-center px-4 py-6">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  return (
    <div className="grid min-h-[calc(100vh-1rem)] w-full place-items-center px-4 py-6">
      <Card className="w-full max-w-[505px] rounded-[30px] border-white/80 bg-white/95 shadow-[0_28px_90px_rgba(28,25,23,0.10)]">
        <CardContent className="space-y-7 p-6 sm:p-8">
          <div className="space-y-4 text-center">
            <div className="mx-auto inline-flex size-14 items-center justify-center rounded-[18px] bg-stone-950 text-white shadow-sm">
              <LockKeyhole className="size-5" />
            </div>
            <div className="space-y-2">
              <h1 className="text-3xl font-semibold tracking-tight text-stone-950">欢迎回来</h1>
              <p className="text-sm leading-6 text-stone-500">管理员可使用账号密码登录；普通用户仍可使用密钥或免登录链接。</p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2 rounded-2xl bg-stone-100 p-1">
            {([
              ["password", "账号密码"],
              ["key", "密钥登录"],
            ] as const).map(([mode, label]) => (
              <button
                key={mode}
                type="button"
                className={cn(
                  "h-10 rounded-xl text-sm font-medium transition",
                  loginMode === mode ? "bg-white text-stone-950 shadow-sm" : "text-stone-500 hover:text-stone-800",
                )}
                onClick={() => setLoginMode(mode)}
              >
                {label}
              </button>
            ))}
          </div>

          {loginMode === "password" ? (
            <div className="space-y-4">
              <div className="space-y-2">
                <label htmlFor="username" className="block text-sm font-medium text-stone-700">
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
                  placeholder="请输入管理员账号"
                  className="h-13 rounded-2xl border-stone-200 bg-white px-4"
                />
              </div>
              <div className="space-y-2">
                <label htmlFor="password" className="block text-sm font-medium text-stone-700">
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
                  placeholder="请输入管理员密码"
                  className="h-13 rounded-2xl border-stone-200 bg-white px-4"
                />
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <label htmlFor="auth-key" className="block text-sm font-medium text-stone-700">
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
                className="h-13 rounded-2xl border-stone-200 bg-white px-4"
              />
            </div>
          )}

          <div className="space-y-3">
            <Button
              className="h-13 w-full rounded-2xl bg-stone-950 text-white hover:bg-stone-800"
              onClick={() => void handleLogin()}
              disabled={isSubmitting}
            >
              {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : null}
              登录
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
