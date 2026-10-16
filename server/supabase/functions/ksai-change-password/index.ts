// ksai-change-password — KS-AI Editor 전용 회원의 본인 비밀번호 변경
// ─────────────────────────────────────────────────────────────────────────
// 배포: Supabase MCP deploy_edge_function (verify_jwt=true) — 이 파일이 소스 정본.
//
// ⚠ ks-works **재직** 직원 계정은 **여기서 바꾸지 않는다**(403). 같은 auth.users를 쓰므로
//   여기서 바꾸면 ks-works 비밀번호도 바뀌고, ks-works 관리자 화면이 보여 주는
//   employee_credentials 미러와 어긋난다. 직원은 ks-works 마이페이지에서 바꾼다.
//
// 흐름: 호출자 JWT → uid/email · 회원 확인 · 현재 비밀번호 재확인(password grant)
//   · admin API로 변경 · must_change_password 해제. 강제 변경 플래그는 **실제로
//   비밀번호를 바꾼 이 경로에서만** 내려간다(클라이언트가 RPC로 플래그만 지우는
//   우회로를 두지 않는다).

import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "jsr:@supabase/supabase-js@2";

const MIN_LENGTH = 6;
const INITIAL_PASSWORD = "ks2026";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    headers: { ...cors, "Content-Type": "application/json" },
    status,
  });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "POST") return json({ error: "method not allowed" }, 405);

  const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
  const anonKey = Deno.env.get("SUPABASE_ANON_KEY")!;
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

  const authHeader = req.headers.get("Authorization");
  if (!authHeader) return json({ error: "로그인이 필요합니다." }, 401);

  const caller = createClient(supabaseUrl, anonKey, {
    global: { headers: { Authorization: authHeader } },
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { data: userData, error: userErr } = await caller.auth.getUser();
  const user = userData?.user;
  if (userErr || !user || !user.email) return json({ error: "로그인이 필요합니다." }, 401);

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return json({ error: "잘못된 요청입니다." }, 400);
  }
  const current = String(body.current_password ?? "");
  const next = String(body.new_password ?? "");

  const admin = createClient(supabaseUrl, serviceKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });

  // 재직 직원만 거부한다 — 퇴사자를 앱 회원으로 전환한 계정은 ks-works에 들어갈 수
  //   없으니(퇴사) 비밀번호 관리 주체가 이 앱이다.
  const { data: emp } = await admin
    .from("employees").select("id").eq("auth_user_id", user.id)
    .is("terminated_at", null).maybeSingle();
  if (emp) {
    return json({
      error: "ks-works 계정의 비밀번호는 ks-works 마이페이지에서 변경하세요.",
      code: "employee_account",
    }, 403);
  }
  const { data: mem, error: memErr } = await admin
    .from("ksai_members").select("disabled_at").eq("auth_user_id", user.id).maybeSingle();
  if (memErr) return json({ error: "계정 확인 실패: " + memErr.message }, 500);
  if (!mem || mem.disabled_at) return json({ error: "사용할 수 없는 계정입니다." }, 403);

  if (!current) return json({ error: "현재 비밀번호를 입력하세요." }, 400);
  if (next.length < MIN_LENGTH) {
    return json({ error: `새 비밀번호는 ${MIN_LENGTH}자 이상이어야 합니다.` }, 400);
  }
  if (next === current) return json({ error: "현재 비밀번호와 다른 비밀번호를 입력하세요." }, 400);
  if (next === INITIAL_PASSWORD) return json({ error: "초기 비밀번호는 사용할 수 없습니다." }, 400);

  // 현재 비밀번호 재확인 — 탈취된 세션만으로 비밀번호를 바꾸지 못하게.
  const verifier = createClient(supabaseUrl, anonKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { error: verifyErr } = await verifier.auth.signInWithPassword({
    email: user.email, password: current,
  });
  if (verifyErr) return json({ error: "현재 비밀번호가 올바르지 않습니다." }, 400);
  // 확인용으로 생긴 세션만 정리한다(scope=local — 'global'이면 앱 세션까지 끊긴다).
  await verifier.auth.signOut({ scope: "local" }).catch(() => {});

  const { error: updErr } = await admin.auth.admin.updateUserById(user.id, { password: next });
  if (updErr) return json({ error: "변경 실패: " + updErr.message }, 400);

  const { error: flagErr } = await admin.from("ksai_members").update({
    must_change_password: false,
    password_changed_at: new Date().toISOString(),
  }).eq("auth_user_id", user.id);
  if (flagErr) {
    // 비밀번호는 이미 바뀌었다 — 실패로 보고하면 사용자가 옛 비밀번호로 재시도한다.
    return json({ ok: true, warning: "flag_update_failed" });
  }
  return json({ ok: true });
});
