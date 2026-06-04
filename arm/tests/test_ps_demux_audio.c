/*
 * test_ps_demux_audio.c — host unit test for AUDIO ES routing (arm/ps_demux).
 *
 * Plain C11 + assert. Crafts a PS in memory with interleaved video + audio PES
 * (MPEG audio 0xC0-0xDF and AC-3 via private_stream_1 0xBD) and asserts:
 *   - audio ES bytes == concatenation of the audio PES payloads, EXACTLY,
 *     and tagged with the correct originating stream_id;
 *   - video ES routing is UNCHANGED (byte-identical to the no-audio-sink case);
 *   - audio_es_bytes stat == total audio payload;
 *   - feed-chunking invariance (one-shot == byte-by-byte) for audio routing.
 *
 * This shares the PES-crafting layout with tests/test_ps_demux.c (kept local to
 * avoid coupling the two test binaries).
 */
#include "../ps_demux.h"

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* ------------------------------------------------------------------ helpers */
typedef struct { uint8_t *p; size_t len, cap; } buf;
static void bput(buf *b, uint8_t v) { assert(b->len < b->cap); b->p[b->len++] = v; }
static void bputn(buf *b, const uint8_t *src, size_t n)
{ for (size_t i = 0; i < n; i++) bput(b, src[i]); }

static void put_ts(buf *b, uint8_t prefix4, uint64_t ts)
{
    bput(b, (uint8_t)((prefix4 << 4) | (((ts >> 30) & 0x7) << 1) | 1));
    bput(b, (uint8_t)((ts >> 22) & 0xFF));
    bput(b, (uint8_t)((((ts >> 15) & 0x7F) << 1) | 1));
    bput(b, (uint8_t)((ts >> 7) & 0xFF));
    bput(b, (uint8_t)(((ts & 0x7F) << 1) | 1));
}

static void put_pack(buf *b)
{
    bput(b, 0x00); bput(b, 0x00); bput(b, 0x01); bput(b, 0xBA);
    for (int i = 0; i < 9; i++) bput(b, 0x44);
    bput(b, 0xF8);                                /* pack_stuffing_length = 0 */
}

static void put_pes(buf *b, uint8_t sid, uint64_t pts, uint64_t dts,
                    const uint8_t *payload, uint16_t paylen)
{
    uint8_t ts_bytes = 0, pts_dts_flags = 0x0;
    if (pts != PS_PTS_NONE && dts != PS_PTS_NONE) { pts_dts_flags = 0x3; ts_bytes = 10; }
    else if (pts != PS_PTS_NONE)                  { pts_dts_flags = 0x2; ts_bytes = 5;  }
    uint8_t hdr_data_len = ts_bytes;
    uint16_t pes_len = (uint16_t)(3 + hdr_data_len + paylen);
    bput(b, 0x00); bput(b, 0x00); bput(b, 0x01); bput(b, sid);
    bput(b, (uint8_t)(pes_len >> 8)); bput(b, (uint8_t)(pes_len & 0xFF));
    bput(b, 0x80);
    bput(b, (uint8_t)(pts_dts_flags << 6));
    bput(b, hdr_data_len);
    if (pts_dts_flags == 0x2) put_ts(b, 0x2, pts);
    if (pts_dts_flags == 0x3) { put_ts(b, 0x3, pts); put_ts(b, 0x1, dts); }
    bputn(b, payload, paylen);
}

/* Video ES sink. */
typedef struct { uint8_t bytes[4096]; size_t len; } es_capture;
static void es_sink(const uint8_t *data, size_t len, void *user)
{
    es_capture *c = (es_capture *)user;
    assert(c->len + len <= sizeof(c->bytes));
    memcpy(c->bytes + c->len, data, len);
    c->len += len;
}

/* Audio ES sink: capture bytes per stream_id class. */
typedef struct {
    uint8_t mpeg[4096];  size_t mpeg_len;   /* 0xC0-0xDF concatenated payload */
    uint8_t priv1[4096]; size_t priv1_len;  /* 0xBD concatenated payload      */
    uint8_t all[8192];   size_t all_len;    /* all audio, in stream order     */
    uint64_t calls;
} audio_capture;

static void audio_sink(uint8_t sid, const uint8_t *data, size_t len, void *user)
{
    audio_capture *c = (audio_capture *)user;
    c->calls++;
    assert(c->all_len + len <= sizeof(c->all));
    memcpy(c->all + c->all_len, data, len); c->all_len += len;
    if (sid == PS_SID_PRIVATE_1) {
        assert(c->priv1_len + len <= sizeof(c->priv1));
        memcpy(c->priv1 + c->priv1_len, data, len); c->priv1_len += len;
    } else {
        assert(sid >= PS_SID_AUDIO_BASE && sid <= PS_SID_AUDIO_LAST);
        assert(c->mpeg_len + len <= sizeof(c->mpeg));
        memcpy(c->mpeg + c->mpeg_len, data, len); c->mpeg_len += len;
    }
}

int main(void)
{
    /* Distinct payloads so concatenation is verifiable. */
    const uint8_t v1[]  = { 0x00, 0x00, 0x01, 0xB3, 0xDE, 0xAD };
    const uint8_t v2[]  = { 0x11, 0x22, 0x33, 0x44 };
    const uint8_t am1[] = { 0xFF, 0xFB, 0x90, 0x00, 0x01 };  /* MP2 frame-ish  */
    const uint8_t am2[] = { 0xFF, 0xFB, 0xAA, 0xBB };        /* MP2, 2nd PES   */
    const uint8_t ac1[] = { 0x80, 0x0B, 0x77, 0x12, 0x34 };  /* AC-3 (substr.) */
    const uint8_t ac2[] = { 0x80, 0x0B, 0x77, 0x56 };        /* AC-3, 2nd PES  */
    const uint8_t nav[] = { 0x00, 0x01, 0x02, 0x03 };

    const uint64_t A_PTS = 0x0AABBCCDDull & 0x1FFFFFFFFull;

    uint8_t storage[1024];
    buf b = { storage, 0, sizeof(storage) };

    /* Interleave video + both audio kinds + a nav packet (must not leak). */
    put_pack(&b);
    put_pes(&b, 0xE0, PS_PTS_NONE, PS_PTS_NONE, v1, sizeof(v1));   /* video */
    put_pes(&b, 0xC0, A_PTS,       PS_PTS_NONE, am1, sizeof(am1)); /* MP2   */
    put_pes(&b, 0xBD, PS_PTS_NONE, PS_PTS_NONE, ac1, sizeof(ac1)); /* AC-3  */
    put_pes(&b, 0xC1, PS_PTS_NONE, PS_PTS_NONE, am2, sizeof(am2)); /* MP2 (track 1) */
    put_pes(&b, 0xE0, PS_PTS_NONE, PS_PTS_NONE, v2, sizeof(v2));   /* video */
    /* nav private_stream_2: 00 00 01 BF <len> <body> -> dropped */
    bput(&b, 0x00); bput(&b, 0x00); bput(&b, 0x01); bput(&b, PS_SID_PRIVATE_2);
    bput(&b, (uint8_t)(sizeof(nav) >> 8)); bput(&b, (uint8_t)(sizeof(nav) & 0xFF));
    bputn(&b, nav, sizeof(nav));
    put_pes(&b, 0xBD, PS_PTS_NONE, PS_PTS_NONE, ac2, sizeof(ac2)); /* AC-3 (track 1) */

    /* Expected video ES = v1 ++ v2. */
    uint8_t exp_v[64]; size_t exp_v_len = 0;
    memcpy(exp_v + exp_v_len, v1, sizeof(v1)); exp_v_len += sizeof(v1);
    memcpy(exp_v + exp_v_len, v2, sizeof(v2)); exp_v_len += sizeof(v2);

    /* Expected audio (stream order) = am1 ++ ac1 ++ am2 ++ ac2. */
    uint8_t exp_a[64]; size_t exp_a_len = 0;
    memcpy(exp_a + exp_a_len, am1, sizeof(am1)); exp_a_len += sizeof(am1);
    memcpy(exp_a + exp_a_len, ac1, sizeof(ac1)); exp_a_len += sizeof(ac1);
    memcpy(exp_a + exp_a_len, am2, sizeof(am2)); exp_a_len += sizeof(am2);
    memcpy(exp_a + exp_a_len, ac2, sizeof(ac2)); exp_a_len += sizeof(ac2);

    /* Expected per-class. */
    uint8_t exp_mpeg[32]; size_t exp_mpeg_len = 0;
    memcpy(exp_mpeg + exp_mpeg_len, am1, sizeof(am1)); exp_mpeg_len += sizeof(am1);
    memcpy(exp_mpeg + exp_mpeg_len, am2, sizeof(am2)); exp_mpeg_len += sizeof(am2);
    uint8_t exp_priv1[32]; size_t exp_priv1_len = 0;
    memcpy(exp_priv1 + exp_priv1_len, ac1, sizeof(ac1)); exp_priv1_len += sizeof(ac1);
    memcpy(exp_priv1 + exp_priv1_len, ac2, sizeof(ac2)); exp_priv1_len += sizeof(ac2);

    /* --- Reference: NO audio sink -> video ES baseline (must be byte-equal to
     *     the audio-sink case). --- */
    es_capture ref_v = {0};
    ps_demux dref;
    ps_demux_init(&dref, es_sink, &ref_v);
    ps_demux_feed(&dref, b.p, b.len);
    assert(ref_v.len == exp_v_len);
    assert(memcmp(ref_v.bytes, exp_v, exp_v_len) == 0);
    /* audio counted but not routed; audio_es_bytes still accounts the payload. */
    assert(ps_demux_stats(&dref)->audio_es_bytes == exp_a_len);
    assert(ps_demux_stats(&dref)->video_es_bytes == exp_v_len);

    /* --- With an audio sink (one-shot). Video ES must be IDENTICAL. --- */
    es_capture vid = {0};
    audio_capture aud = {0};
    ps_demux d;
    ps_demux_init(&d, es_sink, &vid);
    ps_demux_set_audio_sink(&d, audio_sink, &aud);
    ps_demux_feed(&d, b.p, b.len);

    /* Video unchanged. */
    assert(vid.len == ref_v.len);
    assert(memcmp(vid.bytes, ref_v.bytes, vid.len) == 0);

    /* Audio ES == concatenated audio PES payloads, in stream order. */
    assert(aud.all_len == exp_a_len);
    assert(memcmp(aud.all, exp_a, exp_a_len) == 0);
    /* Per-class routing exact. */
    assert(aud.mpeg_len == exp_mpeg_len);
    assert(memcmp(aud.mpeg, exp_mpeg, exp_mpeg_len) == 0);
    assert(aud.priv1_len == exp_priv1_len);
    assert(memcmp(aud.priv1, exp_priv1, exp_priv1_len) == 0);

    /* Stats. */
    const ps_stats *st = ps_demux_stats(&d);
    assert(st->audio_es_bytes == exp_a_len);
    assert(st->audio_pes_mpeg == 2);          /* 0xC0 + 0xC1 */
    assert(st->audio_pes_private1 == 2);      /* two 0xBD */
    assert(st->video_pes == 2);
    assert(st->nav_private2 == 1);
    assert(aud.calls == 4);                   /* one per audio PES */

    printf("audio: one-shot routing exact; video ES unchanged OK\n");

    /* --- Byte-by-byte feed: identical audio + video routing. --- */
    es_capture vid2 = {0};
    audio_capture aud2 = {0};
    ps_demux d2;
    ps_demux_init(&d2, es_sink, &vid2);
    ps_demux_set_audio_sink(&d2, audio_sink, &aud2);
    for (size_t i = 0; i < b.len; i++) ps_demux_feed(&d2, &b.p[i], 1);

    assert(vid2.len == exp_v_len);
    assert(memcmp(vid2.bytes, exp_v, exp_v_len) == 0);
    assert(aud2.all_len == exp_a_len);
    assert(memcmp(aud2.all, exp_a, exp_a_len) == 0);
    assert(aud2.mpeg_len == exp_mpeg_len && memcmp(aud2.mpeg, exp_mpeg, exp_mpeg_len) == 0);
    assert(aud2.priv1_len == exp_priv1_len && memcmp(aud2.priv1, exp_priv1, exp_priv1_len) == 0);
    assert(ps_demux_stats(&d2)->audio_es_bytes == exp_a_len);
    printf("audio: byte-by-byte routing identical OK\n");

    printf("ALL PS_DEMUX AUDIO TESTS PASSED\n");
    return 0;
}
