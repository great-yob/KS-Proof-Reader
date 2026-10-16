// ksai-admin-members — KS-AI Editor 전용 회원 관리(관리자 전용)
// ─────────────────────────────────────────────────────────────────────────
// 배포: Supabase MCP deploy_edge_function (verify_jwt=true) — 이 파일이 소스 정본.
//
// actions:
//   list                                   사람 목록(명단+계정+기여자, 비밀번호·연락처 없음)
//   create   {login, name, role?, allow_former_employee?}
//                                          계정 생성 — 초기 비밀번호 ks2026, 첫 로그인 시 변경 강제
//            ★퇴사자 전환: 같은 ID의 퇴사 직원이 있으면 먼저 409 code=former_employee로
//            확인을 받고, allow_former_employee=true면 **그 auth 계정을 그대로** 회원으로
//            등록한다(auth.users 이메일은 유일 — 새 계정을 만들 수 없다). 차단 해제 +
//            비밀번호 초기화. employees의 퇴사 기록은 건드리지 않으므로 ks-works는
//            계속 로그인을 거부한다(퇴사자는 RLS상 자기 직원 행도 못 읽는다).
//   reset_password {auth_user_id}          초기 비밀번호로 되돌리고 변경 강제
//   set_disabled   {auth_user_id, disabled} 사용 중지/재개(Auth 차단 + 세션 폐기)
//   provision_roster                       명단(ksai_roster)에서 계정이 없는 사람을 일괄 생성
//                                          (퇴사자는 전환, 재직 직원은 건너뜀)
//   set_brain_access {login_email, enabled} 공유 뇌 이벤트 기여 허용/해제(ksai_brain_contributors)
//
// list는 명단·계정·기여자를 **로그인 이메일로 합친 사람 목록**을 돌려준다. 명단의
//   전화번호·성별은 **내보내지 않는다** — 관리 화면에 필요 없고, 앱(사용자 JWT)으로
//   개인정보가 흘러가는 길을 만들지 않는다. 관리자는 규칙으로 기여하므로 목록에 없다.
//
// ⚠ 이 함수는 **ksai_members만** 다룬다. ks-works 직원(employees) 계정은 생성·초기화·
//   중지 어느 것도 하지 않는다 — 직원 계정은 ks-works가 관리한다. 같은 이메일의
//   **재직** 직원이 있으면 생성을 거부한다. employees 행은 퇴사자 전환에서도 읽기만 한다.
// ⚠ 서비스 키는 여기에만 있다. 앱에 서비스 키를 넣지 말 것(퍼블릭 릴리스).

import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "jsr:@supabase/supabase-js@2";

const EMAIL_DOMAIN = "kyungsungmedia.com";
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

function normalizeLogin(raw: string): string | null {
  const t = raw.trim().toLowerCase();
  if (!t) return null;
  if (t.includes("@")) {
    return /^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/.test(t) ? t : null;
  }
  return /^[a-z0-9._-]+$/.test(t) ? `${t}@${EMAIL_DOMAIN}` : null;
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
  const me = userData?.user;
  if (userErr || !me) return json({ error: "로그인이 필요합니다." }, 401);
  // 관리자 판정은 서버 함수 하나(userdict_is_curator — 직원 admin 또는 회원 admin).
  const { data: isAdmin, error: admErr } = await caller.rpc("userdict_is_curator");
  if (admErr || isAdmin !== true) return json({ error: "관리자만 사용할 수 있습니다." }, 403);

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return json({ error: "잘못된 요청입니다." }, 400);
  }
  const action = String(body.action ?? "");

  const admin = createClient(supabaseUrl, serviceKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });

  const loadMember = async (uid: string) => {
    if (!uid) return null;
    const { data } = await admin.from("ksai_members")
      .select("auth_user_id, login_email, name, role, disabled_at")
      .eq("auth_user_id", uid).maybeSingle();
    return data;
  };

  // 퇴사자 계정을 앱 회원으로 전환(create·provision 공용).
  const convertFormer = async (uid: string, email: string, name: string, role: string) => {
    const { data: existing } = await admin.from("ksai_members")
      .select("auth_user_id").eq("auth_user_id", uid).maybeSingle();
    if (existing) return { error: "이미 등록된 로그인 ID입니다.", status: 409 };
    const { error: uErr } = await admin.auth.admin.updateUserById(uid, {
      password: INITIAL_PASSWORD, ban_duration: "none",
    });
    if (uErr) return { error: "계정 전환 실패: " + uErr.message, status: 400 };
    const { error: iErr } = await admin.from("ksai_members").insert({
      auth_user_id: uid, login_email: email, name, role,
      must_change_password: true, created_by: me.id,
    });
    if (iErr) {
      // 되돌린다 — 회원 등록이 안 됐는데 차단만 풀린 상태로 두지 않는다.
      await admin.auth.admin.updateUserById(uid, { ban_duration: "876000h" }).catch(() => {});
      return { error: "회원 등록 실패: " + iErr.message, status: 500 };
    }
    return { uid };
  };

  // 새 계정 생성(create·provision 공용).
  const createNew = async (email: string, name: string, role: string) => {
    const { data: created, error: cErr } = await admin.auth.admin.createUser({
      email, password: INITIAL_PASSWORD, email_confirm: true,
    });
    if (cErr || !created?.user) {
      const msg = cErr?.message ?? "unknown";
      const dup = /already|registered|exists/i.test(msg);
      return { error: dup ? "이미 등록된 로그인 ID입니다." : "계정 생성 실패: " + msg, status: dup ? 409 : 400 };
    }
    const { error: iErr } = await admin.from("ksai_members").insert({
      auth_user_id: created.user.id, login_email: email, name, role,
      must_change_password: true, created_by: me.id,
    });
    if (iErr) {
      await admin.auth.admin.deleteUser(created.user.id).catch(() => {});
      return { error: "회원 등록 실패: " + iErr.message, status: 500 };
    }
    return { uid: created.user.id };
  };

  // ── list ──────────────────────────────────────────────
  if (action === "list") {
    const [mem, ros, con] = await Promise.all([
      admin.from("ksai_members")
        .select("auth_user_id, login_email, name, role, must_change_password, disabled_at, created_at"),
      admin.from("ksai_roster").select("login_email, name, department, position"),
      admin.from("ksai_brain_contributors").select("login_email"),
    ]);
    const err = mem.error || ros.error || con.error;
    if (err) return json({ error: "목록 조회 실패: " + err.message }, 500);
    const contributors = new Set((con.data ?? []).map((c) => c.login_email));
    const people = new Map<string, Record<string, unknown>>();
    for (const r of ros.data ?? []) {
      people.set(r.login_email, {
        login_email: r.login_email, name: r.name, department: r.department,
        position: r.position, kind: "roster",
      });
    }
    for (const m of mem.data ?? []) {
      const p = people.get(m.login_email) ?? { login_email: m.login_email };
      people.set(m.login_email, { ...p, ...m, name: m.name || p.name, kind: "member" });
    }
    // 명단·계정에 없는 기여자 = ks-works 재직 직원(이름만 가져온다).
    const extra = [...contributors].filter((e) => !people.has(e));
    if (extra.length) {
      const { data: emps } = await admin.from("employees")
        .select("email, name, terminated_at").in("email", extra);
      for (const e of emps ?? []) {
        people.set(String(e.email).toLowerCase(), {
          login_email: String(e.email).toLowerCase(), name: e.name,
          kind: e.terminated_at ? "former_employee" : "employee",
        });
      }
      for (const e of extra) {
        if (!people.has(e)) people.set(e, { login_email: e, name: "", kind: "unknown" });
      }
    }
    const list = [...people.values()].map((p) => ({
      ...p, brain_access: contributors.has(String(p.login_email)),
    }));
    const order: Record<string, number> = { member: 0, roster: 1, employee: 2, former_employee: 3, unknown: 4 };
    list.sort((a, b) => (order[String(a.kind)] ?? 9) - (order[String(b.kind)] ?? 9)
      || String(a.name).localeCompare(String(b.name), "ko"));
    return json({ ok: true, people: list });
  }

  // ── provision_roster ──────────────────────────────────
  if (action === "provision_roster") {
    const [{ data: ros, error: rErr }, { data: mem }] = await Promise.all([
      admin.from("ksai_roster").select("login_email, name"),
      admin.from("ksai_members").select("login_email"),
    ]);
    if (rErr) return json({ error: "명단 조회 실패: " + rErr.message }, 500);
    const have = new Set((mem ?? []).map((m) => m.login_email));
    const todo = (ros ?? []).filter((r) => !have.has(r.login_email));
    const { data: emps } = todo.length
      ? await admin.from("employees").select("email, auth_user_id, terminated_at")
          .in("email", todo.map((r) => r.login_email))
      : { data: [] };
    const empBy = new Map((emps ?? []).map((e) => [String(e.email).toLowerCase(), e]));
    const out = { created: 0, converted: 0, skipped_employee: 0, failed: [] as unknown[] };
    for (const r of todo) {
      const emp = empBy.get(r.login_email);
      if (emp && !emp.terminated_at) { out.skipped_employee++; continue; }
      const res = emp && emp.auth_user_id
        ? await convertFormer(emp.auth_user_id as string, r.login_email, r.name, "member")
        : await createNew(r.login_email, r.name, "member");
      if ("uid" in res) {
        if (emp && emp.auth_user_id) out.converted++; else out.created++;
      } else {
        out.failed.push({ login_email: r.login_email, name: r.name, error: res.error });
      }
    }
    return json({ ok: true, ...out, initial_password: INITIAL_PASSWORD });
  }

  // ── set_brain_access ──────────────────────────────────
  if (action === "set_brain_access") {
    const email = normalizeLogin(String(body.login_email ?? ""));
    if (!email) return json({ error: "로그인 ID가 올바르지 않습니다." }, 400);
    if (body.enabled === true) {
      const { error } = await admin.from("ksai_brain_contributors")
        .upsert({ login_email: email, granted_by: me.id }, { onConflict: "login_email" });
      if (error) return json({ error: "허용 실패: " + error.message }, 500);
    } else {
      const { error } = await admin.from("ksai_brain_contributors").delete().eq("login_email", email);
      if (error) return json({ error: "해제 실패: " + error.message }, 500);
    }
    return json({ ok: true });
  }

  // ── create ────────────────────────────────────────────
  if (action === "create") {
    const email = normalizeLogin(String(body.login ?? ""));
    const name = String(body.name ?? "").trim();
    const role = String(body.role ?? "member") === "admin" ? "admin" : "member";
    if (!email) return json({ error: "로그인 ID 형식이 올바르지 않습니다(영문·숫자·._-)." }, 400);
    if (!name) return json({ error: "이름을 입력하세요." }, 400);

    const { data: emp } = await admin.from("employees")
      .select("id, auth_user_id, terminated_at").eq("email", email).maybeSingle();
    if (emp && !emp.terminated_at) {
      return json({ error: "이미 ks-works 직원 계정입니다 — 그 계정으로 로그인할 수 있습니다." }, 409);
    }
    if (emp && emp.auth_user_id) {
      // 퇴사자 — 같은 auth 계정을 앱 회원으로 전환한다(확인 후에만).
      if (body.allow_former_employee !== true) {
        return json({
          error: "ks-works 퇴사자 계정입니다. 앱 전용 사용자로 전환할 수 있습니다(ks-works 로그인은 계속 막힙니다).",
          code: "former_employee",
        }, 409);
      }
      const uid = emp.auth_user_id as string;
      const res = await convertFormer(uid, email, name, role);
      if (!("uid" in res)) return json({ error: res.error }, res.status);
      return json({
        ok: true, converted: true,
        member: { auth_user_id: uid, login_email: email, name, role },
        initial_password: INITIAL_PASSWORD,
      });
    }

    const res = await createNew(email, name, role);
    if (!("uid" in res)) return json({ error: res.error }, res.status);
    return json({
      ok: true,
      member: { auth_user_id: res.uid, login_email: email, name, role },
      initial_password: INITIAL_PASSWORD,
    });
  }

  // ── reset_password ────────────────────────────────────
  if (action === "reset_password") {
    const m = await loadMember(String(body.auth_user_id ?? ""));
    if (!m) return json({ error: "앱 전용 회원이 아닙니다(직원 계정은 ks-works에서 관리)." }, 404);
    const { error } = await admin.auth.admin.updateUserById(m.auth_user_id, { password: INITIAL_PASSWORD });
    if (error) return json({ error: "초기화 실패: " + error.message }, 400);
    await admin.from("ksai_members").update({ must_change_password: true })
      .eq("auth_user_id", m.auth_user_id);
    return json({ ok: true, initial_password: INITIAL_PASSWORD });
  }

  // ── set_disabled ──────────────────────────────────────
  if (action === "set_disabled") {
    const m = await loadMember(String(body.auth_user_id ?? ""));
    if (!m) return json({ error: "앱 전용 회원이 아닙니다(직원 계정은 ks-works에서 관리)." }, 404);
    const disabled = body.disabled === true;
    if (disabled && m.auth_user_id === me.id) {
      return json({ error: "본인 계정은 사용 중지할 수 없습니다." }, 400);
    }
    // Auth 차단이 먼저다 — 차단되면 refresh가 400으로 거부되고 앱 게이트가 세션을 폐기한다.
    const { error: bErr } = await admin.auth.admin.updateUserById(m.auth_user_id, {
      ban_duration: disabled ? "876000h" : "none",
    });
    if (bErr) return json({ error: "상태 변경 실패: " + bErr.message }, 400);
    await admin.from("ksai_members").update({
      disabled_at: disabled ? new Date().toISOString() : null,
    }).eq("auth_user_id", m.auth_user_id);
    return json({ ok: true });
  }

  return json({ error: "알 수 없는 작업입니다: " + action }, 400);
});
