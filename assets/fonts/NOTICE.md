# 번들 폰트 라이선스

이 폴더의 폰트는 모두 **SIL Open Font License 1.1**(OFL)로 배포되며, OFL은 재배포·번들을
허용한다. 아래 고지와 라이선스 전문(각 폰트 파일 내부 `license` 네임 레코드에 포함)이
사본과 함께 유지되어야 한다.

| 파일 | 패밀리 | 저작권 | 출처 |
|---|---|---|---|
| `Pretendard-*.ttf` | Pretendard | Copyright (c) 2021 Kil Hyung-jin | https://github.com/orioncactus/pretendard |
| `NotoSerifKR-VF.ttf` | Noto Serif KR (본명조) | Copyright (c) 2017 Adobe (Source Han Serif) / Google | https://fonts.google.com/noto/specimen/Noto+Serif+KR |

- **Pretendard** — 앱 UI 전역 기본 글꼴. `static/alternative`(TrueType 힌팅) 빌드를 쓴다.
  OTF/CFF 빌드는 Windows/Qt에서 흐리고 세로로 잘려 보인다(2026-06 실측).
- **NotoSerifKR-VF** — 검토 화면 **원문·교정문 본문 전용** 명조. 출판 원고 대부분이
  명조 계열 본문을 쓰므로 원고와 같은 인상으로 읽히게 한다. **가변 폰트(TTF)** 한 파일로
  ExtraLight~Black 7종을 제공해(Qt 6.11 확인) 교정 하이라이트의 SemiBold/Bold도 진짜
  자형이 나온다. ⚠ 한자 병기 원고가 있으므로 **서브셋으로 줄이지 말 것**.

OFL 전문: https://openfontlicense.org
