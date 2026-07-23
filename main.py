"""
main.py — KS-Proof Reader v5 진입점
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PySide6 앱 실행.
"""

import sys
import traceback
from PySide6.QtWidgets import QApplication, QMessageBox, QToolTip
from PySide6.QtGui import QFont
from PySide6.QtCore import qInstallMessageHandler

# Qt 폰트 경고 필터 — Windows의 'Fixedsys' 등은 비트맵(.fon) 래스터 폰트라
# DirectWrite가 CreateFontFaceFromHDC()로 face를 만들 수 없어 startup 시
# 무해한 경고를 남긴다. 폴백 체인에서 비롯되며 렌더링에는 영향이 없으므로
# 이 특정 메시지만 걸러내고 나머지 Qt 메시지는 그대로 stderr로 통과시킨다.
def _qt_message_filter(mode, ctx, message):
    if "CreateFontFaceFromHDC() failed" in message:
        return
    print(message, file=sys.stderr, flush=True)

qInstallMessageHandler(_qt_message_filter)


# 전역 예외 핸들러 — 크래시 원인 표시
def _excepthook(exc_type, exc_value, exc_tb):
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print(msg, file=sys.stderr, flush=True)
    try:
        QMessageBox.critical(None, "오류", msg)
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)

sys.excepthook = _excepthook


def main():
    # 백그라운드 스레드 연산 시 메인(UI) 스레드의 GIL 확보 빈도를 극대화하여 프레임 드랍 방지
    sys.setswitchinterval(0.001)

    # ⚠ **명시적 AppUserModelID를 설정하지 말 것.** 커스텀 AUMID를 지정하면 Windows는
    #   실행 중 작업표시줄 아이콘을 그 AUMID와 **일치하는 바로가기**에서 찾는다. Inno가
    #   만든 바로가기엔 그 AUMID가 없으므로, 창 아이콘(setWindowIcon)이 멀쩡히 붙어 있어도
    #   무시하고 **일반 아이콘으로 폴백**한다(실측: 창 WM_GETICON은 유효한데 작업표시줄만
    #   깨져 보임). AUMID를 두지 않으면 AUMID가 EXE 경로에서 자동 파생돼 ① 창 아이콘이
    #   그대로 쓰이고 ② 실행 버튼이 고정한 바로가기 버튼과 정상 병합된다. 프리즈드 EXE는
    #   자체 프로세스라 파이썬 호스트 아래로 묶이는 문제도 없다.

    app = QApplication(sys.argv)

    # 앱 아이콘 — 실행 중 창/작업표시줄/Alt-Tab 아이콘. EXE 임베드 리소스와 별개로
    #   런타임에 명시 설정해야 한다(icons.app_icon 헤더 참조). 모든 최상위 창이 상속.
    try:
        from ui.styles.icons import app_icon
        app.setWindowIcon(app_icon())
    except Exception:
        pass

    # 번들 폰트(Pretendard) 로드 후 기본 폰트 설정
    from ui.styles.fonts import load_fonts
    family = load_fonts()
    font = QFont(family, 10)
    # Pretendard는 안티앨리어싱 전제 설계 — Windows 기본 힌팅은 얇은 상단 획(예: ㅎ)을
    # 그리드에 스냅하며 떨어뜨린다. NoHinting으로 글리프 형태를 온전히 보존.
    font.setHintingPreference(QFont.PreferNoHinting)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)
    # 툴팁은 지연 생성되는 별도 최상위 창이라 번들 글꼴 face에 명시적으로 고정한다
    #   (QSS가 font-family를 시스템 Pretendard로 재해석하는 일을 방지 — NoHinting 유지).
    QToolTip.setFont(font)

    # 저장된 테마(라이트/다크) 적용
    from core import ConfigLoader
    from ui.styles.theme import apply_theme
    try:
        mode = ConfigLoader().get_theme()
    except Exception:
        mode = "light"
    apply_theme(app, mode)

    from ui.main_window import MainWindow
    window = MainWindow()
    window.show()

    ret = app.exec()
    sys.exit(ret)


if __name__ == "__main__":
    main()
