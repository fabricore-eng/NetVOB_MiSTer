/*
 * test_realpipe.c — committed test for the REAL end-to-end ingest pipeline
 * (file chunks -> ringbuf -> ps_demux -> feeder/sd_* seam), linking the same
 * engine the developer harness (tools/realpipe.c) uses via realpipe_run().
 *
 * Two parts, both CI-safe:
 *
 *   (1) SYNTHETIC always-on: builds a small but realistic MPEG-2 Program Stream
 *       in memory (pack + system header + video PES + AC-3 private_stream_1 +
 *       nav private_stream_2 + padding), writes it to a TEMP file, runs the
 *       full pipeline on it, and asserts the SAME invariants the real run does:
 *       full demux consumption, video-ES byte conservation across ring+feeder,
 *       audio (private_stream_1) detection, nav+padding drop, ZERO FIFO
 *       overruns. This requires no fixture and must pass on a clean clone / CI.
 *
 *   (2) REAL fixture (OPTIONAL): if the copyrighted local VOB slice exists at
 *       the documented path (or $NETVOB_REAL_VOB), run the pipeline on it and
 *       assert the real-data invariants (conservation, no overrun, video +
 *       private_stream_1 audio present). If the fixture is ABSENT, print SKIP
 *       and pass — so clean clones and CI never depend on local data.
 *
 * The real fixture's bytes are NEVER embedded here; only its path is referenced.
 *
 * OFF-TARGET host logic: pure C11, no FPGA/MiSTer headers.
 */
#include "../ps_demux.h"
#include "../tools/realpipe.h"   /* shared engine contract (no layout drift) */

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* The engine lives in tools/realpipe.c (compiled without REALPIPE_MAIN so its
 * main() is excluded) and is linked into this test via realpipe_run(). */

/* ----------------------------------------------------- synthetic PS builder */

typedef struct { uint8_t *p; size_t len, cap; } buf;
static void bput(buf *b, uint8_t v) { assert(b->len < b->cap); b->p[b->len++] = v; }
static void bputn(buf *b, const uint8_t *s, size_t n) { for (size_t i=0;i<n;i++) bput(b,s[i]); }

static void put_pack(buf *b)
{
    bput(b,0x00); bput(b,0x00); bput(b,0x01); bput(b,0xBA);
    for (int i=0;i<9;i++) bput(b,0x44);
    bput(b,0xF8); /* low 3 bits == 0 -> no stuffing */
}
static void put_section(buf *b, uint8_t sid, const uint8_t *body, uint16_t n)
{
    bput(b,0x00); bput(b,0x00); bput(b,0x01); bput(b,sid);
    bput(b,(uint8_t)(n>>8)); bput(b,(uint8_t)(n&0xFF));
    bputn(b,body,n);
}
/* MPEG-2 PES, PTS-only (or none); payload appended verbatim. */
static void put_pes(buf *b, uint8_t sid, uint64_t pts,
                    const uint8_t *payload, uint16_t paylen)
{
    uint8_t ts_bytes = (pts != PS_PTS_NONE) ? 5 : 0;
    uint8_t pts_dts  = (pts != PS_PTS_NONE) ? 0x2 : 0x0;
    uint8_t hdr_len  = ts_bytes;
    uint16_t pes_len = (uint16_t)(3 + hdr_len + paylen);
    bput(b,0x00); bput(b,0x00); bput(b,0x01); bput(b,sid);
    bput(b,(uint8_t)(pes_len>>8)); bput(b,(uint8_t)(pes_len&0xFF));
    bput(b,0x80);                 /* flags1 '10' marker */
    bput(b,(uint8_t)(pts_dts<<6));/* flags2 PTS_DTS_flags */
    bput(b,hdr_len);
    if (pts != PS_PTS_NONE) {
        bput(b,(uint8_t)((0x2<<4)|(((pts>>30)&0x7)<<1)|1));
        bput(b,(uint8_t)((pts>>22)&0xFF));
        bput(b,(uint8_t)((((pts>>15)&0x7F)<<1)|1));
        bput(b,(uint8_t)((pts>>7)&0xFF));
        bput(b,(uint8_t)(((pts&0x7F)<<1)|1));
    }
    bputn(b,payload,paylen);
}

/* ------------------------------------------------------------ part 1: synth */

static void test_synthetic(void)
{
    /* Build ~enough video to exceed several sd_* sectors (2048 B) so the feeder
     * actually pulls + the FIFO low-waters repeatedly under the harness pacing.
     * 8 video PES of 4000 B each = 32000 B video ES (> 15 sectors). */
    static uint8_t storage[1 << 16];
    buf b = { storage, 0, sizeof(storage) };

    static uint8_t vpay[4000];
    for (size_t i = 0; i < sizeof(vpay); i++) vpay[i] = (uint8_t)(i * 7 + 1);
    const uint8_t ac3[]  = { 0x80, 0x01, 0x0B, 0x77, 0x12, 0x34 }; /* substream 0x80 */
    const uint8_t nav[]  = { 0,1,2,3,4,5,6,7 };
    const uint8_t pad[]  = { 0xFF,0xFF,0xFF,0xFF,0xFF,0xFF };
    const uint8_t sysh[] = { 0x80,0,0,0 };

    uint64_t expect_video = 0;
    put_pack(&b);
    put_section(&b, PS_SID_SYSTEM_HEADER, sysh, sizeof(sysh));
    for (int k = 0; k < 8; k++) {
        put_pes(&b, 0xE0, (k==0)?0x123456789ull:PS_PTS_NONE, vpay, sizeof(vpay));
        expect_video += sizeof(vpay);
        put_pes(&b, 0xBD, PS_PTS_NONE, ac3, sizeof(ac3)); /* AC-3 audio */
        if (k % 2 == 0) put_section(&b, PS_SID_PRIVATE_2, nav, sizeof(nav)); /* nav */
        if (k % 3 == 0) put_section(&b, PS_SID_PADDING, pad, sizeof(pad));   /* pad */
        if (k % 4 == 0) put_pack(&b);
    }

    /* Write to a temp file (realpipe_run takes a path). */
    char tmpl[] = "/tmp/netvob_synth_XXXXXX";
    int fd = mkstemp(tmpl);
    assert(fd >= 0);
    FILE *tf = fdopen(fd, "wb");
    assert(tf);
    assert(fwrite(b.p, 1, b.len, tf) == b.len);
    fclose(tf);

    pipe_result r;
    int rc = realpipe_run(tmpl, NULL, &r);
    remove(tmpl);
    assert(rc == 0);          /* file existed; pipeline ran */
    assert(r.ok);             /* all engine invariants held */

    assert(r.file_bytes == b.len);
    assert(r.demux_consumed == b.len);
    assert(r.stats.video_pes == 8);
    assert(r.stats.video_es_bytes == expect_video);
    assert(r.video_es_produced == expect_video);
    /* full conservation across ring + feeder */
    assert(r.video_es_through_feeder + r.fifo_residue + r.ring_residue
           == expect_video);
    /* the feeder actually pulled sectors (didn't trivially keep it all in ring) */
    assert(r.video_es_through_feeder > 0);
    assert(r.fifo_overruns == 0);
    /* audio detected via private_stream_1, nav + padding dropped */
    assert(r.stats.audio_pes_private1 == 8);
    assert(r.stats.audio_pes_mpeg == 0);
    assert(r.audio.pes_priv1_bytes > 0);
    assert(r.stats.nav_private2 == 4);   /* k=0,2,4,6 */
    assert(r.stats.padding_pes  == 3);   /* k=0,3,6 */
    /* one-shot substream histogram pegs the AC-3 substream 0x80 */
    assert(r.hist.sub_runs[0x80] == 8);
    /* PTS decoded from the first video PES */
    assert(r.last_video_pts == 0x123456789ull);

    printf("synthetic: conservation + audio detect + nav/pad drop + no overrun OK\n");
}

/* ------------------------------------------------------------- part 2: real */

static void test_real_fixture(void)
{
    const char *path = getenv("NETVOB_REAL_VOB");
    if (!path || !path[0])
        path = "/tmp/kungpow_slice/VIDEO_TS/VTS_10_1.slice.vob";

    pipe_result r;
    int rc = realpipe_run(path, NULL, &r);
    if (rc < 0) {
        printf("real-fixture: SKIP (no fixture at %s)\n", path);
        return;
    }
    /* Fixture present: assert the real-data invariants. */
    assert(r.ok);                                  /* engine invariants held */
    assert(r.file_bytes > 0);
    assert(r.demux_consumed == r.file_bytes);      /* full consumption       */
    assert(r.stats.video_pes > 0);                 /* it is a video PS       */
    assert(r.stats.video_es_bytes > 0);
    assert(r.video_es_produced == r.stats.video_es_bytes);
    assert(r.video_es_through_feeder + r.fifo_residue + r.ring_residue
           == r.video_es_produced);                /* conservation           */
    assert(r.fifo_overruns == 0);                  /* never overrun the FIFO */
    /* DVD VOB: audio rides private_stream_1 (AC-3), not MPEG audio. */
    assert(r.stats.audio_pes_private1 > 0);
    assert(r.audio.pes_priv1_bytes > 0);
    /* AC-3 substream 0x80 is always present on a DVD title. */
    assert(r.hist.sub_runs[0x80] > 0);
    printf("real-fixture: %s\n", path);
    printf("  file=%llu demux=%llu video_pes=%llu video_es=%llu\n",
           (unsigned long long)r.file_bytes,
           (unsigned long long)r.demux_consumed,
           (unsigned long long)r.stats.video_pes,
           (unsigned long long)r.stats.video_es_bytes);
    printf("  feeder=%llu +fifo=%llu +ring=%llu  fifo_overruns=%llu\n",
           (unsigned long long)r.video_es_through_feeder,
           (unsigned long long)r.fifo_residue,
           (unsigned long long)r.ring_residue,
           (unsigned long long)r.fifo_overruns);
    printf("  priv1 PES=%llu (bytes=%llu)  nav=%llu pad=%llu  AC-3@0x80 PES=%llu\n",
           (unsigned long long)r.stats.audio_pes_private1,
           (unsigned long long)r.audio.pes_priv1_bytes,
           (unsigned long long)r.stats.nav_private2,
           (unsigned long long)r.stats.padding_pes,
           (unsigned long long)r.hist.sub_runs[0x80]);
}

int main(void)
{
    test_synthetic();
    test_real_fixture();
    printf("ALL REALPIPE TESTS PASSED\n");
    return 0;
}
