"use client";

import { KeyRound, LoaderCircle, Plus, PlugZap, Save, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { testProxy, type ExternalImageModelConfig, type ProxyTestResult } from "@/lib/api";

import { useSettingsStore } from "../store";

export function ConfigCard() {
  const [isTestingProxy, setIsTestingProxy] = useState(false);
  const [proxyTestResult, setProxyTestResult] = useState<ProxyTestResult | null>(null);
  const logLevelOptions = ["debug", "info", "warning", "error"];
  const config = useSettingsStore((state) => state.config);
  const isLoadingConfig = useSettingsStore((state) => state.isLoadingConfig);
  const isSavingConfig = useSettingsStore((state) => state.isSavingConfig);
  const setRefreshAccountIntervalMinute = useSettingsStore((state) => state.setRefreshAccountIntervalMinute);
  const setImageRetentionDays = useSettingsStore((state) => state.setImageRetentionDays);
  const setImagePollTimeoutSecs = useSettingsStore((state) => state.setImagePollTimeoutSecs);
  const setImageAccountConcurrency = useSettingsStore((state) => state.setImageAccountConcurrency);
  const setImageTaskWorkerCount = useSettingsStore((state) => state.setImageTaskWorkerCount);
  const setImageUpstreamConcurrency = useSettingsStore((state) => state.setImageUpstreamConcurrency);
  const setImagePerAccountConcurrency = useSettingsStore((state) => state.setImagePerAccountConcurrency);
  const setAutoRemoveInvalidAccounts = useSettingsStore((state) => state.setAutoRemoveInvalidAccounts);
  const setAutoRemoveRateLimitedAccounts = useSettingsStore((state) => state.setAutoRemoveRateLimitedAccounts);
  const setLogLevel = useSettingsStore((state) => state.setLogLevel);
  const setProxy = useSettingsStore((state) => state.setProxy);
  const setBaseUrl = useSettingsStore((state) => state.setBaseUrl);
  const setSiteName = useSettingsStore((state) => state.setSiteName);
  const setPageTitle = useSettingsStore((state) => state.setPageTitle);
  const setImagePageTitle = useSettingsStore((state) => state.setImagePageTitle);
  const setImagePageSubtitle = useSettingsStore((state) => state.setImagePageSubtitle);
  const setExternalImageModels = useSettingsStore((state) => state.setExternalImageModels);
  const saveConfig = useSettingsStore((state) => state.saveConfig);
  const externalImageModels = config?.external_image_models || [];

  const updateExternalImageModel = (index: number, updates: Partial<ExternalImageModelConfig>) => {
    setExternalImageModels(externalImageModels.map((model, currentIndex) => (
      currentIndex === index ? { ...model, ...updates } : model
    )));
  };

  const addGrokImageModel = () => {
    const id = "grok-imagine-image";
    if (externalImageModels.some((item) => item.id === id || item.model === id)) {
      toast.message("Grok Imagine 已在外部图片模型列表中");
      return;
    }
    setExternalImageModels([
      ...externalImageModels,
      {
        id,
        label: "Grok Imagine",
        model: "grok-imagine-image",
        endpoint: "https://aoij.cc.cd/grok2api/v1/images/generations",
        enabled: false,
        supports_edit: false,
        default_size: "1024x1024",
        timeout_seconds: 180,
        api_key: "",
        has_api_key: false,
      },
    ]);
  };

  const addCustomImageModel = () => {
    const id = `external-image-${Date.now()}`;
    setExternalImageModels([
      ...externalImageModels,
      {
        id,
        label: "外部图片模型",
        model: "",
        endpoint: "",
        enabled: false,
        supports_edit: false,
        default_size: "",
        timeout_seconds: 180,
        api_key: "",
        has_api_key: false,
      },
    ]);
  };

  const removeExternalImageModel = (index: number) => {
    setExternalImageModels(externalImageModels.filter((_, currentIndex) => currentIndex !== index));
  };

  const handleTestProxy = async () => {
    const candidate = String(config?.proxy || "").trim();
    if (!candidate) {
      toast.error("请先填写代理地址");
      return;
    }
    setIsTestingProxy(true);
    setProxyTestResult(null);
    try {
      const data = await testProxy(candidate);
      setProxyTestResult(data.result);
      if (data.result.ok) {
        toast.success(`代理可用（${data.result.latency_ms} ms，HTTP ${data.result.status}）`);
      } else {
        toast.error(`代理不可用：${data.result.error ?? "未知错误"}`);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "测试代理失败");
    } finally {
      setIsTestingProxy(false);
    }
  };

  if (isLoadingConfig) {
    return (
      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="flex items-center justify-center p-10">
          <LoaderCircle className="size-5 animate-spin text-stone-400" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
      <CardContent className="space-y-4 p-4 sm:p-6">
        <div className="rounded-xl border border-stone-200 bg-stone-50 px-4 py-3 text-sm leading-6 text-stone-600">
          管理员登录密钥继续从部署配置读取，不再在此页面展示；如需分发给其他人，请在下方创建普通用户密钥。
        </div>
        <div className="grid gap-4 md:grid-cols-2">

          <div className="space-y-2">
            <label className="text-sm text-stone-700">网站名称</label>
            <Input
              value={String(config?.site_name || "")}
              onChange={(event) => setSiteName(event.target.value)}
              placeholder="chatgpt2api"
              className="h-10 rounded-xl border-stone-200 bg-white"
            />
            <p className="text-xs text-stone-500">显示在左上角品牌位置。</p>
          </div>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">浏览器页面名称</label>
            <Input
              value={String(config?.page_title || "")}
              onChange={(event) => setPageTitle(event.target.value)}
              placeholder="ChatGPT 号池管理"
              className="h-10 rounded-xl border-stone-200 bg-white"
            />
            <p className="text-xs text-stone-500">保存后刷新页面即可更新浏览器标签标题。</p>
          </div>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">画图首页标题</label>
            <Input
              value={String(config?.image_page_title || "")}
              onChange={(event) => setImagePageTitle(event.target.value)}
              placeholder="Turn ideas into images"
              className="h-10 rounded-xl border-stone-200 bg-white"
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">画图首页副标题</label>
            <Input
              value={String(config?.image_page_subtitle || "")}
              onChange={(event) => setImagePageSubtitle(event.target.value)}
              placeholder="在同一窗口里保留本地历史与任务状态"
              className="h-10 rounded-xl border-stone-200 bg-white"
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">账号刷新间隔</label>
            <Input
              value={String(config?.refresh_account_interval_minute || "")}
              onChange={(event) => setRefreshAccountIntervalMinute(event.target.value)}
              placeholder="分钟"
              className="h-10 rounded-xl border-stone-200 bg-white"
            />
            <p className="text-xs text-stone-500">单位分钟，控制账号自动刷新频率。</p>
          </div>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">全局代理</label>
            <Input
              value={String(config?.proxy || "")}
              onChange={(event) => {
                setProxy(event.target.value);
                setProxyTestResult(null);
              }}
              placeholder="http://127.0.0.1:7890"
              className="h-10 rounded-xl border-stone-200 bg-white"
            />
            <p className="text-xs text-stone-500">留空表示不使用代理。</p>
            {proxyTestResult ? (
              <div
                className={`rounded-xl border px-3 py-2 text-xs leading-6 ${
                  proxyTestResult.ok
                    ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                    : "border-rose-200 bg-rose-50 text-rose-800"
                }`}
              >
                {proxyTestResult.ok
                  ? `代理可用：HTTP ${proxyTestResult.status}，用时 ${proxyTestResult.latency_ms} ms`
                  : `代理不可用：${proxyTestResult.error ?? "未知错误"}（用时 ${proxyTestResult.latency_ms} ms）`}
              </div>
            ) : null}
            <div className="flex justify-end">
              <Button
                type="button"
                variant="outline"
                className="h-9 rounded-xl border-stone-200 bg-white px-4 text-stone-700"
                onClick={() => void handleTestProxy()}
                disabled={isTestingProxy}
              >
                {isTestingProxy ? <LoaderCircle className="size-4 animate-spin" /> : <PlugZap className="size-4" />}
                测试代理
              </Button>
            </div>
          </div>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">图片访问地址</label>
            <Input
              value={String(config?.base_url || "")}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="https://example.com"
              className="h-10 rounded-xl border-stone-200 bg-white"
            />
            <p className="text-xs text-stone-500">用于生成图片结果的访问前缀地址。</p>
          </div>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">图片自动清理</label>
            <Input
              value={String(config?.image_retention_days || "")}
              onChange={(event) => setImageRetentionDays(event.target.value)}
              placeholder="30"
              className="h-10 rounded-xl border-stone-200 bg-white"
            />
            <p className="text-xs text-stone-500">自动删除多少天前的本地图片。</p>
          </div>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">图片轮询超时</label>
            <Input
              value={String(config?.image_poll_timeout_secs || "")}
              onChange={(event) => setImagePollTimeoutSecs(event.target.value)}
              placeholder="120"
              className="h-10 rounded-xl border-stone-200 bg-white"
            />
            <p className="text-xs text-stone-500">单位秒，等待上游图片结果的最长时间。</p>
          </div>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">前端单次批量张数</label>
            <Input
              value={String(config?.image_account_concurrency || "")}
              onChange={(event) => setImageAccountConcurrency(event.target.value)}
              placeholder="1"
              className="h-10 rounded-xl border-stone-200 bg-white"
            />
            <p className="text-xs text-stone-500">仅限制前端用户一次最多提交多少张图，不再限制后端实际生成并发。</p>
          </div>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">队列工作线程数</label>
            <Input
              value={String(config?.image_task_worker_count || "")}
              onChange={(event) => setImageTaskWorkerCount(event.target.value)}
              placeholder="10"
              className="h-10 rounded-xl border-stone-200 bg-white"
            />
            <p className="text-xs text-stone-500">使用本进程内存队列 + SQLite 任务表持久化；线程数可略高于真实上游并发。</p>
          </div>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">真实上游生图并发</label>
            <Input
              value={String(config?.image_upstream_concurrency || "")}
              onChange={(event) => setImageUpstreamConcurrency(event.target.value)}
              placeholder="3"
              className="h-10 rounded-xl border-stone-200 bg-white"
            />
            <p className="text-xs text-stone-500">真正同时调用 ChatGPT 生图的数量，建议 3-6；账号不稳定时用 3。</p>
          </div>
          <div className="space-y-2">
            <label className="text-sm text-stone-700">单账号生图并发</label>
            <Input
              value={String(config?.image_per_account_concurrency || "")}
              onChange={(event) => setImagePerAccountConcurrency(event.target.value)}
              placeholder="1"
              className="h-10 rounded-xl border-stone-200 bg-white"
            />
            <p className="text-xs text-stone-500">同一个 ChatGPT 账号同时生图数量，建议保持 1，可降低 429 和卡住概率。</p>
          </div>
          <label className="flex items-center gap-3 rounded-xl border border-stone-200 bg-white px-4 py-3 text-sm text-stone-700">
            <Checkbox
              checked={Boolean(config?.auto_remove_invalid_accounts)}
              onCheckedChange={(checked) => setAutoRemoveInvalidAccounts(Boolean(checked))}
            />
            自动移除异常账号
          </label>
          <label className="flex items-center gap-3 rounded-xl border border-stone-200 bg-white px-4 py-3 text-sm text-stone-700">
            <Checkbox
              checked={Boolean(config?.auto_remove_rate_limited_accounts)}
              onCheckedChange={(checked) => setAutoRemoveRateLimitedAccounts(Boolean(checked))}
            />
            自动移除限流账号
          </label>
          <div className="space-y-3 rounded-xl border border-stone-200 bg-white px-4 py-3">
            <div>
              <label className="text-sm text-stone-700">控制台日志级别</label>
              <p className="mt-1 text-xs text-stone-500">不选择时使用默认 info / warning / error。</p>
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {logLevelOptions.map((level) => (
                <label key={level} className="flex items-center gap-2 text-sm capitalize text-stone-700">
                  <Checkbox
                    checked={Boolean(config?.log_levels?.includes(level))}
                    onCheckedChange={(checked) => setLogLevel(level, Boolean(checked))}
                  />
                  {level}
                </label>
              ))}
            </div>
          </div>
          <div className="space-y-4 rounded-xl border border-stone-200 bg-white px-4 py-4 md:col-span-2">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="text-sm font-medium text-stone-800">外部图片模型</div>
                <p className="mt-1 max-w-3xl text-xs leading-5 text-stone-500">
                  支持 OpenAI 兼容的图片生成接口。Client Key 为只写字段，保存后不会在此页面回显；启用后模型会显示在用户画图页面。
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="outline"
                  className="h-9 rounded-xl border-stone-200 bg-white px-3 text-stone-700"
                  onClick={addGrokImageModel}
                >
                  <Plus className="size-4" />
                  添加 Grok
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  className="h-9 rounded-xl border-stone-200 bg-white px-3 text-stone-700"
                  onClick={addCustomImageModel}
                >
                  <Plus className="size-4" />
                  自定义
                </Button>
              </div>
            </div>

            {externalImageModels.length === 0 ? (
              <div className="rounded-xl border border-dashed border-stone-200 bg-stone-50 px-4 py-5 text-sm text-stone-500">
                尚未配置外部图片模型。可点击“添加 Grok”预填模型和接口地址。
              </div>
            ) : (
              <div className="space-y-3">
                {externalImageModels.map((model, index) => (
                  <div key={model.id || `${model.model}-${index}`} className="space-y-3 rounded-xl border border-stone-200 bg-stone-50/70 p-3 sm:p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex min-w-0 items-center gap-2 text-sm font-medium text-stone-800">
                        <KeyRound className="size-4 shrink-0 text-stone-500" />
                        <span className="truncate">{model.label || model.model || "未命名模型"}</span>
                      </div>
                      <div className="flex items-center gap-3">
                        <label className="flex items-center gap-2 text-xs text-stone-600">
                          <Checkbox
                            checked={Boolean(model.enabled)}
                            onCheckedChange={(checked) => updateExternalImageModel(index, { enabled: Boolean(checked) })}
                          />
                          启用
                        </label>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="size-8 text-stone-500 hover:bg-rose-50 hover:text-rose-700"
                          onClick={() => removeExternalImageModel(index)}
                          aria-label={`删除外部图片模型 ${model.label || model.model || index + 1}`}
                          title="删除模型"
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                    </div>
                    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                      <div className="space-y-1.5">
                        <label className="text-xs text-stone-600">显示名称</label>
                        <Input value={model.label} onChange={(event) => updateExternalImageModel(index, { label: event.target.value })} placeholder="Grok Imagine" className="h-10 rounded-xl border-stone-200 bg-white" />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-xs text-stone-600">模型名</label>
                        <Input value={model.model} onChange={(event) => updateExternalImageModel(index, { model: event.target.value })} placeholder="grok-imagine-image" className="h-10 rounded-xl border-stone-200 bg-white font-mono text-xs" />
                      </div>
                      <div className="space-y-1.5 md:col-span-2">
                        <label className="text-xs text-stone-600">图片生成 Endpoint</label>
                        <Input value={model.endpoint} onChange={(event) => updateExternalImageModel(index, { endpoint: event.target.value })} placeholder="https://example.com/v1/images/generations" className="h-10 rounded-xl border-stone-200 bg-white font-mono text-xs" />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-xs text-stone-600">固定尺寸（可选）</label>
                        <Input value={model.default_size || ""} onChange={(event) => updateExternalImageModel(index, { default_size: event.target.value })} placeholder="1024x1024" className="h-10 rounded-xl border-stone-200 bg-white font-mono text-xs" />
                      </div>
                      <div className="space-y-1.5 md:col-span-2">
                        <label className="text-xs text-stone-600">Client Key</label>
                        <Input
                          type="password"
                          value={model.api_key || ""}
                          onChange={(event) => updateExternalImageModel(index, { api_key: event.target.value, clear_api_key: false })}
                          placeholder={model.has_api_key ? "已配置，留空将保留原 Key" : "输入新版 Client Key"}
                          className="h-10 rounded-xl border-stone-200 bg-white font-mono text-xs"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-xs text-stone-600">请求超时（秒）</label>
                        <Input type="number" min="5" max="600" value={String(model.timeout_seconds || 180)} onChange={(event) => updateExternalImageModel(index, { timeout_seconds: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" />
                      </div>
                      <div className="flex items-end gap-3 pb-1">
                        <span className="text-xs text-stone-500">当前外部模型仅支持文生图</span>
                        {model.has_api_key ? (
                          <Button
                            type="button"
                            variant="link"
                            className="h-auto p-0 text-xs text-stone-500"
                            onClick={() => updateExternalImageModel(index, { api_key: "", clear_api_key: true, has_api_key: false })}
                          >
                            清除密钥
                          </Button>
                        ) : null}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="space-y-2 md:col-span-2">
            <label className="text-sm text-stone-700">全局附加指令</label>
            <Textarea
              value={String(config?.global_system_prompt || "")}
              onChange={(event) => setGlobalSystemPrompt(event.target.value)}
              placeholder="例如：先判断用户提示词是否合规；遇到违法、色情、暴力、仇恨等请求时拒绝回答。"
              className="min-h-28 rounded-xl border-stone-200 bg-white font-mono text-xs shadow-none"
            />
            <p className="text-xs text-stone-500">每次请求都会作为 system 消息注入，可用于审核用户提示词、避免违规内容、统一约束模型行为或固定角色设定。</p>
          </div>
          <div className="space-y-2 md:col-span-2">
            <label className="text-sm text-stone-700">敏感词</label>
            <Textarea
              value={(config?.sensitive_words || []).join("\n")}
              onChange={(event) => setSensitiveWordsText(event.target.value)}
              placeholder="一行一个，命中即拒绝"
              className="min-h-28 rounded-xl border-stone-200 bg-white font-mono text-xs shadow-none"
            />
            <p className="text-xs text-stone-500">只要用户请求包含任意敏感词，就直接返回拒绝。</p>
          </div>
          <div className="space-y-4 rounded-xl border border-stone-200 bg-white px-4 py-3 md:col-span-2">
            <label className="flex items-center gap-3 text-sm text-stone-700">
              <Checkbox
                checked={Boolean(config?.ai_review?.enabled)}
                onCheckedChange={(checked) => setAIReviewField("enabled", Boolean(checked))}
              />
              启用 AI 审核
            </label>
            <p className="text-xs leading-6 text-stone-500">
              开启后会在请求进入生图账号前先调用审核模型，审核不通过会直接拒绝，减少违规提示词触达账号造成风控或封号的风险。
            </p>
            <div className="grid gap-4 md:grid-cols-3">
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Base URL</label>
                <Input value={String(config?.ai_review?.base_url || "")} onChange={(event) => setAIReviewField("base_url", event.target.value)} placeholder="https://api.openai.com" className="h-10 rounded-xl border-stone-200 bg-white" />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">API Key</label>
                <Input value={String(config?.ai_review?.api_key || "")} onChange={(event) => setAIReviewField("api_key", event.target.value)} placeholder="sk-..." className="h-10 rounded-xl border-stone-200 bg-white" />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Model</label>
                <Input value={String(config?.ai_review?.model || "")} onChange={(event) => setAIReviewField("model", event.target.value)} placeholder="gpt-5.4-mini" className="h-10 rounded-xl border-stone-200 bg-white" />
              </div>
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">审核提示词</label>
              <Textarea value={String(config?.ai_review?.prompt || "")} onChange={(event) => setAIReviewField("prompt", event.target.value)} placeholder="判断用户请求是否允许。只回答 ALLOW 或 REJECT。" className="min-h-24 rounded-xl border-stone-200 bg-white text-xs shadow-none" />
            </div>
          </div>
        </div>

        <div className="flex justify-end">
          <Button
            className="h-10 rounded-xl bg-stone-950 px-5 text-white hover:bg-stone-800"
            onClick={() => void saveConfig()}
            disabled={isSavingConfig}
          >
            {isSavingConfig ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
            保存
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
