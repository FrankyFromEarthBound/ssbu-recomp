/* aspect.c - the shape of the picture: graphics.ini aspect = 16:9 | 4:3 | W:H (gfx_aspect; Vulkan only).
 *
 * The game is made for 16:9 and draws into 16:9 targets (its window texture is 1920x1080). For another shape A nothing
 * of that changes; three things are added:
 *   1. the game's cameras are made with A instead of 16:9 (here). Each camera object keeps its width / height at +296;
 *      the code that makes one writes 16/9 there and calls the camera update f_71004ec800 [game 0x71004ec800, the same in
 *      13.0.4 and 13.0.5], which derives the other fields from it (+308 tan of half the horizontal angle, +312 the
 *      vertical one, +332 / +336 the picture size 1920 x 1080). The fight sets the angle every frame (+268: 30 degrees)
 *      and keeps +296. Measured at A = 4:3 (debug match, 2026-10-24): the fight camera moves back a little (distance
 *      102.9 -> 105.0) and shows about 88 % of the width of 16:9 and more above and below; a fixed camera (results
 *      screen) keeps its height and shows less at the sides; at 21:9 the fight camera keeps about the width and shows
 *      less height. The 16:9 targets now hold a picture drawn for A, stretched to 16:9.
 *      Next step for the full width at every camera (and more width at 21:9): the 3D draws read the projection as a
 *      plain diag(1 / tan_x, 1 / tan_y, ...) from a uniform range (measured: vertex slot 0, bytes 196 / 216 of a 352-byte
 *      range; 2.799 / 3.732 at 4:3); keeping the cameras at 16:9 and scaling only that matrix's vertical term by
 *      A / (16 / 9) where the game builds it would give the same width as 16:9 everywhere.
 *   2. the present (nvn_vk_draw.c) puts the window texture into a rectangle of shape A (display_fit), which undoes that
 *      stretch.
 *   3. the 2D interface, drawn into the same texture after the 3D picture, is put into a box of that texture that looks
 *      16:9 once it is shown at A (nvn_vk_draw.c ar_*), in the middle; else it would be stretched with it.
 * Only cameras that hold exactly 16/9 are changed (a camera of a square or other target keeps its own shape).
 * The fight itself does not read the camera's shape: a whole debug match with A = 4:3 has the same checksums every frame
 * as with 16:9 (SSBU_FIGHT_HASH, 2026-10-24). */
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include "runtime.h"
#include "hostrt.h"
#include "gfx_options.h"

int hook_install(uint64_t addr, void (*repl)(Ctx *, void *), void *user, ImportFn *orig);      /* fighter_sdk\hook_host.c (in nnro.c) */

#define FN_CAMERA_UPDATE GA(0x71004ec800ULL)      /* (camera *): derives the camera's fields from its angle and shape */
#define CAM_ASPECT 296                            /* float: width / height of the camera's picture */
static ImportFn g_orig_camera_update;
static float g_aspect;
static volatile LONG g_cameras;

static void camera_update_hook(Ctx *c, void *user) {
    (void)user;
    uint64_t cam = c->x[0];
    uint32_t u = rd32(cam + CAM_ASPECT);
    float a;
    memcpy(&a, &u, 4);
    if (a > 1.7777f && a < 1.7778f) {
        memcpy(&u, &g_aspect, 4);
        wr32(cam + CAM_ASPECT, u);
        if (InterlockedIncrement(&g_cameras) <= 8) { printf("aspect: camera %llx made %.4f instead of 16:9\n", (unsigned long long)cam, (double)g_aspect); fflush(stdout); }
    }
    g_orig_camera_update(c);
}

void aspect_init(void) {
    g_aspect = gfx_aspect();
    if (g_aspect == 16.0f / 9.0f) return;
    printf("aspect: %s (%.4f): the game's cameras are made for it, the 2D interface keeps 16:9 in the middle; hook on the camera update %llx: id %d\n",
           gfx_aspect_text(), (double)g_aspect, (unsigned long long)FN_CAMERA_UPDATE, hook_install(FN_CAMERA_UPDATE, camera_update_hook, NULL, &g_orig_camera_update));
    fflush(stdout);
}
