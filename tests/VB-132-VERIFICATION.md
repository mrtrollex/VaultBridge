# VB-132 repair verification

Run from the repository root on Windows (PowerShell):

```text
.venv\Scripts\python.exe -m pytest -o addopts= -q -ra tests\test_promotion.py tests\test_vault_service.py tests\test_capture.py
```

Result: exit 0; **167 passed, 18 skipped**. The Windows skips include the
POSIX-specific promotion symlink and directory-relocation tests, plus tests
requiring symlink privileges unavailable on this host (`WinError 1314`).

Run from the repository root under WSL/Ubuntu:

```text
wsl -d Ubuntu -- bash -lc 'cd /mnt/c/Users/619ri/Desktop/VaultBridge && python3 -m pytest -o addopts= -q -ra tests/test_promotion.py tests/test_vault_service.py tests/test_capture.py'
```

Result: exit 0; **184 passed, 1 skipped**. The sole skip is the Windows
handle-pinning test. The POSIX promotion symlink and directory-relocation tests
ran, as did process and thread retry coverage for create and append.

The focused suite includes legacy `create_note` and `append_note` regression
tests. The repository's normal `agent_check.py` suite is run again by
`agent_finish.py` before generating the review packet.
