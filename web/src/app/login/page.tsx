"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { LoaderCircle, LockKeyhole, QrCode } from "lucide-react";
import { toast } from "sonner";

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
      <main className="login-shell">
        <div className="login-checking" aria-label="正在检查登录状态">
          <LoaderCircle className="size-5 animate-spin text-stone-400" />
        </div>
      </main>
    );
  }

  return (
    <main className="login-shell">
      <section className="login-card-shell" aria-label="登录 chatgpt2api">
        <div className="login-header">
          <div className="login-logo-badge">
            <LockKeyhole className="size-6" strokeWidth={2.2} />
          </div>
          <div className="login-title-block">
            <h1>欢迎回来</h1>
            <p>管理员和普通用户都可使用账号密码登录；普通用户也可继续使用密钥或小程序免登链路。</p>
          </div>
        </div>

        <div className="login-tabs" role="tablist" aria-label="登录方式">
          {([
            ["password", "账号密码"],
            ["key", "密钥登录"],
          ] as const).map(([mode, label]) => (
            <button
              key={mode}
              type="button"
              className={cn("login-tab", loginMode === mode && "is-active")}
              onClick={() => setLoginMode(mode)}
              aria-pressed={loginMode === mode}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="login-form-panel">
          {loginMode === "password" ? (
            <div className="login-fields">
              <label className="login-field" htmlFor="username">
                <span>账号</span>
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
                  className="login-input"
                />
              </label>
              <label className="login-field" htmlFor="password">
                <span>密码</span>
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
                  className="login-input"
                />
              </label>
            </div>
          ) : (
            <div className="login-fields">
              <label className="login-field" htmlFor="auth-key">
                <span>密钥</span>
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
                  className="login-input"
                />
              </label>
            </div>
          )}

          <button
            type="button"
            className="login-submit-button"
            onClick={() => void handleLogin()}
            disabled={isSubmitting}
            aria-label="登录"
          >
            {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : null}
            <span>登录</span>
          </button>
        </div>

        <div className="login-register-card">
          <div className="login-register-title">
            <QrCode className="size-4 text-stone-600" />
            <span>还没有账号？</span>
          </div>
          <p className="login-register-desc">可前往微信小程序「图灵画板」注册并设置登录密码，或关注公众号「身边风向」获取最新提醒。</p>
          <div className="login-register-body">
            <img src="/miniapp-qrcode.png" alt="图灵画板小程序二维码" className="login-mini-qrcode" />
            <div className="login-register-copy">
              <p>微信扫码进入小程序，首次登录会自动创建并绑定账号。</p>
              <p>在小程序「我的」页面可修改昵称、头像和 chatgpt2api 登录密码。</p>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
