"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import { getValidatedAuthSession } from "@/lib/auth-session";
import {
  getDefaultRouteForSession,
  getStoredAuthSession,
  type AuthRole,
  type StoredAuthSession,
} from "@/store/auth";
import { consumeShareKeyFromUrl } from "@/lib/request";

type UseAuthGuardResult = {
  isCheckingAuth: boolean;
  session: StoredAuthSession | null;
};

function isImageScopeRoute(pathname: string) {
  return pathname === "/image"
    || pathname.startsWith("/image/")
    || pathname === "/image-manager"
    || pathname.startsWith("/image-manager/")
    || pathname === "/profile"
    || pathname.startsWith("/profile/");
}

export function useAuthGuard(allowedRoles?: AuthRole[]): UseAuthGuardResult {
  const router = useRouter();
  const pathname = usePathname();
  const [session, setSession] = useState<StoredAuthSession | null>(null);
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);
  const allowedRolesKey = (allowedRoles || []).join(",");

  useEffect(() => {
    let active = true;

    const load = async () => {
      const roleList = allowedRolesKey ? (allowedRolesKey.split(",") as AuthRole[]) : [];
      await consumeShareKeyFromUrl();
      const storedSession = await getStoredAuthSession();
      if (!active) {
        return;
      }

      if (!storedSession) {
        setSession(null);
        setIsCheckingAuth(false);
        router.replace("/login");
        return;
      }

      if (storedSession.scope === "image" && !isImageScopeRoute(pathname)) {
        setSession(storedSession);
        setIsCheckingAuth(false);
        router.replace("/image");
        return;
      }

      if (roleList.length > 0 && !roleList.includes(storedSession.role)) {
        setSession(storedSession);
        setIsCheckingAuth(false);
        router.replace(getDefaultRouteForSession(storedSession));
        return;
      }

      setSession(storedSession);
      setIsCheckingAuth(false);
    };

    void load();
    return () => {
      active = false;
    };
  }, [allowedRolesKey, pathname, router]);

  return { isCheckingAuth, session };
}

export function useRedirectIfAuthenticated() {
  const router = useRouter();
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);

  useEffect(() => {
    let active = true;

    const load = async () => {
      await consumeShareKeyFromUrl();
      const storedSession = await getStoredAuthSession();
      if (!active) {
        return;
      }

      if (storedSession) {
        router.replace(getDefaultRouteForSession(storedSession));
        return;
      }

      setIsCheckingAuth(false);
    };

    void load();
    return () => {
      active = false;
    };
  }, [router]);

  return { isCheckingAuth };
}
