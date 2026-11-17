// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: Apache-2.0
/* teartest.c for a Vulkan client: does a frame presented through the Vulkan driver's own window
 * system code (Mesa's WSI, which is also how Zink shows OpenGL) reach the X server's screen whole?
 *
 * Same method and same report as teartest.c: each frame is one flat colour, presented without
 * waiting for vsync where the driver allows it, then the window is read back from the X server and
 * the pixels that are not that frame's colour are counted. "layers" is how many times the window
 * is cleared per frame before its own colour, to make the GPU's work longer; a clear is cheap, so
 * it takes many more of them than teartest's blended layers ("finish" says how long the GPU took).
 *
 *   gcc -O2 -o /var/tmp/vkteartest vkteartest.c -lX11 -lvulkan
 *   python3 /var/tmp/in_session.py <pid of the app> -- /var/tmp/vkteartest plain|finish \
 *       [frames=300] [layers=40] [width=480] [height=800] [fps=30] [immediate|mailbox|fifo] [images]
 * "finish" waits for the GPU before each present, so no frame can tear then. The last argument is
 * the swapchain's present mode (default: immediate if the driver has it, then mailbox, then fifo);
 * Mesa treats them differently, and fifo is what an app that waits for vsync uses.
 */
#define VK_USE_PLATFORM_XLIB_KHR
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <vulkan/vulkan.h>
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
#define CK(x) do { VkResult r_ = (x); if (r_ < 0) { printf("%s failed: %d\n", #x, (int) r_); return 1; } } while (0)

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

    const char *iext[] = {"VK_KHR_surface", "VK_KHR_xlib_surface"}, *dext[] = {"VK_KHR_swapchain"};
    VkApplicationInfo ai = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "vkteartest", .apiVersion = VK_API_VERSION_1_0};
    VkInstanceCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &ai, .enabledExtensionCount = 2, .ppEnabledExtensionNames = iext};
    VkInstance inst; CK(vkCreateInstance(&ici, 0, &inst));
    uint32_t n = 8; VkPhysicalDevice pds[8]; CK(vkEnumeratePhysicalDevices(inst, &n, pds));
    if (!n) { puts("no Vulkan device"); return 1; }
    VkPhysicalDevice pd = pds[0];
    VkPhysicalDeviceProperties pp; vkGetPhysicalDeviceProperties(pd, &pp);
    VkXlibSurfaceCreateInfoKHR xci = {.sType = VK_STRUCTURE_TYPE_XLIB_SURFACE_CREATE_INFO_KHR, .dpy = d, .window = w};
    VkSurfaceKHR surf; CK(vkCreateXlibSurfaceKHR(inst, &xci, 0, &surf));
    uint32_t nq = 16, qi = ~0u; VkQueueFamilyProperties qf[16]; vkGetPhysicalDeviceQueueFamilyProperties(pd, &nq, qf);
    for (uint32_t i = 0; i < nq; i++) { VkBool32 ok = 0; vkGetPhysicalDeviceSurfaceSupportKHR(pd, i, surf, &ok); if (ok && (qf[i].queueFlags & VK_QUEUE_GRAPHICS_BIT)) { qi = i; break; } }
    if (qi == ~0u) { puts("no queue that can present to this window"); return 1; }
    float prio = 1;
    VkDeviceQueueCreateInfo qci = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueFamilyIndex = qi, .queueCount = 1, .pQueuePriorities = &prio};
    VkDeviceCreateInfo dci = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .queueCreateInfoCount = 1, .pQueueCreateInfos = &qci, .enabledExtensionCount = 1, .ppEnabledExtensionNames = dext};
    VkDevice dev; CK(vkCreateDevice(pd, &dci, 0, &dev));
    VkQueue q; vkGetDeviceQueue(dev, qi, 0, &q);

    VkSurfaceCapabilitiesKHR caps; CK(vkGetPhysicalDeviceSurfaceCapabilitiesKHR(pd, surf, &caps));
    uint32_t nf = 64; VkSurfaceFormatKHR fmts[64], fmt; CK(vkGetPhysicalDeviceSurfaceFormatsKHR(pd, surf, &nf, fmts));
    if (!nf) { puts("no surface format"); return 1; }
    fmt = fmts[0];   /* not an sRGB format: the colours have to arrive as they are written */
    for (uint32_t i = 0; i < nf; i++) if (fmts[i].format == VK_FORMAT_B8G8R8A8_UNORM || fmts[i].format == VK_FORMAT_R8G8B8A8_UNORM) { fmt = fmts[i]; break; }
    uint32_t nm = 8; VkPresentModeKHR modes[8], mode = VK_PRESENT_MODE_FIFO_KHR; CK(vkGetPhysicalDeviceSurfacePresentModesKHR(pd, surf, &nm, modes));
    const char *asked = argc > 7 ? argv[7] : "";
    for (uint32_t i = 0; i < nm; i++) if (modes[i] == VK_PRESENT_MODE_MAILBOX_KHR && (!*asked || !strcmp(asked, "mailbox"))) mode = modes[i];
    for (uint32_t i = 0; i < nm; i++) if (modes[i] == VK_PRESENT_MODE_IMMEDIATE_KHR && (!*asked || !strcmp(asked, "immediate"))) mode = modes[i];
    if (*asked && strcmp(asked, mode == VK_PRESENT_MODE_IMMEDIATE_KHR ? "immediate" : mode == VK_PRESENT_MODE_MAILBOX_KHR ? "mailbox" : "fifo")) { printf("no present mode %s here\n", asked); return 1; }
    VkExtent2D ext = caps.currentExtent.width == 0xffffffffu ? (VkExtent2D) {W, H} : caps.currentExtent;
    /* [images]: how many images to ask for (default: one more than the least the surface says). The
     * driver may give more, and how many it gives per present mode is printed. */
    uint32_t want = argc > 8 && atoi(argv[8]) > 0 ? (uint32_t) atoi(argv[8]) : caps.minImageCount + 1;
    if (caps.maxImageCount && want > caps.maxImageCount) want = caps.maxImageCount;
    VkCompositeAlphaFlagBitsKHR alpha = VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR;
    if (!(caps.supportedCompositeAlpha & alpha)) alpha = (caps.supportedCompositeAlpha & VK_COMPOSITE_ALPHA_INHERIT_BIT_KHR) ? VK_COMPOSITE_ALPHA_INHERIT_BIT_KHR : VK_COMPOSITE_ALPHA_PRE_MULTIPLIED_BIT_KHR;
    VkSwapchainCreateInfoKHR sci = {.sType = VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR, .surface = surf, .minImageCount = want, .imageFormat = fmt.format,
        .imageColorSpace = fmt.colorSpace, .imageExtent = ext, .imageArrayLayers = 1, .imageUsage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT,
        .imageSharingMode = VK_SHARING_MODE_EXCLUSIVE, .preTransform = caps.currentTransform, .compositeAlpha = alpha, .presentMode = mode, .clipped = VK_TRUE};
    VkSwapchainKHR sc; CK(vkCreateSwapchainKHR(dev, &sci, 0, &sc));
    uint32_t ni = 16; VkImage imgs[16]; CK(vkGetSwapchainImagesKHR(dev, sc, &ni, imgs));
    printf("%s, format %d, present mode %s, %u images (asked %u, surface's least %u), %ux%u\n", pp.deviceName, (int) fmt.format,
           mode == VK_PRESENT_MODE_IMMEDIATE_KHR ? "immediate" : mode == VK_PRESENT_MODE_MAILBOX_KHR ? "mailbox" : "fifo", ni, want, caps.minImageCount,
           ext.width, ext.height);

    VkAttachmentDescription att = {.format = fmt.format, .samples = VK_SAMPLE_COUNT_1_BIT, .loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR, .storeOp = VK_ATTACHMENT_STORE_OP_STORE,
        .stencilLoadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE, .stencilStoreOp = VK_ATTACHMENT_STORE_OP_DONT_CARE,
        .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED, .finalLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR};
    VkAttachmentReference ref = {0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
    VkSubpassDescription sp = {.pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS, .colorAttachmentCount = 1, .pColorAttachments = &ref};
    VkSubpassDependency dep = {.srcSubpass = VK_SUBPASS_EXTERNAL, .dstSubpass = 0, .srcStageMask = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,
        .dstStageMask = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, .dstAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT};
    VkRenderPassCreateInfo rci = {.sType = VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO, .attachmentCount = 1, .pAttachments = &att, .subpassCount = 1, .pSubpasses = &sp,
        .dependencyCount = 1, .pDependencies = &dep};
    VkRenderPass rp; CK(vkCreateRenderPass(dev, &rci, 0, &rp));
    VkImageView views[16]; VkFramebuffer fbs[16];
    for (uint32_t i = 0; i < ni; i++) {
        VkImageViewCreateInfo vci = {.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO, .image = imgs[i], .viewType = VK_IMAGE_VIEW_TYPE_2D, .format = fmt.format,
            .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1}};
        CK(vkCreateImageView(dev, &vci, 0, &views[i]));
        VkFramebufferCreateInfo fci = {.sType = VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO, .renderPass = rp, .attachmentCount = 1, .pAttachments = &views[i],
            .width = ext.width, .height = ext.height, .layers = 1};
        CK(vkCreateFramebuffer(dev, &fci, 0, &fbs[i]));
    }
    VkCommandPoolCreateInfo pci = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, .queueFamilyIndex = qi};
    VkCommandPool pool; CK(vkCreateCommandPool(dev, &pci, 0, &pool));
    VkCommandBufferAllocateInfo cai = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
    VkCommandBuffer cb; CK(vkAllocateCommandBuffers(dev, &cai, &cb));
    VkSemaphoreCreateInfo smi = {.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
    VkSemaphore acq, done; CK(vkCreateSemaphore(dev, &smi, 0, &acq)); CK(vkCreateSemaphore(dev, &smi, 0, &done));
    VkFenceCreateInfo fni = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    VkFence fence; CK(vkCreateFence(dev, &fni, 0, &fence));

    unsigned long long left = 0; int leftTorn = 0;   /* the torn picture the frame before left in the window, if it did */
    int torn = 0, late = 0, never = 0, shown = 0, oldk[NPAL] = {0}, tornOther = 0, pending = 0;
    double wrongSum = 0, wrongMax = 0, t0 = now(), finSum = 0, finMax = 0, showSum = 0, showMax = 0; int showN = 0;
    for (int fr = 0; fr < frames; fr++) {
        double tf = now();
        const unsigned char *col = PAL[fr % NPAL];
        /* the frame before may still be on the GPU: its commands are recorded over only when it is done */
        if (pending) { CK(vkWaitForFences(dev, 1, &fence, VK_TRUE, UINT64_MAX)); CK(vkResetFences(dev, 1, &fence)); pending = 0; }
        uint32_t ii = 0; CK(vkAcquireNextImageKHR(dev, sc, UINT64_MAX, acq, VK_NULL_HANDLE, &ii));
        VkCommandBufferBeginInfo bi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
        CK(vkResetCommandBuffer(cb, 0)); CK(vkBeginCommandBuffer(cb, &bi));
        VkClearValue cv = {.color = {.float32 = {0.10f, 0.10f, 0.10f, 1.f}}};
        VkRenderPassBeginInfo rbi = {.sType = VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO, .renderPass = rp, .framebuffer = fbs[ii], .renderArea = {{0, 0}, ext},
            .clearValueCount = 1, .pClearValues = &cv};
        vkCmdBeginRenderPass(cb, &rbi, VK_SUBPASS_CONTENTS_INLINE);
        VkClearRect rect = {{{0, 0}, ext}, 0, 1};
        for (int l = 0; l <= layers; l++) {
            float g = 0.10f + 0.02f * (l % 3);
            VkClearAttachment ca = {.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT, .colorAttachment = 0, .clearValue = {.color = {.float32 = {g, g, g, 1.f}}}};
            if (l == layers) { ca.clearValue.color.float32[0] = col[0] / 255.f; ca.clearValue.color.float32[1] = col[1] / 255.f; ca.clearValue.color.float32[2] = col[2] / 255.f; }
            vkCmdClearAttachments(cb, 1, &ca, 1, &rect);
        }
        vkCmdEndRenderPass(cb); CK(vkEndCommandBuffer(cb));
        VkPipelineStageFlags stage = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT;
        VkSubmitInfo si = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .waitSemaphoreCount = 1, .pWaitSemaphores = &acq, .pWaitDstStageMask = &stage,
            .commandBufferCount = 1, .pCommandBuffers = &cb, .signalSemaphoreCount = 1, .pSignalSemaphores = &done};
        CK(vkQueueSubmit(q, 1, &si, fence)); pending = 1;
        if (finish) { double a0 = now(); CK(vkWaitForFences(dev, 1, &fence, VK_TRUE, UINT64_MAX)); double dt = (now() - a0) * 1000; finSum += dt; if (dt > finMax) finMax = dt; }
        VkPresentInfoKHR pi = {.sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR, .waitSemaphoreCount = 1, .pWaitSemaphores = &done, .swapchainCount = 1, .pSwapchains = &sc, .pImageIndices = &ii};
        CK(vkQueuePresentKHR(q, &pi));
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
    printf("a frame was on the screen's buffer %.1f ms after its present, on average (max %.1f ms; steps of 1 ms)\n", showN ? showSum / showN : 0.0, showMax);
    if (finish) printf("GPU finished a frame %.2f ms after it was sent, on average (max %.2f ms)\n", finSum / frames, finMax);
    vkDeviceWaitIdle(dev);
    return 0;
}
