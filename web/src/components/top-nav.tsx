"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import webConfig from "@/constants/common-env";
import { fetchPublicConfig, type PublicConfig } from "@/lib/api";
import { clearStoredAuthSession, getStoredAuthSession, type StoredAuthSession } from "@/store/auth";
import { cn } from "@/lib/utils";

const adminNavItems = [
  { href: "/image", label: "画图" },
  { href: "/accounts", label: "号池管理" },
  { href: "/register", label: "注册机" },
  { href: "/image-manager", label: "图片管理" },
  { href: "/logs", label: "日志管理" },
  { href: "/users", label: "用户管理" },
  { href: "/settings", label: "设置" },
];

const userNavItems = [{ href: "/image", label: "画图" }];

export function TopNav() {
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSession] = useState<StoredAuthSession | null | undefined>(undefined);
  const [publicConfig, setPublicConfig] = useState<PublicConfig | null>(null);

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

      const storedSession = await getStoredAuthSession();
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
            className="min-w-0 truncate py-1 text-[15px] font-bold tracking-tight text-stone-950 transition hover:text-stone-700 sm:max-w-[220px] sm:shrink-0 lg:max-w-none"
          >
            {publicConfig?.site_name || "chatgpt2api"}
          </Link>
          <button
            type="button"
            className="ml-auto shrink-0 py-1 text-xs text-stone-400 transition hover:text-stone-700 sm:hidden"
            onClick={() => void handleLogout()}
          >
            {"退出"}
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
                className={cn(
                  "relative shrink-0 snap-start whitespace-nowrap rounded-full px-3 py-1.5 text-[13px] font-medium transition sm:rounded-none sm:px-0 sm:py-1 sm:text-[15px]",
                  active
                    ? "bg-stone-950 text-white sm:bg-transparent sm:font-semibold sm:text-stone-950"
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
            {roleLabel}
          </span>
          <span className="hidden rounded-md bg-stone-100 px-2 py-1 text-[10px] font-medium text-stone-500 sm:inline-block sm:text-[11px]">
            v{webConfig.appVersion}
          </span>
          <button
            type="button"
            className="py-1 text-xs text-stone-400 transition hover:text-stone-700 sm:text-sm"
            onClick={() => void handleLogout()}
          >
            {"退出"}
          </button>
        </div>
      </div>
    </header>
  );
}
