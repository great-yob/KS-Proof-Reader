; ═══════════════════════════════════════════════════════════════════════════
;  KS-Proof Reader — 설치 파일 스크립트 (Inno Setup 6)
; ═══════════════════════════════════════════════════════════════════════════
;  build_dist.py 가 자동으로 호출한다. 손으로 돌릴 때:
;      "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" /DAppVersion=1.0.0 installer\KS-Proof-Reader.iss
;  전제: `dist\KS-AI Editor\` 가 이미 빌드돼 있어야 한다(앱+data+bridge32).
;
;  ⚠ 이 파일은 **UTF-8 BOM**으로 저장해야 한다. BOM이 없으면 Inno가 ANSI로 읽어
;    한글 메시지가 깨진다.
;
;  ▌설계 결정 — 왜 사용자 전용(%LOCALAPPDATA%\Programs) 설치인가
;    Program Files에 깔면 설치 폴더가 쓰기 불가라 core/updater.py 의 앱 자동 업데이트
;    (robocopy로 폴더 통째 교체)가 권한 오류로 죽는다. 사용자 전용 설치는 UAC도 없고
;    업데이터도 그대로 동작한다. 형제 앱 KS-Works-Utility(electron-builder)와 같은 위치.
;
;  ▌AppId는 제품 정체성이다. 같은 제품의 버전 업 동안에는 **절대 바꾸지 말 것**
;    (바꾸면 업그레이드가 아니라 별개 제품으로 인식돼 제어판 항목이 둘로 늘어난다).
;    단, **제품명을 바꾸는 rename**은 예외였다 — 2026-07-23 "KS-Proof Reader"→
;    "KS-AI Editor"로 개명하며 AppId를 새로 발급했다. 옛 AppId를 유지하면 기존
;    "KS-Proof Reader" 설치본이 있는 PC에서 새 설치 파일이 **옛 폴더 경로를 재사용**해
;    설치되어(같은 AppId=업그레이드로 인식) 폴더명이 "KS-AI Editor"가 되지 못한다.
;    → 이후로는 이 GUID를 고정한다.
; ═══════════════════════════════════════════════════════════════════════════

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName      "KS-AI Editor"
#define AppPublisher "Kim Daekyung"
#define AppExeName   "KS-AI Editor.exe"
#define AppUrl       "https://github.com/great-yob/KS-Proof-Reader"
; 릴리스 자산 파일명 접두사 — 공백을 하이픈으로(GitHub Releases가 공백을 점으로 바꿈).
;   build_dist.ASSET_PREFIX 와 반드시 같은 규칙이어야 한다.
#define AssetBase    StringChange(AppName, " ", "-")

; 경로는 이 .iss 위치 기준으로 계산한다 — 공백 있는 경로를 /D 로 넘기지 않아도 된다.
#define SrcDir   AddBackslash(SourcePath) + "..\dist\" + AppName
#define OutDir   AddBackslash(SourcePath) + "..\dist\release"
#define IconFile AddBackslash(SourcePath) + "..\assets\icon.ico"

[Setup]
AppId={{32456C4A-71D7-46B8-A59C-60B51DD21734}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} 설치 프로그램

; ── 설치 위치·권한 ──────────────────────────────────────────────
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; ▌폴더 선택 페이지를 숨긴다 — 단순 편의가 아니라 **자동 업데이트를 지키는 장치**다.
;   updater.install_app()은 EXE가 있는 폴더(app_dir)를 robocopy로 통째 교체하는데,
;   사용자가 Program Files 같은 쓰기 불가 경로를 고르면 그 교체가 권한 오류로 죽는다.
;   여기서 위치를 %LOCALAPPDATA%\Programs로 고정하면 그 사고를 원천 차단한다.
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; ── 산출물 ──────────────────────────────────────────────────────
OutputDir={#OutDir}
OutputBaseFilename={#AssetBase}-Setup-{#AppVersion}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}

; ── 압축 ────────────────────────────────────────────────────────
; 494MB(대부분 stdict.db 160MB + PySide6)라 lzma2/max + solid 로 zip보다 작게 만든다.
; 대신 컴파일에 수 분 걸린다.
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4

; ── 마법사 동작 ─────────────────────────────────────────────────
WizardStyle=modern
AllowNoIcons=yes
; 앱이 실행 중이면 재시작 관리자로 감지해 닫도록 안내한다(파일 잠김 방지).
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; dist 폴더를 통째로 — 앱 + data/(사전·kiwi 모델) + bridge32/(32비트 HWP 브리지).
; ⚠ build_dist.py 의 verify()가 유출 금지 파일(key.txt·config.ini·교정샘플)이 dist에
;   없음을 이미 확인한다. 여기서 레포 루트를 참조하는 규칙을 추가하지 말 것.
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}";  Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 자동 업데이트(updater.install_app)가 설치 후에 넣은 파일은 제거 로그에 없다.
; 폴더를 통째로 쓸어야 잔재가 남지 않는다.
Type: filesandordirs; Name: "{app}"

[CustomMessages]
korean.RemoveUserData=설정·캐시와 자동 업데이트로 내려받은 사전 데이터도 함께 삭제할까요?%n%n%1%n%n교정한 원고 파일은 이 폴더에 저장되지 않으므로 삭제되지 않습니다.

[Code]
{ 제거 시 사용자 데이터 폴더(%LOCALAPPDATA%\KS-AI Editor) 처리를 물어본다.
  ⚠ 이 경로는 datapaths.APP_DIR_NAME 과 반드시 같아야 한다(현재: "KS-AI Editor").
  설치 폴더와 별개다 — 설정(config.ini)·조회 캐시(api_cache.db)·업데이터가 받은
  최신 사전 데이터가 여기 있다. 재설치할 사람도 있으므로 묻고 나서 지운다.

  ⚠ 무인 제거(/SILENT·/VERYSILENT)에서는 **묻지도 지우지도 않는다**.
    Inno의 MsgBox는 /SUPPRESSMSGBOXES 상태에서 기본 버튼(MB_YESNO ⇒ 예)을 자동
    선택하므로, 이 가드가 없으면 스크립트로 제거할 때 사용자 데이터가 조용히 날아간다.
    파괴적 동작의 기본값은 '보존'이어야 한다. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  UserDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if UninstallSilent() then
      Exit;
    UserDir := ExpandConstant('{localappdata}\KS-AI Editor');
    if DirExists(UserDir) then
      if MsgBox(FmtMessage(CustomMessage('RemoveUserData'), [UserDir]),
                mbConfirmation, MB_YESNO) = IDYES then
        DelTree(UserDir, True, True, True);
  end;
end;
