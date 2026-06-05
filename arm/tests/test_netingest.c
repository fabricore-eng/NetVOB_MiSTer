/*
 * test_netingest.c — the network-ingest glue (netingest.c) end-to-end, off-target.
 *
 * Asserts the live spine seam invariant: PS bytes fed in arbitrary chunks (the
 * recv() model) come out of ni_get_sector() as EXACTLY the demuxed video ES,
 * byte-for-byte, in order, with audio/nav/padding dropped and no ring overrun
 * when the consumer keeps up. Plus: backpressure accounting on a too-small ring,
 * underrun semantics, and flush.
 */
#include "../netingest.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

/* ---- tiny PS builder (same shape as test_ps_demux.c) -------------------- */
typedef struct { uint8_t *p; size_t len, cap; } buf;
static uint8_t storage[1 << 16];
static void bput(buf *b, uint8_t v) { assert(b->len < b->cap); b->p[b->len++] = v; }
static void bputn(buf *b, const uint8_t *s, size_t n) { for (size_t i = 0; i < n; i++) bput(b, s[i]); }

static void put_pack(buf *b)
{
    bput(b, 0x00); bput(b, 0x00); bput(b, 0x01); bput(b, 0xBA);
    for (int i = 0; i < 9; i++) bput(b, 0x44);
    bput(b, 0xF8); /* reserved + pack_stuffing_length=0 */
}

static void put_section(buf *b, uint8_t sid, const uint8_t *body, uint16_t n)
{
    bput(b, 0x00); bput(b, 0x00); bput(b, 0x01); bput(b, sid);
    bput(b, (uint8_t)(n >> 8)); bput(b, (uint8_t)(n & 0xFF));
    bputn(b, body, n);
}

/* Minimal PES: no PTS/DTS (flags2=0x00, hdr_data_len=0) so payload == ES. */
static void put_pes(buf *b, uint8_t sid, const uint8_t *payload, uint16_t paylen)
{
    bput(b, 0x00); bput(b, 0x00); bput(b, 0x01); bput(b, sid);
    uint16_t pes_len = (uint16_t)(3 + 0 + paylen); /* flags1+flags2+hdrlen + payload */
    bput(b, (uint8_t)(pes_len >> 8)); bput(b, (uint8_t)(pes_len & 0xFF));
    bput(b, 0x80); /* '10' marker + flags1 */
    bput(b, 0x00); /* flags2: no PTS/DTS */
    bput(b, 0x00); /* PES_header_data_length = 0 */
    bputn(b, payload, paylen);
}

/* Distinct, position-encoding payloads so a misorder/dup is caught. */
static void fill(uint8_t *dst, size_t n, uint8_t seed)
{
    for (size_t i = 0; i < n; i++) dst[i] = (uint8_t)(seed + i);
}

int main(void)
{
    /* --- build a realistic PS + the expected video ES (payload concat) --- */
    buf b = { storage, 0, sizeof(storage) };
    uint8_t expect[4096]; size_t elen = 0;

    uint8_t v1[200], v2[333], v3[64], a1[128], ac3[96], nav[40];
    fill(v1, sizeof(v1), 0x10); fill(v2, sizeof(v2), 0x40);
    fill(v3, sizeof(v3), 0x90); fill(a1, sizeof(a1), 0x01);
    fill(ac3, sizeof(ac3), 0x02); fill(nav, sizeof(nav), 0x03);

    put_pack(&b);
    put_section(&b, 0xBB, a1, 8);                 /* system header (dropped) */
    put_pes(&b, 0xE0, v1, sizeof(v1));            /* video  */
    memcpy(expect + elen, v1, sizeof(v1)); elen += sizeof(v1);
    put_pes(&b, 0xC0, a1, sizeof(a1));            /* MPEG audio (dropped from video ES) */
    put_pes(&b, 0xBF, nav, sizeof(nav));          /* nav private_stream_2 (dropped) */
    put_pes(&b, 0xE0, v2, sizeof(v2));            /* video  */
    memcpy(expect + elen, v2, sizeof(v2)); elen += sizeof(v2);
    put_pes(&b, 0xBD, ac3, sizeof(ac3));          /* AC-3 private_stream_1 (dropped) */
    put_pes(&b, 0xBE, nav, sizeof(nav));          /* padding (dropped) */
    put_pack(&b);
    put_pes(&b, 0xE0, v3, sizeof(v3));            /* video  */
    memcpy(expect + elen, v3, sizeof(v3)); elen += sizeof(v3);

    /* === Test 1: chunked feed, full-sector pulls == demuxed video ES === */
    const size_t SECTOR = 16;
    netingest ni;
    assert(ni_init(&ni, 8192, SECTOR) == 0);

    uint8_t out[4096]; size_t olen = 0;
    /* feed in deliberately awkward chunks (recv() model), draining sectors as
     * room allows so the ring never overruns. */
    for (size_t off = 0; off < b.len; ) {
        size_t chunk = 7 + (off % 13);           /* 7..19 bytes */
        if (chunk > b.len - off) chunk = b.len - off;
        ni_feed(&ni, b.p + off, chunk);
        off += chunk;
        uint8_t sec[16];
        while (ni_get_sector(&ni, sec) == SECTOR) {
            memcpy(out + olen, sec, SECTOR); olen += SECTOR;
        }
    }
    /* drain remaining whole sectors */
    { uint8_t sec[16]; while (ni_get_sector(&ni, sec) == SECTOR) { memcpy(out + olen, sec, SECTOR); olen += SECTOR; } }

    const ni_stats *s = ni_get_stats(&ni);
    /* every video payload byte reached the ring; nothing dropped */
    assert(s->video_es_bytes == elen);
    assert(s->es_to_ring == elen);
    assert(s->es_dropped == 0);
    /* full sectors out + the partial tail still queued == all the ES */
    size_t whole = (elen / SECTOR) * SECTOR;
    assert(olen == whole);
    assert(ni_available(&ni) == elen - whole);
    /* and the bytes are byte-exact + in order vs the demuxed video ES */
    assert(memcmp(out, expect, whole) == 0);
    printf("netingest: chunked recv -> full-sector pull == demuxed video ES (%zu bytes, %llu sectors) OK\n",
           whole, (unsigned long long)s->sectors_out);

    /* underrun: pulling now (only a partial sector queued) returns 0, ring intact */
    { uint8_t sec[16]; size_t before = ni_available(&ni);
      assert(ni_get_sector(&ni, sec) == 0);
      assert(ni_available(&ni) == before);
      assert(ni_get_stats(&ni)->sector_underruns >= 1); }
    printf("netingest: underrun pull returns 0, ring untouched OK\n");

    /* flush drops the queued tail */
    ni_flush(&ni);
    assert(ni_available(&ni) == 0);
    printf("netingest: flush clears the ES ring OK\n");
    ni_free(&ni);

    /* === Test 2: backpressure — a ring too small to hold the ES, with NO
     * draining, must COUNT the overflow (es_dropped) and never clobber. === */
    netingest ni2;
    assert(ni_init(&ni2, 64, SECTOR) == 0);       /* 64-byte ring < total ES */
    ni_feed(&ni2, b.p, b.len);                     /* feed everything, drain nothing */
    const ni_stats *s2 = ni_get_stats(&ni2);
    assert(s2->video_es_bytes == elen);            /* demux still saw all of it */
    assert(s2->es_to_ring <= 64);                  /* ring capped */
    assert(s2->es_dropped == elen - s2->es_to_ring);
    assert(s2->es_to_ring + s2->es_dropped == elen); /* conservation across the seam */
    /* whatever DID make it into the ring is still a correct prefix of the ES */
    { uint8_t sec[16]; size_t got = 0; uint8_t pre[64];
      while (ni_get_sector(&ni2, sec) == SECTOR) { memcpy(pre + got, sec, SECTOR); got += SECTOR; }
      assert(memcmp(pre, expect, got) == 0); }
    printf("netingest: small-ring backpressure counted (es_to_ring=%llu dropped=%llu), prefix intact OK\n",
           (unsigned long long)s2->es_to_ring, (unsigned long long)s2->es_dropped);
    ni_free(&ni2);

    /* === Test 3: the netd recv-gate invariant — capping each feed at
     * ni_room() PS bytes (video ES <= PS) means the ring NEVER overflows, so
     * es_dropped stays 0 even with a ring far smaller than the total ES. This
     * is exactly netd.c's backpressure loop (drain, then recv<=ni_room()); the
     * gate paces the producer instead of dropping. === */
    netingest ni3;
    assert(ni_init(&ni3, 256, SECTOR) == 0);       /* 256-byte ring << total ES */
    uint8_t got3[4096]; size_t glen = 0;
    for (size_t off = 0; off < b.len; ) {
        /* drain first (consumer), exactly like netd's drain_to_file */
        uint8_t sec[16];
        while (ni_get_sector(&ni3, sec) == SECTOR) { memcpy(got3 + glen, sec, SECTOR); glen += SECTOR; }
        size_t room = ni_room(&ni3);
        assert(room > 0);                          /* always true right after a drain */
        /* Offer the WHOLE remaining stream each step (recv would happily give a
         * big chunk); the gate alone keeps it from overflowing the tiny ring.
         * Without the `want > room` cap this single feed drops -> es_dropped>0. */
        size_t want = b.len - off;
        if (want > room) want = room;              /* the gate: recv <= ni_room() */
        ni_feed(&ni3, b.p + off, want);
        off += want;
        assert(ni_get_stats(&ni3)->es_dropped == 0);  /* gate => never drops */
    }
    { uint8_t sec[16]; while (ni_get_sector(&ni3, sec) == SECTOR) { memcpy(got3 + glen, sec, SECTOR); glen += SECTOR; } }
    const ni_stats *s3 = ni_get_stats(&ni3);
    assert(s3->es_dropped == 0);                   /* the headline invariant */
    assert(s3->video_es_bytes == elen);
    size_t whole3 = (elen / SECTOR) * SECTOR;
    assert(glen == whole3);
    assert(memcmp(got3, expect, whole3) == 0);     /* ES byte-exact, in order */
    printf("netingest: room-gated feed (netd backpressure) never drops, ES intact OK\n");
    ni_free(&ni3);

    printf("ALL NETINGEST TESTS PASSED\n");
    return 0;
}
