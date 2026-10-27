-- ══════════════════════════════════════════════════════════════════════════
-- userdict_events: 본인 이벤트 SELECT 허용 — 기여자 업로드가 RLS 에 막히던 문제 (2026-10-27)
-- ══════════════════════════════════════════════════════════════════════════
-- 증상: 기여자(ksai_brain_contributors)로 지정된 직원의 push 가 전부 403
--   (Postgres 42501 "new row violates row-level security policy for table userdict_events").
--   10/16 기여자 도입 이후 서버에 쌓인 이벤트는 관리자 것뿐이었다.
--
-- 원인: core/userdict_sync.push() 는 재전송 멱등성을 위해
--   `Prefer: resolution=ignore-duplicates` → INSERT … ON CONFLICT (event_id) DO NOTHING
--   으로 보낸다. ON CONFLICT 는 충돌 검사를 위해 기존 행을 읽어야 하므로 INSERT 정책에
--   더해 **SELECT 정책도 새 행을 통과시켜야** 한다. 그런데 SELECT 정책은 큐레이터 전용
--   (userdict_events_select_curator)이라 관리자만 통과했다.
--   실측: 기여자 계정으로 가장 → 평범한 INSERT 성공, ON CONFLICT 붙이면 42501,
--   아래 정책을 더하면 성공(트랜잭션 롤백 검증).
--
-- 해결: 본인이 올린 이벤트만 읽을 수 있는 SELECT 정책. 노출되는 것은 자기 결정 기록뿐이다.
--   INSERT 권한 의미(활성 사용자 AND (관리자 OR 기여자 목록))는 그대로 — 비기여자는
--   계속 userdict_events_insert 의 WITH CHECK 에서 거부된다.
-- ══════════════════════════════════════════════════════════════════════════

create policy userdict_events_select_own on public.userdict_events
  for select to authenticated
  using (user_id = (select auth.uid()));
