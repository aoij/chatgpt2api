"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type MouseEvent } from "react";
import { usePathname, useRouter } from "next/navigation";

import webConfig from "@/constants/common-env";
import { fetchPublicConfig, type PublicConfig } from "@/lib/api";
import { getValidatedAuthSession } from "@/lib/auth-session";
import { cn } from "@/lib/utils";
import { clearStoredAuthSession, type StoredAuthSession } from "@/store/auth";

const adminNavItems = [
  { href: "/image", label: "画图" },
  { href: "/accounts", label: "号池管理" },
  { href: "/register", label: "注册机" },
  { href: "/image-manager", label: "图片管理" },
  { href: "/logs", label: "日志管理" },
  { href: "/users", label: "用户管理" },
  { href: "/settings", label: "设置" },
];

const userNavItems = [
  { href: "/image", label: "画图" },
  { href: "/image-manager", label: "图片管理" },
  { href: "/profile", label: "API 使用" },
];

export function TopNav() {
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSession] = useState<StoredAuthSession | null | undefined>(undefined);
  const [publicConfig, setPublicConfig] = useState<PublicConfig | null>(null);
  const [navigatingHref, setNavigatingHref] = useState("");

  useEffect(() => {
    let active = true;
    fetchPublicConfig()
      .then((config) => {
        if (!active) return;
        setPublicConfig(config);
        if (config.page_title) {
          document.title = config.page_title;
        }
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;

    const load = async () => {
      if (pathname === "/login") {
        if (!active) {
          return;
        }
        setSession(null);
        return;
      }

      const storedSession = await getValidatedAuthSession();
      if (!active) {
        return;
      }
      setSession(storedSession);
    };

    void load();
    return () => {
      active = false;
    };
  }, [pathname]);

  useEffect(() => {
    setNavigatingHref("");
  }, [pathname]);

  useEffect(() => {
    if (!session || pathname === "/login") {
      return;
    }
    const navItems = session.role === "admin" && session.scope !== "image" ? adminNavItems : userNavItems;
    navItems.forEach((item) => {
      router.prefetch(item.href);
    });
    router.prefetch("/image");
  }, [pathname, router, session]);

  const normalizePath = useCallback((value: string) => {
    const pathnameValue = String(value || "").split(/[?#]/, 1)[0] || "/";
    return pathnameValue.length > 1 ? pathnameValue.replace(/\/+$/, "") : pathnameValue;
  }, []);

  const handleFastNavigate = useCallback(
    (event: MouseEvent<HTMLAnchorElement>, href: string) => {
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.altKey ||
        event.ctrlKey ||
        event.shiftKey ||
        normalizePath(pathname) === normalizePath(href)
      ) {
        return;
      }

      event.preventDefault();
      setNavigatingHref(href);
      router.prefetch(href);

      window.setTimeout(() => {
        router.push(href);
      }, 0);

      // App Router 在页面 JS chunk 或当前页面主线程繁忙时，偶发会长时间停在原页面。
      // 给用户一个硬跳转兜底：700ms 内 URL 还没变，就走浏览器原生跳转。
      window.setTimeout(() => {
        if (normalizePath(window.location.pathname) !== normalizePath(href)) {
          window.location.assign(href);
        }
      }, 700);
    },
    [normalizePath, pathname, router],
  );

  const handleLogout = async () => {
    await clearStoredAuthSession();
    router.replace("/login");
  };

  if (pathname === "/login" || session === undefined || !session) {
    return null;
  }

  const navItems = session.role === "admin" && session.scope !== "image" ? adminNavItems : userNavItems;
  const roleLabel = session.role === "admin" ? "管理员" : "普通用户";
  const isImageOnlySession = session.scope === "image" && navItems.length === 1;
  const displayName = session.name.trim() || roleLabel;

  return (
    <header className="sticky top-1 z-40 rounded-2xl border border-white/70 bg-white/80 shadow-sm backdrop-blur-xl sm:top-2 sm:rounded-none sm:border-x-0 sm:border-t-0 sm:bg-transparent sm:shadow-none sm:backdrop-blur-none">
      <div
        className={cn(
          "flex min-w-0 flex-col gap-1 px-3 py-2 sm:h-12 sm:flex-row sm:items-center sm:justify-between sm:gap-3 sm:px-6 sm:py-0",
          isImageOnlySession ? "min-h-11" : "min-h-12",
        )}
      >
        <div className="flex min-w-0 items-center justify-between gap-2 sm:justify-start sm:gap-3">
          <Link
            href="/image"
            prefetch
            onClick={(event) => handleFastNavigate(event, "/image")}
            onPointerEnter={() => router.prefetch("/image")}
            onFocus={() => router.prefetch("/image")}
            className="min-w-0 truncate py-1 text-[15px] font-bold tracking-tight text-stone-950 transition hover:text-stone-700 sm:max-w-[220px] sm:shrink-0 lg:max-w-none"
          >
            {publicConfig?.site_name || "chatgpt2api"}
          </Link>
          <button
            type="button"
            className="ml-auto shrink-0 py-1 text-xs text-stone-400 transition hover:text-stone-700 sm:hidden"
            onClick={() => void handleLogout()}
          >
            退出
          </button>
        </div>
        <nav
          className={cn(
            "hide-scrollbar -mx-1 min-w-0 flex-1 snap-x gap-1 overflow-x-auto px-1 pb-0.5 sm:mx-0 sm:flex sm:justify-center sm:gap-8 sm:overflow-visible sm:px-0 sm:pb-0",
            isImageOnlySession ? "hidden" : "flex",
          )}
        >
          {navItems.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                prefetch
                onClick={(event) => handleFastNavigate(event, item.href)}
                onPointerEnter={() => router.prefetch(item.href)}
                onFocus={() => router.prefetch(item.href)}
                className={cn(
                  "relative shrink-0 snap-start whitespace-nowrap rounded-full px-3 py-1.5 text-[13px] font-medium transition sm:rounded-none sm:px-0 sm:py-1 sm:text-[15px]",
                  active
                    ? "bg-stone-950 text-white sm:bg-transparent sm:font-semibold sm:text-stone-950"
                    : navigatingHref === item.href
                      ? "bg-stone-100 text-stone-700 sm:bg-transparent sm:text-stone-700"
                      : "text-stone-500 hover:text-stone-900",
                )}
              >
                {item.label}
                {active ? <span className="absolute inset-x-0 -bottom-[1px] hidden h-0.5 bg-stone-950 sm:block" /> : null}
              </Link>
            );
          })}
        </nav>
        <div className="hidden items-center justify-end gap-2 sm:flex sm:gap-3">
          <span className="hidden rounded-md bg-stone-100 px-2 py-1 text-[10px] font-medium text-stone-500 sm:inline-block sm:text-[11px]">
            {roleLabel} · {displayName}
          </span>
          <span className="hidden rounded-md bg-stone-100 px-2 py-1 text-[10px] font-medium text-stone-500 sm:inline-block sm:text-[11px]">
            v{webConfig.appVersion}
          </span>
          <button
            type="button"
            className="py-1 text-xs text-stone-400 transition hover:text-stone-700 sm:text-sm"
            onClick={() => void handleLogout()}
          >
            退出
          </button>
        </div>
      </div>
    </header>
  );
}
