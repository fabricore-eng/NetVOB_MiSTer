/*
 * test_feeder.c — host unit tests for the sd_* sector-pull model (arm/feeder).
 *
 * Plain C11 + assert. Pins down the §4 end-to-end pull pacing:
 *   - the feeder pulls a sector ONLY while the modeled FIFO is at/below
 *     low-water (no eager filling);
 *   - it pulls EXACTLY as the FIFO drains (sectors delivered tracks drain);
 *   - the FIFO is NEVER overrun (fifo_overruns stays 0);
 *   - every byte is conserved: ring producer total == sd_* seam total == FIFO
 *     drained total once the stream is fully consumed;
 *   - a starved ring backpressures (no partial sector ever delivered).
 */
#include "../ringbuf.h"
#include "../feeder.h"

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* sd_* seam capture: record every sector byte, in order, that crosses the seam. */
typedef struct {
    uint8_t bytes[65536];
    size_t  len;
    uint64_t sector_calls;
    size_t   last_sector_len;
} seam_capture;

static void seam_sink(const uint8_t *data, size_t len, void *user)
{
    seam_capture *c = (seam_capture *)user;
    assert(c->len + len <= sizeof(c->bytes));
    memcpy(c->bytes + c->len, data, len);
    c->len += len;
    c->sector_calls++;
    c->last_sector_len = len;
}

/* ---- pull only while FIFO is at/below low-water; never overrun ----------- */
static void test_pull_pacing(void)
{
    enum { SECTOR = 16, FIFO_CAP = 64, FIFO_LOW = 16, CAP = 4096 };
    uint8_t store[CAP];
    ringbuf rb;
    assert(ringbuf_init(&rb, store, sizeof(store), SECTOR, FIFO_CAP) == 0);

    seam_capture cap = {0};
    feeder f;
    assert(feeder_init(&f, &rb, SECTOR, FIFO_CAP, FIFO_LOW, seam_sink, &cap) == 0);

    /* Stage a known byte stream in the ring: 64 sectors * 16 = 1024 bytes. */
    enum { NSECT = 64, TOTAL = NSECT * SECTOR };
    uint8_t src[TOTAL];
    for (int i = 0; i < TOTAL; i++) src[i] = (uint8_t)(i & 0xFF);

    /* Producer fills the ring (in chunks, like recv()). */
    size_t off = 0;
    while (off < TOTAL) {
        size_t room = ringbuf_free_space(&rb);
        if (room == 0) {
            /* Ring full: drain the FIFO a tick to let the feeder pull + free
             * ring space. This is the closed pacing loop. */
            feeder_tick(&f, SECTOR);
            continue;
        }
        size_t chunk = (TOTAL - off) < room ? (TOTAL - off) : room;
        assert(ringbuf_put(&rb, src + off, chunk) == chunk);
        off += chunk;
        /* Opportunistically run the pump: it must pull ONLY up to FIFO low. */
        feeder_pump(&f);
        /* Invariant after any pump: FIFO never exceeds capacity. */
        assert(feeder_fifo_level(&f) <= FIFO_CAP);
        /* And it never pulled past the point where the FIFO is above low-water
         * while more would fit: once above low-water the pump stops. The FIFO
         * level after a pump that started at/below low is at most
         * fifo_low + sector_size (one sector overshoot to clear low-water). */
        assert(feeder_fifo_level(&f) <= FIFO_LOW + SECTOR);
    }

    /* Drain the rest of the chain to completion, ticking until quiescent. */
    while (feeder_fifo_level(&f) > 0 || ringbuf_count(&rb) > 0) {
        feeder_tick(&f, SECTOR);
        assert(feeder_fifo_level(&f) <= FIFO_CAP);
    }

    /* ---- assertions ---- */
    /* FIFO was never overrun. */
    assert(f.fifo_overruns == 0);

    /* Every byte conserved end-to-end:
     *   produced into ring == crossed the sd_* seam == drained by decoder. */
    assert(rb.total_put == TOTAL);
    assert(f.bytes_pulled == (uint64_t)TOTAL);
    assert(cap.len == (size_t)TOTAL);
    assert(f.bytes_drained == (uint64_t)TOTAL);
    assert(f.sectors_pulled == (uint64_t)NSECT);
    assert(cap.sector_calls == (uint64_t)NSECT);

    /* Bytes crossed the seam in exact stream order. */
    assert(memcmp(cap.bytes, src, TOTAL) == 0);

    /* Every seam delivery was a FULL sector (never partial). */
    assert(cap.last_sector_len == SECTOR);
    printf("feeder: pull pacing, no overrun, byte conservation OK\n");
}

/* ---- pump pulls EXACTLY as the FIFO drains ------------------------------- */
static void test_pull_tracks_drain(void)
{
    enum { SECTOR = 32, FIFO_CAP = 128, FIFO_LOW = 32, CAP = 8192 };
    uint8_t store[CAP];
    ringbuf rb;
    assert(ringbuf_init(&rb, store, sizeof(store), 0, FIFO_CAP) == 0);

    feeder f;
    /* NULL sink: still accounts bytes through the modeled FIFO. */
    assert(feeder_init(&f, &rb, SECTOR, FIFO_CAP, FIFO_LOW, NULL, NULL) == 0);

    /* Fill the ring fully with sector-multiple data. */
    enum { NSECT = 100, TOTAL = NSECT * SECTOR };
    uint8_t src[TOTAL];
    memset(src, 0xA5, sizeof(src));

    size_t produced = 0;

    /* Prime: put as much as fits, pump once. The first pump fills the FIFO from
     * empty (0 <= low) up to just over low-water. */
    size_t room0 = ringbuf_free_space(&rb);
    size_t chunk0 = TOTAL < room0 ? TOTAL : room0;
    assert(ringbuf_put(&rb, src, chunk0) == chunk0);
    produced += chunk0;

    size_t s0 = feeder_pump(&f);
    /* From empty with low=32, sector=32, cap=128: it pulls until level > 32:
     * 32 -> not > 32 -> pull again -> 64 > 32 stop. So 2 sectors. */
    assert(s0 == 2);
    assert(feeder_fifo_level(&f) == 64);

    /* Now drain exactly one sector worth and pump: FIFO 64 -> 32 (<= low) ->
     * pull 1 sector -> 64. Exactly one sector pulled to replace one drained. */
    feeder_drain(&f, SECTOR);
    assert(feeder_fifo_level(&f) == 32);
    size_t s1 = feeder_pump(&f);
    assert(s1 == 1);
    assert(feeder_fifo_level(&f) == 64);

    /* Drain a fraction of a sector (16 < SECTOR): FIFO 64 -> 48 (> low) -> pump
     * pulls NOTHING (above low-water). The decoder hasn't drained enough yet. */
    feeder_drain(&f, 16);
    assert(feeder_fifo_level(&f) == 48);
    size_t s2 = feeder_pump(&f);
    assert(s2 == 0);                              /* no eager fill */
    assert(feeder_fifo_level(&f) == 48);

    /* Drain past low-water again -> exactly one sector replaces it. */
    feeder_drain(&f, SECTOR);                      /* 48 -> 16 (<= low) */
    assert(feeder_fifo_level(&f) == 16);
    size_t s3 = feeder_pump(&f);
    assert(s3 == 1);                               /* 16 -> 48 (> low) stop */
    assert(feeder_fifo_level(&f) == 48);

    /* Consume the remainder, refilling the ring as needed; conserve bytes. */
    while (produced < TOTAL || ringbuf_count(&rb) > 0 || feeder_fifo_level(&f) > 0) {
        if (produced < TOTAL) {
            size_t room = ringbuf_free_space(&rb);
            size_t chunk = (TOTAL - produced) < room ? (TOTAL - produced) : room;
            if (chunk) { assert(ringbuf_put(&rb, src + produced, chunk) == chunk); produced += chunk; }
        }
        feeder_tick(&f, SECTOR);
    }

    assert(f.fifo_overruns == 0);
    assert(f.bytes_pulled == (uint64_t)TOTAL);
    assert(f.bytes_drained == (uint64_t)TOTAL);
    assert(produced == TOTAL);
    printf("feeder: pull tracks drain (no eager fill), conservation OK\n");
}

/* ---- starved ring backpressures: never deliver a partial sector --------- */
static void test_starved_backpressure(void)
{
    enum { SECTOR = 64, FIFO_CAP = 256, FIFO_LOW = 64, CAP = 4096 };
    uint8_t store[CAP];
    ringbuf rb;
    assert(ringbuf_init(&rb, store, sizeof(store), 0, FIFO_CAP) == 0);

    seam_capture cap = {0};
    feeder f;
    assert(feeder_init(&f, &rb, SECTOR, FIFO_CAP, FIFO_LOW, seam_sink, &cap) == 0);

    /* Put LESS than one sector (e.g. 40 of 64) — TCP delivered a short read. */
    uint8_t partial[40];
    for (int i = 0; i < 40; i++) partial[i] = (uint8_t)i;
    assert(ringbuf_put(&rb, partial, sizeof(partial)) == sizeof(partial));

    /* FIFO empty (<= low) but ring can't supply a full sector. */
    size_t s = feeder_pump(&f);
    assert(s == 0);                       /* nothing delivered */
    assert(cap.sector_calls == 0);        /* seam never touched */
    assert(f.stall_starved >= 1);         /* recorded the starve */
    assert(ringbuf_count(&rb) == 40);     /* the 40 bytes still wait in the ring */
    assert(feeder_fifo_level(&f) == 0);

    /* Producer completes the sector (24 more) — now a full sector is available. */
    uint8_t rest[24];
    for (int i = 0; i < 24; i++) rest[i] = (uint8_t)(40 + i);
    assert(ringbuf_put(&rb, rest, sizeof(rest)) == sizeof(rest));

    s = feeder_pump(&f);
    assert(s == 1);                       /* now exactly one sector crosses */
    assert(cap.sector_calls == 1);
    assert(cap.len == SECTOR);
    /* The delivered sector is the two halves concatenated, byte-exact. */
    uint8_t expect[SECTOR];
    memcpy(expect, partial, 40);
    memcpy(expect + 40, rest, 24);
    assert(memcmp(cap.bytes, expect, SECTOR) == 0);
    assert(f.fifo_overruns == 0);
    printf("feeder: starved ring backpressure (no partial sector) OK\n");
}

/* ---- flush (seek) resets the FIFO; ring flush is ringbuf_clear ----------- */
static void test_flush(void)
{
    enum { SECTOR = 16, FIFO_CAP = 64, FIFO_LOW = 16, CAP = 1024 };
    uint8_t store[CAP];
    ringbuf rb;
    assert(ringbuf_init(&rb, store, sizeof(store), 0, FIFO_CAP) == 0);
    feeder f;
    assert(feeder_init(&f, &rb, SECTOR, FIFO_CAP, FIFO_LOW, NULL, NULL) == 0);

    uint8_t src[256];
    memset(src, 0x5A, sizeof(src));
    assert(ringbuf_put(&rb, src, sizeof(src)) == sizeof(src));
    feeder_pump(&f);
    assert(feeder_fifo_level(&f) > 0);

    /* Seek: flush FIFO + clear ring (transport §6). */
    feeder_flush(&f);
    ringbuf_clear(&rb);
    assert(feeder_fifo_level(&f) == 0);
    assert(ringbuf_is_empty(&rb));
    /* A pump on an empty ring with an empty FIFO does nothing. */
    assert(feeder_pump(&f) == 0);
    printf("feeder: flush on seek OK\n");
}

int main(void)
{
    test_pull_pacing();
    test_pull_tracks_drain();
    test_starved_backpressure();
    test_flush();
    printf("ALL FEEDER TESTS PASSED\n");
    return 0;
}
