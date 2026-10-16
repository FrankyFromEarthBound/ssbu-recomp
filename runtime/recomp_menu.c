/* recomp_menu.c - the "Graphics" page we add to the game's Options menu (tools/ui/recomp_menu.py makes its files).
 *
 * The menus are Lua scripts the game runs (ui/script/**.lc). tools/ui/recomp_menu.py writes, from the user's dump,
 * a changed option_top.lc / option_top layout (a seventh button) into <build>/ui_patch/recomp/, and our own page
 * script into <build>/ui_patch/@recomp_page/ under the path of the Smash options script (option_melee_top.lc).
 * The loose-file layer (loose_files.c) serves the first folder always and the second only while the page is
 * wanted (folders named "@<name>" ask recomp_menu_condition).
 *
 * The scripts talk to this file through a function they already have: UiSoundManager:play_se_label(label), whose
 * Lua binding is [game] 0x710336e510. Its hook here takes labels that start with "recomp_" and plays nothing:
 *     recomp_open                  Graphics was chosen on the Options screen: the script then leaves with
 *                                  the Smash options' id, and that screen's script is served from @recomp_page
 *     recomp_set:<key>=<value>;... the page's choices: written into graphics.ini and applied at once (window,
 *                                  texture filtering, frame time); the resolution scale and vsync at the next start
 * The current values reach the page the other way: its script holds a string constant "@@RECOMP_VALUES@@" padded
 * with spaces, which is overwritten (same length) in the served bytes with "key=value;..." from graphics.ini.
 * The page flag is cleared when the Options screen's own script is served again (the page was left).
 * Nothing here runs unless the loose-file layer is on (SSBU_LOOSE_FILES=1). Contains nothing of the game. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "runtime.h"
#include "hostrt.h"
#include "gfx_options.h"

typedef void (*HookFn)(Ctx *c, void *user);
int hook_install(uint64_t addr, HookFn repl, void *user, ImportFn *orig);      /* fighter_sdk/hook_host.c (in nnro.c) */
void loose_host_callbacks(int (*condition)(const char *), void (*served)(const char *, uint8_t *, uint32_t));
void display_set_window_mode(int mode);                                        /* display.c */

#define FN_PLAY_SE_LABEL 0x710336e510ull     /* Lua binding of UiSoundManager:play_se_label(self, label) */
#define PAGE_SCRIPT "ui/script/menu/option/option_melee_top.lc"
#define TOP_SCRIPT "ui/script/menu/option/option_top.lc"
#define VALUES_MARK "@@RECOMP_VALUES@@"

static ImportFn g_orig_play_se_label;
static volatile LONG g_page;                 /* 1: the next Smash options screen is our page */

/* the keys the page shows, with the value the file has (or the default) */
static const char *const k_keys[] = {"window", "resolution_scale", "vsync", "anisotropic_filtering", "frame_time_overlay", "shader_wait"};
static const char *const k_defaults[] = {"windowed", "1.0", "auto", "game", "off", "hold"};
#define NKEYS (int)(sizeof k_keys / sizeof k_keys[0])

static void rm_trim(char *s) {
    char *e = s + strlen(s);
    while (e > s && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\r' || e[-1] == '\n')) *--e = 0;
    char *b = s;
    while (*b == ' ' || *b == '\t') b++;
    if (b != s) memmove(s, b, strlen(b) + 1);
}

static void read_values(char vals[][32]) {
    for (int k = 0; k < NKEYS; k++) snprintf(vals[k], 32, "%s", k_defaults[k]);
    FILE *f = fopen(gfx_options()->path, "rb");
    char line[512];
    while (f && fgets(line, sizeof line, f)) {
        char *eq;
        rm_trim(line);
        if (!line[0] || line[0] == ';' || line[0] == '#' || !(eq = strchr(line, '='))) continue;
        *eq = 0;
        rm_trim(line); rm_trim(eq + 1);
        for (int k = 0; k < NKEYS; k++) if (!_stricmp(line, k_keys[k])) snprintf(vals[k], 32, "%s", eq + 1);
    }
    if (f) fclose(f);
}

/* graphics.ini with the given keys set: a line "key = ..." is rewritten, a missing key is appended; comments stay */
static int write_values(const char *assign) {
    const char *path = gfx_options()->path;
    char keys[NKEYS][32], vals[NKEYS][64];
    int n = 0;
    for (const char *p = assign; *p && n < NKEYS;) {
        const char *eq = strchr(p, '='), *end = strchr(p, ';');
        if (!end) end = p + strlen(p);
        if (eq && eq < end) {
            snprintf(keys[n], sizeof keys[n], "%.*s", (int)(eq - p), p);
            snprintf(vals[n], sizeof vals[n], "%.*s", (int)(end - eq - 1), eq + 1);
            int known = 0;
            for (int k = 0; k < NKEYS; k++) known |= !strcmp(keys[n], k_keys[k]);
            if (known) n++;
        }
        p = *end ? end + 1 : end;
    }
    FILE *f = fopen(path, "rb");
    size_t cap = 1 << 16, len = 0;
    char *out = malloc(cap), line[512];
    int done[NKEYS] = {0};
    while (f && fgets(line, sizeof line, f)) {
        char copy[512], *eq;
        snprintf(copy, sizeof copy, "%s", line);
        rm_trim(copy);
        int hit = -1;
        if (copy[0] && copy[0] != ';' && copy[0] != '#' && (eq = strchr(copy, '='))) {
            *eq = 0;
            rm_trim(copy);
            for (int k = 0; k < n; k++) if (!_stricmp(copy, keys[k])) hit = k;
        }
        char repl[600];
        const char *text = line;
        if (hit >= 0) { snprintf(repl, sizeof repl, "%s = %s\r\n", keys[hit], vals[hit]); text = repl; done[hit] = 1; }
        size_t l = strlen(text);
        if (len + l + 1 > cap) out = realloc(out, cap *= 2);
        memcpy(out + len, text, l); len += l;
    }
    if (f) fclose(f);
    for (int k = 0; k < n; k++) if (!done[k]) {
        char repl[200];
        int l = snprintf(repl, sizeof repl, "%s = %s\r\n", keys[k], vals[k]);
        if (len + l + 1 > cap) out = realloc(out, cap *= 2);
        memcpy(out + len, repl, l); len += l;
    }
    int ok = 0;
    if ((f = fopen(path, "wb")) != NULL) { ok = fwrite(out, 1, len, f) == len; fclose(f); }
    free(out);
    printf("recomp menu: %d setting(s) written to %s%s: %s\n", n, path, ok ? "" : " (FAILED)", assign);
    /* and applied now where the program can: window, texture filtering, frame time; the resolution scale and vsync at the next start */
    unsigned changed = 0;
    for (int k = 0; k < n; k++) changed |= gfx_options_set(keys[k], vals[k]);
    if (changed & GFX_CHANGED_WINDOW) display_set_window_mode(gfx_options()->window_mode);
    printf("recomp menu: applied now:%s%s%s%s%s\n", changed & GFX_CHANGED_WINDOW ? " window" : "", changed & GFX_CHANGED_ANISO ? " texture filtering (textures drawn from now on)" : "", changed & GFX_CHANGED_OVERLAY ? " frame time" : "",
           !(changed & ~GFX_CHANGED_RESTART) ? " nothing" : "", changed & GFX_CHANGED_RESTART ? "; the resolution scale and vsync take effect at the next start" : "");
    return ok;
}

/* Lua 5.3 objects, as far as this file reads them: lua_State +0x20 ci; CallInfo +0 func (the called value; the
 * arguments follow it); a value is 16 bytes (+0 pointer, +8 type tag); a string's tag is 0x44 (short) or 0x54
 * (long), its length is at +0xb (short, one byte) or +0x10 (long) and its bytes at +0x18. */
static const char *lua_arg_string(uint64_t L, int n, size_t *len) {
    uint64_t ci = *(uint64_t *)(uintptr_t)(L + 0x20);
    if (!ci) return NULL;
    uint64_t func = *(uint64_t *)(uintptr_t)ci, top = *(uint64_t *)(uintptr_t)(L + 0x10);
    uint64_t v = func + 16ull * n;
    if (v >= top) return NULL;
    int tag = *(int *)(uintptr_t)(v + 8);
    uint64_t ts = *(uint64_t *)(uintptr_t)v;
    if (tag == 0x44) *len = *(uint8_t *)(uintptr_t)(ts + 0xb);
    else if (tag == 0x54) *len = (size_t)*(uint64_t *)(uintptr_t)(ts + 0x10);
    else return NULL;
    return (const char *)(uintptr_t)(ts + 0x18);
}

static void play_se_label_hook(Ctx *c, void *user) {
    (void)user;
    size_t n = 0;
    const char *s = lua_arg_string(c->x[0], 2, &n);
    if (s && n >= 7 && !memcmp(s, "recomp_", 7)) {
        char msg[512];
        snprintf(msg, sizeof msg, "%.*s", (int)(n < sizeof msg - 1 ? n : sizeof msg - 1), s);
        if (!strcmp(msg, "recomp_open")) {
            InterlockedExchange(&g_page, 1);
            printf("recomp menu: Graphics chosen: the next Smash options screen is the settings page\n");
        } else if (!strncmp(msg, "recomp_set:", 11)) {
            write_values(msg + 11);
        } else printf("recomp menu: unknown message %s\n", msg);
        fflush(stdout);
        c->x[0] = 0;                         /* no results */
        return;
    }
    g_orig_play_se_label(c);
}

static int recomp_menu_condition(const char *name) {
    return !strcmp(name, "recomp_page") && g_page;
}

static void recomp_menu_served(const char *arc_path, uint8_t *buf, uint32_t size) {
    if (!strcmp(arc_path, TOP_SCRIPT)) { InterlockedExchange(&g_page, 0); return; }
    if (strcmp(arc_path, PAGE_SCRIPT)) return;
    /* the page script: its placeholder (mark + spaces) becomes the current values, padded with spaces */
    size_t ml = strlen(VALUES_MARK);
    for (uint32_t i = 0; i + ml <= size; i++) {
        if (memcmp(buf + i, VALUES_MARK, ml)) continue;
        uint32_t end = i + (uint32_t)ml;
        while (end < size && buf[end] == ' ') end++;
        char vals[NKEYS][32], text[1024];
        int l = 0;
        read_values(vals);
        for (int k = 0; k < NKEYS; k++) l += snprintf(text + l, sizeof text - l, "%s=%s;", k_keys[k], vals[k]);
        if ((uint32_t)l > end - i) { printf("recomp menu: the values (%d bytes) do not fit the page's placeholder (%u)\n", l, end - i); return; }
        memset(buf + i, ' ', end - i);
        memcpy(buf + i, text, l);
        printf("recomp menu: settings page served with %s\n", text);
        return;
    }
    printf("recomp menu: the page script has no values placeholder\n");
}

static DWORD WINAPI recomp_menu_wait(LPVOID arg) {
    (void)arg;
    for (int i = 0; i < 3000; i++) {         /* the hook table answers once the game's function table is set up */
        if (hook_install(FN_PLAY_SE_LABEL, play_se_label_hook, NULL, &g_orig_play_se_label)) {
            printf("recomp menu: hook on play_se_label %llx installed\n", (unsigned long long)FN_PLAY_SE_LABEL);
            return 0;
        }
        Sleep(10);
    }
    printf("recomp menu: the hook on play_se_label could not be installed: the settings page is OFF\n");
    return 0;
}

__attribute__((constructor)) static void recomp_menu_start(void) {
    const char *e = getenv("SSBU_LOOSE_FILES");
    if (!e || e[0] == '0' || !e[0]) return;
    loose_host_callbacks(recomp_menu_condition, recomp_menu_served);
    HANDLE h = CreateThread(NULL, 0, recomp_menu_wait, NULL, 0, NULL);
    if (h) CloseHandle(h);
}
