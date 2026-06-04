/*
 * test_ringbuf.c — host unit tests for the SPSC byte ring buffer (arm/ringbuf).
 *
 * Plain C11 + assert. Pins down EXACT behaviour of the §4 ARM ring:
 *   - fill-to-capacity and exact empty/full reporting;
 *   - all-or-nothing put rejects an overrun (writes nothing, no clobber);
 *   - all-or-nothing get rejects an underrun (reads nothing);
 *   - best-effort get under-runs (returns < requested) and is reported;
 *   - wrap correctness: bytes survive a head/tail wrap byte-for-byte;
 *   - low/high water-mark transitions as the ring fills and drains.
 */
#include "../ringbuf.h"

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* ---- capacity / empty / full / all-or-nothing overrun + underrun -------- */
static void test_capacity_and_alln(void)
{
    uint8_t store[8];
    ringbuf rb;
    assert(ringbuf_init(&rb, store, sizeof(store), 2, 6) == 0);

    assert(ringbuf_capacity(&rb) == 8);
    assert(ringbuf_is_empty(&rb));
    assert(!ringbuf_is_full(&rb));
    assert(ringbuf_count(&rb) == 0);
    assert(ringbuf_free_space(&rb) == 8);

    /* Fill exactly to capacity. */
    uint8_t in[8] = { 1, 2, 3, 4, 5, 6, 7, 8 };
    assert(ringbuf_put(&rb, in, 8) == 8);
    assert(ringbuf_is_full(&rb));
    assert(!ringbuf_is_empty(&rb));
    assert(ringbuf_count(&rb) == 8);
    assert(ringbuf_free_space(&rb) == 0);

    /* Overrun: all-or-nothing put of even 1 more byte writes NOTHING. */
    uint8_t extra = 0xFF;
    assert(ringbuf_put(&rb, &extra, 1) == 0);
    assert(rb.overrun_events == 1);
    assert(ringbuf_count(&rb) == 8);            /* unchanged: no clobber */

    /* Read everything back, exactly. */
    uint8_t out[8] = {0};
    assert(ringbuf_get_exact(&rb, out, 8) == 8);
    assert(memcmp(out, in, 8) == 0);
    assert(ringbuf_is_empty(&rb));

    /* Underrun: all-or-nothing get of 1 byte from an empty ring reads NOTHING. */
    uint8_t one = 0;
    assert(ringbuf_get_exact(&rb, &one, 1) == 0);
    assert(rb.underrun_events == 1);

    /* Best-effort get under-runs: ask for 4, get the 3 present, reported. */
    assert(ringbuf_put(&rb, in, 3) == 3);
    uint8_t out2[4] = {0};
    size_t g = ringbuf_get(&rb, out2, 4);
    assert(g == 3);
    assert(memcmp(out2, in, 3) == 0);
    assert(rb.underrun_events == 2);            /* short get is an underrun */

    /* Byte conservation across the test. */
    assert(rb.total_put == 8 + 3);
    assert(rb.total_got == 8 + 3);
    printf("ringbuf: capacity/full/empty/overrun/underrun OK\n");
}

/* ---- wrap correctness: force head and tail to wrap, verify byte-exact ---- */
static void test_wrap(void)
{
    uint8_t store[8];
    ringbuf rb;
    assert(ringbuf_init(&rb, store, sizeof(store), 0, 8) == 0);

    /* Advance head/tail near the end: put 6, drain 6. tail/head now at 6. */
    uint8_t a[6] = { 10, 11, 12, 13, 14, 15 };
    assert(ringbuf_put(&rb, a, 6) == 6);
    uint8_t tmp[6];
    assert(ringbuf_get_exact(&rb, tmp, 6) == 6);
    assert(ringbuf_is_empty(&rb));
    assert(rb.head == 6 && rb.tail == 6);

    /* Now put 5 bytes: this must wrap (2 at [6,7], 3 at [0,1,2]). */
    uint8_t b[5] = { 20, 21, 22, 23, 24 };
    assert(ringbuf_put(&rb, b, 5) == 5);
    assert(ringbuf_count(&rb) == 5);
    assert(rb.head == 3);                        /* (6+5) % 8 */

    /* Read it back across the wrap; must equal b exactly. */
    uint8_t out[5] = {0};
    assert(ringbuf_get_exact(&rb, out, 5) == 5);
    assert(memcmp(out, b, 5) == 0);
    assert(ringbuf_is_empty(&rb));

    /* A second wrap with a best-effort split put + multiple gets. */
    uint8_t c[7] = { 30, 31, 32, 33, 34, 35, 36 };
    assert(ringbuf_put(&rb, c, 7) == 7);         /* wraps again */
    uint8_t o1[3], o2[4];
    assert(ringbuf_get_exact(&rb, o1, 3) == 3);
    assert(ringbuf_get_exact(&rb, o2, 4) == 4);
    assert(memcmp(o1, c, 3) == 0);
    assert(memcmp(o2, c + 3, 4) == 0);
    printf("ringbuf: head/tail wrap byte-exact OK\n");
}

/* ---- water-mark transitions as the ring fills then drains --------------- */
static void test_watermarks(void)
{
    uint8_t store[10];
    ringbuf rb;
    /* low=3, high=7 on a 10-byte ring. */
    assert(ringbuf_init(&rb, store, sizeof(store), 3, 7) == 0);

    uint8_t z[10] = {0};

    /* Empty: below low-water (0 <= 3), not at high-water. */
    assert(ringbuf_below_low_water(&rb));
    assert(!ringbuf_at_high_water(&rb));

    /* Put 3: count==3 == low_water -> still "below/at low" (<=). */
    assert(ringbuf_put(&rb, z, 3) == 3);
    assert(ringbuf_below_low_water(&rb));        /* 3 <= 3 */
    assert(!ringbuf_at_high_water(&rb));

    /* Put 1 more -> count 4: above low, below high. */
    assert(ringbuf_put(&rb, z, 1) == 4 - 3);
    assert(!ringbuf_below_low_water(&rb));       /* 4 > 3 */
    assert(!ringbuf_at_high_water(&rb));

    /* Fill to 7 -> at high-water. */
    assert(ringbuf_put(&rb, z, 3) == 3);         /* now 7 */
    assert(ringbuf_count(&rb) == 7);
    assert(ringbuf_at_high_water(&rb));          /* 7 >= 7 */
    assert(!ringbuf_below_low_water(&rb));

    /* Fill to capacity 10 -> still at high-water. */
    assert(ringbuf_put(&rb, z, 3) == 3);
    assert(ringbuf_is_full(&rb));
    assert(ringbuf_at_high_water(&rb));

    /* Drain back down: 10 -> 4 (above low, below high). */
    uint8_t tmp[16];
    assert(ringbuf_get_exact(&rb, tmp, 6) == 6); /* now 4 */
    assert(!ringbuf_at_high_water(&rb));
    assert(!ringbuf_below_low_water(&rb));

    /* Drain to 3 -> back below/at low-water (the "fetch more" trigger). */
    assert(ringbuf_get_exact(&rb, tmp, 1) == 1); /* now 3 */
    assert(ringbuf_below_low_water(&rb));
    printf("ringbuf: low/high water-mark transitions OK\n");
}

/* ---- clear() drops bytes but preserves lifetime counters ---------------- */
static void test_clear(void)
{
    uint8_t store[8];
    ringbuf rb;
    assert(ringbuf_init(&rb, store, sizeof(store), 0, 8) == 0);
    uint8_t z[5] = {1,2,3,4,5};
    assert(ringbuf_put(&rb, z, 5) == 5);
    uint64_t put_before = rb.total_put;
    ringbuf_clear(&rb);
    assert(ringbuf_is_empty(&rb));
    assert(ringbuf_count(&rb) == 0);
    assert(rb.total_put == put_before);          /* lifetime counter kept */
    printf("ringbuf: clear() flush OK\n");
}

/* ---- heap-owned init/free path ----------------------------------------- */
static void test_alloc(void)
{
    ringbuf rb;
    assert(ringbuf_init_alloc(&rb, 16, 4, 12) == 0);
    assert(rb.owns_buf == 1);
    uint8_t z[16];
    for (int i = 0; i < 16; i++) z[i] = (uint8_t)i;
    assert(ringbuf_put(&rb, z, 16) == 16);
    uint8_t out[16] = {0};
    assert(ringbuf_get_exact(&rb, out, 16) == 16);
    assert(memcmp(out, z, 16) == 0);
    ringbuf_free(&rb);
    assert(rb.buf == NULL);
    printf("ringbuf: heap init/free OK\n");
}

int main(void)
{
    test_capacity_and_alln();
    test_wrap();
    test_watermarks();
    test_clear();
    test_alloc();
    printf("ALL RINGBUF TESTS PASSED\n");
    return 0;
}
