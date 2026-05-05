import axios, {AxiosError, type AxiosRequestConfig} from "axios";

import webConfig from "@/constants/common-env";
import {clearStoredAuthSession, getStoredAuthKey, setStoredAuthSession} from "@/store/auth";

type RequestConfig = AxiosRequestConfig & {
    redirectOnUnauthorized?: boolean;
};

type ErrorPayload = {
    detail?: string | { error?: string | { message?: string } };
    error?: string | { message?: string };
    message?: string;
};

function errorMessageFromValue(value: unknown): string {
    if (typeof value === "string") {
        return value;
    }
    if (!value || typeof value !== "object") {
        return "";
    }

    const item = value as { error?: unknown; message?: unknown };
    if (typeof item.message === "string") {
        return item.message;
    }
    return errorMessageFromValue(item.error);
}

const request = axios.create({
    baseURL: webConfig.apiUrl.replace(/\/$/, ""),
});

request.interceptors.request.use(async (config) => {
    const nextConfig = {...config};
    let authKey = await getStoredAuthKey();
    if (typeof window !== "undefined") {
        const url = new URL(window.location.href);
        const sharedKey = String(url.searchParams.get("key") || url.searchParams.get("auth_key") || url.searchParams.get("token") || "").trim();
        if (sharedKey) {
            authKey = sharedKey;
        }
    }
    const headers = {...(nextConfig.headers || {})} as Record<string, string>;
    if (authKey && !headers.Authorization) {
        headers.Authorization = `Bearer ${authKey}`;
    }
    // eslint-disable-next-line @typescript-eslint/ban-ts-comment
    // @ts-expect-error
    nextConfig.headers = headers;
    return nextConfig;
});

let shareKeyLoginPromise: Promise<void> | null = null;

function isImageScopeRoute(pathname: string) {
    return pathname === "/image"
        || pathname.startsWith("/image/")
        || pathname === "/image-manager"
        || pathname.startsWith("/image-manager/");
}

export async function consumeShareKeyFromUrl() {
    if (typeof window === "undefined") {
        return;
    }
    const url = new URL(window.location.href);
    const sharedKey = String(url.searchParams.get("key") || url.searchParams.get("auth_key") || url.searchParams.get("token") || "").trim();
    if (!sharedKey) {
        return;
    }
    url.searchParams.delete("key");
    url.searchParams.delete("auth_key");
    url.searchParams.delete("token");
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
    if (shareKeyLoginPromise) {
        return shareKeyLoginPromise;
    }
    shareKeyLoginPromise = httpRequest<{
        ok: boolean;
        role: "admin" | "user";
        subject_id: string;
        name: string;
        quota?: number | null;
        auth_mode?: string;
        scope?: "full" | "image";
    }>("/auth/login", {
        method: "POST",
        body: {},
        headers: {Authorization: `Bearer ${sharedKey}`},
        redirectOnUnauthorized: false,
    })
        .then(async (data) => {
            const isLinkToken = sharedKey.startsWith("lk-");
            await setStoredAuthSession({
                key: sharedKey,
                role: data.role,
                subjectId: data.subject_id,
                name: data.name,
                quota: data.quota,
                scope: data.scope === "image" || isLinkToken ? "image" : "full",
                authMode: data.auth_mode || (isLinkToken ? "link" : "key"),
            });
            if ((data.scope === "image" || isLinkToken) && !isImageScopeRoute(window.location.pathname)) {
                window.location.replace("/image");
            }
        })
        .finally(() => {
            shareKeyLoginPromise = null;
        });
    return shareKeyLoginPromise;
}

request.interceptors.response.use(
    (response) => response,
    async (error: AxiosError<ErrorPayload>) => {
        const status = error.response?.status;
        const shouldRedirect = (error.config as RequestConfig | undefined)?.redirectOnUnauthorized !== false;
        if (status === 401 && shouldRedirect && typeof window !== "undefined") {
            // Avoid redirect loop — only redirect if not already on /login
            if (!window.location.pathname.startsWith("/login")) {
                await clearStoredAuthSession();
                window.location.replace("/login");
                // Return a never-resolving promise to prevent further error handling
                // while the browser navigates away
                return new Promise(() => {});
            }
        }

        const payload = error.response?.data;
        const message =
            errorMessageFromValue(payload?.detail) ||
            errorMessageFromValue(payload?.error) ||
            payload?.message ||
            error.message ||
            `请求失败 (${status || 500})`;
        if (/cloudflare|origin web server|invalid or incomplete response|proxy read timeout|error 52[024]/i.test(message)) {
            return Promise.reject(new Error("上游图片服务临时返回 Cloudflare 错误，可能是节点/账号或上游服务拥堵，请稍后重试；如连续出现请切换账号/节点"));
        }
        if (!status && /network error|failed to fetch|connection|abort|timeout/i.test(message)) {
            return Promise.reject(new Error("网络请求失败：可能是移动网络不稳定、服务刚重启，或一次上传图片过大，请稍后重试"));
        }
        return Promise.reject(new Error(message));
    },
);

type RequestOptions = {
    method?: string;
    body?: unknown;
    headers?: Record<string, string>;
    redirectOnUnauthorized?: boolean;
};

function filenameFromContentDisposition(value: unknown) {
    const header = String(value || "");
    if (!header) {
        return "";
    }
    const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
    if (encoded?.[1]) {
        try {
            return decodeURIComponent(encoded[1].replace(/^"|"$/g, ""));
        } catch {
            return encoded[1].replace(/^"|"$/g, "");
        }
    }
    const plain = /filename="?([^";]+)"?/i.exec(header);
    return plain?.[1] ? plain[1].trim() : "";
}

export async function httpRequest<T>(path: string, options: RequestOptions = {}) {
    const {method = "GET", body, headers, redirectOnUnauthorized = true} = options;
    const config: RequestConfig = {
        url: path,
        method,
        data: body,
        headers,
        redirectOnUnauthorized,
    };
    const response = await request.request<T>(config);
    return response.data;
}

export async function httpBlobRequest(path: string, options: RequestOptions = {}) {
    const {method = "GET", body, headers, redirectOnUnauthorized = true} = options;
    const config: RequestConfig = {
        url: path,
        method,
        data: body,
        headers,
        redirectOnUnauthorized,
        responseType: "blob",
    };
    const response = await request.request<Blob>(config);
    return {
        blob: response.data,
        filename: filenameFromContentDisposition(response.headers["content-disposition"]),
        contentType: String(response.headers["content-type"] || response.data.type || ""),
    };
}
