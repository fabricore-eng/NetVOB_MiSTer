/*
 * test_ps_demux.c — host unit tests for the PS demuxer (arm/ps_demux.c).
 *
 * Plain C11 + assert. Builds a crafted MPEG-2 Program Stream byte-for-byte in
 * memory (no files, no FPGA), feeds it to the demuxer, and asserts:
 *   - emitted video ES == concatenation of the video PES payloads, exactly;
 *   - PTS is decoded to the exact 33-bit value placed in a PES header;
 *   - DTS is decoded when PTS_DTS_flags == '11';
 *   - audio PES are detected/counted (MPEG audio 0xC0-0xDF AND AC-3 via
 *     private_stream_1 0xBD);
 *   - navigation (private_stream_2 0xBF) and padding (0xBE) are dropped;
 *   - the parser is feed-chunking invariant (1-byte feeds == one-shot feed).
 */
#include "../ps_demux.h"

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* ------------------------------------------------------------------ helpers */

/* Append a single byte to a growing buffer. */
typedef struct { uint8_t *p; size_t len, cap; } buf;

static void bput(buf *b, uint8_t v)
{
    assert(b->len < b->cap);
    b->p[b->len++] = v;
}
static void bputn(buf *b, const uint8_t *src, size_t n)
{
    for (size_t i = 0; i < n; i++) bput(b, src[i]);
}

/* Encode a 33-bit timestamp into 5 PES bytes with the given 4-bit prefix
 * (0x2 for PTS-only, 0x3 for the PTS in a PTS+DTS pair, 0x1 for DTS). */
static void put_ts(buf *b, uint8_t prefix4, uint64_t ts)
{
    bput(b, (uint8_t)((prefix4 << 4) | (((ts >> 30) & 0x7) << 1) | 1));
    bput(b, (uint8_t)((ts >> 22) & 0xFF));
    bput(b, (uint8_t)((((ts >> 15) & 0x7F) << 1) | 1));
    bput(b, (uint8_t)((ts >> 7) & 0xFF));
    bput(b, (uint8_t)(((ts & 0x7F) << 1) | 1));
}

/* Emit a 14-byte MPEG-2 pack header: 00 00 01 BA + 10 bytes (9 SCR/mux-rate
 * bytes + 1 stuffing-length byte). Total = 4 + 10 = 14. */
static void put_pack(buf *b)
{
    bput(b, 0x00); bput(b, 0x00); bput(b, 0x01); bput(b, 0xBA);
    /* 9 SCR/mux-rate bytes (content irrelevant to the demuxer) ... */
    for (int i = 0; i < 9; i++) bput(b, 0x44);
    /* 10th fixed byte: 5 reserved bits + 3-bit pack_stuffing_length = 0. */
    bput(b, 0xF8); /* low 3 bits == 0 -> no stuffing bytes */
}

/* Emit a length-delimited skip section: 00 00 01 <sid> <len16> <len bytes>. */
static void put_section(buf *b, uint8_t sid, const uint8_t *body, uint16_t n)
{
    bput(b, 0x00); bput(b, 0x00); bput(b, 0x01); bput(b, sid);
    bput(b, (uint8_t)(n >> 8)); bput(b, (uint8_t)(n & 0xFF));
    bputn(b, body, n);
}

/*
 * Emit an MPEG-2 PES packet:
 *   00 00 01 <sid> <PES_packet_length16> <flags1> <flags2> <hdr_data_len>
 *   [PTS][DTS] <payload...>
 *
 * pts/dts: pass PS_PTS_NONE to omit. (DTS requires PTS.)
 */
static void put_pes(buf *b, uint8_t sid, uint64_t pts, uint64_t dts,
                    const uint8_t *payload, uint16_t paylen)
{
    uint8_t ts_bytes = 0;
    uint8_t pts_dts_flags = 0x0;
    if (pts != PS_PTS_NONE && dts != PS_PTS_NONE) { pts_dts_flags = 0x3; ts_bytes = 10; }
    else if (pts != PS_PTS_NONE)                  { pts_dts_flags = 0x2; ts_bytes = 5;  }

    uint8_t hdr_data_len = ts_bytes;                 /* no other optional fields */
    uint16_t pes_len = (uint16_t)(3 + hdr_data_len + paylen); /* after length field */

    bput(b, 0x00); bput(b, 0x00); bput(b, 0x01); bput(b, sid);
    bput(b, (uint8_t)(pes_len >> 8)); bput(b, (uint8_t)(pes_len & 0xFF));
    bput(b, 0x80);                       /* flags1: '10' marker, all else 0 */
    bput(b, (uint8_t)(pts_dts_flags << 6)); /* flags2: PTS_DTS_flags, rest 0 */
    bput(b, hdr_data_len);
    if (pts_dts_flags == 0x2) put_ts(b, 0x2, pts);
    if (pts_dts_flags == 0x3) { put_ts(b, 0x3, pts); put_ts(b, 0x1, dts); }
    bputn(b, payload, paylen);
}

/* Video ES sink: append to a capture buffer. */
typedef struct { uint8_t bytes[4096]; size_t len; } es_capture;
static void es_sink(const uint8_t *data, size_t len, void *user)
{
    es_capture *c = (es_capture *)user;
    assert(c->len + len <= sizeof(c->bytes));
    memcpy(c->bytes + c->len, data, len);
    c->len += len;
}

/* ---------------------------------------------------------------- the test */

int main(void)
{
    /* Known timestamps. PTS is a full 33-bit value to exercise the high bits. */
    const uint64_t V_PTS = 0x123456789ull; /* 4886718345, < 2^33 */
    const uint64_t V_DTS = 0x123456700ull;
    const uint64_t A_PTS = 0x0AABBCCDDull & 0x1FFFFFFFFull;

    /* Distinct video payloads so we can verify exact concatenation. */
    const uint8_t v1[] = { 0x00, 0x00, 0x01, 0xB3, 0xDE, 0xAD }; /* seq hdr-ish */
    const uint8_t v2[] = { 0xBE, 0xEF, 0x00, 0x00, 0x01, 0xB8 }; /* contains    */
    const uint8_t v3[] = { 0x11, 0x22, 0x33, 0x44, 0x55 };       /* misleading  */
    const uint8_t a1[] = { 0xFF, 0xFB, 0x90, 0x00 };             /* MPEG audio  */
    const uint8_t ac3[] = { 0x0B, 0x77, 0x12, 0x34 };            /* AC-3 sync   */
    const uint8_t nav[] = { 0x00, 0x01, 0x02, 0x03, 0x04, 0x05 };/* nav body    */
    const uint8_t pad[] = { 0xFF, 0xFF, 0xFF, 0xFF };            /* padding     */
    const uint8_t sysh[] = { 0x80, 0x00, 0x00, 0x00 };           /* sys-hdr body*/

    uint8_t storage[1024];
    buf b = { storage, 0, sizeof(storage) };

    /* Build a realistic PS: pack, system header, then interleaved units. */
    put_pack(&b);
    put_section(&b, PS_SID_SYSTEM_HEADER, sysh, sizeof(sysh));
    put_pes(&b, 0xE0, V_PTS, V_DTS, v1, sizeof(v1));   /* video, PTS+DTS */
    put_section(&b, PS_SID_PRIVATE_2, nav, sizeof(nav)); /* nav -> drop */
    put_pes(&b, 0xC0, A_PTS, PS_PTS_NONE, a1, sizeof(a1)); /* MPEG audio */
    put_pes(&b, 0xE0, PS_PTS_NONE, PS_PTS_NONE, v2, sizeof(v2)); /* video, no PTS */
    put_section(&b, PS_SID_PADDING, pad, sizeof(pad));   /* padding -> drop */
    put_pes(&b, 0xBD, PS_PTS_NONE, PS_PTS_NONE, ac3, sizeof(ac3)); /* AC-3 */
    put_pack(&b);                                        /* a second pack */
    put_pes(&b, 0xE0, PS_PTS_NONE, PS_PTS_NONE, v3, sizeof(v3)); /* video */

    /* Expected concatenated video ES = v1 ++ v2 ++ v3. */
    uint8_t expect_es[64];
    size_t  expect_len = 0;
    memcpy(expect_es + expect_len, v1, sizeof(v1)); expect_len += sizeof(v1);
    memcpy(expect_es + expect_len, v2, sizeof(v2)); expect_len += sizeof(v2);
    memcpy(expect_es + expect_len, v3, sizeof(v3)); expect_len += sizeof(v3);

    /* --- Pass 1: feed the whole buffer in one shot. --- */
    es_capture cap1 = {0};
    ps_demux d1;
    ps_demux_init(&d1, es_sink, &cap1);
    size_t consumed = ps_demux_feed(&d1, b.p, b.len);
    assert(consumed == b.len);

    /* Video ES exactness. */
    assert(cap1.len == expect_len);
    assert(memcmp(cap1.bytes, expect_es, expect_len) == 0);

    const ps_stats *s1 = ps_demux_stats(&d1);
    assert(s1->video_pes == 3);
    assert(s1->audio_pes_mpeg == 1);
    assert(s1->audio_pes_private1 == 1);   /* AC-3 via private_stream_1 */
    assert(s1->nav_private2 == 1);         /* nav detected + dropped */
    assert(s1->padding_pes == 1);          /* padding detected + dropped */
    assert(s1->system_headers == 1);
    assert(s1->pack_headers == 2);
    assert(s1->video_es_bytes == expect_len);

    /* PTS/DTS exactness from the first video PES. */
    assert(ps_demux_last_video_pts(&d1) == V_PTS);
    assert(ps_demux_last_video_dts(&d1) == V_DTS);
    assert(ps_demux_last_audio_pts(&d1) == A_PTS);

    printf("pass 1 (one-shot): video ES %zu bytes, PTS 0x%llX, DTS 0x%llX OK\n",
           cap1.len, (unsigned long long)ps_demux_last_video_pts(&d1),
           (unsigned long long)ps_demux_last_video_dts(&d1));

    /* --- Pass 2: feed one byte at a time; results must be identical. --- */
    es_capture cap2 = {0};
    ps_demux d2;
    ps_demux_init(&d2, es_sink, &cap2);
    for (size_t i = 0; i < b.len; i++) {
        size_t c = ps_demux_feed(&d2, &b.p[i], 1);
        assert(c == 1);
    }
    assert(cap2.len == expect_len);
    assert(memcmp(cap2.bytes, expect_es, expect_len) == 0);

    const ps_stats *s2 = ps_demux_stats(&d2);
    assert(s2->video_pes == 3);
    assert(s2->audio_pes_mpeg == 1);
    assert(s2->audio_pes_private1 == 1);
    assert(s2->nav_private2 == 1);
    assert(s2->padding_pes == 1);
    assert(s2->system_headers == 1);
    assert(s2->pack_headers == 2);
    assert(ps_demux_last_video_pts(&d2) == V_PTS);
    assert(ps_demux_last_video_dts(&d2) == V_DTS);
    assert(ps_demux_last_audio_pts(&d2) == A_PTS);
    printf("pass 2 (byte-by-byte): identical results OK\n");

    /* --- Pass 3: a single PTS-only field decodes to the exact 33-bit value,
     *     independent of the rest of the stream. --- */
    {
        const uint64_t T = 0x1FFFFFFFFull;       /* all 33 bits set */
        uint8_t s2buf[64];
        buf bb = { s2buf, 0, sizeof(s2buf) };
        const uint8_t pay[] = { 0xAB, 0xCD };
        put_pes(&bb, 0xE0, T, PS_PTS_NONE, pay, sizeof(pay));
        es_capture cap = {0};
        ps_demux d;
        ps_demux_init(&d, es_sink, &cap);
        ps_demux_feed(&d, bb.p, bb.len);
        assert(ps_demux_last_video_pts(&d) == T);
        assert(cap.len == sizeof(pay));
        assert(memcmp(cap.bytes, pay, sizeof(pay)) == 0);
        printf("pass 3 (max PTS 0x%llX): exact decode OK\n",
               (unsigned long long)T);
    }

    /* --- Pass 4: NULL sink must still count video bytes (ring-buffer-only). */
    {
        ps_demux d;
        ps_demux_init(&d, NULL, NULL);
        ps_demux_feed(&d, b.p, b.len);
        assert(ps_demux_stats(&d)->video_es_bytes == expect_len);
        assert(ps_demux_stats(&d)->video_pes == 3);
        printf("pass 4 (NULL sink): byte accounting OK\n");
    }

    /* --- Pass 5: an UNBOUNDED video PES (PES_packet_length == 0) whose payload
     *     runs until the next start code. Payload deliberately contains 0x00
     *     bytes (but no false 00 00 01) to exercise the pending-zeros carry.
     *     Verified identical for one-shot and byte-by-byte feeds. --- */
    {
        uint8_t ub[128];
        buf ubb = { ub, 0, sizeof(ub) };
        /* unbounded video PES: 00 00 01 E0 00 00 80 00 00 <payload...> */
        const uint8_t upay[] = { 0xAA, 0x00, 0xBB, 0x00, 0x00, 0xCC, 0xDD };
        bput(&ubb, 0x00); bput(&ubb, 0x00); bput(&ubb, 0x01); bput(&ubb, 0xE0);
        bput(&ubb, 0x00); bput(&ubb, 0x00);   /* PES_packet_length = 0 */
        bput(&ubb, 0x80); bput(&ubb, 0x00); bput(&ubb, 0x00); /* no PTS */
        bputn(&ubb, upay, sizeof(upay));
        /* terminate with the next unit: a bounded video PES with payload v3 */
        put_pes(&ubb, 0xE0, PS_PTS_NONE, PS_PTS_NONE, v3, sizeof(v3));

        uint8_t expect2[64]; size_t el2 = 0;
        memcpy(expect2 + el2, upay, sizeof(upay)); el2 += sizeof(upay);
        memcpy(expect2 + el2, v3, sizeof(v3));     el2 += sizeof(v3);

        es_capture ca = {0};
        ps_demux da; ps_demux_init(&da, es_sink, &ca);
        ps_demux_feed(&da, ubb.p, ubb.len);
        assert(ca.len == el2);
        assert(memcmp(ca.bytes, expect2, el2) == 0);
        assert(ps_demux_stats(&da)->video_pes == 2);

        es_capture cb = {0};
        ps_demux db; ps_demux_init(&db, es_sink, &cb);
        for (size_t i = 0; i < ubb.len; i++) ps_demux_feed(&db, &ubb.p[i], 1);
        assert(cb.len == el2);
        assert(memcmp(cb.bytes, expect2, el2) == 0);
        assert(ps_demux_stats(&db)->video_pes == 2);
        printf("pass 5 (unbounded video PES, len==0): exact + chunk-invariant OK\n");
    }

    /* --- Pass 6 (REGRESSION): unbounded video PES whose payload ends in a 0x00
     *     IMMEDIATELY before the next unit's start-code prefix: on-wire
     *     '...77 00 | 00 00 01 ...'. The leading 0x00 is real payload; only the
     *     last two 0x00 + 0x01 are structure. The matched-prefix path must emit
     *     that payload 0x00. BUG (pre-fix): it reset pend_zeros to 0 without
     *     emitting the (pend_zeros-2) excess, dropping the payload byte and
     *     permanently desyncing the HW decoder. Fed as ONE chunk = the
     *     production path (netd 64KiB recv -> ni_feed); byte-by-byte happens to
     *     work via the chunk-exhausted path, which is why pass 5 missed it. --- */
    {
        uint8_t ub[128];
        buf ubb = { ub, 0, sizeof(ub) };
        bput(&ubb, 0x00); bput(&ubb, 0x00); bput(&ubb, 0x01); bput(&ubb, 0xE0);
        bput(&ubb, 0x00); bput(&ubb, 0x00);   /* PES_packet_length = 0 */
        bput(&ubb, 0x80); bput(&ubb, 0x00); bput(&ubb, 0x00); /* no PTS */
        /* payload ends in 77 00 — the 00 is payload, then the next unit's prefix */
        const uint8_t upay6[] = { 0x11, 0x22, 0x77, 0x00 };
        bputn(&ubb, upay6, sizeof(upay6));
        put_pes(&ubb, 0xE0, PS_PTS_NONE, PS_PTS_NONE, v3, sizeof(v3)); /* terminator */

        uint8_t expect6[64]; size_t el6 = 0;
        memcpy(expect6 + el6, upay6, sizeof(upay6)); el6 += sizeof(upay6);
        memcpy(expect6 + el6, v3, sizeof(v3));       el6 += sizeof(v3);

        es_capture cc = {0};
        ps_demux dc; ps_demux_init(&dc, es_sink, &cc);
        ps_demux_feed(&dc, ubb.p, ubb.len);   /* ONE chunk = production path */
        assert(cc.len == el6);
        assert(memcmp(cc.bytes, expect6, el6) == 0);  /* must include the payload 00 */
        assert(ps_demux_stats(&dc)->video_pes == 2);
        printf("pass 6 (payload 0x00 before prefix, one chunk): emitted OK\n");
    }

    /* --- Pass 7 (REGRESSION): a malformed/spliced bounded PES whose
     *     PES_header_data_length (200) exceeds its PES_packet_length (4) must
     *     RESYNC, not skip 200 bytes into and past the following valid PES
     *     (swallowing it). Common at DVD seek/splice points. --- */
    {
        uint8_t mb[128];
        buf mbb = { mb, 0, sizeof(mb) };
        /* corrupt video PES: 00 00 01 E0, len=4, flags1=80 flags2=00 hdr_len=200 */
        bput(&mbb, 0x00); bput(&mbb, 0x00); bput(&mbb, 0x01); bput(&mbb, 0xE0);
        bput(&mbb, 0x00); bput(&mbb, 0x04);              /* PES_packet_length = 4 */
        bput(&mbb, 0x80); bput(&mbb, 0x00); bput(&mbb, 0xC8); /* flags, hdr_len=200 */
        bput(&mbb, 0x00);                                /* 4th body byte */
        /* a VALID bounded video PES follows — its payload must survive */
        put_pes(&mbb, 0xE0, PS_PTS_NONE, PS_PTS_NONE, v3, sizeof(v3));

        es_capture cm = {0};
        ps_demux dm; ps_demux_init(&dm, es_sink, &cm);
        ps_demux_feed(&dm, mbb.p, mbb.len);
        assert(cm.len == sizeof(v3));                    /* not swallowed */
        assert(memcmp(cm.bytes, v3, sizeof(v3)) == 0);
        printf("pass 7 (malformed hdr_len>peslen resyncs, next PES survives): OK\n");
    }

    /* --- Pass 8 (REGRESSION): an unbounded video PES that ENDS the stream with
     *     trailing 0x00 payload bytes and no following start code. Those bytes
     *     are withheld in pend_zeros (they could have begun a 00 00 01 prefix
     *     across a feed); at clean EOF no prefix can complete, so they are real
     *     payload. ps_demux_finalize() must flush them, else the last frame's
     *     tail is truncated. BUG (pre-fix): no finalize -> up to 2 bytes lost. */
    {
        uint8_t ub[128];
        buf ubb = { ub, 0, sizeof(ub) };
        bput(&ubb, 0x00); bput(&ubb, 0x00); bput(&ubb, 0x01); bput(&ubb, 0xE0);
        bput(&ubb, 0x00); bput(&ubb, 0x00);   /* PES_packet_length = 0 */
        bput(&ubb, 0x80); bput(&ubb, 0x00); bput(&ubb, 0x00); /* no PTS */
        /* payload ends in two trailing 0x00 with NO following start code */
        const uint8_t upay8[] = { 0x11, 0x22, 0x00, 0x00 };
        bputn(&ubb, upay8, sizeof(upay8));

        es_capture ce = {0};
        ps_demux de; ps_demux_init(&de, es_sink, &ce);
        ps_demux_feed(&de, ubb.p, ubb.len);
        /* Before finalize the two trailing zeros are still withheld. */
        assert(ce.len == 2);
        assert(ce.bytes[0] == 0x11 && ce.bytes[1] == 0x22);
        /* Finalize flushes them -> full payload delivered. */
        ps_demux_finalize(&de);
        assert(ce.len == sizeof(upay8));
        assert(memcmp(ce.bytes, upay8, sizeof(upay8)) == 0);
        /* Idempotent: a second finalize emits nothing more. */
        ps_demux_finalize(&de);
        assert(ce.len == sizeof(upay8));

        /* And on a clean bounded stream (no withheld bytes) finalize is a
         * no-op (doesn't fabricate trailing zeros). */
        es_capture cf = {0};
        ps_demux df; ps_demux_init(&df, es_sink, &cf);
        uint8_t bnd[64]; buf bb = { bnd, 0, sizeof(bnd) };
        put_pes(&bb, 0xE0, PS_PTS_NONE, PS_PTS_NONE, v3, sizeof(v3));
        ps_demux_feed(&df, bb.p, bb.len);
        size_t before = cf.len;
        ps_demux_finalize(&df);
        assert(cf.len == before);
        printf("pass 8 (EOF finalize flushes withheld trailing zeros): OK\n");
    }

    printf("ALL TESTS PASSED\n");
    return 0;
}
