@echo off
chcp 65001 >nul
rem ============================================================
rem  publish_to_github.bat  (UTF-8, chcp 65001 required)
rem  One-shot publisher for the "knowledge-base-from-zero" repo.
rem
rem  Flow:  privacy scan -> git init/commit -> create public repo
rem         -> push -> verify assets -> create Release v1.3.0
rem
rem  Prereqs:
rem    1) run "gh auth login" once (needs YOUR GitHub account)
rem    2) build assets in this folder: kb-from-zero.zip + kb-hub.exe
rem    3) keep your private term list OUTSIDE the repo:
rem       %USERPROFILE%\.kb_privacy_terms.txt
rem ============================================================
setlocal
title knowledge-base-from-zero publisher
set "ROOT=%~dp0"
set "REPO_NAME=knowledge-base-from-zero"
set "REPO_DESC=知识库搭建体系：从零到一完整搭建（方法论 + 门户 + 示例，去隐私化）"
set "TAG=v1.3.0"

rem resolve gh: PATH first, then common install locations
set "GH=gh"
where gh >nul 2>&1
if errorlevel 1 if exist "%USERPROFILE%\.workbuddy\binaries\gh\bin\gh.exe" set "GH=%USERPROFILE%\.workbuddy\binaries\gh\bin\gh.exe"
if errorlevel 1 if exist "%ProgramFiles%\GitHub CLI\gh.exe" set "GH=%ProgramFiles%\GitHub CLI\gh.exe"

rem resolve python: py launcher, then python
set "PY=py -3"
where py >nul 2>&1
if errorlevel 1 set "PY=python"

echo [0/6] checking gh auth ...
"%GH%" auth status >nul 2>&1
if errorlevel 1 (
  echo [ERROR] gh is not logged in. Run this first:
  echo         "%GH%" auth login
  pause
  exit /b 1
)

cd /d "%ROOT%"

echo [1/6] privacy scan (BLOCK = stop) ...
if exist "kb-hub\inventory.json" (
  echo [ERROR] kb-hub\inventory.json exists - it contains real local paths.
  echo         Delete it, then regenerate the example:
  echo         %PY% tools\make_example_inventory.py
  pause
  exit /b 1
)
%PY% tools\privacy_scan.py --terms "%USERPROFILE%\.kb_privacy_terms.txt"
if errorlevel 1 (
  echo.
  echo [ERROR] privacy scan reported BLOCK items. Publishing aborted.
  echo         Fix them, then run this script again.
  pause
  exit /b 1
)

echo [2/6] git init + commit ...
if not exist ".git" ( git init -q )
git add -A
git diff --cached --quiet
if errorlevel 1 (
  git commit -q -m "release: %TAG% 全库检索页签（三链路 RAG）+ 界面中英切换 + 全量中英双语文档"
) else (
  echo   (nothing to commit)
)

echo [3/6] create public repo + push ...
git remote get-url origin >nul 2>&1
if errorlevel 1 (
  "%GH%" repo create "%REPO_NAME%" --public --description "%REPO_DESC%" --source=. --remote=origin --push
) else (
  git push -u origin --all
)

echo [4/6] verifying assets ...
if not exist "kb-from-zero.zip" ( echo [ERROR] missing kb-from-zero.zip & pause & exit /b 1 )
if not exist "kb-hub.exe" ( echo [ERROR] missing kb-hub.exe & pause & exit /b 1 )

echo [5/6] create GitHub Release %TAG% ...
"%GH%" release create %TAG% ^
  --title "%TAG% 知识库搭建体系（门户 + 示例）" ^
  --notes-file RELEASE.md ^
  "kb-from-zero.zip#源码压缩包" ^
  "kb-hub.exe#门户可执行文件"

echo [6/6] done.
for /f "delims=" %%u in ('"%GH%" api user -q .login 2^>nul') do set "GHUSER=%%u"
if defined GHUSER (
  echo  Repo    : https://github.com/%GHUSER%/%REPO_NAME%
) else (
  echo  Repo    : run "%GH%" repo view --web  to open it
)
echo  Release : %TAG%
echo  Next    : edit the About description, add topics, pin the release.
pause
