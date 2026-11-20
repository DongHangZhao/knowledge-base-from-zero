@echo off
chcp 65001 >nul
rem ============================================================
rem  publish_to_github.bat  (UTF-8, chcp 65001 required)
rem  One-shot publisher for the "knowledge-base-from-zero" repo.
rem
rem  Flow:  release-gate preflight -> privacy scan -> git commit/push branch
rem         -> tag + push tag -> CI builds 5 assets and publishes the Release
rem
rem  🔴 TAG is read from root VERSION (the single source of truth).
rem     It used to be hard-coded as v1.3.1 and stayed stale for 3 releases.
rem  🔴 This is the ONLY script that performs outbound actions.
rem     Run it by hand = you are authorizing the publish; no automation calls it.
rem     tools/release_gate.py itself NEVER pushes.
rem
rem  🔴 2026-11-20: 本脚本不再自己 `gh release create`。
rem     旧版只上传 kb-from-zero.zip + kb-hub.exe 两件，CI 那条路一件都不传，
rem     两条发版路径各缺各的 —— v1.21.0~v1.23.0 连续四版资产全空就是这么来的。
rem     现在统一成一条：本地只 push，Release 由 .github/workflows/release.yml
rem     构建 5 个资产（源码包/exe/linux/macos/apk）后发布，构建失败就不发。
rem     发版完成的判据是 tools/release_assets_gate.py 返回 0，不是脚本不报错。
rem
rem  Prereqs:
rem    1) run "gh auth login" once (needs YOUR GitHub account)
rem    2) keep your private term list OUTSIDE the repo:
rem       %USERPROFILE%\.kb_privacy_terms.txt
rem ============================================================
setlocal
title knowledge-base-from-zero publisher
set "ROOT=%~dp0"
set "REPO_NAME=knowledge-base-from-zero"
set "REPO_DESC=知识库搭建体系：从零到一完整搭建（方法论 + 门户 + 示例，去隐私化）"

rem ---- TAG from VERSION (never hard-code) ----
set "TAG="
for /f "usebackq delims=" %%v in ("%ROOT%VERSION") do set "TAG=v%%v"
if not defined TAG (
  echo [ERROR] Cannot read VERSION - aborting.
  pause
  exit /b 1
)
echo   TAG=%TAG%  ^(from VERSION^)

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

rem 🔴 --skip-tag: 此刻本地 tag 还没打（第 4 步才打），不能让 gate 去比对旧 tag。
echo [0.5/6] release gate (version consistency + privacy + payload size) ...
%PY% tools\release_gate.py --check --skip-tag --preflight
if errorlevel 1 (
  echo [ERROR] release gate failed. Fix it before publishing.
  pause
  exit /b 1
)

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
  git commit -q -m "release: %TAG%"
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

echo [4/6] tag + push tag (CI will build 5 assets and publish) ...
rem 本地不构建产物：CI 从 tag 现场构建，资产名与 Release 逐字一致。
git rev-parse -q --verify "refs/tags/%TAG%" >nul 2>&1
if errorlevel 1 (
  git tag -a %TAG% -m "release %TAG%"
  echo   created tag %TAG%
) else (
  echo   tag %TAG% already exists
)
git push origin %TAG%
if errorlevel 1 (
  echo [ERROR] failed to push tag %TAG%.
  pause
  exit /b 1
)

echo [5/6] Release is built by CI, not here ...
echo   workflow : .github/workflows/release.yml
echo   watch    : "%GH%" run list --workflow release.yml --limit 3
echo   note     : build takes ~30-60 min (android layer is the slow one)
echo   a Release only counts as published when assets pass the gate below.

echo [6/6] verify assets (run again after the workflow finishes) ...
%PY% tools\release_assets_gate.py --tag %TAG%
if errorlevel 1 (
  echo.
  echo   [PENDING] assets not complete yet - wait for the workflow, then re-run:
  echo             %PY% tools\release_assets_gate.py --tag %TAG%
)
for /f "delims=" %%u in ('"%GH%" api user -q .login 2^>nul') do set "GHUSER=%%u"
if defined GHUSER (
  echo  Repo    : https://github.com/%GHUSER%/%REPO_NAME%
) else (
  echo  Repo    : run "%GH%" repo view --web  to open it
)
echo  Release : %TAG%
echo  Next    : edit the About description, add topics, pin the release.
pause
