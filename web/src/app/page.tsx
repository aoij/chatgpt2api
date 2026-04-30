"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { consumeShareKeyFromUrl } from "@/lib/request";
import { getDefaultRouteForSession, getStoredAuthSession } from "@/store/auth";

export default function HomePage() {
  const router = useRouter();

  useEffect(() => {
    let active = true;

    const redirect = async () => {
      await consumeShareKeyFromUrl();
      const session = await getStoredAuthSession();
      if (!active) {
        return;
      }
      router.replace(session ? getDefaultRouteForSession(session) : "/login");
    };

    void redirect();
    return () => {
      active = false;
    };
  }, [router]);

  return null;
}
