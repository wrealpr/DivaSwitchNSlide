#include <windows.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

#define PLUGIN_NAME "Python Bridge Loader"
#define PLUGIN_VERSION "1.0"

HWND hWnd = NULL;
HANDLE hProcess = NULL;
DWORD pid = 0;

BOOL APIENTRY DllMain(HMODULE hModule, DWORD reason, LPVOID lpReserved) {
    switch (reason) {
        case DLL_PROCESS_ATTACH: {
            char cmdLine[1024];
            char dllPath[MAX_PATH];
            char dirPath[MAX_PATH];
            STARTUPINFO si;
            PROCESS_INFORMATION pi;
            
            // Get the DLL's directory
            GetModuleFileName(hModule, dllPath, MAX_PATH);
            char* lastSlash = strrchr(dllPath, '\\');
            if (lastSlash) {
                *lastSlash = '\0';
                strcpy(dirPath, dllPath);
            }
            
            // Build command to run python script in new window
            snprintf(cmdLine, sizeof(cmdLine), 
                     "cmd.exe /c \"cd /d \"%s\" && start \"\" python switch_slider_bridge.py\"", 
                     dirPath);
            
            // Create process with new window
            ZeroMemory(&si, sizeof(si));
            si.cb = sizeof(si);
            ZeroMemory(&pi, sizeof(pi));
            
            if (CreateProcess(NULL, cmdLine, NULL, NULL, FALSE, 
                              CREATE_NEW_CONSOLE, NULL, NULL, &si, &pi)) {
                hProcess = pi.hProcess;
                pid = pi.dwProcessId;
                CloseHandle(pi.hThread);
            }
            break;
        }
            
        case DLL_PROCESS_DETACH: {
            // Kill the python process when the app exits
            if (hProcess) {
                TerminateProcess(hProcess, 0);
                CloseHandle(hProcess);
                hProcess = NULL;
            }
            break;
        }
    }
    return TRUE;
}

// Export functions required by the app
extern "C" __declspec(dllexport) const char* GetPluginName() {
    return PLUGIN_NAME;
}

extern "C" __declspec(dllexport) const char* GetPluginVersion() {
    return PLUGIN_VERSION;
}

extern "C" __declspec(dllexport) void PluginInit(HWND hwnd) {
    hWnd = hwnd;
}

extern "C" __declspec(dllexport) void PluginCleanup() {
    if (hProcess) {
        TerminateProcess(hProcess, 0);
        CloseHandle(hProcess);
        hProcess = NULL;
    }
}