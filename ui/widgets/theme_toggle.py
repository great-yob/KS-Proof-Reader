import math
from PySide6.QtCore import Qt, QVariantAnimation, QEasingCurve, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath
from PySide6.QtWidgets import QAbstractButton

from ui.styles.theme import current_palette, current_mode


class ThemeToggleButton(QAbstractButton):
    """
    좌우로 슬라이딩하는 토글 스위치 형태의 다크/라이트 모드 버튼.
    내부의 Thumb(손잡이) 위치가 이동하며, Thumb 내부에서 
    해/달 SVG 모핑 애니메이션이 동시 진행됩니다.
    """
    
    toggled_mode = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # 가로 60, 세로 32의 토글 스위치 크기
        self.setFixedSize(60, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("다크/라이트 전환")
        
        self._is_dark = (current_mode() == "dark")
        self._progress = 1.0 if self._is_dark else 0.0
        
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(800)  # 좀 더 천천히 (500 -> 800)
        self._anim.setEasingCurve(QEasingCurve.InOutQuart)  # 조금 더 부드럽고 고급스러운 곡선
        self._anim.valueChanged.connect(self._on_anim_step)
        
        self.clicked.connect(self._on_click)

    def _on_click(self):
        self._is_dark = not self._is_dark
        self._anim.setStartValue(self._progress)
        self._anim.setEndValue(1.0 if self._is_dark else 0.0)
        self._anim.start()
        self.toggled_mode.emit("dark" if self._is_dark else "light")

    def _on_anim_step(self, val):
        self._progress = val
        self.update()

    def set_mode(self, mode: str):
        target_dark = (mode == "dark")
        if self._is_dark != target_dark:
            self._is_dark = target_dark
            self._progress = 1.0 if target_dark else 0.0
            self.update()

    def refresh_theme(self):
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        
        pal = current_palette()
        
        # 1. 트랙(배경) 그리기
        # 트랙 색상: 토글 상태 및 Hover 상태에 따라 보간/변경
        track_light = QColor(pal["border_strong"] if self.underMouse() else pal["border"])
        track_dark = QColor(pal["accent_hover"] if self.underMouse() else pal["accent"])
        
        # progress(0~1)에 따라 트랙 색상 부드럽게 믹스
        r = int(track_light.red() * (1 - self._progress) + track_dark.red() * self._progress)
        g = int(track_light.green() * (1 - self._progress) + track_dark.green() * self._progress)
        b = int(track_light.blue() * (1 - self._progress) + track_dark.blue() * self._progress)
        track_color = QColor(r, g, b)
        
        p.setPen(Qt.NoPen)
        p.setBrush(track_color)
        p.drawRoundedRect(self.rect(), 16, 16)
        
        # 2. Thumb(손잡이) 위치 계산
        # 썸 크기: 26x26 (상하좌우 3px 여백)
        # 좌측 시작 cx = 3 + 13 = 16
        # 우측 끝 cx = 60 - 3 - 13 = 44
        # 이동 거리 = 28
        thumb_r = 13.0
        cx = 16.0 + (28.0 * self._progress)
        cy = 16.0

        # Thumb 본체 그리기 (항상 하얀색/밝은 표면 유지)
        thumb_bg = QColor("#FFFFFF") if self._progress < 0.5 else QColor("#2A2F38")
        
        # Thumb 그림자 (약간의 입체감)
        p.setBrush(QColor(0, 0, 0, 30))
        p.drawEllipse(cx - thumb_r, cy - thumb_r + 1, thumb_r * 2, thumb_r * 2)
        
        # Thumb 배경
        p.setBrush(thumb_bg)
        p.drawEllipse(cx - thumb_r, cy - thumb_r, thumb_r * 2, thumb_r * 2)

        # 3. Thumb 내부에 아이콘 그리기 (중심점 이동)
        p.translate(cx, cy)
        
        # 아이콘 스케일 (Thumb 지름 26px 안에 맞게 축소)
        p.scale(0.65, 0.65)

        # 내부 아이콘 색상 (트랙과 대비되는 색)
        icon_color = QColor(pal["text_sub"]) if self._progress < 0.5 else QColor(pal["text"])

        # ── 애니메이션 값 보간 (progress: 0.0=Sun, 1.0=Moon) ──
        # 해와 달이 변환될 때 다이나믹하게 180도 회전
        global_rot = self._progress * 180.0
        p.rotate(global_rot)

        # 달 모양일 때 본체 스케일을 확실하게 키움 (1.0 -> 1.6)
        circle_scale = 1.0 + (0.6 * self._progress)
        
        # 마스크 원 위치 조정 (Sun: 멀리 바깥쪽 -> Moon: 중심부 침범)
        # 180도 회전 후 달의 모양이 예쁘게 미러(좌우 반전)되도록 mask_x, y를 조절
        # progress=1일 때 mask(5, 5)
        mask_x = 20.0 - (15.0 * self._progress)
        mask_y = 20.0 - (15.0 * self._progress)
        
        beams_op = 1.0 - self._progress
        # 햇살도 자체적으로 회전하면서 사라짐
        beams_rot = self._progress * -90.0

        # ── 달/해 본체 그리기 (Mask 처리) ──
        base_circle = QPainterPath()
        cr = 6.0 * circle_scale
        base_circle.addEllipse(-cr, -cr, cr * 2, cr * 2)
        
        mask_circle = QPainterPath()
        mask_r = 9.0  # 달이 커진 만큼 마스크 반경도 더 키워서 비율을 맞춤
        mask_circle.addEllipse(mask_x - mask_r, mask_y - mask_r, mask_r * 2, mask_r * 2)
        
        moon_path = base_circle.subtracted(mask_circle)
        
        p.setPen(Qt.NoPen)
        p.setBrush(icon_color)
        p.drawPath(moon_path)

        # ── 햇살(Beams) 그리기 ──

        if beams_op > 0.01:
            p.save()
            p.rotate(beams_rot)
            
            c = QColor(icon_color)
            c.setAlphaF(c.alphaF() * beams_op)
            
            pen = QPen(c, 2.0)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            
            lines = [
                (0.0, -11.0, 0.0, -9.0),          
                (0.0, 9.0, 0.0, 11.0),            
                (-11.0, 0.0, -9.0, 0.0),          
                (9.0, 0.0, 11.0, 0.0),            
                (-7.78, -7.78, -6.36, -6.36),     
                (6.36, 6.36, 7.78, 7.78),         
                (-7.78, 7.78, -6.36, 6.36),       
                (6.36, -6.36, 7.78, -7.78)        
            ]
            
            for x1, y1, x2, y2 in lines:
                p.drawLine(x1, y1, x2, y2)
                
            p.restore()
