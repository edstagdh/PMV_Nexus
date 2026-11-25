/*
 * PMVNexus.exe: the program the shortcuts start, so Windows shows "PMV Nexus" with its icon
 * (Task Manager, the Start menu, Explorer) instead of "pythonw".
 *
 * The same file does two jobs, depending on where it is:
 *
 *   {app}\PMVNexus.exe (3.2.0, what shortcuts point to): starts the copy below with the same
 *   arguments in the app folder, and ends.
 *       PMVNexus.exe                  -> .venv\Scripts\PMVNexus.exe -m Tools.open_app
 *       PMVNexus.exe run.py --tray    -> .venv\Scripts\PMVNexus.exe run.py --tray
 *   (no copy there -- an older install -> .venv\Scripts\pythonw.exe, as before 3.7.0)
 *
 *   {app}\.venv\Scripts\PMVNexus.exe (3.7.0, Setup copies it there): runs Python itself, inside
 *   this process, like pythonw.exe does -- so the running app shows as "PMV Nexus" in Task
 *   Manager instead of pythonw.exe. It loads python3XY.dll from the Python the virtual
 *   environment was made from (pyvenv.cfg "home" and "version"), and Python finds the
 *   environment by itself (pyvenv.cfg one folder up). Programs the app starts with
 *   sys.executable get this file too, which accepts the same arguments as python.
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
#include <shellapi.h>
#include <stdio.h>
#include <wchar.h>
#ifdef _MSC_VER
#pragma comment(lib, "user32.lib")   /* MessageBoxW */
#pragma comment(lib, "shell32.lib")  /* CommandLineToArgvW */
#endif

#define BUF 2048
typedef int (*py_main_fn)(int, wchar_t **);

static void fail(const wchar_t *text)
{
    MessageBoxW(NULL, text, L"PMV Nexus", MB_OK | MB_ICONERROR);
}

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

/* "key = value" from pyvenv.cfg (UTF-8), or 0. */
static int cfg_value(const wchar_t *cfg, const char *key, wchar_t *out, size_t size)
{
    FILE *f = NULL;
    char line[BUF];
    size_t klen = strlen(key);
    int found = 0;

    if (_wfopen_s(&f, cfg, L"rb") != 0 || !f)
        return 0;
    while (!found && fgets(line, sizeof line, f)) {
        char *p = line, *v, *end;
        while (*p == ' ' || *p == '\t')
            p++;
        if (_strnicmp(p, key, klen) != 0)
            continue;
        v = p + klen;
        while (*v == ' ' || *v == '\t')
            v++;
        if (*v != '=')
            continue;
        v++;
        while (*v == ' ' || *v == '\t')
            v++;
        end = v + strlen(v);
        while (end > v && (end[-1] == '\r' || end[-1] == '\n' || end[-1] == ' ' || end[-1] == '\t'))
            *--end = '\0';
        found = MultiByteToWideChar(CP_UTF8, 0, v, -1, out, (int)size) > 0;
    }
    fclose(f);
    return found;
}

#define NO_PYTHON_DLL (-1000)

/* Inside a virtual environment's Scripts folder: run Python in this process.
   NO_PYTHON_DLL when its python3XY.dll can't be used (the caller then starts pythonw.exe). */
static int run_python(const wchar_t *venv)
{
    wchar_t cfg[BUF], home[BUF], version[64], dll[BUF], **argv, *dot;
    wchar_t *fallback[3];
    int argc, major = 0, minor = 0, code;
    HMODULE lib;
    py_main_fn py_main;

    _snwprintf_s(cfg, BUF, _TRUNCATE, L"%s\\pyvenv.cfg", venv);
    if (!cfg_value(cfg, "home", home, BUF)
        || !(cfg_value(cfg, "version_info", version, 64) || cfg_value(cfg, "version", version, 64))
        || swscanf_s(version, L"%d.%d", &major, &minor) != 2) {
        fail(L"PMV Nexus's Python environment is damaged (pyvenv.cfg).\n\nRun PMV Nexus Setup again to repair it.");
        return 2;
    }
    dot = home + wcslen(home);
    while (dot > home && (dot[-1] == L'\\' || dot[-1] == L'/'))
        *--dot = L'\0';
    _snwprintf_s(dll, BUF, _TRUNCATE, L"%s\\python%d%d.dll", home, major, minor);
    lib = LoadLibraryExW(dll, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!lib)
        return NO_PYTHON_DLL;
    py_main = (py_main_fn)(void (*)(void))GetProcAddress(lib, "Py_Main");
    if (!py_main)
        return NO_PYTHON_DLL;
    argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv)
        return 1;
    if (argc < 2) {  /* nothing to run (an interactive Python would wait forever without a window) */
        fallback[0] = argv[0];
        fallback[1] = L"-m";
        fallback[2] = L"Tools.open_app";
        code = py_main(3, fallback);
    } else {
        code = py_main(argc, argv);
    }
    LocalFree(argv);
    return code;
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, LPWSTR cmdline, int show)
{
    wchar_t dir[BUF], parent[BUF], target[BUF], cfg[BUF], *slash, *line;
    const wchar_t *args, *workdir = dir;
    int code;
    size_t size;
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    DWORD n;

    (void)inst; (void)prev; (void)cmdline; (void)show;
    n = GetModuleFileNameW(NULL, dir, BUF);
    if (n == 0 || n >= BUF)
        return 1;
    slash = wcsrchr(dir, L'\\');
    if (!slash)
        return 1;
    *slash = L'\0';

    /* .venv\Scripts\PMVNexus.exe: pyvenv.cfg is one folder up */
    wcscpy_s(parent, BUF, dir);
    slash = wcsrchr(parent, L'\\');
    if (slash) {
        *slash = L'\0';
        _snwprintf_s(cfg, BUF, _TRUNCATE, L"%s\\pyvenv.cfg", parent);
        if (GetFileAttributesW(cfg) != INVALID_FILE_ATTRIBUTES) {
            code = run_python(parent);
            if (code != NO_PYTHON_DLL)
                return code;
            /* e.g. a Python whose DLL isn't where pyvenv.cfg says: the app still starts, as pythonw */
            _snwprintf_s(target, BUF, _TRUNCATE, L"%s\\pythonw.exe", dir);
            workdir = NULL;  /* keep the current folder */
            goto start;
        }
    }

    /* {app}\PMVNexus.exe: start the copy in the environment (or, without it, its pythonw) */
    _snwprintf_s(target, BUF, _TRUNCATE, L"%s\\.venv\\Scripts\\PMVNexus.exe", dir);
    if (GetFileAttributesW(target) == INVALID_FILE_ATTRIBUTES)
        _snwprintf_s(target, BUF, _TRUNCATE, L"%s\\.venv\\Scripts\\pythonw.exe", dir);
start:
    if (GetFileAttributesW(target) == INVALID_FILE_ATTRIBUTES) {
        fail(L"PMV Nexus isn't installed completely (its Python environment is missing).\n\n"
             L"An antivirus may have removed part of it (PMV Nexus isn't code-signed): check its quarantine "
             L"and allow the PMV Nexus folder there. Then run PMV Nexus Setup again to repair it.");
        return 2;
    }
    args = arguments(GetCommandLineW());
    if (!*args)
        args = L"-m Tools.open_app";
    size = wcslen(target) + wcslen(args) + 4;
    line = (wchar_t *)HeapAlloc(GetProcessHeap(), 0, size * sizeof(wchar_t));
    if (!line)
        return 1;
    _snwprintf_s(line, size, _TRUNCATE, L"\"%s\" %s", target, args);

    ZeroMemory(&si, sizeof si);
    si.cb = sizeof si;
    ZeroMemory(&pi, sizeof pi);
    /* standing in for Python (workdir NULL): pass the handles on, wait, and return its exit code */
    if (!CreateProcessW(target, line, NULL, NULL, workdir == NULL, 0, NULL, workdir, &si, &pi)) {
        fail(L"PMV Nexus couldn't be started.\n\nAn antivirus may be blocking it (PMV Nexus isn't code-signed): "
             L"check its quarantine and allow the PMV Nexus folder there. Then run PMV Nexus Setup again to repair it.");
        return 3;
    }
    CloseHandle(pi.hThread);
    if (workdir == NULL) {
        WaitForSingleObject(pi.hProcess, INFINITE);
        if (!GetExitCodeProcess(pi.hProcess, &n))
            n = 1;
        CloseHandle(pi.hProcess);
        return (int)n;
    }
    CloseHandle(pi.hProcess);
    return 0;
}
