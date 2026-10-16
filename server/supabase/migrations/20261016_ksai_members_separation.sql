-- ══════════════════════════════════════════════════════════════════════════
-- KS-AI Editor 전용 회원 — ks-works(근태앱) 직원 명부와 분리 (2026-10-16)
-- ══════════════════════════════════════════════════════════════════════════
-- 배경: 이 앱은 ks-works와 같은 Supabase 프로젝트(ogcwpfkrimzdjsjledtv)를 쓴다.
--   지금까지 앱 사용자 = employees 행이 있는 직원뿐이었고, 사용자를 추가하려면
--   ks-works 직원으로 등록(create-employee)해야 했다 — 근태 대상이 아닌 사람까지
--   ks-works 명부·휴가·DM에 들어가는 구조였다.
--
-- 결정(사용자, 2026-10-16):
--   · 별도 프로젝트 대신 이 프로젝트 안에서 분리(무료 활성 프로젝트 2개 상한).
--   · 기존 직원 계정은 **그대로**(employees로 계속 로그인 — 손대지 않는다).
--   · 새 사용자는 ksai_members에만 등록 — employees 행이 없으므로 ks-works는
--     로그인 직후 로그아웃시키고(AuthContext loadEmployee='none'), ks-works RLS는
--     전부 current_employee_id()/is_admin()을 요구해 데이터도 못 본다.
--
-- 개인정보 최소화: 로그인 ID(이메일)·이름·권한·상태만 둔다. 휴대폰·생년월일·
--   성별 등 employees의 인사 정보는 두지 않으며, 평문 비밀번호도 저장하지 않는다
--   (ks-works의 employee_credentials.password_plain 미러를 따르지 않는다).
--
-- 접근: 테이블에 직접 정책을 두지 않는다(authenticated/anon 전부 차단).
--   본인 조회 = ksai_my_profile() / 관리 = Edge Function ksai-admin-members
--   (service role) / 비밀번호 변경 = Edge Function ksai-change-password.
-- ══════════════════════════════════════════════════════════════════════════

create table if not exists public.ksai_members (
  auth_user_id         uuid primary key references auth.users(id) on delete cascade,
  login_email          text not null unique,
  name                 text not null check (length(btrim(name)) > 0),
  role                 text not null default 'member' check (role in ('member', 'admin')),
  -- 초기 비밀번호(2026)로 만든 계정은 첫 로그인 때 변경을 강제한다.
  must_change_password boolean not null default true,
  disabled_at          timestamptz,
  password_changed_at  timestamptz,
  created_by           uuid references auth.users(id) on delete set null,
  created_at           timestamptz not null default now()
);

comment on table public.ksai_members is
  'KS-AI Editor 전용 회원(ks-works 직원 아님). 직원은 employees로 로그인한다. '
  '직접 접근 정책 없음 — ksai_my_profile()/Edge Function만 사용.';

alter table public.ksai_members enable row level security;
revoke all on public.ksai_members from anon, authenticated;


-- ── 본인 프로필 — 앱 로그인 게이트가 쓰는 유일한 신원 조회 ──────────────
-- 예전 앱은 employees를 직접 SELECT했다. employees_select_auth 정책상 로그인한
-- 직원은 **전 직원의 전 열**(휴대폰·생년월일·휴직 여부…)을 읽을 수 있었고, 앱이
-- 4개 열만 골라 읽었을 뿐이다. 이제 앱은 이 함수가 돌려주는 최소 필드만 받는다.
-- 직원이 우선한다(같은 uid가 양쪽에 있으면 employees 기준).
create or replace function public.ksai_my_profile()
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    (select jsonb_build_object(
              'kind', 'employee',
              'employee_id', e.id,
              'name', e.name,
              'role', case when e.role = 'admin' then 'admin' else 'employee' end,
              'active', e.terminated_at is null,
              'must_change_password', false,
              'can_change_password', false)
       from public.employees e
      where e.auth_user_id = auth.uid()
      limit 1),
    (select jsonb_build_object(
              'kind', 'member',
              'employee_id', null,
              'name', m.name,
              'role', m.role,
              'active', m.disabled_at is null,
              'must_change_password', m.must_change_password,
              'can_change_password', true)
       from public.ksai_members m
      where m.auth_user_id = auth.uid())
  );
$$;

revoke execute on function public.ksai_my_profile() from public, anon;
grant execute on function public.ksai_my_profile() to authenticated;


-- ── 공유 용어 뇌 권한 — 앱 전용 회원도 포함 ─────────────────────────────
-- (create or replace는 기존 EXECUTE 권한을 유지한다)
create or replace function public.userdict_is_member()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
           select 1 from public.employees e
            where e.auth_user_id = auth.uid() and e.terminated_at is null)
      or exists (
           select 1 from public.ksai_members m
            where m.auth_user_id = auth.uid() and m.disabled_at is null)
$$;

create or replace function public.userdict_is_curator()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
           select 1 from public.employees e
            where e.auth_user_id = auth.uid() and e.terminated_at is null
              and e.role = 'admin')
      or exists (
           select 1 from public.ksai_members m
            where m.auth_user_id = auth.uid() and m.disabled_at is null
              and m.role = 'admin')
$$;
