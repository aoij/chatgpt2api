"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, Copy, LoaderCircle, LockKeyhole, ShoppingCart } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  createRechargeOrder,
  fetchRechargeOptions,
  fetchRechargeOrder,
  login,
  loginWithPassword,
  refreshRechargeOrder,
  type RechargeOption,
  type RechargeOrder,
  type RechargePayType,
  type RechargePayTypeOption,
} from "@/lib/api";
import { useRedirectIfAuthenticated } from "@/lib/use-auth-guard";
import { cn } from "@/lib/utils";
import { getDefaultRouteForSession, setStoredAuthSession, type StoredAuthSession } from "@/store/auth";

type LoginMode = "password" | "key";

const FALLBACK_AMOUNTS: RechargeOption[] = [
  { amount: 1, money: "1.00", quota: 30 },
  { amount: 5, money: "5.00", quota: 150 },
  { amount: 10, money: "10.00", quota: 300 },
];

const FALLBACK_PAY_TYPES: RechargePayTypeOption[] = [
  { type: "wxpay", label: "微信" },
  { type: "alipay", label: "支付宝" },
];

const FALLBACK_NOTICE = [
  "充值金额只支持 1 元、5 元、10 元，分别对应 30 / 150 / 300 张图片额度。",
  "订单请在 5 分钟内完成支付，超时后需要重新下单。",
  "支付成功后系统每 1 分钟自动检查一次订单，可能会有短暂延迟，请支付后回到本页耐心等待。",
  "如果已完成支付，可点击「我已付款，立即检查」主动查询到账状态。",
  "系统确认到账后会自动创建令牌，并回显一键登录画图链接。",
  "请正确填写令牌名称，后续画图页面会显示该名称。",
  "图片仅保存 10 天，请及时下载；超过 10 天系统会自动删除。",
  "有疑问可以加 QQ 909256107 联系；需要大量额度或者 API 对接也可以联系。",
];

function copyText(value: string) {
  if (!value) {
    return;
  }
  if (navigator.clipboard?.writeText) {
    void navigator.clipboard.writeText(value).then(
      () => toast.success("已复制"),
      () => toast.error("复制失败，请手动复制"),
    );
    return;
  }
  toast.message(value);
}

const RECHARGE_PAY_URL_STORAGE_PREFIX = "chatgpt2api:recharge_pay_url:";

function rechargePayUrlStorageKey(outTradeNo: string) {
  return `${RECHARGE_PAY_URL_STORAGE_PREFIX}${outTradeNo}`;
}

function storeRechargePayUrl(outTradeNo: string, value: string) {
  if (typeof window === "undefined" || !outTradeNo || !value) {
    return;
  }
  try {
    window.sessionStorage.setItem(rechargePayUrlStorageKey(outTradeNo), value);
  } catch {
    // ignore storage failures in privacy mode
  }
}

function readStoredRechargePayUrl(outTradeNo: string) {
  if (typeof window === "undefined" || !outTradeNo) {
    return "";
  }
  try {
    return window.sessionStorage.getItem(rechargePayUrlStorageKey(outTradeNo)) || "";
  } catch {
    return "";
  }
}

export default function LoginPage() {
  const router = useRouter();
  const [loginMode, setLoginMode] = useState<LoginMode>("password");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [authKey, setAuthKey] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [rechargeOpen, setRechargeOpen] = useState(false);
  const [rechargeEnabled, setRechargeEnabled] = useState(true);
  const [amounts, setAmounts] = useState<RechargeOption[]>(FALLBACK_AMOUNTS);
  const [payTypes, setPayTypes] = useState<RechargePayTypeOption[]>(FALLBACK_PAY_TYPES);
  const [notice, setNotice] = useState<string[]>(FALLBACK_NOTICE);
  const [orderExpireMinutes, setOrderExpireMinutes] = useState(5);
  const [autoCheckIntervalSeconds, setAutoCheckIntervalSeconds] = useState(60);
  const [selectedAmount, setSelectedAmount] = useState(1);
  const [selectedPayType, setSelectedPayType] = useState<RechargePayType>("wxpay");
  const [tokenName, setTokenName] = useState("");
  const [rechargeOrder, setRechargeOrder] = useState<RechargeOrder | null>(null);
  const [payUrl, setPayUrl] = useState("");
  const [isCreatingOrder, setIsCreatingOrder] = useState(false);
  const [isRefreshingOrder, setIsRefreshingOrder] = useState(false);
  const [isPollingOrder, setIsPollingOrder] = useState(false);
  const [rechargeError, setRechargeError] = useState("");
  const pollingRef = useRef<ReturnType<typeof window.setInterval> | null>(null);
  const { isCheckingAuth } = useRedirectIfAuthenticated();

  const selectedPlan = useMemo(
    () => amounts.find((item) => item.amount === selectedAmount) || amounts[0] || FALLBACK_AMOUNTS[0],
    [amounts, selectedAmount],
  );

  const stopPolling = useCallback(() => {
    if (pollingRef.current) {
      window.clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
    setIsPollingOrder(false);
  }, []);

  const startPolling = useCallback(
    (outTradeNo: string) => {
      stopPolling();
      setIsPollingOrder(true);

      const tick = async () => {
        try {
          const order = await fetchRechargeOrder(outTradeNo);
          setRechargeOrder(order);
          if (order.status === "issued" && order.login_url) {
            stopPolling();
            toast.success("支付成功，令牌已创建");
          } else if (order.status === "expired") {
            stopPolling();
            toast.error("订单已超时，请重新下单");
          }
        } catch (error) {
          setRechargeError(error instanceof Error ? error.message : "查询订单失败");
        }
      };

      void tick();
      const intervalMs = Math.max(10, autoCheckIntervalSeconds) * 1000;
      pollingRef.current = window.setInterval(() => void tick(), intervalMs);
    },
    [autoCheckIntervalSeconds, stopPolling],
  );

  useEffect(() => {
    void fetchRechargeOptions()
      .then((data) => {
        setRechargeEnabled(Boolean(data.enabled));
        if (Array.isArray(data.amounts) && data.amounts.length > 0) {
          setAmounts(data.amounts);
          setSelectedAmount((current) => data.amounts.some((item) => item.amount === current) ? current : data.amounts[0].amount);
        }
        if (Array.isArray(data.pay_types) && data.pay_types.length > 0) {
          setPayTypes(data.pay_types);
          setSelectedPayType((current) => data.pay_types.some((item) => item.type === current) ? current : data.pay_types[0].type);
        }
        if (Array.isArray(data.notice) && data.notice.length > 0) {
          setNotice(data.notice);
        }
        if (typeof data.order_expire_minutes === "number" && data.order_expire_minutes > 0) {
          setOrderExpireMinutes(data.order_expire_minutes);
        }
        if (typeof data.auto_check_interval_seconds === "number" && data.auto_check_interval_seconds > 0) {
          setAutoCheckIntervalSeconds(data.auto_check_interval_seconds);
        }
      })
      .catch(() => {
        setRechargeEnabled(false);
      });
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const outTradeNo = String(new URL(window.location.href).searchParams.get("recharge_order") || "").trim();
    if (!outTradeNo) {
      return;
    }
    setRechargeOpen(true);
    setPayUrl(readStoredRechargePayUrl(outTradeNo));
    startPolling(outTradeNo);
  }, [startPolling]);

  useEffect(() => () => stopPolling(), [stopPolling]);

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

  const handleCreateRechargeOrder = async () => {
    const normalizedTokenName = tokenName.trim();
    if (!normalizedTokenName) {
      toast.error("请输入令牌名称");
      return;
    }
    if (!rechargeEnabled) {
      toast.error("充值暂未配置，请联系管理员");
      return;
    }

    setIsCreatingOrder(true);
    setRechargeError("");
    setRechargeOrder(null);
    setPayUrl("");
    try {
      const order = await createRechargeOrder({
        amount: selectedPlan.amount,
        pay_type: selectedPayType,
        token_name: normalizedTokenName,
      });
      setRechargeOrder(order);
      setPayUrl(order.pay_url);
      storeRechargePayUrl(order.out_trade_no, order.pay_url);
      startPolling(order.out_trade_no);
      toast.success("订单已创建，请点击“打开支付页面”继续支付");
    } catch (error) {
      const message = error instanceof Error ? error.message : "创建支付订单失败";
      setRechargeError(message);
      toast.error(message);
    } finally {
      setIsCreatingOrder(false);
    }
  };

  const openRechargePayUrl = () => {
    if (!payUrl) {
      toast.error("支付链接不存在，请重新创建订单");
      return;
    }
    if (rechargeOrder?.out_trade_no) {
      storeRechargePayUrl(rechargeOrder.out_trade_no, payUrl);
    }
    window.location.href = payUrl;
  };

  const handleRefreshRechargeOrder = async () => {
    const outTradeNo = String(rechargeOrder?.out_trade_no || "").trim();
    if (!outTradeNo) {
      toast.error("请先创建支付订单");
      return;
    }

    setIsRefreshingOrder(true);
    setRechargeError("");
    try {
      const order = await refreshRechargeOrder(outTradeNo);
      setRechargeOrder(order);
      if (order.status === "issued" && order.login_url) {
        stopPolling();
        toast.success("支付成功，令牌已创建");
      } else if (order.status === "expired") {
        stopPolling();
        toast.error("订单已超时，请重新下单");
      } else {
        toast.message("暂未查询到到账结果，请稍后再试或等待系统自动确认");
        if (!pollingRef.current) {
          startPolling(outTradeNo);
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "查询支付状态失败";
      setRechargeError(message);
      toast.error(message);
    } finally {
      setIsRefreshingOrder(false);
    }
  };

  if (isCheckingAuth) {
    return (
      <div className="grid min-h-[calc(100vh-1rem)] w-full place-items-center px-4 py-6">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  const issuedLoginUrl = rechargeOrder?.status === "issued" ? String(rechargeOrder.login_url || "") : "";

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
            <Button
              variant="outline"
              className="h-13 w-full rounded-2xl border-red-200 bg-red-50 text-red-600 hover:bg-red-100 hover:text-red-700"
              onClick={() => setRechargeOpen(true)}
            >
              <ShoppingCart className="size-4" />
              充值购买画图令牌
            </Button>
          </div>
        </CardContent>
      </Card>

      <Dialog open={rechargeOpen} onOpenChange={setRechargeOpen}>
        <DialogContent className="w-[min(calc(100vw-1rem),620px)] rounded-[28px] p-4 sm:p-6">
          <DialogHeader className="gap-2 pr-8">
            <DialogTitle>充值购买画图令牌</DialogTitle>
            <DialogDescription className="leading-6">
              支持微信、支付宝支付。订单 {orderExpireMinutes} 分钟内有效；支付后可点“我已付款，立即检查”，也会每 {Math.max(1, Math.round(autoCheckIntervalSeconds / 60))} 分钟自动检查一次。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-5">
            <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-900">
              <div className="mb-1 font-semibold">注意事项</div>
              <ul className="list-disc space-y-1 pl-5">
                {notice.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>

            <div className="space-y-2">
              <label htmlFor="recharge-token-name" className="block text-sm font-medium text-stone-700">
                令牌名称
              </label>
              <Input
                id="recharge-token-name"
                value={tokenName}
                onChange={(event) => setTokenName(event.target.value)}
                placeholder="例如：test、张三画图号"
                className="h-12 rounded-2xl border-stone-200 bg-white px-4"
              />
              <p className="text-xs text-stone-500">支付成功后画图页面会显示这个令牌名称。</p>
            </div>

            <div className="space-y-2">
              <div className="text-sm font-medium text-stone-700">选择金额</div>
              <div className="grid grid-cols-3 gap-2">
                {amounts.map((item) => (
                  <button
                    key={item.amount}
                    type="button"
                    className={cn(
                      "rounded-2xl border p-3 text-left transition",
                      selectedAmount === item.amount
                        ? "border-red-300 bg-red-50 text-red-700 shadow-sm"
                        : "border-stone-200 bg-white text-stone-700 hover:border-stone-300",
                    )}
                    onClick={() => setSelectedAmount(item.amount)}
                  >
                    <div className="text-lg font-bold">￥{item.amount}</div>
                    <div className="text-xs">额度 {item.quota} 张</div>
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <div className="text-sm font-medium text-stone-700">支付方式</div>
              <div className="grid grid-cols-2 gap-2">
                {payTypes.map((item) => (
                  <button
                    key={item.type}
                    type="button"
                    className={cn(
                      "h-11 rounded-2xl border text-sm font-medium transition",
                      selectedPayType === item.type
                        ? "border-stone-950 bg-stone-950 text-white"
                        : "border-stone-200 bg-white text-stone-700 hover:border-stone-300",
                    )}
                    onClick={() => setSelectedPayType(item.type)}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>

            {!rechargeEnabled ? (
              <div className="rounded-2xl border border-red-200 bg-red-50 p-3 text-sm text-red-600">
                充值支付暂未配置，请联系 QQ 909256107。
              </div>
            ) : null}

            {rechargeError ? (
              <div className="rounded-2xl border border-red-200 bg-red-50 p-3 text-sm text-red-600">
                {rechargeError}
              </div>
            ) : null}

            {rechargeOrder ? (
              <div className="rounded-2xl border border-stone-200 bg-stone-50 p-4 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="font-semibold text-stone-900">订单 {rechargeOrder.out_trade_no}</div>
                  <div className="rounded-full bg-white px-3 py-1 text-xs text-stone-600">
                    {rechargeOrder.status === "issued"
                      ? "已到账"
                      : rechargeOrder.status === "expired"
                        ? "已超时"
                        : isPollingOrder
                          ? "系统自动检查中"
                          : "待支付"}
                  </div>
                </div>
                <div className="mt-3 grid gap-2 text-stone-600 sm:grid-cols-3">
                  <div>金额：￥{rechargeOrder.amount}</div>
                  <div>额度：{rechargeOrder.quota} 张</div>
                  <div>方式：{rechargeOrder.pay_type_label}</div>
                </div>
                {rechargeOrder.status !== "issued" ? (
                  <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800">
                    请在 {orderExpireMinutes} 分钟内完成支付。为避免部分内置浏览器弹出空白页，订单创建后不会自动打开新窗口；请点击下方“打开支付页面”。支付成功后不需要重复下单，回到本页点击“我已付款，立即检查”可马上查询；如果支付平台回调有延迟，也会继续自动检查。
                  </div>
                ) : null}

                {payUrl && rechargeOrder.status !== "issued" && rechargeOrder.status !== "expired" ? (
                  <div className="mt-3 grid gap-2 sm:grid-cols-3">
                    <Button
                      className="h-10 w-full rounded-xl bg-stone-950 text-white hover:bg-stone-800"
                      onClick={openRechargePayUrl}
                    >
                      打开支付页面
                    </Button>
                    <Button
                      variant="outline"
                      className="h-10 w-full rounded-xl border-stone-200 bg-white"
                      onClick={() => copyText(payUrl)}
                    >
                      <Copy className="size-4" />
                      复制支付链接
                    </Button>
                    <Button
                      className="h-10 w-full rounded-xl bg-red-600 text-white hover:bg-red-700"
                      onClick={() => void handleRefreshRechargeOrder()}
                      disabled={isRefreshingOrder}
                    >
                      {isRefreshingOrder ? <LoaderCircle className="size-4 animate-spin" /> : null}
                      我已付款，立即检查
                    </Button>
                  </div>
                ) : null}

                {issuedLoginUrl ? (
                  <div className="mt-4 rounded-2xl border border-red-200 bg-white p-3">
                    <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-red-600">
                      <CheckCircle2 className="size-4" />
                      令牌已创建，请保存下面的一键登录链接
                    </div>
                    <div className="break-all rounded-xl bg-red-50 p-3 text-sm font-semibold text-red-600">
                      {issuedLoginUrl}
                    </div>
                    <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                      <Button
                        className="h-10 flex-1 rounded-xl bg-red-600 text-white hover:bg-red-700"
                        onClick={() => copyText(issuedLoginUrl)}
                      >
                        <Copy className="size-4" />
                        复制链接
                      </Button>
                      <Button
                        variant="outline"
                        className="h-10 flex-1 rounded-xl border-red-200 text-red-600 hover:bg-red-50 hover:text-red-700"
                        onClick={() => {
                          window.location.href = issuedLoginUrl;
                        }}
                      >
                        立即进入画图
                      </Button>
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>

          <DialogFooter className="gap-2">
            <Button
              variant="outline"
              className="h-11 rounded-xl border-stone-200 bg-white"
              onClick={() => setRechargeOpen(false)}
            >
              关闭
            </Button>
            <Button
              className="h-11 rounded-xl bg-red-600 text-white hover:bg-red-700"
              onClick={() => void handleCreateRechargeOrder()}
              disabled={isCreatingOrder || !rechargeEnabled}
            >
              {isCreatingOrder ? <LoaderCircle className="size-4 animate-spin" /> : null}
              创建{selectedPayType === "wxpay" ? "微信" : "支付宝"}支付订单 ￥{selectedPlan.amount} / {selectedPlan.quota} 张
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
