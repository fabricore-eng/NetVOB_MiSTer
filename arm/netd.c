/*
 * netd.c — the runnable console-HPS ingest daemon.
 *
 * Connects (TCP) to the Pi provider's media socket, receives the MPEG-2 Program
 * Stream, demuxes it to video ES, and hands fixed-size sectors to the sd_* seam.
 * This is the live spine: Pi streamer -> [TCP] -> netd -> (sd_* -> FPGA decoder).
 *
 *   Pi provider ──TCP──► recv() ──► ni_feed ─demux─► ring ─sector─► sink
 *
 * OFF-TARGET (here): the sector sink writes the video ES to a file/stdout, so
 * netd is a runnable, testable TCP->ES pipe you can point at the Pi streamer (or
 * `nc -l`) to prove the network path end-to-end without the board.
 * ON-TARGET (console HPS): replace drain_to_file() with the sd_* service — each
 * sd_rd request calls ni_get_sector() and writes sd_buff_dout/sd_buff_wr (see
 * docs/findings.md §5). That swap is the only board-specific change.
 *
 * Plain C11 + POSIX sockets (builds on the dev host and on the ARM Linux). No
 * MiSTer/FPGA headers.
 */
#include "netingest.h"

#include <arpa/inet.h>
#include <errno.h>
#include <netdb.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define DEF_SECTOR 2048u
#define DEF_RING   (4u * 1024u * 1024u) /* 4 MiB >> a few seconds of <2 MB/s PS */
#define RECV_CHUNK 65536u

static int connect_tcp(const char *host, const char *port)
{
    struct addrinfo hints, *res = NULL, *ai;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    int rc = getaddrinfo(host, port, &hints, &res);
    if (rc != 0) {
        fprintf(stderr, "netd: getaddrinfo(%s:%s): %s\n", host, port, gai_strerror(rc));
        return -1;
    }
    int fd = -1;
    for (ai = res; ai; ai = ai->ai_next) {
        fd = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
        if (fd < 0) continue;
        if (connect(fd, ai->ai_addr, ai->ai_addrlen) == 0) break;
        close(fd);
        fd = -1;
    }
    freeaddrinfo(res);
    if (fd < 0)
        fprintf(stderr, "netd: could not connect to %s:%s: %s\n", host, port, strerror(errno));
    return fd;
}

/* Off-target consumer model: pull every whole sector currently queued and write
 * it out. On hardware this is driven by sd_rd requests, one sector per request. */
static void drain_to_file(netingest *ni, size_t sector, FILE *out)
{
    uint8_t *sec = (uint8_t *)malloc(sector);
    if (!sec) return;
    while (ni_get_sector(ni, sec) == sector) {
        if (out) fwrite(sec, 1, sector, out);
    }
    free(sec);
}

static void usage(const char *a0)
{
    fprintf(stderr,
        "usage: %s <host> <port> [--out FILE] [--sector N] [--ring BYTES]\n"
        "  Connects to the Pi provider's PS media socket and feeds the sd_* seam.\n"
        "  --out FILE   write the demuxed video ES here (default: discard)\n"
        "  --sector N   sd_* sector size (default %u)\n"
        "  --ring BYTES video-ES ring capacity (default %u)\n",
        a0, DEF_SECTOR, DEF_RING);
}

int main(int argc, char **argv)
{
    if (argc < 3) { usage(argv[0]); return 2; }
    const char *host = argv[1], *port = argv[2], *outpath = NULL;
    size_t sector = DEF_SECTOR, ring = DEF_RING;
    for (int i = 3; i < argc; i++) {
        if (!strcmp(argv[i], "--out") && i + 1 < argc) outpath = argv[++i];
        else if (!strcmp(argv[i], "--sector") && i + 1 < argc) sector = (size_t)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--ring") && i + 1 < argc) ring = (size_t)strtoul(argv[++i], NULL, 0);
        else { usage(argv[0]); return 2; }
    }
    if (sector == 0 || ring < sector) { fprintf(stderr, "netd: bad sector/ring\n"); return 2; }

    netingest ni;
    if (ni_init(&ni, ring, sector) != 0) { fprintf(stderr, "netd: ni_init failed\n"); return 1; }

    FILE *out = NULL;
    if (outpath) {
        out = fopen(outpath, "wb");
        if (!out) { fprintf(stderr, "netd: open %s: %s\n", outpath, strerror(errno)); ni_free(&ni); return 1; }
    }

    int fd = connect_tcp(host, port);
    if (fd < 0) { if (out) fclose(out); ni_free(&ni); return 1; }
    fprintf(stderr, "netd: connected to %s:%s (sector=%zu ring=%zu)\n", host, port, sector, ring);

    uint8_t *buf = (uint8_t *)malloc(RECV_CHUNK);
    if (!buf) { close(fd); if (out) fclose(out); ni_free(&ni); return 1; }

    for (;;) {
        /* Consume first so the ring has room (real path: the sd_* pull drains;
         * file-stub: the file always accepts, so the ring empties fully). */
        drain_to_file(&ni, sector, out);
        /* Backpressure (transport.md §4): never recv more than the ring can
         * hold. Video ES <= the PS bytes fed (nav/headers are stripped), so
         * capping recv at ni_room() guarantees ni_feed() can't overflow the
         * ring -> no silent ES drop that would desync the HW decoder. If the
         * ring is full (consumer not draining), don't recv at all so TCP
         * backpressures the sender; yield 1 ms to avoid a busy-spin. */
        size_t room = ni_room(&ni);
        if (room == 0) {
            struct timespec ts = { 0, 1000000L };  /* 1 ms */
            nanosleep(&ts, NULL);
            continue;
        }
        size_t want = room < RECV_CHUNK ? room : RECV_CHUNK;
        ssize_t n = recv(fd, buf, want, 0);
        if (n == 0) break;                 /* peer closed */
        if (n < 0) {
            if (errno == EINTR) continue;
            fprintf(stderr, "netd: recv: %s\n", strerror(errno));
            break;
        }
        ni_feed(&ni, buf, (size_t)n);
        if (ni_get_stats(&ni)->es_dropped > 0) {
            /* The room gate makes this impossible; if it fires the ring is
             * mis-sized vs the ES/PS ratio. Fail loudly rather than feed a
             * desynced (gap-corrupted) stream to the decoder. */
            fprintf(stderr,
                "netd: FATAL es_dropped=%llu — ring overflow despite the "
                "backpressure gate\n",
                (unsigned long long)ni_get_stats(&ni)->es_dropped);
            free(buf); close(fd); if (out) fclose(out); ni_free(&ni);
            return 2;
        }
    }
    drain_to_file(&ni, sector, out);       /* final flush of whole sectors */

    const ni_stats *s = ni_get_stats(&ni);
    fprintf(stderr,
        "netd: done. fed=%llu video_es=%llu to_ring=%llu dropped=%llu sectors=%llu underruns=%llu\n",
        (unsigned long long)s->bytes_fed, (unsigned long long)s->video_es_bytes,
        (unsigned long long)s->es_to_ring, (unsigned long long)s->es_dropped,
        (unsigned long long)s->sectors_out, (unsigned long long)s->sector_underruns);

    free(buf);
    close(fd);
    if (out) fclose(out);
    ni_free(&ni);
    return 0;
}
