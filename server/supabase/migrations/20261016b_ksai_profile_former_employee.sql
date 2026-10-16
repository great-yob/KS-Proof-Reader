-- ══════════════════════════════════════════════════════════════════════════
-- 퇴사자를 앱 전용 회원으로 전환할 수 있게 — 신원 판정 순서 변경 (2026-10-16)
-- ══════════════════════════════════════════════════════════════════════════
-- 계기: 퇴사자(employees.terminated_at 있음)를 같은 로그인 ID로 앱에서만 쓰게 하려는 요청.
--   auth.users의 이메일은 유일하므로 새 계정을 만들 수 없다 → 기존 auth 사용자를
--   ksai_members에 등록(Edge Function ksai-admin-members `create` + allow_former_employee).
--
-- 판정 순서: ① 재직 직원 → ② 앱 회원 → ③ 퇴사 직원(active=false → 거부).
--   예전엔 직원 행이 무조건 이겨서, 퇴사자 행이 회원 행을 가려 로그인이 거부됐다.
--
-- ks-works 차단은 그대로 유지된다: ks-works는 employees 행으로 로그인을 판정하는데
--   퇴사자는 RLS상 자기 행도 못 읽어(current_employee_id()가 terminated_at is null만 반환)
--   AuthContext가 'none' → signOut 한다. ks-works RLS도 전부 current_employee_id()를 요구.
-- ⚠ ks-works에서 그 직원을 '재입사' 처리하면 ①이 되어 다시 직원으로 판정되고,
--   다시 '퇴사' 처리하면 admin_set_employee_status가 auth 차단(banned_until)을 걸어
--   이 앱 로그인도 막힌다 — 이 경우 사용자 관리에서 '사용 재개'로 차단을 풀 수 있다.

create or replace function public.ksai_my_profile()
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    -- ① 재직 직원
    (select jsonb_build_object(
              'kind', 'employee',
              'employee_id', e.id,
              'name', e.name,
              'role', case when e.role = 'admin' then 'admin' else 'employee' end,
              'active', true,
              'must_change_password', false,
              'can_change_password', false)
       from public.employees e
      where e.auth_user_id = auth.uid() and e.terminated_at is null
      limit 1),
    -- ② 앱 전용 회원(퇴사자를 전환한 경우 포함)
    (select jsonb_build_object(
              'kind', 'member',
              'employee_id', null,
              'name', m.name,
              'role', m.role,
              'active', m.disabled_at is null,
              'must_change_password', m.must_change_password,
              'can_change_password', true)
       from public.ksai_members m
      where m.auth_user_id = auth.uid()),
    -- ③ 퇴사 직원(회원 아님) — 거부
    (select jsonb_build_object(
              'kind', 'employee',
              'employee_id', e.id,
              'name', e.name,
              'role', 'employee',
              'active', false,
              'must_change_password', false,
              'can_change_password', false)
       from public.employees e
      where e.auth_user_id = auth.uid()
      limit 1)
  );
$$;
