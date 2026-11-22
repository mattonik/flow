/* flow-pointer: a pointer for the guest tests, through the compositor's virtual-pointer protocol
 * (zwlr_virtual_pointer_v1), as wtype is a keyboard through the virtual-keyboard one.
 *
 *   flow-pointer WIDTH HEIGHT STEP...
 *
 * WIDTH and HEIGHT are the size of the area every output covers together (Niri maps absolute motion
 * from a pointer made without an output onto that area); positions are logical pixels from its top
 * left corner. Steps run in order, each sent as one frame:
 *
 *   move X Y         put the pointer at X, Y
 *   down [BUTTON]    press BUTTON (left, right or middle; left when left out)
 *   up [BUTTON]      release it
 *   click [BUTTON]   press and release
 *   scroll N         N wheel notches, down (positive) or up (negative)
 *   hscroll N        N wheel notches, right (positive) or left (negative)
 *   wait MS          wait MS milliseconds
 *
 * A gesture that needs a held button (a drag) is one invocation, since the pointer goes away when the
 * program exits. A button still held at the end is released first; one held when the program is killed
 * is left to the compositor. Exits 1 if the compositor drops the connection, 2 for a bad call.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <wayland-client.h>
#include "virtual-pointer.h"

#define BTN_LEFT 0x110
#define BTN_RIGHT 0x111
#define BTN_MIDDLE 0x112
#define NOTCH 15.0 /* a wheel notch in wl_pointer axis units, as libinput reports one */

static struct zwlr_virtual_pointer_manager_v1 *manager;

static void global(void *data, struct wl_registry *registry, uint32_t name, const char *interface,
                   uint32_t version) {
    (void)data; (void)version;
    if (strcmp(interface, zwlr_virtual_pointer_manager_v1_interface.name) == 0)
        manager = wl_registry_bind(registry, name, &zwlr_virtual_pointer_manager_v1_interface, 1);
}

static void global_remove(void *data, struct wl_registry *registry, uint32_t name) {
    (void)data; (void)registry; (void)name;
}

static const struct wl_registry_listener registry_listener = { global, global_remove };

static uint32_t now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000 + ts.tv_nsec / 1000000);
}

static int fail(const char *message, const char *detail) {
    fprintf(stderr, "flow-pointer: %s%s%s\n", message, detail ? ": " : "", detail ? detail : "");
    return 2;
}

/* Reads a whole decimal number in [min, max] into *value; says why and returns 0 when it isn't one. */
static int number(const char *text, long min, long max, long *value) {
    char *end;
    errno = 0;
    *value = text ? strtol(text, &end, 10) : 0;
    if (!text || errno || end == text || *end || *value < min || *value > max) {
        fail("not a number in range", text);
        return 0;
    }
    return 1;
}

/* Sends what is queued and waits for the compositor to have handled it; exits if the connection is gone, so a
 * test never carries on as if the pointer had moved. */
static void sync_or_exit(struct wl_display *display) {
    if (wl_display_roundtrip(display) < 0) {
        fprintf(stderr, "flow-pointer: lost the Wayland connection\n");
        exit(1);
    }
}

static int button_code(const char *name) {
    if (!name || strcmp(name, "left") == 0) return BTN_LEFT;
    if (strcmp(name, "right") == 0) return BTN_RIGHT;
    if (strcmp(name, "middle") == 0) return BTN_MIDDLE;
    return -1;
}

static int is_button(const char *name) { return name && button_code(name) > 0; }

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: flow-pointer WIDTH HEIGHT STEP...  (move X Y | down|up|click [BUTTON] | "
                        "scroll N | hscroll N | wait MS)\n");
        return 2;
    }
    long width, height, value;
    if (!number(argv[1], 1, 1 << 20, &width) || !number(argv[2], 1, 1 << 20, &height)) return 2;

    /* Check every step before the pointer exists, so a typo moves nothing. */
    for (int i = 3; i < argc; i++) {
        const char *step = argv[i];
        const char *next = i + 1 < argc ? argv[i + 1] : NULL, *after = i + 2 < argc ? argv[i + 2] : NULL;
        if (strcmp(step, "move") == 0) {
            if (!number(next, 0, width - 1, &value) || !number(after, 0, height - 1, &value))
                return fail("move needs X and Y inside the area", NULL);
            i += 2;
        } else if (strcmp(step, "down") == 0 || strcmp(step, "up") == 0 || strcmp(step, "click") == 0) {
            if (is_button(next)) i++;
        } else if (strcmp(step, "scroll") == 0 || strcmp(step, "hscroll") == 0) {
            if (!number(next, -100, 100, &value)) return fail("scroll needs notches from -100 to 100", NULL);
            i++;
        } else if (strcmp(step, "wait") == 0) {
            if (!number(next, 0, 60000, &value)) return fail("wait needs milliseconds up to 60000", NULL);
            i++;
        } else {
            return fail("unknown step", step);
        }
    }

    struct wl_display *display = wl_display_connect(NULL);
    if (!display) return fail("cannot connect to the Wayland display", getenv("WAYLAND_DISPLAY"));
    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    sync_or_exit(display);
    if (!manager) return fail("the compositor has no zwlr_virtual_pointer_manager_v1", NULL);
    struct zwlr_virtual_pointer_v1 *pointer = zwlr_virtual_pointer_manager_v1_create_virtual_pointer(manager, NULL);

    int held[3] = { 0, 0, 0 };
    for (int i = 3; i < argc; i++) {
        const char *step = argv[i];
        if (strcmp(step, "move") == 0) {
            uint32_t x = (uint32_t)strtol(argv[i + 1], NULL, 10), y = (uint32_t)strtol(argv[i + 2], NULL, 10);
            zwlr_virtual_pointer_v1_motion_absolute(pointer, now_ms(), x, y, (uint32_t)width, (uint32_t)height);
            i += 2;
        } else if (strcmp(step, "down") == 0 || strcmp(step, "up") == 0 || strcmp(step, "click") == 0) {
            const char *name = is_button(i + 1 < argc ? argv[i + 1] : NULL) ? argv[++i] : NULL;
            int code = button_code(name);
            if (strcmp(step, "up") != 0) {
                zwlr_virtual_pointer_v1_button(pointer, now_ms(), code, WL_POINTER_BUTTON_STATE_PRESSED);
                held[code - BTN_LEFT] = 1;
            }
            if (strcmp(step, "click") == 0) {
                zwlr_virtual_pointer_v1_frame(pointer);
                sync_or_exit(display);
            }
            if (strcmp(step, "down") != 0) {
                zwlr_virtual_pointer_v1_button(pointer, now_ms(), code, WL_POINTER_BUTTON_STATE_RELEASED);
                held[code - BTN_LEFT] = 0;
            }
        } else if (strcmp(step, "scroll") == 0 || strcmp(step, "hscroll") == 0) {
            int notches = (int)strtol(argv[++i], NULL, 10);
            uint32_t axis = step[0] == 'h' ? WL_POINTER_AXIS_HORIZONTAL_SCROLL : WL_POINTER_AXIS_VERTICAL_SCROLL;
            zwlr_virtual_pointer_v1_axis_source(pointer, WL_POINTER_AXIS_SOURCE_WHEEL);
            zwlr_virtual_pointer_v1_axis_discrete(pointer, now_ms(), axis, wl_fixed_from_double(NOTCH * notches), notches);
        } else if (strcmp(step, "wait") == 0) {
            long ms = strtol(argv[++i], NULL, 10);
            struct timespec pause = { ms / 1000, (ms % 1000) * 1000000 };
            nanosleep(&pause, NULL);
            continue;
        }
        zwlr_virtual_pointer_v1_frame(pointer);
        sync_or_exit(display);
    }
    for (int b = 0; b < 3; b++) {
        if (!held[b]) continue;
        zwlr_virtual_pointer_v1_button(pointer, now_ms(), BTN_LEFT + b, WL_POINTER_BUTTON_STATE_RELEASED);
        zwlr_virtual_pointer_v1_frame(pointer);
    }
    zwlr_virtual_pointer_v1_destroy(pointer);
    zwlr_virtual_pointer_manager_v1_destroy(manager);
    sync_or_exit(display);
    wl_display_disconnect(display);
    return 0;
}
