// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: Apache-2.0
//
// The phone's end of a session in a gramarye (AOHP) env: what a session's terminal runs here,
// in place of the app, which runs in the env on a terminal the env's agent makes for it.
//
//   libtessera-relay.so <port> <hello file>
//
// The hello file holds the line the agent expects, less its last two words; they are this
// terminal's rows and columns (0 0 when the input isn't a terminal: the app gets pipes then).
// After that line both ends send frames: a type byte, the payload's length as four bytes (big
// endian), the payload.
//
//   to the agent    D bytes typed          W rows, columns (two bytes each): the size changed
//                   E the input ended
//   from the agent  D the app's output     X its exit status (four bytes, signed; -n: signal n)
//
// The terminal here is put in raw mode: the env's terminal does the line editing, echo and
// signals, as it would for a program run there by hand.

#define _GNU_SOURCE  // pipe2

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <termios.h>
#include <unistd.h>

#define MAX_HELLO (8u * 1024 * 1024)
#define MAX_FRAME (1u * 1024 * 1024)

static int winch_pipe[2] = {-1, -1};
static struct termios saved_tty;
static int tty_is_raw;

static void on_winch(int sig) {
    (void)sig;
    int e = errno;
    char c = 0;
    if (write(winch_pipe[1], &c, 1) < 0) { /* full: a change is already waiting */ }
    errno = e;
}

static void restore_tty(void) {
    if (tty_is_raw) tcsetattr(0, TCSANOW, &saved_tty);
    tty_is_raw = 0;
}

static void die(const char *what) {
    restore_tty();
    fprintf(stderr, "tessera: %s\r\n", what);
    exit(1);
}

static int write_all(int fd, const unsigned char *p, size_t n) {
    while (n > 0) {
        ssize_t w = write(fd, p, n);
        if (w < 0) {
            if (errno == EINTR) continue;
            if (errno == EAGAIN) {
                struct pollfd pfd = {.fd = fd, .events = POLLOUT};
                poll(&pfd, 1, 1000);
                continue;
            }
            return -1;
        }
        p += w;
        n -= (size_t)w;
    }
    return 0;
}

static int send_frame(int sock, char type, const unsigned char *data, uint32_t n) {
    unsigned char head[5] = {(unsigned char)type, (unsigned char)(n >> 24), (unsigned char)(n >> 16),
                             (unsigned char)(n >> 8), (unsigned char)n};
    if (write_all(sock, head, sizeof head) != 0) return -1;
    return n ? write_all(sock, data, n) : 0;
}

static int send_size(int sock) {
    struct winsize ws;
    if (ioctl(0, TIOCGWINSZ, &ws) != 0 || ws.ws_row == 0 || ws.ws_col == 0) return 0;
    unsigned char b[4] = {(unsigned char)(ws.ws_row >> 8), (unsigned char)ws.ws_row,
                          (unsigned char)(ws.ws_col >> 8), (unsigned char)ws.ws_col};
    return send_frame(sock, 'W', b, sizeof b);
}

// The whole file, without the newline it may end in; *len gets its length.
static unsigned char *read_hello(const char *path, size_t *len) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return NULL;
    struct stat st;
    if (fstat(fd, &st) != 0 || st.st_size <= 0 || (uint64_t)st.st_size > MAX_HELLO) { close(fd); return NULL; }
    unsigned char *buf = malloc((size_t)st.st_size + 64);
    size_t got = 0;
    while (buf && got < (size_t)st.st_size) {
        ssize_t r = read(fd, buf + got, (size_t)st.st_size - got);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) break;
        got += (size_t)r;
    }
    close(fd);
    if (!buf) return NULL;
    while (got > 0 && (buf[got - 1] == '\n' || buf[got - 1] == '\r')) got--;
    *len = got;
    return buf;
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s <port> <hello file>\n", argv[0]);
        return 2;
    }
    int port = atoi(argv[1]);
    size_t hello_len = 0;
    unsigned char *hello = read_hello(argv[2], &hello_len);
    // Read once: it holds the env's token.
    unlink(argv[2]);
    if (!hello || hello_len == 0 || port <= 0 || port > 65535) die("this launch has nothing to tell the env");

    int is_tty = isatty(0);
    struct winsize ws = {0};
    if (is_tty && ioctl(0, TIOCGWINSZ, &ws) != 0) ws.ws_row = ws.ws_col = 0;
    if (is_tty && (ws.ws_row == 0 || ws.ws_col == 0)) { ws.ws_row = 24; ws.ws_col = 80; }

    signal(SIGPIPE, SIG_IGN);
    int sock = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    struct sockaddr_in addr = {.sin_family = AF_INET, .sin_port = htons((uint16_t)port)};
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (sock < 0 || connect(sock, (struct sockaddr *)&addr, sizeof addr) != 0) {
        die("the env's Tessera agent doesn't answer (is the env running?)");
    }
    int one = 1;
    setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);

    int tail = snprintf((char *)hello + hello_len, 64, " %u %u\n", is_tty ? ws.ws_row : 0u, is_tty ? ws.ws_col : 0u);
    if (write_all(sock, hello, hello_len + (size_t)tail) != 0) die("couldn't reach the env's Tessera agent");
    memset(hello, 0, hello_len);
    free(hello);

    if (is_tty && tcgetattr(0, &saved_tty) == 0) {
        struct termios raw = saved_tty;
        cfmakeraw(&raw);
        if (tcsetattr(0, TCSANOW, &raw) == 0) tty_is_raw = 1;
        atexit(restore_tty);
    }
    if (is_tty && pipe2(winch_pipe, O_NONBLOCK | O_CLOEXEC) == 0) {
        struct sigaction sa;
        memset(&sa, 0, sizeof sa);
        sa.sa_handler = on_winch;
        sa.sa_flags = SA_RESTART;
        sigaction(SIGWINCH, &sa, NULL);
        // The size may have changed between asking for it and here.
        on_winch(SIGWINCH);
    }

    static unsigned char in[65536];
    unsigned char *buf = malloc(MAX_FRAME + 5 + sizeof in);
    if (!buf) die("out of memory");
    size_t have = 0;
    int input_open = 1, answered = 0;

    for (;;) {
        struct pollfd fds[3] = {
            {.fd = sock, .events = POLLIN},
            {.fd = input_open ? 0 : -1, .events = POLLIN},
            {.fd = winch_pipe[0], .events = POLLIN},
        };
        if (poll(fds, 3, -1) < 0) {
            if (errno == EINTR) continue;
            die("poll failed");
        }
        if (fds[2].revents & POLLIN) {
            char drain[64];
            while (read(winch_pipe[0], drain, sizeof drain) > 0) { }
            if (send_size(sock) != 0) die("lost the env's Tessera agent");
        }
        if (fds[1].revents & (POLLIN | POLLHUP | POLLERR)) {
            ssize_t r = read(0, in, sizeof in);
            if (r > 0) {
                if (send_frame(sock, 'D', in, (uint32_t)r) != 0) die("lost the env's Tessera agent");
            } else if (r == 0 || errno != EINTR) {
                // A terminal that is gone (its window closed) ends the session; input that
                // merely ended leaves the app running to its own end.
                if (is_tty) { restore_tty(); return 129; }
                input_open = 0;
                if (send_frame(sock, 'E', NULL, 0) != 0) die("lost the env's Tessera agent");
            }
        }
        if (fds[0].revents & (POLLIN | POLLHUP | POLLERR)) {
            ssize_t r = read(sock, buf + have, MAX_FRAME + 5 + sizeof in - have);
            if (r < 0 && errno == EINTR) continue;
            if (r <= 0) {
                die(answered ? "lost the env's Tessera agent"
                             : "the env's Tessera agent refused this launch (an agent older than this app? run gramarye/install.sh in the env)");
            }
            have += (size_t)r;
            size_t at = 0;
            while (have - at >= 5) {
                uint32_t n = ((uint32_t)buf[at + 1] << 24) | ((uint32_t)buf[at + 2] << 16) | ((uint32_t)buf[at + 3] << 8) | buf[at + 4];
                if (n > MAX_FRAME) die("the env's Tessera agent sent something this app doesn't understand");
                if (have - at < 5 + (size_t)n) break;
                answered = 1;
                unsigned char type = buf[at];
                unsigned char *data = buf + at + 5;
                if (type == 'D') {
                    if (write_all(1, data, n) != 0) { restore_tty(); return 129; }
                } else if (type == 'X' && n == 4) {
                    int32_t code = (int32_t)(((uint32_t)data[0] << 24) | ((uint32_t)data[1] << 16) | ((uint32_t)data[2] << 8) | data[3]);
                    restore_tty();
                    if (code < 0) return 128 + (-code & 127);
                    return code & 255;
                }
                at += 5 + (size_t)n;
            }
            memmove(buf, buf + at, have - at);
            have -= at;
        }
    }
}
