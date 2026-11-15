// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: Apache-2.0
/* Which way does a client get its frames to the X server? Counts the requests that carry them and
 * says so on stderr when the client exits:
 *   PresentPixmap           a GPU buffer (DRI3 pixmap) is presented: the server reads the buffer
 *   DRI3 PixmapFromBuffer   such buffers were handed to the server (a few, once)
 *   ShmPutImage / PutImage  the client copied the pixels itself, after its GPU was done
 *
 *   gcc -O2 -shared -fPIC -o /var/tmp/xcbcount.so xcbcount.c -ldl
 *   LD_PRELOAD=/var/tmp/xcbcount.so <client>
 * Needs the headers of libxcb-present, -dri3 and -shm (libxcb-present-dev and so on).
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <xcb/xcb.h>
#include <xcb/present.h>
#include <xcb/dri3.h>
#include <xcb/shm.h>

static unsigned nPresent, nFromBuffer, nShmPut, nPut;
/* The real function, from its own library: Mesa is loaded with dlopen(), and what such a library
 * links to is not where RTLD_NEXT looks. */
#define REAL(lib, name) static __typeof__(&name) real; if (!real) real = (__typeof__(&name)) dlsym(dlopen(lib, RTLD_LAZY), #name)
#define PRESENT "libxcb-present.so.0"
#define DRI3 "libxcb-dri3.so.0"
#define SHM "libxcb-shm.so.0"
#define XCB "libxcb.so.1"

#define PRESENT_ARGS xcb_connection_t *c, xcb_window_t window, xcb_pixmap_t pixmap, uint32_t serial, xcb_xfixes_region_t valid, \
    xcb_xfixes_region_t update, int16_t x_off, int16_t y_off, xcb_randr_crtc_t target_crtc, xcb_sync_fence_t wait_fence, \
    xcb_sync_fence_t idle_fence, uint32_t options, uint64_t target_msc, uint64_t divisor, uint64_t remainder, uint32_t notifies_len, \
    const xcb_present_notify_t *notifies
#define PRESENT_PASS c, window, pixmap, serial, valid, update, x_off, y_off, target_crtc, wait_fence, idle_fence, options, target_msc, \
    divisor, remainder, notifies_len, notifies
xcb_void_cookie_t xcb_present_pixmap(PRESENT_ARGS) { REAL(PRESENT, xcb_present_pixmap); nPresent++; return real(PRESENT_PASS); }
xcb_void_cookie_t xcb_present_pixmap_checked(PRESENT_ARGS) { REAL(PRESENT, xcb_present_pixmap_checked); nPresent++; return real(PRESENT_PASS); }

#define SYNCED_ARGS xcb_connection_t *c, xcb_window_t window, xcb_pixmap_t pixmap, uint32_t serial, xcb_xfixes_region_t valid, \
    xcb_xfixes_region_t update, int16_t x_off, int16_t y_off, xcb_randr_crtc_t target_crtc, xcb_dri3_syncobj_t acquire_syncobj, \
    xcb_dri3_syncobj_t release_syncobj, uint64_t acquire_point, uint64_t release_point, uint32_t options, uint64_t target_msc, \
    uint64_t divisor, uint64_t remainder, uint32_t notifies_len, const xcb_present_notify_t *notifies
#define SYNCED_PASS c, window, pixmap, serial, valid, update, x_off, y_off, target_crtc, acquire_syncobj, release_syncobj, acquire_point, \
    release_point, options, target_msc, divisor, remainder, notifies_len, notifies
xcb_void_cookie_t xcb_present_pixmap_synced(SYNCED_ARGS) { REAL(PRESENT, xcb_present_pixmap_synced); nPresent++; return real(SYNCED_PASS); }
xcb_void_cookie_t xcb_present_pixmap_synced_checked(SYNCED_ARGS) { REAL(PRESENT, xcb_present_pixmap_synced_checked); nPresent++; return real(SYNCED_PASS); }

#define BUFFER_ARGS xcb_connection_t *c, xcb_pixmap_t pixmap, xcb_drawable_t drawable, uint32_t size, uint16_t width, uint16_t height, \
    uint16_t stride, uint8_t depth, uint8_t bpp, int32_t pixmap_fd
#define BUFFER_PASS c, pixmap, drawable, size, width, height, stride, depth, bpp, pixmap_fd
xcb_void_cookie_t xcb_dri3_pixmap_from_buffer(BUFFER_ARGS) { REAL(DRI3, xcb_dri3_pixmap_from_buffer); nFromBuffer++; return real(BUFFER_PASS); }
xcb_void_cookie_t xcb_dri3_pixmap_from_buffer_checked(BUFFER_ARGS) { REAL(DRI3, xcb_dri3_pixmap_from_buffer_checked); nFromBuffer++; return real(BUFFER_PASS); }

#define BUFFERS_ARGS xcb_connection_t *c, xcb_pixmap_t pixmap, xcb_window_t window, uint8_t num_buffers, uint16_t width, uint16_t height, \
    uint32_t stride0, uint32_t offset0, uint32_t stride1, uint32_t offset1, uint32_t stride2, uint32_t offset2, uint32_t stride3, \
    uint32_t offset3, uint8_t depth, uint8_t bpp, uint64_t modifier, const int32_t *buffers
#define BUFFERS_PASS c, pixmap, window, num_buffers, width, height, stride0, offset0, stride1, offset1, stride2, offset2, stride3, offset3, \
    depth, bpp, modifier, buffers
xcb_void_cookie_t xcb_dri3_pixmap_from_buffers(BUFFERS_ARGS) { REAL(DRI3, xcb_dri3_pixmap_from_buffers); nFromBuffer++; return real(BUFFERS_PASS); }
xcb_void_cookie_t xcb_dri3_pixmap_from_buffers_checked(BUFFERS_ARGS) { REAL(DRI3, xcb_dri3_pixmap_from_buffers_checked); nFromBuffer++; return real(BUFFERS_PASS); }

#define SHMPUT_ARGS xcb_connection_t *c, xcb_drawable_t drawable, xcb_gcontext_t gc, uint16_t total_width, uint16_t total_height, \
    uint16_t src_x, uint16_t src_y, uint16_t src_width, uint16_t src_height, int16_t dst_x, int16_t dst_y, uint8_t depth, uint8_t format, \
    uint8_t send_event, xcb_shm_seg_t shmseg, uint32_t offset
#define SHMPUT_PASS c, drawable, gc, total_width, total_height, src_x, src_y, src_width, src_height, dst_x, dst_y, depth, format, \
    send_event, shmseg, offset
xcb_void_cookie_t xcb_shm_put_image(SHMPUT_ARGS) { REAL(SHM, xcb_shm_put_image); nShmPut++; return real(SHMPUT_PASS); }
xcb_void_cookie_t xcb_shm_put_image_checked(SHMPUT_ARGS) { REAL(SHM, xcb_shm_put_image_checked); nShmPut++; return real(SHMPUT_PASS); }

#define PUT_ARGS xcb_connection_t *c, uint8_t format, xcb_drawable_t drawable, xcb_gcontext_t gc, uint16_t width, uint16_t height, \
    int16_t dst_x, int16_t dst_y, uint8_t left_pad, uint8_t depth, uint32_t data_len, const uint8_t *data
#define PUT_PASS c, format, drawable, gc, width, height, dst_x, dst_y, left_pad, depth, data_len, data
xcb_void_cookie_t xcb_put_image(PUT_ARGS) { REAL(XCB, xcb_put_image); nPut++; return real(PUT_PASS); }
xcb_void_cookie_t xcb_put_image_checked(PUT_ARGS) { REAL(XCB, xcb_put_image_checked); nPut++; return real(PUT_PASS); }

__attribute__((destructor)) static void report(void) {
    fprintf(stderr, "xcbcount: PresentPixmap %u, DRI3 PixmapFromBuffer %u, ShmPutImage %u, PutImage %u\n", nPresent, nFromBuffer, nShmPut, nPut);
}
