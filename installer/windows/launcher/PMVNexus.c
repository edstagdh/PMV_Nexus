/*
 * PMVNexus.exe (3.0.1): the program the shortcuts start, so Windows shows "PMV Nexus" with
 * its icon (Task Manager's Startup tab, the Start menu, Explorer) instead of "pythonw".
 *
 * It only starts the app's own Python with the same arguments and ends:
 *     PMVNexus.exe                  -> .venv\Scripts\pythonw.exe -m Tools.open_app
 *     PMVNexus.exe run.py --tray    -> .venv\Scripts\pythonw.exe run.py --tray
 * in the folder this file is in. The app itself still runs as Python (pythonw.exe) after that.
 *
 * Built by installer/windows/build_launcher.ps1 (MSVC) in the release workflow.
 */
#define WIN32_LEAN_AND_MEAN
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <wchar.h>
#ifdef _MSC_VER
#pragma comment(lib, "user32.lib")   /* MessageBoxW */
#endif

/* The arguments after this program's own name, as written (quotes kept). */
static const wchar_t *arguments(const wchar_t *cmd)
{
    if (*cmd == L'"') {
        cmd++;
        while (*cmd && *cmd != L'"')
            cmd++;
        if (*cmd == L'"')
            cmd++;
    } else {
        while (*cmd && *cmd != L' ' && *cmd != L'\t')
            cmd++;
    }
    while (*cmd == L' ' || *cmd == L'\t')
        cmd++;
    return cmd;
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, LPWSTR cmdline, int show)
{
    wchar_t dir[MAX_PATH], python[MAX_PATH + 32], *slash, *line;
    const wchar_t *args;
    size_t size;
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    DWORD n;

    (void)inst; (void)prev; (void)cmdline; (void)show;
    n = GetModuleFileNameW(NULL, dir, MAX_PATH);
    if (n == 0 || n >= MAX_PATH)
        return 1;
    slash = wcsrchr(dir, L'\\');
    if (!slash)
        return 1;
    *slash = L'\0';
    if (_snwprintf_s(python, MAX_PATH + 32, _TRUNCATE, L"%s\\.venv\\Scripts\\pythonw.exe", dir) < 0)
        return 1;
    if (GetFileAttributesW(python) == INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(NULL, L"PMV Nexus isn't installed completely (its Python environment is missing).\n\n"
                          L"Run PMV Nexus Setup again to repair it.", L"PMV Nexus", MB_OK | MB_ICONERROR);
        return 2;
    }
    args = arguments(GetCommandLineW());
    if (!*args)
        args = L"-m Tools.open_app";
    size = wcslen(python) + wcslen(args) + 4;
    line = (wchar_t *)HeapAlloc(GetProcessHeap(), 0, size * sizeof(wchar_t));
    if (!line)
        return 1;
    _snwprintf_s(line, size, _TRUNCATE, L"\"%s\" %s", python, args);

    ZeroMemory(&si, sizeof si);
    si.cb = sizeof si;
    ZeroMemory(&pi, sizeof pi);
    if (!CreateProcessW(python, line, NULL, NULL, FALSE, 0, NULL, dir, &si, &pi)) {
        MessageBoxW(NULL, L"PMV Nexus couldn't be started.\n\nRun PMV Nexus Setup again to repair it.",
                    L"PMV Nexus", MB_OK | MB_ICONERROR);
        return 3;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
