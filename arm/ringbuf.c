/*
 * ringbuf.c — single-producer / single-consumer byte ring buffer.
 *
 * See ringbuf.h for the contract. Implementation notes:
 *   - The store is a flat byte array indexed mod cap. head is the next write
 *     position, tail the next read position; `count` is the authoritative
 *     occupancy (so head==tail is unambiguously empty when count==0 and full
 *     when count==cap — we don't sacrifice a slot to disambiguate).
 *   - put/get copy in at most two memcpy runs (the wrap split). No byte-at-a-
 *     time loops; behaviour is identical regardless of where the wrap lands.
 *   - All-or-nothing variants check free/avail up front and touch nothing on
 *     failure — modelling TCP backpressure (producer waits) and consumer wait
 *     (decoder FIFO not yet low-water).
 */
#include "ringbuf.h"

#include <stdlib.h>
#include <string.h>

static size_t clamp_sz(size_t v, size_t lo, size_t hi)
{
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

int ringbuf_init(ringbuf *rb, uint8_t *storage, size_t cap,
                 size_t low_water, size_t high_water)
{
    if (!rb || !storage || cap == 0) return -1;
    memset(rb, 0, sizeof(*rb));
    rb->buf      = storage;
    rb->cap      = cap;
    rb->owns_buf = 0;
    /* Clamp waters to [0,cap] and enforce low <= high. */
    rb->high_water = clamp_sz(high_water, 0, cap);
    rb->low_water  = clamp_sz(low_water, 0, rb->high_water);
    return 0;
}

int ringbuf_init_alloc(ringbuf *rb, size_t cap,
                       size_t low_water, size_t high_water)
{
    if (!rb || cap == 0) return -1;
    uint8_t *p = (uint8_t *)malloc(cap);
    if (!p) return -1;
    int rc = ringbuf_init(rb, p, cap, low_water, high_water);
    if (rc != 0) { free(p); return rc; }
    rb->owns_buf = 1;
    return 0;
}

void ringbuf_free(ringbuf *rb)
{
    if (!rb) return;
    if (rb->owns_buf && rb->buf) free(rb->buf);
    rb->buf = NULL;
    rb->cap = rb->count = rb->head = rb->tail = 0;
    rb->owns_buf = 0;
}

void ringbuf_clear(ringbuf *rb)
{
    if (!rb) return;
    rb->head = rb->tail = rb->count = 0;
}

size_t ringbuf_count(const ringbuf *rb)      { return rb->count; }
size_t ringbuf_capacity(const ringbuf *rb)   { return rb->cap; }
size_t ringbuf_free_space(const ringbuf *rb) { return rb->cap - rb->count; }
int    ringbuf_is_empty(const ringbuf *rb)   { return rb->count == 0; }
int    ringbuf_is_full(const ringbuf *rb)    { return rb->count == rb->cap; }

int ringbuf_below_low_water(const ringbuf *rb)
{
    return rb->count <= rb->low_water;
}

int ringbuf_at_high_water(const ringbuf *rb)
{
    return rb->count >= rb->high_water;
}

/* Copy `len` bytes from src into the ring starting at head (wrap-aware). The
 * caller has already guaranteed free space >= len. */
static void write_run(ringbuf *rb, const uint8_t *src, size_t len)
{
    size_t first = rb->cap - rb->head;          /* bytes until physical end */
    if (first > len) first = len;
    memcpy(rb->buf + rb->head, src, first);
    if (len > first) memcpy(rb->buf, src + first, len - first);
    rb->head = (rb->head + len) % rb->cap;
    rb->count += len;
    rb->total_put += len;
}

/* Copy `len` bytes out of the ring at tail into dst (wrap-aware), advancing
 * tail. The caller has already guaranteed count >= len. dst may be NULL to
 * discard (skip). */
static void read_run(ringbuf *rb, uint8_t *dst, size_t len)
{
    size_t first = rb->cap - rb->tail;          /* bytes until physical end */
    if (first > len) first = len;
    if (dst) {
        memcpy(dst, rb->buf + rb->tail, first);
        if (len > first) memcpy(dst + first, rb->buf, len - first);
    }
    rb->tail = (rb->tail + len) % rb->cap;
    rb->count -= len;
    rb->total_got += len;
}

size_t ringbuf_put(ringbuf *rb, const uint8_t *src, size_t len)
{
    if (len == 0) return 0;
    if (!src) return 0;
    if (len > ringbuf_free_space(rb)) {
        rb->overrun_events++;
        return 0;                                /* all-or-nothing: write none */
    }
    write_run(rb, src, len);
    return len;
}

size_t ringbuf_put_some(ringbuf *rb, const uint8_t *src, size_t len)
{
    if (len == 0 || !src) return 0;
    size_t room = ringbuf_free_space(rb);
    size_t take = len < room ? len : room;
    if (take) write_run(rb, src, take);
    if (take < len) rb->overrun_events++;
    return take;
}

size_t ringbuf_get(ringbuf *rb, uint8_t *dst, size_t len)
{
    if (len == 0) return 0;
    size_t avail = rb->count;
    size_t take  = len < avail ? len : avail;
    if (take) read_run(rb, dst, take);
    if (take < len) rb->underrun_events++;
    return take;
}

size_t ringbuf_get_exact(ringbuf *rb, uint8_t *dst, size_t len)
{
    if (len == 0) return 0;
    if (len > rb->count) {
        rb->underrun_events++;
        return 0;                                /* all-or-nothing: read none */
    }
    read_run(rb, dst, len);
    return len;
}

size_t ringbuf_skip(ringbuf *rb, size_t len)
{
    if (len == 0) return 0;
    size_t avail = rb->count;
    size_t drop  = len < avail ? len : avail;
    if (drop) read_run(rb, NULL, drop);
    return drop;
}
