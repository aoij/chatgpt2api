"use client";

import Link from "next/link";
import { ArrowLeft, LoaderCircle } from "lucide-react";

import { useAuthGuard } from "@/lib/use-auth-guard";

import { UserKeysCard } from "../settings/components/user-keys-card";

export default function UsersPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);

  if (isCheckingAuth || !session || session.role !== "admin") {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  return (
    <>
      <div className="flex flex-col gap-2">
        <Link href="/settings" className="inline-flex w-fit items-center gap-1 text-sm text-stone-500 transition hover:text-stone-900">
          <ArrowLeft className="size-4" />
          返回设置
        </Link>
        <div className="text-xs font-semibold tracking-[0.18em] text-stone-500 uppercase">User Management</div>
        <h1 className="text-2xl font-semibold tracking-tight">用户管理</h1>
        <p className="text-sm text-stone-500">人多时可以在这里集中搜索、筛选、分页维护普通用户密钥。</p>
      </div>
      <UserKeysCard standalone />
    </>
  );
}
