// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: Apache-2.0
/* Does a GL client's frame reach the X server's screen whole?
 *
 * Draws each frame in one flat colour through EGL and GLES2, swaps without waiting for vsync (as
 * OBS Studio and most toolkits do), then reads its own window back from the X server and counts
 * the pixels that are not that frame's colour. A frame that comes back as two frames' colours was
 * copied by the server while the GPU was still drawing it: that is tearing, and here it is counted
 * instead of looked for. The colours are dark greys a few steps apart, so the window doesn't flash.
 *
 * Build in the env, and run it in a session with a display (DISPLAY and the app's GL variables
 * set). in_session.py borrows those from an app that is open:
 *   gcc -O2 -o /var/tmp/teartest teartest.c -lX11 -lEGL -lGLESv2
 *   python3 /var/tmp/in_session.py <pid of the app> -- /var/tmp/teartest plain|finish \
 *       [frames=300] [layers=40] [width=480] [height=800] [fps=30]
 * With the Mesa of gramarye/mesa, FD_KGSL_PRESENT_WAIT=0 (before the --) gives the driver's
 * behaviour without its wait for the GPU, to compare.
 * "finish" calls glFinish() before each swap and says how long the GPU took: no frame can tear
 * then, which tells a server that reads too soon from one that mixes frames up itself. "layers" is
 * how many times the window is filled per frame, to make the GPU's work longer.
 */
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <EGL/egl.h>
#include <GLES2/gl2.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>

#define NPAL 7
static const unsigned char PAL[NPAL][3] = {{40,40,40},{52,40,40},{40,52,40},{40,40,52},{52,52,40},{40,52,52},{52,40,52}};
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }
static int near(int a, int b) { return a - b <= 1 && b - a <= 1; }
static int shift_of(unsigned long m) { int s = 0; while (m && !(m & 1)) { m >>= 1; s++; } return s; }

int main(int argc, char **argv) {
    int finish = argc > 1 && !strcmp(argv[1], "finish");
    int frames = argc > 2 ? atoi(argv[2]) : 300, layers = argc > 3 ? atoi(argv[3]) : 40;
    int W = argc > 4 ? atoi(argv[4]) : 480, H = argc > 5 ? atoi(argv[5]) : 800, fps = argc > 6 ? atoi(argv[6]) : 30;
    Display *d = XOpenDisplay(NULL);
    if (!d) { puts("no display"); return 1; }
    XSetWindowAttributes a; a.override_redirect = True; a.background_pixel = 0; a.event_mask = StructureNotifyMask;
    Window w = XCreateWindow(d, DefaultRootWindow(d), 40, 120, W, H, 0, CopyFromParent, InputOutput, CopyFromParent,
                             CWOverrideRedirect | CWBackPixel | CWEventMask, &a);
    XMapRaised(d, w);
    for (;;) { XEvent e; XNextEvent(d, &e); if (e.type == MapNotify) break; }
    EGLDisplay ed = eglGetDisplay((EGLNativeDisplayType) d);
    if (!eglInitialize(ed, 0, 0)) { puts("eglInitialize failed"); return 1; }
    eglBindAPI(EGL_OPENGL_ES_API);
    EGLint ca[] = {EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT, EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_NONE};
    EGLConfig cfgs[256], cfg; EGLint n = 0, vid = (EGLint) XVisualIDFromVisual(DefaultVisual(d, DefaultScreen(d)));
    if (!eglChooseConfig(ed, ca, cfgs, 256, &n) || n < 1) { puts("no egl config"); return 1; }
    cfg = cfgs[0];
    for (int i = 0; i < n; i++) { EGLint v = 0; eglGetConfigAttrib(ed, cfgs[i], EGL_NATIVE_VISUAL_ID, &v); if (v == vid) { cfg = cfgs[i]; break; } }
    EGLSurface s = eglCreateWindowSurface(ed, cfg, (EGLNativeWindowType) w, 0);
    EGLint cta[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
    EGLContext cx = eglCreateContext(ed, cfg, EGL_NO_CONTEXT, cta);
    if (s == EGL_NO_SURFACE || cx == EGL_NO_CONTEXT || !eglMakeCurrent(ed, s, s, cx)) { printf("egl surface/context failed 0x%x\n", eglGetError()); return 1; }
    eglSwapInterval(ed, 0);
    const char *vs = "attribute vec2 p; void main(){ gl_Position = vec4(p, 0., 1.); }";
    const char *fs = "precision mediump float; uniform vec4 c; void main(){ gl_FragColor = c; }";
    GLuint v = glCreateShader(GL_VERTEX_SHADER), f = glCreateShader(GL_FRAGMENT_SHADER), p = glCreateProgram();
    glShaderSource(v, 1, &vs, 0); glCompileShader(v); glShaderSource(f, 1, &fs, 0); glCompileShader(f);
    glAttachShader(p, v); glAttachShader(p, f); glBindAttribLocation(p, 0, "p"); glLinkProgram(p); glUseProgram(p);
    GLint u = glGetUniformLocation(p, "c");
    static const GLfloat quad[] = {-1, -1, 1, -1, -1, 1, 1, 1};
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, quad); glEnableVertexAttribArray(0);
    glViewport(0, 0, W, H); glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);

    unsigned long long left = 0; int leftTorn = 0;   /* the torn picture the frame before left in the window, if it did */
    int torn = 0, late = 0, never = 0, shown = 0, oldk[NPAL] = {0}, tornOther = 0;
    double wrongSum = 0, wrongMax = 0, t0 = now(), finSum = 0, finMax = 0, showSum = 0, showMax = 0; int showN = 0;
    for (int fr = 0; fr < frames; fr++) {
        double tf = now();
        const unsigned char *col = PAL[fr % NPAL];
        glEnable(GL_BLEND);
        for (int l = 0; l < layers; l++) { float g = 0.10f + 0.02f * (l % 3); glUniform4f(u, g, g, g, 0.5f); glDrawArrays(GL_TRIANGLE_STRIP, 0, 4); }
        glDisable(GL_BLEND);
        glUniform4f(u, col[0] / 255.f, col[1] / 255.f, col[2] / 255.f, 1.f); glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
        if (finish) { glFlush(); double a0 = now(); glFinish(); double dt = (now() - a0) * 1000; finSum += dt; if (dt > finMax) finMax = dt; }
        eglSwapBuffers(ed, s);
        double ts = now();
        for (int tries = 0; ; tries++) {
            XImage *im = XGetImage(d, w, 0, 0, W, H, AllPlanes, ZPixmap);
            if (!im) { puts("XGetImage failed"); return 1; }
            int rs = shift_of(im->red_mask), gs = shift_of(im->green_mask), bs = shift_of(im->blue_mask);
            long good = 0, total = 0, old[NPAL] = {0}, other = 0, blank = 0; int y0 = -1, y1 = -1, x0 = W, x1 = -1;
            unsigned long long sum = 14695981039346656037ULL;
            for (int y = 0; y < H; y += 2) for (int x = 0; x < W; x += 2) {
                unsigned long px = XGetPixel(im, x, y);
                int r = (px >> rs) & 255, g = (px >> gs) & 255, b = (px >> bs) & 255, hit = -1;
                total++; sum = (sum ^ (unsigned long long) (r << 16 | g << 8 | b)) * 1099511628211ULL;
                if (near(r, col[0]) && near(g, col[1]) && near(b, col[2])) { good++; continue; }
                if (!r && !g && !b) blank++;   /* the window's background, before its first frame */
                for (int k = 1; k < NPAL; k++) { const unsigned char *o = PAL[(fr + NPAL - k) % NPAL]; if (near(r, o[0]) && near(g, o[1]) && near(b, o[2])) { hit = k; break; } }
                if (hit > 0) old[hit]++; else other++;
                if (y0 < 0) y0 = y; y1 = y; if (x < x0) x0 = x; if (x > x1) x1 = x;
            }
            XDestroyImage(im);
            if (good == total) { double dt = (now() - ts) * 1000; showSum += dt; showN++; if (dt > showMax) showMax = dt; if (tries) late++; leftTorn = 0; break; }
            /* a torn frame stays in the window until the next one is copied: that is not this frame torn */
            if (leftTorn && sum == left) { if (tries < 30) { usleep(1000); continue; } never++; break; }
            if (blank == total && tries < 30) { usleep(1000); continue; }
            if (good == 0 && other == 0 && tries < 30) { usleep(1000); continue; }   /* not copied yet: whole window is an older frame */
            if (good == 0 && other == 0) { never++; leftTorn = 0; break; }
            torn++; left = sum; leftTorn = 1;
            double wf = 100.0 * (total - good) / total; wrongSum += wf; if (wf > wrongMax) wrongMax = wf;
            int best = 0; for (int k = 1; k < NPAL; k++) if (old[k] > old[best]) best = k;
            if (best && old[best] >= other) oldk[best]++; else tornOther++;
            if (shown++ < 4) printf("  frame %d torn: %.1f%% of it not this frame (rows %d-%d, cols %d-%d of %dx%d), is %s%d, tries %d\n", fr, wf, y0, y1, x0, x1, W, H,
                                    best && old[best] >= other ? "frame -" : "mid-draw ", best && old[best] >= other ? best : 0, tries);
            break;
        }
        double left = 1.0 / fps - (now() - tf); if (left > 0) usleep((useconds_t) (left * 1e6));
    }
    printf("%s: %d frames %dx%d, %d layers, %.1f fps: TORN %d (%.1f%%), avg %.1f%% max %.1f%% of a torn frame wrong; late %d, never shown %d; torn parts were frame", finish ? "finish" : "plain",
           frames, W, H, layers, frames / (now() - t0), torn, 100.0 * torn / frames, torn ? wrongSum / torn : 0.0, wrongMax, late, never);
    for (int k = 1; k < NPAL; k++) if (oldk[k]) printf(" -%d x%d", k, oldk[k]);
    printf(", mid-draw x%d\n", tornOther);
    printf("a frame was on the screen's buffer %.1f ms after its swap, on average (max %.1f ms; steps of 1 ms)\n", showN ? showSum / showN : 0.0, showMax);
    if (finish) printf("GPU finished a frame %.2f ms after it was sent, on average (max %.2f ms)\n", finSum / frames, finMax);
    return 0;
}
