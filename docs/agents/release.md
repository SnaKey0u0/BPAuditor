# Release

Release only after the user explicitly approves (see memory `release-needs-explicit-approval`). Steps, from a clean `dev` that is pushed:

1. **Build**: `cmd /c "C:\Users\Jacky\Desktop\BPAuditor\build.bat < nul"`. Use the absolute path, since `cmd /c` from PowerShell does not resolve a bare `build.bat`, and `< nul` gets past the trailing `pause`. Done when the output ends with `完成：dist\BPAuditor.exe`.
2. **Smoke**: `powershell -NoProfile -ExecutionPolicy Bypass -File tools\smoke_exe.ps1`. It launches the exe against a temp APPDATA and checks it is still alive after 8 s. Done when it prints `OK`.
3. **Merge and tag**: `git checkout main && git pull --ff-only && git merge --no-ff dev` (the pre-merge-commit hook runs the checks), then `git tag -a vX.Y.Z -m "血壓稽查器 vX.Y.Z"` and push `main` plus the tag.
4. **Publish**: `gh release create vX.Y.Z dist/BPAuditor.exe --title "血壓稽查器 vX.Y.Z" --notes-file <notes.md>`. Use `## 調整` / `## 修正` / `## 內部` as notes headings. To verify, run `gh release list --limit 2`; `gh release view --json` has no `isLatest` field.
