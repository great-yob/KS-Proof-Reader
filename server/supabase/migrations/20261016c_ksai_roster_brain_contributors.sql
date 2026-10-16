-- ══════════════════════════════════════════════════════════════════════════
-- 사용자 명단(ksai_roster) + 공유 뇌 이벤트 기여자(ksai_brain_contributors) (2026-10-16)
-- ══════════════════════════════════════════════════════════════════════════
-- 사용자 요청:
--   ① 추가할 직원 명단의 기본 인사 정보(부서·직급·이름·성별·사내번호·전화·이메일)를
--      "추후 필요할 수 있으니" DB에 보관.
--   ② 공유 뇌 **이벤트** 기여는 지정된 사람만 — 교정 전문 인력이 아닌 직원의 수락·거절은
--      합의 집계에 노이즈만 더한다. 대상 = 관리자 + 명단의 '공유 뇌 접근' 표시자 +
--      지정한 ks-works 직원.
--
-- ★ 명단과 계정(ksai_members)을 분리한 이유: 명단은 계정이 생기기 **전에도** 존재해야
--   한다(ksai_members는 auth_user_id가 PK). 둘은 로그인 이메일로 이어진다.
-- ★ 기여 권한을 명단의 열이 아니라 **별도 테이블 하나**에 둔 이유: 대상에 명단 밖의
--   ks-works 직원도 있다. 직원의 인사 정보를 명단에 복제하지 않으면서 권한의 출처를
--   한 곳으로 유지하려면 '이메일 목록' 테이블이 맞다. 관리자는 목록이 아니라 **규칙**
--   (userdict_is_curator)으로 포함한다 — 관리자가 바뀌어도 따라간다.
--
-- ⚠ 둘 다 RLS on + 정책 0 + anon/authenticated 권한 회수. service role(Edge Function)과
--   SECURITY DEFINER 함수만 읽는다. 앱은 전화번호 등을 받지 않는다.
-- ⚠ 퍼블릭 저장소: 명단·기여자 **데이터**는 이 파일이나 저장소 어디에도 두지 않는다.
--
-- 권한 의미:
--   · 이벤트 **기여**(userdict_events INSERT) = 활성 사용자 AND (관리자 OR 기여자 목록).
--   · 스냅샷 **수신**(userdict_snapshots SELECT)은 전 사용자 유지 — 큐레이터가 승인한
--     결과물이라 노이즈가 아니고, 모두가 같은 사내 용어로 교정받는 게 목적이다.
--
-- ⚠ Supabase MCP는 DROP POLICY·TRUNCATE 같은 파괴적 구문에 별도 확인을 요구하는데
--   이 환경에선 자동 거절된다(실측 3회). 그래서 정책은 DROP+CREATE가 아니라 ALTER POLICY.
-- ══════════════════════════════════════════════════════════════════════════

create table if not exists public.ksai_roster (
  login_email   text primary key check (login_email = lower(btrim(login_email))),
  name          text not null check (length(btrim(name)) > 0),
  department    text,
  position      text,
  gender        text,
  office_phone  text,          -- 사내번호(내선). '재택' 같은 비번호 값도 그대로 보관
  mobile_phone  text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
comment on table public.ksai_roster is
  'KS-AI Editor 사용자 명단(개인정보). 계정과 무관하게 존재, 로그인 이메일로 계정과 연결. '
  '직접 접근 정책 없음 — service role/SECDEF만.';
alter table public.ksai_roster enable row level security;
revoke all on public.ksai_roster from anon, authenticated;

create table if not exists public.ksai_brain_contributors (
  login_email   text primary key check (login_email = lower(btrim(login_email))),
  granted_by    uuid references auth.users(id) on delete set null,
  created_at    timestamptz not null default now()
);
comment on table public.ksai_brain_contributors is
  '공유 뇌 이벤트 기여자(로그인 이메일). 관리자는 규칙으로 포함되므로 여기 넣지 않아도 된다. '
  '직접 접근 정책 없음 — service role/SECDEF만.';
alter table public.ksai_brain_contributors enable row level security;
revoke all on public.ksai_brain_contributors from anon, authenticated;


create or replace function public.userdict_can_contribute()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select public.userdict_is_member()
     and (public.userdict_is_curator()
          or exists (
               select 1
                 from public.ksai_brain_contributors b
                 join auth.users u on lower(u.email) = b.login_email
                where u.id = auth.uid()))
$$;
revoke execute on function public.userdict_can_contribute() from public, anon;
grant execute on function public.userdict_can_contribute() to authenticated;


alter policy userdict_events_insert on public.userdict_events
  with check (user_id = auth.uid() and public.userdict_can_contribute());


-- 프로필에 brain_access 노출 — 앱이 비기여자의 이벤트를 아예 쌓지 않게
--   (정책만 막으면 앱이 매번 업로드를 시도해 거부당하고, 로컬 큐가 끝없이 자란다).
--   coalesce가 null(미등록)이면 `null || jsonb` = null이라 거부 판정이 유지된다.
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
              'active', true,
              'must_change_password', false,
              'can_change_password', false)
       from public.employees e
      where e.auth_user_id = auth.uid() and e.terminated_at is null
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
      where m.auth_user_id = auth.uid()),
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
  ) || jsonb_build_object('brain_access', public.userdict_can_contribute());
$$;
