@echo off
chcp 949 >nul 2>&1
setlocal enabledelayedexpansion
title KS-AI Editor - 한/글 브리지 진단 및 수리

set "APP=%LOCALAPPDATA%\Programs\KS-AI Editor"
set "BR=%APP%\bridge32\hwp_bridge_worker.exe"
set "DLL=%APP%\bridge32\FilePathCheckerModule.dll"
set "MODKEY=HKCU\Software\HNC\HwpAutomation\Modules"
set "PYTHONIOENCODING=utf-8"
set "LOG=%USERPROFILE%\Desktop\KS_bridge_diag_%COMPUTERNAME%.txt"
set "LOGB=%USERPROFILE%\Desktop\KS_bridge_diag_%COMPUTERNAME%_bridge.txt"

echo ============================================================
echo  KS-AI Editor  한/글 브리지 진단 및 수리
echo ============================================================
echo.

> "%LOG%" echo [KS-AI Editor 브리지 진단] %DATE% %TIME%
>> "%LOG%" echo PC=%COMPUTERNAME%  USER=%USERNAME%
>> "%LOG%" echo 대상파일=%~f1
>> "%LOG%" echo.

echo  [1/6] 설치 확인
if not exist "%BR%" (
  echo        X 브리지 실행파일이 없습니다: %BR%
  >> "%LOG%" echo [1] bridge32 없음
  goto :done
)
echo        O 브리지 있음
>> "%LOG%" echo [1] bridge32 OK

echo  [2/6] 한/글 COM 등록 확인
set "COMOK=0"
reg query "HKCR\HWPFrame.HwpObject\CLSID" >nul 2>&1 && set "COMOK=1"
reg query "HKLM\SOFTWARE\WOW6432Node\Classes\HWPFrame.HwpObject\CLSID" >nul 2>&1 && set "COMOK=1"
if "!COMOK!"=="1" ( echo        O 등록됨 & >> "%LOG%" echo [2] COM 등록 OK ) else ( echo        X 미등록 - 한/글 복구 설치 필요 & >> "%LOG%" echo [2] COM 미등록 )

echo  [3/6] 한/글 보안 모듈 등록  ^<== 유력 원인
>> "%LOG%" echo --- HwpAutomation Modules (before) ---
reg query "%MODKEY%" >> "%LOG%" 2>&1
reg query "%MODKEY%" /v FilePathCheckerModule >nul 2>&1
if errorlevel 1 (
  echo        X 미등록  - 한/글이 '문서 접근 승인' 창을 띄웁니다
  >> "%LOG%" echo [3] 미등록 -^> 수리 시도
  if not exist "%DLL%" (
    echo        ! 보안 모듈 DLL을 찾을 수 없습니다: %DLL%
    >> "%LOG%" echo [3] DLL 없음
  ) else (
    reg add "%MODKEY%" /v FilePathCheckerModule /t REG_SZ /d "%DLL%" /f >nul 2>&1
    if errorlevel 1 (
      echo        X 등록 실패
      >> "%LOG%" echo [3] reg add 실패
    ) else (
      echo        O 수리 완료 - 보안 모듈을 등록했습니다
      >> "%LOG%" echo [3] reg add 성공: %DLL%
    )
  )
) else (
  echo        O 이미 등록되어 있음
  >> "%LOG%" echo [3] 이미 등록됨
)
>> "%LOG%" echo --- HwpAutomation Modules (after) ---
reg query "%MODKEY%" /v FilePathCheckerModule >> "%LOG%" 2>&1

echo  [4/6] 남아 있는 한/글 프로세스 정리
powershell -NoProfile -Command "$h=@(Get-Process hwp -ErrorAction SilentlyContinue); $z=@($h | Where-Object {$_.MainWindowHandle -eq 0}); $v=@($h | Where-Object {$_.MainWindowHandle -ne 0}); $z | Stop-Process -Force -ErrorAction SilentlyContinue; Write-Output ('HIDDEN_KILLED=' + $z.Count); Write-Output ('VISIBLE_LEFT=' + $v.Count)" > "%TEMP%\ks_hwp.txt" 2>&1
>> "%LOG%" echo [4] hwp.exe 정리:
type "%TEMP%\ks_hwp.txt" >> "%LOG%"
set "HK=0"
set "VL=0"
for /f "tokens=2 delims==" %%a in ('findstr /b "HIDDEN_KILLED" "%TEMP%\ks_hwp.txt"') do set "HK=%%a"
for /f "tokens=2 delims==" %%a in ('findstr /b "VISIBLE_LEFT" "%TEMP%\ks_hwp.txt"') do set "VL=%%a"
if "!HK!"=="0" (
  echo        O 숨어 있던 한/글 없음
) else (
  echo        O 창 없이 남아 있던 한/글 !HK!개를 정리했습니다
  echo          ^(교정 실패가 남긴 것입니다. 이게 쌓이면 계속 멈춥니다^)
)
if not "!VL!"=="0" echo        - 사용 중인 한/글 !VL!개는 그대로 두었습니다

echo  [5/6] 브리지 기동 확인 ^(한/글은 건드리지 않음^)
> "%TEMP%\ks_q.txt" echo {"cmd":"quit"}
type "%TEMP%\ks_q.txt" | "%BR%" > "%TEMP%\ks_q_out.txt" 2>&1
>> "%LOG%" echo [5] quit-only 결과:
type "%TEMP%\ks_q_out.txt" >> "%LOG%"
find /i "quit" "%TEMP%\ks_q_out.txt" >nul 2>&1
if errorlevel 1 (
  echo        X 브리지 무응답 - 32비트 워커 기동 자체가 막힘
  echo          ^(백신/SmartScreen 차단 의심. 이 경우 한/글은 원인이 아닙니다^)
  >> "%LOG%" echo [5] 워커 기동 실패
) else (
  echo        O 정상 기동 - 워커는 멀쩡, 문제는 한/글 COM 쪽
  >> "%LOG%" echo [5] 워커 정상
)

if "%~1"=="" (
  echo.
  echo  [6/6] 건너뜀 - 실제 문서 열기 시험을 하려면
  echo        이 파일 위로 .hwp 파일을 끌어다 놓고 다시 실행하세요.
  >> "%LOG%" echo [6] 생략 - 파일 미지정
  goto :done
)

echo.
echo  [6/6] 실제 문서를 ^<한/글 창이 보이는 상태^>로 열어 봅니다.
echo.
echo        ***  여기서 멈추면 = 문제 재현 성공  ***
echo        한/글 창이나 대화상자가 뜨면 캡처한 뒤 확인/닫아 주세요.
echo.
pause

rem 워커는 stdin을 UTF-8로 읽는다. batch의 echo는 콘솔 코드페이지로 쓰므로
rem 한글 파일명이 깨진다(실측) - PowerShell로 UTF-8 기록한다.
set "KSPATH=%~f1"
powershell -NoProfile -Command "$q=[char]34; $p=$env:KSPATH.Replace([char]92,'/'); $l=@(('{'+$q+'cmd'+$q+':'+$q+'open'+$q+','+$q+'file_path'+$q+':'+$q+$p+$q+','+$q+'visible'+$q+':true}'),('{'+$q+'cmd'+$q+':'+$q+'quit'+$q+'}')); [IO.File]::WriteAllLines($env:TEMP+'\ks_cmd.txt',$l,(New-Object Text.UTF8Encoding $false))"

>> "%LOG%" echo [6] 브리지 실행 %TIME% - 상세는 %LOGB%
type "%TEMP%\ks_cmd.txt" | "%BR%" > "%LOGB%" 2>&1
>> "%LOG%" echo [6] 종료 %TIME%

echo.
echo  ---- 브리지 출력 요약 ----
findstr /i "error ok true false" "%LOGB%"
echo  --------------------------

:done
echo.
echo  결과 파일 ^(둘 다 담당자에게 보내 주세요^):
echo    %LOG%
if exist "%LOGB%" echo    %LOGB%
echo.
pause
