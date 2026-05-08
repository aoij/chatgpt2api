"use client";

import { login } from "@/lib/api";
import { clearStoredAuthSession, getStoredAuthSession, setStoredAuthSession, type StoredAuthSession } from "@/store/auth";

let validatedSessionCache: StoredAuthSession | null | undefined;
let validatedSessionAt = 0;
let validatedSessionPromise: Promise<StoredAuthSession | null> | null = null;
const VALIDATED_SESSION_TTL_MS = 45_000;

export async function getValidatedAuthSession(): Promise<StoredAuthSession | null> {
  const storedSession = await getStoredAuthSession();
  if (!storedSession) {
    validatedSessionCache = null;
    validatedSessionAt = Date.now();
    return null;
  }

  if (
    validatedSessionCache
    && validatedSessionCache.key === storedSession.key
    && Date.now() - validatedSessionAt < VALIDATED_SESSION_TTL_MS
  ) {
    return validatedSessionCache;
  }

  if (validatedSessionPromise) {
    return validatedSessionPromise;
  }

  validatedSessionPromise = (async () => {
    try {
      const data = await login(storedSession.key);
      const nextSession: StoredAuthSession = {
        key: storedSession.key,
        role: data.role,
        subjectId: data.subject_id,
        name: data.name,
        quota: data.quota,
        scope: data.scope || storedSession.scope || "full",
        authMode: data.auth_mode || storedSession.authMode,
      };
      await setStoredAuthSession(nextSession);
      validatedSessionCache = nextSession;
      validatedSessionAt = Date.now();
      return nextSession;
    } catch {
      await clearStoredAuthSession();
      validatedSessionCache = null;
      validatedSessionAt = Date.now();
      return null;
    } finally {
      validatedSessionPromise = null;
    }
  })();

  return validatedSessionPromise;
}
