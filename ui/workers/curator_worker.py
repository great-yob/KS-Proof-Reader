"""
ui/workers/curator_worker.py — 큐레이터 패널 비동기 작업 워커 (DO-5)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Supabase 큐레이터 연산(core.userdict_sync)을 UI 스레드 밖에서 실행. 모든 결과는
시그널로 전달한다. 어떤 실패도 예외로 전파하지 않는다(failed 시그널로만 보고).

ops:
  · "is_curator" — 로그인 사용자가 큐레이터인지(헤더 진입 버튼 노출 결정).
  · "load"       — (선택) 집계 후 후보 목록 + 페어 가드 결과를 함께 반환.
  · "set"        — 후보 상태 전이(cand_id, status).
  · "snapshot"   — 승인 후보로 스냅샷 배포.
"""

from PySide6.QtCore import QThread, Signal


class CuratorWorker(QThread):
    done   = Signal(str, object)   # (op, result)
    failed = Signal(str, str)      # (op, message)

    def __init__(self, op: str, parent=None, *, aggregate: bool = False,
                 cand_id: str = "", status: str = ""):
        super().__init__(parent)
        self._op = op
        self._aggregate = aggregate
        self._cand_id = cand_id
        self._status = status

    def run(self):
        try:
            from core import userdict_sync as us
            op = self._op
            if op == "is_curator":
                self.done.emit(op, us.is_curator())
            elif op == "load":
                agg = us.aggregate() if self._aggregate else {}
                cands = us.list_candidates(
                    statuses=["pending", "context_dependent", "active", "rejected"])
                self._attach_guard(cands)
                self.done.emit(op, {"aggregate": agg, "candidates": cands})
            elif op == "set":
                ok = us.set_candidate_status(self._cand_id, self._status)
                if ok:
                    self.done.emit(op, {"cand_id": self._cand_id, "status": self._status})
                else:
                    self.failed.emit(op, "상태 변경 실패(권한·네트워크 확인)")
            elif op == "snapshot":
                res = us.build_snapshot()
                if res:
                    self.done.emit(op, res)
                else:
                    self.failed.emit(op, "스냅샷 배포 실패")
            else:
                self.failed.emit(op, f"알 수 없는 작업: {op}")
        except Exception as e:
            self.failed.emit(self._op, str(e))

    @staticmethod
    def _attach_guard(cands: list):
        """pair 후보에 동형이의어/등재 가드 결과를 부착(stdict 1회 연결, 빌드타임과 동일 기준)."""
        try:
            import build_userdict_db as b
            pairs = [(c.get("original"), c.get("corrected")) for c in cands
                     if c.get("kind") == "pair" and c.get("original") and c.get("corrected")]
            if not pairs:
                return
            guard = b.guard_check_many(pairs)
            for c in cands:
                if c.get("kind") == "pair":
                    ok, reason = guard.get((c.get("original"), c.get("corrected")), (True, ""))
                    c["_guard_ok"] = ok
                    c["_guard_reason"] = reason
        except Exception:
            pass   # 가드 미부착이어도 패널은 동작(승인 시 빌드타임 가드가 최종 방어선)
